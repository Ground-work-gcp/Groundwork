"""Property-case reconciliation: recorded obligations against payments.

Built from the same primitives as Magpie's settlement matcher, and with the same design:

* tiers escalate (T0 exact → T1 tolerance → T2 structural/escalation → T4 exception);
* a match is never a boolean — every result names its rule, its candidates and one
  evidence line per fact;
* three outcomes (``AUTO_MATCHED`` / ``PROPOSED`` / ``EXCEPTION``), and the same
  ``auto_apply`` line decides between the first two.

Lanes:

``OBLIGATION_TO_PAYMENT``
    tax bills and liens against payments (bank lines, receipts, county payment history).
``PAYER_TO_OWNER``
    for every linked payment, does the payer match the person the obligation is billed to?
    A relative paying a decedent's taxes is common in heirs' property and matters legally
    (contribution claims, possession), so it is surfaced, never silently accepted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cmp_to_key
from typing import Any

from app.domain.evidence import names_match

from .engine.candidates import Window, build_index, edit_distance, find_subsets, normalise_reference
from .engine.match import result
from .engine.money import to_usd
from .engine.tolerance import CONFIDENCE, add_days, days_between

LANE = "OBLIGATION_TO_PAYMENT"
PARTY_LANE = "PAYER_TO_OWNER"


@dataclass(frozen=True)
class PropertyTolerances:
    # Payments may precede the due date (prepayment, installments) by up to a year,
    early_days: int = 365
    # and delinquent taxes are routinely paid years late.
    late_days: int = 3 * 365
    rounding_cents: int = 5
    max_edit_distance: int = 2
    min_reference_length: int = 5
    escalation_slack_cents: int = 500
    max_candidates: int = 5
    max_nodes: int = 200_000
    max_installments: int = 6
    auto_apply: float = 0.85

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


PROPERTY_TOLERANCES = PropertyTolerances()


@dataclass(eq=False)
class ObligationRecord:
    id: str
    obligation_type: str
    reference: str
    amount: int
    due_date: str
    debtor: str = ""
    creditor: str = ""
    label: str = ""


@dataclass(eq=False)
class PaymentRecord:
    id: str
    date: str
    reference: str
    description: str
    amount: int
    payer: str = ""


@dataclass
class PropertyRun:
    results: list[dict[str, Any]]
    stats: dict[str, Any] = field(default_factory=dict)


def _ref_of(payment: PaymentRecord) -> str:
    return normalise_reference(payment.reference)


def reconcile_property_case(
    obligations: list[ObligationRecord],
    payments: list[PaymentRecord],
    config: PropertyTolerances = PROPERTY_TOLERANCES,
) -> PropertyRun:
    results: list[dict[str, Any]] = []
    emit = results.append

    def applied(confidence: float) -> str:
        return "AUTO_MATCHED" if confidence >= config.auto_apply else "PROPOSED"

    def money(cents: int) -> str:
        return to_usd(cents)

    payments = [p for p in payments if p.amount > 0]
    index = build_index(
        payments,
        read_id=lambda p: p.id,
        read_date=lambda p: p.date,
        read_amount=lambda p: p.amount,
        read_reference=lambda p: p.reference,
    )
    open_payments = {p.id for p in payments}
    open_obligations = {o.id for o in obligations}
    linked: list[tuple[ObligationRecord, list[PaymentRecord], str]] = []

    def window_for(obligation: ObligationRecord) -> Window:
        return Window(add_days(obligation.due_date, -config.early_days), add_days(obligation.due_date, config.late_days))

    def in_window(payment: PaymentRecord, obligation: ObligationRecord) -> bool:
        w = window_for(obligation)
        return w.start <= payment.date <= w.end

    def referenced(obligation: ObligationRecord) -> list[PaymentRecord]:
        key = normalise_reference(obligation.reference)
        if len(key) < 3:
            return []
        by_column = index.by_reference.get(key, [])
        in_text = [p for p in payments if p not in by_column and key in normalise_reference(p.description)]
        return [p for p in [*by_column, *in_text] if p.id in open_payments and in_window(p, obligation)]

    def by_date(a: PaymentRecord, b: PaymentRecord) -> int:
        if a.date == b.date:
            return -1 if a.id < b.id else 1
        return -1 if a.date < b.date else 1

    def close(obligation: ObligationRecord | None, paid: list[PaymentRecord]) -> None:
        if obligation is not None:
            open_obligations.discard(obligation.id)
        for p in paid:
            open_payments.discard(p.id)

    ordered = sorted(obligations, key=lambda o: (o.due_date, o.id))

    # ── T0 / T1: the payment names the obligation ────────────────────────────
    for obligation in ordered:
        candidates = sorted(referenced(obligation), key=cmp_to_key(by_date))
        if not candidates:
            continue
        exact = [p for p in candidates if p.amount == obligation.amount]
        rounding = [p for p in candidates if 0 < abs(p.amount - obligation.amount) <= config.rounding_cents]
        chosen: list[PaymentRecord] | None = None
        if len(exact) == 1:
            payment = exact[0]
            late = days_between(obligation.due_date, payment.date)
            on_time = late <= 0
            rule, confidence, failure = (
                ("T0_REFERENCE_AND_AMOUNT", CONFIDENCE.REFERENCE_EXACT, None)
                if on_time
                else ("T1_PAID_LATE", CONFIDENCE.LATE_CREDIT, "PAID_LATE")
            )
            evidence = [
                f"payment reference {payment.reference or '(in description)'} names {obligation.label or obligation.reference}",
                f"amount is the obligation amount {money(obligation.amount)} to the cent",
                f"paid {payment.date} against a due date of {obligation.due_date}"
                + ("" if on_time else f" — {late} days late; penalties or interest may have accrued"),
            ]
            chosen = [payment]
            tier = "T0" if on_time else "T1"
        elif not exact and len(rounding) == 1:
            payment = rounding[0]
            rule, confidence, failure, tier = "T1_ROUNDING", CONFIDENCE.ROUNDING, "ROUNDING", "T1"
            evidence = [
                f"payment reference {payment.reference} names {obligation.label or obligation.reference}",
                f"amount {money(payment.amount)} is within {config.rounding_cents} cents of {money(obligation.amount)}",
            ]
            chosen = [payment]
        else:
            # Several referenced payments: installments that sum exactly, or a shortfall.
            search = find_subsets(
                candidates,
                lambda p: p.amount,
                obligation.amount,
                max_size=config.max_installments,
                max_nodes=config.max_nodes,
            )
            viable = [s for s in search.subsets if len(s) > 1]
            if len(viable) == 1:
                chosen = sorted(viable[0], key=cmp_to_key(by_date))
                rule, confidence, failure, tier = "T2_INSTALLMENTS", CONFIDENCE.SPLIT_CREDIT, "INSTALLMENTS", "T2"
                evidence = [
                    f"{len(chosen)} payments referencing {obligation.reference} sum to {money(obligation.amount)} exactly: "
                    + " + ".join(f"{p.date} {money(p.amount)}" for p in chosen),
                ]
            else:
                paid = sum(p.amount for p in candidates)
                short = obligation.amount - paid
                close(None, candidates)
                if short > 0:
                    emit(
                        result(
                            lane=LANE, tier="T2", rule="T2_UNDERPAID", outcome="EXCEPTION", confidence=0,
                            left=[obligation.id], right=[p.id for p in candidates],
                            inputs=[obligation.id, *[p.id for p in candidates]],
                            evidence=[
                                f"{len(candidates)} payment(s) reference {obligation.reference} and total {money(paid)}",
                                f"the obligation is {money(obligation.amount)}, so {money(short)} remains outstanding",
                                "no combination of the referenced payments ties to the full amount",
                            ],
                            failure="UNDERPAID", amount=short,
                        )
                    )
                    linked.append((obligation, candidates, "partial"))
                    open_obligations.discard(obligation.id)
                else:
                    emit(
                        result(
                            lane=LANE, tier="T2", rule="T2_AMOUNT_MISMATCH", outcome="EXCEPTION", confidence=0,
                            left=[obligation.id], right=[p.id for p in candidates],
                            inputs=[obligation.id, *[p.id for p in candidates]],
                            evidence=[
                                f"{len(candidates)} payment(s) reference {obligation.reference} and total {money(paid)}",
                                f"against an obligation of {money(obligation.amount)} — over by {money(-short)}",
                                "the records disagree about the amount; one of them is wrong" if len(viable) == 0
                                else f"{len(viable)} different combinations tie — a human has to choose",
                            ],
                            failure="AMOUNT_MISMATCH", amount=-short,
                        )
                    )
                    open_obligations.discard(obligation.id)
                continue

        close(obligation, chosen)
        linked.append((obligation, chosen, "paid"))
        emit(
            result(
                lane=LANE, tier=tier, rule=rule, outcome=applied(confidence), confidence=confidence,
                left=[obligation.id], right=[p.id for p in chosen], inputs=[obligation.id, *[p.id for p in candidates]],
                evidence=evidence, failure=failure, amount=obligation.amount,
            )
        )

    # ── T1: a reference one typo away, gated on an exact amount first ───────
    for payment in sorted(payments, key=cmp_to_key(by_date)):
        if payment.id not in open_payments:
            continue
        ref = _ref_of(payment)
        if len(ref) < config.min_reference_length:
            continue
        near = []
        for obligation in ordered:
            if obligation.id not in open_obligations or obligation.amount != payment.amount or not in_window(payment, obligation):
                continue
            distance = edit_distance(ref, normalise_reference(obligation.reference), config.max_edit_distance)
            if distance is not None:
                near.append((obligation, distance))
        if not near:
            continue
        near.sort(key=lambda row: row[1])
        best, distance = near[0]
        ambiguous = len(near) > 1 and near[1][1] == distance
        confidence = CONFIDENCE.AMBIGUOUS if ambiguous else CONFIDENCE.REFERENCE_NEAR_MISS
        if not ambiguous:
            close(best, [payment])
            linked.append((best, [payment], "paid"))
        emit(
            result(
                lane=LANE, tier="T1", rule="T1_REFERENCE_NEAR_MISS", outcome="PROPOSED" if ambiguous else applied(confidence),
                confidence=confidence, left=[best.id], right=[payment.id], inputs=[payment.id, *[o.id for o, _ in near]],
                evidence=[
                    f"payment reference {payment.reference} is {distance} edit(s) from {best.reference}",
                    f"amount {money(payment.amount)} ties exactly and {payment.date} is inside the payment window",
                    f"{len(near)} obligations are equally close — nothing separates them" if ambiguous
                    else f"no other open obligation is within {config.max_edit_distance} edits",
                ],
                failure=None if ambiguous else "TYPO_REFERENCE", amount=payment.amount,
            )
        )

    # ── T1: no usable reference; a unique amount and window carry it ─────────
    for payment in sorted(payments, key=cmp_to_key(by_date)):
        if payment.id not in open_payments or len(_ref_of(payment)) >= config.min_reference_length:
            continue
        fits = [o for o in ordered if o.id in open_obligations and o.amount == payment.amount and in_window(payment, o)]
        if not fits:
            continue
        unique = len(fits) == 1
        best = fits[0]
        confidence = CONFIDENCE.AMOUNT_AND_DATE if unique else CONFIDENCE.AMBIGUOUS
        if unique:
            close(best, [payment])
            linked.append((best, [payment], "paid"))
        emit(
            result(
                lane=LANE, tier="T1", rule="T1_AMOUNT_AND_DATE", outcome=applied(confidence) if unique else "PROPOSED",
                confidence=confidence, left=[best.id], right=[payment.id], inputs=[payment.id, *[o.id for o in fits]],
                evidence=[
                    f'the payment carries no reference: "{payment.description}"',
                    f"{money(payment.amount)} on {payment.date} equals {best.label or best.reference} exactly",
                    "it is the only open obligation of this amount in the window" if unique
                    else f"{len(fits)} open obligations have this amount — a reference is needed to choose",
                ],
                failure="MISSING_REFERENCE" if unique else None, amount=payment.amount,
            )
        )

    # ── T2: rank candidates and decline (escalation packet) ─────────────────
    escalated_obligations: set[str] = set()
    for payment in sorted(payments, key=cmp_to_key(by_date)):
        if payment.id not in open_payments:
            continue
        ranked = sorted(
            (
                (o, payment.amount - o.amount)
                for o in ordered
                if o.id in open_obligations
                and abs(payment.amount - o.amount) <= config.escalation_slack_cents
                and in_window(payment, o)
            ),
            key=lambda row: (abs(row[1]), row[0].id),
        )[: config.max_candidates]
        if not ranked:
            continue
        open_payments.discard(payment.id)
        escalated_obligations.add(ranked[0][0].id)
        emit(
            result(
                lane=LANE, tier="T2", rule="T2_ESCALATION_CANDIDATES", outcome="PROPOSED", confidence=CONFIDENCE.ESCALATED,
                left=[ranked[0][0].id], right=[payment.id], inputs=[payment.id, *[o.id for o, _ in ranked]],
                evidence=[
                    f'{money(payment.amount)} paid {payment.date} with no usable reference: "{payment.description}"',
                    f"{len(ranked)} open obligation(s) are within {money(config.escalation_slack_cents)}:",
                    *[
                        f"  {o.label or o.reference} — {money(o.amount)} ({'exact' if gap == 0 else ('over' if gap > 0 else 'short') + ' by ' + money(abs(gap))}), due {o.due_date}"
                        for o, gap in ranked
                    ],
                    "an amount that is close but not exact, with nothing naming the obligation, is left for a person to judge",
                ],
                failure=None, amount=payment.amount,
            )
        )

    # ── T4: everything left, with a reason ───────────────────────────────────
    for obligation in ordered:
        if obligation.id not in open_obligations or obligation.id in escalated_obligations:
            continue
        emit(
            result(
                lane=LANE, tier="T4", rule="T4_UNPAID_OBLIGATION", outcome="EXCEPTION", confidence=0,
                left=[obligation.id], right=[], inputs=[obligation.id],
                evidence=[
                    f"{obligation.label or obligation.reference}: {money(obligation.amount)} due {obligation.due_date} owed to {obligation.creditor or 'the creditor'}",
                    "no payment ties on reference, amount, installments or a near-miss reference",
                ],
                failure="UNPAID", amount=obligation.amount,
            )
        )
    for payment in payments:
        if payment.id not in open_payments:
            continue
        emit(
            result(
                lane=LANE, tier="T4", rule="T4_UNEXPLAINED_PAYMENT", outcome="EXCEPTION", confidence=0,
                left=[], right=[payment.id], inputs=[payment.id],
                evidence=[
                    f'{money(payment.amount)} paid {payment.date}: "{payment.description}"',
                    "no recorded obligation explains it",
                ],
                failure="UNMATCHED_PAYMENT", amount=payment.amount,
            )
        )

    # ── PAYER_TO_OWNER: who actually paid ────────────────────────────────────
    for obligation, paid, _state in linked:
        for payment in paid:
            if not payment.payer or not obligation.debtor or names_match(payment.payer, obligation.debtor):
                continue
            emit(
                result(
                    lane=PARTY_LANE, tier="T1", rule="T1_PAYER_NOT_BILLED_PARTY", outcome="EXCEPTION", confidence=0.9,
                    left=[obligation.id], right=[payment.id], inputs=[obligation.id, payment.id],
                    evidence=[
                        f"{money(payment.amount)} on {payment.date} was paid by {payment.payer}",
                        f"{obligation.label or obligation.reference} is billed to {obligation.debtor}",
                        "a non-owner paying the obligation can affect contribution and possession claims — needs professional review",
                    ],
                    failure="THIRD_PARTY_PAYER", amount=payment.amount,
                )
            )

    stats: dict[str, Any] = {"byOutcome": {"AUTO_MATCHED": 0, "PROPOSED": 0, "EXCEPTION": 0}, "byRule": {}}
    for item in results:
        stats["byOutcome"][item["outcome"]] += 1
        stats["byRule"][item["rule"]] = stats["byRule"].get(item["rule"], 0) + 1
    stats["obligations"] = len(obligations)
    stats["payments"] = len(payments)
    stats["linkStates"] = {o.id: state for o, _, state in linked}
    return PropertyRun(results, stats)
