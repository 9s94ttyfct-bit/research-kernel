import pytest

from rk.argument import ArgumentService, ClaimIn, EvidenceIn
from rk.checkpoints import list_checkpoints
from rk.config import Settings, save_settings
from rk.fulltext import attach_pdf
from rk.library.models import Creator, ItemIn
from rk.llm.drivers import DriverSpec
from rk.llm.types import Availability, TaskResult
from rk.notes import NoteIn, add_note
from rk.store.projections import rebuild
from rk.tools.builtin import build_registry
from rk.tools.registry import CallContext
from rk.verify.judge import JudgeOpinion, JudgeVerdict
from rk.verify.minicheck import Layer1Score, Layer1Unavailable
from rk.verify.support import combine, verify_support
from rk.workspace import Kernel

from .conftest import DOCS

QUOTE = "about copper catalysts for CO2 reduction"
CODEX, LUNA_A = DriverSpec("codex_cli"), DriverSpec("omp_gateway", "anthropic/claude-haiku-4-5")


def op(label, error=None):
    return JudgeOpinion(driver="d", vendor="v", label=label, error=error)


@pytest.mark.parametrize(("p", "labels", "k", "expected"), [
    (0.9, ["supported", "supported"], 2, "supported"),
    (0.9, ["supported"], 2, "uncertain"),            # one vote is not enough
    (0.9, ["supported"], 1, "supported"),
    (0.9, ["supported", "partial"], 2, "uncertain"),
    (0.5, ["supported", "supported"], 2, "uncertain"),  # layer 1 in the grey zone
    (0.1, ["contradicted", "contradicted"], 2, "contradicted"),
    (0.1, ["contradicted", "unsupported"], 2, "uncertain"),  # split negative labels are not agreement
    (0.1, ["unsupported", "unsupported"], 2, "unsupported"),
    (0.1, ["supported", "unsupported"], 2, "uncertain"),
    (0.9, ["unsupported", "unsupported"], 2, "uncertain"),  # layers disagree
    (None, ["supported", "supported"], 2, "uncertain"),
])
def test_combine_truth_table(p, labels, k, expected):
    assert combine(p, [op(lbl) for lbl in labels], tau_hi=0.85, tau_lo=0.15, min_judges=k) == expected


def test_failed_judges_do_not_count():
    ops = [op("supported"), op("uncertain", error="same_or_unknown_vendor")]
    assert combine(0.95, ops, tau_hi=0.85, tau_lo=0.15, min_judges=2) == "uncertain"


@pytest.mark.parametrize(("p", "labels", "k", "expected"), [
    (0.5, ["supported", "supported"], 2, "supported"),      # two agreeing judges: τ_hi_judged = 0.4 applies
    (0.3, ["supported", "supported"], 2, "uncertain"),      # still below the judged threshold
    (0.5, ["supported"], 1, "uncertain"),                   # one judge: layer-1-only τ_hi = 0.85 applies
    (0.5, ["supported", "partial"], 2, "uncertain"),        # judges disagree: no verdict at any p
    (0.55, ["unsupported", "unsupported"], 2, "unsupported"),  # τ_lo_judged = 0.6
    (0.55, ["unsupported", "contradicted"], 2, "uncertain"),   # split: no agreement, no relaxation
    (0.55, ["unsupported"], 1, "uncertain"),
    (0.7, ["contradicted", "contradicted"], 2, "uncertain"),
])
def test_combine_judged_tier(p, labels, k, expected):
    assert combine(p, [op(lbl) for lbl in labels], tau_hi=0.85, tau_lo=0.15, min_judges=k,
                   tau_hi_judged=0.4, tau_lo_judged=0.6) == expected


def test_judged_thresholds_never_tighten():
    ops = [op("supported"), op("supported")]
    assert combine(0.9, ops, tau_hi=0.85, tau_lo=0.15, min_judges=2, tau_hi_judged=0.95) == "supported"
    neg = [op("unsupported"), op("unsupported")]
    assert combine(0.1, neg, tau_hi=0.85, tau_lo=0.15, min_judges=2, tau_lo_judged=0.05) == "unsupported"


class FakeLayer1:
    def __init__(self, p=None, fail=False):
        self.p, self.fail = p, fail

    def score(self, doc, claim):
        if self.fail:
            raise Layer1Unavailable("no torch")
        return Layer1Score(p=self.p, chunk_index=0, n_chunks=1, model="fake")


class FakeJudge:
    kind = "chat_api"
    model = "fake"
    vendor = "x"

    def __init__(self, label, spans):
        self.v = JudgeVerdict(label=label, quoted_spans=spans)

    def available(self):
        return Availability(ok=True)

    def run(self, spec, ctx):
        return TaskResult(spec.task_id, True, self.v, "", "fake", "fake", 0.1, None)


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


def judges(label="supported", spans=("copper catalysts",)):
    j = FakeJudge(label, list(spans))
    return {"judge_drivers": [CODEX, LUNA_A], "judge_providers": {str(CODEX): j, str(LUNA_A): j}}


def test_supported_end_to_end_with_records(env):
    k, reg, ps, svc, claim = env
    cid = claim("Copper catalysts are studied for CO2 reduction.", vendor="google")
    r = verify_support(k, reg, ps, cid, actor="t", layer1=FakeLayer1(0.97), **judges())
    assert r.verdict == "supported" and r.numbers_ok and [o.label for o in r.opinions] == ["supported", "supported"]
    assert svc.get_claim(cid)["status"] == "supported"
    rows = ps.db.execute("SELECT verifier, verdict FROM verifications WHERE target_id=? ORDER BY verifier",
                         (cid,)).fetchall()
    assert [tuple(x) for x in rows] == [("V-NUM", "pass"), ("V-SUPPORT", "supported")]
    before = svc.get_claim(cid)
    rebuild(ps.db)
    assert svc.get_claim(cid) == before


def test_calibrated_judged_thresholds_apply_in_verify_support(env):
    k, reg, ps, _, claim = env
    cid = claim("Copper catalysts are studied for CO2 reduction.", vendor="google")
    assert verify_support(k, reg, ps, cid, actor="t", layer1=FakeLayer1(0.6), **judges()).verdict == "uncertain"
    save_settings(Settings(email="rk-fixtures@users.noreply.github.com", support_tau_hi_judged=0.5))
    r = verify_support(k, reg, ps, cid, actor="t", layer1=FakeLayer1(0.6), **judges())
    assert r.verdict == "supported" and r.tau_hi_judged == 0.5 and r.tau_hi == 0.85


def test_single_judge_never_supported(env):
    k, reg, ps, _, claim = env
    cid = claim("Copper catalysts are studied for CO2 reduction.", vendor="anthropic")  # the claude-hosted judge is out
    r = verify_support(k, reg, ps, cid, actor="t", layer1=FakeLayer1(0.99), **judges())
    assert [o.error for o in r.opinions] == [None, "same_or_unknown_vendor"] and r.verdict == "uncertain"


def test_layer1_unavailable_is_uncertain(env):
    k, reg, ps, _, claim = env
    cid = claim("Copper catalysts are studied for CO2 reduction.", vendor="google")
    r = verify_support(k, reg, ps, cid, actor="t", layer1=FakeLayer1(fail=True), **judges())
    assert r.verdict == "uncertain" and r.p is None and "layer1_unavailable" in r.layer1_reason


def test_ungrounded_number_blocks_supported(env):
    k, reg, ps, svc, claim = env
    cid = claim("Copper catalysts reduce CO2 by 40%.", vendor="google")
    r = verify_support(k, reg, ps, cid, actor="t", layer1=FakeLayer1(0.97), **judges())
    assert r.verdict == "uncertain" and not r.numbers_ok and [n.text for n in r.ungrounded_numbers] == ["40%"]
    assert svc.get_claim(cid)["status"] == "uncertain"


def test_uncertain_creates_checkpoint_in_collab_and_no_evidence(env):
    k, reg, ps, svc, _ = env
    cid = svc.add_claim(ClaimIn(text="An orphan claim."), generator_vendor="google", actor="a")
    r = verify_support(k, reg, ps, cid, actor="t", layer1=FakeLayer1(0.9), **judges())
    assert r.verdict == "uncertain" and r.reason == "no_evidence"
    (cp,) = list_checkpoints(ps, "open")
    assert cp["kind"] == "uncertain_support" and cp["options"]["claim_id"] == cid


def test_contradicted(env):
    k, reg, ps, svc, claim = env
    cid = claim("Copper catalysts cannot reduce CO2.", vendor="google")
    r = verify_support(k, reg, ps, cid, actor="t", layer1=FakeLayer1(0.02),
                       **judges("contradicted", ["copper catalysts for CO2 reduction"]))
    assert r.verdict == "contradicted" and svc.get_claim(cid)["status"] == "contradicted"
    assert list_checkpoints(ps) == []


def test_rk_verify_claim_tool_reports_layer1_state(env, monkeypatch):
    _k, reg, _ps, _, claim = env
    cid = claim("Copper catalysts are studied for CO2 reduction.", vendor="google")
    monkeypatch.setattr("rk.verify.support.get_minicheck", lambda *a: FakeLayer1(fail=True))
    out = reg.call("rk_verify", {"project": "sp", "target_type": "claim", "target_id": cid},
                   CallContext(role="cli", actor="u", idempotency_key="v1"))
    assert out["support"]["verdict"] == "uncertain" and "layer1_unavailable" in out["support"]["layer1_reason"]
