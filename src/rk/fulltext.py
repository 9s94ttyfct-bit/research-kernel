"""Full-text acquisition: JATS -> BioC -> arXiv HTML -> OA PDF; snapshots and library attachments."""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from rk.canon import canonical_json
from rk.ids import new_id
from rk.library.models import Item
from rk.library.service import Library, fts_append
from rk.net.http import HttpError
from rk.sources import arxiv, europepmc, openalex, pubmed
from rk.sources.identifiers import Identifier
from rk.sources.resolver import SourceContext
from rk.store.events import Event, NewEvent
from rk.store.project import ProjectStore
from rk.store.projections import projection
from rk.text.layer import TextLayer
from rk.text.markup import bioc_to_layer, html_to_layer, jats_to_layer
from rk.text.pdf import extract_pdf

AttachOrigin = Literal["oa_api", "library_browser", "publisher_browser", "user_upload", "import"]


class FulltextResult(BaseModel):
    snapshot_sha: str
    kind: str
    origin: str
    page_count: int
    url: str | None = None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def attachment_filename(item: Item, sha256: str, ext: str = ".pdf") -> str:
    """Readable, filesystem-safe name; the short content hash keeps different files of one item apart."""
    first = item.creators[0].family if item.creators else "Anon"
    short = re.sub(r"\s+", " ", re.sub(r"[^\w\s-]", "", item.title)).strip()[:60].rstrip()
    name = f"{first} {item.year or 'n.d.'} - {short} [{sha256[:8]}]{ext}"
    return re.sub(r"[/\\:]", "-", name)


def _record_snapshot(ps: ProjectStore, item_id: str, raw: bytes, layer: TextLayer, kind: str, url: str | None,
                     actor: str) -> FulltextResult:
    sha = ps.blobs.put(raw)
    layer = layer.model_copy(update={"sha256": sha})
    layer.save(ps.text_dir / f"{sha}.json")
    ps.apply(NewEvent(
        type="snapshot.created", entity_id=item_id, actor_kind="agent", actor=actor,
        payload={"sha256": sha, "kind": kind, "url": url, "page_count": len(layer.pages), "fetched_at": _now()},
    ))
    return FulltextResult(snapshot_sha=sha, kind=kind, origin="oa_api", page_count=len(layer.pages), url=url)


def _attach(ps: ProjectStore, item: Item, raw: bytes, origin: AttachOrigin, actor: str,
            actor_kind: Literal["human", "agent"], access_note: str | None = None, kind: str = "pdf") -> str:
    sha = ps.blobs.put(raw)
    fname = attachment_filename(item, sha, ".html" if kind == "html_snapshot" else ".pdf")
    dest = ps.files_dir / item.id / fname
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ps.blobs.path(sha), dest)
    att_id = new_id("att")
    ps.apply(NewEvent(
        type="attachment.added", entity_id=item.id, actor_kind=actor_kind, actor=actor,
        payload={"id": att_id, "kind": kind, "filename": fname, "sha256": sha, "origin": origin,
                 "access_note": access_note, "fetched_at": _now()},
    ))
    return att_id


def _looks_pdf(body: bytes, ctype: str) -> bool:
    return body[:5] == b"%PDF-" or "pdf" in ctype


def fetch_fulltext(ps: ProjectStore, lib: Library, ctx: SourceContext, item_id: str, *, actor: str) -> FulltextResult | None:
    item = lib.get(item_id)
    http = ctx.http
    if not item.ids.pmcid and item.ids.doi:
        oa = openalex.get_work(http, Identifier(kind="doi", value=item.ids.doi), ctx.settings.openalex_key)
        if oa:
            lib.add_item(oa.item, added_by="agent", actor=actor)
            item = lib.get(item_id)
    if item.ids.pmcid:
        xml = europepmc.fulltext_xml(http, item.ids.pmcid)
        if xml:
            layer = jats_to_layer(xml, "")
            if layer.pages[0].text.strip():
                return _record_snapshot(ps, item.id, xml, layer, "jats",
                                        f"{europepmc.BASE}/{item.ids.pmcid}/fullTextXML", actor)
        doc = pubmed.bioc_json(http, item.ids.pmcid)
        if doc:
            raw = canonical_json(doc).encode()
            layer = bioc_to_layer(doc, "")
            if layer.pages[0].text.strip():
                return _record_snapshot(ps, item.id, raw, layer, "bioc", f"{pubmed.BIOC}/{item.ids.pmcid}/unicode", actor)
    if item.ids.arxiv:
        url = arxiv.html_url(item.ids.arxiv)
        try:
            res = http.get(url)
        except HttpError:
            res = None
        if res is not None and res.status == 200 and "html" in res.content_type:
            layer = html_to_layer(res.body.decode("utf-8", "replace"), "")
            if len(layer.pages[0].text) > 500:
                return _record_snapshot(ps, item.id, res.body, layer, "html", url, actor)
    for url in item.oa_urls:
        try:
            res = http.get(url, ttl_s=0)
        except HttpError:
            continue
        if res.status == 200 and _looks_pdf(res.body, res.content_type):
            layer = extract_pdf(None, data=res.body)
            if any(p.text.strip() for p in layer.pages):
                result = _record_snapshot(ps, item.id, res.body, layer, "pdf", url, actor)
                _attach(ps, item, res.body, "oa_api", actor, "agent")
                return result
    return None


def attach_pdf(ps: ProjectStore, lib: Library, item_id: str, path: Path, *, origin: AttachOrigin, actor: str,
               actor_kind: Literal["human", "agent"] = "human", access_note: str | None = None) -> FulltextResult:
    raw = Path(path).read_bytes()
    if raw[:5] != b"%PDF-":
        raise ValueError(f"not a PDF: {path}")
    return attach_pdf_bytes(ps, lib, item_id, raw, origin=origin, actor=actor, actor_kind=actor_kind,
                            access_note=access_note)


def attach_pdf_bytes(ps: ProjectStore, lib: Library, item_id: str, raw: bytes, *, origin: AttachOrigin, actor: str,
                     actor_kind: Literal["human", "agent"], access_note: str | None = None,
                     url: str | None = None) -> FulltextResult:
    """Attach a PDF obtained outside the API channels (upload, built-in browser) and build its text layer."""
    item = lib.get(item_id)
    if raw[:5] != b"%PDF-":
        raise ValueError("not a PDF")
    layer = extract_pdf(None, data=raw)
    _attach(ps, item, raw, origin, actor, actor_kind, access_note)
    res = _record_snapshot(ps, item.id, raw, layer, "pdf", url, actor)
    return res.model_copy(update={"origin": origin})


def attach_html_snapshot(ps: ProjectStore, lib: Library, item_id: str, html: str, *, origin: AttachOrigin, actor: str,
                         actor_kind: Literal["human", "agent"], url: str, access_note: str | None = None,
                         text_layer: bool = True) -> tuple[str, FulltextResult | None]:
    """Keep a web page as an ``html_snapshot`` attachment and, with ``text_layer``, as the item's text layer (hidden
    text removed). Without it the file is kept only, so a page snapshot never replaces a better text layer."""
    item = lib.get(item_id)
    raw = html.encode()
    att = _attach(ps, item, raw, origin, actor, actor_kind, access_note, kind="html_snapshot")
    if not text_layer:
        return att, None
    res = _record_snapshot(ps, item.id, raw, html_to_layer(html, ""), "html", url, actor)
    return att, res.model_copy(update={"origin": origin})


def latest_snapshot(ps: ProjectStore, item_id: str) -> tuple[str, TextLayer] | None:
    r = ps.db.execute("SELECT sha256 FROM snapshots WHERE item_id=? ORDER BY seq DESC LIMIT 1", (item_id,)).fetchone()
    if not r:
        return None
    return r["sha256"], TextLayer.load(ps.text_dir / f"{r['sha256']}.json")


# ---- projections ----
def _project_root(conn: sqlite3.Connection) -> Path:
    path = next(r["file"] for r in conn.execute("PRAGMA database_list") if r["name"] == "main")
    return Path(path).parent


@projection("snapshot.created")
def _snapshot_created(conn: sqlite3.Connection, ev: Event) -> None:
    p = ev.payload
    conn.execute(
        "INSERT OR REPLACE INTO snapshots(sha256, item_id, kind, url, fetched_at, page_count, seq) VALUES (?,?,?,?,?,?,?)",
        (p["sha256"], ev.entity_id, p["kind"], p["url"], p["fetched_at"], p["page_count"], ev.seq),
    )
    layer_path = _project_root(conn) / "text" / f"{p['sha256']}.json"
    if not layer_path.exists():  # silently skipping would drop the item's full text from search after a rebuild
        raise FileNotFoundError(f"text layer for snapshot {p['sha256']} is missing: {layer_path}")
    text = " ".join(pg["text"] for pg in json.loads(layer_path.read_text(encoding="utf-8"))["pages"])
    fts_append(conn, ev.entity_id, "fulltext", text)


@projection("attachment.added")
def _attachment_added(conn: sqlite3.Connection, ev: Event) -> None:
    p = ev.payload
    conn.execute(
        "INSERT OR REPLACE INTO attachments(id, item_id, kind, filename, sha256, origin, access_note, fetched_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (p["id"], ev.entity_id, p["kind"], p["filename"], p["sha256"], p["origin"], p["access_note"], p["fetched_at"]),
    )
