import shutil

import pytest

from rk.config import Settings, save_settings
from rk.llm.drivers import DriverSpec
from rk.llm.types import Availability, TaskResult
from rk.tools.builtin import build_registry
from rk.verify.judge import JudgeVerdict, check_opinion, run_judges, span_in
from rk.workspace import Kernel

EVIDENCE = ("The patient received 50 mg of drug X daily for two weeks.\nNo adverse events were observed during "
            "treat-\nment.")
CODEX, LUNA, CLAUDE = DriverSpec("codex_cli"), DriverSpec("omp_gateway", "openai-codex/gpt-6-luna"), \
    DriverSpec("claude_cli")


class Fixed:
    kind = "chat_api"
    model = "fixed"

    def __init__(self, verdict: JudgeVerdict, vendor="openai"):
        self.verdict, self.vendor, self.calls = verdict, vendor, 0

    def available(self):
        return Availability(ok=True)

    def run(self, spec, ctx):
        self.calls += 1
        assert spec.tools == [] and spec.inputs["evidence"] == EVIDENCE
        return TaskResult(spec.task_id, True, self.verdict, self.verdict.model_dump_json(), "fixed", "fixed", 0.1, None)


@pytest.fixture
def env(tmp_home, replay_http):
    save_settings(Settings(email="rk-fixtures@users.noreply.github.com"))
    k = Kernel(tmp_home, http=replay_http)
    reg = build_registry(k)
    k.create_project("jd", "Judge", actor="t")
    yield k, reg
    k.close()


def test_span_in_uses_normalized_word_bounded_matching():
    assert span_in(EVIDENCE, "received 50 mg of drug X daily")
    assert span_in(EVIDENCE, "No adverse events were observed during treatment.")  # line-end hyphenation
    assert not span_in(EVIDENCE, "received 500 mg")
    assert not span_in(EVIDENCE, "eceived 50 mg")  # partial word


def test_check_opinion_rules():
    assert check_opinion(JudgeVerdict(label="supported", quoted_spans=["drug X daily"]), EVIDENCE) == ("supported",
                                                                                                       True)
    assert check_opinion(JudgeVerdict(label="supported", quoted_spans=[]), EVIDENCE)[0] == "uncertain"
    assert check_opinion(JudgeVerdict(label="unsupported", quoted_spans=[]), EVIDENCE)[0] == "unsupported"


def test_judge_fabricated_quote_downgraded(env):
    k, reg = env
    fake = Fixed(JudgeVerdict(label="supported", quoted_spans=["The drug cured every patient."]))
    (op,) = run_judges(k, reg, project="jd", claim_text="Drug X cures.", evidence_text=EVIDENCE,
                       generator_vendor="anthropic", drivers=[CODEX], providers={"codex_cli": fake})
    assert op.label == "uncertain" and op.raw_label == "supported" and not op.spans_ok and op.task_id


def test_judge_without_spans_downgraded(env):
    k, reg = env
    fake = Fixed(JudgeVerdict(label="contradicted", quoted_spans=[]))
    (op,) = run_judges(k, reg, project="jd", claim_text="x", evidence_text=EVIDENCE, generator_vendor="anthropic",
                       drivers=[CODEX], providers={"codex_cli": fake})
    assert op.label == "uncertain"


def test_same_vendor_judge_excluded(env):
    k, reg = env
    fake = Fixed(JudgeVerdict(label="supported", quoted_spans=["drug X daily"]))
    (op,) = run_judges(k, reg, project="jd", claim_text="x", evidence_text=EVIDENCE, generator_vendor="openai",
                       drivers=[CODEX], providers={"codex_cli": fake})
    assert op.error == "same_or_unknown_vendor" and fake.calls == 0


def test_duplicate_vendor_skipped_and_unknown_vendor_excluded(env):
    k, reg = env
    good = Fixed(JudgeVerdict(label="supported", quoted_spans=["drug X daily"]))
    ops = run_judges(k, reg, project="jd", claim_text="x", evidence_text=EVIDENCE, generator_vendor="anthropic",
                     drivers=[CODEX, LUNA, DriverSpec("ollama", "lfm3:latest")],
                     providers={"codex_cli": good, str(LUNA): good})
    assert [o.label for o in ops] == ["supported", "uncertain", "uncertain"]
    assert [o.error for o in ops] == [None, "duplicate_vendor", "same_or_unknown_vendor"] and good.calls == 1


@pytest.mark.live
@pytest.mark.llm
@pytest.mark.skipif(shutil.which("omp") is None, reason="omp not installed")
def test_luna_judge_real_pair(env):
    k, reg = env
    save_settings(Settings(email="rk-fixtures@users.noreply.github.com"))
    (yes,) = run_judges(k, reg, project="jd", claim_text="The patient took drug X.", evidence_text=EVIDENCE,
                        generator_vendor="human", drivers=[LUNA])
    (no,) = run_judges(k, reg, project="jd", claim_text="The patient never received drug X.",
                       evidence_text=EVIDENCE, generator_vendor="human", drivers=[LUNA])
    assert yes.label == "supported" and yes.spans_ok, yes
    assert no.label in ("contradicted", "unsupported"), no
