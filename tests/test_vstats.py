"""V-STATS: kernel-made statistics and figures recompute from their extraction rows (Plan 5b Task 3)."""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from rk.argument import ArgumentService, ClaimIn
from rk.canon import canonical_json, sha256_hex
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
from rk.verify.stats import (
    PoolOptions,
    check_stats,
    make_forest_artifact,
    make_map_artifact,
    make_map_table_artifact,
    make_stats_artifact,
    row_hash,
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
WHERE = {"itm_A": ("RCT", ["UK", "US"]), "itm_B": ("RCT", "US"), "itm_C": ("cohort", None)}


@pytest.fixture
def env(tmp_home, replay_http):
    k, ps, _ = make_project(tmp_home, replay_http)
    for iid, data in STUDIES.items():
        for field, value in data.items():
            add_extraction(ps, 1, iid, field, value, None, True, actor="reader")
    yield k, ps
    k.close()


def _rows(ps, items=("itm_A", "itm_B", "itm_C", "itm_D")) -> list[dict]:
    return [r for r in extractions(ps, 1) if r["item_id"] in items]


def _verdicts(ps, target_id: str) -> list[tuple[str, str]]:
    return [tuple(r) for r in ps.db.execute(
        "SELECT verifier, verdict FROM verifications WHERE target_id=? ORDER BY rowid", (target_id,))]


def _stt(ps, rows, **kw) -> str:
    return make_stats_artifact(ps, rows, "RR", kw.pop("options", None), stage="synthesize", attempt=1,
                               actor="kernel", **kw)


def test_vstats_passes_on_untouched_inputs_and_fails_on_changed_row(env):
    _, ps = env
    rows = _rows(ps)
    opts = PoolOptions(hksj="auto", pi_df="k-2")
    sid = _stt(ps, rows, options=opts, created_by_task="tsk_S")
    labels = {"itm_A": "Aronson 1948", "itm_B": "Ferguson & Simes 1949", "itm_C": "Rosenthal et al 1960"}
    fid = make_forest_artifact(ps, rows, "RR", opts, labels=labels, stage="synthesize", attempt=1, actor="kernel")

    # the artifact records everything its recomputation needs
    s = artifact(ps, sid)
    effects = [compute("RR", STUDIES[i]) for i in ("itm_A", "itm_B", "itm_C")]
    expected = pool(effects, model="REML", hksj="auto", pi_df="k-2")
    assert (s["kind"], s["code_path"], s["code_version"], s["created_by_task"]) == \
        ("stt", "rk.stats.meta:pool", STATS_VERSION, "tsk_S")
    assert s["input_hashes"] == [sha256_hex(canonical_json(r)) for r in rows] == [row_hash(r) for r in rows]
    v = s["values"]
    assert v["row_ids"] == [r["id"] for r in rows] and v["measure"] == "RR"
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
    rebuild(ps.db)
    assert check_stats(ps, sid, actor="kernel").ok  # the hashes survive a rebuild of the projections

    # a row marked (here: missing) after the artifact was made is a changed input
    mark_extraction(ps, rows[0]["id"], "missing: the value was not in the text", actor="kernel")
    r = check_stats(ps, sid, actor="kernel")
    assert not r.ok and any(rows[0]["id"] in m and "changed" in m for m in r.mismatches)
    assert not check_stats(ps, fid, actor="kernel").ok
    assert _verdicts(ps, sid)[-1] == ("V-STATS", "mismatch")
    # and a row gone from the store is a missing input
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


def test_vstats_rejects_unknown_code_and_changed_values_or_files(env, monkeypatch):
    _, ps = env
    rows = _rows(ps)
    hand = add_artifact(ps, ArtifactIn(kind="stt", code_path="rk.stats.pool:pool", code_version=STATS_VERSION,
                                       input_hashes=[row_hash(r) for r in rows], values={"k": 3}, output_path=None),
                        stage="synthesize", attempt=1, actor="kernel")
    r = check_stats(ps, hand, actor="kernel")
    assert not r.ok and any("unknown code path" in m for m in r.mismatches)
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
    fid = make_forest_artifact(ps, rows, "RR", None, stage="synthesize", attempt=1, actor="kernel")
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
        make_stats_artifact(ps, _rows(ps), "HR", None, stage="synthesize", attempt=1, actor="kernel")
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
    sid = make_map_artifact(ps, rows, ["design", "region"], stage="synthesize", attempt=1, actor="kernel")
    tid = make_map_table_artifact(ps, rows, ["design", "region"], stage="synthesize", attempt=1, actor="kernel")
    s, t = artifact(ps, sid), artifact(ps, tid)
    per_study = [{"design": d, "region": r} for d, r in WHERE.values()]
    assert s["code_path"] == "rk.stats.plots:map_table" and s["values"]["fields"] == ["design", "region"]
    assert s["values"]["table"] == map_table(per_study, ["design", "region"])
    assert t["kind"] == "fig" and t["path"].endswith(".md")
    md = (ps.root / t["path"]).read_text(encoding="utf-8")
    assert "| RCT | 2 |" in md and "| not reported | 1 |" in md
    assert check_stats(ps, sid, actor="kernel").ok and check_stats(ps, tid, actor="kernel").ok
    mark_extraction(ps, rows[0]["id"], "missing: x", actor="kernel")
    assert not check_stats(ps, sid, actor="kernel").ok and not check_stats(ps, tid, actor="kernel").ok


def test_stat_result_claim_needs_an_artifact_and_never_calls_judges(env, monkeypatch):
    k, ps = env
    reg = build_registry(k)
    svc = ArgumentService(ps)
    with pytest.raises(ValueError, match="artifact"):
        svc.add_claim(ClaimIn(text="Pooled RR 0.62.", kind="stat_result"), generator_vendor=None, actor="kernel",
                      actor_kind="kernel", origin="synthesis")

    def no_judges(*a, **kw):
        raise AssertionError("a stat_result claim is never judged")

    monkeypatch.setattr(support, "run_judges", no_judges)
    rows = _rows(ps)
    sid = _stt(ps, rows)
    d = artifact(ps, sid)["values"]["display"]
    text = (f"Pooled RR {d['estimate']:.2f} (95% CI {d['ci_low']:.2f}–{d['ci_high']:.2f}; {d['k']} studies; "
            f"I² {d['i2_percent']:.0f}%).")

    def stat(t: str) -> str:
        return svc.add_claim(ClaimIn(text=t, kind="stat_result", artifact_ids=[sid]), generator_vendor=None,
                             actor="kernel", actor_kind="kernel", origin="synthesis")

    good = stat(text)
    r = verify_support(k, reg, ps, good, actor="kernel")
    assert r.verdict == "supported" and r.opinions == [] and r.numbers_ok
    assert svc.get_claim(good)["status"] == "supported"
    assert _verdicts(ps, good) == [("V-NUM", "pass"), ("V-SUPPORT", "supported")]
    assert _verdicts(ps, sid) == [("V-STATS", "pass")]
    (details,) = [r["details"] for r in ps.db.execute(
        "SELECT details FROM verifications WHERE target_id=? AND verifier='V-SUPPORT'", (good,))]
    assert '"by":"V-STATS"' in details

    wrong = stat(f"Pooled RR 0.99 ({d['k']} studies).")
    r = verify_support(k, reg, ps, wrong, actor="kernel")
    assert r.verdict == "unsupported" and [n.text for n in r.ungrounded_numbers] == ["0.99"]

    # once an input row changes, V-STATS fails and so does the claim
    mark_extraction(ps, rows[0]["id"], "missing: x", actor="kernel")
    r = verify_support(k, reg, ps, good, actor="kernel")
    assert r.verdict == "unsupported" and r.reason == "stats_mismatch"
    assert svc.get_claim(good)["status"] == "unsupported" and _verdicts(ps, sid)[-1] == ("V-STATS", "mismatch")
