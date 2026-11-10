"""Normalisation that every cross-document comparison depends on."""

from __future__ import annotations

from app.domain.evidence import names_match, parse_date_text, parse_money_text, split_names
from app.domain.reconciliation.engine.parse import parse_date, parse_minor_units


def test_names_match_across_record_formats():
    assert names_match("PARKER ERNEST L ETAL", "Ernest L. Parker")
    assert names_match("Ernest Parker", "Ernest L. Parker")
    assert names_match("Coastal Land Partners, LLC", "COASTAL LAND PARTNERS LLC")
    assert not names_match("Lula M. Parker", "Ernest L. Parker")
    assert not names_match("Parker", "Ernest L. Parker")
    assert not names_match("Marcus Parker", "Denise Parker Holmes")


def test_split_names_drops_tenancy_descriptors():
    assert split_names("Ernest L. Parker and Lula M. Parker, husband and wife, as joint tenants with right of survivorship") == [
        "Ernest L. Parker",
        "Lula M. Parker",
    ]
    assert split_names("Smith and Sons Holdings LLC") == ["Smith and Sons Holdings LLC"]
    assert split_names("Denise Parker Holmes; Marcus Parker") == ["Denise Parker Holmes", "Marcus Parker"]


def test_dates_are_month_first_for_us_records_and_reject_impossible_days():
    assert parse_date_text("Recorded 06/12/1978") == "1978-06-12"
    assert parse_date_text("March 2, 2009") == "2009-03-02"
    assert parse_date_text("2nd day of March, 2009") == "2009-03-02"
    assert parse_date_text("02/30/2009") is None
    assert parse_date("31/02/2026", "DMY").ok is False


def test_money_is_integer_minor_units_never_float():
    assert parse_money_text("$1,316.20") == 131620
    assert parse_minor_units("11,57,147.71").value == 115714771
    assert parse_minor_units("(842.17)").value == -84217
    assert parse_minor_units("12.345").ok is False
