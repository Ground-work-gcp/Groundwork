"""Cash position derived from the reconciliation (ported from ``cash.ts``).

Three lines, deliberately separate, because a controller needs to know which is which:
**reconciled** (the bank confirmed it), **in flight** (expected, not confirmed) and
**at risk** (claimed, unresolved). Folding the third into the second is exactly the
forecast a finance team stops trusting the first time it is wrong.
"""

from __future__ import annotations

from typing import Any

from .records import BankCredit, Payment, Settlement
from .tolerance import TOLERANCES, Tolerances, expected_settlement_date


def _month_of(iso_date: str) -> str:
    return iso_date[:7]


def months_between(earliest: str, latest: str) -> list[str]:
    keys: list[str] = []
    year, month = (int(part) for part in earliest.split("-"))
    last_year, last_month = (int(part) for part in latest.split("-"))
    while year < last_year or (year == last_year and month <= last_month):
        keys.append(f"{year}-{month:02d}")
        month += 1
        if month > 12:
            month = 1
            year += 1
    return keys


def build_cash_series(
    payments: list[Payment],
    settlements: list[Settlement],
    bank: list[BankCredit],
    results: list[dict[str, Any]],
    queue: list[dict[str, Any]],
    config: Tolerances = TOLERANCES,
) -> dict[str, Any]:
    credit_by_id = {c.id: c for c in bank}
    settlement_by_id = {s.id: s for s in settlements}
    dates = sorted(
        [c.value_date for c in bank]
        + [s.settled_at for s in settlements]
        + [expected_settlement_date(p.captured_at, config) for p in payments]
    )
    period_keys = months_between(_month_of(dates[0]), _month_of(dates[-1]))
    index_of = {key: i for i, key in enumerate(period_keys)}
    reconciled = [0] * len(period_keys)
    in_flight = [0] * len(period_keys)
    at_risk = [0] * len(period_keys)

    # Reconciled: driven off AUTO_MATCHED results, so it can never disagree with the scoreboard.
    confirmed: set[str] = set()
    for item in results:
        if item["lane"] != "SETTLEMENT_TO_BANK" or item["outcome"] != "AUTO_MATCHED":
            continue
        confirmed.update(item["left"])
        for credit_id in item["right"]:
            credit = credit_by_id.get(credit_id)
            if credit is None:
                continue
            at = index_of.get(_month_of(credit.value_date))
            if at is not None:
                reconciled[at] += credit.amount

    last_statement = ""
    for credit in bank:
        if credit.value_date > last_statement:
            last_statement = credit.value_date

    for settlement in settlements:
        if settlement.id in confirmed:
            continue
        at = index_of.get(_month_of(settlement.settled_at))
        if at is not None:
            in_flight[at] += settlement.net

    settled_payments: set[str] = set()
    for item in results:
        if item["lane"] == "PAYMENT_TO_SETTLEMENT":
            settled_payments.update(item["left"])
    for payment in payments:
        if payment.id in settled_payments:
            continue
        payout = expected_settlement_date(payment.captured_at, config)
        if payout <= last_statement:
            continue
        at = index_of.get(_month_of(payout))
        if at is not None:
            in_flight[at] += payment.net

    entry_periods: list[int] = []
    for entry in queue:
        found = next((credit_by_id[i].value_date for i in entry["right"] if i in credit_by_id), None)
        if found is None:
            found = next((settlement_by_id[i].settled_at for i in entry["left"] if i in settlement_by_id), None)
        if found is None:
            found = next((settlement_by_id[i].settled_at for i in entry["right"] if i in settlement_by_id), None)
        at = index_of.get(_month_of(found)) if found else None
        if at is None:
            entry_periods.append(0)
            continue
        at_risk[at] += abs(entry["amount"])
        entry_periods.append(at)

    return {
        "periodKeys": period_keys,
        "reconciled": reconciled,
        "inFlight": in_flight,
        "atRisk": at_risk,
        "entryPeriods": entry_periods,
    }
