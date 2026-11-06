import json

import pytest

from rk.text.layer import normalize_query
from rk.text.pdf import extract_pdf
from rk.verify.hedge import check_hedge, hedge_level
from rk.verify.paraphrase import check_paraphrase
from rk.verify.quote import verify_quote

from .conftest import DOCS, GOLD
from .gold_sources import layer


def test_quote_hyphen_variants():
    L = extract_pdf(DOCS / "hyphen.pdf")
    assert verify_quote("fact-checking", L).ok and verify_quote("evidence", L).ok
    assert verify_quote('"quoted" claims', L).ok  # curly quotes in source
    assert verify_quote("matter - and errors persist", L).ok  # em-dash in source
    r = verify_quote("fact checking evidence base", L)
    assert not r.ok and r.best_snippet and r.best_score > 0


def test_quote_ligature():
    assert verify_quote("The finding was robust", extract_pdf(DOCS / "ligature.pdf")).ok


def test_quote_locator_points_to_text():
    L = extract_pdf(DOCS / "two_pages.pdf")
    phrase = "contains the target phrase about copper catalysts"
    r = verify_quote(phrase, L)
    assert r.ok and r.page == 2
    assert normalize_query(L.pages[1].text[r.start : r.end]) == normalize_query(phrase)


def test_empty_quote_not_ok():
    assert not verify_quote("   ", extract_pdf(DOCS / "two_pages.pdf")).ok


def test_paraphrase_thresholds():
    src = "Quotation errors are common in medical journal articles and readers should be aware of them."
    assert not check_paraphrase("Quotation errors are common in medical journal articles, readers should know.", src).ok
    good = check_paraphrase("Medical papers frequently cite sources inaccurately.", src)
    assert good.ok and good.longest_common_run <= 8


def test_hedge_levels():
    assert hedge_level("X may reduce Y") == 0
    assert hedge_level("Results suggest X affects Y") == 1
    assert hedge_level("X reduced Y in mice") == 2
    assert hedge_level("This proves that X causes Y") == 3


def test_hedge_monotonic():
    assert check_hedge("X may reduce Y", ["X reduced Y in mice"]).ok
    assert not check_hedge("X proves that Y", ["results suggest X affects Y"]).ok
    assert check_hedge("X reduces Y", ["results suggest X", "X reduced Y in a trial"]).ok
    with pytest.raises(ValueError):
        check_hedge("x", [])


def _run(case: dict) -> bool:
    if case["kind"] == "quote":
        return verify_quote(case["text"], layer(case["source"])).ok
    if case["kind"] == "paraphrase":
        return check_paraphrase(case["text"], case["span"]).ok
    if case["kind"] == "hedge":
        return check_hedge(case["claim"], case["sources"]).ok
    raise ValueError(case["kind"])


def test_gold_set_100_percent():
    cases = [json.loads(line) for line in (GOLD / "quote_cases.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(cases) >= 120
    assert len({c["source"] for c in cases if "source" in c}) == 6
    failures = [c["id"] for c in cases if _run(c) != c["expected"]]
    assert failures == []
