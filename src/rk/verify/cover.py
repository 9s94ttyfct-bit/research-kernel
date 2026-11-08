"""Every sentence of a draft section verified (spec §9.1, §9.2 G-COVER, G-SUPPORT, G-HEDGE, G-QUOTE). Every prose
check reads the sentence's ``prose``: its rendered text with code spans blanked (a number, a strong word or a quote in
a code span is not checked and does not count).

- **V-COVER:** at least one source marker; each ``clm``/``run``/``fig``/``stat``/``plan`` marker names a target that
  exists (a statistic is ``stt_``, a figure ``fig_``; a ``run`` is an event or the frozen protocol; a ``plan`` a frozen
  design or a hypothesis that was not dropped) and, where it has a hash, still matches it (an artifact's file, the
  frozen protocol's body, the frozen design's body, and the frozen design a hypothesis is pre-registered in by its
  ``prereg_hash``). ``inf`` and ``ck`` take no target.
- **V-NUM:** every number of the sentence is in a marker's target (``number_pool``): a claim's text and its evidence
  notes, as written; an artifact's reported values (a pooled statistic's ``display`` values and ``k``, a map table's
  counts), an event's whitelisted fields (``EVENT_NUMBERS``) and the protocol's stored values, exactly or rounded as
  the sentence writes them; a design's or hypothesis's text. ``[[inf]]`` and ``[[ck]]`` cover no number.
- **V-HEDGE:** the sentence is no stronger than the strongest claim it cites; an ``[[inf]]`` sentence is worded
  tentatively (level ≤ 1: "[[inf]] 转为推断性措辞"). Quoted text that V-QUOTE checks is the source's wording, not the
  sentence's, and is left out of the measure.
- **V-QUOTE:** in a sentence that cites a claim or the protocol, text in quotation marks (“…”, "…", “…", „…“, «…»,
  ‘…’, '…'; an apostrophe is no quotation mark) and a block quote's whole text are (a word-bounded part of) a verified
  quote note of a claim the sentence cites or a text of a protocol it cites (e.g. a search string).
- **G-SUPPORT:** every cited claim passes the claim gate (``rk.verify.support.claim_problems``: supported, never by
  vote; V-HEDGE; V-STATS for a ``stat_result``; no open or conceded priority-1 objection); a withdrawn claim is never
  cited.
- ``[[ck]]`` sentences carry no number and are at most 5% of a section's sentences; ``[[plan:]]`` only in a research
  proposal. A section with a markdown table or a table caption fails (``section_problems``).

Nothing here calls a model."""

from __future__ import annotations

import json
import re

from pydantic import BaseModel

from rk.argument import ArgumentService
from rk.canon import canonical_json, sha256_hex
from rk.production.artifacts import artifact
from rk.production.manuscript import Marker, Sentence, parse_section, section_problems
from rk.production.store import design, protocol_hash, protocol_row
from rk.store.project import ProjectStore
from rk.text.layer import normalize_query
from rk.verify.hedge import hedge_level
from rk.verify.numbers import NumberPool, extract_numbers, numeric_leaves
from rk.verify.quote import find_verbatim
from rk.verify.support import claim_problems

CK_MAX_SHARE = 0.05
INF_MAX_LEVEL = 1
_MARKER_TEXT = re.compile(r"\[\[[^\]]*\]\]")
# quotation mark pairs; a straight or curly single quote opens only at a word start and closes only before no letter,
# so an apostrophe inside (don't) or at a word's end (the patients' outcomes) is none
_QUOTED = re.compile(
    r"“(?P<a>[^“”\"]+)[”\"]"
    r"|\"(?P<b>[^\"“”]+)[\"”]"
    r"|„(?P<c>[^„“”]+)[“”]"
    r"|«(?P<d>[^«»]+)»"
    r"|(?<!\w)‘(?P<e>(?:[^‘’]|’(?=\w))+)’(?!\w)"
    r"|(?<!\w)'(?=\S)(?P<f>(?:[^'\n]|'(?=\w))+?)(?<=\S)'(?!\w)")
# The numbers a run event reports: per event type, the named top-level fields of its producer's payload that a
# manuscript states (never bookkeeping: attempt, round, offset, limit, timestamps, ids; never nested payloads). A list
# field gives its length. An event type not listed covers no number.
EVENT_NUMBERS: dict[str, tuple[str, ...]] = {
    # rk.discovery.store.log_search: the years searched, malformed entries dropped, the hits returned
    "search.logged": ("after_year", "date_from", "date_to", "dropped", "hit_ids"),
    # rk.production.protocol._probe: the record-cap probe's total count
    "protocol.probed": ("total",),
    # rk.production.store._deviation_event: the protocol versions a deviation links
    "protocol.deviation": ("from_version", "to_version"),
    # rk.browser.log.record_browse: the results a browser search showed
    "browse.logged": ("result_count",),
    # rk.production.store.add_extraction: the extracted value (a number only; nested values are not pooled)
    "extraction.added": ("value",),
    # rk.orchestrator.state.record_edge_taken: how often a back-edge was taken
    "edge.taken": ("count",),
}


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
    problems: list[str] = []  # section-level (the common-knowledge share, a table, an empty section)


def _claim(ps: ProjectStore, cid: str) -> dict | None:
    try:
        return ArgumentService(ps).get_claim(cid)
    except KeyError:
        return None


def _event(ps: ProjectStore, evt_id: str) -> tuple[str, dict] | None:
    r = ps.db.execute("SELECT type, payload FROM events WHERE evt_id=?", (evt_id,)).fetchone()
    return (r["type"], json.loads(r["payload"])) if r else None


def _hypothesis(ps: ProjectStore, hyp_id: str) -> dict | None:
    r = ps.db.execute("SELECT * FROM hypotheses WHERE id=?", (hyp_id,)).fetchone()
    return dict(r) if r else None


def _design_problem(d: dict) -> str | None:
    if d["frozen_hash"] != sha256_hex(canonical_json(d["body"])):
        return f"design {d['id']} no longer matches its frozen hash"
    return None


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


def _hypothesis_problem(ps: ProjectStore, hyp_id: str) -> str | None:
    h = _hypothesis(ps, hyp_id)
    if h is None:
        return f"no hypothesis {hyp_id}"
    if h["status"] == "dropped":
        return f"hypothesis {hyp_id} was dropped"
    if h["prereg_hash"]:
        rows = ps.db.execute("SELECT id FROM designs WHERE status='frozen' AND frozen_hash=?",
                             (h["prereg_hash"],)).fetchall()
        if not rows:
            return f"hypothesis {hyp_id} is pre-registered, but no frozen design has the hash {h['prereg_hash']}"
        if all(_design_problem(design(ps, r["id"])) for r in rows):
            return f"the design hypothesis {hyp_id} is pre-registered in no longer matches its frozen hash"
    return None


def _target_problem(ps: ProjectStore, m: Marker, paper_type: str) -> str | None:
    """Why the marker's target is not a valid one (V-COVER, G-SUPPORT), else ``None``."""
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
        problems = claim_problems(ps, m.target)
        return f"claim {m.target} fails the claim gate: " + "; ".join(p.line for p in problems) if problems else None
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
        return None if _event(ps, m.target) is not None else f"no event {m.target}"
    # plan
    if paper_type != "research_proposal":
        return f"[[plan:{m.target}]] belongs only in a research proposal"
    if m.target.startswith("dsg_"):
        d = design(ps, m.target)
        if d is None or d["status"] != "frozen":
            return f"no frozen design {m.target}"
        return _design_problem(d)
    return _hypothesis_problem(ps, m.target)


def _number(v) -> list[float]:
    return [float(v)] if isinstance(v, int | float) and not isinstance(v, bool) else []


def _reported_values(values: dict) -> list[float]:
    """An artifact's reported values: a pooled statistic's ``display`` values and its ``k``; a map table's study count
    and its category (and cross-tabulation) counts. Nothing else it stores (study rows, the pooled model's log-scale
    internals, options, row ids, the extraction attempt)."""
    out: list[float] = []
    display = values.get("display")
    if isinstance(display, dict):
        out += [x for v in display.values() for x in _number(v)]
    pooled = values.get("pooled")
    out += _number(values.get("k")) + (_number(pooled.get("k")) if isinstance(pooled, dict) else [])
    table = values.get("table")
    if isinstance(table, dict):
        out += _number(table.get("n"))
        out += [x for counts in (table.get("fields") or {}).values() for v in counts.values() for x in _number(v)]
        cross = table.get("cross") or {}
        out += [x for counts in (cross.get("counts") or {}).values() for v in counts.values() for x in _number(v)]
    return out


def _event_values(etype: str, payload: dict) -> list[float]:
    out: list[float] = []
    for field in EVENT_NUMBERS.get(etype, ()):
        v = payload.get(field)
        out += [float(len(v))] if isinstance(v, list) else _number(v)
    return out


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
            pool.values += _reported_values(a["values"])
        elif m.kind == "run" and m.target.startswith("prt_") and (p := protocol_row(ps, m.target)) is not None:
            pool.values += numeric_leaves(p["body"])
        elif m.kind == "run" and (ev := _event(ps, m.target)) is not None:
            pool.values += _event_values(*ev)
        elif m.kind == "plan" and m.target.startswith("dsg_") and (d := design(ps, m.target)) is not None:
            pool.values += numeric_leaves(d["body"])
            pool.nums += extract_numbers(canonical_json(d["body"]))
        elif m.kind == "plan" and (h := _hypothesis(ps, m.target)) is not None:
            pool.nums += extract_numbers(f"{h['statement']} {h['falsification']}")
    return pool


def _strings(obj) -> list[str]:
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in _strings(v)]
    if isinstance(obj, list):
        return [s for v in obj for s in _strings(v)]
    return [obj] if isinstance(obj, str) else []


def _quote_sources(ps: ProjectStore, markers: list[Marker]) -> list[str]:
    """The normalized texts a quotation in the sentence may come from: the verified quote notes of the claims it cites
    and the texts of the protocols it cites."""
    out = []
    for m in markers:
        if m.kind == "clm" and m.target and (c := _claim(ps, m.target)) is not None:
            out += [normalize_query(n["text"]) for e in c["evidence"] for n in e["notes"]
                    if n and n["kind"] == "quote" and (n.get("verified") or {}).get("quote", {}).get("ok")]
        elif m.kind == "run" and m.target and m.target.startswith("prt_") and \
                (p := protocol_row(ps, m.target)) is not None:
            out += [normalize_query(s) for s in _strings(p["body"])]
    return out


def _quotations(s: Sentence, prose: str) -> list[tuple[int, int, str]]:
    """``(start, end, quoted text)`` of each quotation in the sentence's prose (markers blanked); a block quote is one."""
    if s.quoted:
        return [(0, len(prose), prose.strip())]
    return [(q.start(), q.end(), next(g for g in q.groups() if g is not None)) for q in _QUOTED.finditer(prose)]


def verify_sentence(ps: ProjectStore, s: Sentence, *, paper_type: str) -> SentenceVerdict:
    """The sentence against every rule of the module docstring."""
    problems: list[str] = []
    if not s.markers:
        problems.append("the sentence has no source marker")
    for m in s.markers:
        if (why := _target_problem(ps, m, paper_type)) is not None:
            problems.append(why)
    kinds = {m.kind for m in s.markers}
    prose = _MARKER_TEXT.sub(lambda x: " " * len(x.group(0)), s.prose)
    nums = extract_numbers(s.prose)
    if "ck" in kinds and nums:
        problems.append("a common knowledge sentence ([[ck]]) carries no number: " + ", ".join(n.text for n in nums))
    pool = number_pool(ps, s.markers)
    problems += [f"the number {n.text} is not in any marker's target" for n in nums if not pool.covers(n)]
    measured = prose
    if any(m.kind == "clm" or (m.kind == "run" and (m.target or "").startswith("prt_")) for m in s.markers):
        sources = _quote_sources(ps, s.markers)
        for a, b, quoted in _quotations(s, prose):
            wanted = normalize_query(quoted)
            if wanted and not any(find_verbatim(wanted, t) >= 0 for t in sources):
                what = "the block quote" if s.quoted else "the quoted text"
                problems.append(f"{what} “{quoted.strip()}” is not a verified quote note of a claim the sentence "
                                "cites, nor a text of a protocol it cites")
            measured = measured[:a] + " " * (b - a) + measured[b:]
    level = hedge_level(measured) if re.search(r"\w", measured) else 0  # all quoted: no wording of its own
    if "inf" in kinds and level > INF_MAX_LEVEL:
        problems.append("an inference ([[inf]]) must be worded tentatively (may, suggest, likely …)")
    cited = [c for m in s.markers if m.kind == "clm" and m.target and (c := _claim(ps, m.target)) is not None]
    if cited and level > max(hedge_level(c["text"]) for c in cited):
        problems.append("the sentence is stronger than the claims it cites (V-HEDGE)")
    return SentenceVerdict(index=s.index, ok=not problems, problems=problems)


def verify_section(ps: ProjectStore, section: str, markdown: str, *, paper_type: str,
                   version: int | None = None) -> SectionVerdict:
    """Every sentence of the section, the common-knowledge share (at most ``CK_MAX_SHARE``) and what the section may
    not contain (``section_problems``: tables and table captions)."""
    sentences = parse_section(markdown)
    verdicts = [verify_sentence(ps, s, paper_type=paper_type) for s in sentences]
    ck = sum(1 for s in sentences if any(m.kind == "ck" for m in s.markers))
    share = ck / len(sentences) if sentences else 0.0
    problems = section_problems(markdown)
    if share > CK_MAX_SHARE:
        problems.append(f"{ck} of {len(sentences)} sentences are common knowledge ([[ck]]), more than "
                        f"{CK_MAX_SHARE:.0%}")
    if not sentences:
        problems.append("the section has no sentences")
    return SectionVerdict(section=section, version=version, sentences=verdicts, ck_share=share,
                          ok=not problems and all(v.ok for v in verdicts), problems=problems)
