"""Money as integer minor units (ported from Magpie ``lib/recon/money.ts``).

Every amount in the reconciliation engine is an ``int`` count of minor units (paise for
the settlement lanes, cents for the property-case lanes). Floats never carry money here:
a reconciliation is a pile of additions asking whether two sides are equal, the single
worst place to keep a float.
"""

from __future__ import annotations

import math

Paise = int


def js_round(value: float) -> int:
    """JavaScript ``Math.round``: halves round toward +infinity (Python rounds to even)."""
    return math.floor(value + 0.5)


def rupees(amount: float) -> Paise:
    """₹1,234.56 → 123456. For authoring fixtures, never on parsed input."""
    return js_round(amount * 100)


def pct(amount: Paise, percent: float) -> Paise:
    """Percentage of an amount, rounded to the paisa — the rounding a gateway does."""
    return js_round((amount * percent) / 100)


def total(values: list[Paise]) -> Paise:
    return sum(values)


def to_decimal(paise: Paise) -> str:
    """``123456`` → ``"1234.56"``. The plain decimal a gateway export uses."""
    sign = "-" if paise < 0 else ""
    magnitude = abs(paise)
    return f"{sign}{magnitude // 100}.{magnitude % 100:02d}"


def _group_indian(digits: str) -> str:
    """Last three digits together, then groups of two: ``1234567`` → ``12,34,567``."""
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    groups: list[str] = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join(groups) + "," + tail


def to_indian_decimal(paise: Paise) -> str:
    """``123456`` → ``"1,234.56"``; ``12345678`` → ``"1,23,456.78"`` (Indian grouping)."""
    sign = "-" if paise < 0 else ""
    magnitude = abs(paise)
    return f"{sign}{_group_indian(str(magnitude // 100))}.{magnitude % 100:02d}"


def indian_int(value: int) -> str:
    """``Number.toLocaleString("en-IN")`` for a non-negative integer: ``200000`` → ``2,00,000``."""
    sign = "-" if value < 0 else ""
    return sign + _group_indian(str(abs(value)))


def to_usd(cents: int) -> str:
    """``123456`` → ``"$1,234.56"``; negative amounts keep a leading minus."""
    sign = "-" if cents < 0 else ""
    magnitude = abs(cents)
    return f"{sign}${magnitude // 100:,}.{magnitude % 100:02d}"
