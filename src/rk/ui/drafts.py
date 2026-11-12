"""Draft preview (spec §8.7): the stored versions of each section (the ``drafts`` projection; a file without a row is
no draft, and a file whose sha256 changed is refused) rendered to HTML with every source marker (``[[kind:id]]``,
spec §9.1 V-COVER; ``rk.production.manuscript.source_markers``, the verifier's markers: none inside a code span or a
code block) turned into a hover card that names its target and links to the evidence (an objection, ``[[obj:]]``:
its text and answer, and the source item it cites).

Raw HTML in a draft is escaped (markdown-it with ``html=False``); markers are swapped for placeholders before rendering
and for ``<span class="mk mk-<kind>">`` elements after it, so no draft text can inject markup."""

from __future__ import annotations

import html as htmlmod
import re

from markdown_it import MarkdownIt

from rk.argument import ArgumentService
from rk.library.service import Library
from rk.production.artifacts import artifact
from rk.production.common import entity_row
from rk.production.manuscript import draft_text, latest_draft, source_markers
from rk.production.store import design, protocol_row
from rk.store.project import ProjectStore

_LABELS = {"inf": "author's inference", "ck": "common knowledge"}


def read_draft(ps: ProjectStore, section: str, version: int | None = None) -> tuple[int, str]:
    """``(version, markdown)`` of a stored version (the latest by default). ``KeyError`` when there is none;
    ``ValueError`` when its file is missing or no longer has its recorded sha256."""
    if version is not None:
        return version, draft_text(ps, section, version)
    if (latest := latest_draft(ps, section)) is None:
        raise KeyError(section)
    return latest


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
    if kind == "obj" and target:
        try:
            o = entity_row(ps, "objections", target)
        except KeyError:
            return {**info, "missing": True, "label": f"unknown objection {target}"}
        if not o.get("source_id"):  # the verifier refuses it too: [[obj:]] cites what an objection's source says
            return {**info, "missing": True, "label": f"objection {target} has no source to cite: {o['text']}"}
        try:
            item = lib.get(o["source_id"])
        except KeyError:
            return {**info, "missing": True, "label": f"the source {o['source_id']} of objection {target} is not in "
                                                      "the library"}
        return {**info, "label": o["text"], "status": o.get("status"), "target_id": o["target_id"],
                "source": {"item_id": item.id, "title": item.title, "year": item.year}}
    if kind == "run" and target and target.startswith("prt_"):
        if (p := protocol_row(ps, target)) is not None:
            return {**info, "label": f"protocol version {p['version']} ({p['status']})"}
    elif kind == "run" and target:
        row = ps.db.execute("SELECT type, ts FROM events WHERE evt_id=?", (target,)).fetchone()
        if row:
            return {**info, "label": f"{row['type']} at {row['ts']}"}
    elif kind in ("stat", "fig") and target and (a := artifact(ps, target)) is not None:
        what = "statistic" if a["kind"] == "stt" else "figure"
        if a["kind"] != {"stat": "stt", "fig": "fig"}[kind]:  # the verifier rejects it too
            return {**info, "missing": True,
                    "label": f"{target} is a {what}, not a {'statistic' if kind == 'stat' else 'figure'}"}
        return {**info, "label": f"{what} from {a['code_path']}"}
    elif kind == "plan" and target and target.startswith("dsg_") and (d := design(ps, target)) is not None:
        return {**info, "label": f"method design ({d['status']})"}
    elif kind == "plan" and target:
        r = ps.db.execute("SELECT statement FROM hypotheses WHERE id=?", (target,)).fetchone()
        if r:
            return {**info, "label": r["statement"]}
    return {**info, "missing": True, "label": f"no {kind} target {target or ''}".strip()}


def render_draft(ps: ProjectStore, lib: Library, markdown: str) -> dict:
    markers: list[dict] = []
    pieces, last = [], 0
    for m in source_markers(markdown):
        markers.append(_resolve(ps, lib, m.kind, m.target))
        pieces += [markdown[last:m.span[0]], f"RKMARKER{len(markers) - 1}X"]
        last = m.span[1]
    body = MarkdownIt("commonmark", {"html": False}).render("".join(pieces) + markdown[last:])

    def span(m: re.Match) -> str:
        i = int(m[1])
        mk = markers[i]
        cls = f"mk mk-{mk['kind']}" + (" mk-missing" if mk.get("missing") else "")
        text = mk["kind"] if not mk["id"] else f"{mk['kind']}:{mk['id'][-6:]}"
        return (f'<span class="{cls}" data-i="{i}" data-kind="{mk["kind"]}" '
                f'data-id="{htmlmod.escape(mk["id"] or "")}">{htmlmod.escape(text)}</span>')

    return {"html": re.sub(r"RKMARKER(\d+)X", span, body), "markers": markers}
