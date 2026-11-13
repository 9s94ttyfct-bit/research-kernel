"""Full-text acquisition: JATS -> BioC -> arXiv HTML -> OA PDF (``fetch_fulltext``, which names each source that gave
none); snapshots and library attachments."""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel

from rk.canon import canonical_json
from rk.ids import new_id
from rk.library.models import Item
from rk.library.service import Library, fts_append
from rk.net.http import HttpClient, HttpError
from rk.sources import arxiv, europepmc, openalex, pubmed
from rk.sources.identifiers import Identifier
from rk.sources.resolver import SourceContext
from rk.store.events import Event, NewEvent
from rk.store.project import ProjectStore
from rk.store.projections import projection
from rk.text.layer import TextLayer
from rk.text.markup import bioc_to_layer, html_to_layer, jats_to_layer
from rk.text.pdf import extract_pdf

if TYPE_CHECKING:
    from collections.abc import Callable

    from rk.browser.service import BrowserService
    from rk.tools.registry import CallContext

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


def _html_snapshot(ps: ProjectStore, http: HttpClient, item_id: str, url: str, actor: str, source: str,
                   why: list[str], *, min_chars: int = 500) -> FulltextResult | None:
    """The page at ``url`` as the item's text layer (kind ``html``) when it is HTML with more than ``min_chars``
    characters of text, else ``None`` with the reason added to ``why`` (named by ``source``)."""
    try:
        res = http.get(url)
    except HttpError as e:
        why.append(f"{source}: HTTP {e.status}")
        return None
    if res.status != 200 or "html" not in res.content_type:
        why.append(f"{source}: no page (HTTP {res.status}, {res.content_type or 'no content type'})")
        return None
    layer = html_to_layer(res.body.decode("utf-8", "replace"), "")
    if len(layer.pages[0].text) <= min_chars:
        why.append(f"{source}: the page has no full text")
        return None
    return _record_snapshot(ps, item_id, res.body, layer, "html", url, actor)


def _on_pmc_web_site(url: str) -> bool:
    """Whether ``url`` is on PMC's web site, from which PMC's terms forbid automated downloading of articles
    (https://pmc.ncbi.nlm.nih.gov/about/copyright/)."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return host == "pmc.ncbi.nlm.nih.gov" or (host == "www.ncbi.nlm.nih.gov" and parts.path.startswith("/pmc/"))


def fetch_fulltext(ps: ProjectStore, lib: Library, ctx: SourceContext, item_id: str, *, actor: str,
                   why: list[str] | None = None) -> FulltextResult | None:
    """The open-access API channels, in order: an item without a PMCID looks it up (by DOI in OpenAlex, else by PMID
    in Europe PMC: ``europepmc.by_pmid``), then by PMCID Europe PMC's full-text XML (JATS) and PMC's BioC, then
    arXiv's HTML, then the item's open-access PDF URLs (never one on PMC's web site: ``_on_pmc_web_site``). ``None``
    when none has the text; each source tried and why it gave none is then added to ``why`` (one line per source).

    Calibration (full-text screening, 2026-11-13, docs/eval/ft-screening-2026-11-13.md): the 36 abstract-only records
    with a PMCID across the srrepro9 projects were free PMC articles outside PMC's open-access subset, for which
    Europe PMC's full-text XML answers 500 (retried) and BioC "No result", and the reasons were dropped. Such an
    article is not fetched from PMC's web site (PMC's terms forbid automated downloading of articles): it stays
    abstract-only, with the reasons recorded. When the PMID lookup's record says it is outside the open-access subset,
    its full-text XML is not requested."""
    why = [] if why is None else why
    item = lib.get(item_id)
    http = ctx.http
    open_access: bool | None = None  # what the PMID lookup's record says, when it ran
    if not item.ids.pmcid and item.ids.doi:
        try:
            oa = openalex.get_work(http, Identifier(kind="doi", value=item.ids.doi), ctx.settings.openalex_key)
        except HttpError as e:
            why.append(f"OpenAlex (PMCID by DOI): HTTP {e.status}")
            oa = None
        if oa:
            lib.add_item(oa.item, added_by="agent", actor=actor)
            item = lib.get(item_id)
    if not item.ids.pmcid and item.ids.pmid:
        try:
            med = europepmc.by_pmid(http, item.ids.pmid)
        except HttpError as e:
            why.append(f"Europe PMC (PMCID by PMID): HTTP {e.status}")
            med = None
        if med is not None and med.item.ids.pmcid:
            lib.add_item(med.item, added_by="agent", actor=actor)
            item, open_access = lib.get(item_id), med.open_access
    if not item.ids.pmcid:
        why.append("PMC: no PMCID")
    else:
        xml = None
        if open_access is False:
            why.append("Europe PMC full-text XML: not requested (not in the open-access subset)")
        else:
            try:
                xml = europepmc.fulltext_xml(http, item.ids.pmcid)
            except HttpError as e:
                outside = " (Europe PMC answers so for articles outside the open-access subset)" if e.status == 500 else ""
                why.append(f"Europe PMC full-text XML: HTTP {e.status}{outside}")
            else:
                if not xml:
                    why.append("Europe PMC full-text XML: none")
        if xml:
            layer = jats_to_layer(xml, "")
            if layer.pages[0].text.strip():
                return _record_snapshot(ps, item.id, xml, layer, "jats",
                                        f"{europepmc.BASE}/{item.ids.pmcid}/fullTextXML", actor)
            why.append("Europe PMC full-text XML: no text")
        try:
            doc = pubmed.bioc_json(http, item.ids.pmcid)
        except HttpError as e:
            why.append(f"PMC BioC: HTTP {e.status}")
            doc = None
        else:
            if not doc:
                why.append("PMC BioC: no result")
        if doc:
            raw = canonical_json(doc).encode()
            layer = bioc_to_layer(doc, "")
            if layer.pages[0].text.strip():
                return _record_snapshot(ps, item.id, raw, layer, "bioc", f"{pubmed.BIOC}/{item.ids.pmcid}/unicode", actor)
            why.append("PMC BioC: no text")
    if item.ids.arxiv and (page := _html_snapshot(ps, http, item.id, arxiv.html_url(item.ids.arxiv), actor,
                                                  "arXiv HTML", why)):
        return page
    for url in item.oa_urls:
        if _on_pmc_web_site(url):
            why.append(f"open-access URL {urlsplit(url).hostname}: PMC web site, not fetched (PMC's terms)")
            continue
        try:
            res = http.get(url, ttl_s=0)
        except HttpError as e:
            why.append(f"open-access URL {url}: HTTP {e.status}")
            continue
        if res.status != 200 or not _looks_pdf(res.body, res.content_type):
            why.append(f"open-access URL {url}: no PDF (HTTP {res.status})")
            continue
        layer = extract_pdf(None, data=res.body)
        if any(p.text.strip() for p in layer.pages):
            result = _record_snapshot(ps, item.id, res.body, layer, "pdf", url, actor)
            _attach(ps, item, res.body, "oa_api", actor, "agent")
            return result
        why.append(f"open-access URL {url}: the PDF has no text")
    if not item.oa_urls:
        why.append("open-access PDF: no URL")
    return None


def fulltext_with_fallback(kernel, ps: ProjectStore, lib: Library, item_id: str, *, call: CallContext, project: str,
                           browser: Callable[[], BrowserService | None],
                           why: list[str] | None = None) -> FulltextResult | None:
    """The full-text channel chain (spec §11.1): the open-access API channels first, then the built-in browser (library
    or publisher page) when it runs and ``fulltext_browser_fallback`` is on. ``None`` when no channel applies, each
    source tried and why it gave nothing added to ``why`` (``fetch_fulltext``); browser failures raise its
    ``ToolError``."""
    why = [] if why is None else why
    r = fetch_fulltext(ps, lib, kernel.ctx(), item_id, actor=call.actor, why=why)
    if r is not None or not kernel.settings().fulltext_browser_fallback:
        return r
    svc = browser()
    if svc is None or not svc.running:
        why.append("built-in browser: not running")
        return None
    return svc.fetch_fulltext(call, project, item_id)


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
