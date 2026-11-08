"""Every sentence of a draft section verified (spec §9.1, §9.2 G-COVER, G-SUPPORT, G-HEDGE, G-QUOTE):

- **V-COVER:** at least one source marker; each ``clm``/``run``/``fig``/``stat``/``plan`` marker names a target that
  exists (a statistic is ``stt_``, a figure ``fig_``; a ``run`` is an event or the frozen protocol; a ``plan`` a frozen
  design or a hypothesis that was not dropped) and, where it has a hash, still matches it (an artifact's file, the
  frozen protocol's body). ``inf`` and ``ck`` take no target.
- **V-NUM:** every number of the sentence is in a marker's target (``number_pool``): a claim's text and its evidence
  notes, as written; an artifact's, event's or protocol's stored values, exactly or rounded as the sentence writes
  them; a design's or hypothesis's text. ``[[inf]]`` and ``[[ck]]`` cover no number.
- **V-HEDGE:** the sentence is no stronger than the strongest claim it cites; an ``[[inf]]`` sentence is worded
  tentatively (level ≤ 1: "[[inf]] 转为推断性措辞").
- **V-QUOTE:** text in quotation marks is (a word-bounded part of) a verified quote note of a claim the sentence cites.
- **G-SUPPORT:** every cited claim is supported (never by vote); a withdrawn claim is never cited.
- ``[[ck]]`` sentences carry no number and are at most 5% of a section's sentences; ``[[plan:]]`` only in a research
  proposal.

Nothing here calls a model."""

from __future__ import annotations

import json
import re

from pydantic import BaseModel

from rk.argument import ArgumentService
from rk.canon import canonical_json, sha256_hex
from rk.production.artifacts import artifact
from rk.production.manuscript import Marker, Sentence, parse_section
from rk.production.store import design, protocol_hash, protocol_row
from rk.store.project import ProjectStore
from rk.text.layer import normalize_query
from rk.verify.hedge import hedge_level
from rk.verify.numbers import NumberPool, extract_numbers, numeric_leaves
from rk.verify.quote import find_verbatim

CK_MAX_SHARE = 0.05
INF_MAX_LEVEL = 1
_NEEDS_TARGET = ("clm", "run", "fig", "stat", "plan")
_QUOTED = re.compile(r"“([^”]+)”|\"([^\"]+)\"")
_MARKER_TEXT = re.compile(r"\[\[[^\]]*\]\]")


class SentenceVerdict(BaseModel):
    index: int
    ok: bool
    problems: list[str]


class SectionVerdict(BaseModel):
    section: str
    version: int | None
    sentences: list[SentenceVerdict]
    ck_share: float
    ok: bool
    problems: list[str] = []  # section-level (the common-knowledge share)


def _claim(ps: ProjectStore, cid: str) -> dict | None:
    try:
        return ArgumentService(ps).get_claim(cid)
    except KeyError:
        return None


def _event_payload(ps: ProjectStore, evt_id: str) -> dict | None:
    r = ps.db.execute("SELECT payload FROM events WHERE evt_id=?", (evt_id,)).fetchone()
    return json.loads(r["payload"]) if r else None


def _hypothesis(ps: ProjectStore, hyp_id: str) -> dict | None:
    r = ps.db.execute("SELECT * FROM hypotheses WHERE id=?", (hyp_id,)).fetchone()
    return dict(r) if r else None


def _artifact_problem(ps: ProjectStore, m: Marker) -> str | None:
    a = artifact(ps, m.target)
    what = {"stat": "statistic", "fig": "figure"}[m.kind]
    if a is None:
        return f"no {what} {m.target}"
    if a["kind"] != {"stat": "stt", "fig": "fig"}[m.kind]:
        return f"{m.target} is a {'statistic' if a['kind'] == 'stt' else 'figure'}, not a {what}"
    path = ps.root / a["path"]
    if not path.is_file() or sha256_hex(path.read_bytes()) != a["sha256"]:
        return f"the file of {m.target} no longer has its recorded sha256"
    return None


def _target_problem(ps: ProjectStore, m: Marker, paper_type: str) -> str | None:
    """Why the marker's target is not a valid one (V-COVER), else ``None``."""
    if m.kind in ("inf", "ck"):
        return f"[[{m.kind}]] takes no target" if m.target else None
    if not m.target:
        return f"[[{m.kind}]] needs a target"
    if m.kind == "clm":
        c = _claim(ps, m.target)
        if c is None:
            return f"unknown claim {m.target}"
        if c["status"] == "withdrawn":
            return f"claim {m.target} is withdrawn"
        if c["status"] != "supported":
            return f"claim {m.target} is {c['status']}, not supported"
        return None
    if m.kind in ("stat", "fig"):
        return _artifact_problem(ps, m)
    if m.kind == "run":
        if m.target.startswith("prt_"):
            p = protocol_row(ps, m.target)
            if p is None or p["status"] != "frozen":
                return f"no frozen protocol {m.target}"
            if p["frozen_hash"] != protocol_hash(p["body"]):
                return f"protocol {m.target} no longer matches its frozen hash"
            return None
        return None if _event_payload(ps, m.target) is not None else f"no event {m.target}"
    # plan
    if paper_type != "research_proposal":
        return f"[[plan:{m.target}]] belongs only in a research proposal"
    if m.target.startswith("dsg_"):
        d = design(ps, m.target)
        if d is None or d["status"] != "frozen":
            return f"no frozen design {m.target}"
        if d["frozen_hash"] != sha256_hex(canonical_json(d["body"])):
            return f"design {m.target} no longer matches its frozen hash"
        return None
    h = _hypothesis(ps, m.target)
    if h is None:
        return f"no hypothesis {m.target}"
    return f"hypothesis {m.target} was dropped" if h["status"] == "dropped" else None


def number_pool(ps: ProjectStore, markers: list[Marker]) -> NumberPool:
    """The numbers the markers' targets contain (V-NUM, see the module docstring); unknown targets add nothing."""
    pool = NumberPool()
    for m in markers:
        if not m.target:
            continue
        if m.kind == "clm" and (c := _claim(ps, m.target)) is not None:
            texts = [c["text"]] + [n["text"] for e in c["evidence"] for n in e["notes"] if n]
            pool.nums += [n for t in texts for n in extract_numbers(t)]
        elif m.kind in ("stat", "fig") and (a := artifact(ps, m.target)) is not None:
            pool.values += numeric_leaves(a["values"])
        elif m.kind == "run" and m.target.startswith("prt_") and (p := protocol_row(ps, m.target)) is not None:
            pool.values += numeric_leaves(p["body"])
        elif m.kind == "run" and (payload := _event_payload(ps, m.target)) is not None:
            pool.values += numeric_leaves(payload)
        elif m.kind == "plan" and m.target.startswith("dsg_") and (d := design(ps, m.target)) is not None:
            pool.values += numeric_leaves(d["body"])
            pool.nums += extract_numbers(canonical_json(d["body"]))
        elif m.kind == "plan" and (h := _hypothesis(ps, m.target)) is not None:
            pool.nums += extract_numbers(f"{h['statement']} {h['falsification']}")
    return pool


def _quote_notes(ps: ProjectStore, markers: list[Marker]) -> list[str]:
    """The normalized texts of the verified quote notes of the claims the sentence cites."""
    out = []
    for m in markers:
        if m.kind == "clm" and m.target and (c := _claim(ps, m.target)) is not None:
            out += [normalize_query(n["text"]) for e in c["evidence"] for n in e["notes"]
                    if n and n["kind"] == "quote" and (n.get("verified") or {}).get("quote", {}).get("ok")]
    return out


def verify_sentence(ps: ProjectStore, s: Sentence, *, paper_type: str) -> SentenceVerdict:
    """The sentence against every rule of the module docstring."""
    problems: list[str] = []
    if not s.markers:
        problems.append("the sentence has no source marker")
    for m in s.markers:
        if (why := _target_problem(ps, m, paper_type)) is not None:
            problems.append(why)
    kinds = {m.kind for m in s.markers}
    prose = _MARKER_TEXT.sub(" ", s.text)
    nums = extract_numbers(s.text)
    if "ck" in kinds and nums:
        problems.append("a common knowledge sentence ([[ck]]) carries no number: " + ", ".join(n.text for n in nums))
    pool = number_pool(ps, s.markers)
    problems += [f"the number {n.text} is not in any marker's target" for n in nums if not pool.covers(n)]
    level = hedge_level(prose)
    if "inf" in kinds and level > INF_MAX_LEVEL:
        problems.append("an inference ([[inf]]) must be worded tentatively (may, suggest, likely …)")
    cited = [c for m in s.markers if m.kind == "clm" and m.target and (c := _claim(ps, m.target)) is not None]
    if cited and level > max(hedge_level(c["text"]) for c in cited):
        problems.append("the sentence is stronger than the claims it cites (V-HEDGE)")
    notes = _quote_notes(ps, s.markers)
    for q in _QUOTED.finditer(s.text):
        wanted = normalize_query(q.group(1) or q.group(2))
        if wanted and not any(find_verbatim(wanted, n) >= 0 for n in notes):
            problems.append(f"the quoted text “{q.group(1) or q.group(2)}” is not a verified quote note of a claim "
                            "the sentence cites")
    return SentenceVerdict(index=s.index, ok=not problems, problems=problems)


def verify_section(ps: ProjectStore, section: str, markdown: str, *, paper_type: str,
                   version: int | None = None) -> SectionVerdict:
    """Every sentence of the section, and the common-knowledge share (at most ``CK_MAX_SHARE``)."""
    sentences = parse_section(markdown)
    verdicts = [verify_sentence(ps, s, paper_type=paper_type) for s in sentences]
    ck = sum(1 for s in sentences if any(m.kind == "ck" for m in s.markers))
    share = ck / len(sentences) if sentences else 0.0
    problems = []
    if share > CK_MAX_SHARE:
        problems.append(f"{ck} of {len(sentences)} sentences are common knowledge ([[ck]]), more than "
                        f"{CK_MAX_SHARE:.0%}")
    if not sentences:
        problems.append("the section has no sentences")
    return SectionVerdict(section=section, version=version, sentences=verdicts, ck_share=share,
                          ok=not problems and all(v.ok for v in verdicts), problems=problems)
