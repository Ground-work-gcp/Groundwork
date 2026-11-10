"""Canonical records for the settlement reconciliation loop (ported from ``types.ts``).

``eq=False`` is deliberate: the matcher compares records by *identity* (``includes``,
``!==`` and ``Set`` membership in the TypeScript), and two distinct records with equal
fields must never be confused with one another.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(eq=False, slots=True)
class Payment:
    id: str
    order_id: str
    captured_at: str
    method: str
    gross: int
    fee: int
    tax: int
    net: int
    status: str


@dataclass(eq=False, slots=True)
class Refund:
    id: str
    payment_id: str
    created_at: str
    amount: int
    speed: str


@dataclass(eq=False, slots=True)
class Chargeback:
    id: str
    payment_id: str
    raised_at: str
    amount: int
    status: str


@dataclass(eq=False, slots=True)
class Settlement:
    id: str
    settled_at: str
    utr: str
    gross: int
    fees: int
    tax: int
    refunds: int
    chargebacks: int
    tds: int
    net: int
    payment_count: int


@dataclass(eq=False, slots=True)
class BankCredit:
    id: str
    value_date: str
    description: str
    reference: str
    # Signed: positive is a credit, negative a debit.
    amount: int


@dataclass(eq=False, slots=True)
class ReconRow:
    id: str
    settlement_id: str
    utr: str
    settled_at: str
    type: str
    entity_id: str
    amount: int
    fee: int
    tax: int


@dataclass(eq=False, slots=True)
class LedgerEntry:
    id: str
    journal_id: str
    posted_at: str
    account: str
    debit: int
    credit: int
    memo: str


LANES = ("PAYMENT_TO_SETTLEMENT", "SETTLEMENT_TO_BANK", "SETTLEMENT_TO_LEDGER")

FAILURE_LABEL: dict[str, str] = {
    "MISSING_UTR": "Bank line carries no reference",
    "TYPO_UTR": "Reference transposed",
    "SPLIT_SETTLEMENT": "One settlement, two credits",
    "COMBINED_CREDIT": "One credit, two settlements",
    "FEE_NOT_BOOKED": "Fee and GST never posted",
    "TDS_WITHHELD": "Credit short by TDS",
    "TIMING_T_PLUS_N": "Credit landed late",
    "REFUND_NETTED": "Refund missing from the report",
    "CHARGEBACK_DEDUCTION": "Standalone chargeback debit",
    "DUPLICATE_CREDIT": "Same credit ingested twice",
    "FOREIGN_CREDIT": "Not a gateway credit",
    "ROUNDING_PAISE": "Off by paise",
    "MISSING_LEDGER_ENTRY": "Settled but never posted",
    "MISSING_RECON_ROW": "Settled but never itemised",
    "MISATTRIBUTED_PAYMENT": "Payment itemised against the wrong payout",
    "UTR_IN_NARRATION": "Reference buried in the narration",
    "DISGUISED_COUNTERPARTY": "Gateway payout under an unrecognisable name",
    "NARRATED_PAYOUT": "Payout described in prose, not referenced",
}
