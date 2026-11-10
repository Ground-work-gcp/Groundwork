"""Every number the matcher is allowed to be lenient about (ported from ``tolerance.ts``).

Tolerances are named config, not magic numbers scattered in the code: every act of
leniency is one line you can read, change and blame. They are business facts (T+2
settlement, 1% TDS under s.194-O), not tuning knobs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta


@dataclass(frozen=True)
class SettlementTolerance:
    delay_days: int = 2
    skip_weekends: bool = True


@dataclass(frozen=True)
class BankTolerance:
    on_time_days: int = 1
    late_days: int = 7
    split_span_days: int = 3


@dataclass(frozen=True)
class AmountTolerance:
    rounding_paise: int = 5
    tds_percent_of_gross: float = 1


@dataclass(frozen=True)
class ReferenceTolerance:
    max_edit_distance: int = 2
    min_length: int = 8


@dataclass(frozen=True)
class EscalationTolerance:
    slack_paise: int = 10
    max_candidates: int = 5


@dataclass(frozen=True)
class SearchTolerance:
    max_nodes: int = 200_000
    max_subset_size: int = 3
    max_combinatorial_size: int = 4


@dataclass(frozen=True)
class Tolerances:
    settlement: SettlementTolerance = field(default_factory=SettlementTolerance)
    bank: BankTolerance = field(default_factory=BankTolerance)
    amount: AmountTolerance = field(default_factory=AmountTolerance)
    reference: ReferenceTolerance = field(default_factory=ReferenceTolerance)
    escalation: EscalationTolerance = field(default_factory=EscalationTolerance)
    search: SearchTolerance = field(default_factory=SearchTolerance)
    # Confidence at or above which a match is applied without a human. It sits above every
    # rule that guesses and below every rule that verifies arithmetic.
    auto_apply: float = 0.85


TOLERANCES = Tolerances()


class CONFIDENCE:  # noqa: N801 - mirrors the TS constant object
    """What each rule is worth. The ordering is the whole design."""

    REFERENCE_EXACT = 1
    LATE_CREDIT = 0.98
    ROUNDING = 0.96
    TDS_WITHHELD = 0.96
    REFERENCE_NEAR_MISS = 0.94
    AMOUNT_AND_DATE = 0.9
    SPLIT_CREDIT = 0.9
    COMBINED_CREDIT = 0.88
    STRUCTURAL_UNANCHORED = 0.7
    DATE_POOL_UNIQUE = 0.97
    PAYMENT_PARTITION = 0.9
    BY_ELIMINATION = 0.9
    DATE_POOL_TIED = 0.6
    AMBIGUOUS = 0.4
    NARRATION_REFERENCE = 0.94
    ESCALATED = 0.3


# ── The payout calendar ──────────────────────────────────────────────────────


def day_of(iso_date: str) -> date:
    return date.fromisoformat(iso_date[:10])


def iso_of(value: date) -> str:
    return value.isoformat()


def days_between(start: str, end: str) -> int:
    """Whole days from ``start`` to ``end``. Signed: negative means ``end`` came first."""
    return (day_of(end) - day_of(start)).days


def add_days(iso_date: str, days: int) -> str:
    return iso_of(day_of(iso_date) + timedelta(days=days))


def expected_settlement_date(captured_at: str, config: Tolerances = TOLERANCES) -> str:
    """When a payment captured on ``captured_at`` should be paid out (T+n, weekends skipped)."""
    when = day_of(captured_at) + timedelta(days=config.settlement.delay_days)
    if config.settlement.skip_weekends:
        while when.weekday() >= 5:  # Saturday=5, Sunday=6
            when += timedelta(days=1)
    return iso_of(when)


def tolerances_as_dict(config: Tolerances = TOLERANCES) -> dict:
    """The camelCase shape Magpie's eval report used, for API responses and audit."""
    return {
        "settlement": {"delayDays": config.settlement.delay_days, "skipWeekends": config.settlement.skip_weekends},
        "bank": {
            "onTimeDays": config.bank.on_time_days,
            "lateDays": config.bank.late_days,
            "splitSpanDays": config.bank.split_span_days,
        },
        "amount": {
            "roundingPaise": config.amount.rounding_paise,
            "tdsPercentOfGross": config.amount.tds_percent_of_gross,
        },
        "reference": {
            "maxEditDistance": config.reference.max_edit_distance,
            "minLength": config.reference.min_length,
        },
        "escalation": {
            "slackPaise": config.escalation.slack_paise,
            "maxCandidates": config.escalation.max_candidates,
        },
        "search": {
            "maxNodes": config.search.max_nodes,
            "maxSubsetSize": config.search.max_subset_size,
            "maxCombinatorialSize": config.search.max_combinatorial_size,
        },
        "autoApply": config.auto_apply,
    }
