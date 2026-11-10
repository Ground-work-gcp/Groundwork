"""Ingestion of the seven settlement sources (ported from ``ingest.ts``).

A pure function of file contents. **A row that cannot be parsed becomes a record, not a
warning**: silently dropping malformed lines removes them from the denominator and the
match rate becomes a confident number about a batch that was never fully read.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypeVar

from .parse import (
    CsvRow,
    DateFormat,
    Decoded,
    js_trim,
    parse_csv,
    parse_date,
    parse_enum,
    parse_integer,
    parse_minor_units,
    parse_text,
)
from .records import BankCredit, Chargeback, LedgerEntry, Payment, ReconRow, Refund, Settlement

T = TypeVar("T")

SOURCE_NAMES = (
    "payments.csv",
    "refunds.csv",
    "chargebacks.csv",
    "settlements.csv",
    "recon.csv",
    "bank.csv",
    "ledger.csv",
)


@dataclass
class Rejection:
    file: str
    line: int
    reason: str  # SHORT_ROW | BAD_FIELD | DUPLICATE_ID | NO_AMOUNT
    detail: str
    raw: str

    def to_dict(self) -> dict:
        return {"file": self.file, "line": self.line, "reason": self.reason, "detail": self.detail, "raw": self.raw}


@dataclass
class FileStat:
    file: str
    rows_in: int
    records_out: int
    rejected: int
    missing_columns: list[str]

    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "rowsIn": self.rows_in,
            "recordsOut": self.records_out,
            "rejected": self.rejected,
            "missingColumns": self.missing_columns,
        }


@dataclass
class IngestedBatch:
    payments: list[Payment] = field(default_factory=list)
    refunds: list[Refund] = field(default_factory=list)
    chargebacks: list[Chargeback] = field(default_factory=list)
    settlements: list[Settlement] = field(default_factory=list)
    recon: list[ReconRow] = field(default_factory=list)
    bank: list[BankCredit] = field(default_factory=list)
    ledger: list[LedgerEntry] = field(default_factory=list)
    rejections: list[Rejection] = field(default_factory=list)
    files: list[FileStat] = field(default_factory=list)


class Reader:
    """Reads one row by column name, collecting decode errors instead of raising."""

    def __init__(self, header: list[str], row: CsvRow, errors: list[str]):
        self._index: dict[str, int] = {}
        for i, name in enumerate(header):
            self._index[js_trim(name).lower()] = i
        self._row = row
        self._errors = errors

    def cell(self, column: str) -> str:
        at = self._index.get(column.lower())
        if at is None or at >= len(self._row.cells):
            return ""
        return self._row.cells[at]

    def _take(self, column: str, decoded: Decoded, fallback):
        if decoded.ok:
            return decoded.value
        self._errors.append(f"{column}: {decoded.error}")
        return fallback

    def text(self, column: str, *, required: bool = True) -> str:
        return self._take(column, parse_text(self.cell(column), required=required), "")

    def minor(self, column: str, *, required: bool = True) -> int:
        raw = self.cell(column)
        if js_trim(raw) == "" and not required:
            return 0
        return self._take(column, parse_minor_units(raw), 0)

    def date(self, column: str, fmt: DateFormat) -> str:
        return self._take(column, parse_date(self.cell(column), fmt), "")

    def integer(self, column: str) -> int:
        return self._take(column, parse_integer(self.cell(column)), 0)

    def choice(self, column: str, allowed: tuple[str, ...]) -> str:
        return self._take(column, parse_enum(self.cell(column), allowed), allowed[0])


def ingest_file(
    file: str,
    text: str | None,
    required: tuple[str, ...],
    decode: Callable[[Reader, list[str]], T],
    id_of: Callable[[T], str] = lambda record: record.id,  # type: ignore[attr-defined]
) -> tuple[list[T], list[Rejection], FileStat]:
    """Decode one CSV into records plus typed rejections. Generic over the record shape."""
    rejections: list[Rejection] = []
    if text is None:
        return [], rejections, FileStat(file, 0, 0, 0, list(required))

    header, rows = parse_csv(text)
    present = {name.lower() for name in header}
    missing = [name for name in required if name.lower() not in present]
    # A missing column is a failure of the *file*, not of its rows.
    if missing:
        return [], rejections, FileStat(file, len(rows), 0, len(rows), missing)

    records: list[T] = []
    seen: set[str] = set()
    for row in rows:
        if all(js_trim(value) == "" for value in row.cells):
            continue
        if len(row.cells) < len(header):
            rejections.append(
                Rejection(file, row.line, "SHORT_ROW", f"expected {len(header)} columns, found {len(row.cells)}", row.raw)
            )
            continue
        errors: list[str] = []
        record = decode(Reader(header, row, errors), errors)
        if errors:
            reason = "NO_AMOUNT" if errors[0].startswith("!") else "BAD_FIELD"
            detail = "; ".join(errors)
            if detail.startswith("!"):
                detail = detail[1:]
            rejections.append(Rejection(file, row.line, reason, detail, row.raw))
            continue
        record_id = id_of(record)
        if record_id in seen:
            rejections.append(Rejection(file, row.line, "DUPLICATE_ID", f"{record_id} already seen in this file", row.raw))
            continue
        seen.add(record_id)
        records.append(record)

    return records, rejections, FileStat(file, len(rows), len(records), len(rejections), [])


METHODS = ("card", "upi", "netbanking", "wallet")
ACCOUNTS = (
    "Bank",
    "Razorpay Clearing",
    "Payment Gateway Fees",
    "GST Input",
    "Refunds",
    "Chargebacks",
    "TDS Receivable",
)


def _bank(read: Reader, errors: list[str]) -> BankCredit:
    debit_raw = js_trim(read.cell("Debit"))
    credit_raw = js_trim(read.cell("Credit"))
    if debit_raw == "" and credit_raw == "":
        errors.append("!Debit/Credit: both empty")
    record_id = read.text("Txn Id")
    value_date = read.date("Value Date", "DMY")
    description = read.text("Narration", required=False)
    reference = read.text("Ref No", required=False)
    credit = 0 if credit_raw == "" else read.minor("Credit")
    debit = 0 if debit_raw == "" else read.minor("Debit")
    return BankCredit(record_id, value_date, description, reference, credit - debit)


def ingest_sources(sources: dict[str, str | None]) -> IngestedBatch:
    parts = [
        ingest_file(
            "payments.csv",
            sources.get("payments.csv"),
            ("payment_id", "order_id", "captured_at", "method", "amount", "fee", "tax", "net", "status"),
            lambda r, _e: Payment(
                r.text("payment_id"),
                r.text("order_id"),
                r.date("captured_at", "ISO"),
                r.choice("method", METHODS),
                r.minor("amount"),
                r.minor("fee", required=False),
                r.minor("tax", required=False),
                r.minor("net"),
                r.choice("status", ("captured", "refunded", "disputed")),
            ),
        ),
        ingest_file(
            "refunds.csv",
            sources.get("refunds.csv"),
            ("refund_id", "payment_id", "created_at", "amount", "speed"),
            lambda r, _e: Refund(
                r.text("refund_id"),
                r.text("payment_id"),
                r.date("created_at", "ISO"),
                r.minor("amount"),
                r.choice("speed", ("normal", "instant")),
            ),
        ),
        ingest_file(
            "chargebacks.csv",
            sources.get("chargebacks.csv"),
            ("dispute_id", "payment_id", "raised_at", "amount", "status"),
            lambda r, _e: Chargeback(
                r.text("dispute_id"),
                r.text("payment_id"),
                r.date("raised_at", "ISO"),
                r.minor("amount"),
                r.choice("status", ("open", "lost", "won")),
            ),
        ),
        ingest_file(
            "settlements.csv",
            sources.get("settlements.csv"),
            ("settlement_id", "settled_at", "utr", "gross", "fees", "tax", "net", "payment_count"),
            lambda r, _e: Settlement(
                r.text("settlement_id"),
                r.date("settled_at", "ISO"),
                r.text("utr", required=False),
                r.minor("gross"),
                r.minor("fees", required=False),
                r.minor("tax", required=False),
                r.minor("refunds", required=False),
                r.minor("chargebacks", required=False),
                r.minor("tds", required=False),
                r.minor("net"),
                r.integer("payment_count"),
            ),
        ),
        ingest_file(
            "recon.csv",
            sources.get("recon.csv"),
            ("entry_id", "settlement_id", "settled_at", "type", "entity_id", "amount"),
            lambda r, _e: ReconRow(
                r.text("entry_id"),
                r.text("settlement_id"),
                r.text("settlement_utr", required=False),
                r.date("settled_at", "ISO"),
                r.choice("type", ("payment", "refund", "chargeback")),
                r.text("entity_id"),
                r.minor("amount"),
                r.minor("fee", required=False),
                r.minor("tax", required=False),
            ),
        ),
        ingest_file("bank.csv", sources.get("bank.csv"), ("txn id", "value date", "narration", "ref no"), _bank),
        ingest_file(
            "ledger.csv",
            sources.get("ledger.csv"),
            ("line_id", "journal_id", "posted_at", "account", "debit", "credit"),
            lambda r, _e: LedgerEntry(
                r.text("line_id"),
                r.text("journal_id"),
                r.date("posted_at", "ISO"),
                r.choice("account", ACCOUNTS),
                r.minor("debit", required=False),
                r.minor("credit", required=False),
                r.text("memo", required=False),
            ),
        ),
    ]
    names = ("payments", "refunds", "chargebacks", "settlements", "recon", "bank", "ledger")
    batch = IngestedBatch()
    for name, (records, rejections, stat) in zip(names, parts, strict=True):
        setattr(batch, name, records)
        batch.rejections.extend(rejections)
        batch.files.append(stat)
    return batch
