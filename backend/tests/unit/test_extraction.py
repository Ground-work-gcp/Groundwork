"""Rule-based extraction, classification, the AI grounding gate, PII and text extraction."""

from __future__ import annotations

from app.services import pii
from app.services.extraction import extract_text, ground
from app.services.extraction.rules import classify_document, extract_facts
from tests.helpers import DOCS, simple_pdf


def _facts(filename: str):
    text = (DOCS / filename).read_text(encoding="utf-8")
    doc_type, _ = classify_document(text)
    return doc_type, extract_facts([(1, text)], doc_type)


def _values(facts, fact_type):
    return sorted(f.normalized for f in facts if f.fact_type == fact_type)


def test_deed_is_classified_and_its_parties_dates_and_parcel_extracted():
    doc_type, facts = _facts("01-warranty-deed-1978.txt")
    assert doc_type == "deed"
    assert _values(facts, "grantor") == ["SAMUEL R WHITFIELD"]
    assert _values(facts, "grantee") == ["ERNEST L PARKER", "LULA M PARKER"]
    assert _values(facts, "recording_date") == ["1978-06-12"]
    assert _values(facts, "parcel_number") == ["140220113"]
    assert _values(facts, "consideration_amount") == ["950000"]
    assert _values(facts, "book_page") == ["412/88"]
    grantee = next(f for f in facts if f.fact_type == "grantee")
    assert grantee.page == 1 and "Grantee:" in grantee.snippet and grantee.confidence >= 0.85


def test_tax_bill_lien_will_and_death_certificate():
    doc_type, facts = _facts("05-tax-bill-2023.txt")
    assert doc_type == "tax_record"
    assert _values(facts, "owner_of_record") == ["PARKER ERNEST L"]
    assert _values(facts, "amount_due") == ["128450"]
    assert _values(facts, "due_date") == ["2024-01-31"]
    assert _values(facts, "bill_number") == ["TX202388412"]
    assert _values(facts, "parcel_number") == ["140220118"]

    doc_type, facts = _facts("07-water-lien-2019.txt")
    assert doc_type == "lien"
    assert _values(facts, "lien_amount") == ["84217"]
    assert _values(facts, "lien_reference") == ["WL201900731"]

    doc_type, facts = _facts("03-will-lula-parker.txt")
    assert doc_type == "will"
    assert _values(facts, "devisee") == ["DENISE PARKER HOLMES", "MARCUS PARKER"]

    doc_type, facts = _facts("02-death-certificate-ernest-parker.txt")
    assert doc_type == "death_certificate"
    assert _values(facts, "decedent") == ["ERNEST L PARKER"]
    assert _values(facts, "date_of_death") == ["2009-03-02"]


def test_grounding_gate_rejects_ungrounded_model_facts():
    pages = {1: "Grantor: Samuel R. Whitfield\nGrantee: Ernest L. Parker"}
    accepted, rejected = ground(
        [
            {"fact_type": "grantor", "value": "Samuel R. Whitfield", "page": 1, "snippet": "Grantor: Samuel R. Whitfield", "confidence": 0.99},
            {"fact_type": "grantee", "value": "Jane Doe", "page": 1, "snippet": "Grantee: Jane Doe", "confidence": 0.9},
            {"fact_type": "grantee", "value": "Ernest L. Parker", "page": 4, "snippet": "Grantee: Ernest L. Parker", "confidence": 0.9},
            {"fact_type": "grantee", "value": "Lula Parker", "page": 1, "snippet": "Grantee: Ernest L. Parker", "confidence": 0.9},
            {"fact_type": "salary", "value": "x", "page": 1, "snippet": "Grantor", "confidence": 0.9},
        ],
        pages,
        "gemini-test",
    )
    assert [f.value for f in accepted] == ["Samuel R. Whitfield"]
    assert accepted[0].confidence <= 0.85 and accepted[0].extractor == "gemini:gemini-test"
    assert [r["reason"] for r in rejected] == [
        "snippet is not on the cited page",
        "cited page 4 does not exist",
        "value is not in the snippet",
        "unknown fact type",
    ]


def test_pii_is_counted_by_page_and_redacted_without_storing_values():
    text = (DOCS / "02-death-certificate-ernest-parker.txt").read_text(encoding="utf-8")
    summary = pii.inspect([(1, text)])
    assert summary["by_type"] == {"US_SOCIAL_SECURITY_NUMBER": 1}
    assert summary["pages"] == {"1": {"US_SOCIAL_SECURITY_NUMBER": 1}}
    assert "123-45-6789" not in str(summary)
    assert "123-45-6789" not in pii.redact(text) and "[REDACTED:US_SOCIAL_SECURITY_NUMBER]" in pii.redact(text)


def test_pdf_pages_keep_their_page_numbers():
    pdf = simple_pdf(["QUITCLAIM DEED", "Grantor: Marcus Parker\nGrantee: Coastal Land Partners LLC"])
    extracted = extract_text(pdf, "application/pdf")
    assert extracted.status == "extracted" and extracted.engine == "local:pypdf"
    assert [n for n, _, _ in extracted.pages] == [1, 2]
    facts = extract_facts([(n, t) for n, t, _ in extracted.pages], "deed")
    grantor = next(f for f in facts if f.fact_type == "grantor")
    assert grantor.page == 2 and grantor.normalized == "MARCUS PARKER"


def test_images_are_marked_needs_ocr_locally_rather_than_guessed():
    assert extract_text(b"\x89PNG....", "image/png").status == "needs_ocr"


def test_prose_grantor_does_not_absorb_the_address_line_above():
    _doc_type, facts = _facts("04-quitclaim-deed-2016.txt")
    assert _values(facts, "grantor") == ["MARCUS PARKER"]
    assert _values(facts, "grantee") == ["COASTAL LAND PARTNERS LLC"]
