import pytest

from rk.verify.numbers import check_numbers, extract_numbers


def values(text):
    return [(n.value, n.unit) for n in extract_numbers(text)]


@pytest.mark.parametrize(("claim", "evidence"), [
    ("1,234 patients", "1234 patients"),
    ("12 %", "12%"),
    ("12%", "12 percent"),
    ("−0.5", "-0.5"),
    ("3×10⁻³", "0.003"),
    ("3 x 10^-3", "0.003"),
    ("2.5e3", "2500"),
    ("10–20 mg", "10 to 20 mg"),
    ("1\u202f234 samples", "1,234 samples"),
])
def test_vnum_format_equivalence(claim, evidence):
    r = check_numbers(claim, [evidence])
    assert r.ok, (claim, evidence, r.ungrounded)


@pytest.mark.parametrize(("claim", "evidence"), [
    ("21%", "12%"), ("0.03", "0.3"), ("1,234", "1,243"), ("12 mg", "12 g"), ("-0.5", "0.5"),
])
def test_vnum_value_change_fails(claim, evidence):
    r = check_numbers(claim, [evidence])
    assert not r.ok and r.ungrounded


def test_vnum_ignores_markers_citations_and_names():
    assert values("Rates fell [[clm:clm_01ABC]] (Smith 2020).") == []
    assert values("as shown before (Smith et al., 2019; Lee 2021a)") == []
    assert values("CO2 reduction in COVID-19 patients on H2O") == []


def test_vnum_units_and_ranges():
    assert values("10–20 mg") == [(10.0, None), (20.0, "mg")]
    assert values("at 25 °C for 3 h") == [(25.0, "°C"), (3.0, "h")]
    assert values("a 1.5-fold increase") == [(1.5, None)]


def test_vnum_reports_spans():
    text = "Of 1,234 patients, 12% improved."
    nums = extract_numbers(text)
    assert [text[n.span[0]:n.span[1]] for n in nums] == ["1,234", "12%"]
    r = check_numbers("Of 1,234 patients, 21% improved.", ["Of 1,234 patients, 12% improved."])
    assert [n.text for n in r.ungrounded] == ["21%"]


def test_vnum_multiple_evidence_texts():
    assert check_numbers("50 mg daily for 14 days", ["Dose: 50 mg daily.", "Duration was 14 days."]).ok
    assert check_numbers("no numbers here", []).ok


ART = {"display": {"estimate": 0.6213, "ci_low": 0.4791, "ci_high": 0.8034, "k": 7, "i2_percent": 41.3,
                   "ci_level_percent": 95.0},
       "pooled": {"estimate": -0.4759, "tau2": 0.0123, "hksj": True}, "studies": [{"item_id": "itm_A", "yi": 1.25e-3}],
       "measure": "RR"}


def test_vnum_matches_rounded_artifact_value():
    stat = "Pooled RR 0.62 (95% CI 0.48–0.80; 7 studies; I² 41%)."
    assert check_numbers(stat, [], artifact_values=[ART]).ok
    assert not check_numbers(stat, []).ok  # without the artifact nothing grounds them
    assert not check_numbers(stat, ["RR 0.6213"]).ok  # evidence text still needs the value as written
    # rounded as written in the claim: the claim's last digit decides
    for ok in ("0.6", "0.621", "−0.48", "-0.476", "6.2e-1", "0.0123", "1.25 × 10^-3", "41.3%", "0.80"):
        assert check_numbers(f"x {ok} y", [], artifact_values=[ART]).ok, ok
    for bad in ("0.63", "0.622", "0.47", "8 studies", "2", "0.0124", "42%", "0.6213 1.0"):
        r = check_numbers(f"x {bad} y", [], artifact_values=[ART])
        assert not r.ok, bad
    # several artifacts pool their values; a bool is not a number
    assert check_numbers("k 7 and 3", [], artifact_values=[ART, {"n": [3]}]).ok
    assert not check_numbers("x 1", [], artifact_values=[{"hksj": True}]).ok
