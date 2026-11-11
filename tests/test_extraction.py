"""Plan 5a Task 7: reading and extraction. Every value links a verified quote note (V-QUOTE, against the full text or,
for an abstract-only study, the abstract), retracted or withdrawn studies are flagged and not extracted, the gate
names study, field and reason, P1 degrades by marking values missing, and an interrupted attempt is resumed without
reading the finished studies again."""

from __future__ import annotations

import pytest

from rk.discovery.executor import RateCapped
from rk.fulltext import latest_snapshot
from rk.library.models import Ids, ItemIn
from rk.llm.drivers import DriverSpec
from rk.llm.types import TaskResult
from rk.net.http import HttpError
from rk.notes import NoteIn, NoteRejected, add_note, list_notes
from rk.orchestrator.state import degradations, record_passed, record_ran
from rk.production import store
from rk.production.audit import quote_note_problem
from rk.production.extraction import (
    CHANGED,
    QUOTE_FAILED,
    READER_TOOLS,
    ReadExtractWorker,
    extraction_limitations,
    usable_evidence,
)
from rk.production.models import ExtractionField
from rk.store.events import NewEvent
from rk.verify.records import record_verification
from rk.verify.retract import RetractionInfo
from tests.discovery_fakes import FakeSources, ScriptedExecutor, add_text_layer
from tests.production_fakes import a_protocol, make_frozen_project, production_ctx

TA, FT = "title_abstract", "full_text"
METHODS = "A cohort of 100 people over 80 took aspirin daily."
PAGES = ["Aspirin in the oldest old\nAbstract\nWe followed people over 80 on aspirin.",
         f"2. Methods\n{METHODS}\n3. Results\nFewer strokes on aspirin."]
GOOD = {"values": [{"field": "n", "value": 100, "quote": METHODS},
                   {"field": "design", "value": "cohort", "quote": "A cohort of 100 people over 80"}]}


@pytest.fixture
def env(tmp_home, replay_http, request):
    k, ps, lib, _ = make_frozen_project(tmp_home, replay_http, **getattr(request, "param", {}))
    yield k, ps, lib
    k.close()


class Reader(ScriptedExecutor):
    """A reader that answers by the study's title: ``outs[title]`` is a list of outputs, one per call (``None``: the
    task fails); ``cap_at`` raises ``RateCapped`` at that call."""

    def __init__(self, outs: dict[str, list[dict | None]], *, cap_at: int | None = None):
        super().__init__({})
        self.outs = {t: list(v) for t, v in outs.items()}
        self.cap_at = cap_at

    def run(self, role, instructions, inputs, output_schema, **kw):
        if self.cap_at is not None and len(self.calls) + 1 == self.cap_at:
            raise RateCapped("claude_cli", 60.0)
        out = self.outs[inputs["study"]["title"]].pop(0)
        if out is None:
            self.calls.append({"role": role, "inputs": inputs, "tools": kw.get("tools")})
            spec = DriverSpec.parse(self.drivers[role])
            return TaskResult("tsk_x", False, None, "", spec.provider, spec.model, 0.0, "invalid_output")
        self.script[(role, output_schema.__name__)] = [out]
        return super().run(role, instructions, inputs, output_schema, **kw)

    def titles(self) -> list[str]:
        return [c["inputs"]["study"]["title"] for c in self.calls]


def _included(env, studies: dict[str, str], protocol=None) -> dict[str, str]:
    """Freeze the protocol (``a_protocol()`` unless given) and record ``studies`` (title -> ``"full"``,
    ``"abstract"``; ``"none"``: no full text and no abstract; ``"gone"``: recorded as retrieved but no full text
    stored) as included by a passed ``search_screen`` attempt 1; returns title -> item id."""
    _, ps, lib = env
    pid = store.add_protocol(ps, 1, protocol or a_protocol(), version=1, actor="tester")
    store.freeze_protocol(ps, pid, actor="tester")
    ids = {}
    for i, (title, kind) in enumerate(studies.items()):
        iid = lib.add_item(ItemIn(title=title, year=2015, venue="Age Ageing", ids=Ids(doi=f"10.1000/x{i}"),
                                  abstract=None if kind == "none" else f"Abstract of {title}: 80 people over 80 "
                                                                       "in a cohort."),
                           added_by="agent", actor="tester").item_id
        rid = store.add_record(ps, 1, iid, language="en", actor="tester")
        for phase in (TA, FT):
            store.set_final(ps, rid, 1, phase, "yes", "single", "fits", actor="tester")
        store.set_record(ps, rid, actor="tester", retrieved=kind in ("full", "gone"), abstract_only=kind == "abstract")
        if kind == "full":
            add_text_layer(ps, iid, PAGES)
        ids[title] = iid
    record_passed(ps, "search_screen", 1, actor="tester")
    return ids


def _ctx(env, ex, *, attempt: int = 1, sources=None):
    k, ps, lib = env
    return production_ctx(k, ps, lib, ex, stage="read_extract", attempt=attempt,
                          sources=sources or FakeSources({}, lib=lib))


def _note(ps, note_id: str) -> dict:
    return next(n for n in list_notes(ps) if n["id"] == note_id)


def test_every_value_links_a_verified_quote_note(env):
    _, ps, _ = env
    ids = _included(env, {"Aspirin A": "full", "Aspirin B": "full"})
    ex = Reader({"Aspirin A": [GOOD], "Aspirin B": [{"values": [
        {"field": "n", "value": 100, "quote": METHODS},
        {"field": "design", "value": "not_reported", "quote": "Fewer strokes on aspirin."}]}]})
    w = ReadExtractWorker()
    out = w.run(_ctx(env, ex))
    assert out.status == "done", out.reason
    assert all(c["tools"] == READER_TOOLS for c in ex.calls)
    assert ex.calls[0]["inputs"]["study"]["text_kind"] == "full_text"
    rows = store.extractions(ps, 1)
    assert [(r["field"], r["value"]) for r in rows] == [("n", 100), ("design", "cohort"), ("n", 100),
                                                         ("design", "not_reported")]
    for r in rows:
        n = _note(ps, r["note_id"])
        assert r["problem"] is None and r["sufficient"] == 1
        assert n["kind"] == "quote" and n["item_id"] == r["item_id"] and n["verified"]["quote"]["ok"]
        assert n["locator"] is not None and "extraction" in n["keywords"]
    assert _note(ps, rows[3]["note_id"])["text"] == "Fewer strokes on aspirin."  # not reported: the section searched
    assert len(list_notes(ps, ids["Aspirin A"])) == 2  # one note per distinct quote
    assert w.gate(ps, 1).ok


def test_unverbatim_quote_is_a_problem_not_a_value(env):
    _, ps, _ = env
    ids = _included(env, {"Aspirin A": "full"})
    ex = Reader({"Aspirin A": [{"values": [
        {"field": "n", "value": 120, "quote": "A cohort of 120 people over 80 took aspirin daily."},
        {"field": "design", "value": "trial", "quote": "A cohort of 100 people over 80"},
        {"field": "dose", "value": "81 mg", "quote": METHODS}]}]})
    w = ReadExtractWorker()
    assert w.run(_ctx(env, ex)).status == "done"
    rows = {r["field"]: r for r in store.extractions(ps, 1)}
    assert set(rows) == {"n", "design"}  # the value of a field outside the schema is not stored
    assert (rows["n"]["note_id"], rows["n"]["problem"]) == (None, QUOTE_FAILED)
    assert (rows["design"]["note_id"], rows["design"]["problem"]) == (None, "not one of the categories (rct, cohort, other)")
    assert list_notes(ps, ids["Aspirin A"]) == []  # no note for a failed quote
    (study,) = store.extraction_notes(ps, 1, kind="study")
    assert study["problems"] == ['a value for "dose", not a field of the extraction schema, was ignored']
    gate = w.gate(ps, 1)
    iid = ids["Aspirin A"]
    assert not gate.ok and gate.failures == [
        f'study {iid} ("Aspirin A"), field "n": {QUOTE_FAILED}',
        f'study {iid} ("Aspirin A"), field "design": not one of the categories (rct, cohort, other)']


def test_abstract_only_study_is_marked_low_sufficiency(env):  # Review Focus 4
    _, ps, _ = env
    ids = _included(env, {"Aspirin A": "abstract"})
    ex = Reader({"Aspirin A": [{"values": [
        {"field": "n", "value": 80, "quote": "80 people over 80"},
        {"field": "design", "value": "cohort", "quote": "in a cohort"}]}]})
    w = ReadExtractWorker()
    assert w.run(_ctx(env, ex)).status == "done"
    (call,) = ex.calls
    assert call["tools"] == [] and call["inputs"]["study"]["text_kind"] == "abstract_only"
    rows = store.extractions(ps, 1)
    assert [r["sufficient"] for r in rows] == [0, 0] and all(r["problem"] is None for r in rows)
    for r in rows:
        n = _note(ps, r["note_id"])
        assert n["verified"]["quote"] == {"ok": True, "page": 1, "located": "search", "against": "abstract"}
        assert n["snapshot_sha"] is None
    (study,) = store.extraction_notes(ps, 1, kind="study")
    assert (study["text"], study["sufficient"]) == ("abstract", "low")
    assert extraction_limitations(ps, 1)["low_sufficiency"] == [ids["Aspirin A"]]
    assert w.gate(ps, 1).ok

    # a quote that is not in the abstract is not verified against it (attempt 1 ran to completion: no carry-over)
    record_ran(ps, "read_extract", 1, "done", "", actor="tester")
    ex2 = Reader({"Aspirin A": [{"values": [{"field": "n", "value": 100, "quote": METHODS},
                                            {"field": "design", "value": "cohort", "quote": "in a cohort"}]}]})
    w.run(_ctx(env, ex2, attempt=2))
    assert [r["problem"] for r in store.extractions(ps, 2)] == [QUOTE_FAILED, None]


def test_a_record_field_is_quoted_from_the_study_record_and_a_text_field_never(env):
    """Calibration (critical review, 2026-11-10): the reader quoted the venue it was given as metadata for "where was it
    published", which no full text states verbatim, so 47 of 63 values failed V-QUOTE on every attempt. A record field
    is verified against the study's bibliographic record; a text field still only against its text."""
    _, ps, _ = env
    venue = ExtractionField(name="venue_kind", description="Where it was published", type="category",
                            categories=["journal", "preprint"], source="record")
    ids = _included(env, {"Aspirin A": "full", "Aspirin B": "abstract"},
                    a_protocol(extraction_schema=[*a_protocol().extraction_schema, venue]))
    ex = Reader({"Aspirin A": [{"values": [*GOOD["values"],
                                           {"field": "venue_kind", "value": "journal", "quote": "Venue: Age Ageing"}]}],
                 "Aspirin B": [{"values": [
                     {"field": "n", "value": 80, "quote": "80 people over 80"},
                     {"field": "design", "value": "cohort", "quote": "Publication type: journal article"},
                     {"field": "venue_kind", "value": "journal", "quote": "in a cohort"}]}]})
    w = ReadExtractWorker()
    w.run(_ctx(env, ex))
    record = ex.calls[0]["inputs"]["study"]["record"]
    assert "Venue: Age Ageing" in record and "Publication type: journal article" in record
    rows = {(r["item_id"], r["field"]): r for r in store.extractions(ps, 1)}
    a = rows[(ids["Aspirin A"], "venue_kind")]
    assert a["problem"] is None and _note(ps, a["note_id"])["verified"]["quote"]["against"] == "record"
    assert quote_note_problem(ps, _note(ps, a["note_id"])) is None  # the audit's G-QUOTE re-verifies it
    # a text field quoted from the record, a record field quoted from the text: neither verifies
    assert rows[(ids["Aspirin B"], "design")]["problem"] == QUOTE_FAILED
    assert rows[(ids["Aspirin B"], "venue_kind")]["problem"] == QUOTE_FAILED
    iid = ids["Aspirin B"]
    assert w.gate(ps, 1).failures == [f'study {iid} ("Aspirin B"), field "design": {QUOTE_FAILED}',
                                      f'study {iid} ("Aspirin B"), field "venue_kind": {QUOTE_FAILED}']


def test_retracted_study_is_flagged_not_extracted(env):
    _, ps, lib = env
    ids = _included(env, {"Aspirin A": "full", "Aspirin B": "full"})
    src = FakeSources({}, lib=lib, retractions={"Aspirin B": RetractionInfo(status="retracted", sources=["openalex"])})
    ex = Reader({"Aspirin A": [GOOD]})
    w = ReadExtractWorker()
    assert w.run(_ctx(env, ex, sources=src)).status == "done"
    assert ex.titles() == ["Aspirin A"]
    assert {r["item_id"] for r in store.extractions(ps, 1)} == {ids["Aspirin A"]}
    notes = {n["item_id"]: n for n in store.extraction_notes(ps, 1, kind="study")}
    assert notes[ids["Aspirin B"]]["flagged"] and not notes[ids["Aspirin A"]]["flagged"]
    assert notes[ids["Aspirin B"]]["retraction"]["status"] == "retracted"
    checked = ps.db.execute("SELECT target_id, verdict FROM verifications WHERE verifier='V-RETRACT'").fetchall()
    assert {(r["target_id"], r["verdict"]) for r in checked} == {(ids["Aspirin A"], "none"),
                                                                 (ids["Aspirin B"], "retracted")}
    assert lib.get(ids["Aspirin B"]).retraction == "retracted"
    assert extraction_limitations(ps, 1)["flagged"] == [{"item_id": ids["Aspirin B"], "status": "retracted"}]
    assert w.gate(ps, 1).ok

    # a later attempt uses the recorded V-RETRACT (here a newer check found Aspirin A withdrawn) without a lookup;
    # when every study is retracted or withdrawn the stage fails: a back-edge would screen the same studies again
    record_ran(ps, "read_extract", 1, "done", "", actor="tester")
    record_verification(ps, "item", ids["Aspirin A"], "V-RETRACT", "withdrawn",
                        RetractionInfo(status="withdrawn", sources=["crossref:x"]).model_dump(), actor="tester")
    src2 = FakeSources({}, lib=lib)
    out = w.run(_ctx(env, Reader({}), attempt=2, sources=src2))
    assert [c for c in src2.calls if c[0] == "retraction"] == []
    assert out.status == "failed" and out.to is None
    assert out.reason.startswith("dead end: every included study is retracted or withdrawn")
    assert f'study {ids["Aspirin A"]} ("Aspirin A"): withdrawn' in out.reason
    assert f'study {ids["Aspirin B"]} ("Aspirin B"): retracted' in out.reason
    assert "start a new project with a revised protocol (its eligibility or search strategy)" in out.reason
    assert "only a revision of the protocol" not in out.reason  # no control reopens the frozen protocol
    assert w.gate(ps, 2).failures == ["no included study is left after the retraction check"]
    assert w.degrade(_ctx(env, Reader({}), attempt=2), []).status == "failed"


class FailingRetraction(FakeSources):
    def retraction(self, item_id):
        raise HttpError(503, "https://api.example/works")


def test_gate_lists_missing_fields(env):
    _, ps, lib = env
    ids = _included(env, {"Aspirin A": "full", "Aspirin B": "full"})
    ex = Reader({"Aspirin A": [{"values": [{"field": "n", "value": 100, "quote": METHODS}]}],
                 "Aspirin B": [None, None]})
    w = ReadExtractWorker()
    assert w.run(_ctx(env, ex, sources=FailingRetraction({}, lib=lib))).status == "done"
    assert ex.titles() == ["Aspirin A", "Aspirin B", "Aspirin B"]  # a task without output is asked once more
    a, b = ids["Aspirin A"], ids["Aspirin B"]
    gate = w.gate(ps, 1)
    assert not gate.ok
    assert gate.failures == [
        (f'study {a} ("Aspirin A"): retraction not checked: retraction lookup of {a} failed '
         "(HttpError: HTTP 503 for https://api.example/works)"),
        f'study {a} ("Aspirin A"), field "design": no value',
        (f'study {b} ("Aspirin B"): retraction not checked: retraction lookup of {b} failed '
         "(HttpError: HTTP 503 for https://api.example/works)"),
        f'study {b} ("Aspirin B"), field "n": no value',
        f'study {b} ("Aspirin B"), field "design": no value']
    problems = {n["item_id"]: n["problems"] for n in store.extraction_notes(ps, 1, kind="study")}
    assert problems[b][-1] == "the reader gave no extraction (invalid_output), also when asked again"


def test_p1_degrade_marks_missing(env):
    _, ps, lib = env
    ids = _included(env, {"Aspirin A": "full", "Aspirin B": "abstract", "Aspirin C": "full"})
    src = FailingRetraction({}, lib=lib)
    for t in ("Aspirin A", "Aspirin B"):  # their V-RETRACT is recorded; Aspirin C's lookup fails
        record_verification(ps, "item", ids[t], "V-RETRACT", "none", {"status": "none"}, actor="tester")
    ex = Reader({"Aspirin A": [{"values": [{"field": "n", "value": 100, "quote": METHODS}]}],
                 "Aspirin B": [{"values": [{"field": "n", "value": 80, "quote": "80 people over 80"},
                                           {"field": "design", "value": "rct", "quote": "a randomised trial"}]}],
                 "Aspirin C": [GOOD]})
    w = ReadExtractWorker()
    ctx = _ctx(env, ex, sources=src)
    assert w.run(ctx).status == "done"
    failures = w.gate(ps, 1).failures
    assert len(failures) == 3
    out = w.degrade(ctx, failures)
    assert out.status == "done"
    missing = [(r["item_id"], r["field"], r["value"], r["note_id"], r["sufficient"], r["problem"])
               for r in store.extractions(ps, 1) if r["problem"] and r["problem"].startswith("missing")]
    assert missing == [(ids["Aspirin A"], "design", None, None, 1, "missing: no value"),
                       (ids["Aspirin B"], "design", None, None, 0, f"missing: {QUOTE_FAILED}")]
    lim = extraction_limitations(ps, 1)
    assert lim["missing"] == [{"item_id": ids["Aspirin A"], "field": "design", "reason": "no value"},
                              {"item_id": ids["Aspirin B"], "field": "design", "reason": QUOTE_FAILED}]
    assert [e["item_id"] for e in lim["excluded"]] == [ids["Aspirin C"]]  # retraction not checked: out of evidence
    (deg,) = degradations(ps, "read_extract")
    assert deg["removed"] == [f"{ids['Aspirin A']}:design", f"{ids['Aspirin B']}:design", ids["Aspirin C"]]
    assert w.gate(ps, 1).ok
    c_rows = store.extractions(ps, 1, item_id=ids["Aspirin C"])  # the excluded study's rows are marked
    assert c_rows and all(r["problem"].startswith("excluded: retraction not checked") for r in c_rows)
    usable = usable_evidence(ps, 1)
    assert [s["item_id"] for s in usable] == [ids["Aspirin A"], ids["Aspirin B"]]
    assert all(v["item_id"] != ids["Aspirin C"] for s in usable for v in s["values"])
    assert [s["sufficient"] for s in usable] == ["full", "low"]


def test_degrade_with_no_study_left_fails(env):
    _, ps, lib = env
    _included(env, {"Aspirin A": "full"})
    w = ReadExtractWorker()
    ctx = _ctx(env, Reader({"Aspirin A": [GOOD]}), sources=FailingRetraction({}, lib=lib))
    assert w.run(ctx).status == "done"
    out = w.degrade(ctx, w.gate(ps, 1).failures)
    assert out.status == "failed" and "no study would be left in the evidence" in out.reason
    assert "start a new project with a revised protocol" in out.reason
    assert "retraction not checked" in out.reason
    assert degradations(ps, "read_extract") == [] and store.extraction_notes(ps, 1, kind="degraded") == []


def test_rate_capped_extraction_resumes_without_rereading_finished_studies(env):
    _, ps, _ = env
    ids = _included(env, {"Aspirin A": "full", "Aspirin B": "full", "Aspirin C": "abstract"})
    c_out = {"values": [{"field": "n", "value": 80, "quote": "80 people over 80"},
                        {"field": "design", "value": "cohort", "quote": "in a cohort"}]}
    w = ReadExtractWorker()
    capped = Reader({"Aspirin A": [GOOD]}, cap_at=2)
    with pytest.raises(RateCapped):
        w.run(_ctx(env, capped))  # attempt 1 is interrupted while reading Aspirin B
    a_notes = [n["id"] for n in list_notes(ps, ids["Aspirin A"])]
    assert len(a_notes) == 2

    ex = Reader({"Aspirin B": [GOOD], "Aspirin C": [c_out]})
    out = w.run(_ctx(env, ex, attempt=2))
    assert out.status == "done" and "1 studies carried over" in out.reason
    assert ex.titles() == ["Aspirin B", "Aspirin C"]  # Aspirin A is not read again
    carried = store.extractions(ps, 2, item_id=ids["Aspirin A"])
    assert [(r["field"], r["carried_from"]) for r in carried] == [("n", 1), ("design", 1)]
    assert sorted(r["note_id"] for r in carried) == sorted(a_notes)
    assert [n["id"] for n in list_notes(ps, ids["Aspirin A"])] == a_notes  # no duplicate notes
    assert {n["item_id"]: n["carried_from"] for n in store.extraction_notes(ps, 2, kind="study")} == {
        ids["Aspirin A"]: 1, ids["Aspirin B"]: None, ids["Aspirin C"]: None}
    assert w.gate(ps, 2).ok

    record_ran(ps, "read_extract", 2, "done", "", actor="tester")  # a completed attempt is never carried over
    again = Reader({"Aspirin A": [GOOD], "Aspirin B": [GOOD], "Aspirin C": [c_out]})
    w.run(_ctx(env, again, attempt=3))
    assert again.titles() == ["Aspirin A", "Aspirin B", "Aspirin C"]
    assert [n["id"] for n in list_notes(ps, ids["Aspirin A"])] == a_notes  # the same quotes link the same notes


def test_linked_paraphrase_notes_pass_v_para(env):
    _, ps, lib = env
    ids = _included(env, {"Aspirin A": "full", "Aspirin B": "full"})
    a = ids["Aspirin A"]
    para = add_note(ps, lib, NoteIn(source_id=a, kind="paraphrase", text="Strokes were rarer with the drug.",
                                    source_span="Fewer strokes on aspirin.", role_in_source="main"),
                    actor="reader").note_id
    other = add_note(ps, lib, NoteIn(source_id=ids["Aspirin B"], kind="quote", text=METHODS, role_in_source="main"),
                     actor="reader").note_id
    copied = "nte_copied"  # a paraphrase stored as passed that copies its source span (e.g. an older check)
    ps.apply(NewEvent(type="note.added", entity_id=a, actor_kind="agent", actor="reader", payload={"note": {
        "id": copied, "item_id": a, "kind": "paraphrase", "text": "Fewer strokes on aspirin.", "locator": None,
        "role_in_source": "main", "keywords": [], "source_span": "Fewer strokes on aspirin.", "hedge_level": None,
        "snapshot_sha": latest_snapshot(ps, a)[0], "verified": {"quote": {"ok": True, "page": 2, "located": "search"},
                                                                "paraphrase": {"ok": True}}}}))
    ex = Reader({"Aspirin A": [{**GOOD, "notes": [para, other, copied]}], "Aspirin B": [GOOD]})
    w = ReadExtractWorker()
    ctx = _ctx(env, ex)
    assert w.run(ctx).status == "done"
    study = next(n for n in store.extraction_notes(ps, 1, kind="study") if n["item_id"] == a)
    assert study["notes"] == [para, copied]
    assert study["problems"] == [f"note {other} is not a note of this study"]
    gate = w.gate(ps, 1)
    assert gate.failures == [f'study {a} ("Aspirin A"): paraphrase note {copied}: fails V-PARA']
    assert w.degrade(ctx, gate.failures).status == "done"  # P1: the note is dropped from the evidence
    assert degradations(ps, "read_extract")[0]["removed"] == [copied]
    assert w.gate(ps, 1).ok
    assert copied not in usable_evidence(ps, 1)[0]["notes"] and para in usable_evidence(ps, 1)[0]["notes"]


def test_quote_sources_follow_the_text_read(env):
    _, ps, lib = env
    ids = _included(env, {"Aspirin A": "full", "Aspirin B": "none", "Aspirin C": "gone"})
    a = ids["Aspirin A"]
    ex = Reader({"Aspirin A": [{"values": [
        {"field": "n", "value": 80, "quote": "80 people over 80 in a cohort"},  # only in the item's abstract
        {"field": "design", "value": "cohort", "quote": "  "}]}]})
    w = ReadExtractWorker()
    assert w.run(_ctx(env, ex)).status == "done"
    assert ex.titles() == ["Aspirin A"]  # B has no text at all, C's full text is gone: neither is read
    assert [r["problem"] for r in store.extractions(ps, 1)] == [QUOTE_FAILED, "no quote"]
    problems = {n["item_id"]: n["problems"] for n in store.extraction_notes(ps, 1, kind="study")}
    assert problems[ids["Aspirin B"]] == ["no full text and no abstract to read"]
    assert problems[ids["Aspirin C"]] == ["recorded as retrieved, but no full text is stored: not read"]
    with pytest.raises(NoteRejected) as e:  # an item with a full text is never verified against its abstract
        add_note(ps, lib, NoteIn(source_id=a, kind="quote", text="80 people over 80", role_in_source="main",
                                 against="abstract"), actor="reader")
    assert e.value.reason == "has_full_text"
    with pytest.raises(NoteRejected) as e:
        add_note(ps, lib, NoteIn(source_id=ids["Aspirin B"], kind="quote", text="anything", role_in_source="main",
                                 against="abstract"), actor="reader")
    assert e.value.reason == "no_abstract"


def test_gate_refuses_a_note_of_another_text(env):
    _, ps, _ = env
    ids = _included(env, {"Aspirin A": "full"})
    w = ReadExtractWorker()
    assert w.run(_ctx(env, Reader({"Aspirin A": [GOOD]}))).status == "done"
    assert w.gate(ps, 1).ok
    add_text_layer(ps, ids["Aspirin A"], [*PAGES, "4. Discussion\nIt may help."])  # a newer full text
    a = ids["Aspirin A"]
    assert w.gate(ps, 1).failures == [
        f'study {a} ("Aspirin A"), field "n": the study\'s full text changed since it was read',
        f'study {a} ("Aspirin A"), field "design": the study\'s full text changed since it was read']


def test_a_wrong_locator_links_the_same_note(env):
    _, ps, _ = env
    ids = _included(env, {"Aspirin A": "full"})
    ex = Reader({"Aspirin A": [{"values": [
        {"field": "n", "value": 100, "quote": METHODS},
        {"field": "design", "value": "cohort", "quote": METHODS, "locator": {"page": 1, "start": 0, "end": 5}}]}]})
    assert ReadExtractWorker().run(_ctx(env, ex)).status == "done"
    rows = store.extractions(ps, 1)
    assert [r["problem"] for r in rows] == [None, None]
    assert len(list_notes(ps, ids["Aspirin A"])) == 1 and rows[0]["note_id"] == rows[1]["note_id"]


def test_conflicting_values_are_listed(env):
    _, ps, _ = env
    ids = _included(env, {"Aspirin A": "full"})
    ex = Reader({"Aspirin A": [{"values": [
        {"field": "n", "value": 100, "quote": METHODS},
        {"field": "n", "value": "not_reported", "quote": "Fewer strokes on aspirin."},
        {"field": "design", "value": "cohort", "quote": "A cohort of 100 people over 80"},
        {"field": "design", "value": "cohort", "quote": METHODS}]}]})
    assert ReadExtractWorker().run(_ctx(env, ex)).status == "done"
    assert extraction_limitations(ps, 1)["conflicts"] == [
        {"item_id": ids["Aspirin A"], "field": "n", "values": [100, "not_reported"]}]


def test_carry_over_needs_the_same_text(env):
    _, ps, _ = env
    ids = _included(env, {"Aspirin A": "abstract", "Aspirin B": "full"})
    c_out = {"values": [{"field": "n", "value": 80, "quote": "80 people over 80"},
                        {"field": "design", "value": "cohort", "quote": "in a cohort"}]}
    w = ReadExtractWorker()
    w.run(_ctx(env, Reader({"Aspirin A": [c_out], "Aspirin B": [GOOD]})))  # attempt 1 reads both, then crashes
    for t in ("Aspirin A", "Aspirin B"):  # A's full text is found; B's full text is replaced by a newer one
        add_text_layer(ps, ids[t], [*PAGES, f"Appendix of {t}."])
    ex = Reader({"Aspirin A": [GOOD], "Aspirin B": [GOOD]})
    w.run(_ctx(env, ex, attempt=2))
    assert ex.titles() == ["Aspirin A", "Aspirin B"]  # neither is carried over
    assert all(r["carried_from"] is None and r["sufficient"] == 1 for r in store.extractions(ps, 2))
    assert w.gate(ps, 2).ok


def test_degrade_marks_the_stale_rows_of_a_missing_field(env):
    _, ps, lib = env
    ids = _included(env, {"Aspirin A": "full", "Aspirin B": "full"})
    a, b = ids["Aspirin A"], ids["Aspirin B"]
    record_verification(ps, "item", a, "V-RETRACT", "none", {"status": "none"}, actor="tester")  # B's lookup fails
    ex = Reader({"Aspirin A": [GOOD], "Aspirin B": [{"values": [
        {"field": "n", "value": 5, "quote": "Five people took part."}, GOOD["values"][1]]}]})
    w = ReadExtractWorker()
    ctx = _ctx(env, ex, sources=FailingRetraction({}, lib=lib))
    assert w.run(ctx).status == "done"
    add_text_layer(ps, a, [*PAGES, "4. Discussion\nIt may help."])  # A's full text is replaced after the reading
    assert w.degrade(ctx, w.gate(ps, 1).failures).status == "done"
    assert [r["problem"] for r in store.extractions(ps, 1, item_id=a)] == [
        f"degraded: {CHANGED}", f"degraded: {CHANGED}", f"missing: {CHANGED}", f"missing: {CHANGED}"]
    (usable,) = usable_evidence(ps, 1)
    assert usable["item_id"] == a and usable["notes"] == []
    assert all(r["problem"] is not None for r in usable["values"])  # no stale value passes as evidence
    assert extraction_limitations(ps, 1)["conflicts"] == []
    excluded = (f"excluded: retraction not checked: retraction lookup of {b} failed "
                "(HttpError: HTTP 503 for https://api.example/works)")
    assert [r["problem"] for r in store.extractions(ps, 1, item_id=b)] == [f"{QUOTE_FAILED}; {excluded}", excluded]
    assert w.gate(ps, 1).ok
