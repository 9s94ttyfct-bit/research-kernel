import pytest

from rk.config import Settings, save_settings
from rk.fulltext import attach_pdf
from rk.library.models import Creator, ItemIn
from rk.llm.drivers import DriverSpec
from rk.llm.types import Availability, TaskResult
from rk.notes import NoteIn, add_note
from rk.support_check import (
    ExtractedClaim,
    ExtractedClaims,
    QuotedEvidence,
    support_check,
)
from rk.tools.builtin import build_registry
from rk.verify.judge import JudgeVerdict
from rk.workspace import Kernel

from .conftest import DOCS

QUOTE = "about copper catalysts for CO2 reduction"
CLAIMS = ["Copper catalysts are discussed for CO2 reduction.", "Copper catalysts cut CO2 by 40%."]
CODEX, LUNA_A = DriverSpec("codex_cli"), DriverSpec("omp_gateway", "anthropic/claude-haiku-4-5")


class Driver:
    """Plays arguer (two claims) and reader (quotes the copper sentence via the real note tool path)."""

    kind, model = "chat_api", "d"

    def __init__(self, vendor="openai"):
        self.vendor = vendor
        self.roles = []

    def available(self):
        return Availability(ok=True)

    def run(self, spec, ctx):
        self.roles.append(spec.role)
        if spec.role == "arguer":
            out = ExtractedClaims(claims=[ExtractedClaim(sentence_index=i, text=t) for i, t in enumerate(CLAIMS)])
            return TaskResult(spec.task_id, True, out, "", "d", "d", 0.0, None)
        ps, lib = ctx.kernel.project(spec.project)
        nid = add_note(ps, lib, NoteIn(source_id=spec.inputs["source_ids"][0], kind="quote", text=QUOTE,
                                       role_in_source="main"), actor="reader").note_id
        return TaskResult(spec.task_id, True, QuotedEvidence(note_ids=[nid]), "", "d", "d", 0.0, None)


class Judge:
    kind, model, vendor = "chat_api", "j", "x"

    def __init__(self):
        self.calls = 0

    def available(self):
        return Availability(ok=True)

    def run(self, spec, ctx):
        self.calls += 1
        v = JudgeVerdict(label="supported", quoted_spans=["copper catalysts for CO2 reduction"])
        return TaskResult(spec.task_id, True, v, "", "j", "j", 0.0, None)


@pytest.fixture
def env(tmp_home, replay_http):
    save_settings(Settings(email="rk-fixtures@users.noreply.github.com"))
    k = Kernel(tmp_home, http=replay_http)
    reg = build_registry(k)
    ps = k.create_project("sc", "Support check", actor="t")
    _, lib = k.project("sc")
    iid = lib.add_item(ItemIn(title="Copper catalysts", year=2021, creators=[Creator(family="Li")]), added_by="human",
                       actor="t").item_id
    attach_pdf(ps, lib, iid, DOCS / "two_pages.pdf", origin="user_upload", actor="t")
    yield k, reg, iid
    k.close()


def test_support_check_end_to_end_offline(env):
    k, reg, iid = env
    judge = Judge()
    rep = support_check(k, reg, project="sc", text=" ".join(CLAIMS), source_ids=[iid], driver=DriverSpec("ollama",
                        "gemma4:31b-cloud"), provider=Driver(vendor="google"), judge_drivers=[CODEX, LUNA_A],
                        judge_providers={str(CODEX): judge, str(LUNA_A): judge},
                        gold={0: "supported", 1: "unsupported"})
    first, second = rep.rows
    assert first.verdict == "supported" and first.evidence and first.evidence[0].startswith("p2[")
    assert "p" not in first.model_dump()
    assert second.verdict == "uncertain" and not second.numbers_ok  # 40% is in no evidence
    assert rep.accuracy == 0.5 and rep.confusion["unsupported"]["uncertain"] == 1


def test_generator_vendor_drives_judge_exclusion(env):
    k, reg, iid = env
    judge = Judge()
    rep = support_check(k, reg, project="sc", text=CLAIMS[0], source_ids=[iid], driver=CODEX,
                        provider=Driver(vendor="openai"), judge_drivers=[CODEX, LUNA_A],
                        judge_providers={str(CODEX): judge, str(LUNA_A): judge}, min_judges=1)
    row = rep.rows[0]
    assert row.judges[0].startswith("codex_cli=uncertain (same_or_unknown_vendor)")
    assert row.judges[1].startswith("omp_gateway:anthropic/claude-haiku-4-5=supported") and judge.calls == 2
    assert row.verdict == "supported"  # one eligible judge, min_judges=1


def test_unknown_source_rejected(env):
    k, reg, _ = env
    with pytest.raises(KeyError):
        support_check(k, reg, project="sc", text="x", source_ids=["src_nope"], driver=CODEX, provider=Driver())


class BadArguer(Driver):
    def __init__(self, claims, reader_fails=False):
        super().__init__(vendor="google")
        self.claims, self.reader_fails = claims, reader_fails

    def run(self, spec, ctx):
        if spec.role == "arguer":
            out = ExtractedClaims(claims=[ExtractedClaim(sentence_index=i, text=t) for i, t in self.claims])
            return TaskResult(spec.task_id, True, out, "", "d", "d", 0.0, None)
        if self.reader_fails:
            return TaskResult(spec.task_id, False, None, "", "d", "d", 0.0, "provider_error: quota")
        return super().run(spec, ctx)


def test_duplicate_sentence_indices_rejected(env):
    k, reg, iid = env
    with pytest.raises(RuntimeError, match="invalid sentence indices"):
        support_check(k, reg, project="sc", text="x", source_ids=[iid], driver=CODEX,
                      provider=BadArguer([(0, CLAIMS[0]), (0, CLAIMS[1])]))


def test_missing_gold_scored_and_reader_failure_reported(env):
    k, reg, iid = env
    judge = Judge()
    rep = support_check(k, reg, project="sc", text=" ".join(CLAIMS), source_ids=[iid], driver=CODEX,
                        provider=BadArguer([(0, CLAIMS[0])], reader_fails=True), judge_drivers=[CODEX, LUNA_A],
                        judge_providers={str(CODEX): judge, str(LUNA_A): judge},
                        gold={0: "supported", 1: "unsupported"})
    (row,) = rep.rows
    assert row.reader_error == "provider_error: quota" and row.evidence == [] and row.verdict == "uncertain"
    assert rep.missing_gold == [1] and rep.confusion["unsupported"]["uncertain"] == 1 and rep.accuracy == 0.0
