"""V-PRISMA (spec §9.1): the PRISMA flow of a ``search_screen`` attempt, its arithmetic, and the notes PRISMA
reporting needs; and, for the paper types without PRISMA counts (critical review, research proposal), the check
that every selection decision carries its reason.

``prisma_counts`` counts each box of the flow from what the attempt stored, independently of the others, so that
``check_prisma`` can find a box that does not follow from the one before:

- identified: every hit of every source and query; duplicates removed: every later hit of a work
  (``rk.production.search.search_counts``);
- screened: the records with a title/abstract final; excluded at title/abstract: those excluded (language rule, the
  screeners, a vote or you);
- sought: the title/abstract includes, whose full text was sought; not retrieved: those with neither a full text nor
  an abstract (a record with an abstract but no full text is assessed on its abstract: ``abstract_only``);
- assessed at full text: the records with a full-text final; excluded at full text by reason (the eligibility
  criterion the screeners named; a disagreement the P1 vote or you excluded); included.

The notes: the P1 vote's exclusions of screening disagreements per phase (``excluded_disagreement``, also counted in
their box: the limitations name the recall loss), the language exclusions, the abstract-only assessments, the
title/abstract exclusions without a reason, the screening rows carried over from interrupted attempts
(``rk.production.resume``), the searches that could not finish and the entries sources dropped as unreadable."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from rk.production.models import FULL_TEXT, PHASE_WORDS, PHASES, TITLE_ABSTRACT
from rk.production.search import (
    deduplicated,
    dropped_entries,
    incomplete_searches,
    search_counts,
)
from rk.production.store import (
    decisions,
    finals,
    frozen_protocol,
    retrieval_note,
    voted_out,
)
from rk.store.project import ProjectStore

VOTED_OUT = "screeners disagreed: excluded by the P1 vote (not reviewed by a human)"
HUMAN_OUT = "screeners disagreed: excluded by you"
NO_CRITERION = "no criterion named"


@dataclass(frozen=True)
class Prisma:
    """The PRISMA flow of an attempt (``prisma_counts``) and its notes. ``excluded_ft``: reason -> records (``""``:
    an exclusion without a reason); ``excluded_disagreement``: phase -> records the P1 vote excluded on a screening
    disagreement; ``carried``: screening decisions and finals carried over from interrupted attempts;
    ``dropped_entries``: source -> unreadable entries it returned."""
    identified: int
    duplicates_removed: int
    screened: int
    excluded_ta: int
    sought: int
    not_retrieved: int
    assessed_ft: int
    excluded_ft: dict[str, int]
    excluded_disagreement: dict[str, int]
    included: int
    excluded_language: int = 0
    abstract_only: int = 0
    unreasoned_ta: int = 0
    carried: int = 0
    incomplete_searches: list[str] = field(default_factory=list)
    dropped_entries: dict[str, int] = field(default_factory=dict)


def _criteria(ps: ProjectStore) -> dict[str, str]:
    """criterion id -> how an exclusion resting on it reads, from the frozen protocol's eligibility."""
    row = frozen_protocol(ps)
    if row is None:
        return {}
    elig = row["body"]["eligibility"]
    return ({c["id"]: f"{c['id']} not met: {c['text']}" for c in elig["include"]}
            | {c["id"]: f"{c['id']}: {c['text']}" for c in elig["exclude"]})


def _reasoned(final: dict, said: list[dict], phase: str) -> bool:
    """Whether an exclusion has a reason. One the screeners made (``single``, ``agreement``) has the reason of a
    screener who excluded it: a non-blank reason, or at full text the criterion it names (the final's own text only
    quotes them); a vote's, your or a rule's exclusion its own reason."""
    if final["decided_by"] in ("single", "agreement"):
        return any((d["reason"] or "").strip() or (phase == FULL_TEXT and d["criterion"])
                   for d in said if d["include"] == "no")
    return bool((final["reason"] or "").strip())


def _reason(final: dict, said: list[dict], labels: dict[str, str]) -> str:
    """The reason a full-text exclusion is counted under: ``""`` without a reason (``_reasoned``)."""
    if not _reasoned(final, said, FULL_TEXT):
        return ""
    if voted_out(final):
        return VOTED_OUT
    if final["decided_by"] == "human":
        return HUMAN_OUT
    if final["decided_by"] == "rule":
        return final["reason"]
    named = dict.fromkeys(d["criterion"] for d in said if d["include"] == "no" and d["criterion"])
    return "; ".join(labels.get(c, c) for c in named) or NO_CRITERION


def _said(ps: ProjectStore, attempt: int, phase: str) -> dict[str, list[dict]]:
    """record id -> the screener decisions on it in ``phase``."""
    out: dict[str, list[dict]] = {}
    for d in decisions(ps, attempt, phase=phase):
        out.setdefault(d["record_id"], []).append(d)
    return out


def prisma_counts(ps: ProjectStore, attempt: int) -> Prisma:
    """The PRISMA flow of the attempt's ``search_screen``, each box counted from the stored records, finals and the
    full-text retrieval note (a record counts as not retrieved or abstract-only only once its full text was sought)."""
    search = search_counts(ps, attempt)
    recs = {r["id"]: r for r in deduplicated(ps, attempt)}
    ta = [f for f in finals(ps, attempt, phase=TITLE_ABSTRACT) if f["record_id"] in recs]
    ft = [f for f in finals(ps, attempt, phase=FULL_TEXT) if f["record_id"] in recs]
    sought = {f["record_id"] for f in ta if f["include"] == "yes"}
    note = retrieval_note(ps, attempt) or {"not_retrieved": [], "abstract_only": []}
    said_ta, said_ft = _said(ps, attempt, TITLE_ABSTRACT), _said(ps, attempt, FULL_TEXT)
    labels = _criteria(ps)
    ta_out = [f for f in ta if f["include"] == "no"]
    carried = (sum(1 for d in decisions(ps, attempt) if d["carried_from"] is not None)
               + sum(1 for f in ta + ft if f["carried_from"] is not None))
    return Prisma(
        identified=search["identified"], duplicates_removed=search["duplicates_removed"], screened=len(ta),
        excluded_ta=len(ta_out), sought=len(sought),
        not_retrieved=sum(1 for r in note["not_retrieved"] if r in sought),
        assessed_ft=len(ft),
        excluded_ft=dict(Counter(_reason(f, said_ft.get(f["record_id"], []), labels)
                                 for f in ft if f["include"] == "no")),
        excluded_disagreement={p: sum(1 for f in fs if voted_out(f)) for p, fs in ((TITLE_ABSTRACT, ta),
                                                                                   (FULL_TEXT, ft))},
        included=sum(1 for f in ft if f["include"] == "yes"),
        excluded_language=sum(1 for f in ta_out if f["decided_by"] == "rule"),
        abstract_only=sum(1 for r in note["abstract_only"] if r in sought),
        unreasoned_ta=sum(1 for f in ta_out
                          if not _reasoned(f, said_ta.get(f["record_id"], []), TITLE_ABSTRACT)),
        carried=carried, incomplete_searches=incomplete_searches(ps, attempt),
        dropped_entries=dropped_entries(ps, attempt))


def _n(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def check_prisma(p: Prisma) -> list[str]:
    """V-PRISMA: why the flow's arithmetic does not hold, or an exclusion has no reason; ``[]`` when it passes."""
    out = []
    if p.identified - p.duplicates_removed != p.screened:
        out.append(f"identified {p.identified} - duplicates removed {p.duplicates_removed} = "
                   f"{p.identified - p.duplicates_removed}, but {p.screened} records were screened")
    if p.screened - p.excluded_ta != p.sought:
        out.append(f"screened {p.screened} - excluded at title/abstract {p.excluded_ta} = "
                   f"{p.screened - p.excluded_ta}, but {p.sought} were sought for their full text")
    if p.sought - p.not_retrieved != p.assessed_ft:
        out.append(f"sought {p.sought} - not retrieved {p.not_retrieved} = {p.sought - p.not_retrieved}, but "
                   f"{p.assessed_ft} were assessed at full text")
    excluded = sum(p.excluded_ft.values())
    if p.assessed_ft - excluded != p.included:
        out.append(f"assessed at full text {p.assessed_ft} - excluded at full text {excluded} = "
                   f"{p.assessed_ft - excluded}, but {p.included} were included")
    if p.unreasoned_ta:
        out.append(_n(p.unreasoned_ta, "title/abstract exclusion has", "title/abstract exclusions have") + " no reason")
    if blank := sum(n for reason, n in p.excluded_ft.items() if not reason.strip()):
        out.append(_n(blank, "full-text exclusion has", "full-text exclusions have") + " no reason")
    return out


def prisma_notes(p: Prisma) -> list[str]:
    """The notes PRISMA reporting needs, in order: the P1 vote's exclusions of screening disagreements, the language
    exclusions, the abstract-only assessments, the entries sources dropped as unreadable, the searches that could not
    finish and the screening decisions carried over from interrupted attempts."""
    notes = []
    if voted := sum(p.excluded_disagreement.values()):
        notes.append(f"{voted} excluded by the P1 vote on a screening disagreement (not reviewed by a human)")
    if p.excluded_language:
        notes.append(f"{p.excluded_language} excluded for language")
    if p.abstract_only:
        notes.append(f"{p.abstract_only} assessed on the abstract only")
    if p.dropped_entries:
        notes.append("unreadable entries dropped: " + ", ".join(f"{s} {n}" for s, n in p.dropped_entries.items()))
    if p.incomplete_searches:
        notes.append("incomplete searches: " + "; ".join(p.incomplete_searches))
    if p.carried:
        notes.append(f"{p.carried} screening decisions carried over from interrupted attempts")
    return notes


def flow(p: Prisma) -> str:
    """The PRISMA flow and its notes (``prisma_notes``) in one line (the stage's reason, the PRISMA notes)."""
    reasons = "; ".join(f"{reason or 'no reason'}: {n}" for reason, n in p.excluded_ft.items())
    out = (f"PRISMA: identified {p.identified}, duplicates removed {p.duplicates_removed}, screened {p.screened}, "
           f"excluded at title/abstract {p.excluded_ta}, sought {p.sought}, not retrieved {p.not_retrieved}, "
           f"assessed at full text {p.assessed_ft}, excluded at full text {sum(p.excluded_ft.values())}"
           + (f" ({reasons})" if reasons else "") + f", included {p.included}")
    notes = prisma_notes(p)
    return out + (f"; notes: {'; '.join(notes)}" if notes else "")


def check_selection_reasons(ps: ProjectStore, attempt: int) -> list[str]:
    """For the paper types without PRISMA counts: every selection decision carries its reason. A decision the
    screeners made (``single``, ``agreement``) needs every screener's reason; a record no screener decided (also when
    asked again) has the reason of the missing-decision rule its final states, in both phases. A vote's, your or a
    rule's decision needs its own reason. ``[]`` when complete."""
    said: dict[tuple[str, str], list[dict]] = {}
    for d in decisions(ps, attempt):
        said.setdefault((d["record_id"], d["phase"]), []).append(d)
    missing = []
    for phase in PHASES:
        for f in finals(ps, attempt, phase=phase):
            ds = said.get((f["record_id"], phase), [])
            if f["decided_by"] in ("single", "agreement") and ds:
                ok = all((d["reason"] or "").strip() for d in ds)
            else:
                ok = bool((f["reason"] or "").strip())
            if not ok:
                missing.append(f"{f['record_id']} ({PHASE_WORDS[phase]})")
    if not missing:
        return []
    return [_n(len(missing), "selection decision has", "selection decisions have") + " no selection reason: "
            + ", ".join(missing)]
