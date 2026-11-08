"""Draft preview (spec §8.7): ``artifacts/drafts/<section>.v<N>.md`` rendered to HTML with every source marker
(``[[kind:id]]``, spec §9.1 V-COVER; the grammar is ``rk.production.manuscript.MARKER``) turned into a hover card that
names its target and links to the evidence.

Raw HTML in a draft is escaped (markdown-it with ``html=False``); markers are swapped for placeholders before rendering
and for ``<span class="mk mk-<kind>">`` elements after it, so no draft text can inject markup."""

from __future__ import annotations

import html as htmlmod
import re
from pathlib import Path

from markdown_it import MarkdownIt

from rk.argument import ArgumentService
from rk.library.service import Library
from rk.production.artifacts import artifact
from rk.production.manuscript import MARKER
from rk.production.store import design, protocol_row
from rk.store.project import ProjectStore

_DRAFT = re.compile(r"^(?P<section>[a-z0-9][a-z0-9_-]*)\.v(?P<n>\d+)\.md$")
_LABELS = {"inf": "author's inference", "ck": "common knowledge"}


def drafts_dir(ps: ProjectStore) -> Path:
    return ps.root / "artifacts" / "drafts"


def list_drafts(ps: ProjectStore) -> list[dict]:
    found: dict[str, list[int]] = {}
    d = drafts_dir(ps)
    for p in sorted(d.glob("*.md")) if d.exists() else []:
        if m := _DRAFT.match(p.name):
            found.setdefault(m["section"], []).append(int(m["n"]))
    return [{"section": s, "versions": sorted(v)} for s, v in sorted(found.items())]


def read_draft(ps: ProjectStore, section: str, version: int | None = None) -> tuple[int, str]:
    versions = next((d["versions"] for d in list_drafts(ps) if d["section"] == section), None)
    if not versions:
        raise KeyError(section)
    n = version if version is not None else versions[-1]
    if n not in versions:
        raise KeyError(f"{section} v{n}")
    return n, (drafts_dir(ps) / f"{section}.v{n}.md").read_text()


def _resolve(ps: ProjectStore, lib: Library, kind: str, target: str | None) -> dict:
    info: dict = {"kind": kind, "id": target}
    if kind in _LABELS:
        return {**info, "label": _LABELS[kind]}
    if kind == "clm" and target:
        try:
            c = ArgumentService(ps).get_claim(target)
        except KeyError:
            return {**info, "missing": True, "label": f"unknown claim {target}"}
        evidence = []
        for e in c.get("evidence", []):
            for n in e.get("notes", []):
                if not n:
                    continue
                try:
                    title = lib.get(n["item_id"]).title
                except KeyError:
                    title = None
                evidence.append({"note_id": n["id"], "item_id": n["item_id"], "item_title": title,
                                 "kind": n["kind"], "text": n["text"], "locator": n.get("locator")})
        return {**info, "label": c.get("text"), "status": c.get("status"), "evidence": evidence}
    if kind == "run" and target and target.startswith("prt_"):
        if (p := protocol_row(ps, target)) is not None:
            return {**info, "label": f"protocol version {p['version']} ({p['status']})"}
    elif kind == "run" and target:
        row = ps.db.execute("SELECT type, ts FROM events WHERE evt_id=?", (target,)).fetchone()
        if row:
            return {**info, "label": f"{row['type']} at {row['ts']}"}
    elif kind in ("stat", "fig") and target and (a := artifact(ps, target)) is not None:
        return {**info, "label": f"{'statistic' if a['kind'] == 'stt' else 'figure'} from {a['code_path']}"}
    elif kind == "plan" and target and target.startswith("dsg_") and (d := design(ps, target)) is not None:
        return {**info, "label": f"method design ({d['status']})"}
    elif kind == "plan" and target:
        r = ps.db.execute("SELECT statement FROM hypotheses WHERE id=?", (target,)).fetchone()
        if r:
            return {**info, "label": r["statement"]}
    return {**info, "missing": True, "label": f"no {kind} target {target or ''}".strip()}


def render_draft(ps: ProjectStore, lib: Library, markdown: str) -> dict:
    markers: list[dict] = []

    def stash(m: re.Match) -> str:
        markers.append(_resolve(ps, lib, m[1], m[2]))
        return f"RKMARKER{len(markers) - 1}X"

    body = MarkdownIt("commonmark", {"html": False}).render(MARKER.sub(stash, markdown))

    def span(m: re.Match) -> str:
        i = int(m[1])
        mk = markers[i]
        cls = f"mk mk-{mk['kind']}" + (" mk-missing" if mk.get("missing") else "")
        text = mk["kind"] if not mk["id"] else f"{mk['kind']}:{mk['id'][-6:]}"
        return (f'<span class="{cls}" data-i="{i}" data-kind="{mk["kind"]}" '
                f'data-id="{htmlmod.escape(mk["id"] or "")}">{htmlmod.escape(text)}</span>')

    return {"html": re.sub(r"RKMARKER(\d+)X", span, body), "markers": markers}
