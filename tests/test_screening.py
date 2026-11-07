"""Plan 5a Task 5: title/abstract screening (single, dual and the scoping sample), the record cap, language
exclusions, stray and missing screener decisions, and disagreements (P2 checkpoints, P1 votes)."""

from __future__ import annotations

import pytest

from rk.checkpoints import answer_checkpoint, get_checkpoint, list_checkpoints
from rk.discovery.executor import RateCapped
from rk.library.models import Ids, ItemIn
from rk.orchestrator.engine import Orchestrator, default_workers
from rk.orchestrator.runs import RunManager
from rk.orchestrator.state import record_ran
from rk.production import screening, store
from rk.production.models import Protocol
from rk.production.resume import carry_attempts
from rk.production.screening import (
    BATCH,
    SearchScreenWorker,
    excluded_by_disagreement,
    include_differs,
    sample_size,
    screening_notes,
    switches_to_dual,
)
from rk.production.search import SearchResult, deduplicated
from rk.tools.builtin import build_registry
from rk.tools.registry import ToolError
from tests.discovery_fakes import FakeSources
from tests.production_fakes import (
    CappedAt,
    Screeners,
    a_protocol,
    make_frozen_project,
    production_ctx,
)

TA = "title_abstract"
SR_P1 = {"paper_type": "systematic_review", "production_mode": "auto"}
SR_P2 = {"paper_type": "systematic_review", "production_mode": "collab"}
SCOPING_P2 = {"paper_type": "scoping_review", "production_mode": "collab"}


@pytest.fixture
def env(tmp_home, replay_http, request):
    k, ps, lib, _ = make_frozen_project(tmp_home, replay_http, **getattr(request, "param", {}))
    yield k, ps, lib
    k.close()


def _work(title: str, i: int, *, language: str | None = None) -> ItemIn:
    return ItemIn(title=title, year=2010 + i % 10, venue="Age Ageing", abstract=f"Abstract of {title}.",
                  ids=Ids(doi=f"10.1000/w{i}"), language=language)


def _works(*decisions: str, n: int = 1) -> list[ItemIn]:
    """``n`` works per ``"a/b"`` decision pair."""
    return [_work(f"Aspirin study {i} [{d}]", i) for i, d in enumerate(d for d in decisions for _ in range(n))]


def _freeze(ps, **overrides) -> Protocol:
    body = a_protocol(**overrides)
    pid = store.add_protocol(ps, 1, body, version=1, actor="tester")
    store.freeze_protocol(ps, pid, actor="tester")
    return body


def _ctx(env, ex, src, *, attempt: int = 1):
    k, ps, lib = env
    src.lib = src.lib or lib  # the full-text phase asks the sources per item
    return production_ctx(k, ps, lib, ex, attempt=attempt, sources=src)


def _titles(env, attempt: int = 1) -> dict[str, str]:
    _, ps, lib = env
    return {r["id"]: lib.get(r["item_id"]).title for r in deduplicated(ps, attempt)}


def _finals(env, attempt: int = 1) -> dict[str, tuple[str, str, str]]:
    """title -> (include, decided_by, reason) of the title/abstract finals."""
    _, ps, _ = env
    t = _titles(env, attempt)
    return {t[f["record_id"]]: (f["include"], f["decided_by"], f["reason"]) for f in store.finals(ps, attempt, phase=TA)}


def _final_events(ps) -> int:
    return ps.db.execute("SELECT COUNT(*) FROM events WHERE type='screening.final'").fetchone()[0]


def _answer(ps, cid: str, answer: dict) -> dict:
    answer_checkpoint(ps, cid, answer, answered_by="human", actor="researcher")
    return get_checkpoint(ps, cid)


@pytest.mark.parametrize("env", [SR_P2], indirect=True)
def test_dual_screening_agreement_rules(env):
    _, ps, _ = env
    _freeze(ps)
    pairs = ["yes/yes", "yes/unclear", "unclear/yes", "no/no", "yes/no", "unclear/unclear", "no/unclear"]
    works = _works(*pairs) + [_work("Aspirine chez le sujet âgé [yes/yes]", 99, language="fr")]
    src = FakeSources({"aspirin": works})
    ex = Screeners()
    out = SearchScreenWorker().run(_ctx(env, ex, src))

    assert out.status == "waiting_human"
    f = _finals(env)
    by_pair = {t[t.rindex("[") + 1:-1]: v for t, v in f.items() if t.startswith("Aspirin study")}
    assert {p: v[:2] for p, v in by_pair.items()} == {
        "yes/yes": ("yes", "agreement"), "yes/unclear": ("yes", "agreement"), "unclear/yes": ("yes", "agreement"),
        "no/no": ("no", "agreement")}
    assert f["Aspirine chez le sujet âgé [yes/yes]"] == ("no", "rule", "language")  # never screened
    (cp,) = list_checkpoints(ps, "open")
    t = _titles(env)
    assert cp["kind"] == "screening_disagreements" and cp["id"] == out.checkpoint_id
    assert cp["options"]["phase"] == TA and cp["options"]["attempt"] == 1
    assert sorted(t[r][t[r].rindex("[") + 1:-1] for r in cp["options"]["record_ids"]) == [
        "no/unclear", "unclear/unclear", "yes/no"]

    for role in ("screener_a", "screener_b"):
        (call,) = ex.of(role)  # one batch each, both screeners, the French record left out
        assert len(call["inputs"]["records"]) == 7 and call["tools"] == []
        assert set(call["inputs"]["records"][0]) >= {"record_id", "title", "abstract", "year", "venue", "language"}
        assert call["inputs"]["eligibility"]["include"][0]["id"] == "I1"
    rows = store.decisions(ps, 1, phase=TA)
    assert len(rows) == 14 and {r["screener"] for r in rows} == {"screener_a", "screener_b"}
    assert {r["criterion"] for r in rows if r["include"] == "yes"} == {"I1"}
    assert not SearchScreenWorker().gate(ps, 1).ok  # three records await your decision


@pytest.mark.parametrize("env", [SR_P2], indirect=True)
def test_disagreements_batch_into_checkpoints_of_25(env):
    """Review Focus 2: 30 disagreements in P2 make two checkpoints (25 + 5); your answers settle exactly those
    records, answered in any order, an invalid answer gets a replacement, and applying an answer again changes
    nothing."""
    _, ps, _ = env
    _freeze(ps)
    src = FakeSources({"aspirin": _works("yes/no", n=30)
                       + [_work("Aspirin agreed [yes/yes]", 40), _work("Aspirin rejected [no/no]", 41)]})
    ex = Screeners()
    w = SearchScreenWorker()
    out = w.run(_ctx(env, ex, src))
    assert [len(c["inputs"]["records"]) for c in ex.of("screener_a")] == [BATCH, 7]

    first, second = list_checkpoints(ps, "open")
    assert out.status == "waiting_human" and out.checkpoint_id == first["id"]
    assert "30 screening disagreements" in out.reason and "2 checkpoints" in out.reason
    ids1, ids2 = first["options"]["record_ids"], second["options"]["record_ids"]
    assert (len(ids1), len(ids2)) == (25, 5) and not set(ids1) & set(ids2)
    assert "Aspirin study 0 [yes/no]" in first["prompt"] and "screener_b: no (E1)" in first["prompt"]

    # you answer the second checkpoint first, then the first one wrongly
    _answer(ps, second["id"], {"decisions": {r: "include" for r in ids2}})
    bad = {r: "exclude" for r in ids1[1:]} | {ids2[0]: "exclude"}
    cp1 = _answer(ps, first["id"], {"decisions": bad})
    res = w.on_answer(_ctx(env, ex, src), cp1)
    assert res.status == "waiting_human" and res.checkpoint_id not in (first["id"], second["id"])
    assert ids1[0] in res.reason and ids2[0] in res.reason
    again = w.on_answer(_ctx(env, ex, src), cp1)  # idempotent: the same replacement
    assert again.checkpoint_id == res.checkpoint_id
    (repl,) = list_checkpoints(ps, "open")
    assert repl["options"]["record_ids"] == ids1 and repl["options"]["replaces"] == first["id"]
    assert "could not be applied" in repl["prompt"]
    f = _finals(env)
    assert sum(1 for v in f.values() if v[1] == "human") == 5  # the second checkpoint's answer is applied

    repl = _answer(ps, repl["id"], {"decisions": {r: "exclude" for r in ids1}})
    done = w.on_answer(_ctx(env, ex, src), repl)
    assert done.status == "done"
    t, f = _titles(env), _finals(env)
    assert {t[r] for r in ids1} | {t[r] for r in ids2} == {k for k, v in f.items() if v[1] == "human"}
    assert all(f[t[r]][0] == "no" for r in ids1) and all(f[t[r]][0] == "yes" for r in ids2)
    assert (f["Aspirin agreed [yes/yes]"][:2], f["Aspirin rejected [no/no]"][:2]) == (("yes", "agreement"),
                                                                                    ("no", "agreement"))
    assert len(f) == len(deduplicated(ps, 1)) == 32  # every screened record has exactly one final decision
    n = _final_events(ps)
    assert w.on_answer(_ctx(env, ex, src), repl).status == "done" and _final_events(ps) == n
    assert w.gate(ps, 1).ok


@pytest.mark.parametrize("env", [SR_P1], indirect=True)
def test_p1_disagreement_votes_conservative_exclude(env):
    _, ps, _ = env
    _freeze(ps)
    works = [_work("Aspirin A [yes/no]", 1), _work("Aspirin B [no/unclear]", 2), _work("Aspirin C [yes/yes]", 3)]
    ex = Screeners(votes={"Aspirin A [yes/no]": ("include", "include"), "Aspirin B [no/unclear]": ("include", "exclude")})
    out = SearchScreenWorker().run(_ctx(env, ex, FakeSources({"aspirin": works})))

    assert out.status == "done" and not list_checkpoints(ps, "open")
    f = _finals(env)
    assert f["Aspirin A [yes/no]"][:2] == ("yes", "vote")
    assert f["Aspirin A [yes/no]"][2].startswith("screeners disagreed")
    assert f["Aspirin B [no/unclear]"][:2] == ("no", "vote")
    assert f["Aspirin B [no/unclear]"][2].startswith("screeners disagreed; P1 conservative")
    assert f["Aspirin C [yes/yes]"][:2] == ("yes", "agreement")
    t = {v: k for k, v in _titles(env).items()}
    (va,), (vb,) = store.votes(ps, "record", t["Aspirin A [yes/no]"]), store.votes(ps, "record", t["Aspirin B [no/unclear]"])
    assert (va["decision"], va["conservative"], vb["decision"], vb["conservative"]) == ("include", 0, "exclude", 1)
    assert [f["record_id"] for f in excluded_by_disagreement(ps, 1, TA)] == [t["Aspirin B [no/unclear]"]]
    judge = ex.of("judge")[0]["inputs"]
    assert judge["options"] == ["include", "exclude"]
    assert {s["screener"]: s["include"] for s in judge["subject"]["screeners"]} == {"screener_a": "yes",
                                                                                     "screener_b": "no"}
    assert "1 excluded by the P1 vote on a disagreement" in out.reason


def test_scoping_sample_rule():
    assert [sample_size(n) for n in (0, 1, 9, 10, 11, 20, 100)] == [0, 1, 1, 1, 2, 2, 10]
    assert not switches_to_dual(1, 10) and switches_to_dual(2, 10) and switches_to_dual(1, 2)
    assert not switches_to_dual(0, 0)


@pytest.mark.parametrize("env", [SCOPING_P2], indirect=True)
@pytest.mark.parametrize("b", ["yes", "no"])
def test_scoping_sample_switches_to_dual_above_10_percent(env, b):
    _, ps, _ = env
    body = _freeze(ps)
    src = FakeSources({"aspirin": _works(f"yes/{b}", n=20)})
    ex = Screeners()
    out = SearchScreenWorker().run(_ctx(env, ex, src))

    (note,) = screening_notes(ps, 1, kind="scoping_sample", phase=TA)
    assert (len(note["sample"]), note["disagreements"]) == (2, 0 if b == "yes" else 2)
    assert note["switched"] is (b == "no")
    b_records = [r["record_id"] for c in ex.of("screener_b") for r in c["inputs"]["records"]]
    a_records = [r["record_id"] for c in ex.of("screener_a") for r in c["inputs"]["records"]]
    assert sorted(a_records) == sorted(_titles(env))
    assert b_records[:2] == note["sample"]
    f = _finals(env)
    if b == "yes":  # the sample agrees: single screening for the rest
        assert out.status == "done" and len(b_records) == 2
        assert sorted(v[1] for v in f.values()) == ["agreement"] * 2 + ["single"] * 18
    else:  # 2 of 2 disagree (> 10%): every remaining record is screened by both
        assert out.status == "waiting_human" and sorted(b_records) == sorted(_titles(env))
        (cp,) = list_checkpoints(ps, "open")
        assert len(cp["options"]["record_ids"]) == 20 and f == {}

    # the sample is seeded by the protocol hash: another attempt over the same hits samples the same works (attempt 1
    # ran to completion, so attempt 2 carries nothing over and draws its sample afresh)
    record_ran(ps, "search_screen", 1, out.status, out.reason, actor="tester")
    item = {r["id"]: r["item_id"] for r in store.records(ps, 1)}
    fresh = Screeners()
    SearchScreenWorker().run(_ctx(env, fresh, src, attempt=2))
    assert _sizes(fresh, "screener_b")[0] == 2  # screened again, not carried
    (note2,) = screening_notes(ps, 2, kind="scoping_sample", phase=TA)
    item |= {r["id"]: r["item_id"] for r in store.records(ps, 2)}
    assert [item[r] for r in note2["sample"]] == [item[r] for r in note["sample"]]
    assert note["protocol_hash"] == store.protocol_hash(body.model_dump(mode="json"))


def test_single_screening_unclear_goes_to_full_text(env):  # critical_review, P1
    _, ps, _ = env
    _freeze(ps)
    works = [_work("Aspirin yes [yes/-]", 1), _work("Aspirin unclear [unclear/-]", 2), _work("Aspirin no [no/-]", 3),
             _work("Aspirin silent [-/-]", 4)]
    ex = Screeners(extra={"screener_a": [{"record_id": "rec_not_in_batch", "include": "yes", "reason": "?"}]})
    out = SearchScreenWorker().run(_ctx(env, ex, FakeSources({"aspirin": works})))

    assert out.status == "done" and ex.of("screener_b") == [] and ex.of("judge") == []
    f = _finals(env)
    assert f["Aspirin yes [yes/-]"][:2] == ("yes", "single")
    assert f["Aspirin unclear [unclear/-]"][:2] == ("yes", "single")
    assert "unclear" in f["Aspirin unclear [unclear/-]"][2] and "full text" in f["Aspirin unclear [unclear/-]"][2]
    assert f["Aspirin no [no/-]"][:2] == ("no", "single")
    # a record the screener leaves out is batched once more, then recorded and treated as unclear
    assert [len(c["inputs"]["records"]) for c in ex.of("screener_a")] == [4, 1]
    assert f["Aspirin silent [-/-]"][:2] == ("yes", "single") and "no decision" in f["Aspirin silent [-/-]"][2]
    t = {v: k for k, v in _titles(env).items()}
    (missing,) = screening_notes(ps, 1, kind="missing_decision")
    assert (missing["record_id"], missing["screener"]) == (t["Aspirin silent [-/-]"], "screener_a")
    assert "screener_a gave no title/abstract decision" in store.record_row(ps, t["Aspirin silent [-/-]"])["problem"]
    (stray,) = screening_notes(ps, 1, kind="stray_decision")  # a decision on a record not in the batch: ignored
    assert (stray["record_id"], stray["screener"]) == ("rec_not_in_batch", "screener_a")
    assert not any(d["record_id"] == "rec_not_in_batch" for d in store.decisions(ps, 1))
    assert isinstance(default_workers()["search_screen"], SearchScreenWorker)


@pytest.mark.parametrize("env", [SR_P2], indirect=True)
def test_dual_missing_decision_is_a_disagreement(env):
    _, ps, _ = env
    _freeze(ps)
    ex = Screeners()
    out = SearchScreenWorker().run(_ctx(env, ex, FakeSources({"aspirin": [_work("Aspirin quiet b [yes/-]", 1),
                                                                           _work("Aspirin both [yes/yes]", 2)]})))
    assert [len(c["inputs"]["records"]) for c in ex.of("screener_b")] == [2, 1]
    assert out.status == "waiting_human"
    t = {v: k for k, v in _titles(env).items()}
    (cp,) = list_checkpoints(ps, "open")
    assert cp["options"]["record_ids"] == [t["Aspirin quiet b [yes/-]"]]
    assert "screener_b: no decision" in cp["prompt"]


@pytest.mark.parametrize("env", [SR_P2], indirect=True)
def test_same_vendor_screeners_refused(env):
    from rk.config import save_settings

    k, ps, _ = env
    _freeze(ps)
    s = k.settings()
    save_settings(s.model_copy(update={"drivers": {**s.drivers, "screener_b": "claude_cli"}}))
    src, ex = FakeSources({"aspirin": _works("yes/yes")}), Screeners()
    out = SearchScreenWorker().run(_ctx(env, ex, src))
    assert out.status == "failed"
    assert "screener_a" in out.reason and "screener_b" in out.reason and "anthropic" in out.reason
    assert src.calls == [] and ex.calls == []  # before any search or screening

    reg = build_registry(k)
    m = RunManager(k, reg, Orchestrator(k, reg, executor_factory=lambda *a: ex,
                                        sources_factory=lambda project: src))
    try:
        with pytest.raises(ToolError) as e:  # a run that would reach search_screen fails at start
            m.start("demo", "search_screen", actor="tester")
        assert e.value.type == "screeners_unusable" and "anthropic" in e.value.message
    finally:
        m.close()


def test_single_screening_ignores_screener_vendors(env):  # critical_review: no dual screening, no vendor rule
    k, ps, _ = env
    _freeze(ps)
    from rk.config import save_settings

    s = k.settings()
    save_settings(s.model_copy(update={"drivers": {**s.drivers, "screener_b": "claude_cli"}}))
    out = SearchScreenWorker().run(_ctx(env, Screeners(), FakeSources({"aspirin": _works("yes/-")})))
    assert out.status == "done"


@pytest.mark.parametrize("env", [SR_P2], indirect=True)
def test_record_cap_backs_to_protocol(env):
    _, ps, _ = env
    _freeze(ps, record_cap=2)
    ex = Screeners()
    out = SearchScreenWorker().run(_ctx(env, ex, FakeSources({"aspirin": _works("yes/yes", n=3)})))
    assert (out.status, out.to) == ("back_edge", "protocol")
    assert "> cap 2" in out.reason and "narrow the search strategy" in out.reason
    assert ex.calls == []


WHY = "s2 returned only the first 1000 results for query 'aspirin': later results were not searched"


def _incomplete(monkeypatch) -> None:
    real = screening.run_strategy

    def incomplete(ctx, protocol):
        res = real(ctx, protocol)
        return SearchResult(res.record_ids, [WHY], res.stopped_at_cap)

    monkeypatch.setattr(screening, "run_strategy", incomplete)


def test_incomplete_searches_are_a_limitation_of_a_critical_review(env, monkeypatch):
    _, ps, _ = env
    _freeze(ps)
    _incomplete(monkeypatch)
    out = SearchScreenWorker().run(_ctx(env, Screeners(), FakeSources({"aspirin": _works("yes/-")})))
    assert out.status == "done" and WHY in out.reason and "incomplete search" in out.reason
    (note,) = screening_notes(ps, 1, kind="incomplete_searches")
    assert (note["searches"], note["phase"]) == ([WHY], None)


@pytest.mark.parametrize("env", [SR_P2], indirect=True)
def test_incomplete_search_blocks_systematic_screening(env, monkeypatch):
    _, ps, _ = env
    _freeze(ps)
    src = FakeSources({"aspirin": [_work("Aspirin split [yes/no]", 1)]})
    w = SearchScreenWorker()
    out = w.run(_ctx(env, Screeners(), src))  # complete search: the disagreement goes to you
    (cp,) = list_checkpoints(ps, "open")
    with monkeypatch.context() as m:
        _incomplete(m)
        ex = Screeners()
        blocked = w.run(_ctx(env, ex, src, attempt=2))
    assert (blocked.status, blocked.to) == ("back_edge", "protocol")
    assert WHY in blocked.reason and "Narrow it or drop the source" in blocked.reason
    assert ex.calls == [] and screening_notes(ps, 2, kind="incomplete_searches") == []

    # the same rule when an answer is applied
    assert out.status == "waiting_human"
    cp = _answer(ps, cp["id"], {"decisions": {r: "include" for r in cp["options"]["record_ids"]}})
    monkeypatch.setattr(screening, "incomplete_searches", lambda ps, attempt: [WHY])
    res = w.on_answer(_ctx(env, Screeners(), src), cp)
    assert (res.status, res.to) == ("back_edge", "protocol") and WHY in res.reason


# ---- resuming an interrupted attempt ----

def _sizes(ex: Screeners, role: str) -> list[int]:
    return [len(c["inputs"]["records"]) for c in ex.of(role)]


@pytest.mark.parametrize("env", [SR_P2], indirect=True)
def test_rate_capped_attempt_resumes_without_asking_again(env):
    _, ps, _ = env
    _freeze(ps)
    src = FakeSources({"aspirin": _works("yes/yes", n=55) + [_work(f"Aspirin no {i} [no/no]", 60 + i) for i in range(3)]
                      + [_work(f"Aspirin split {i} [yes/no]", 70 + i) for i in range(2)]})
    w = SearchScreenWorker()
    capped = CappedAt("screener_b", 2)
    with pytest.raises(RateCapped):  # screener_b's second batch: attempt 1 is interrupted (no stage.ran)
        w.run(_ctx(env, capped, src))
    assert (_sizes(capped, "screener_a"), _sizes(capped, "screener_b")) == ([25, 25, 10], [25])

    resumed = Screeners()
    out = w.run(_ctx(env, resumed, src, attempt=2))
    assert out.status == "waiting_human"
    assert (_sizes(resumed, "screener_a"), _sizes(resumed, "screener_b")) == ([], [25, 10])  # only what was left
    carried = [d for d in store.decisions(ps, 2, phase=TA) if d["carried_from"] == 1]
    assert len(carried) == 60 + 25 and len(store.decisions(ps, 2, phase=TA)) == 120
    record_ran(ps, "search_screen", 2, out.status, out.reason, actor="tester")

    fresh = Screeners()  # attempt 2 ran to completion: a rerun screens afresh
    w.run(_ctx(env, fresh, src, attempt=3))
    assert (_sizes(fresh, "screener_a"), _sizes(fresh, "screener_b")) == ([25, 25, 10], [25, 25, 10])
    assert not any(d["carried_from"] for d in store.decisions(ps, 3))
    assert _finals(env, 2) == _finals(env, 3) and len(_finals(env, 2)) == 58  # complete and identical
    (cp2,) = [c for c in list_checkpoints(ps, "open") if c["options"]["attempt"] == 2]
    assert len(cp2["options"]["record_ids"]) == 2


@pytest.mark.parametrize("env", [SR_P1], indirect=True)
def test_p1_votes_carry_over(env):
    _, ps, _ = env
    _freeze(ps)
    works = [_work(f"Aspirin D{i} [yes/no]", 10 + i) for i in (1, 2, 3)] + _works("yes/yes", n=2)
    votes = {"Aspirin D1 [yes/no]": ("include", "include"), "Aspirin D2 [yes/no]": ("include", "exclude"),
             "Aspirin D3 [yes/no]": ("exclude", "exclude")}
    src = FakeSources({"aspirin": works})
    with pytest.raises(RateCapped):  # the judge's second vote
        SearchScreenWorker().run(_ctx(env, CappedAt("judge", 2, votes=votes), src))

    resumed = Screeners(votes=votes)
    assert SearchScreenWorker().run(_ctx(env, resumed, src, attempt=2)).status == "done"
    assert _sizes(resumed, "screener_a") == _sizes(resumed, "screener_b") == []
    assert [c["inputs"]["subject"]["record"]["title"] for c in resumed.of("judge")] == [
        "Aspirin D2 [yes/no]", "Aspirin D3 [yes/no]"]
    f = _finals(env, 2)
    assert len(f) == 5
    assert {t: v[:2] for t, v in f.items() if t.startswith("Aspirin D")} == {
        "Aspirin D1 [yes/no]": ("yes", "vote"), "Aspirin D2 [yes/no]": ("no", "vote"),
        "Aspirin D3 [yes/no]": ("no", "vote")}
    t = _titles(env, 2)
    assert {t[r["record_id"]]: r["carried_from"] for r in store.finals(ps, 2, phase=TA)
            if t[r["record_id"]].startswith("Aspirin D")} == {"Aspirin D1 [yes/no]": 1, "Aspirin D2 [yes/no]": None,
                                                              "Aspirin D3 [yes/no]": None}


@pytest.mark.parametrize("env", [SR_P2], indirect=True)
def test_carry_over_needs_the_same_protocol(env):
    _, ps, _ = env
    body = _freeze(ps)
    src = FakeSources({"aspirin": _works("yes/yes", n=30)})
    with pytest.raises(RateCapped):
        SearchScreenWorker().run(_ctx(env, CappedAt("screener_b", 2), src))
    pid = store.add_protocol(ps, 2, body.model_copy(update={"record_cap": 2000}), version=2, actor="tester")
    store.freeze_protocol(ps, pid, actor="tester")  # another frozen protocol (a deviation in a real run)

    fresh = Screeners()
    assert SearchScreenWorker().run(_ctx(env, fresh, src, attempt=2)).status == "done"
    assert (_sizes(fresh, "screener_a"), _sizes(fresh, "screener_b")) == ([25, 5], [25, 5])
    assert not any(d["carried_from"] for d in store.decisions(ps, 2))


def test_scoping_switch_counts_include_differences():
    assert [include_differs(a, b) for a, b in (("yes", "unclear"), ("unclear", "unclear"), ("no", "no"),
                                              ("yes", "yes"), (None, "yes"))] == [False] * 5
    assert [include_differs(a, b) for a, b in (("yes", "no"), ("no", "unclear"), (None, "no"))] == [True] * 3


@pytest.mark.parametrize("env", [SCOPING_P2], indirect=True)
def test_scoping_sample_of_unclear_pairs_does_not_switch(env):
    _, ps, _ = env
    _freeze(ps)
    ex = Screeners()
    SearchScreenWorker().run(_ctx(env, ex, FakeSources({"aspirin": _works("unclear/unclear", n=20)})))
    (note,) = screening_notes(ps, 1, kind="scoping_sample")
    assert (len(note["sample"]), note["disagreements"], note["switched"]) == (2, 0, False)
    assert "unclear" in note["measure"]
    assert _sizes(ex, "screener_b") == [2]  # single screening for the rest
    (cp,) = list_checkpoints(ps, "open")  # the dual sample's unclear pairs are still disagreements to settle
    assert cp["options"]["record_ids"] == note["sample"]


@pytest.mark.parametrize("env", [SR_P1], indirect=True)
def test_carried_vote_needs_every_screener_decision_carried(env):
    _, ps, _ = env
    _freeze(ps)
    works = [_work("Aspirin X [yes/-]", 1), _work("Aspirin Y [yes/no]", 2)]
    src = FakeSources({"aspirin": works})
    first = CappedAt("judge", 2, votes={"Aspirin X [yes/-]": ("include", "include")})
    with pytest.raises(RateCapped):  # X (screener_b silent: a disagreement) is voted on, Y's vote is capped
        SearchScreenWorker().run(_ctx(env, first, src))
    assert _sizes(first, "screener_b") == [2, 1]

    # screener_b is asked about X again and now decides: X's earlier vote is not reused
    resumed = Screeners(overrides={"Aspirin X [yes/-]": "yes/no"},
                        votes={"Aspirin X [yes/-]": ("exclude", "exclude"), "Aspirin Y [yes/no]": ("include", "include")})
    assert SearchScreenWorker().run(_ctx(env, resumed, src, attempt=2)).status == "done"
    assert (_sizes(resumed, "screener_a"), _sizes(resumed, "screener_b")) == ([], [1])
    assert sorted(c["inputs"]["subject"]["record"]["title"] for c in resumed.of("judge")) == [
        "Aspirin X [yes/-]", "Aspirin Y [yes/no]"]
    t = _titles(env, 2)
    got = {t[f["record_id"]]: (f["include"], f["decided_by"], f["carried_from"]) for f in store.finals(ps, 2, phase=TA)}
    assert got == {"Aspirin X [yes/-]": ("no", "vote", None), "Aspirin Y [yes/no]": ("yes", "vote", None)}


@pytest.mark.parametrize("env", [SR_P2], indirect=True)
def test_unmarked_interrupted_attempt_is_passed_over(env):
    _, ps, _ = env
    _freeze(ps)
    src = FakeSources({"aspirin": _works("yes/yes", n=30)})
    with pytest.raises(RateCapped):
        SearchScreenWorker().run(_ctx(env, CappedAt("screener_b", 2), src))
    # attempt 2 was interrupted before it marked its protocol (it did nothing); attempt 3 carries attempt 1 over
    h = store.frozen_protocol(ps)["frozen_hash"]
    assert carry_attempts(ps, "search_screen", 3, h) == [1]
    resumed = Screeners()
    assert SearchScreenWorker().run(_ctx(env, resumed, src, attempt=3)).status == "done"
    assert (_sizes(resumed, "screener_a"), _sizes(resumed, "screener_b")) == ([], [5])
    assert {d["carried_from"] for d in store.decisions(ps, 3)} == {1, None}


@pytest.mark.parametrize("env", [SCOPING_P2], indirect=True)
def test_scoping_carry_over_keeps_the_sample_and_the_switch(env):
    _, ps, _ = env
    _freeze(ps)
    src = FakeSources({"aspirin": _works("yes/no", n=30)})
    capped = CappedAt("screener_b", 2)  # the sample (3) disagrees, so screener_b screens the rest: capped there
    with pytest.raises(RateCapped):
        SearchScreenWorker().run(_ctx(env, capped, src))
    assert (_sizes(capped, "screener_a"), _sizes(capped, "screener_b")) == ([3, 25, 2], [3])

    resumed = Screeners()
    assert SearchScreenWorker().run(_ctx(env, resumed, src, attempt=2)).status == "waiting_human"
    assert (_sizes(resumed, "screener_a"), _sizes(resumed, "screener_b")) == ([], [25, 2])
    (note,) = screening_notes(ps, 2, kind="scoping_sample")
    assert (len(note["sample"]), note["disagreements"], note["switched"]) == (3, 3, True)
    carried_b = {d["record_id"] for d in store.decisions(ps, 2) if d["screener"] == "screener_b" and d["carried_from"]}
    assert carried_b == set(note["sample"])


@pytest.mark.parametrize("env", [SR_P1], indirect=True)
def test_carried_final_is_reused_only_on_the_decisions_it_was_made_on(env):
    _, ps, _ = env
    _freeze(ps)
    src = FakeSources({"aspirin": [_work("Aspirin X [yes/-]", 1), _work("Aspirin Y [yes/no]", 2)]})
    with pytest.raises(RateCapped):  # attempt 1: screener_b silent on X, the vote includes X; Y's vote is capped
        SearchScreenWorker().run(_ctx(env, CappedAt("judge", 2, votes={"Aspirin X [yes/-]": ("include", "include")}),
                                      src))
    with pytest.raises(RateCapped):  # attempt 2: screener_b now says no on X; the judge is capped before X's vote
        SearchScreenWorker().run(_ctx(env, CappedAt("judge", 1, overrides={"Aspirin X [yes/-]": "yes/no"}), src,
                                      attempt=2))

    # attempt 3: screener_a's decision on X comes from attempt 1, screener_b's from attempt 2, the vote from attempt 1
    # (made while screener_b was silent): the vote is not reused, X is voted on again
    resumed = Screeners(overrides={"Aspirin X [yes/-]": "yes/no"},
                        votes={"Aspirin X [yes/-]": ("exclude", "exclude"), "Aspirin Y [yes/no]": ("include", "include")})
    assert SearchScreenWorker().run(_ctx(env, resumed, src, attempt=3)).status == "done"
    assert (_sizes(resumed, "screener_a"), _sizes(resumed, "screener_b")) == ([], [])
    t = _titles(env, 3)
    got = {t[f["record_id"]]: (f["include"], f["decided_by"], f["carried_from"]) for f in store.finals(ps, 3, phase=TA)}
    assert got["Aspirin X [yes/-]"] == ("no", "vote", None)
    assert {(d["screener"], d["carried_from"]) for d in store.decisions(ps, 3) if t[d["record_id"]].startswith(
        "Aspirin X")} == {("screener_a", 1), ("screener_b", 2)}
