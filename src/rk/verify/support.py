"""V-SUPPORT (spec §9.1, rule amended 2026-11-07: cloud judges only, no layer-1 model): does the linked evidence
support the claim?

supported      ≥ min_judges valid judges, and every valid judge says "supported" (with verbatim quotes)
unsupported /  ≥ min_judges valid judges, all sharing that one label (a split between the two is not agreement)
contradicted
uncertain      everything else, including missing evidence or too few valid judges

A judge is valid when it ran and its vendor is admitted (``rk.verify.judge.run_judges``: a known, non-human vendor
that is neither the claim author's nor another judge's); a failed or skipped judge does not count. V-NUM runs
alongside: a number in the claim that its evidence does not contain keeps the claim from "supported".

The judges read the claim's verified passages grouped by study, each group headed by the study's label, title, year,
design and arms (``evidence_document``); the passages, the label rule and V-NUM are unchanged. Calibration (synthesis
across studies, 2026-11-13): given bare passages the judges could not count the studies a finding rests on, nor tell
which arm a passage is about.

A ``stat_result`` claim (kernel-written from its artifacts) gets no judges: it is supported when V-STATS passes on
every artifact it reports and V-NUM finds each of its numbers among those artifacts' values, else unsupported.

Also here: V-HEDGE of a stored claim (``check_claim_hedge``) and the claim gate of the synthesize and argue stages
(``gate_claims``).
"""

from __future__ import annotations

import json
from collections.abc import Collection
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from rk.argument import ArgumentService
from rk.checkpoints import create_checkpoint
from rk.llm.drivers import DriverSpec
from rk.production.artifacts import artifact
from rk.verify.hedge import (
    SYNONYMS,
    HedgeResult,
    check_hedge,
    hedge_level,
    universal_terms,
)
from rk.verify.judge import JudgeOpinion, JudgeRunner, run_judges
from rk.verify.numbers import Num, NumResult, check_bound_numbers, check_numbers
from rk.verify.records import record_verification
from rk.verify.stats import StatsResult, check_stats

Verdict = Literal["supported", "uncertain", "unsupported", "contradicted"]


class SupportResult(BaseModel):
    verdict: Verdict
    opinions: list[JudgeOpinion] = []
    numbers_ok: bool = True
    ungrounded_numbers: list[Num] = []
    min_judges: int
    reason: str | None = None


def combine(opinions: list[JudgeOpinion], *, min_judges: int) -> Verdict:
    """Spec §9.1 (amended 2026-11-07): the verdict is the one label shared by every valid judge (``error is None``)
    when there are at least ``min_judges`` of them and that label is a verdict; anything else is ``uncertain``."""
    valid = [o for o in opinions if o.error is None]
    labels = {o.label for o in valid}
    if not valid or len(valid) < min_judges or len(labels) != 1:
        return "uncertain"
    (label,) = labels
    return label if label in ("supported", "unsupported", "contradicted") else "uncertain"


def _evidence_notes(claim: dict) -> dict[str, tuple[str, str]]:
    """note id -> (its source item, its verified passage) for each note behind a claim: a quote note's text, a
    paraphrase note's original span."""
    out: dict[str, tuple[str, str]] = {}
    for ev in claim["evidence"]:
        for n in ev["notes"]:
            if n is None:
                continue
            if n["kind"] == "quote":
                out[n["id"]] = (n["item_id"], n["text"])
            elif n["kind"] == "paraphrase" and n.get("source_span"):
                out[n["id"]] = (n["item_id"], n["source_span"])
    return out


def evidence_texts(claim: dict) -> list[str]:
    """The verified passages behind a claim: quote notes' text, paraphrase notes' original span."""
    return [text for _, text in _evidence_notes(claim).values()]


def _heading(who: dict, n: int, k: int) -> str:
    """A study group's heading in the judges' evidence document (``evidence_document``)."""
    out = f"Study {n} of {k}: {who['label']}"
    if who["title"] and who["title"] != who["label"]:
        out += f': "{who["title"]}"'
    if who["year"]:
        out += f" ({who['year']})"
    if who["design"]:
        out += f"; design {who['design']}"
    return out + "".join(f"; {f} {v}" for f, v in who["arms"].items())


def evidence_document(ps, claim: dict) -> str:
    """The evidence the V-SUPPORT judges read: the claim's verified passages (``evidence_texts``, unchanged), grouped
    by study in the order the evidence first cites each, every group headed by the study's identity
    (``rk.production.synthesis.study_identities``): "Study 1 of 2: <label>: "<title>" (<year>); design <design>;
    <arm field> <value>; ...". V-NUM still checks the passages only.

    Calibration (synthesis across studies, 2026-11-13): given bare passages, the judges could not count the studies a
    finding rests on nor tell which arm a passage is about (76 of 106 non-supported opinions on cross-study findings
    turned on breadth; "the evidence text never names the groups")."""
    # synthesis imports this module
    from rk.production.synthesis import study_identities

    by_study: dict[str, list[str]] = {}
    for item, text in _evidence_notes(claim).values():
        by_study.setdefault(item, []).append(text)
    who = study_identities(ps, list(by_study))
    return "\n\n".join(f"{_heading(who[item], n, len(by_study))}\n" + "\n\n".join(texts)
                       for n, (item, texts) in enumerate(by_study.items(), 1))


def verify_support(kernel, registry, ps, claim_id: str, *, actor: str,
                   judge_drivers: list[DriverSpec] | None = None, judge_providers: dict | None = None,
                   min_judges: int | None = None, checkpoint: bool = True,
                   run: JudgeRunner | None = None,
                   exclude_vendors: Collection[str | None] = ()) -> SupportResult:
    """Check the claim against its linked evidence and record V-NUM, V-SUPPORT and the claim status. In collab mode an
    ``uncertain`` verdict opens an ``uncertain_support`` checkpoint unless ``checkpoint`` is false (the gap miners:
    their claims are evidence checks of a candidate gap, not claims of the argument). ``run`` replaces ``run_task`` for
    the judge tasks (``run_judges``), e.g. a stage's task executor; its exceptions propagate, and no verification of
    the claim is recorded then. The judges exclude the claim's generator vendor and ``exclude_vendors`` (e.g. the
    vendor of the claim a rewrite narrows). A ``stat_result`` claim is decided by V-STATS and V-NUM
    (``_verify_stat``). A withdrawn claim is refused (``ValueError``) before any judge runs or anything is recorded."""
    svc = ArgumentService(ps)
    claim = svc.get_claim(claim_id)
    if claim["status"] == "withdrawn":
        raise ValueError(f"claim {claim_id} is withdrawn: it is out of the argument and is not verified")
    if claim.get("kind") == "stat_result":
        return _verify_stat(ps, svc, claim, actor=actor)
    k = kernel.settings().support_min_judges if min_judges is None else min_judges
    texts = evidence_texts(claim)
    if not texts:
        result = SupportResult(verdict="uncertain", min_judges=k, reason="no_evidence")
    else:
        doc = evidence_document(ps, claim)
        opinions = run_judges(kernel, registry, project=_slug(ps), claim_text=claim["text"], evidence_text=doc,
                              generator_vendor=claim.get("generator_vendor"), drivers=judge_drivers,
                              providers=judge_providers, run=run, exclude_vendors=exclude_vendors,
                              span_text="\n\n".join(texts))  # the headings are the kernel's, not evidence
        verdict = combine(opinions, min_judges=k)
        nums = check_numbers(claim["text"], texts,  # a count of the cited studies is grounded by the citations
                             counts={len({item for item, _ in _evidence_notes(claim).values()})})
        if verdict == "supported" and not nums.ok:
            verdict = "uncertain"
        result = SupportResult(verdict=verdict, opinions=opinions, numbers_ok=nums.ok,
                               ungrounded_numbers=nums.ungrounded, min_judges=k)
        record_verification(ps, "claim", claim_id, "V-NUM", "pass" if nums.ok else "ungrounded_number",
                            {"ungrounded": [n.model_dump() for n in nums.ungrounded]}, actor=actor)
    vid = record_verification(ps, "claim", claim_id, "V-SUPPORT", result.verdict, result.model_dump(mode="json"),
                              actor=actor)
    svc.set_claim_status(claim_id, result.verdict, verification_id=vid, actor=actor)
    if checkpoint and result.verdict == "uncertain" and (ps.meta("production_mode") or "collab") == "collab":
        create_checkpoint(ps, "uncertain_support", f"Is this claim supported by its evidence?\n\n{claim['text']}",
                          {"claim_id": claim_id, "opinions": [o.model_dump() for o in result.opinions],
                           "reason": result.reason}, stage="argue", actor=actor)
    return result


def _stat_numbers(ps, claim: dict, ids: list[str]):
    """V-NUM of a ``stat_result``: each printed number against the artifact ``display`` value its number binding (the
    kernel template's, ``rk.verify.stats.stat_claim_text``) names. A claim without bindings (stored before they were
    required) grounds nothing."""
    displays = {a: (artifact(ps, a)["values"].get("display") or {}) for a in ids}
    bindings = claim.get("number_bindings") or []
    if not bindings:
        return NumResult(ok=False, ungrounded=[], problems=["the stat_result claim has no number bindings"])

    def bound(b: dict) -> float | None:
        v = displays.get(b["artifact_id"], {}).get(b["key"])
        return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None

    return check_bound_numbers(claim["text"], [(b["text"], bound(b)) for b in bindings])


def _verify_stat(ps, svc: ArgumentService, claim: dict, *, actor: str) -> SupportResult:
    """A ``stat_result`` claim: V-STATS on every artifact it reports, then V-NUM of its text against their display
    values (``_stat_numbers``). No judge runs (``min_judges`` 0); the verdict is ``supported`` or ``unsupported``."""
    cid, ids = claim["id"], list(claim.get("artifact_ids") or [])
    stats = {a: check_stats(ps, a, actor=actor) for a in ids}
    nums = _stat_numbers(ps, claim, ids)
    reason = ("no_artifact" if not ids else "stats_mismatch" if not all(s.ok for s in stats.values())
              else None if nums.ok else "ungrounded_number")
    result = SupportResult(verdict="unsupported" if reason else "supported", numbers_ok=nums.ok,
                           ungrounded_numbers=nums.ungrounded, min_judges=0, reason=reason)
    record_verification(ps, "claim", cid, "V-NUM", "pass" if nums.ok else "ungrounded_number",
                        {"ungrounded": [n.model_dump() for n in nums.ungrounded], "problems": nums.problems,
                         "artifact_ids": ids, "bound": bool(claim.get("number_bindings"))}, actor=actor)
    vid = record_verification(ps, "claim", cid, "V-SUPPORT", result.verdict,
                              {"by": "V-STATS", "artifacts": {a: s.model_dump() for a, s in stats.items()},
                               **result.model_dump(mode="json")}, actor=actor)
    svc.set_claim_status(cid, result.verdict, verification_id=vid, actor=actor)
    return result


# A stat_result claim's source is its artifact: kernel code computed the value, which the claim states plainly.
STAT_SOURCE_LEVEL = 2


def check_claim_hedge(ps, claim_id: str, *, actor: str = "kernel", record: bool = True) -> HedgeResult:
    """V-HEDGE of a stored claim, recorded on it (``pass`` / ``overstated``) unless ``record`` is false. A withdrawn
    claim is refused (``ValueError``).

    * Level: the claim's level is at most the highest level of its evidence notes' verified passages
      (``check_hedge``); a ``stat_result`` claim's source level is ``STAT_SOURCE_LEVEL`` (its artifacts). A claim
      with neither is overstated (``reason="no_evidence"``).
    * Universal quantifiers the claim states positively (``universal_terms``: "all", "every", "never", "always",
      "none") need evidence from at least two sources (distinct note items), and each must itself be stated
      positively by an evidence note: the same term or a declared synonym (``SYNONYMS``: none / never / no)."""
    claim = ArgumentService(ps).get_claim(claim_id)
    if claim["status"] == "withdrawn":
        raise ValueError(f"claim {claim_id} is withdrawn: it is out of the argument and is not checked")
    notes = _evidence_notes(claim)
    level = hedge_level(claim["text"])
    if notes:
        r = check_hedge(claim["text"], [text for _, text in notes.values()])
    elif claim.get("kind") == "stat_result":
        r = HedgeResult(ok=level <= STAT_SOURCE_LEVEL, claim_level=level, source_level=STAT_SOURCE_LEVEL)
    else:
        r = HedgeResult(ok=False, claim_level=level, source_level=None, reason="no_evidence",
                        problems=["the claim has no evidence note to compare its wording with"])
    problems = list(r.problems)
    if r.source_level is not None and not r.ok:
        problems.append(f"the claim's level {r.claim_level} is above its evidence's highest level {r.source_level}")
    universal = universal_terms(claim["text"])
    if universal:
        said = ", ".join(f'"{u}"' for u in universal)
        sources = {item for item, _ in notes.values()}
        if len(sources) < 2:
            problems.append(f"the universal {said} needs evidence from at least two sources (it has {len(sources)})")
        stated = {t for _, text in notes.values() for t in universal_terms(text)}
        problems += [f'the universal "{u}" is not stated by any evidence note' for u in universal
                     if not SYNONYMS[u] & stated]
    result = r.model_copy(update={"ok": not problems, "universal": universal, "problems": problems})
    if record:
        record_verification(ps, "claim", claim_id, "V-HEDGE", "pass" if result.ok else "overstated",
                            result.model_dump(), actor=actor)
    return result


@dataclass(frozen=True)
class ClaimProblem:
    """Why a claim fails the claim gate: ``kind``, the gate's short ``line`` and the full ``detail`` (for a narrowing
    task and a withdrawal reason); ``ids``: the artifacts or objections it concerns."""
    kind: Literal["status", "hedge", "stats", "objection", "conceded"]
    line: str
    detail: str
    ids: tuple[str, ...] = ()


def _support_reason(ps, claim_id: str) -> str:
    r = ps.db.execute("SELECT details FROM verifications WHERE target_id=? AND verifier='V-SUPPORT' "
                      "ORDER BY rowid DESC LIMIT 1", (claim_id,)).fetchone()
    reason = json.loads(r["details"]).get("reason") if r else None
    return f" ({reason})" if reason else ""


def dropped_links(ps, attempt: int | None = None) -> set[str]:
    """The reason links argue dropped from an argument (its ``argue.noted`` ``dropped_link`` notes: a failed warrant
    conceded or left untested, or a priority-1 objection on the link or its warrant conceded), of ``attempt`` or of
    every attempt (an edge belongs to the one attempt that stored it)."""
    sql = ("SELECT json_extract(payload, '$.edge_id') AS e FROM events WHERE type='argue.noted' "
           "AND json_extract(payload, '$.kind')='dropped_link'")
    rows = (ps.db.execute(sql) if attempt is None
            else ps.db.execute(sql + " AND json_extract(payload, '$.attempt')=?", (attempt,)))
    return {r["e"] for r in rows}


def link_gone(ps, edge_id: str, *, dropped: set[str] | None = None) -> bool:
    """Whether an edge is out of the argument, the one definition argue and the claim gate share: a claim at either
    end is withdrawn (an evidence end: the claim it supports; an edge end, a warrant's: that edge is out), or it is a
    reason link argue dropped (``dropped_links``, read once unless ``dropped`` is given). An unknown id is not out."""
    dropped = dropped_links(ps) if dropped is None else dropped

    def out(end_id: str) -> bool:
        table = {"edg": "edges", "evd": "evidence"}.get(end_id.split("_")[0], "claims")
        r = ps.db.execute(f"SELECT data FROM {table} WHERE id=?", (end_id,)).fetchone()
        if r is None:
            return False
        row = json.loads(r["data"])
        if table == "edges":
            return end_id in dropped or out(row["from_id"]) or out(row["to_id"])
        if table == "evidence":
            return out(row["claim_id"])
        return row["status"] == "withdrawn"

    return edge_id.startswith("edg_") and out(edge_id)


def claim_problems(ps, claim_id: str, *, hedge: HedgeResult | None = None,
                   stats: dict[str, StatsResult] | None = None) -> list[ClaimProblem]:
    """Why the claim fails the claim gate, the one definition ``gate_claims`` formats: its status is not
    ``supported`` (with V-SUPPORT's recorded reason); V-HEDGE, rerun now, is not ``pass``; for a ``stat_result``,
    V-STATS, rerun now on each of its artifacts, finds a mismatch; a priority-1 objection is still open against it,
    its evidence or an edge into it; or one was conceded on it or its evidence (the objection holds, so the claim
    fails until it is narrowed or withdrawn). A conceded one on a reason link into it fails neither claim (conceding
    it drops the link from the argument), and an edge out of the argument (``link_gone``: an end withdrawn, or a
    dropped link) counts for no problem. The reruns record nothing and call no model; a caller that already reran
    V-HEDGE (``hedge``) or V-STATS (``stats``: artifact id -> result) passes them in. ``[]`` for a withdrawn claim (out
    of the argument); an unknown id is a ``KeyError``. Edges and objections are read with their legacy shapes tolerated
    (no typed warrant, hand-added ``response_to`` edges, objections without a priority: priority 1)."""
    svc = ArgumentService(ps)
    claim = svc.get_claim(claim_id)
    if claim["status"] == "withdrawn":
        return []
    out = []
    if claim["status"] != "supported":
        out.append(ClaimProblem("status", f"status {claim['status']} (not supported)",
                                f"V-SUPPORT {claim['status']}{_support_reason(ps, claim_id)}"))
    hedge = hedge if hedge is not None else check_claim_hedge(ps, claim_id, record=False)
    if not hedge.ok:
        out.append(ClaimProblem("hedge", "V-HEDGE overstated",
                                "V-HEDGE overstated: " + "; ".join(hedge.problems or [hedge.reason or "overstated"])))
    if claim.get("kind") == "stat_result":
        stats = stats or {}
        out += [ClaimProblem("stats", f"V-STATS mismatch {a}", f"V-STATS mismatch {a}", (a,))
                for a in claim.get("artifact_ids") or []
                if not (stats[a] if a in stats else check_stats(ps, a, record=False)).ok]
    dropped = dropped_links(ps)
    edges = [e for e in svc.list_edges(to_id=claim_id) if e.get("id") and not link_gone(ps, e["id"], dropped=dropped)]
    links = {e["id"] for e in edges if e.get("type") == "reason_of"}
    targets = [claim_id, *(e["id"] for e in claim["evidence"]), *(e["id"] for e in edges)]
    p1 = [o for t in targets if t for o in svc.list_objections(target_id=t) if o.get("priority", 1) == 1]
    open_p1 = [o for o in p1 if o.get("status", "open") == "open"]
    if open_p1:
        out.append(ClaimProblem("objection", "open priority-1 objection " + ", ".join(o["id"] for o in open_p1),
                                "; ".join(f"open priority-1 objection {o['id']}: {o['text']}" for o in open_p1),
                                tuple(o["id"] for o in open_p1)))
    conceded = [o for o in p1 if o.get("status") == "conceded" and o["target_id"] not in links]
    if conceded:
        out.append(ClaimProblem("conceded", "conceded priority-1 objection " + ", ".join(o["id"] for o in conceded),
                                "; ".join(f"conceded priority-1 objection {o['id']} (it holds, so the claim must be "
                                          f"narrowed or withdrawn): {o['text']}" for o in conceded),
                                tuple(o["id"] for o in conceded)))
    return out


def gate_claims(ps, claim_ids: list[str]) -> list[str]:
    """One line per claim that fails the claim gate, naming each of its problems (``claim_problems``)."""
    out = []
    for cid in claim_ids:
        if problems := claim_problems(ps, cid):
            out.append(f"{cid}: " + "; ".join(p.line for p in problems))
    return out


def _slug(ps) -> str:
    return ps.meta("slug") or ps.root.name
