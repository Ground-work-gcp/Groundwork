"""Candidate generation and the bounded structural search (ported from ``candidates.ts``).

Matching must not be O(n²): every pass starts from an index here and may only look at the
candidates an index hands it, so the candidate set *is* the evidence trail.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Generic, TypeVar

from .records import BankCredit, LedgerEntry, Payment, Settlement
from .tolerance import TOLERANCES, Tolerances, add_days, days_between, expected_settlement_date

T = TypeVar("T")

_NON_ALNUM = re.compile(r"[^A-Z0-9]")


def normalise_reference(raw: str) -> str:
    """A bank reference reduced to what is comparable: upper case, letters and digits only."""
    return _NON_ALNUM.sub("", raw.upper())


def edit_distance(a: str, b: str, maximum: int) -> int | None:
    """Damerau–Levenshtein distance, abandoned as soon as it exceeds ``maximum``.

    A transposition costs 1 — the typo humans actually make copying a reference — and the
    bound means a merely *similar* reference can never be quietly accepted.
    """
    if a == b:
        return 0
    if abs(len(a) - len(b)) > maximum:
        return None
    rows = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        rows[i][0] = i
    for j in range(len(b) + 1):
        rows[0][j] = j
    for i in range(1, len(a) + 1):
        best = float("inf")
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            value = min(rows[i - 1][j] + 1, rows[i][j - 1] + 1, rows[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                value = min(value, rows[i - 2][j - 2] + 1)
            rows[i][j] = value
            best = min(best, value)
        if best > maximum:
            return None
    distance = rows[len(a)][len(b)]
    return None if distance > maximum else distance


@dataclass
class Window:
    start: str
    end: str


@dataclass
class Index(Generic[T]):
    """Exact-probe index over records that carry a date, an amount and a reference."""

    all: list[T]
    read_date: Callable[[T], str]
    by_id: dict[str, T] = field(default_factory=dict)
    by_reference: dict[str, list[T]] = field(default_factory=dict)
    by_amount: dict[int, list[T]] = field(default_factory=dict)
    sorted_rows: list[tuple[str, T]] = field(default_factory=list)

    def _lower_bound(self, day: str) -> int:
        low, high = 0, len(self.sorted_rows)
        while low < high:
            mid = (low + high) >> 1
            if self.sorted_rows[mid][0] < day:
                low = mid + 1
            else:
                high = mid
        return low

    def in_window(self, start: str, end: str) -> list[T]:
        out: list[T] = []
        i = self._lower_bound(start)
        while i < len(self.sorted_rows) and self.sorted_rows[i][0] <= end:
            out.append(self.sorted_rows[i][1])
            i += 1
        return out

    def _in_date(self, record: T, window: Window | None) -> bool:
        return window is None or (window.start <= self.read_date(record) <= window.end)

    def with_amount(self, amount: int, window: Window | None = None) -> list[T]:
        return [r for r in self.by_amount.get(amount, []) if self._in_date(r, window)]

    def near_amount(self, amount: int, slack: int, window: Window | None = None) -> list[T]:
        out: list[T] = []
        for delta in range(-slack, slack + 1):
            for record in self.by_amount.get(amount + delta, []):
                if self._in_date(record, window):
                    out.append(record)
        return out


def build_index(
    records: list[T],
    *,
    read_id: Callable[[T], str],
    read_date: Callable[[T], str],
    read_amount: Callable[[T], int],
    read_reference: Callable[[T], str],
) -> Index[T]:
    index: Index[T] = Index(all=records, read_date=read_date)
    for record in records:
        index.by_id[read_id(record)] = record
        reference = normalise_reference(read_reference(record))
        if reference != "":
            index.by_reference.setdefault(reference, []).append(record)
        index.by_amount.setdefault(read_amount(record), []).append(record)
    # Python's sort is stable, as is Array.prototype.sort: equal dates keep input order.
    index.sorted_rows = sorted(((read_date(r), r) for r in records), key=lambda row: row[0])
    return index


def index_bank(bank: list[BankCredit]) -> Index[BankCredit]:
    return build_index(
        bank,
        read_id=lambda c: c.id,
        read_date=lambda c: c.value_date,
        read_amount=lambda c: c.amount,
        read_reference=lambda c: c.reference,
    )


def index_settlements(settlements: list[Settlement]) -> Index[Settlement]:
    return build_index(
        settlements,
        read_id=lambda s: s.id,
        read_date=lambda s: s.settled_at,
        read_amount=lambda s: s.net,
        read_reference=lambda s: s.utr,
    )


def group_payments_by_payout_date(
    payments: list[Payment], config: Tolerances = TOLERANCES
) -> dict[str, list[Payment]]:
    out: dict[str, list[Payment]] = {}
    for payment in payments:
        out.setdefault(expected_settlement_date(payment.captured_at, config), []).append(payment)
    return out


def group_journals(ledger: list[LedgerEntry]) -> dict[str, list[LedgerEntry]]:
    out: dict[str, list[LedgerEntry]] = {}
    for line in ledger:
        out.setdefault(line.journal_id, []).append(line)
    return out


# ── The bounded structural search ────────────────────────────────────────────


@dataclass
class SubsetSearch(Generic[T]):
    subsets: list[list[T]]
    nodes: int
    # True when the search stopped early. "No combination explains this" and "I stopped
    # looking after 200,000 combinations" are different sentences.
    cap_hit: bool


def find_subsets(
    items: list[T],
    amount_of: Callable[[T], int],
    target: int,
    *,
    max_size: int,
    max_nodes: int,
    exact_size: int | None = None,
    limit: int = 2,
    required: list[T] | None = None,
) -> SubsetSearch[T]:
    """Subsets of ``items`` summing to exactly ``target``, bounded by size and node budget.

    Stops at ``limit + 1`` solutions: one subset that ties is a match, two is an ambiguity.
    """
    required = required or []
    required_ids = {id(item) for item in required}
    required_sum = sum(amount_of(item) for item in required)

    pool = [item for item in items if id(item) not in required_ids and amount_of(item) > 0]
    pool.sort(key=lambda item: -amount_of(item))  # descending, stable

    suffix = [0] * (len(pool) + 1)
    for i in range(len(pool) - 1, -1, -1):
        suffix[i] = suffix[i + 1] + amount_of(pool[i])

    remaining = target - required_sum
    subsets: list[list[T]] = []
    state = {"nodes": 0, "cap_hit": False}

    min_size = max(0, (exact_size or 0) - len(required))
    bound = min(max_size - len(required), len(pool))

    if remaining < 0 or bound < min_size:
        return SubsetSearch(subsets, 0, False)
    if remaining == 0 and min_size == 0:
        return SubsetSearch([[*required]], 0, False)

    chosen: list[T] = []

    def walk(start: int, left: int) -> None:
        if state["cap_hit"] or len(subsets) > limit:
            return
        state["nodes"] += 1
        if state["nodes"] > max_nodes:
            state["cap_hit"] = True
            return
        if left == 0:
            if exact_size is None or len(chosen) == min_size:
                subsets.append([*required, *chosen])
            return
        if len(chosen) >= bound:
            return
        if suffix[start] < left:
            return
        for i in range(start, len(pool)):
            amount = amount_of(pool[i])
            if amount > left:
                continue
            if exact_size is not None and len(pool) - i < min_size - len(chosen):
                break
            chosen.append(pool[i])
            walk(i + 1, left - amount)
            chosen.pop()
            if state["cap_hit"] or len(subsets) > limit:
                return

    walk(0, remaining)
    return SubsetSearch(subsets, state["nodes"], state["cap_hit"])


def partition_is_tractable(total: int, size: int, config: Tolerances = TOLERANCES) -> bool:
    """Whether choosing ``size`` of ``total`` is small enough to attempt at all."""
    limit = config.search.max_combinatorial_size
    return size <= limit or total - size <= limit or total <= limit * 4


def window_around(day: str, before: int, after: int) -> Window:
    return Window(add_days(day, -before), add_days(day, after))


__all__ = [
    "Index",
    "SubsetSearch",
    "Window",
    "build_index",
    "days_between",
    "edit_distance",
    "find_subsets",
    "group_journals",
    "group_payments_by_payout_date",
    "index_bank",
    "index_settlements",
    "normalise_reference",
    "partition_is_tractable",
    "window_around",
]
