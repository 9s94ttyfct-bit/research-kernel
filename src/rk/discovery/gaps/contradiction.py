"""Contradictions: the GapMiner pairs works of one cluster whose findings disagree, each with a verbatim quote. The
kernel keeps both quotes as quote notes (V-QUOTE), stores quote A as a gap-mining claim with note B as its evidence
and runs V-SUPPORT (two agreeing cross-vendor judges, no checkpoint). The pair is verified only when the verdict is
"contradicted"."""

from __future__ import annotations

from rk.discovery.gaps import (
    PASS,
    READ_TOOLS,
    Checks,
    MinerResult,
    SupportMiner,
    fulltext_clusters,
    quote_note,
    task_context,
    title_year,
)
from rk.discovery.models import ContradictionOut, ContradictionsOut, Gap, GapEvidence
from rk.llm.vendors import vendor_of
from rk.orchestrator.engine import StageContext

MAX_PAIRS = 4  # pairs checked per attempt: each costs two judge tasks
CHECKS = ("same_cluster", "quote_a", "quote_b", "support")

MINE_TASK = (
    "Find pairs of works in the same cluster whose findings contradict each other on the same question (for example "
    "one reports an effect and the other reports none, or effects in opposite directions). Read each work's text "
    "with rk_text_read (pages numbered from 1) and rk_text_find. For each pair give the cluster_id, and as a and b "
    "each work's item id as source_id with a quote of one or two sentences copied character for character from that "
    "work's text that states its finding (the kernel rejects a quote that is not verbatim). Pair only works of the "
    "same cluster, never a work with itself. At most 4 pairs, the clearest contradiction first; none when no finding "
    "contradicts another. Follow the guidance when there is any.")


def _check(ctx: StageContext, miner: SupportMiner, p: ContradictionOut, members: dict[str, set[str]],
           vendor: str | None) -> tuple[Gap, bool, dict]:
    checks = Checks(CHECKS)
    data: dict = {"claim_id": None, "verdict": None, "p": None, "opinions": []}
    details: dict = {"a": p.a.model_dump(), "b": p.b.model_dump()}
    evidence = GapEvidence()

    def result(statement: str, cluster_id: str | None = None) -> tuple[Gap, bool, dict]:
        gap = Gap(kind="contradiction", statement=statement, cluster_id=cluster_id, evidence=evidence,
                  details=details)
        return gap, checks.ok, checks.dump(**data)

    ids = members.get(p.cluster_id)
    if ids is None:
        checks.failed("same_cluster", f"unknown cluster {p.cluster_id}")
    elif p.a.source_id == p.b.source_id:
        checks.failed("same_cluster", "a and b are the same work")
    elif not {p.a.source_id, p.b.source_id} <= ids:
        checks.failed("same_cluster", f"a and b are not both works of cluster {p.cluster_id}")
    else:
        checks.passed("same_cluster")
    if checks.values["same_cluster"] != PASS:
        return result(f'Possible contradiction: "{p.a.quote}" vs "{p.b.quote}"')

    evidence.item_ids = [p.a.source_id, p.b.source_id]
    a_label, b_label = title_year(ctx.lib, p.a.source_id), title_year(ctx.lib, p.b.source_id)
    possible = f'Possible contradiction between {a_label}: "{p.a.quote}" and {b_label}: "{p.b.quote}"'
    note_a = quote_note(ctx, checks, "quote_a", p.a.source_id, p.a.quote, role_in_source="main",
                        keywords=["finding", "contradiction"])
    note_b = quote_note(ctx, checks, "quote_b", p.b.source_id, p.b.quote, role_in_source="main",
                        keywords=["finding", "contradiction"])
    evidence.note_ids = [n for n in (note_a, note_b) if n is not None]
    if note_a is None or note_b is None:
        return result(possible, p.cluster_id)

    res = miner.check_support(ctx, p.a.quote, [note_b], generator_vendor=vendor, checks=checks, data=data)
    if res is None:
        return result(possible, p.cluster_id)
    evidence.claim_ids = [data["claim_id"]]
    details["claim_id"] = data["claim_id"]
    if res.verdict != "contradicted":
        checks.failed("support", f"V-SUPPORT verdict {res.verdict}, not contradicted")
        return result(possible, p.cluster_id)
    checks.passed("support")
    return result(f'{a_label} and {b_label} report contradicting findings: the first states "{p.a.quote}", the '
                  f'second "{p.b.quote}"; V-SUPPORT (two cross-vendor judges agreeing) found the second contradicts '
                  "the first.", p.cluster_id)


class ContradictionMiner(SupportMiner):
    def __call__(self, ctx: StageContext, clusters: list[dict]) -> MinerResult:
        sources = fulltext_clusters(ctx, clusters, min_works=2)
        if not sources:
            return MinerResult()  # no cluster has two works with a full text to compare
        res = ctx.executor.run("gapminer", MINE_TASK, {**task_context(ctx), "clusters": sources}, ContradictionsOut,
                               stage=ctx.stage, attempt=ctx.attempt, tools=READ_TOOLS)
        if not res.ok or res.output is None:
            return MinerResult(problems=[f"the gapminer gave no contradictions ({res.error})"])
        members = {c["id"]: set(c["item_ids"]) for c in clusters}
        vendor = vendor_of(res.provider, res.model)
        return MinerResult(gaps=[_check(ctx, self, p, members, vendor) for p in res.output.pairs[:MAX_PAIRS]])
