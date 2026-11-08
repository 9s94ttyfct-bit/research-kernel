"""Plan 5c Task 1: a section's markdown parsed into sentences with their source markers (spec §9.1 V-COVER), and the
draft store (``draft.written`` / ``draft.verified``)."""

import pytest

from rk.config import Settings, save_settings
from rk.production.manuscript import (
    draft_versions,
    latest_draft,
    parse_section,
    record_section_verdict,
    write_draft,
)
from rk.store.projections import rebuild
from rk.workspace import Kernel


def test_sentences_keep_abbreviations_and_decimals():
    (s,) = parse_section("Smith et al. found 0.62 (e.g. in trials) [[clm:clm_A]].")
    assert s.text == "Smith et al. found 0.62 (e.g. in trials) [[clm:clm_A]]."
    two = parse_section("Fig. 2 shows it vs. the control [[fig:fig_X]]. The effect was small [[inf]]!")
    assert [x.text for x in two] == ["Fig. 2 shows it vs. the control [[fig:fig_X]].",
                                     "The effect was small [[inf]]!"]
    assert [x.index for x in two] == [0, 1]


def test_markers_attach_to_their_sentence():
    out = parse_section("Melatonin reduced delirium [[clm:clm_A]] [[stat:stt_B]]. Searches ran in 2024 "
                        "[[run:evt_C]].\n\nA new paragraph [[ck]].")
    assert [[(m.kind, m.target) for m in s.markers] for s in out] == [
        [("clm", "clm_A"), ("stat", "stt_B")], [("run", "evt_C")], [("ck", None)]]
    first = out[0]
    assert first.text[first.markers[1].span[0]:first.markers[1].span[1]] == "[[stat:stt_B]]"


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
    record_section_verdict(ps, "results", 2, {"ok": False, "sentences": [{"index": 0, "ok": False,
                                                                           "problems": ["x"]}]}, actor="k")
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
