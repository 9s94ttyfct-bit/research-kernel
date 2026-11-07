"""Kernel-made artifacts (``fig_``, ``stt_``) and the method design store (Plan 5b Task 1)."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from rk.canon import canonical_json, sha256_hex
from rk.discovery.store import hypotheses
from rk.production.artifacts import ArtifactIn, add_artifact, artifact, artifacts
from rk.production.models import DesignIn
from rk.production.store import design, freeze_design, latest_design, set_design
from rk.store.projections import rebuild

from .discovery_fakes import freeze_an_rq, make_project


@pytest.fixture
def ps(tmp_home, replay_http):
    k, ps, _ = make_project(tmp_home, replay_http)
    yield ps
    k.close()


def _table(ps, name: str) -> list[tuple]:
    return sorted(tuple(r) for r in ps.db.execute(f"SELECT * FROM {name}"))


def test_artifact_round_trip_with_file_hash(ps, tmp_path):
    svg = tmp_path / "forest.svg"
    svg.write_text("<svg>forest</svg>", encoding="utf-8")
    fig = ArtifactIn(kind="fig", code_path="rk.stats.forest:forest_svg", code_version="c0de", input_hashes=["h1", "h2"],
                     values={"k": 2}, output_path=str(svg))
    fid = add_artifact(ps, fig, stage="synthesize", attempt=1, created_by_task="tsk_X", actor="kernel")
    assert fid.startswith("fig_")
    a = artifact(ps, fid)
    assert a["path"] == f"artifacts/{fid}.svg" and (ps.root / a["path"]).read_bytes() == b"<svg>forest</svg>"
    assert a["sha256"] == sha256_hex(b"<svg>forest</svg>")
    assert (a["kind"], a["code_path"], a["code_version"], a["input_hashes"], a["values"], a["created_by_task"]) == \
        ("fig", "rk.stats.forest:forest_svg", "c0de", ["h1", "h2"], {"k": 2}, "tsk_X")
    assert str(tmp_path) not in json.dumps(a)  # the source path is not kept, only the project-relative file

    values = {"estimate": -0.48, "ci": [-0.73, -0.22], "k": 7}
    sid = add_artifact(ps, ArtifactIn(kind="stt", code_path="rk.stats.pool:pool", code_version="c0de",
                                      input_hashes=["h1"], values=values, output_path=None),
                       stage="synthesize", attempt=1, actor="kernel")
    s = artifact(ps, sid)
    assert sid.startswith("stt_") and s["path"] == f"artifacts/{sid}.json"
    body = (ps.root / s["path"]).read_bytes()
    assert json.loads(body) == values and s["sha256"] == sha256_hex(body) == sha256_hex(canonical_json(values))
    assert s["created_by_task"] is None

    with pytest.raises(ValueError, match="a figure needs its output file"):
        add_artifact(ps, fig.model_copy(update={"output_path": None}), stage="synthesize", attempt=1, actor="kernel")
    with pytest.raises(FileNotFoundError):
        add_artifact(ps, fig.model_copy(update={"output_path": str(tmp_path / "missing.svg")}), stage="synthesize",
                     attempt=1, actor="kernel")
    with pytest.raises(ValidationError):
        ArtifactIn(kind="tbl", code_path="x", code_version="v", input_hashes=[], values={}, output_path=None)

    assert {r["id"] for r in artifacts(ps, "synthesize", 1)} == {fid, sid}
    assert artifacts(ps, "synthesize", 2) == [] and artifact(ps, "stt_missing") is None
    before = _table(ps, "artifacts")
    rebuild(ps.db)
    assert _table(ps, "artifacts") == before


def _design(hyp_id: str) -> dict:
    return {
        "items": [{"id": "D1", "kind": "experiment", "sample": "40 cells per arm", "controls": ["bare carbon"],
                   "measurements": [{"id": "M1", "what": "Faradaic efficiency to C2", "instrument": "GC",
                                     "unit": "%"},
                                    {"id": "M2", "what": "stability", "instrument": "chronoamperometry"}],
                   "analysis": "two-sided t test, alpha 0.05", "data_source": "new measurements",
                   "hypothesis_ids": [hyp_id]}],
        "expected_results": "Copper beats nickel on C2 efficiency.",
        "alternative_explanations": ["surface area differs"],
    }


def test_design_freeze_hash_is_canonical(ps):
    rq_id = freeze_an_rq(ps)
    (h,) = hypotheses(ps, rq_id)
    raw = _design(h["id"])
    did = set_design(ps, DesignIn.model_validate(raw), stage="design", attempt=1, actor="ideator")
    assert did.startswith("dsg_") and latest_design(ps)["id"] == did
    d = design(ps, did)
    assert (d["status"], d["frozen_hash"], d["stage"], d["attempt"]) == ("draft", None, "design", 1)
    assert d["body"]["items"][0]["measurements"][1]["unit"] is None

    # the same design, its keys in another order: another row, the same frozen hash
    reordered = json.loads(json.dumps(raw, sort_keys=True))
    reordered = {k: reordered[k] for k in reversed(list(reordered))}
    other = set_design(ps, DesignIn.model_validate(reordered), stage="design", attempt=2, actor="ideator")
    assert latest_design(ps)["id"] == other
    h1 = freeze_design(ps, did, actor="kernel")
    assert h1 == sha256_hex(canonical_json(DesignIn.model_validate(raw).model_dump(mode="json")))
    assert freeze_design(ps, other, actor="kernel") == h1
    d = design(ps, did)
    assert (d["status"], d["frozen_hash"]) == ("frozen", h1) and d["frozen_at"]
    with pytest.raises(ValueError, match="already frozen"):
        freeze_design(ps, did, actor="kernel")
    with pytest.raises(KeyError):
        freeze_design(ps, "dsg_missing", actor="kernel")
    with pytest.raises(ValueError, match="unknown hypotheses"):
        set_design(ps, DesignIn.model_validate(_design("hyp_missing")), stage="design", attempt=3, actor="ideator")
    dup = _design(h["id"])
    dup["items"].append(dup["items"][0])
    with pytest.raises(ValidationError, match="duplicate design item ids"):
        DesignIn.model_validate(dup)
    dup = _design(h["id"])
    dup["items"][0]["measurements"][1]["id"] = "M1"
    with pytest.raises(ValidationError, match="duplicate measurement ids"):
        DesignIn.model_validate(dup)
    assert design(ps, "dsg_missing") is None
    before = _table(ps, "designs")
    rebuild(ps.db)
    assert _table(ps, "designs") == before
