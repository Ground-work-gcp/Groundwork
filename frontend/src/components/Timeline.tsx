import { formatStamp } from "../hooks";
import type { AuditEvent } from "../types";

/* Beacon's incident timeline, now reading the case's audit chain. */

const LABELS: Record<string, string> = {
  "case.created": "Case opened",
  "case.updated": "Case details updated",
  "case.stage_changed": "Stage changed",
  "case.report_generated": "Report generated",
  "case.question_answered": "Evidence question answered",
  "party.added": "Party added",
  "party.updated": "Party updated",
  "party.removed": "Party removed",
  "property.updated": "Property updated",
  "document.uploaded": "Document stored",
  "document.extracted": "Document extracted",
  "document.reprocessed": "Document re-extracted",
  "document.downloaded": "Document downloaded",
  "fact.added": "Fact added by hand",
  "fact.reviewed": "Fact reviewed",
  "ownership.reconstructed": "Ownership history rebuilt",
  "ownership.event_added": "Ownership event added",
  "ownership.event_removed": "Ownership event removed",
  "obligations.derived_from_evidence": "Obligations derived from evidence",
  "obligation.added": "Obligation added",
  "obligation.updated": "Obligation updated",
  "obligation.removed": "Obligation removed",
  "transaction.added": "Payment added",
  "transactions.imported": "Payments imported",
  "reconciliation.run": "Reconciliation run",
  "reconciliation.reviewed": "Reconciliation reviewed",
  "risks.detected": "Discrepancy detection run",
  "finding.reviewed": "Finding reviewed",
  "task.created": "Task created",
  "task.updated": "Task updated",
  "comment.added": "Comment",
  "decision.recorded": "Decision recorded",
  "action.proposed": "Action proposed",
  "action.approved": "Action approved",
  "action.executed": "Action executed",
  "action.failed": "Action failed",
  "action.rejected": "Action rejected",
  "action.withdrawn": "Action withdrawn",
};

function tone(e: AuditEvent): string {
  if (e.event === "action.executed" || e.event === "action.approved" || e.event === "decision.recorded") return "ok";
  if (e.event === "action.failed" || e.event === "action.rejected") return "bad";
  if (e.event === "action.proposed" || e.event === "risks.detected") return "warn";
  if (e.event.startsWith("reconciliation")) return "moon";
  return "";
}

function detail(e: AuditEvent): string {
  const d = (e.detail ?? {}) as Record<string, unknown>;
  const after = (d.after ?? {}) as { status?: string; value?: string };
  const before = (d.before ?? {}) as { value?: string };
  switch (e.event) {
    case "case.stage_changed":
      return `${d.from} → ${d.to}`;
    case "document.uploaded":
      return `${d.filename} · sha256 ${String(d.sha256 ?? "").slice(0, 12)}…`;
    case "document.extracted":
      return `${d.doc_type} · ${d.pages} page(s) · ${d.facts} fact(s) · ${d.engine}`;
    case "fact.reviewed":
      return `${before.value ?? ""} → ${after.status ?? ""} ${after.value ?? ""}`;
    case "ownership.reconstructed":
      return `${d.events} event(s) · ${d.issues} issue(s)`;
    case "risks.detected":
      return `${d.created} new · ${d.updated} updated · ${d.closed} closed`;
    case "finding.reviewed":
      return `${d.title}: ${d.from} → ${d.to}${d.note ? ` — ${d.note}` : ""}`;
    case "action.proposed":
      return `action ${d.seq}: ${d.summary}`;
    case "action.approved":
      return `action ${d.seq} · “${d.statement}”`;
    case "action.executed":
    case "action.failed":
      return `action ${d.seq} · ${d.action}`;
    case "action.rejected":
    case "action.withdrawn":
      return `action ${d.seq} · ${d.reason}`;
    case "transactions.imported":
      return `${d.imported} imported · ${d.rejected} rejected`;
    case "decision.recorded":
      return `${d.title}: ${d.decision}`;
    default:
      return "";
  }
}

export function Timeline({ events }: { events: AuditEvent[] }) {
  if (!events.length) return <p className="dim small">Nothing recorded yet.</p>;
  return (
    <ul className="timeline">
      {events.map((e) => (
        <li key={e.id} className={tone(e)}>
          <div className="t" title={e.occurred_at}>{formatStamp(e.occurred_at)}</div>
          <div className="e">
            {LABELS[e.event] ?? e.event}
            <span className="faint small"> · {e.actor_name ?? e.actor_kind}</span>
          </div>
          {detail(e) ? <div className="d">{detail(e)}</div> : null}
        </li>
      ))}
    </ul>
  );
}
