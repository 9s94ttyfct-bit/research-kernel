"""Citation distortion: the GapMiner quotes what a cluster work says a cited work found and names the cited work (item
id or DOI). The kernel keeps the citing quote as a quote note (V-QUOTE), adds the cited work if needed and requires it
to pass V-REF, fetches its full text and picks its passages itself (``top_passages``, Plan 4 deviation 4: the miner
cannot cherry-pick), keeps every one as a quote note at its position and runs V-SUPPORT of the quote against them (two
agreeing cross-vendor judges, no checkpoint). Verified when the verdict is "unsupported" or "contradicted"."""

from __future__ import annotations

from rk.discovery.boundary import per_item
from rk.discovery.gaps import (
    READ_TOOLS,
    Checks,
    MinerResult,
    SupportMiner,
    cluster_of,
    fulltext_clusters,
    quote_note,
    task_context,
    title_year,
)
from rk.discovery.models import DistortionOut, DistortionsOut, Gap, GapEvidence
from rk.discovery.passages import top_passages
from rk.library.models import Ids
from rk.llm.vendors import vendor_of
from rk.notes import Locator
from rk.orchestrator.engine import StageContext

MAX_ITEMS = 4  # statements checked per attempt: each costs two judge tasks
PASSAGES = 5  # passages of the cited work the statement is checked against
DISTORTED = frozenset({"unsupported", "contradicted"})
CHECKS = ("citing_quote", "cited", "cited_ref", "fulltext", "passages", "support")

MINE_TASK = (
    "Find statements in the given works that report what another, cited work found (for example \"Smith et al. "
    "showed that ...\"), where the statement may be stronger, broader or more certain than the cited work supports. "
    "Read each work's text with rk_text_read (pages numbered from 1) and rk_text_find, including its reference list. "
    "For each statement give the work's item id as citing_source_id, the statement as quote, one or two sentences "
    "copied character for character from the text (the kernel rejects a quote that is not verbatim), and as cited "
    "the cited work's item_id when it is one of the given works, else its doi from the reference list. Do not quote "
    "the cited work: the kernel reads it itself. At most 4 statements, the most doubtful first. Follow the guidance "
    "when there is any.")


def _cited_id(ctx: StageContext, it: DistortionOut, checks: Checks) -> str | None:
    """The cited work's item id: a library item, else the work added by its DOI; records the check ``cited``."""
    cited = None
    if it.cited.item_id:
        try:
            cited = ctx.lib.get(it.cited.item_id).id
        except KeyError:
            checks.failed("cited", f"unknown item {it.cited.item_id}")
            return None
    elif it.cited.doi:
        doi = it.cited.doi
        cited, err = per_item(f"adding the cited work {doi}",
                              lambda: ctx.lib.find_by_ids(Ids(doi=doi)) or ctx.sources.add(doi))
        if err:
            checks.failed("cited", err)
            return None
        if cited is None:
            checks.failed("cited", f"DOI {doi} resolves to no work")
            return None
    else:
        checks.failed("cited", "no item id or DOI of the cited work")
        return None
    if cited == it.citing_source_id:
        checks.failed("cited", "a work cannot be its own cited work")
        return None
    checks.passed("cited")
    return cited


def _check(ctx: StageContext, miner: SupportMiner, it: DistortionOut, of: dict[str, str],
           vendor: str | None) -> tuple[Gap, bool, dict]:
    checks = Checks(CHECKS)
    data: dict = {"cited_id": None, "ref_status": None, "passage_note_ids": [], "rejected_passages": [],
                  "claim_id": None, "verdict": None, "p": None, "opinions": []}
    details: dict = {"citing_source_id": it.citing_source_id, "quote": it.quote, "cited": it.cited.model_dump(),
                     "cited_id": None}
    evidence = GapEvidence()

    def result(statement: str, cluster_id: str | None = None) -> tuple[Gap, bool, dict]:
        gap = Gap(kind="citation_distortion", statement=statement, cluster_id=cluster_id, evidence=evidence,
                  details=details)
        return gap, checks.ok, checks.dump(**data)

    cluster_id = of.get(it.citing_source_id)
    if cluster_id is None:
        checks.failed("citing_quote", "not a cluster item")
        return result(f'Possible citation distortion: "{it.quote}"')
    evidence.item_ids = [it.citing_source_id]
    citing = title_year(ctx.lib, it.citing_source_id)
    citing_note = quote_note(ctx, checks, "citing_quote", it.citing_source_id, it.quote,
                             role_in_source="summary_of_other", keywords=["citation", "citation_distortion"])
    if citing_note is None:
        return result(f'Possible citation distortion: {citing} states "{it.quote}"', cluster_id)
    evidence.note_ids = [citing_note]

    cited = _cited_id(ctx, it, checks)
    if cited is None:
        return result(f'Possible citation distortion: {citing} states "{it.quote}"', cluster_id)
    details["cited_id"] = data["cited_id"] = cited
    evidence.item_ids.append(cited)
    cited_label = title_year(ctx.lib, cited)
    possible = f'Possible citation distortion: {citing} cites {cited_label} for "{it.quote}"'
    data["ref_status"], err = per_item(f"V-REF of the cited work {cited}", lambda: ctx.sources.verify(cited))
    if err:
        checks.failed("cited_ref", err)
        return result(possible, cluster_id)
    if data["ref_status"] != "pass":  # V-REF: otherwise the work checked may not be the paper the source cites
        checks.failed("cited_ref", f"V-REF of the cited work is {data['ref_status']}, not pass")
        return result(possible, cluster_id)
    checks.passed("cited_ref")
    has_text, err = per_item(f"full text of the cited work {cited}", lambda: ctx.sources.fulltext(cited))
    if err:
        checks.failed("fulltext", err)
        return result(possible, cluster_id)
    passages = top_passages(ctx.ps, cited, it.quote, k=PASSAGES) if has_text else []
    if not passages:
        checks.failed("fulltext", "the cited work has no full text", value="missing")
        return result(possible, cluster_id)
    checks.passed("fulltext")

    notes = []
    for p in passages:  # the passages keep the positions the kernel chose them at
        passage_checks = Checks(["passage"])
        nid = quote_note(ctx, passage_checks, "passage", cited, p.text, role_in_source="main",
                         keywords=["cited_passage", "citation_distortion"],
                         locator=Locator(page=p.page, start=p.start, end=p.end))
        if nid is None:
            data["rejected_passages"].append({"page": p.page, "start": p.start, "end": p.end,
                                              "reason": passage_checks.reasons["passage"]})
        else:
            notes.append(nid)
    data["passage_note_ids"] = notes
    evidence.note_ids += notes
    if data["rejected_passages"]:  # checking against fewer passages than the kernel chose could hide support
        checks.failed("passages", f"{len(data['rejected_passages'])} of {len(passages)} passages of the cited work "
                                  "failed V-QUOTE")
        return result(possible, cluster_id)
    checks.passed("passages")

    res = miner.check_support(ctx, it.quote, notes, generator_vendor=vendor, checks=checks, data=data)
    if res is None:
        return result(possible, cluster_id)
    evidence.claim_ids = [data["claim_id"]]
    details["claim_id"] = data["claim_id"]
    if res.verdict not in DISTORTED:
        checks.failed("support", f"V-SUPPORT verdict {res.verdict}, not unsupported or contradicted")
        return result(possible, cluster_id)
    checks.passed("support")
    return result(f'{citing} cites {cited_label} for "{it.quote}", but the cited work does not support it (V-SUPPORT: '
                  f"{res.verdict}, two cross-vendor judges agreeing); no supporting passage was found within the "
                  f"searched scope (the {len(notes)} passages of its full text most similar to the statement).",
                  cluster_id)


class DistortionMiner(SupportMiner):
    def __call__(self, ctx: StageContext, clusters: list[dict]) -> MinerResult:
        sources = fulltext_clusters(ctx, clusters)
        if not sources:
            return MinerResult()  # no citing work with a full text (the limitation miner reports it)
        res = ctx.executor.run("gapminer", MINE_TASK, {**task_context(ctx), "clusters": sources}, DistortionsOut,
                               stage=ctx.stage, attempt=ctx.attempt, tools=READ_TOOLS)
        if not res.ok or res.output is None:
            return MinerResult(problems=[f"the gapminer gave no cited statements ({res.error})"])
        of = cluster_of(clusters)
        vendor = vendor_of(res.provider, res.model)
        return MinerResult(gaps=[_check(ctx, self, it, of, vendor) for it in res.output.items[:MAX_ITEMS]])
