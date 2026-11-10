"""The property-case reconciliation lane: tiers, three outcomes, evidence, and the party lane."""

from __future__ import annotations

from app.domain.reconciliation.property_lane import ObligationRecord, PaymentRecord, reconcile_property_case


def _ob(id_, ref, amount, due, debtor="Ernest L. Parker", kind="property_tax"):
    return ObligationRecord(id=id_, obligation_type=kind, reference=ref, amount=amount, due_date=due, debtor=debtor, creditor="County")


def _pay(id_, date, amount, ref="", desc="payment", payer=""):
    return PaymentRecord(id=id_, date=date, reference=ref, description=desc, amount=amount, payer=payer)


def _rules(run):
    return {(r["rule"], r["outcome"], tuple(r["left"]), tuple(r["right"])) for r in run.results}


def test_reference_and_amount_exact_is_auto_matched_with_evidence():
    run = reconcile_property_case([_ob("o1", "TX-2023-1", 128450, "2024-01-31")], [_pay("p1", "2024-01-20", 128450, "TX20231")])
    [result] = [r for r in run.results if r["lane"] == "OBLIGATION_TO_PAYMENT"]
    assert result["rule"] == "T0_REFERENCE_AND_AMOUNT" and result["outcome"] == "AUTO_MATCHED"
    assert result["confidence"] == 1 and len(result["evidence"]) == 3


def test_late_payment_still_matches_but_is_labelled():
    run = reconcile_property_case([_ob("o1", "TX-1", 1000, "2020-01-31")], [_pay("p1", "2021-06-01", 1000, "TX-1")])
    assert ("T1_PAID_LATE", "AUTO_MATCHED", ("o1",), ("p1",)) in _rules(run)


def test_installments_that_sum_exactly_are_one_match():
    run = reconcile_property_case(
        [_ob("o1", "TX-24", 131620, "2025-01-31")],
        [_pay("p1", "2025-01-10", 60000, "TX-24"), _pay("p2", "2025-01-20", 71620, "TX-24")],
    )
    result = next(r for r in run.results if r["rule"] == "T2_INSTALLMENTS")
    assert result["outcome"] == "AUTO_MATCHED" and result["right"] == ["p1", "p2"]


def test_shortfall_is_an_exception_carrying_the_outstanding_amount():
    run = reconcile_property_case(
        [_ob("o1", "TX-24", 131620, "2025-01-31")],
        [_pay("p1", "2025-03-15", 60000, "TX-24"), _pay("p2", "2025-05-02", 50000, "TX-24")],
    )
    result = next(r for r in run.results if r["rule"] == "T2_UNDERPAID")
    assert result["outcome"] == "EXCEPTION" and result["amount"] == 21620 and result["class"] == "UNDERPAID"


def test_typo_reference_needs_an_exact_amount_first():
    obligations = [_ob("o1", "TX-2023-88412", 5000, "2024-01-31"), _ob("o2", "TX-2023-88999", 7000, "2024-01-31")]
    run = reconcile_property_case(obligations, [_pay("p1", "2024-01-10", 5000, "TX-2023-84812")])
    assert ("T1_REFERENCE_NEAR_MISS", "AUTO_MATCHED", ("o1",), ("p1",)) in _rules(run)
    # The same typo against a different amount must not match anything.
    run = reconcile_property_case(obligations, [_pay("p1", "2024-01-10", 5001, "TX-2023-84812")])
    assert not any(r["outcome"] == "AUTO_MATCHED" for r in run.results)


def test_two_equal_candidates_without_a_reference_are_proposed_not_applied():
    obligations = [_ob("o1", "A-1", 5000, "2024-01-31"), _ob("o2", "B-1", 5000, "2024-02-28")]
    run = reconcile_property_case(obligations, [_pay("p1", "2024-01-10", 5000)])
    result = next(r for r in run.results if r["rule"] == "T1_AMOUNT_AND_DATE")
    assert result["outcome"] == "PROPOSED"


def test_close_amount_with_nothing_naming_it_is_escalated_for_judgement():
    run = reconcile_property_case([_ob("o1", "WL-1", 84217, "2019-04-15", kind="lien")], [_pay("p1", "2021-07-08", 84271)])
    result = next(r for r in run.results if r["rule"] == "T2_ESCALATION_CANDIDATES")
    assert result["outcome"] == "PROPOSED" and result["confidence"] < 0.85
    assert not any(r["rule"] == "T4_UNPAID_OBLIGATION" for r in run.results)


def test_leftovers_become_exceptions_with_reasons():
    run = reconcile_property_case([_ob("o1", "WL-1", 84217, "2019-04-15")], [_pay("p1", "2023-11-03", 12000, desc="HARDWARE STORE")])
    rules = {r["rule"] for r in run.results}
    assert {"T4_UNPAID_OBLIGATION", "T4_UNEXPLAINED_PAYMENT"} <= rules


def test_payer_who_is_not_the_billed_owner_is_surfaced():
    run = reconcile_property_case(
        [_ob("o1", "TX-1", 1000, "2024-01-31", debtor="PARKER ERNEST L ETAL")],
        [_pay("p1", "2024-01-10", 1000, "TX-1", payer="Denise Parker Holmes")],
    )
    party = [r for r in run.results if r["lane"] == "PAYER_TO_OWNER"]
    assert party and party[0]["class"] == "THIRD_PARTY_PAYER" and party[0]["outcome"] == "EXCEPTION"
    # The same owner under the tax roll's LAST FIRST ordering is not a third party.
    run = reconcile_property_case(
        [_ob("o1", "TX-1", 1000, "2024-01-31", debtor="PARKER ERNEST L ETAL")],
        [_pay("p1", "2024-01-10", 1000, "TX-1", payer="Ernest L. Parker")],
    )
    assert not [r for r in run.results if r["lane"] == "PAYER_TO_OWNER"]
