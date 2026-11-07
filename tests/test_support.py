import tomllib

import pytest

from rk.argument import ArgumentService, ClaimIn, EvidenceIn
from rk.checkpoints import create_checkpoint, list_checkpoints
from rk.config import (
    DEFAULT_DRIVERS,
    Settings,
    config_path,
    load_settings,
    save_settings,
)
from rk.fulltext import attach_pdf
from rk.library.models import Creator, ItemIn
from rk.llm.drivers import DriverSpec
from rk.llm.types import TaskResult
from rk.llm.vendors import vendor_of
from rk.notes import NoteIn, add_note
from rk.roles.policy import JUDGE_ROLES
from rk.store.projections import rebuild
from rk.tools.builtin import build_registry
from rk.tools.registry import CallContext
from rk.verify import support
from rk.verify.judge import JudgeOpinion
from rk.verify.records import record_verification
from rk.verify.support import SupportResult, combine, verify_support
from rk.workspace import Kernel

from .conftest import DOCS
from .discovery_fakes import FakeJudge

QUOTE = "about copper catalysts for CO2 reduction"
LUNA = DriverSpec("codex_cli", "gpt-6-luna")
DEEPSEEK = DriverSpec("ollama", "deepseek-v4.1-flash:cloud")
HAIKU = DriverSpec("claude_cli", "claude-haiku-5-5")
CLAIM = "Copper catalysts are studied for CO2 reduction."


def op(label, error=None):
    return JudgeOpinion(driver="d", vendor="v", label=label, error=error)


@pytest.mark.parametrize(("labels", "k", "expected"), [
    (["supported", "supported"], 2, "supported"),
    (["supported"], 2, "uncertain"),            # one vote is not enough
    (["supported"], 1, "supported"),
    (["supported", "partial"], 2, "uncertain"),
    (["supported", "supported", "supported"], 2, "supported"),
    (["supported", "supported", "unsupported"], 2, "uncertain"),  # every valid judge must agree
    (["contradicted", "contradicted"], 2, "contradicted"),
    (["contradicted", "unsupported"], 2, "uncertain"),  # split negative labels are not agreement
    (["unsupported", "unsupported"], 2, "unsupported"),
    (["unsupported", "unsupported", "unsupported"], 2, "unsupported"),
    (["supported", "unsupported"], 2, "uncertain"),
    (["uncertain", "uncertain"], 2, "uncertain"),
    ([], 0, "uncertain"),                         # no valid judge never passes
])
def test_combine_truth_table(labels, k, expected):
    assert combine([op(lbl) for lbl in labels], min_judges=k) == expected


def test_failed_judges_do_not_count():
    ops = [op("supported"), op("uncertain", error="same_or_unknown_vendor")]
    assert combine(ops, min_judges=2) == "uncertain"


def test_support_result_has_no_layer1_fields():
    assert not {"p", "layer1_reason", "n_chunks", "tau_hi", "tau_lo", "tau_hi_judged",
                "tau_lo_judged"} & set(SupportResult.model_fields)


def test_judge_roles_and_default_judges():
    assert JUDGE_ROLES == ("judge", "judge_2", "judge_3")
    judges = [DriverSpec.parse(DEFAULT_DRIVERS[r]) for r in JUDGE_ROLES]
    assert [str(j) for j in judges] == [str(LUNA), str(DEEPSEEK), str(HAIKU)]
    assert [vendor_of(j.provider, j.model) for j in judges] == ["openai", "deepseek", "anthropic"]


@pytest.fixture
def env(tmp_home, replay_http):
    save_settings(Settings(email="rk-fixtures@users.noreply.github.com"))
    k = Kernel(tmp_home, http=replay_http)
    reg = build_registry(k)
    ps = k.create_project("sp", "Support", actor="t")
    _, lib = k.project("sp")
    iid = lib.add_item(ItemIn(title="Copper catalysts", year=2021, creators=[Creator(family="Li")]), added_by="human",
                       actor="t").item_id
    attach_pdf(ps, lib, iid, DOCS / "two_pages.pdf", origin="user_upload", actor="t")
    nid = add_note(ps, lib, NoteIn(source_id=iid, kind="quote", text=QUOTE, role_in_source="main"), actor="t").note_id
    svc = ArgumentService(ps)

    def claim(text, vendor="anthropic"):
        cid = svc.add_claim(ClaimIn(text=text), generator_vendor=vendor, actor="a")
        svc.link_evidence(EvidenceIn(claim_id=cid, note_ids=[nid]), actor="a")
        return cid

    yield k, reg, ps, svc, claim
    k.close()


class FailedJudge(FakeJudge):
    """A judge whose task fails without an error message."""

    def run(self, spec, ctx):
        self.inputs.append(spec.inputs)
        return TaskResult(spec.task_id, False, None, "", "fake", "fake", 0.1, None)


def judges(*pairs, spans=("copper catalysts",)):
    """``(driver, label)`` per judge, in role order; the label ``"failed"`` makes that judge's task fail."""
    providers = {str(d): FailedJudge("supported", []) if label == "failed" else FakeJudge(label, list(spans))
                 for d, label in pairs}
    return {"judge_drivers": [d for d, _ in pairs], "judge_providers": providers}


def test_supported_needs_two_agreeing_cross_vendor_judges(env):
    k, reg, ps, svc, claim = env
    cid = claim(CLAIM, vendor="anthropic")
    js = judges((LUNA, "supported"), (DEEPSEEK, "supported"), (HAIKU, "supported"))
    r = verify_support(k, reg, ps, cid, actor="t", **js)
    assert r.verdict == "supported" and r.min_judges == 2 and r.numbers_ok
    assert [(o.vendor, o.label, o.error) for o in r.opinions] == [
        ("openai", "supported", None), ("deepseek", "supported", None),
        ("anthropic", "uncertain", "same_or_unknown_vendor")]
    assert js["judge_providers"][str(HAIKU)].inputs == []  # the generator's vendor never judges
    assert svc.get_claim(cid)["status"] == "supported"
    rows = ps.db.execute("SELECT verifier, verdict FROM verifications WHERE target_id=? ORDER BY verifier",
                         (cid,)).fetchall()
    assert [tuple(x) for x in rows] == [("V-NUM", "pass"), ("V-SUPPORT", "supported")]
    before = svc.get_claim(cid)
    rebuild(ps.db)
    assert svc.get_claim(cid) == before


def test_one_dissent_makes_it_uncertain(env):
    k, reg, ps, svc, claim = env
    cid = claim(CLAIM)
    r = verify_support(k, reg, ps, cid, actor="t", **judges((LUNA, "supported"), (DEEPSEEK, "unsupported")))
    assert r.verdict == "uncertain" and svc.get_claim(cid)["status"] == "uncertain"


def test_shared_negative_label_is_kept(env):
    k, reg, ps, svc, claim = env
    spans = ["copper catalysts for CO2 reduction"]
    cid = claim("Copper catalysts cannot reduce CO2.")
    r = verify_support(k, reg, ps, cid, actor="t",
                       **judges((LUNA, "contradicted"), (DEEPSEEK, "contradicted"), spans=spans))
    assert r.verdict == "contradicted" and svc.get_claim(cid)["status"] == "contradicted"
    assert list_checkpoints(ps) == []
    r = verify_support(k, reg, ps, cid, actor="t",
                       **judges((LUNA, "unsupported"), (DEEPSEEK, "contradicted"), spans=spans))
    assert r.verdict == "uncertain"


def test_one_valid_judge_is_not_enough(env):
    k, reg, ps, _, claim = env
    cid = claim(CLAIM)
    r = verify_support(k, reg, ps, cid, actor="t", **judges((LUNA, "supported"), (DEEPSEEK, "failed")))
    assert [o.error for o in r.opinions] == [None, "failed"] and r.verdict == "uncertain"


def test_three_admitted_judges_all_must_agree(env):
    k, reg, ps, _, claim = env
    # OpenAI generator: luna is skipped, haiku and deepseek decide (and must agree).
    cid = claim(CLAIM, vendor="openai")
    r = verify_support(k, reg, ps, cid, actor="t",
                       **judges((LUNA, "supported"), (DEEPSEEK, "supported"), (HAIKU, "unsupported")))
    assert [o.error for o in r.opinions] == ["same_or_unknown_vendor", None, None] and r.verdict == "uncertain"
    # Human generator: all three are admitted; two "supported" and one "unsupported" is not agreement.
    cid = claim(CLAIM, vendor="human")
    r = verify_support(k, reg, ps, cid, actor="t",
                       **judges((LUNA, "supported"), (DEEPSEEK, "supported"), (HAIKU, "unsupported")))
    assert [o.error for o in r.opinions] == [None, None, None] and r.verdict == "uncertain"
    r = verify_support(k, reg, ps, cid, actor="t",
                       **judges((LUNA, "supported"), (DEEPSEEK, "supported"), (HAIKU, "supported")))
    assert r.verdict == "supported"


def test_ungrounded_number_blocks_supported(env):
    k, reg, ps, svc, claim = env
    cid = claim("Copper catalysts reduce CO2 by 40%.", vendor="google")
    r = verify_support(k, reg, ps, cid, actor="t", **judges((LUNA, "supported"), (DEEPSEEK, "supported")))
    assert r.verdict == "uncertain" and not r.numbers_ok and [n.text for n in r.ungrounded_numbers] == ["40%"]
    assert svc.get_claim(cid)["status"] == "uncertain"


def test_uncertain_creates_checkpoint_in_collab_and_no_evidence(env):
    k, reg, ps, svc, claim = env
    cid = svc.add_claim(ClaimIn(text="An orphan claim."), generator_vendor="google", actor="a")
    r = verify_support(k, reg, ps, cid, actor="t", **judges((LUNA, "supported"), (DEEPSEEK, "supported")))
    assert r.verdict == "uncertain" and r.reason == "no_evidence"
    (cp,) = list_checkpoints(ps, "open")
    assert cp["kind"] == "uncertain_support" and cp["options"] == {"claim_id": cid, "opinions": [],
                                                                   "reason": "no_evidence"}
    cid = claim(CLAIM)
    verify_support(k, reg, ps, cid, actor="t", **judges((LUNA, "supported"), (DEEPSEEK, "unsupported")))
    cp = list_checkpoints(ps, "open")[-1]
    assert set(cp["options"]) == {"claim_id", "opinions", "reason"} and len(cp["options"]["opinions"]) == 2


def test_withdrawn_claim_is_refused_before_any_judge_or_record(env):
    k, reg, ps, svc, claim = env
    cid = claim(CLAIM)
    svc.withdraw_claim(cid, reason="uncertain in P1", actor="kernel")
    js = judges((LUNA, "supported"), (DEEPSEEK, "supported"))
    with pytest.raises(ValueError, match="withdrawn"):
        verify_support(k, reg, ps, cid, actor="t", **js)
    assert all(p.inputs == [] for p in js["judge_providers"].values())
    assert ps.db.execute("SELECT COUNT(*) FROM verifications WHERE target_id=?", (cid,)).fetchone()[0] == 0
    assert list_checkpoints(ps) == []


def test_rk_verify_claim_tool_returns_the_judges_verdict(env, monkeypatch):
    _k, reg, _ps, _, claim = env
    cid = claim(CLAIM)
    fake = judges((LUNA, "supported"), (DEEPSEEK, "supported"))
    # the tool takes no judge providers, so the configured (real CLI) judges are replaced here
    real_run_judges = support.run_judges
    monkeypatch.setattr(support, "run_judges", lambda *a, **kw: real_run_judges(
        *a, **{**kw, "drivers": fake["judge_drivers"], "providers": fake["judge_providers"]}))
    out = reg.call("rk_verify", {"project": "sp", "target_type": "claim", "target_id": cid},
                   CallContext(role="cli", actor="u", idempotency_key="v1"))
    assert out["support"]["verdict"] == "supported" and "p" not in out["support"]


def test_old_config_keys_are_ignored(tmp_home):
    config_path().parent.mkdir(parents=True, exist_ok=True)
    config_path().write_text(
        'email = "rk-fixtures@users.noreply.github.com"\n'
        'support_model = "ollama:bespoke-minicheck:7b-q8_0"\n'
        'support_tau_hi = 0.9\nsupport_tau_lo = 0.1\nsupport_tau_hi_judged = 0.5\nsupport_tau_lo_judged = 0.6\n'
        'support_min_judges = 2\n'
        'support_calibration = { model = "ollama:bespoke-minicheck:7b-q8_0", tau_hi = 0.9 }\n'
        '[drivers]\njudge = "codex_cli"\njudge_2 = "ollama:deepseek-v4.1-flash:cloud"\nreader = "codex_cli"\n')
    s = load_settings()
    assert s.email == "rk-fixtures@users.noreply.github.com" and s.support_min_judges == 2
    # a stored [drivers] table keeps its roles; roles it lacks (judge_3, the other roles) come from the defaults
    assert s.drivers == {**DEFAULT_DRIVERS, "judge": "codex_cli", "reader": "codex_cli"}
    save_settings(s)
    stored = tomllib.loads(config_path().read_text())
    assert not {k for k in stored if k.startswith("support_")} - {"support_min_judges"}
    assert stored["drivers"]["judge_3"] == DEFAULT_DRIVERS["judge_3"] and stored["drivers"]["judge"] == "codex_cli"
    assert Settings(drivers={"judge": "codex_cli"}).drivers == {**DEFAULT_DRIVERS, "judge": "codex_cli"}


def test_old_support_records_still_render(env):
    _k, reg, ps, svc, claim = env
    cid = claim(CLAIM)
    old = {"verdict": "uncertain", "p": 0.42, "layer1_reason": "layer1_unavailable: no torch", "n_chunks": 1,
           "opinions": [], "numbers_ok": True, "ungrounded_numbers": [], "tau_hi": 0.85, "tau_lo": 0.15,
           "tau_hi_judged": None, "tau_lo_judged": None, "min_judges": 2, "reason": None}
    vid = record_verification(ps, "claim", cid, "V-SUPPORT", "uncertain", old, actor="t")
    svc.set_claim_status(cid, "uncertain", verification_id=vid, actor="t")
    chk = create_checkpoint(ps, "uncertain_support", f"Is this claim supported by its evidence?\n\n{CLAIM}",
                            {"claim_id": cid, "p": 0.42, "opinions": [], "reason": "layer1_unavailable: no torch"},
                            stage="argue", actor="t")
    ctx = CallContext(role="cli", actor="u")
    got = reg.call("rk_claim_get", {"project": "sp", "claim_id": cid}, ctx)["claim"]
    assert got["status"] == "uncertain" and got["verification_id"] == vid
    (cp,) = reg.call("rk_checkpoint_list", {"project": "sp", "status": "open"}, ctx)["checkpoints"]
    assert cp["id"] == chk and cp["options"]["p"] == 0.42
    reg.call("rk_checkpoint_answer", {"project": "sp", "checkpoint_id": chk, "answer": {"supported": False}},
             CallContext(role="cli", actor="u", idempotency_key="a1"))
    assert list_checkpoints(ps, "open") == []
    rebuild(ps.db)
    assert svc.get_claim(cid)["status"] == "uncertain"
