"""The rural land engines as pure functions: names, units, succession, valuation."""

from __future__ import annotations

from datetime import date
from fractions import Fraction

import pytest

from app.domain.land.names import same_person, similarity, transliterate
from app.domain.land.regions import region
from app.domain.land.succession import Person, compute
from app.domain.land.units import land_class, parse_extent, survey_key, surveys_in
from app.domain.land.valuation import estimate, inr

TN, KA, MH, UP = region("TN"), region("KA"), region("MH"), region("UP")


# ── names across scripts ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "a,b",
    [
        ("முத்துசாமி", "Muthusamy"),
        ("Muthuswamy K.", "K. Muthuchami s/o Ramasamy"),
        ("லட்சுமி அம்மாள்", "Lakshmi Ammal"),
        ("Laxmi", "Lakshmi"),
        ("அழகு", "Alagu"),
        ("கோபால்", "Gopal"),
        ("रामचन्द्र", "Ramachandra"),
        ("Late Thiru. Ramasamy", "Ramasamy"),
    ],
)
def test_the_same_person_across_scripts_and_spellings(a, b):
    assert same_person(a, b), similarity(a, b)


@pytest.mark.parametrize("a,b", [("Ramasamy", "Kannan"), ("Muthusamy", "Muthulakshmi"), ("Selvi", "Selvam")])
def test_different_people_stay_different(a, b):
    assert not same_person(a, b), similarity(a, b)


def test_one_offset_table_reads_every_indic_script():
    assert transliterate("ರಾಮಪ್ಪ") == "raamappa"  # Kannada keeps the final vowel
    assert transliterate("लक्ष्मी") == "lakshmii"  # Devanagari conjuncts through the virama
    assert transliterate("அளகு").startswith("al")  # Tamil


# ── extents, survey numbers, land classes ────────────────────────────────────


def test_extents_in_regional_units():
    acre = 4046.8564224
    assert parse_extent("1 acre 20 cents", TN) == pytest.approx(1.2 * acre, rel=1e-4)
    assert parse_extent("0.48.56 hectare", TN) == 4856  # hectare . are . centiare
    assert parse_extent("2-15", KA) == pytest.approx(2 * acre + 15 * 101.17141056, rel=1e-6)  # acres-guntas
    assert parse_extent("1-20 H.R", MH) == 12_000  # hectare-are
    assert parse_extent("3 bigha 5 biswa", UP) == pytest.approx(3.25 * 2529.29, rel=1e-6)
    assert parse_extent("2400 sq ft") == pytest.approx(222.97, rel=1e-3)
    assert parse_extent("a lot", TN) is None and parse_extent("2-15") is None  # ambiguous without a region


def test_survey_keys_and_mentions():
    assert survey_key("S.F. No. 123 / 4-A") == survey_key("Sy. No 123/4A") == "123/4A"
    assert survey_key("R.S. No. 045/02") == "45/2"
    assert surveys_in("North: S.F. No. 122; East: Survey No 124/1B") == {"122", "124/1B"}


def test_land_classes_use_local_terms():
    assert land_class("Nanjai (wet)", TN) == "wet"
    assert land_class("Punjai", TN) == "dry"
    assert land_class("Poramboke — water course", TN) == "government"
    assert land_class("Gairan", MH) == "government"


# ── succession ───────────────────────────────────────────────────────────────


def family(**overrides):
    P = Person
    people = {
        "r": P("r", "Ramasamy", "male", False, "1987-03-02", [], ["l"]),
        "l": P("l", "Lakshmi", "female", False, "2015-06-10", [], ["r"]),
        "m": P("m", "Muthusamy", "male", False, "2019-01-20", ["r", "l"], ["v"]),
        "v": P("v", "Valli", "female", True, None, [], ["m"]),
        "k": P("k", "Kannan", "male", True, None, ["r", "l"], []),
        "s": P("s", "Selvi", "female", True, None, ["r", "l"], []),
        "a": P("a", "Arun", "male", True, None, ["m", "v"], []),
        "p": P("p", "Priya", "female", True, None, ["m", "v"], []),
    }
    for key, person in overrides.items():
        if person is None:
            people.pop(key)
        else:
            people[key] = person
    return list(people.values())


def shares(result):
    return {s["name"]: Fraction(s["share"]) for s in result["shares"]}


def test_hindu_devolution_through_three_deaths_sums_to_one():
    r = compute(family(), "r")
    assert r["status"] == "computed"
    assert shares(r) == {"Kannan": Fraction(1, 3), "Selvi": Fraction(1, 3), "Arun": Fraction(1, 9), "Priya": Fraction(1, 9), "Valli": Fraction(1, 9)}
    assert sum(shares(r).values()) == 1 and [s["person"] for s in r["steps"]] == ["Ramasamy", "Lakshmi", "Muthusamy"]


def test_predeceased_son_branch_takes_one_share_with_his_widow():
    # Muthusamy dies before his father: his branch (widow + 2 children) takes one Class I share.
    r = compute(family(m=Person("m", "Muthusamy", "male", False, "1980-01-01", ["r", "l"], ["v"])), "r")
    got = shares(r)
    # 1987: widow, Kannan, Selvi, Muthusamy's branch → 1/4 each; branch split 3 ways. 2015: Lakshmi's 1/4 to her children's branches.
    assert got["Kannan"] == Fraction(1, 4) + Fraction(1, 12)
    assert got["Valli"] == Fraction(1, 12)  # widow of a predeceased son is Class I under s.8, but not an heir of her mother-in-law under s.15
    assert got["Arun"] == Fraction(1, 12) + Fraction(1, 24)
    assert sum(got.values()) == 1


def test_section_15_2_returns_a_childless_widows_inheritance_to_her_husbands_heirs():
    people = [
        Person("h", "Husband", "male", False, "2000-01-01", ["f"], ["w"]),
        Person("w", "Widow", "female", False, "2010-01-01", ["wf"], ["h"]),
        Person("f", "Father", "male", False, "1990-01-01", [], []),
        Person("b", "Brother", "male", True, None, ["f"], []),
        Person("wf", "WidowsFather", "male", True, None, [], []),
    ]
    r = compute(people, "h")
    # 2000: the widow is the only Class I heir. 2010: no children, land came from her husband → his heirs (his brother).
    assert shares(r) == {"Brother": Fraction(1)}
    assert "15(2)(b)" in r["steps"][-1]["rule"]


def test_ancestral_land_after_2005_makes_daughters_coparceners():
    people = [
        Person("f", "Father", "male", False, "2010-05-05", [], ["w"]),
        Person("w", "Wife", "female", True, None, [], ["f"]),
        Person("s", "Son", "male", True, None, ["f", "w"], []),
        Person("d", "Daughter", "female", True, None, ["f", "w"], []),
    ]
    r = compute(people, "f", nature="ancestral")
    # coparceners: father, son, daughter → 1/3 each; father's 1/3 to wife, son, daughter → 1/9 each.
    assert shares(r) == {"Son": Fraction(4, 9), "Daughter": Fraction(4, 9), "Wife": Fraction(1, 9)}


def test_christian_spouse_takes_a_third():
    people = [
        Person("h", "Joseph", "male", False, "2001-01-01", [], ["w"]),
        Person("w", "Mary", "female", True, None, [], ["h"]),
        Person("c1", "Thomas", "male", True, None, ["h", "w"], []),
        Person("c2", "Anna", "female", True, None, ["h", "w"], []),
    ]
    r = compute(people, "h", law="christian")
    assert shares(r) == {"Mary": Fraction(1, 3), "Thomas": Fraction(1, 3), "Anna": Fraction(1, 3)}


def test_muslim_law_is_declined_not_guessed():
    r = compute(family(), "r", law="muslim")
    assert r["status"] == "not_computed" and r["shares"] == [] and "qazi" in r["unresolved"][0]["reason"]


def test_missing_death_date_is_reported_not_assumed_away():
    r = compute(family(m=Person("m", "Muthusamy", "male", False, None, ["r", "l"], ["v"])), "r")
    assert r["status"] == "partial"
    assert any(u["person"] == "Muthusamy" and "date of death unknown" in u["reason"] for u in r["unresolved"])
    assert any("without a date of death" in w for w in r["warnings"])
    assert sum(shares(r).values()) == 1  # the unresolved share stays visible on the dead holder


# ── valuation ────────────────────────────────────────────────────────────────

SUBJECT = {"extent_sqm": 4856.0, "land_class": "wet", "road_access": False, "guideline_inr_per_sqm": 150}
TODAY = date(2026, 11, 11)


def comp(i, per_sqm, *, sale_date="2025-06-01", extent=4000.0, cls="wet", dist=1.0):
    return {"id": str(i), "sale_date": sale_date, "extent_sqm": extent, "consideration_inr": int(per_sqm * extent), "land_class": cls, "distance_km": dist}


def test_no_comparables_falls_back_to_the_guideline_value_with_a_wide_low_confidence_range():
    r = estimate(SUBJECT, [], today=TODAY)
    assert r["status"] == "estimated" and r["confidence"] == "low" and r["market_weight"] == 0
    assert r["p50_inr"] == pytest.approx(150 * 4856, rel=0.01) and r["p90_inr"] / r["p10_inr"] > 2.5


def test_more_comparables_move_the_estimate_toward_the_market_and_narrow_it():
    few = estimate(SUBJECT, [comp(1, 260), comp(2, 240)], today=TODAY)
    many = estimate(SUBJECT, [comp(i, 250 + (i % 3) * 5) for i in range(12)], today=TODAY)
    assert few["market_weight"] < many["market_weight"]
    assert few["range_ratio"] > many["range_ratio"]
    assert many["confidence"] in ("medium", "high") and many["per_sqm_p50"] > few["per_sqm_p50"]


def test_a_high_outlier_is_discounted_harder_than_a_low_one():
    base = [comp(i, 250) for i in range(5)]
    high = estimate(SUBJECT, [*base, comp(9, 2500)], today=TODAY)
    low = estimate(SUBJECT, [*base, comp(9, 25)], today=TODAY)
    plain = estimate(SUBJECT, base, today=TODAY)
    assert high["p50_inr"] / plain["p50_inr"] < plain["p50_inr"] / low["p50_inr"]


def test_deeds_at_the_guideline_value_count_as_floors():
    r = estimate(SUBJECT, [comp(1, 150), comp(2, 300)], today=TODAY)
    flags = {c["id"]: c["censored_at_guideline"] for c in r["comparables"]}
    assert flags == {"1": True, "2": False}


def test_lending_value_sits_below_the_median_and_falls_with_title_problems():
    clean = estimate(SUBJECT, [comp(i, 250) for i in range(4)], today=TODAY)
    messy = estimate(SUBJECT, [comp(i, 250) for i in range(4)], issues={"deceased_holder": 1, "boundaries": 2, "encumbrance": 1, "extents": 1}, consents_missing=3, today=TODAY)
    assert clean["lending_value_inr"] < clean["p50_inr"] and clean["title_haircut"] == 0
    assert messy["title_haircut"] == 0.40 and messy["lending_value_inr"] < clean["lending_value_inr"]
    assert messy["loan_ceiling_inr"] == pytest.approx(messy["lending_value_inr"] * 0.6, abs=100)


def test_missing_extent_is_refused():
    assert estimate({**SUBJECT, "extent_sqm": None}, [], today=TODAY)["status"] == "insufficient"


def test_indian_digit_grouping():
    assert inr(1703100) == "₹17,03,100" and inr(100000) == "₹1,00,000" and inr(999) == "₹999"
