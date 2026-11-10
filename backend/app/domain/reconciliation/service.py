"""Runs, persists and reviews reconciliations. The matcher decides; this module records."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.database.models import (
    Case,
    FinancialObligation,
    ReconciliationResult,
    ReconciliationRun,
    Transaction,
    User,
    utcnow,
)

from .engine import SOURCE_NAMES, MatchInput, ingest_sources, run_match
from .engine.cash import build_cash_series
from .engine.queue import apply_queue_command
from .engine.tolerance import tolerances_as_dict
from .property_lane import PROPERTY_TOLERANCES, ObligationRecord, PaymentRecord, reconcile_property_case


def _persist(db: Session, run: ReconciliationRun, results: list[dict[str, Any]]) -> None:
    for seq, item in enumerate(results):
        db.add(
            ReconciliationResult(
                run_id=run.id,
                seq=seq,
                lane=item["lane"],
                tier=item["tier"],
                rule=item["rule"],
                outcome=item["outcome"],
                confidence=float(item["confidence"]),
                left_ids=item["left"],
                right_ids=item["right"],
                inputs=item["inputs"],
                evidence=item["evidence"],
                failure_class=item["class"],
                amount_cents=int(item["amount"]),
                cap_hit=bool(item.get("capHit")),
            )
        )


def run_case(db: Session, case: Case, actor: User) -> ReconciliationRun:
    obligations = list(db.scalars(select(FinancialObligation).where(FinancialObligation.case_id == case.id)))
    transactions = list(db.scalars(select(Transaction).where(Transaction.case_id == case.id)))
    records = [
        ObligationRecord(
            id=o.id,
            obligation_type=o.obligation_type,
            reference=o.reference,
            amount=o.amount_cents,
            due_date=o.due_date or o.assessed_date or "1900-01-01",
            debtor=o.debtor_name,
            creditor=o.creditor,
            label=f"{o.obligation_type.replace('_', ' ')} {o.reference or ''}".strip()
            + (f" ({o.tax_year})" if o.tax_year else ""),
        )
        for o in obligations
    ]
    payments = [
        PaymentRecord(id=t.id, date=t.txn_date, reference=t.reference, description=t.description, amount=t.amount_cents, payer=t.payer)
        for t in transactions
    ]
    outcome = reconcile_property_case(records, payments)
    run = ReconciliationRun(
        organization_id=case.organization_id,
        case_id=case.id,
        kind="property_case",
        stats={k: v for k, v in outcome.stats.items() if k != "linkStates"},
        tolerances=PROPERTY_TOLERANCES.as_dict(),
        created_by_id=actor.id,
    )
    db.add(run)
    db.flush()
    _persist(db, run, outcome.results)

    # Obligation status is derived state, recomputed on every run.
    states = outcome.stats["linkStates"]
    for obligation in obligations:
        if obligation.status == "disputed":
            continue
        state = states.get(obligation.id)
        auto = any(r["outcome"] == "AUTO_MATCHED" and obligation.id in r["left"] for r in outcome.results)
        obligation.status = "paid" if state == "paid" and auto else "partial" if state == "partial" else "open"
    run.summary = summary(db, case, run)
    db.flush()
    return run


def latest_run(db: Session, case: Case) -> ReconciliationRun | None:
    return db.scalar(
        select(ReconciliationRun)
        .where(ReconciliationRun.case_id == case.id, ReconciliationRun.kind == "property_case")
        .order_by(ReconciliationRun.created_at.desc())
        .limit(1)
    )


def results_of(db: Session, run: ReconciliationRun) -> list[ReconciliationResult]:
    return list(db.scalars(select(ReconciliationResult).where(ReconciliationResult.run_id == run.id).order_by(ReconciliationResult.seq)))


def summary(db: Session, case: Case, run: ReconciliationRun | None) -> dict[str, Any]:
    """Magpie's three-line honesty rule, for a case: confirmed, outstanding, unverified."""
    obligations = list(db.scalars(select(FinancialObligation).where(FinancialObligation.case_id == case.id)))
    total = sum(o.amount_cents for o in obligations)
    out = {"obligations_total_cents": total, "confirmed_paid_cents": 0, "outstanding_cents": 0, "unverified_cents": 0, "obligation_count": len(obligations)}
    if run is None:
        out["outstanding_cents"] = total
        out["unverified_cents"] = total
        return out
    by_id = {o.id: o for o in obligations}
    for result in results_of(db, run):
        if result.lane != "OBLIGATION_TO_PAYMENT":
            continue
        obligation = by_id.get(result.left_ids[0]) if result.left_ids else None
        if result.outcome == "AUTO_MATCHED" and result.review_status != "rejected" or result.review_status == "accepted" and result.outcome == "PROPOSED":
            if obligation is not None:
                out["confirmed_paid_cents"] += obligation.amount_cents
        elif result.rule in ("T4_UNPAID_OBLIGATION",):
            out["outstanding_cents"] += result.amount_cents
        elif result.rule == "T2_UNDERPAID":
            out["outstanding_cents"] += result.amount_cents
            if obligation is not None:
                out["confirmed_paid_cents"] += obligation.amount_cents - result.amount_cents
        elif result.outcome == "PROPOSED" and obligation is not None and result.review_status == "open" or result.rule == "T2_AMOUNT_MISMATCH" and obligation is not None:
            out["unverified_cents"] += obligation.amount_cents
    return out


def apply_review(db: Session, run: ReconciliationRun, command: dict[str, Any], actor: User) -> dict[str, Any]:
    """Accept/reject proposals (or restore them), returning the inverse command for undo."""
    rows = {r.id: r for r in results_of(db, run)}
    ids = list(command.get("ids", [])) + list((command.get("states") or {}).keys())
    unknown = [i for i in ids if i not in rows]
    if unknown:
        raise ValueError(f"results not in this run: {unknown[:3]}")
    for entry_id in ids:
        if rows[entry_id].outcome == "AUTO_MATCHED" and command.get("type") == "Resolve" and command.get("to") == "accepted":
            raise ValueError("auto-matched results are already applied")
    state = {r.id: r.review_status for r in rows.values() if r.review_status != "open"}
    outcome = apply_queue_command(state, {**command, "scope": command.get("scope") or "reconciliation"})
    now = utcnow()
    for entry_id in ids:
        row = rows[entry_id]
        row.review_status = outcome["state"].get(entry_id, "open")
        row.reviewed_by_id = actor.id if row.review_status != "open" else None
        row.reviewed_at = now if row.review_status != "open" else None
    db.flush()
    return {"label": outcome["label"], "inverse": outcome["inverse"]}


def run_settlement_batch(db: Session, organization_id: str, sources: dict[str, str | None], actor: User) -> dict[str, Any]:
    """Magpie's original seven-source settlement reconciliation, unchanged (internal tool)."""
    batch = ingest_sources({name: sources.get(name) for name in SOURCE_NAMES})
    run_out = run_match(
        MatchInput(
            payments=batch.payments,
            settlements=batch.settlements,
            recon=batch.recon,
            bank=batch.bank,
            ledger=batch.ledger,
            chargebacks=batch.chargebacks,
        )
    )
    queue = sorted((r for r in run_out.results if r["outcome"] != "AUTO_MATCHED"), key=lambda r: -abs(r["amount"]))
    cash = (
        build_cash_series(batch.payments, batch.settlements, batch.bank, run_out.results, queue)
        if (batch.bank or batch.settlements or batch.payments)
        else None
    )
    run = ReconciliationRun(
        organization_id=organization_id,
        case_id=None,
        kind="settlement_batch",
        stats={k: v for k, v in run_out.stats.items() if k != "elapsedMs"} | {"elapsedMs": round(run_out.stats["elapsedMs"], 2)},
        tolerances=tolerances_as_dict(),
        summary={"cash": cash, "rejections": [r.to_dict() for r in batch.rejections], "files": [f.to_dict() for f in batch.files]},
        created_by_id=actor.id,
    )
    db.add(run)
    db.flush()
    _persist(db, run, run_out.results)
    db.flush()
    return {"run_id": run.id, "stats": run.stats, "queue": queue[:200], "cash": cash, "rejections": run.summary["rejections"], "files": run.summary["files"]}
