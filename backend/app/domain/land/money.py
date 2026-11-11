"""Following the money: what a family spends, in rupees and days, to read and fix its own records.

Every payment is logged with who received it (an office, a document writer, an advocate, a
middleman, a moneylender) and whether there is a receipt. The summary compares what was paid
for each service with the indicative official fee for it, and prices the informal credit the
family took because the land could not be pledged: the interest actually paid against what the
same principal would have cost from the formal lender.
"""

from __future__ import annotations

from collections import defaultdict
from statistics import median
from typing import Any

from .regions import FORMAL_RATES, Region, services

CATEGORIES = (
    "official_fee", "document_writer", "advocate", "travel", "lost_wages", "unreceipted_payment",
    "survey_fee", "stamp_duty", "informal_loan", "other",
)
PAYEES = ("office", "document_writer", "advocate", "middleman", "lender", "other")


def summarise(entries: list[Any], region: Region, formal_lender: str = "bank_crop_loan") -> dict[str, Any]:
    svc = services(region)
    total = sum(e.amount_inr for e in entries if e.category != "informal_loan")
    days = sum(e.days_spent for e in entries)
    by_cat: dict[str, int] = defaultdict(int)
    by_payee: dict[str, int] = defaultdict(int)
    receipted = unreceipted = 0
    paid_for: dict[str, int] = defaultdict(int)
    for e in entries:
        if e.category == "informal_loan":
            continue
        by_cat[e.category] += e.amount_inr
        by_payee[e.payee_kind or "other"] += e.amount_inr
        if e.has_receipt:
            receipted += e.amount_inr
        else:
            unreceipted += e.amount_inr
        if e.service:
            paid_for[e.service] += e.amount_inr
    excess = []
    for sid, paid in sorted(paid_for.items()):
        if sid in svc and paid > svc[sid].fee_inr:
            excess.append({"service": sid, "name": svc[sid].name, "paid_inr": paid, "official_fee_inr": svc[sid].fee_inr, "excess_inr": paid - svc[sid].fee_inr})
    rate = FORMAL_RATES.get(formal_lender, 0.07)
    loans = []
    for e in entries:
        if e.category != "informal_loan" or not isinstance(e.extra, dict):
            continue
        principal = int(e.extra.get("principal_inr") or 0)
        monthly = float(e.extra.get("monthly_rate_pct") or 0) / 100
        months = float(e.extra.get("months") or 0)
        paid = int(round(principal * monthly * months))
        formal = int(round(principal * rate * months / 12))
        loans.append({
            "id": e.id, "principal_inr": principal, "monthly_rate_pct": monthly * 100, "annual_rate_pct": round(monthly * 1200, 1), "months": months,
            "interest_paid_inr": paid, "formal_interest_inr": formal, "premium_inr": paid - formal, "lender": e.notes or e.payee_kind,
        })
    reading = sum(e.amount_inr for e in entries if e.category in ("document_writer", "unreceipted_payment") or e.payee_kind == "middleman")
    return {
        "total_inr": total, "days": round(days, 1), "by_category": dict(by_cat), "by_payee": dict(by_payee),
        "receipted_inr": receipted, "unreceipted_inr": unreceipted,
        "unreceipted_share": round(unreceipted / total, 3) if total else 0.0,
        "excess_over_official": excess, "excess_total_inr": sum(x["excess_inr"] for x in excess),
        "informal_loans": loans, "informal_premium_inr": sum(x["premium_inr"] for x in loans), "formal_rate_pct": rate * 100,
        "paid_to_intermediaries_inr": reading,
        "note": "Official fees are indicative defaults; set the district's actual fees before quoting an excess.",
    }


def med(values: list[float]) -> float | None:
    vals = [v for v in values if v is not None]
    return float(median(vals)) if vals else None
