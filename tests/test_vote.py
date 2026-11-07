import json

import pytest

from rk.canon import canonical_json, sha256_hex
from rk.config import DEFAULT_DRIVERS, Settings, save_settings
from rk.ids import new_id
from rk.llm.types import TaskResult
from rk.production import store
from rk.production.vote import vote
from rk.roles.policy import JUDGE_ROLES
from rk.store.projections import rebuild
from tests.discovery_fakes import ScriptedExecutor
from tests.production_fakes import a_protocol, make_frozen_project, production_ctx

OPTIONS = ["include", "exclude"]


@pytest.fixture
def env(tmp_home, replay_http):
    k, ps, lib, _ = make_frozen_project(tmp_home, replay_http)
    yield k, ps, lib
    k.close()


def _votes(*choices: str | None) -> ScriptedExecutor:
    """The judges' choices in role order (``judge``, ``judge_2``, ``judge_3``); None leaves that role unscripted."""
    script = {}
    for role, choice in zip(JUDGE_ROLES, choices, strict=False):
        if choice is not None:
            script[(role, "VoteOut")] = [{"choice": choice, "reason": f"{role} says {choice}"}]
    return ScriptedExecutor(script)


def _vote(k, ps, lib, ex, **kw):
    record_id = store.add_record(ps, 1, "src_1", language="en", actor="tester")
    result = vote(production_ctx(k, ps, lib, ex), "record", record_id, "Does the record meet the criteria?",
                  OPTIONS, "include", {"title": "Aspirin over 80"}, **kw)
    return record_id, result


class FailingJudge2(ScriptedExecutor):
    """The other judges answer as scripted; the ``judge_2`` task fails (no output)."""

    def run(self, role, instructions, inputs, output_schema, **kw):
        if role != "judge_2":
            return super().run(role, instructions, inputs, output_schema, **kw)
        self.calls.append({"role": role, "schema": output_schema.__name__})
        return TaskResult(new_id("tsk"), False, None, "", "ollama", "deepseek-v4.1-flash:cloud", 0.0, "timeout")


def _decided(ps) -> list[dict]:
    return [json.loads(r["payload"]) for r in ps.db.execute("SELECT payload FROM events WHERE type='vote.decided'")]


def test_vote_agreement_passes(env):
    k, ps, lib = env
    ex = _votes("exclude", "exclude", "exclude")
    _, result = _vote(k, ps, lib, ex)
    assert (result.decision, result.conservative_used) == ("exclude", False)
    assert [(o["vendor"], o["choice"]) for o in result.opinions] == [
        ("openai", "exclude"), ("deepseek", "exclude"), ("anthropic", "exclude")]
    # Every task went through the stage's executor (counted against the stage attempt).
    assert [(c["role"], c["schema"], c["stage"], c["attempt"]) for c in ex.calls] == [
        (role, "VoteOut", "search_screen", 1) for role in JUDGE_ROLES]
    assert ex.calls[0]["inputs"]["options"] == OPTIONS and ex.calls[0]["inputs"]["subject"] == {
        "title": "Aspirin over 80"}


def test_vote_agreement_on_the_conservative_option_is_not_the_fallback(env):
    k, ps, lib = env
    _, result = _vote(k, ps, lib, _votes("include", "include", "include"))
    assert (result.decision, result.conservative_used) == ("include", False)


def test_vote_disagreement_is_conservative(env):
    k, ps, lib = env
    _, result = _vote(k, ps, lib, _votes("exclude", "include", "exclude"))
    assert (result.decision, result.conservative_used) == ("include", True)
    assert "disagree" in result.reason


def test_vote_uses_every_admitted_judge(env):
    k, ps, lib = env
    ex = _votes("exclude", "exclude", "include")
    _, result = _vote(k, ps, lib, ex)
    assert [c["role"] for c in ex.calls] == list(JUDGE_ROLES)
    assert [o["choice"] for o in result.opinions] == ["exclude", "exclude", "include"]
    assert (result.decision, result.conservative_used) == ("include", True) and "disagree" in result.reason


def test_vote_with_a_failed_judge_task_is_conservative(env):
    k, ps, lib = env
    ex = FailingJudge2({("judge", "VoteOut"): [{"choice": "exclude", "reason": "animal study"}],
                        ("judge_3", "VoteOut"): [{"choice": "exclude", "reason": "animal study"}]})
    _, result = _vote(k, ps, lib, ex)
    assert (result.decision, result.conservative_used) == ("include", True)
    assert [c["role"] for c in ex.calls] == list(JUDGE_ROLES)
    assert result.opinions[1] == {"role": "judge_2", "vendor": "deepseek", "choice": None, "reason": "timeout"}
    assert "no valid choice" in result.reason and _decided(ps)[0]["conservative_used"] is True


def test_vote_with_a_choice_outside_the_options_is_conservative(env):
    k, ps, lib = env
    _, result = _vote(k, ps, lib, _votes("exclude", "maybe", "exclude"))
    assert (result.decision, result.conservative_used) == ("include", True)
    assert result.opinions[1]["choice"] is None and "maybe" in result.opinions[1]["reason"]


def test_vote_with_one_vendor_is_conservative(env):
    k, ps, lib = env
    # Every judge configured on the same vendor: only judge is admitted, so no task runs and the vote is conservative.
    save_settings(Settings(email="rk-fixtures@users.noreply.github.com",
                           drivers={**DEFAULT_DRIVERS, "judge_2": DEFAULT_DRIVERS["judge"],
                                    "judge_3": DEFAULT_DRIVERS["judge"]}))
    ex = _votes("exclude", "exclude", "exclude")
    _, result = _vote(k, ps, lib, ex)
    assert (result.decision, result.conservative_used) == ("include", True)
    assert ex.calls == [] and result.opinions == []
    assert "fewer than two" in result.reason and "duplicate_vendor" in result.reason
    assert _decided(ps)[0]["skipped"] == {"judge_2": "duplicate_vendor", "judge_3": "duplicate_vendor"}


def test_vote_skips_a_judge_of_the_generator_vendor(env):
    k, ps, lib = env
    ex = _votes("include", "exclude", "exclude")
    _, result = _vote(k, ps, lib, ex, generator_vendors={"openai"})
    # judge (openai) is skipped; judge_2 and judge_3 decide
    assert (result.decision, result.conservative_used) == ("exclude", False)
    assert [c["role"] for c in ex.calls] == ["judge_2", "judge_3"]
    assert _decided(ps)[0]["skipped"] == {"judge": "same_or_unknown_vendor"}


def test_vote_with_one_admitted_judge_is_conservative(env):
    k, ps, lib = env
    save_settings(Settings(email="rk-fixtures@users.noreply.github.com",
                           drivers={**DEFAULT_DRIVERS, "judge_3": DEFAULT_DRIVERS["judge"]}))
    ex = _votes("exclude", "exclude", "exclude")
    _, result = _vote(k, ps, lib, ex, generator_vendors={"openai"})
    assert (result.decision, result.conservative_used) == ("include", True)
    assert ex.calls == [] and "fewer than two" in result.reason and "same_or_unknown_vendor" in result.reason


def test_vote_skips_a_judge_of_any_generator_vendor(env):
    k, ps, lib = env
    ex = _votes("exclude", "exclude", "exclude")
    _, result = _vote(k, ps, lib, ex, generator_vendors={"openai", "anthropic"})  # e.g. an Ideator and a Scout
    assert (result.decision, result.conservative_used) == ("include", True) and ex.calls == []
    assert _decided(ps)[0]["skipped"] == {"judge": "same_or_unknown_vendor", "judge_3": "same_or_unknown_vendor"}


def test_vote_rejects_a_conservative_option_outside_the_options(env):
    k, ps, lib = env
    with pytest.raises(ValueError, match="conservative"):
        vote(production_ctx(k, ps, lib, _votes(None, None)), "record", "rec_x", "Q?", OPTIONS, "unclear", {})


def test_vote_is_recorded_as_not_human_reviewed(env):
    k, ps, lib = env
    record_id, result = _vote(k, ps, lib, _votes("exclude", "include", "exclude"))
    [payload] = _decided(ps)
    assert payload["human_reviewed"] is False
    assert (payload["subject_type"], payload["subject_id"]) == ("record", record_id)
    assert payload["question"] == "Does the record meet the criteria?" and payload["options"] == OPTIONS
    assert (payload["decision"], payload["conservative"], payload["conservative_used"]) == ("include", "include", True)
    assert [(o["role"], o["vendor"], o["choice"], o["reason"]) for o in payload["opinions"]] == [
        ("judge", "openai", "exclude", "judge says exclude"), ("judge_2", "deepseek", "include", "judge_2 says include"),
        ("judge_3", "anthropic", "exclude", "judge_3 says exclude")]
    [row] = store.votes(ps, "record", record_id)
    assert row["id"] == result.id and row["decision"] == "include" and row["conservative"] == 1
    assert row["opinions"] == payload["opinions"]


def test_protocol_freezes_once_on_the_hash_of_its_body(env):
    _, ps, _ = env
    body = a_protocol()
    pid = store.add_protocol(ps, 1, body, version=1, actor="lead")
    h = store.freeze_protocol(ps, pid, actor="lead")
    assert h == sha256_hex(canonical_json(body.model_dump(mode="json")))
    row = store.protocol_row(ps, pid)
    assert (row["status"], row["frozen_hash"]) == ("frozen", h) and row["frozen_at"]
    assert row["body"]["record_cap"] == 3000
    assert store.frozen_protocol(ps)["id"] == pid
    with pytest.raises(ValueError, match="protocol already frozen"):
        store.freeze_protocol(ps, pid, actor="lead")


def test_frozen_protocol_is_the_highest_frozen_version(env):
    _, ps, _ = env
    v2 = store.add_protocol(ps, 2, a_protocol(), version=2, actor="lead")
    store.freeze_protocol(ps, v2, actor="lead")
    v1 = store.add_protocol(ps, 3, a_protocol(), version=1, actor="lead")
    store.freeze_protocol(ps, v1, actor="lead")  # frozen later, but a lower version
    assert store.frozen_protocol(ps)["id"] == v2


def test_deviation_goes_from_a_stored_version_to_a_later_one(env):
    _, ps, _ = env
    with pytest.raises(ValueError, match="no protocol version 1"):
        store.add_deviation(ps, 1, 2, "added a source", "search_screen", actor="lead")
    store.add_protocol(ps, 1, a_protocol(), version=1, actor="lead")
    for to_v in (1, 0):
        with pytest.raises(ValueError, match="later version"):
            store.add_deviation(ps, 1, to_v, "r", "search_screen", actor="lead")
    assert store.deviations(ps) == []
    store.add_deviation(ps, 1, 2, "added a source", "search_screen", actor="lead")
    assert [(d["from_version"], d["to_version"]) for d in store.deviations(ps)] == [(1, 2)]


def test_store_rejects_unknown_values(env):
    _, ps, _ = env
    rid = store.add_record(ps, 1, "src_1", language="en", actor="scout")
    with pytest.raises(ValueError, match="no record fields given"):
        store.set_record(ps, rid, actor="kernel")
    with pytest.raises(ValueError, match="unknown record field"):
        store.set_record(ps, rid, actor="kernel", status="x")
    with pytest.raises(ValueError, match="phase"):
        store.add_decision(ps, 1, rid, "abstract", "screener_a", "yes", "r", None, actor="screener_a")
    with pytest.raises(ValueError, match="include"):
        store.add_decision(ps, 1, rid, "title_abstract", "screener_a", "maybe", "r", None, actor="screener_a")
    with pytest.raises(ValueError, match="decided_by"):
        store.set_final(ps, rid, 1, "title_abstract", "yes", "lead", "r", actor="kernel")
    with pytest.raises(KeyError):
        store.set_record(ps, "rec_missing", actor="kernel", retrieved=True)


def _queries(ps, pid: str, rid: str) -> dict:
    return {"protocols": store.protocols(ps, 1), "frozen": store.frozen_protocol(ps),
            "deviations": store.deviations(ps), "records": store.records(ps, 1), "record": store.record_row(ps, rid),
            "decisions": store.decisions(ps, 1), "ta": store.decisions(ps, 1, phase="title_abstract"),
            "finals": store.finals(ps, 1), "extractions": store.extractions(ps, 1),
            "votes": store.votes(ps, "record", rid)}


def test_production_tables_rebuild(env):
    k, ps, lib = env
    pid = store.add_protocol(ps, 1, a_protocol(), version=1, actor="lead")
    store.freeze_protocol(ps, pid, actor="lead")
    store.add_deviation(ps, 1, 2, "added a source", "search_screen", actor="lead")
    rid, _ = _vote(k, ps, lib, _votes("exclude", "exclude", "exclude"))
    dup = store.add_record(ps, 1, "src_2", dedup_of=rid, language="de", actor="scout")
    store.set_record(ps, rid, actor="kernel", retrieved=True, abstract_only=False)
    store.set_record(ps, dup, actor="kernel", problem="no full text")
    store.add_decision(ps, 1, rid, "title_abstract", "screener_a", "yes", "meets I1", "I1", actor="screener_a")
    store.add_decision(ps, 1, rid, "title_abstract", "screener_b", "no", "animal", "E1", actor="screener_b")
    store.add_decision(ps, 1, rid, "full_text", "screener_a", "unclear", "dose missing", None, actor="screener_a")
    store.set_final(ps, rid, 1, "title_abstract", "unclear", "single", "first pass", actor="kernel")
    store.set_final(ps, rid, 1, "title_abstract", "no", "vote", "judges agree", actor="kernel")
    store.add_extraction(ps, 1, "src_1", "n", 120, "nte_1", True, actor="reader")
    store.add_extraction(ps, 1, "src_1", "design", None, None, False, actor="reader", problem="not reported")
    # Another attempt's rows stay out of attempt 1's queries.
    store.add_record(ps, 2, "src_3", language="en", actor="scout")

    before = _queries(ps, pid, rid)
    assert all(v for v in before.values())
    assert before["deviations"][0] == {**before["deviations"][0], "from_version": 1, "to_version": 2,
                                       "reason": "added a source", "stage": "search_screen"}
    assert [r["item_id"] for r in before["records"]] == ["src_1", "src_2"]
    assert before["record"] == {**before["record"], "retrieved": 1, "abstract_only": 0, "problem": None}
    assert before["records"][1]["dedup_of"] == rid and before["records"][1]["problem"] == "no full text"
    assert len(before["decisions"]) == 3 and len(before["ta"]) == 2
    assert [(f["include"], f["decided_by"]) for f in before["finals"]] == [("no", "vote")]
    assert [(e["field"], e["value"], e["sufficient"]) for e in before["extractions"]] == [
        ("n", 120, 1), ("design", None, 0)]
    assert before["extractions"][1]["problem"] == "not reported"
    rebuild(ps.db)
    assert _queries(ps, pid, rid) == before
