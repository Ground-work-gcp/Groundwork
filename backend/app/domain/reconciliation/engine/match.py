"""The deterministic matcher (ported line-for-line from Magpie ``lib/recon/match.ts``).

No model is involved anywhere in this file, and that is the design (§1.1). Three ideas
hold it together:

* **Tiers escalate**, and each tier only sees what the last could not resolve (§A1).
* **A match is never a boolean** (§1.2): every result carries its rule, what it compared,
  what it rejected and one evidence line per fact, written for a controller.
* **Three outcomes, never two** (§1.3): ``AUTO_MATCHED``, ``PROPOSED``, ``EXCEPTION``.
  Abstention is a success state; precision on the auto lane is the number to protect.

Results are plain dicts with the TypeScript's camelCase keys so they serialise
identically, which is what ``tests/unit/test_recon_engine_golden.py`` checks against
output captured from the original implementation.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cmp_to_key
from typing import Any

from .candidates import (
    Window,
    edit_distance,
    find_subsets,
    group_journals,
    group_payments_by_payout_date,
    index_bank,
    index_settlements,
    normalise_reference,
    partition_is_tractable,
    window_around,
)
from .money import indian_int, pct, to_indian_decimal
from .parse import js_trim
from .records import BankCredit, Chargeback, LedgerEntry, Payment, ReconRow, Settlement
from .tolerance import CONFIDENCE, TOLERANCES, Tolerances, add_days, days_between, expected_settlement_date

MatchResult = dict[str, Any]
TIERS = ("T0", "T1", "T2", "T3", "T4")
OUTCOMES = ("AUTO_MATCHED", "PROPOSED", "EXCEPTION")
LANES = ("PAYMENT_TO_SETTLEMENT", "SETTLEMENT_TO_BANK", "SETTLEMENT_TO_LEDGER")
INPUT_CAP = 25


@dataclass
class MatchInput:
    payments: list[Payment] = field(default_factory=list)
    settlements: list[Settlement] = field(default_factory=list)
    recon: list[ReconRow] = field(default_factory=list)
    bank: list[BankCredit] = field(default_factory=list)
    ledger: list[LedgerEntry] = field(default_factory=list)
    chargebacks: list[Chargeback] = field(default_factory=list)


@dataclass
class MatchRun:
    results: list[MatchResult]
    stats: dict[str, Any]


def money(paise: int) -> str:
    return f"₹{to_indian_decimal(paise)}"


def delta(actual: int, expected: int) -> str:
    """Evidence reads better with a signed delta than with two amounts and a subtraction."""
    return f"{'over' if actual >= expected else 'short'} by {money(abs(actual - expected))}"


def capped_inputs(ids: list[str]) -> list[str]:
    """Cap the audit trail (``inputs``) and nothing else: the link itself is the answer."""
    return ids if len(ids) <= INPUT_CAP else ids[:INPUT_CAP]


def by_id(a: Any, b: Any) -> int:
    return -1 if a.id < b.id else 1


def stable_credits(credits: list[BankCredit]) -> list[BankCredit]:
    def cmp(a: BankCredit, b: BankCredit) -> int:
        if a.value_date == b.value_date:
            return -1 if a.id < b.id else 1
        return -1 if a.value_date < b.value_date else 1

    return sorted(credits, key=cmp_to_key(cmp))


def result(
    *,
    lane: str,
    tier: str,
    rule: str,
    outcome: str,
    confidence: float,
    left: list[str],
    right: list[str],
    inputs: list[str],
    evidence: list[str],
    failure: str | None,
    amount: int,
    cap_hit: bool = False,
) -> MatchResult:
    out: MatchResult = {
        "lane": lane,
        "tier": tier,
        "rule": rule,
        "outcome": outcome,
        "confidence": confidence,
        "left": left,
        "right": right,
        "inputs": inputs,
        "evidence": evidence,
        "class": failure,
        "amount": amount,
    }
    if cap_hit:
        out["capHit"] = True
    return out


def run_match(inp: MatchInput, config: Tolerances = TOLERANCES) -> MatchRun:
    started = time.perf_counter()
    results: list[MatchResult] = []
    counters = {"nodes": 0}

    def emit(item: MatchResult) -> MatchResult:
        results.append(item)
        return item

    def applied(confidence: float) -> str:
        """One place decides AUTO_MATCHED vs PROPOSED, from the confidence and the config."""
        return "AUTO_MATCHED" if confidence >= config.auto_apply else "PROPOSED"

    _match_bank_lane(inp, config, emit, applied, counters)
    _match_ledger_lane(inp, config, emit, applied)
    _match_payment_lane(inp, config, emit, applied, counters)
    elapsed_ms = (time.perf_counter() - started) * 1000
    return MatchRun(results, summarise(results, counters, inp, elapsed_ms))


# ── Lane 1 of 3: settlements against the bank statement ──────────────────────


def _match_bank_lane(
    inp: MatchInput,
    config: Tolerances,
    emit: Callable[[MatchResult], MatchResult],
    applied: Callable[[float], str],
    counters: dict[str, int],
) -> None:
    lane = "SETTLEMENT_TO_BANK"
    settlement_index = index_settlements(inp.settlements)
    bank_index = index_bank(inp.bank)

    open_settlements = {s.id for s in inp.settlements}
    open_credits = {c.id for c in inp.bank}
    escalated_credits: set[str] = set()
    escalated_settlements: set[str] = set()

    def settle(settlements: list[Settlement], credits: list[BankCredit]) -> None:
        for s in settlements:
            open_settlements.discard(s.id)
        for c in credits:
            open_credits.discard(c.id)

    def is_open_credit(credit: BankCredit) -> bool:
        return credit.id in open_credits

    def is_open_settlement(settlement: Settlement) -> bool:
        return settlement.id in open_settlements

    def report_gap(s: Settlement) -> int:
        return s.gross - s.fees - s.tax - s.refunds - s.chargebacks - s.tds - s.net

    def identity_evidence(s: Settlement) -> tuple[list[str], str | None]:
        gap = report_gap(s)
        if gap == 0:
            return [], None
        return (
            [
                f"the report does not explain its own net: gross less fees, tax and itemised deductions leaves {money(gap)} unaccounted for — a refund or dispute was netted without being itemised"
            ],
            "REFUND_NETTED",
        )

    # ── T0, step 0: the same credit twice ────────────────────────────────────
    duplicate_groups: dict[str, list[BankCredit]] = {}
    for credit in inp.bank:
        key = "|".join(
            [
                normalise_reference(credit.reference),
                str(credit.amount),
                credit.value_date,
                js_trim(credit.description).upper(),
            ]
        )
        duplicate_groups.setdefault(key, []).append(credit)

    for group in duplicate_groups.values():
        if len(group) < 2:
            continue
        ordered = sorted(group, key=cmp_to_key(by_id))
        kept, *copies = ordered
        for copy in copies:
            settle([], [copy])
            emit(
                result(
                    lane=lane,
                    tier="T0",
                    rule="T0_DUPLICATE_STATEMENT_ROW",
                    outcome="EXCEPTION",
                    confidence=CONFIDENCE.REFERENCE_EXACT,
                    left=[],
                    right=[copy.id],
                    inputs=[c.id for c in ordered],
                    evidence=[
                        f"identical to {kept.id} in reference, amount, value date and narration — the statement was ingested twice",
                        f"{money(copy.amount)} on {copy.value_date}",
                        f"the pair is indistinguishable; kept the lower id ({kept.id}) by convention",
                    ],
                    failure="DUPLICATE_CREDIT",
                    amount=copy.amount,
                )
            )

    # ── T0, step 2: reference and amount both exact ──────────────────────────
    def referenced_credits(settlement: Settlement) -> list[BankCredit]:
        key = normalise_reference(settlement.utr)
        if key == "":
            return []
        return [c for c in bank_index.by_reference.get(key, []) if is_open_credit(c)]

    tds = config.amount.tds_percent_of_gross
    rounding = config.amount.rounding_paise
    reference_rules: list[dict[str, Any]] = [
        {
            "rule": "T0_REFERENCE_AND_AMOUNT",
            "tier": "T0",
            "confidence": CONFIDENCE.REFERENCE_EXACT,
            "class": None,
            "max_late_days": config.bank.on_time_days,
            "expected": lambda s: s.net,
            "slack": 0,
            "why": lambda s, _c: f"amount is the settlement net {money(s.net)} to the paisa",
        },
        {
            "rule": "T1_LATE_CREDIT",
            "tier": "T1",
            "confidence": CONFIDENCE.LATE_CREDIT,
            "class": "TIMING_T_PLUS_N",
            "max_late_days": config.bank.late_days,
            "expected": lambda s: s.net,
            "slack": 0,
            "why": lambda s, c: f"amount ties exactly at {money(s.net)}; the credit landed {days_between(s.settled_at, c.value_date)} days after the settlement date",
        },
        {
            "rule": "T1_TDS_WITHHELD",
            "tier": "T1",
            "confidence": CONFIDENCE.TDS_WITHHELD,
            "class": "TDS_WITHHELD",
            "max_late_days": config.bank.late_days,
            "expected": lambda s: s.net - pct(s.gross, tds),
            "slack": 0,
            "why": lambda s, _c: f"credit is short by {money(pct(s.gross, tds))}, which is exactly {_num(tds)}% of gross {money(s.gross)} — TDS withheld and not shown in the report",
        },
        {
            "rule": "T1_ROUNDING",
            "tier": "T1",
            "confidence": CONFIDENCE.ROUNDING,
            "class": "ROUNDING_PAISE",
            "max_late_days": config.bank.late_days,
            "expected": lambda s: s.net,
            "slack": rounding,
            "why": lambda s, c: f"credit is {delta(c.amount, s.net)} against net {money(s.net)} — inside the {rounding} paise rounding tolerance",
        },
    ]

    for rule in reference_rules:
        for settlement in inp.settlements:
            if not is_open_settlement(settlement):
                continue
            candidates = stable_credits(referenced_credits(settlement))
            if not candidates:
                continue
            target = rule["expected"](settlement)
            slack = rule["slack"]

            def fits(credit: BankCredit, target: int = target, slack: int = slack, settlement: Settlement = settlement, rule: dict = rule) -> bool:
                gap = abs(credit.amount - target)
                if gap > slack:
                    return False
                if slack > 0 and gap == 0:
                    return False
                late = days_between(settlement.settled_at, credit.value_date)
                return 0 <= late <= rule["max_late_days"]

            fitting = [c for c in candidates if fits(c)]
            if len(fitting) != 1:
                continue
            credit = fitting[0]
            identity, failure = identity_evidence(settlement)
            settle([settlement], [credit])
            emit(
                result(
                    lane=lane,
                    tier=rule["tier"],
                    rule=rule["rule"],
                    outcome=applied(rule["confidence"]),
                    confidence=rule["confidence"],
                    left=[settlement.id],
                    right=[credit.id],
                    inputs=[settlement.id, *[c.id for c in candidates]],
                    evidence=[
                        f"bank reference {credit.reference} is the settlement UTR",
                        rule["why"](settlement, credit),
                        f"value date {credit.value_date} against settled_at {settlement.settled_at}",
                        *identity,
                    ],
                    failure=rule["class"] if rule["class"] is not None else failure,
                    amount=settlement.net,
                )
            )

    # ── T1, step 3: a reference one typo away (amount gated first) ───────────
    for credit in inp.bank:
        if not is_open_credit(credit) or credit.amount == 0:
            continue
        reference = normalise_reference(credit.reference)
        if len(reference) < config.reference.min_length:
            continue
        window = Window(add_days(credit.value_date, -config.bank.late_days), credit.value_date)
        candidates_s = [s for s in settlement_index.with_amount(credit.amount, window) if is_open_settlement(s)]
        near: list[tuple[Settlement, int]] = []
        for settlement in candidates_s:
            distance = edit_distance(reference, normalise_reference(settlement.utr), config.reference.max_edit_distance)
            if distance is not None:
                near.append((settlement, distance))
        if not near:
            continue
        near.sort(key=lambda row: row[1])
        best_settlement, best_distance = near[0]
        ambiguous = len(near) > 1 and near[1][1] == best_distance
        confidence = CONFIDENCE.AMBIGUOUS if ambiguous else CONFIDENCE.REFERENCE_NEAR_MISS
        identity, _failure = identity_evidence(best_settlement)
        if not ambiguous:
            settle([best_settlement], [credit])
        emit(
            result(
                lane=lane,
                tier="T1",
                rule="T1_REFERENCE_NEAR_MISS",
                outcome="PROPOSED" if ambiguous else applied(confidence),
                confidence=confidence,
                left=[best_settlement.id],
                right=[credit.id],
                inputs=[credit.id, *[s.id for s in candidates_s]],
                evidence=[
                    f"bank reference {credit.reference} is {best_distance} edit from settlement UTR {best_settlement.utr}",
                    f"amount {money(credit.amount)} ties exactly to net, and value date {credit.value_date} is within {config.bank.late_days} days of {best_settlement.settled_at}",
                    f"{len(near)} settlements are equally close on the reference — nothing here separates them"
                    if ambiguous
                    else f"no other settlement in the window is within {config.reference.max_edit_distance} edits",
                    *identity,
                ],
                failure=None if ambiguous else "TYPO_UTR",
                amount=credit.amount,
            )
        )

    # ── T1, step 3b: the reference is in the narration ───────────────────────
    for credit in inp.bank:
        if not is_open_credit(credit) or credit.amount == 0:
            continue
        if normalise_reference(credit.reference) != "":
            continue
        narration = normalise_reference(credit.description)

        def contains_utr(settlement: Settlement, narration: str = narration, credit: BankCredit = credit) -> bool:
            if not is_open_settlement(settlement):
                return False
            utr = normalise_reference(settlement.utr)
            if len(utr) < config.reference.min_length:
                return False
            if utr not in narration:
                return False
            late = days_between(settlement.settled_at, credit.value_date)
            return 0 <= late <= config.bank.late_days

        found = [s for s in inp.settlements if contains_utr(s)]
        if len(found) != 1:
            continue
        settlement = found[0]
        gap = credit.amount - settlement.net
        if abs(gap) > config.amount.rounding_paise:
            continue
        identity, _failure = identity_evidence(settlement)
        settle([settlement], [credit])
        emit(
            result(
                lane=lane,
                tier="T1",
                rule="T1_NARRATION_REFERENCE",
                outcome=applied(CONFIDENCE.NARRATION_REFERENCE),
                confidence=CONFIDENCE.NARRATION_REFERENCE,
                left=[settlement.id],
                right=[credit.id],
                inputs=[credit.id, settlement.id],
                evidence=[
                    f'the reference column is empty, and the settlement UTR {settlement.utr} appears inside the narration: "{credit.description}"',
                    f"amount ties exactly at {money(settlement.net)}"
                    if gap == 0
                    else f"amount is {delta(credit.amount, settlement.net)} against net {money(settlement.net)}, inside the {config.amount.rounding_paise} paise rounding tolerance",
                    "no other unmatched settlement's UTR appears in this narration",
                    *identity,
                ],
                failure="UTR_IN_NARRATION",
                amount=settlement.net,
            )
        )

    # ── T1, step 4: no reference at all ──────────────────────────────────────
    for credit in inp.bank:
        if not is_open_credit(credit) or credit.amount == 0:
            continue
        if normalise_reference(credit.reference) != "":
            continue
        window = Window(add_days(credit.value_date, -config.bank.late_days), credit.value_date)
        candidates_s = [s for s in settlement_index.with_amount(credit.amount, window) if is_open_settlement(s)]
        if not candidates_s:
            continue
        ordered = sorted(candidates_s, key=lambda s, c=credit: days_between(s.settled_at, c.value_date))
        unique = len(ordered) == 1
        confidence = CONFIDENCE.AMOUNT_AND_DATE if unique else CONFIDENCE.AMBIGUOUS
        best = ordered[0]
        identity, _failure = identity_evidence(best)
        if unique:
            settle([best], [credit])
        emit(
            result(
                lane=lane,
                tier="T1",
                rule="T1_AMOUNT_AND_DATE",
                outcome=applied(confidence) if unique else "PROPOSED",
                confidence=confidence,
                left=[best.id],
                right=[credit.id],
                inputs=[credit.id, *[s.id for s in ordered]],
                evidence=[
                    f'the bank line carries no reference: "{credit.description}"',
                    f"{money(credit.amount)} on {credit.value_date} ties to net {money(best.net)} settled {best.settled_at}",
                    f"it is the only unmatched settlement of this value within {config.bank.late_days} days"
                    if unique
                    else f"{len(ordered)} unmatched settlements of this value sit within {config.bank.late_days} days — a reference is needed to choose",
                    *identity,
                ],
                failure="MISSING_UTR" if unique else None,
                amount=credit.amount,
            )
        )

    # ── T1, step 6: debits that no payout explains (a catch-all runs last) ───
    chargeback_by_id = {c.id.upper(): c for c in inp.chargebacks}
    for credit in inp.bank:
        if not is_open_credit(credit) or credit.amount >= 0:
            continue
        narration = credit.description.upper()
        referenced = re.findall(r"DISP_[A-Z0-9]+", narration)
        dispute = next((chargeback_by_id[i] for i in referenced if i in chargeback_by_id), None)
        is_chargeback = "CHARGEBACK" in narration or dispute is not None
        settle([], [credit])
        if dispute is not None:
            tail = (
                " — the amount ties exactly"
                if dispute.amount == -credit.amount
                else f" — {delta(-credit.amount, dispute.amount)}"
            )
            second = f"narration names dispute {dispute.id}, raised {dispute.raised_at} for {money(dispute.amount)}{tail}"
        else:
            second = f"narration: {credit.description}"
        emit(
            result(
                lane=lane,
                tier="T0",
                rule="T0_STANDALONE_DEBIT",
                outcome="EXCEPTION",
                confidence=CONFIDENCE.REFERENCE_EXACT,
                left=[],
                right=[credit.id],
                inputs=[credit.id, dispute.id] if dispute else [credit.id],
                evidence=[
                    f"{money(-credit.amount)} debited on {credit.value_date}, and no unmatched settlement was paid out as a debit of that value",
                    second,
                ],
                failure="CHARGEBACK_DEDUCTION" if is_chargeback else None,
                amount=credit.amount,
            )
        )

    # ── T2, step 5: one settlement, several credits ──────────────────────────
    for settlement in inp.settlements:
        if not is_open_settlement(settlement):
            continue
        anchors = referenced_credits(settlement)
        anchor_ids = {id(a) for a in anchors}
        window = window_around(settlement.settled_at, 0, config.bank.split_span_days)
        pool = [
            c
            for c in bank_index.in_window(window.start, window.end)
            if is_open_credit(c) and c.amount > 0 and id(c) not in anchor_ids
        ]
        search = find_subsets(
            [*anchors, *pool],
            lambda c: c.amount,
            settlement.net,
            max_size=max(config.search.max_subset_size, len(anchors)),
            max_nodes=config.search.max_nodes,
            required=anchors,
        )
        counters["nodes"] += search.nodes
        viable = [subset for subset in search.subsets if len(subset) > 1]
        if not viable:
            continue
        anchored = len(anchors) > 0
        unique = len(viable) == 1
        if not unique:
            confidence = CONFIDENCE.AMBIGUOUS
        else:
            confidence = CONFIDENCE.SPLIT_CREDIT if anchored else CONFIDENCE.STRUCTURAL_UNANCHORED
        subset = stable_credits(viable[0])
        span = days_between(subset[0].value_date, subset[-1].value_date)
        identity, _failure = identity_evidence(settlement)
        if unique and confidence >= config.auto_apply:
            settle([settlement], subset)
        emit(
            result(
                lane=lane,
                tier="T2",
                rule="T2_SPLIT_CREDIT" if anchored else "T2_SPLIT_CREDIT_UNREFERENCED",
                outcome=applied(confidence) if unique else "PROPOSED",
                confidence=confidence,
                left=[settlement.id],
                right=[c.id for c in subset],
                inputs=capped_inputs([settlement.id, *[c.id for c in anchors], *[c.id for c in pool]]),
                evidence=[
                    f"{len(subset)} credits sum to {money(settlement.net)} exactly: "
                    + " + ".join(f"{c.id} {money(c.amount)}" for c in subset),
                    f"all of them carry the settlement UTR {settlement.utr}"
                    if anchored
                    else "none of them carries a usable reference, so the arithmetic is the only evidence",
                    f"paid across {span + 1} day(s) from {subset[0].value_date}, within the {config.bank.split_span_days}-day split window",
                    *([] if unique else [f"{len(viable)} different combinations tie to the same total — a human has to choose"]),
                    *(
                        [f"the search stopped at its {indian_int(config.search.max_nodes)}-node cap, so other combinations may exist"]
                        if search.cap_hit
                        else []
                    ),
                    *identity,
                ],
                failure="SPLIT_SETTLEMENT" if unique and anchored else None,
                amount=settlement.net,
                cap_hit=search.cap_hit,
            )
        )

    # ── T2, step 6: one credit, several settlements ──────────────────────────
    for credit in inp.bank:
        if not is_open_credit(credit) or credit.amount <= 0:
            continue
        reference = normalise_reference(credit.reference)
        anchors_s = [] if reference == "" else [s for s in settlement_index.by_reference.get(reference, []) if is_open_settlement(s)]
        subset_s: list[Settlement] | None = None
        ambiguity = 0
        cap_hit = False
        considered: list[str] = []

        if len(anchors_s) == 1:
            anchor = anchors_s[0]
            remainder = credit.amount - anchor.net
            if remainder > 0:
                exact = [s for s in settlement_index.with_amount(remainder) if is_open_settlement(s)]
                considered = [anchor.id, *[s.id for s in exact]]
                if len(exact) == 1:
                    subset_s = [anchor, exact[0]]
                elif len(exact) > 1:
                    ambiguity = len(exact)
                    subset_s = [anchor, exact[0]]
                else:
                    pool_s = [
                        s
                        for s in inp.settlements
                        if is_open_settlement(s) and s is not anchor and s.net > 0 and s.net <= remainder
                    ]
                    search_s = find_subsets(
                        pool_s,
                        lambda s: s.net,
                        remainder,
                        max_size=config.search.max_subset_size - 1,
                        max_nodes=config.search.max_nodes,
                    )
                    counters["nodes"] += search_s.nodes
                    cap_hit = search_s.cap_hit
                    considered = [anchor.id, *[s.id for s in pool_s]]
                    if len(search_s.subsets) == 1:
                        subset_s = [anchor, *search_s.subsets[0]]
                    elif len(search_s.subsets) > 1:
                        ambiguity = len(search_s.subsets)
                        subset_s = [anchor, *search_s.subsets[0]]
        elif len(anchors_s) == 0:
            window = window_around(credit.value_date, config.bank.late_days, 0)
            pool_s = [
                s
                for s in settlement_index.in_window(window.start, window.end)
                if is_open_settlement(s) and s.net > 0 and s.net < credit.amount
            ]
            search_s = find_subsets(
                pool_s,
                lambda s: s.net,
                credit.amount,
                max_size=config.search.max_subset_size,
                max_nodes=config.search.max_nodes,
            )
            counters["nodes"] += search_s.nodes
            cap_hit = search_s.cap_hit
            considered = [s.id for s in pool_s]
            viable_s = [c for c in search_s.subsets if len(c) > 1]
            if len(viable_s) == 1:
                subset_s = viable_s[0]
            elif len(viable_s) > 1:
                ambiguity = len(viable_s)
                subset_s = viable_s[0]

        if not subset_s or len(subset_s) < 2:
            continue
        anchored = len(anchors_s) == 1
        if ambiguity:
            confidence = CONFIDENCE.AMBIGUOUS
        else:
            confidence = CONFIDENCE.COMBINED_CREDIT if anchored else CONFIDENCE.STRUCTURAL_UNANCHORED
        ordered_s = sorted(subset_s, key=cmp_to_key(by_id))
        if not ambiguity and confidence >= config.auto_apply:
            settle(ordered_s, [credit])
        emit(
            result(
                lane=lane,
                tier="T2",
                rule="T2_COMBINED_CREDIT" if anchored else "T2_COMBINED_CREDIT_UNREFERENCED",
                outcome="PROPOSED" if ambiguity else applied(confidence),
                confidence=confidence,
                left=[s.id for s in ordered_s],
                right=[credit.id],
                inputs=capped_inputs([credit.id, *considered]),
                evidence=[
                    f"one credit of {money(credit.amount)} on {credit.value_date} equals {len(ordered_s)} settlement nets exactly: "
                    + " + ".join(f"{s.id} {money(s.net)}" for s in ordered_s),
                    f"reference {credit.reference} names {anchors_s[0].id}; the remainder {money(credit.amount - anchors_s[0].net)} is a single unmatched settlement"
                    if anchored
                    else "no reference survived on the credit, so the arithmetic is the only evidence",
                    f"narration: {credit.description}",
                    *([f"{ambiguity} different settlement combinations tie to this credit"] if ambiguity else []),
                    *(["the search stopped at its node cap; other combinations may exist"] if cap_hit else []),
                ],
                failure="COMBINED_CREDIT" if not ambiguity and anchored else None,
                amount=credit.amount,
                cap_hit=cap_hit,
            )
        )

    # ── T2, step 7: rank the candidates and decline ──────────────────────────
    for credit in inp.bank:
        if not is_open_credit(credit) or credit.amount == 0:
            continue
        window = window_around(credit.value_date, config.bank.late_days, 0)
        rows = [
            {"settlement": s, "gap": credit.amount - s.net, "late": days_between(s.settled_at, credit.value_date)}
            for s in settlement_index.near_amount(credit.amount, config.escalation.slack_paise, window)
            if is_open_settlement(s)
        ]

        def rank(a: dict, b: dict) -> int:
            first = abs(a["gap"]) - abs(b["gap"])
            if first:
                return first
            second = a["late"] - b["late"]
            if second:
                return second
            return -1 if a["settlement"].id < b["settlement"].id else 1

        ranked = sorted(rows, key=cmp_to_key(rank))[: config.escalation.max_candidates]
        if not ranked:
            continue
        best_row = ranked[0]
        escalated_credits.add(credit.id)
        escalated_settlements.add(best_row["settlement"].id)

        def describe(row: dict, credit: BankCredit = credit) -> str:
            s = row["settlement"]
            gap_text = "exact" if row["gap"] == 0 else delta(credit.amount, s.net)
            when = " (same day)" if row["late"] == 0 else f" ({row['late']} day(s) earlier)"
            return f"  {s.id} — net {money(s.net)} ({gap_text}), settled {s.settled_at}{when}, UTR {s.utr or '(none)'}"

        emit(
            result(
                lane=lane,
                tier="T2",
                rule="T2_ESCALATION_CANDIDATES",
                outcome="PROPOSED",
                confidence=CONFIDENCE.ESCALATED,
                left=[best_row["settlement"].id],
                right=[credit.id],
                inputs=[credit.id, *[row["settlement"].id for row in ranked]],
                evidence=[
                    f'{money(credit.amount)} credited {credit.value_date} with no usable reference: "{credit.description}"',
                    f"{len(ranked)} unmatched settlement(s) are within {config.escalation.slack_paise} paise and {config.bank.late_days} days:",
                    *[describe(row) for row in ranked],
                    "no rule can settle this safely: matching on an amount a few paise out with nothing to identify it is how a silent false match gets made, so this is ranked and left for judgement",
                ],
                failure=None,
                amount=credit.amount,
            )
        )

    # ── T4: everything left, with a reason ───────────────────────────────────
    for credit in inp.bank:
        if not is_open_credit(credit) or credit.id in escalated_credits:
            continue
        is_gateway = "RAZORPAY" in credit.description.upper()
        emit(
            result(
                lane=lane,
                tier="T4",
                rule="T4_UNEXPLAINED_CREDIT",
                outcome="EXCEPTION",
                confidence=0,
                left=[],
                right=[credit.id],
                inputs=[credit.id],
                evidence=[
                    f"{money(credit.amount)} credited {credit.value_date} with no settlement to explain it",
                    f"narration names the gateway but no unmatched settlement ties on reference, amount or a combination: {credit.description}"
                    if is_gateway
                    else f"narration is a different counterparty entirely: {credit.description}",
                    f"reference {'(none)' if credit.reference == '' else credit.reference}",
                ],
                failure=None if is_gateway else "FOREIGN_CREDIT",
                amount=credit.amount,
            )
        )

    for settlement in inp.settlements:
        if not is_open_settlement(settlement) or settlement.id in escalated_settlements:
            continue
        window = window_around(settlement.settled_at, 0, config.bank.late_days)
        near_c = [c for c in bank_index.near_amount(settlement.net, config.amount.rounding_paise, window) if is_open_credit(c)]
        emit(
            result(
                lane=lane,
                tier="T4",
                rule="T4_SETTLEMENT_NOT_BANKED",
                outcome="EXCEPTION",
                confidence=0,
                left=[settlement.id],
                right=[],
                inputs=[settlement.id, *[c.id for c in near_c]],
                evidence=[
                    f"{money(settlement.net)} settled {settlement.settled_at} under UTR {settlement.utr or '(none)'} never arrived",
                    f"no unmatched credit of that value within {config.bank.late_days} days"
                    if not near_c
                    else f"{len(near_c)} unmatched credit(s) are close on value but explain nothing: "
                    + ", ".join(f"{c.id} {money(c.amount)}" for c in near_c),
                ],
                failure=None,
                amount=settlement.net,
            )
        )


def _num(value: float) -> str:
    """JavaScript number-to-string for the values tolerances hold (1 → "1", 0.5 → "0.5")."""
    return str(int(value)) if float(value).is_integer() else str(value)


# ── Lane 2 of 3: settlements against the books ───────────────────────────────


def _match_ledger_lane(
    inp: MatchInput,
    _config: Tolerances,
    emit: Callable[[MatchResult], MatchResult],
    applied: Callable[[float], str],
) -> None:
    lane = "SETTLEMENT_TO_LEDGER"
    journals = group_journals(inp.ledger)
    by_settlement: dict[str, list[str]] = {}
    claimed: set[str] = set()

    for journal_id, lines in journals.items():
        referenced: dict[str, None] = {}  # insertion-ordered set
        for line in lines:
            for found in re.findall(r"setl_[a-z0-9]+", line.memo):
                referenced[found] = None
        for settlement_id in referenced:
            by_settlement.setdefault(settlement_id, []).append(journal_id)
            claimed.add(journal_id)

    def totals(lines: list[LedgerEntry]) -> tuple[int, int, dict[str, int]]:
        per_account: dict[str, int] = {}
        debits = 0
        credits = 0
        for line in lines:
            debits += line.debit
            credits += line.credit
            per_account[line.account] = per_account.get(line.account, 0) + line.debit - line.credit
        return debits, credits, per_account

    for settlement in inp.settlements:
        journal_ids = by_settlement.get(settlement.id, [])
        if not journal_ids:
            emit(
                result(
                    lane=lane,
                    tier="T4",
                    rule="T4_NOT_POSTED",
                    outcome="EXCEPTION",
                    confidence=0,
                    left=[settlement.id],
                    right=[],
                    inputs=[settlement.id],
                    evidence=[
                        f"settled {settlement.settled_at} for {money(settlement.net)} with no journal referencing it",
                        f"nothing in the books moves {money(settlement.gross)} out of Razorpay Clearing, so the clearing account is overstated by that much",
                    ],
                    failure="MISSING_LEDGER_ENTRY",
                    amount=settlement.net,
                )
            )
            continue
        if len(journal_ids) > 1:
            emit(
                result(
                    lane=lane,
                    tier="T1",
                    rule="T1_MULTIPLE_JOURNALS",
                    outcome="PROPOSED",
                    confidence=CONFIDENCE.AMBIGUOUS,
                    left=[settlement.id],
                    right=journal_ids,
                    inputs=[settlement.id, *journal_ids],
                    evidence=[f"{len(journal_ids)} journals reference this settlement: {', '.join(journal_ids)}"],
                    failure=None,
                    amount=settlement.net,
                )
            )
            continue

        journal_id = journal_ids[0]
        lines = journals[journal_id]
        debits, credits, per_account = totals(lines)
        bank = per_account.get("Bank", 0)
        clearing = -per_account.get("Razorpay Clearing", 0)
        balanced = debits == credits
        expensed = per_account.get("Payment Gateway Fees", 0) + per_account.get("GST Input", 0)

        if balanced and bank == settlement.net and clearing == settlement.gross:
            evidence = [
                f"journal {journal_id} names the settlement in its memo",
                f"Bank debited {money(bank)} — the settlement net — and Razorpay Clearing credited {money(clearing)} — the gross",
                f"the journal balances: {money(debits)} debits against {money(credits)} credits",
            ]
            if expensed != settlement.fees + settlement.tax:
                evidence.append(
                    f"the books expense {money(expensed)} of fees and GST where the report shows {money(settlement.fees + settlement.tax)} — the link holds, the report does not"
                )
            emit(
                result(
                    lane=lane,
                    tier="T0",
                    rule="T0_JOURNAL_REFERENCE",
                    outcome=applied(CONFIDENCE.REFERENCE_EXACT),
                    confidence=CONFIDENCE.REFERENCE_EXACT,
                    left=[settlement.id],
                    right=[journal_id],
                    inputs=[settlement.id, journal_id],
                    evidence=evidence,
                    failure=None,
                    amount=settlement.net,
                )
            )
            continue

        fees_missing = (
            settlement.fees + settlement.tax > 0
            and expensed == 0
            and bank == settlement.net + settlement.fees + settlement.tax
        )
        if fees_missing:
            evidence = [
                f"journal {journal_id} debits Bank {money(bank)}, which is the gross payout, not the net {money(settlement.net)}",
                f"no Payment Gateway Fees or GST Input line exists, so {money(settlement.fees + settlement.tax)} of cost was never expensed",
                f"the journal balances, which is why nothing else catches this: profit is overstated by {money(settlement.fees)} and input credit of {money(settlement.tax)} is unclaimed",
            ]
        else:
            evidence = [
                f"journal {journal_id} does not tie to the settlement",
                f"Bank debited {money(bank)} against net {money(settlement.net)}"
                + ("" if bank == settlement.net else f" — {delta(bank, settlement.net)}"),
                f"Razorpay Clearing credited {money(clearing)} against gross {money(settlement.gross)}",
                "the journal itself balances"
                if balanced
                else f"the journal does not balance: {money(debits)} debits against {money(credits)} credits",
            ]
        emit(
            result(
                lane=lane,
                tier="T4",
                rule="T4_FEE_NOT_EXPENSED" if fees_missing else "T4_JOURNAL_DOES_NOT_TIE",
                outcome="EXCEPTION",
                confidence=0,
                left=[settlement.id],
                right=[journal_id],
                inputs=[settlement.id, journal_id],
                evidence=evidence,
                failure="FEE_NOT_BOOKED" if fees_missing else None,
                amount=settlement.net,
            )
        )

    for journal_id, lines in journals.items():
        if journal_id in claimed:
            continue
        debits, _credits, _per = totals(lines)
        emit(
            result(
                lane=lane,
                tier="T4",
                rule="T4_ORPHAN_JOURNAL",
                outcome="EXCEPTION",
                confidence=0,
                left=[],
                right=[journal_id],
                inputs=[journal_id],
                evidence=[
                    f"journal {journal_id} posts {money(debits)} on {lines[0].posted_at} but references no settlement",
                    f"memo: {lines[0].memo}",
                ],
                failure=None,
                amount=debits,
            )
        )


# ── Lane 3 of 3: payments against settlements ────────────────────────────────


def _match_payment_lane(
    inp: MatchInput,
    config: Tolerances,
    emit: Callable[[MatchResult], MatchResult],
    applied: Callable[[float], str],
    counters: dict[str, int],
) -> None:
    lane = "PAYMENT_TO_SETTLEMENT"
    payment_by_id = {p.id: p for p in inp.payments}
    pools = group_payments_by_payout_date(inp.payments, config)

    rows_by_settlement: dict[str, list[ReconRow]] = {}
    for row in inp.recon:
        rows_by_settlement.setdefault(row.settlement_id, []).append(row)
    itemised = {row.entity_id for row in inp.recon if row.type == "payment"}

    def calendar_agreement(settlement: Settlement, batch: list[Payment]) -> int:
        return sum(1 for p in batch if expected_settlement_date(p.captured_at, config) == settlement.settled_at)

    unreported: list[Settlement] = []

    for settlement in inp.settlements:
        rows = rows_by_settlement.get(settlement.id, [])
        if not rows:
            unreported.append(settlement)
            continue
        payment_rows = [row for row in rows if row.type == "payment"]
        batch: list[Payment] = []
        unknown: list[ReconRow] = []
        for row in payment_rows:
            payment = payment_by_id.get(row.entity_id)
            if payment is not None:
                batch.append(payment)
            else:
                unknown.append(row)

        gross = sum(p.gross for p in batch)
        fees = sum(p.fee for p in batch)
        tax = sum(p.tax for p in batch)
        deductions = 0
        for row in rows:
            if row.type != "payment":
                deductions -= row.amount

        count_ties = len(batch) == settlement.payment_count
        gross_ties = gross == settlement.gross
        fees_tie = fees == settlement.fees
        tax_ties = tax == settlement.tax
        utr_disagrees = sum(
            1 for row in rows if normalise_reference(row.utr) != normalise_reference(settlement.utr)
        )
        agreed = calendar_agreement(settlement, batch)

        if count_ties and gross_ties and fees_tie and tax_ties and not unknown:
            emit(
                result(
                    lane=lane,
                    tier="T0",
                    rule="T0_RECON_REPORT",
                    outcome=applied(CONFIDENCE.REFERENCE_EXACT),
                    confidence=CONFIDENCE.REFERENCE_EXACT,
                    left=[p.id for p in batch],
                    right=[settlement.id],
                    inputs=capped_inputs([settlement.id, *[row.id for row in rows]]),
                    evidence=[
                        f"the recon report itemises {len(payment_rows)} payments against this payout under UTR {settlement.utr}",
                        f"count, gross {money(gross)}, fees {money(fees)} and GST {money(tax)} all tie to the settlement report exactly",
                        f"the T+{config.settlement.delay_days} payout calendar independently puts all {len(batch)} of them on {settlement.settled_at}"
                        if agreed == len(batch)
                        else f"the payout calendar puts only {agreed} of {len(batch)} on {settlement.settled_at} — the report is authoritative, but that disagreement is worth a look",
                        *(
                            [f"{money(deductions)} of refunds and disputes are itemised against the same payout"]
                            if deductions > 0
                            else []
                        ),
                        *([f"{utr_disagrees} row(s) carry a different UTR than the settlement"] if utr_disagrees > 0 else []),
                    ],
                    failure=None,
                    amount=settlement.gross,
                )
            )
            continue

        misattributed = count_ties and not unknown and not gross_ties
        if misattributed:
            evidence = [
                f"the recon report itemises exactly the {settlement.payment_count} payments this payout claims, and they total {money(gross)} against a reported gross of {money(settlement.gross)} — {delta(gross, settlement.gross)}",
                "the count is right and the value is not, so a payment has been itemised against the wrong payout",
                f"every payment listed exists and settles on {settlement.settled_at}, so nothing here is missing — only misfiled",
            ]
        else:
            evidence = [
                "the recon report does not reconcile to the settlement report",
                f"{len(batch)} payments itemised against {settlement.payment_count} claimed, totalling {money(gross)} against {money(settlement.gross)}",
                *(
                    [
                        f"{len(unknown)} row(s) name a payment that is not in the payments export: "
                        + ", ".join(row.entity_id for row in unknown[:3])
                    ]
                    if unknown
                    else []
                ),
                *([] if fees_tie else [f"fees {money(fees)} against {money(settlement.fees)}"]),
                *([] if tax_ties else [f"GST {money(tax)} against {money(settlement.tax)}"]),
            ]
        emit(
            result(
                lane=lane,
                tier="T4",
                rule="T4_RECON_VALUE_DOES_NOT_TIE" if misattributed else "T4_RECON_DOES_NOT_TIE",
                outcome="EXCEPTION",
                confidence=0,
                left=[],
                right=[settlement.id],
                inputs=capped_inputs([settlement.id, *[row.id for row in rows]]),
                evidence=evidence,
                failure="MISATTRIBUTED_PAYMENT" if misattributed else None,
                amount=settlement.gross,
            )
        )

    if not unreported:
        return

    unreported_by_date: dict[str, list[Settlement]] = {}
    for settlement in unreported:
        unreported_by_date.setdefault(settlement.settled_at, []).append(settlement)

    def by_count_then_id(a: Settlement, b: Settlement) -> int:
        first = a.payment_count - b.payment_count
        if first:
            return first
        return -1 if a.id < b.id else 1

    for day in sorted(unreported_by_date.keys()):
        missing = sorted(unreported_by_date[day], key=cmp_to_key(by_count_then_id))
        remaining = [p for p in pools.get(day, []) if p.id not in itemised]
        claimed_count = sum(s.payment_count for s in missing)
        claimed_gross = sum(s.gross for s in missing)
        remaining_gross = sum(p.gross for p in remaining)
        ties_out = len(remaining) == claimed_count and remaining_gross == claimed_gross
        tie_evidence = (
            f"the remainder ties out: {len(remaining)} payments worth {money(remaining_gross)} are due for payout on {day} and unplaced by the report, against {claimed_count} worth {money(claimed_gross)} claimed"
            if ties_out
            else f"the remainder does NOT tie out: {len(remaining)} unplaced payments worth {money(remaining_gross)} against {claimed_count} claimed worth {money(claimed_gross)}"
        )

        if len(missing) == 1 and ties_out:
            settlement = missing[0]
            emit(
                result(
                    lane=lane,
                    tier="T2",
                    rule="T2_RECON_BY_ELIMINATION",
                    outcome=applied(CONFIDENCE.BY_ELIMINATION),
                    confidence=CONFIDENCE.BY_ELIMINATION,
                    left=[p.id for p in remaining],
                    right=[settlement.id],
                    inputs=capped_inputs([settlement.id, *[p.id for p in remaining]]),
                    evidence=[
                        "the recon report has no rows for this payout at all",
                        f"every other payout on {day} is itemised, so its payments are exactly what is left over",
                        tie_evidence,
                        "count and gross both tie to the paisa, so no combination had to be chosen",
                    ],
                    failure="MISSING_RECON_ROW",
                    amount=settlement.gross,
                )
            )
            continue

        resolved: set[str] = set()
        for settlement in missing:
            if not partition_is_tractable(len(remaining), settlement.payment_count, config):
                continue
            search = find_subsets(
                remaining,
                lambda p: p.gross,
                settlement.gross,
                max_size=settlement.payment_count,
                exact_size=settlement.payment_count,
                max_nodes=config.search.max_nodes,
            )
            counters["nodes"] += search.nodes
            if len(search.subsets) != 1:
                continue
            chosen = search.subsets[0]
            emit(
                result(
                    lane=lane,
                    tier="T2",
                    rule="T2_RECON_SUBSET_SUM",
                    outcome=applied(CONFIDENCE.PAYMENT_PARTITION),
                    confidence=CONFIDENCE.PAYMENT_PARTITION,
                    left=[p.id for p in chosen],
                    right=[settlement.id],
                    inputs=capped_inputs([settlement.id, *[p.id for p in remaining]]),
                    evidence=[
                        "the recon report has no rows for this payout",
                        f"exactly one set of {settlement.payment_count} payments out of the {len(remaining)} unplaced on {day} totals {money(settlement.gross)}, and no other combination of that size does",
                        tie_evidence,
                    ],
                    failure="MISSING_RECON_ROW",
                    amount=settlement.gross,
                )
            )
            resolved.add(settlement.id)
            taken = {id(p) for p in chosen}
            remaining = [p for p in remaining if id(p) not in taken]

        for settlement in missing:
            if settlement.id in resolved:
                continue
            emit(
                result(
                    lane=lane,
                    tier="T4",
                    rule="T4_NOT_ITEMISED",
                    outcome="EXCEPTION",
                    confidence=0,
                    left=[],
                    right=[settlement.id],
                    inputs=capped_inputs([settlement.id, *[p.id for p in remaining]]),
                    evidence=[
                        f"the recon report has no rows for this payout, and {len(missing) - 1} other payout(s) on {day} are missing from it too",
                        *(
                            [f"{len(resolved)} of them were recovered by exact subset-sum; this one was not"]
                            if resolved
                            else []
                        ),
                        tie_evidence,
                        f"splitting {len(remaining)} unplaced payments across {len(missing)} payouts of "
                        + " and ".join(str(s.payment_count) for s in missing)
                        + " is not derivable from these sources — the report is the only document that says which is which",
                        "the money is accounted for; the attribution is not. Ask for a complete recon report.",
                    ],
                    failure="MISSING_RECON_ROW",
                    amount=settlement.gross,
                )
            )


# ── Stats ────────────────────────────────────────────────────────────────────


def _collation_key(text: str) -> tuple:
    """Approximates ``String.prototype.localeCompare`` (ICU root) for rule identifiers:
    case-insensitive, with punctuation ordering before digits and letters."""
    return tuple((0, ch) if not ch.isalnum() else (1, ch.lower()) for ch in text)


def _locale_compare(a: str, b: str) -> int:
    ka, kb = _collation_key(a), _collation_key(b)
    return (ka > kb) - (ka < kb)


def summarise(results: list[MatchResult], counters: dict[str, int], inp: MatchInput, elapsed_ms: float) -> dict[str, Any]:
    by_tier = dict.fromkeys(TIERS, 0)
    by_outcome = dict.fromkeys(OUTCOMES, 0)
    by_lane = {lane: dict.fromkeys(OUTCOMES, 0) for lane in LANES}
    rules: dict[str, dict[str, Any]] = {}
    cap_hits = 0
    for item in results:
        by_tier[item["tier"]] += 1
        by_outcome[item["outcome"]] += 1
        by_lane[item["lane"]][item["outcome"]] += 1
        if item.get("capHit"):
            cap_hits += 1
        key = f"{item['rule']}|{item['outcome']}"
        if key in rules:
            rules[key]["count"] += 1
        else:
            rules[key] = {"rule": item["rule"], "tier": item["tier"], "outcome": item["outcome"], "count": 1}

    def by_rule(a: dict, b: dict) -> int:
        return _locale_compare(a["tier"], b["tier"]) or (b["count"] - a["count"]) or _locale_compare(a["rule"], b["rule"])

    return {
        "byTier": by_tier,
        "byOutcome": by_outcome,
        "byLane": by_lane,
        "byRule": sorted(rules.values(), key=cmp_to_key(by_rule)),
        "capHits": cap_hits,
        "searchNodes": counters["nodes"],
        "units": len(inp.settlements) * 2 + len(inp.bank),
        "elapsedMs": elapsed_ms,
    }
