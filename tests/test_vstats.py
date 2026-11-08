"""V-STATS: kernel-made statistics and figures recompute from their extraction rows (Plan 5b Task 3)."""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from rk.argument import ArgumentService, ClaimIn
from rk.canon import canonical_json, sha256_hex
from rk.orchestrator.state import record_attempt, record_passed, record_ran
from rk.production.artifacts import ArtifactIn, add_artifact, artifact
from rk.production.store import add_extraction, extractions, mark_extraction
from rk.stats import STATS_VERSION
from rk.stats.effects import compute
from rk.stats.meta import pool
from rk.stats.plots import forest_svg, map_table
from rk.store.projections import rebuild
from rk.tools.builtin import build_registry
from rk.verify import stats as vstats
from rk.verify import support
from rk.verify.numbers import extract_numbers
from rk.verify.stats import (
    PoolOptions,
    check_stats,
    make_forest_artifact,
    make_map_artifact,
    make_map_table_artifact,
    make_stats_artifact,
    row_hash,
    stat_claim_text,
)
from rk.verify.support import verify_support

from .discovery_fakes import make_project

# BCG trials 1-3 (Viechtbauer 2010) and a study without its control arm's size.
STUDIES = {
    "itm_A": {"events_treatment": 4, "n_treatment": 123, "events_control": 11, "n_control": 139},
    "itm_B": {"events_treatment": 6, "n_treatment": 306, "events_control": 29, "n_control": 303},
    "itm_C": {"events_treatment": 3, "n_treatment": 231, "events_control": 11, "n_control": 220},
    "itm_D": {"events_treatment": 5, "n_treatment": 80, "events_control": 9},
}
GROUP = ("itm_A", "itm_B", "itm_C", "itm_D")
WHERE = {"itm_A": ("RCT", ["UK", "US"]), "itm_B": ("RCT", "US"), "itm_C": ("cohort", None)}


def _pass_read_extract(ps, attempt: int) -> None:
    record_attempt(ps, "read_extract", attempt, run_id=None, guidance="", requested=None, actor="t")
    record_ran(ps, "read_extract", attempt, "done", "", actor="t")
    record_passed(ps, "read_extract", attempt, actor="t")


@pytest.fixture
def env(tmp_home, replay_http):
    k, ps, _ = make_project(tmp_home, replay_http)
    for iid, data in STUDIES.items():
        for field, value in data.items():
            add_extraction(ps, 1, iid, field, value, None, True, actor="reader")
    _pass_read_extract(ps, 1)
    yield k, ps
    k.close()


def _rows(ps, items=GROUP, attempt: int = 1) -> list[dict]:
    return [r for r in extractions(ps, attempt) if r["item_id"] in items]


def _verdicts(ps, target_id: str) -> list[tuple[str, str]]:
    return [tuple(r) for r in ps.db.execute(
        "SELECT verifier, verdict FROM verifications WHERE target_id=? ORDER BY rowid", (target_id,))]


def _stt(ps, rows, items=GROUP, **kw) -> str:
    return make_stats_artifact(ps, rows, "RR", kw.pop("options", None), item_ids=list(items), stage="synthesize",
                               attempt=1, actor="kernel", **kw)


def _fig(ps, rows, items=GROUP, **kw) -> str:
    return make_forest_artifact(ps, rows, "RR", kw.pop("options", None), item_ids=list(items), stage="synthesize",
                                attempt=1, actor="kernel", **kw)


def test_vstats_passes_on_untouched_inputs_and_fails_on_changed_row(env):
    _, ps = env
    rows = _rows(ps)
    opts = PoolOptions(hksj="auto", pi_df="k-2")
    sid = _stt(ps, rows, options=opts, created_by_task="tsk_S")
    labels = {"itm_A": "Aronson 1948", "itm_B": "Ferguson & Simes 1949", "itm_C": "Rosenthal et al 1960"}
    fid = _fig(ps, rows, options=opts, labels=labels)

    # the artifact records everything its recomputation needs
    s = artifact(ps, sid)
    effects = [compute("RR", STUDIES[i]) for i in ("itm_A", "itm_B", "itm_C")]
    expected = pool(effects, model="REML", hksj="auto", pi_df="k-2")
    assert (s["kind"], s["code_path"], s["code_version"], s["created_by_task"]) == \
        ("stt", "rk.stats.meta:pool", STATS_VERSION, "tsk_S")
    assert s["input_hashes"] == [sha256_hex(canonical_json(r)) for r in rows] == [row_hash(r) for r in rows]
    v = s["values"]
    assert v["row_ids"] == [r["id"] for r in rows] and v["measure"] == "RR"
    assert v["item_ids"] == list(GROUP) and v["extraction_attempt"] == 1
    assert v["options"] == {"model": "REML", "hksj": "auto", "pi_df": "k-2", "level": 0.95}
    assert v["pooled"] == expected.as_dict()
    assert [x["item_id"] for x in v["studies"]] == ["itm_A", "itm_B", "itm_C"]
    assert v["uncomputable"] == [{"item_id": "itm_D", "reason": "missing: n_control"}]
    assert v["display"]["estimate"] == pytest.approx(math.exp(expected.estimate))
    assert v["display"]["ci_low"] == pytest.approx(math.exp(expected.ci_low))
    assert v["display"]["k"] == 3 and v["display"]["i2_percent"] == pytest.approx(100 * expected.i2)
    f = artifact(ps, fid)
    assert (f["kind"], f["code_path"], f["input_hashes"]) == ("fig", "rk.stats.plots:forest_svg", s["input_hashes"])
    svg = forest_svg(list(labels.values()), effects, expected, measure="RR")
    assert (ps.root / f["path"]).read_text(encoding="utf-8") == svg and f["sha256"] == sha256_hex(svg)
    assert f["values"]["labels"] == {**labels, "itm_D": "itm_D"}

    assert check_stats(ps, sid, actor="kernel").model_dump() == {"ok": True, "mismatches": []}
    assert check_stats(ps, fid, actor="kernel").ok
    assert _verdicts(ps, sid) == [("V-STATS", "pass")] and _verdicts(ps, fid) == [("V-STATS", "pass")]
    assert check_stats(ps, sid, record=False).ok and _verdicts(ps, sid) == [("V-STATS", "pass")]
    rebuild(ps.db)
    assert check_stats(ps, sid, actor="kernel").ok  # the hashes survive a rebuild of the projections

    # a row marked (here: missing) after the artifact was made is a changed input
    mark_extraction(ps, rows[0]["id"], "missing: the value was not in the text", actor="kernel")
    r = check_stats(ps, sid, actor="kernel")
    assert not r.ok and any(rows[0]["id"] in m and "changed" in m for m in r.mismatches)
    assert not check_stats(ps, fid, actor="kernel").ok
    assert _verdicts(ps, sid)[-1] == ("V-STATS", "mismatch")
    # and a row gone from the store is a missing input. Events cannot delete a row, so the test removes it from the
    # projection table directly (as a corrupted or hand-edited store would).
    ps.db.execute("DELETE FROM extractions WHERE id=?", (rows[1]["id"],))
    r = check_stats(ps, sid, actor="kernel")
    assert any(rows[1]["id"] in m and "missing" in m for m in r.mismatches)


def test_vstats_fails_on_stale_code_version(env, monkeypatch):
    _, ps = env
    sid = _stt(ps, _rows(ps), options={"model": "DL", "hksj": False, "pi_df": "k-2"})
    v = artifact(ps, sid)["values"]
    assert v["options"]["hksj"] is False and v["pooled"]["model"] == "DL" and v["pooled"]["pi_df"] == "k-2"
    assert check_stats(ps, sid, actor="kernel").ok  # recomputed with the recorded options
    monkeypatch.setattr(vstats, "STATS_VERSION", "0" * 64)
    r = check_stats(ps, sid, actor="kernel")
    assert not r.ok and any("code_version" in m for m in r.mismatches)
    assert _verdicts(ps, sid) == [("V-STATS", "pass"), ("V-STATS", "mismatch")]


def test_vstats_checks_the_extraction_attempt(env):
    _, ps = env
    old = _stt(ps, _rows(ps))
    for iid in ("itm_A", "itm_B", "itm_C"):
        for field, value in STUDIES[iid].items():
            add_extraction(ps, 2, iid, field, value, None, True, actor="reader")
    with pytest.raises(ValueError, match="one extraction attempt"):
        _stt(ps, [*_rows(ps, ("itm_A",)), *_rows(ps, ("itm_B", "itm_C"), attempt=2)])
    _pass_read_extract(ps, 2)
    r = check_stats(ps, old, actor="kernel")
    assert not r.ok and any("extraction attempt 1" in m and "attempt 2" in m for m in r.mismatches)
    new = _stt(ps, _rows(ps, attempt=2))
    assert artifact(ps, new)["values"]["extraction_attempt"] == 2 and check_stats(ps, new, actor="kernel").ok


def test_group_items_without_rows_are_uncomputable(env):
    _, ps = env
    group = ("itm_A", "itm_E", "itm_B", "itm_C")
    sid = _stt(ps, _rows(ps, ("itm_A", "itm_B", "itm_C")), items=group)
    v = artifact(ps, sid)["values"]
    assert [x["item_id"] for x in v["studies"]] == ["itm_A", "itm_B", "itm_C"] and v["pooled"]["k"] == 3
    assert v["uncomputable"] == [{"item_id": "itm_E", "reason": "missing: events_treatment, n_treatment, "
                                                               "events_control, n_control"}]
    assert check_stats(ps, sid, actor="kernel").ok
    fid = _fig(ps, _rows(ps, ("itm_A", "itm_B", "itm_C")), items=group)
    assert artifact(ps, fid)["values"]["labels"] == {i: i for i in group}
    with pytest.raises(ValueError, match="not in the group"):
        _stt(ps, _rows(ps), items=("itm_A", "itm_B", "itm_C"))
    with pytest.raises(ValueError, match="twice"):
        _stt(ps, _rows(ps), items=(*GROUP, "itm_A"))


def test_vstats_rejects_unknown_code_and_changed_values_or_files(env, monkeypatch):
    _, ps = env
    rows = _rows(ps)
    hand = add_artifact(ps, ArtifactIn(kind="stt", code_path="rk.stats.pool:pool", code_version=STATS_VERSION,
                                       input_hashes=[row_hash(r) for r in rows], values={"k": 3}, output_path=None),
                        stage="synthesize", attempt=1, actor="kernel")
    r = check_stats(ps, hand, actor="kernel")
    assert not r.ok and any("unknown code path" in m for m in r.mismatches)
    empty = add_artifact(ps, ArtifactIn(kind="stt", code_path="rk.stats.meta:pool", code_version=STATS_VERSION,
                                        input_hashes=[], values={"row_ids": []}, output_path=None),
                         stage="synthesize", attempt=1, actor="kernel")
    r = check_stats(ps, empty, actor="kernel")
    assert not r.ok and any("no input rows" in m for m in r.mismatches)
    with pytest.raises(KeyError):
        check_stats(ps, "stt_missing", actor="kernel")

    # a stored value the code no longer produces
    sid = _stt(ps, rows)
    real = vstats.pool
    monkeypatch.setattr(vstats, "pool", lambda es, **kw: real(es, **{**kw, "model": "FE"}))
    r = check_stats(ps, sid, actor="kernel")
    assert not r.ok and any(m.startswith("values.pooled.") for m in r.mismatches)
    monkeypatch.setattr(vstats, "pool", real)
    assert check_stats(ps, sid, actor="kernel").ok

    # a figure whose code now draws different bytes, and a stored file changed on disk
    fid = _fig(ps, rows)
    monkeypatch.setattr(vstats, "forest_svg", lambda *a, **kw: "<svg/>\n")
    r = check_stats(ps, fid, actor="kernel")
    assert not r.ok and any("regenerated" in m for m in r.mismatches)
    monkeypatch.undo()
    assert check_stats(ps, fid, actor="kernel").ok
    (ps.root / artifact(ps, fid)["path"]).write_text("<svg>edited</svg>", encoding="utf-8")
    r = check_stats(ps, fid, actor="kernel")
    assert not r.ok and any("file" in m for m in r.mismatches)
    (ps.root / artifact(ps, sid)["path"]).write_text("{}", encoding="utf-8")
    assert not check_stats(ps, sid, actor="kernel").ok


def test_stats_artifact_helpers_refuse_bad_input(env):
    _, ps = env
    with pytest.raises(ValidationError):
        PoolOptions(hksj="yes")
    with pytest.raises(ValidationError):
        PoolOptions(hksj=1)
    with pytest.raises(ValidationError):
        PoolOptions(pi_df="k-1")
    with pytest.raises(ValueError, match="at least two"):
        _stt(ps, _rows(ps, ("itm_A", "itm_D")))
    with pytest.raises(ValueError, match="no extraction rows"):
        _stt(ps, [])
    with pytest.raises(KeyError):
        _stt(ps, [{"id": "ext_missing"}])
    with pytest.raises(ValueError, match="measure"):
        make_stats_artifact(ps, _rows(ps), "HR", None, item_ids=list(GROUP), stage="synthesize", attempt=1,
                            actor="kernel")
    dup = add_extraction(ps, 1, "itm_A", "n_treatment", 124, None, True, actor="reader")
    with pytest.raises(ValueError, match="two values"):
        _stt(ps, _rows(ps))
    with pytest.raises(ValueError, match="twice"):
        _stt(ps, [*_rows(ps, ("itm_B", "itm_C")), _rows(ps, ("itm_B",))[0]])
    # a row with a problem is no value: the study is uncomputable, not pooled on it
    a_n = next(r for r in _rows(ps, ("itm_A",)) if r["field"] == "n_treatment")
    mark_extraction(ps, a_n["id"], "missing: not reported", actor="kernel")
    mark_extraction(ps, dup, "missing: not reported", actor="kernel")
    sid = _stt(ps, _rows(ps))
    v = artifact(ps, sid)["values"]
    assert v["uncomputable"][0] == {"item_id": "itm_A", "reason": "missing: n_treatment"}
    assert v["pooled"]["k"] == 2 and check_stats(ps, sid, actor="kernel").ok


def test_map_artifacts_recompute(env):
    _, ps = env
    for iid, (design, region) in WHERE.items():
        add_extraction(ps, 1, iid, "design", design, None, True, actor="reader")
        add_extraction(ps, 1, iid, "region", region, None, True, actor="reader")
    rows = [r for r in extractions(ps, 1) if r["field"] in ("design", "region")]
    sid = make_map_artifact(ps, rows, ["design", "region"], item_ids=list(GROUP), stage="synthesize", attempt=1,
                            actor="kernel")
    tid = make_map_table_artifact(ps, rows, ["design", "region"], item_ids=list(GROUP), stage="synthesize",
                                  attempt=1, actor="kernel")
    s, t = artifact(ps, sid), artifact(ps, tid)
    # itm_D has no design or region row: "not reported" in both
    per_study = [{"design": d, "region": r} for d, r in WHERE.values()] + [{}]
    assert s["code_path"] == "rk.stats.plots:map_table" and s["values"]["fields"] == ["design", "region"]
    assert s["values"]["item_ids"] == list(GROUP) and s["values"]["table"]["n"] == 4
    assert s["values"]["table"] == map_table(per_study, ["design", "region"])
    assert s["values"]["table"]["fields"]["region"]["not reported"] == 2
    assert t["kind"] == "fig" and t["path"].endswith(".md")
    md = (ps.root / t["path"]).read_text(encoding="utf-8")
    assert "| RCT | 2 |" in md and "| not reported | 2 |" in md
    with pytest.raises(ValueError, match="not in the group"):
        make_map_artifact(ps, rows, ["design"], item_ids=["itm_A"], stage="synthesize", attempt=1, actor="kernel")
    assert check_stats(ps, sid, actor="kernel").ok and check_stats(ps, tid, actor="kernel").ok
    mark_extraction(ps, rows[0]["id"], "missing: x", actor="kernel")
    assert not check_stats(ps, sid, actor="kernel").ok and not check_stats(ps, tid, actor="kernel").ok


def _stat(svc, sid: str, text: str, bindings) -> str:
    return svc.add_claim(ClaimIn(text=text, kind="stat_result", artifact_ids=[sid], number_bindings=list(bindings)),
                         generator_vendor=None, actor="kernel", actor_kind="kernel", origin="synthesis")


def _bind(sid: str, text: str, *keys: str) -> list[dict]:
    """Bind the numbers of ``text``, in order, to ``keys``."""
    return [{"artifact_id": sid, "key": key, "text": n.text}
            for key, n in zip(keys, extract_numbers(text), strict=True)]


def test_stat_result_claim_needs_an_artifact_and_never_calls_judges(env, monkeypatch):
    k, ps = env
    reg = build_registry(k)
    svc = ArgumentService(ps)
    with pytest.raises(ValueError, match="artifact"):
        svc.add_claim(ClaimIn(text="Pooled RR 0.62.", kind="stat_result"), generator_vendor=None, actor="kernel",
                      actor_kind="kernel", origin="synthesis")
    with pytest.raises(ValueError, match="number_bindings"):
        svc.add_claim(ClaimIn(text="Pooled RR 0.62.", kind="stat_result", artifact_ids=[_stt(ps, _rows(ps))]),
                      generator_vendor=None, actor="kernel", actor_kind="kernel", origin="synthesis")

    def no_judges(*a, **kw):
        raise AssertionError("a stat_result claim is never judged")

    monkeypatch.setattr(support, "run_judges", no_judges)
    rows = _rows(ps)
    sid = _stt(ps, rows)
    text, bindings = stat_claim_text(artifact(ps, sid))
    good = _stat(svc, sid, text, bindings)
    r = verify_support(k, reg, ps, good, actor="kernel")
    assert r.verdict == "supported" and r.opinions == [] and r.numbers_ok
    assert svc.get_claim(good)["status"] == "supported"
    assert _verdicts(ps, good) == [("V-NUM", "pass"), ("V-SUPPORT", "supported")]
    assert _verdicts(ps, sid) == [("V-STATS", "pass")]
    (details,) = [r["details"] for r in ps.db.execute(
        "SELECT details FROM verifications WHERE target_id=? AND verifier='V-SUPPORT'", (good,))]
    assert '"by":"V-STATS"' in details

    d = artifact(ps, sid)["values"]["display"]
    wrong_text = f"Pooled RR 0.99 ({d['k']} studies)."
    wrong = _stat(svc, sid, wrong_text, _bind(sid, wrong_text, "estimate", "k"))
    r = verify_support(k, reg, ps, wrong, actor="kernel")
    assert r.verdict == "unsupported" and [n.text for n in r.ungrounded_numbers] == ["0.99"]

    # once an input row changes, V-STATS fails and so does the claim
    mark_extraction(ps, rows[0]["id"], "missing: x", actor="kernel")
    r = verify_support(k, reg, ps, good, actor="kernel")
    assert r.verdict == "unsupported" and r.reason == "stats_mismatch"
    assert svc.get_claim(good)["status"] == "unsupported" and _verdicts(ps, sid)[-1] == ("V-STATS", "mismatch")


def test_stat_claim_template_binds_each_number(env):
    k, ps = env
    reg = build_registry(k)
    svc = ArgumentService(ps)
    # a fourth, discordant trial: heterogeneity, so a prediction interval wider than the CI (pooled RR about 0.44)
    for field, value in {"events_treatment": 30, "n_treatment": 100, "events_control": 25, "n_control": 100}.items():
        add_extraction(ps, 1, "itm_E", field, value, None, True, actor="reader")
    group = (*GROUP, "itm_E")
    sid = _stt(ps, _rows(ps, group), items=group)
    a = artifact(ps, sid)
    d = a["values"]["display"]
    text, bindings = stat_claim_text(a)
    assert text.startswith("Pooled RR ") and "prediction interval" in text
    assert [b["key"] for b in bindings] == ["estimate", "ci_level_percent", "ci_low", "ci_high", "k", "i2_percent",
                                            "ci_level_percent", "pi_low", "pi_high"]
    assert [b["text"] for b in bindings] == [n.text for n in extract_numbers(text)]
    assert all(b["artifact_id"] == sid for b in bindings)
    lo, hi = bindings[2]["text"], bindings[3]["text"]
    assert lo != hi and f"{d['k']} studies" in text and 0 < d["estimate"] < 0.5

    def verdict(t: str, bs=bindings) -> str:
        return verify_support(k, reg, ps, _stat(svc, sid, t, bs), actor="kernel").verdict

    assert verdict(text) == "supported"
    # k wrong, CI bounds swapped in the text, the bindings' bounds swapped, a number dropped
    assert verdict(text.replace(f"{d['k']} studies", f"{d['k'] + 1} studies")) == "unsupported"
    assert verdict(text.replace(f"{lo} to {hi}", f"{hi} to {lo}")) == "unsupported"
    swapped = [*bindings[:2], {**bindings[2], "key": "ci_high"}, {**bindings[3], "key": "ci_low"}, *bindings[4:]]
    assert verdict(text, swapped) == "unsupported"
    assert verdict(text, bindings[:-1]) == "unsupported"
    # rounded to the written digit only where that digit is no larger than the value ("0" is not 0.44, "1" not 0.12
    # or 1.66), and only display values count (the log-scale pooled estimate is not one)
    for est, expected in ((bindings[0]["text"], "supported"), ("0", "unsupported"), ("1", "unsupported"),
                          (f"{math.log(d['estimate']):.2f}", "unsupported")):
        t = f"Pooled RR {est} ({d['k']} studies)."
        assert verdict(t, _bind(sid, t, "estimate", "k")) == expected, est

    with pytest.raises(ValueError, match="only the kernel"):
        svc.add_claim(ClaimIn(text=text, number_bindings=bindings), generator_vendor=None, actor="a")
    with pytest.raises(ValueError, match="artifact"):
        svc.add_claim(ClaimIn(text=text, kind="stat_result", artifact_ids=[_stt(ps, _rows(ps))],
                              number_bindings=bindings), generator_vendor=None, actor="kernel", actor_kind="kernel")
    with pytest.raises(ValueError, match="pool"):
        stat_claim_text(artifact(ps, _fig(ps, _rows(ps))))
