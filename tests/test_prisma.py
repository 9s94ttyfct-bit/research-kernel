"""Plan 5a Task 6: full-text retrieval (retrieved, abstract only, not retrieved), full-text screening (the
title/abstract rules on the abstract, methods and results of the full text), V-PRISMA and the stage gate."""

from __future__ import annotations

import dataclasses

import pytest

from rk.checkpoints import answer_checkpoint, get_checkpoint, list_checkpoints
from rk.discovery.executor import RateCapped
from rk.discovery.store import SearchNote, log_search
from rk.library.models import Ids, ItemIn
from rk.net.http import HttpError
from rk.orchestrator.state import record_ran
from rk.production import screening, store
from rk.production.screening import FULLTEXT_CHARS, SearchScreenWorker, fulltext_excerpt
from rk.production.search import deduplicated
from rk.production.store import screening_notes
from rk.verify.prisma import check_prisma, check_selection_reasons, flow, prisma_counts
from tests.discovery_fakes import FakeSources
from tests.production_fakes import (
    CappedAt,
    Screeners,
    a_protocol,
    make_frozen_project,
    production_ctx,
)

TA, FT = "title_abstract", "full_text"
SCOPING_P1 = {"paper_type": "scoping_review", "production_mode": "auto"}
SR_P1 = {"paper_type": "systematic_review", "production_mode": "auto"}
SR_P2 = {"paper_type": "systematic_review", "production_mode": "collab"}
VOTED_OUT = "screeners disagreed: excluded by the P1 vote (not reviewed by a human)"
PAPER = ("Aspirin in the oldest old\n\nAbstract\nWe followed people over 80 on aspirin.\n\n1. Introduction\n"
         "INTRO-TEXT Strokes are common.\n\n2. Methods\nA cohort of 100 people over 80.\n\n3. RESULTS\n"
         "Fewer strokes on aspirin.\n\n4. Discussion\nDISCUSSION-TEXT It may help.\n\nReferences\n1. Someone.")


@pytest.fixture
def env(tmp_home, replay_http, request):
    k, ps, lib, _ = make_frozen_project(tmp_home, replay_http, **getattr(request, "param", {}))
    yield k, ps, lib
    k.close()


class Sources(FakeSources):
    """``FakeSources`` whose full-text lookup fails with an HTTP 503 for the titles in ``failing``."""

    def __init__(self, items, *, failing=(), **kw):
        super().__init__(items, **kw)
        self.failing = set(failing)

    def fulltext(self, item_id: str) -> bool:
        if self._title(item_id) in self.failing:
            raise HttpError(503, "https://fulltext.example/x")
        return super().fulltext(item_id)


def _work(title: str, i: int, *, abstract: bool = True, language: str | None = None) -> ItemIn:
    return ItemIn(title=title, year=2010 + i % 10, venue="Age Ageing", abstract=f"Abstract of {title}." if abstract
                  else None, ids=Ids(doi=f"10.1000/f{i}"), language=language)


def _freeze(ps, **overrides) -> None:
    pid = store.add_protocol(ps, 1, a_protocol(**overrides), version=1, actor="tester")
    store.freeze_protocol(ps, pid, actor="tester")


def _sources(env, works, **kw) -> Sources:
    return Sources({"aspirin": works}, lib=env[2], **kw)


def _ctx(env, ex, src, *, attempt: int = 1):
    k, ps, lib = env
    return production_ctx(k, ps, lib, ex, attempt=attempt, sources=src)


def _ids(env, attempt: int = 1) -> dict[str, str]:
    """title -> record id of the screened records."""
    _, ps, lib = env
    return {lib.get(r["item_id"]).title: r["id"] for r in deduplicated(ps, attempt)}


def _finals(env, phase: str, attempt: int = 1) -> dict[str, tuple[str, str, str]]:
    _, ps, _ = env
    t = {v: k for k, v in _ids(env, attempt).items()}
    return {t[f["record_id"]]: (f["include"], f["decided_by"], f["reason"])
            for f in store.finals(ps, attempt, phase=phase)}


def _ft_records(ex: Screeners, role: str) -> dict[str, dict]:
    return {r["title"]: r for c in ex.of(role, FT) for r in c["inputs"]["records"]}


def _final_events(ps) -> int:
    return ps.db.execute("SELECT COUNT(*) FROM events WHERE type='screening.final'").fetchone()[0]


def _answer(ps, cid: str, answer: dict) -> dict:
    answer_checkpoint(ps, cid, answer, answered_by="human", actor="researcher")
    return get_checkpoint(ps, cid)


def test_fulltext_excerpt_takes_abstract_methods_and_results():
    text, sections = fulltext_excerpt(PAPER)
    assert sections == ["abstract", "methods", "results"]
    assert text == ("Abstract\nWe followed people over 80 on aspirin.\n\nMethods\nA cohort of 100 people over 80."
                    "\n\nResults\nFewer strokes on aspirin.")
    # a long section gets what the short ones leave; the whole excerpt stays within the limit
    long = PAPER.replace("A cohort of 100 people over 80.", "M" * 9000)
    text, _ = fulltext_excerpt(long)
    assert len(text) <= FULLTEXT_CHARS and "Fewer strokes on aspirin." in text and "M" * 5000 in text
    # no sections: the whole text, cut
    text, sections = fulltext_excerpt("Just prose. " * 1000)
    assert sections == [] and text.startswith("Just prose.") and len(text) == FULLTEXT_CHARS


@pytest.mark.parametrize("env", [SR_P1], indirect=True)
def test_prisma_arithmetic_holds_and_fails_when_broken(env):
    _, ps, _ = env
    _freeze(ps)
    works = [_work("Aspirin A [yes/yes]", 1), _work("Aspirin B [yes/yes]", 2), _work("Aspirin C [no/no]", 3),
             _work("Aspirin D [yes/no]", 4), _work("Aspirin E [yes/no]", 5),
             _work("Aspirin F [yes/yes]", 6, abstract=False),
             _work("Aspirine chez le sujet âgé [yes/yes]", 7, language="fr")]
    pages = {t: [PAPER] for t in ("Aspirin A [yes/yes]", "Aspirin B [yes/yes]", "Aspirin D [yes/no]")}
    ex = Screeners(full_text={"Aspirin B [yes/yes]": "no/no", "Aspirin D [yes/no]": "yes/no"},
                   votes={"Aspirin D [yes/no]": ("include", "include"), "Aspirin E [yes/no]": ("include", "exclude"),
                          ("Aspirin D [yes/no]", FT): ("include", "exclude")})
    w = SearchScreenWorker()
    out = w.run(_ctx(env, ex, _sources(env, works, fulltexts=pages)))
    assert out.status == "done"

    p = prisma_counts(ps, 1)
    # every work is found on both sources of the strategy: the second hit is a duplicate
    assert (p.identified, p.duplicates_removed, p.screened, p.excluded_ta) == (14, 7, 7, 3)
    assert (p.sought, p.not_retrieved, p.assessed_ft, p.included) == (4, 1, 3, 1)
    assert p.excluded_ft == {"E1: Animal studies": 1, VOTED_OUT: 1}
    assert p.excluded_disagreement == {TA: 1, FT: 1}
    assert (p.excluded_language, p.abstract_only, p.carried, p.unreasoned_ta) == (1, 0, 0, 0)
    assert (p.incomplete_searches, p.dropped_entries) == ([], {})
    assert check_prisma(p) == []
    assert w.gate(ps, 1).ok
    assert "PRISMA" in out.reason and "identified 14" in out.reason and "included 1" in out.reason

    broken = {
        "duplicates_removed": (p.duplicates_removed - 1, "identified 14 - duplicates removed 6"),
        "excluded_ta": (p.excluded_ta + 1, "screened 7 - excluded at title/abstract 4"),
        "not_retrieved": (0, "sought 4 - not retrieved 0"),
        "included": (2, "assessed at full text 3 - excluded at full text 2"),
        "unreasoned_ta": (2, "2 title/abstract exclusions have no reason"),
    }
    for name, (value, said) in broken.items():
        (failure,) = check_prisma(dataclasses.replace(p, **{name: value}))
        assert said in failure, (name, failure)
    (failure,) = check_prisma(dataclasses.replace(p, excluded_ft={"": 1, VOTED_OUT: 1}))
    assert "1 full-text exclusion has no reason" in failure

    # the stored decisions break the arithmetic: the included study's full-text final becomes unclear
    store.set_final(ps, _ids(env)["Aspirin A [yes/yes]"], 1, FT, "unclear", "human", "unsure", actor="tester")
    gate = w.gate(ps, 1)
    assert not gate.ok and any(f.startswith("V-PRISMA:") and "but 0 were included" in f for f in gate.failures)


@pytest.mark.parametrize("env", [SR_P1], indirect=True)
def test_not_retrieved_and_abstract_only_are_counted(env):
    _, ps, _ = env
    _freeze(ps)
    works = [_work("Aspirin full [yes/yes]", 1), _work("Aspirin abstract [yes/-]", 2),
             _work("Aspirin failing [yes/yes]", 3), _work("Aspirin nothing [yes/yes]", 4, abstract=False)]
    src = _sources(env, works, fulltexts={"Aspirin full [yes/yes]": [PAPER]}, failing={"Aspirin failing [yes/yes]"})
    ex = Screeners(votes={"Aspirin abstract [yes/-]": ("include", "include")})
    out = SearchScreenWorker().run(_ctx(env, ex, src))
    assert out.status == "done"

    rid = _ids(env)
    row = {t: store.record_row(ps, r) for t, r in rid.items()}
    flags = {t: (r["retrieved"], r["abstract_only"]) for t, r in row.items()}
    assert flags == {"Aspirin full [yes/yes]": (1, 0), "Aspirin abstract [yes/-]": (0, 1),
                     "Aspirin failing [yes/yes]": (0, 1), "Aspirin nothing [yes/yes]": (0, 0)}
    assert row["Aspirin full [yes/yes]"]["problem"] is None
    # the title/abstract problem stays; the full-text one is added to it
    assert row["Aspirin abstract [yes/-]"]["problem"] == (
        "screener_b gave no title/abstract decision, also when asked again; no full text: assessed on the abstract")
    failing = row["Aspirin failing [yes/yes]"]["problem"]
    assert "failed (HttpError: HTTP 503" in failing and failing.endswith("no full text: assessed on the abstract")
    assert row["Aspirin nothing [yes/yes]"]["problem"] == "no full text and no abstract: not retrieved"

    seen = _ft_records(ex, "screener_a")
    assert set(seen) == {"Aspirin full [yes/yes]", "Aspirin abstract [yes/-]", "Aspirin failing [yes/yes]"}
    assert seen["Aspirin abstract [yes/-]"]["text_kind"] == "abstract_only"
    assert seen["Aspirin abstract [yes/-]"]["text"] == "Abstract of Aspirin abstract [yes/-]."
    full = seen["Aspirin full [yes/yes]"]
    assert full["text_kind"] == "sections" and full["sections"] == ["abstract", "methods", "results"]
    assert "Fewer strokes" in full["text"] and "INTRO-TEXT" not in full["text"] and "DISCUSSION" not in full["text"]
    assert "abstract_only" in ex.of("screener_a", FT)[0]["instructions"]
    f = _finals(env, FT)
    assert "Aspirin nothing [yes/yes]" not in f and len(f) == 3

    (note,) = screening_notes(ps, 1, kind="full_text_retrieval")
    assert note["phase"] == FT and len(note["sought"]) == 4
    assert (note["retrieved"], note["not_retrieved"]) == ([rid["Aspirin full [yes/yes]"]],
                                                           [rid["Aspirin nothing [yes/yes]"]])
    p = prisma_counts(ps, 1)
    assert (p.sought, p.not_retrieved, p.assessed_ft, p.abstract_only, p.included) == (4, 1, 3, 2, 3)
    assert check_prisma(p) == []
    assert "1 not retrieved" in out.reason and "2 assessed on the abstract only" in out.reason


@pytest.mark.parametrize("env", [SR_P2], indirect=True)
def test_full_text_screening_rules_match_title_abstract(env):
    _, ps, _ = env
    _freeze(ps)
    pairs = ["yes/yes", "yes/unclear", "unclear/yes", "no/no", "yes/no", "unclear/unclear", "no/unclear", "yes/-"]
    works = [_work(f"Aspirin {p} [yes/yes]", i) for i, p in enumerate(pairs)] + [_work("Aspirin split [yes/no]", 20)]
    full_text = {f"Aspirin {p} [yes/yes]": p for p in pairs}
    ex = Screeners(full_text=full_text)
    long = PAPER.replace("A cohort of 100 people over 80.", "A cohort. " * 300)  # a long methods section
    src = _sources(env, works, fulltexts={t: [long] for t in full_text} | {"Aspirin split [yes/no]": [long]})
    w = SearchScreenWorker()
    out = w.run(_ctx(env, ex, src))
    (ta_cp,) = list_checkpoints(ps, "open")  # the title/abstract disagreement comes first
    assert out.status == "waiting_human" and ta_cp["options"]["phase"] == TA and ex.of("screener_a", FT) == []
    p = prisma_counts(ps, 1)  # nothing was sought for its full text yet: nothing counts as not retrieved
    assert (p.sought, p.not_retrieved, p.abstract_only) == (8, 0, 0) and check_prisma(p)

    ta_cp = _answer(ps, ta_cp["id"], {"decisions": {r: "include" for r in ta_cp["options"]["record_ids"]}})
    out = w.on_answer(_ctx(env, ex, src), ta_cp)
    assert out.status == "waiting_human"
    (cp,) = list_checkpoints(ps, "open")
    assert cp["id"] == out.checkpoint_id and cp["kind"] == "screening_disagreements"
    assert (cp["options"]["phase"], cp["options"]["attempt"]) == (FT, 1)
    assert "full-text screening" in cp["prompt"] and "Fewer strokes" in cp["prompt"]
    agreed = {"yes/yes": "yes", "yes/unclear": "yes", "unclear/yes": "yes", "no/no": "no"}
    assert {t: v[:2] for t, v in _finals(env, FT).items()} == {
        **{f"Aspirin {p} [yes/yes]": (inc, "agreement") for p, inc in agreed.items()},
        "Aspirin split [yes/no]": ("yes", "agreement")}  # the disagreements have no final yet
    rid = _ids(env)
    assert sorted(cp["options"]["record_ids"]) == sorted(rid[f"Aspirin {p} [yes/yes]"] for p in
                                                          ("yes/no", "unclear/unclear", "no/unclear", "yes/-"))
    for role in ("screener_a", "screener_b"):
        seen = _ft_records(ex, role)
        assert len(seen) == 9 and all(r["text_kind"] == "sections" and len(r["text"]) <= FULLTEXT_CHARS
                                      for r in seen.values())
    assert [len(c["inputs"]["records"]) for c in ex.of("screener_b", FT)] == [9, 1]  # yes/- asked once more

    # applying the title/abstract answer again changes nothing: it waits on the full-text checkpoint
    n = _final_events(ps)
    again = w.on_answer(_ctx(env, ex, src), ta_cp)
    assert (again.status, again.checkpoint_id) == ("waiting_human", cp["id"]) and _final_events(ps) == n
    assert [len(c["inputs"]["records"]) for c in ex.of("screener_b", FT)] == [9, 1]

    cp = _answer(ps, cp["id"], {"decisions": {r: "exclude" for r in cp["options"]["record_ids"]}})
    done = w.on_answer(_ctx(env, ex, src), cp)
    assert done.status == "done"
    f = _finals(env, FT)
    assert sum(1 for v in f.values() if v[1] == "human" and v[0] == "no") == 4
    assert w.gate(ps, 1).ok
    p = prisma_counts(ps, 1)
    assert p.excluded_ft == {"E1: Animal studies": 1, "screeners disagreed: excluded by you": 4}
    assert p.included == 4 and check_prisma(p) == []
    n = _final_events(ps)
    assert w.on_answer(_ctx(env, ex, src), cp).status == "done" and _final_events(ps) == n


def test_single_full_text_screening_rule(env):  # critical_review, P1: single screening, as at title/abstract
    _, ps, _ = env
    _freeze(ps)
    works = [_work(f"Aspirin {t} [yes/-]", i) for i, t in enumerate(("yes", "unclear", "no", "silent"))]
    ex = Screeners(full_text={"Aspirin yes [yes/-]": "yes/-", "Aspirin unclear [yes/-]": "unclear/-",
                              "Aspirin no [yes/-]": "no/-", "Aspirin silent [yes/-]": "-/-"})
    out = SearchScreenWorker().run(_ctx(env, ex, _sources(env, works)))
    assert out.status == "done" and ex.of("screener_b", FT) == [] and ex.of("judge", None) == []
    f = _finals(env, FT)
    assert {t: v[:2] for t, v in f.items()} == {
        "Aspirin yes [yes/-]": ("yes", "single"), "Aspirin unclear [yes/-]": ("yes", "single"),
        "Aspirin no [yes/-]": ("no", "single"), "Aspirin silent [yes/-]": ("yes", "single")}
    assert "unclear" in f["Aspirin unclear [yes/-]"][2] and "no decision" in f["Aspirin silent [yes/-]"][2]
    (missing,) = screening_notes(ps, 1, kind="missing_decision", phase=FT)
    rid = _ids(env)
    assert missing["record_id"] == rid["Aspirin silent [yes/-]"]
    assert "screener_a gave no full-text decision" in store.record_row(ps, rid["Aspirin silent [yes/-]"])["problem"]
    # a selection without the screener's reason is no complete selection: the gate fails on it
    gate = SearchScreenWorker().gate(ps, 1)
    assert not gate.ok and any("selection reason" in x and rid["Aspirin silent [yes/-]"] in x for x in gate.failures)


@pytest.mark.parametrize("env", [SR_P1], indirect=True)
def test_interrupted_full_text_screening_resumes_without_asking_again(env):
    _, ps, _ = env
    _freeze(ps)
    works = [_work(f"Aspirin {i} [yes/yes]", i) for i in range(30)]
    src = _sources(env, works, fulltexts={f"Aspirin {i} [yes/yes]": [PAPER] for i in range(1, 30)})
    capped = CappedAt("screener_b", 3)  # title/abstract: 25 + 5; the third call is the first full-text batch
    with pytest.raises(RateCapped):
        SearchScreenWorker().run(_ctx(env, capped, src))
    assert [len(c["inputs"]["records"]) for c in capped.of("screener_a", FT)] == [25, 5]

    src.fulltexts["Aspirin 0 [yes/yes]"] = [PAPER]  # its full text is found now: screened afresh on it
    resumed = Screeners()
    assert SearchScreenWorker().run(_ctx(env, resumed, src, attempt=2)).status == "done"
    assert resumed.of("screener_a", TA) == resumed.of("screener_b", TA) == []
    assert [[r["title"] for r in c["inputs"]["records"]] for c in resumed.of("screener_a", FT)] == [
        ["Aspirin 0 [yes/yes]"]]
    assert [len(c["inputs"]["records"]) for c in resumed.of("screener_b", FT)] == [25, 5]
    carried = [d for d in store.decisions(ps, 2, phase=FT) if d["carried_from"] == 1]
    assert len(carried) == 29 and {d["screener"] for d in carried} == {"screener_a"}
    p = prisma_counts(ps, 2)
    assert p.carried == 29 + 60 and check_prisma(p) == [] and p.included == 30


@pytest.mark.parametrize("env", [SR_P1], indirect=True)
@pytest.mark.parametrize("where", [TA, FT])
def test_zero_included_backs_to_protocol(env, where):
    _, ps, _ = env
    _freeze(ps)
    works = [_work(f"Aspirin {i} [{'no/no' if where == TA else 'yes/yes'}]", i) for i in range(3)]
    ex = Screeners(full_text={w.title: "no/no" for w in works})
    out = SearchScreenWorker().run(_ctx(env, ex, _sources(env, works)))
    assert (out.status, out.to) == ("back_edge", "protocol")
    assert "no study met the criteria" in out.reason
    assert (ex.of("screener_a", FT) == []) is (where == TA)
    gate = SearchScreenWorker().gate(ps, 1)
    assert not gate.ok and any("no study met the criteria" in x for x in gate.failures)


def test_critical_review_has_reasons_not_prisma(env, monkeypatch):  # critical_review, P1
    _, ps, _ = env
    _freeze(ps)
    works = [_work("Aspirin in [yes/-]", 1), _work("Aspirin out [no/-]", 2), _work("Aspirin late [yes/-]", 3)]
    ex = Screeners(full_text={"Aspirin late [yes/-]": "no/-"})
    w = SearchScreenWorker()
    out = w.run(_ctx(env, ex, _sources(env, works)))
    assert out.status == "done" and "PRISMA" not in out.reason
    assert "selection reasons" in out.reason
    monkeypatch.setattr(screening, "check_prisma", lambda p: ["V-PRISMA must not gate a critical review"])
    assert w.gate(ps, 1).ok and check_selection_reasons(ps, 1) == []

    # attempt 2: the screener gives no reason for a study it selects
    record_ran(ps, "search_screen", 1, out.status, out.reason, actor="tester")
    blank = Screeners(full_text={"Aspirin late [yes/-]": "no/-"}, reasons={("Aspirin in [yes/-]", FT): ""})
    assert w.run(_ctx(env, blank, _sources(env, works), attempt=2)).status == "done"
    (failure,) = check_selection_reasons(ps, 2)
    assert "1 selection decision has no selection reason" in failure
    assert _ids(env, 2)["Aspirin in [yes/-]"] in failure and "full-text" in failure
    gate = w.gate(ps, 2)
    assert not gate.ok and gate.failures == [failure]


@pytest.mark.parametrize("env", [SR_P1], indirect=True)
def test_prisma_notes_name_incomplete_searches_and_dropped_entries(env):
    _, ps, _ = env
    _freeze(ps)
    out = SearchScreenWorker().run(_ctx(env, Screeners(), _sources(env, [_work("Aspirin A [yes/yes]", 1)])))
    assert out.status == "done"
    why = "arxiv returned a short page (2 of 200 results) at start=0 of totalResults=9 for query 'aspirin'"
    log_search(ps, stage="search_screen", attempt=1, purpose="protocol_search", source="arxiv", query="aspirin",
               limit=200, item_ids=[], actor="tester", note=SearchNote(incomplete=why, dropped=2))
    p = prisma_counts(ps, 1)
    assert (p.incomplete_searches, p.dropped_entries) == ([why], {"arxiv": 2})
    assert "unreadable entries dropped: arxiv 2" in flow(p) and why in flow(p)


@pytest.mark.parametrize("env", [SCOPING_P1], indirect=True)
@pytest.mark.parametrize("b", ["yes", "no"])
def test_scoping_full_text_keeps_the_switch(env, b):
    _, ps, _ = env
    _freeze(ps)
    works = [_work(f"Aspirin {i} [yes/{b}]", i) for i in range(20)]
    ex = Screeners(votes={w.title: ("include", "include") for w in works})
    out = SearchScreenWorker().run(_ctx(env, ex, _sources(env, works)))
    assert out.status == "done"
    (ta,) = screening_notes(ps, 1, kind="scoping_sample", phase=TA)
    assert ta["switched"] is (b == "no")
    ft_b = [r for c in ex.of("screener_b", FT) for r in c["inputs"]["records"]]
    if b == "no":  # the stage switched to dual screening at title/abstract: dual at full text too
        assert len(ft_b) == 20 and screening_notes(ps, 1, kind="scoping_sample", phase=FT) == []
    else:  # a new 10% sample of the full-text records
        (ft,) = screening_notes(ps, 1, kind="scoping_sample", phase=FT)
        assert len(ft_b) == 2 and [r["record_id"] for r in ft_b] == ft["sample"] and not ft["switched"]
        assert ft["seed"].endswith(":full_text")
    assert prisma_counts(ps, 1).included == 20 and SearchScreenWorker().gate(ps, 1).ok


def _p2_after_ta(env, works, ex, src):
    """Run an SR P2 attempt whose one title/abstract disagreement waits on you, and answer it: include."""
    _, ps, _ = env
    assert SearchScreenWorker().run(_ctx(env, ex, src)).status == "waiting_human"
    (cp,) = list_checkpoints(ps, "open")
    return _answer(ps, cp["id"], {"decisions": {r: "include" for r in cp["options"]["record_ids"]}})


@pytest.mark.parametrize("env", [SR_P2], indirect=True)
def test_rate_capped_full_text_screening_resumes_in_the_same_attempt(env):
    _, ps, _ = env
    _freeze(ps)
    works = [_work("Aspirin split [yes/no]", 1)] + [_work(f"Aspirin {i} [yes/yes]", 10 + i) for i in range(3)]
    src = _sources(env, works)
    ta_cp = _p2_after_ta(env, works, Screeners(), src)
    w = SearchScreenWorker()
    capped = CappedAt("screener_b", 1)  # the full-text phase runs while your answer is applied: capped there
    with pytest.raises(RateCapped):
        w.on_answer(_ctx(env, capped, src), ta_cp)
    assert [len(c["inputs"]["records"]) for c in capped.of("screener_a", FT)] == [4]

    resumed = Screeners()  # the engine applies the answer again, in the same attempt
    out = w.on_answer(_ctx(env, resumed, src), ta_cp)
    assert out.status == "done", out.reason
    assert resumed.of("screener_a", FT) == [] and [len(c["inputs"]["records"]) for c in
                                                  resumed.of("screener_b", FT)] == [4]
    assert len(store.decisions(ps, 1, phase=FT)) == 8 and len(_finals(env, FT)) == 4
    assert len(screening_notes(ps, 1, kind="full_text_retrieval")) == 1
    assert w.gate(ps, 1).ok


@pytest.mark.parametrize("env", [SR_P2], indirect=True)
def test_crash_between_checkpoints_opens_the_missing_one_when_resumed(env, monkeypatch):
    _, ps, _ = env
    _freeze(ps)
    works = [_work("Aspirin split [yes/no]", 1)] + [_work(f"Aspirin d{i} [yes/yes]", 10 + i) for i in range(30)]
    ex = Screeners(full_text={w.title: "yes/no" for w in works[1:]})
    src = _sources(env, works)
    ta_cp = _p2_after_ta(env, works, ex, src)
    real, opened = screening._open, []

    def crash_on_second(*a, **kw):
        opened.append(a)
        if len(opened) == 2:
            raise RuntimeError("crash between two checkpoints")
        return real(*a, **kw)

    w = SearchScreenWorker()
    monkeypatch.setattr(screening, "_open", crash_on_second)
    with pytest.raises(RuntimeError):
        w.on_answer(_ctx(env, ex, src), ta_cp)
    monkeypatch.setattr(screening, "_open", real)
    calls = len(ex.calls)

    out = w.on_answer(_ctx(env, ex, src), ta_cp)
    assert out.status == "waiting_human" and len(ex.calls) == calls  # nobody is asked again
    first, second = [c for c in list_checkpoints(ps, "open") if c["options"]["phase"] == FT]
    assert out.checkpoint_id == first["id"] and "30 screening disagreements" in out.reason
    ids1, ids2 = first["options"]["record_ids"], second["options"]["record_ids"]
    assert (len(ids1), len(ids2)) == (25, 5) and set(ids1) | set(ids2) == {_ids(env)[w.title] for w in works[1:]}
    assert [(c["options"]["batch"], c["options"]["batches"]) for c in (first, second)] == [(1, 2), (2, 2)]
    again = w.on_answer(_ctx(env, ex, src), ta_cp)  # no third checkpoint
    assert again.checkpoint_id == first["id"] and len(list_checkpoints(ps, "open")) == 2


@pytest.mark.parametrize("env", [SR_P2], indirect=True)
def test_missing_decision_is_noted_once_when_the_phase_runs_again(env, monkeypatch):
    _, ps, _ = env
    _freeze(ps)
    works = [_work("Aspirin split [yes/no]", 1), _work("Aspirin quiet [yes/yes]", 2)]
    ex = Screeners(full_text={"Aspirin quiet [yes/yes]": "yes/-"})
    src = _sources(env, works)
    ta_cp = _p2_after_ta(env, works, ex, src)
    real = screening._open

    def crash(*a, **kw):
        raise RuntimeError("crash before the full-text checkpoint opens")

    w = SearchScreenWorker()
    monkeypatch.setattr(screening, "_open", crash)
    with pytest.raises(RuntimeError):
        w.on_answer(_ctx(env, ex, src), ta_cp)
    monkeypatch.setattr(screening, "_open", real)
    assert w.on_answer(_ctx(env, ex, src), ta_cp).status == "waiting_human"
    rid = _ids(env)["Aspirin quiet [yes/yes]"]
    notes = screening_notes(ps, 1, kind="missing_decision", phase=FT)
    assert [(n["screener"], n["record_id"]) for n in notes] == [("screener_b", rid)]


@pytest.mark.parametrize("env", [SR_P1], indirect=True)
def test_exclusion_without_a_screener_reason_is_unreasoned(env, monkeypatch):
    _, ps, _ = env
    _freeze(ps)
    # decisions stored without the reason rule of collection (``screening.unreasoned``), e.g. by an older version:
    # V-PRISMA still counts them
    monkeypatch.setattr(screening, "unreasoned", lambda *a: False)
    works = [_work("Aspirin in [yes/yes]", 1), _work("Aspirin out [no/no]", 2), _work("Aspirin late [yes/yes]", 3),
             _work("Aspirin vague [yes/yes]", 4)]
    ex = Screeners(full_text={"Aspirin late [yes/yes]": "no/no", "Aspirin vague [yes/yes]": "no/no"},
                   reasons={("Aspirin out [no/no]", TA): "", ("Aspirin late [yes/yes]", FT): ""},
                   criteria={("Aspirin late [yes/yes]", FT): None, ("Aspirin vague [yes/yes]", FT): None})
    w = SearchScreenWorker()
    assert w.run(_ctx(env, ex, _sources(env, works))).status == "done"
    p = prisma_counts(ps, 1)
    # "late": excluded at full text with neither a reason nor a criterion; "vague": a reason but no criterion
    assert p.unreasoned_ta == 1 and p.excluded_ft == {"": 1, "no criterion named": 1}
    assert check_prisma(p) == ["1 title/abstract exclusion has no reason", "1 full-text exclusion has no reason"]
    gate = w.gate(ps, 1)
    assert not gate.ok and gate.failures == [f"V-PRISMA: {f}" for f in check_prisma(p)]
