"""Plan 5c Task 1: a section's markdown parsed into sentences with their source markers (spec §9.1 V-COVER), and the
draft store (``draft.written`` / ``draft.verified``)."""

import os

import pytest
from pydantic import ValidationError

from rk.config import Settings, save_settings
from rk.production.manuscript import (
    draft_versions,
    latest_draft,
    parse_section,
    record_section_verdict,
    section_problems,
    source_markers,
    write_draft,
)
from rk.store.projections import rebuild
from rk.verify.cover import SectionVerdict
from rk.workspace import Kernel


def test_sentences_keep_abbreviations_and_decimals():
    (s,) = parse_section("Smith et al. found 0.62 (e.g. in trials) [[clm:clm_A]].")
    assert s.text == "Smith et al. found 0.62 (e.g. in trials) [[clm:clm_A]]."
    two = parse_section("Fig. 2 shows it vs. the control [[fig:fig_X]]. The effect was small [[inf]]!")
    assert [x.text for x in two] == ["Fig. 2 shows it vs. the control [[fig:fig_X]].",
                                     "The effect was small [[inf]]!"]
    assert [x.index for x in two] == [0, 1]


def test_markers_attach_to_their_sentence():
    md = ("Melatonin reduced delirium [[clm:clm_A]] [[stat:stt_B]]. Searches ran in 2024 [[run:evt_C]].\n\n"
          "A new paragraph [[ck]].")
    out = parse_section(md)
    assert [[(m.kind, m.target) for m in s.markers] for s in out] == [
        [("clm", "clm_A"), ("stat", "stt_B")], [("run", "evt_C")], [("ck", None)]]
    first = out[0]
    assert md[first.markers[1].span[0]:first.markers[1].span[1]] == "[[stat:stt_B]]"


def test_markers_in_code_spans_and_broken_markers_do_not_count():
    """Review Focus 3: a marker inside a code span or code block, or one split across a line break, is no marker;
    raw HTML is text, never markup."""
    out = parse_section("This uses `[[clm:clm_A]]` as text. A broken one [[clm:\nclm_B]] here. "
                        "Raw <b>html</b> stays [[inf]].\n\n```\n[[clm:clm_C]] in a block.\n```\n")
    assert [s.markers for s in out[:2]] == [[], []]
    assert "<b>html</b>" in out[2].text and [m.kind for m in out[2].markers] == ["inf"]
    assert all("clm_C" not in s.text for s in out)


def test_headings_are_not_sentences():
    out = parse_section("# Results\n\n## Primary outcome\n\nDelirium fell [[clm:clm_A]].\n\n"
                        "| a | b |\n|---|---|\n| 1 | 2 |\n")
    assert [s.text for s in out] == ["Delirium fell [[clm:clm_A]]."]


def test_list_items_are_prose():
    out = parse_section("- First point [[clm:clm_A]].\n- Second point [[inf]].")
    assert [s.text for s in out] == ["First point [[clm:clm_A]].", "Second point [[inf]]."]


# ---- fix round 1: the splitter merges no two sentences (one test per review probe) ----
def test_markers_after_terminal_punctuation_attach_to_the_sentence_they_follow():
    out = parse_section("It works. [[clm:clm_A]] Unmarked other claim. [[inf]]")
    assert [s.text for s in out] == ["It works. [[clm:clm_A]]", "Unmarked other claim. [[inf]]"]
    assert [[m.target for m in s.markers] for s in out] == [["clm_A"], [None]]


def test_a_sentence_starting_with_a_digit_is_split():
    out = parse_section("Delirium was rare [[inf]]. 12 patients died [[clm:clm_A]].")
    assert [s.text for s in out] == ["Delirium was rare [[inf]].", "12 patients died [[clm:clm_A]]."]


def test_a_sentence_starting_lowercase_or_with_a_non_ascii_letter_is_split():
    assert [s.text for s in parse_section("It rose [[inf]]. mRNA levels fell [[clm:clm_A]].")] == [
        "It rose [[inf]].", "mRNA levels fell [[clm:clm_A]]."]
    assert len(parse_section("It rose [[inf]]. β-blockers were used [[clm:clm_A]].")) == 2
    assert len(parse_section("It rose [[inf]]. Ölmez agreed [[clm:clm_A]].")) == 2


def test_no_is_not_an_abbreviation():
    out = parse_section("The answer was no. Further trials are needed [[inf]].")
    assert [s.text for s in out] == ["The answer was no.", "Further trials are needed [[inf]]."]


def test_a_capital_letter_is_an_initial_only_before_a_capitalised_name():
    assert len(parse_section("As shown by J. Smith, it works [[clm:clm_A]].")) == 1
    for text in ("Patients took vitamin D. The trial ran [[clm:clm_A]].", "It was group A. Next came B [[inf]].",
                 "See Figure 2A. Then it fell [[inf]]."):
        assert len(parse_section(text)) == 2, text


# ---- fix round 1: tables and captions, markers in the source ----
def test_tables_and_table_captions_are_section_problems_not_sentences():
    md = "Delirium fell [[clm:clm_A]].\n\nTable: Results 42.\n\n: Other caption 7.\n"
    assert [s.text for s in parse_section(md)] == ["Delirium fell [[clm:clm_A]]."]
    problems = section_problems(md)
    assert len(problems) == 2 and all("caption" in p for p in problems)
    (table,) = section_problems("Text [[inf]].\n\n| a | b |\n|---|---|\n| 0.99 | melatonin halves delirium |\n")
    assert "table" in table and "code artifacts" in table
    assert section_problems("Text [[inf]].\n\nMore: 3 points [[inf]].\n") == []


def test_spans_are_source_offsets_into_the_section_markdown():
    md = ("# Results\n\n> Quoted *text* here [[clm:clm_A]].\n\n- Item **one** [[inf]]. Item two [[clm:clm_B]].\n\n"
          "A [link](https://doi.org/10.1/x) and `code` [[inf]].\n")
    out = parse_section(md)
    assert [md[s.span[0]:s.span[1]] for s in out] == [
        "Quoted *text* here [[clm:clm_A]].", "Item **one** [[inf]].", "Item two [[clm:clm_B]].",
        "A [link](https://doi.org/10.1/x) and `code` [[inf]]."]
    assert [s.text for s in out] == ["Quoted text here [[clm:clm_A]].", "Item one [[inf]].", "Item two [[clm:clm_B]].",
                                     "A link and `code` [[inf]]."]
    assert [s.quoted for s in out] == [True, False, False, False]
    for s in out:
        for m in s.markers:
            assert md[m.span[0]:m.span[1]] == (f"[[{m.kind}:{m.target}]]" if m.target else f"[[{m.kind}]]")
    assert out[3].prose == "A link and        [[inf]]."  # the code span is masked from the prose checks


@pytest.mark.parametrize("fake", [r"\[\[clm:clm_A\]\]", r"\[[clm:clm_A]]", "&#91;&#91;clm:clm_A&#93;&#93;",
                                  "[[clm:*clm_A*]]"],
                         ids=["escaped", "first-bracket-escaped", "entity-encoded", "emphasis-split"])
def test_escaped_entity_encoded_and_emphasis_split_markers_do_not_count(fake):
    md = f"It works {fake}."
    (s,) = parse_section(md)
    assert s.markers == [] and source_markers(md) == []


def test_source_markers_skip_code_spans_and_code_blocks():
    md = "Use `[[clm:clm_A]]` here [[inf]].\n\n```\n[[clm:clm_B]]\n```\n\n    [[clm:clm_C]]\n"
    assert [(m.kind, md[m.span[0]:m.span[1]]) for m in source_markers(md)] == [("inf", "[[inf]]")]


@pytest.fixture
def ps(tmp_home, replay_http):
    save_settings(Settings(email="rk-fixtures@users.noreply.github.com"))
    k = Kernel(tmp_home, http=replay_http)
    yield k.create_project("ms", "Ms", actor="t")
    k.close()


def test_draft_versions_files_and_verdicts_survive_a_rebuild(ps):
    assert latest_draft(ps, "results") is None
    v1 = write_draft(ps, "results", "One [[inf]].", stage="draft", attempt=1, round=0, by_task=None, actor="k")
    v2 = write_draft(ps, "results", "Two [[inf]].", stage="draft", attempt=1, round=0, by_task="tsk_1", actor="k")
    assert (v1, v2) == (1, 2) and latest_draft(ps, "results") == (2, "Two [[inf]].")
    assert (ps.root / "artifacts" / "drafts" / "results.v2.md").read_text() == "Two [[inf]]."
    verdict = SectionVerdict(section="results", version=2, ck_share=0.0, ok=False,
                             sentences=[{"index": 0, "ok": False, "problems": ["x"]}])
    record_section_verdict(ps, "results", 2, verdict, actor="k")
    before = draft_versions(ps, "results")
    assert [d["version"] for d in before] == [1, 2] and before[1]["verdict"]["ok"] is False
    assert before[0]["sha256"] != before[1]["sha256"] and before[1]["by_task"] == "tsk_1"
    rebuild(ps.db)
    assert draft_versions(ps, "results") == before


def test_a_draft_file_changed_on_disk_is_not_the_draft(ps):
    write_draft(ps, "results", "One [[inf]].", stage="draft", attempt=1, round=0, by_task=None, actor="k")
    (ps.root / "artifacts" / "drafts" / "results.v1.md").write_text("Tampered [[inf]].")
    with pytest.raises(ValueError, match="no longer has its recorded sha256"):
        latest_draft(ps, "results")


def test_a_section_verdict_is_validated_before_it_is_recorded(ps):
    write_draft(ps, "results", "One [[inf]].", stage="draft", attempt=1, round=0, by_task=None, actor="k")
    with pytest.raises(ValidationError):
        record_section_verdict(ps, "results", 1, {"ok": False, "sentences": []}, actor="k")
    other = SectionVerdict(section="methods", version=None, sentences=[], ck_share=0.0, ok=True)
    with pytest.raises(ValueError, match="methods"):
        record_section_verdict(ps, "results", 1, other, actor="k")
    good = other.model_copy(update={"section": "results"}).model_dump(mode="json")
    record_section_verdict(ps, "results", 1, good, actor="k")
    assert draft_versions(ps, "results")[0]["verdict"]["version"] == 1


def test_the_draft_file_is_synced_before_its_event(ps, monkeypatch):
    rows_at_sync = []
    real = os.fsync

    def fsync(fd):
        rows_at_sync.append(ps.db.execute("SELECT COUNT(*) FROM drafts").fetchone()[0])
        real(fd)

    monkeypatch.setattr(os, "fsync", fsync)
    write_draft(ps, "results", "One [[inf]].", stage="draft", attempt=1, round=0, by_task=None, actor="k")
    assert rows_at_sync and rows_at_sync[0] == 0
