import json
import string

import pytest

from rk.eval.reportbench import (
    citation_pr,
    identifiers_in,
    normalize_title,
    resolver_titles,
    score_reports,
)


def test_normalize_title_matches_upstream_rule():
    def upstream(t):  # related_work_evaluator.py L534-544: lower, strip string.punctuation, collapse whitespace
        t = t.lower()
        t = t.translate(str.maketrans("", "", string.punctuation))
        return " ".join(t.split())

    for t in ["Attention Is All You Need", "BERT: Pre-training of Deep  Bidirectional\nTransformers",
              "What's in a (Name)?", "  Ünïcode — dashes & co.  "]:
        assert normalize_title(t) == upstream(t)


def test_citation_pr_hand_computed():
    r = citation_pr(["Paper A", "paper b!", "Paper C"], ["paper a", "Paper B", "Paper D", "Paper E"])
    assert r == {"precision": 2 / 3, "recall": 0.5, "n_pred": 3, "n_gt": 4, "n_match": 2}
    assert citation_pr([], ["x"])["precision"] == 0.0


def test_identifiers_in_text():
    text = ("See doi 10.1038/nature14539, https://doi.org/10.7717/peerj.1364. and arXiv:1706.03762v5 or "
            "https://arxiv.org/abs/1810.04805.")
    assert identifiers_in(text) == ["10.1038/nature14539", "10.7717/peerj.1364", "arXiv:1706.03762", "arXiv:1810.04805"]


def test_micro_macro_average(tmp_path):
    reports, gt = tmp_path / "r", tmp_path / "gt"
    reports.mkdir()
    gt.mkdir()
    (reports / "1111.11111.json").write_text(json.dumps({"response": "x", "cited_titles": ["A", "B"]}))
    (reports / "2222.22222.json").write_text(json.dumps({"content": "see 10.1234/zzz"}))
    (gt / "1111.11111.jsonl").write_text('{"title": "A"}\n{"title": "C"}\n')
    (gt / "2222.22222.jsonl").write_text('{"title": "Resolved Title"}\n')
    out = score_reports(reports, lambda i: "Resolved Title" if i == "10.1234/zzz" else None, gt_dir=gt)
    assert out["reports"]["1111.11111"]["precision"] == 0.5
    assert out["reports"]["2222.22222"] == {"precision": 1.0, "recall": 1.0, "n_pred": 1, "n_gt": 1, "n_match": 1}
    assert out["micro"] == {"precision": 2 / 3, "recall": 2 / 3}
    assert out["macro"] == {"precision": 0.75, "recall": 0.75}


def test_titles_resolved_from_dois(source_ctx):
    title = resolver_titles(source_ctx)
    assert "quotation" in title("10.7717/peerj.1364").lower()  # recorded Crossref/OpenAlex fixture
    assert title("not an identifier") is None


def test_empty_report_dir_errors(tmp_path):
    with pytest.raises(ValueError):
        score_reports(tmp_path, lambda i: None, gt_dir=tmp_path)
