import { useState } from "react";
import type { Ctx } from "../../app/App";
import { usePoll } from "../../hooks";
import { api } from "../../services/api";
import type { Finding } from "../../types";
import { Empty, ErrorLine, EvidenceRefs, SeverityPill, StatusPill, useAction } from "../../components/ui";
import type { CaseCtx } from "../cases/CaseView";

/* Stages 5 and 6 · discrepancy detection and human review. Each finding says what was
   detected, which records support it (document, page, line), how sure, and what is uncertain. */

export function FindingCard({ f, ctx, onChanged, showCase }: { f: Finding; ctx: Ctx; onChanged: () => void; showCase?: boolean }) {
  const [note, setNote] = useState("");
  const { busy, error, run } = useAction(ctx.toast);
  const serious = f.severity === "critical" || f.severity === "high";
  const canResolve = ctx.can("finding:review") && (!serious || ctx.can("decision:record"));
  const set = async (status: string) => {
    if (await run(() => api.reviewFinding(f.id, status, note), `Finding ${status}`)) onChanged();
  };
  return (
    <div className="card finding rise">
      <div className="title">
        <SeverityPill severity={f.severity} />
        <span className="alarm">{f.title}</span>
        <span className="state-col">
          {showCase && f.case_reference ? <a className="mono small" href={`#case/${f.case_id}/findings`}>{f.case_reference}</a> : null}
          <StatusPill status={f.status} />
        </span>
      </div>
      <div className="summary">{f.description}</div>
      <EvidenceRefs refs={f.evidence} caseId={f.case_id} />
      {f.uncertainty ? <div className="uncertain"><b>Uncertain:</b> {f.uncertainty}</div> : null}
      <div className="faint small" style={{ marginTop: 6 }}>
        confidence {Math.round(f.confidence * 100)}% · {f.source}{f.requires_professional_review ? " · requires professional review" : ""}
        {f.status_note ? ` · note: ${f.status_note}` : ""}
      </div>
      {ctx.can("finding:review") && f.status !== "dismissed" ? (
        <div className="review row">
          <input className="input" style={{ maxWidth: 360 }} placeholder="review note" value={note} onChange={(e) => setNote(e.target.value)} />
          {f.status === "open" ? <button className="btn small" disabled={busy} onClick={() => void set("acknowledged")}>Acknowledge</button> : null}
          {f.status !== "resolved" && canResolve ? <button className="btn small" disabled={busy || !note.trim()} onClick={() => void set("resolved")}>Resolve</button> : null}
          {f.status !== "open" ? <button className="btn ghost small" disabled={busy} onClick={() => void set("open")}>Reopen</button> : null}
          {ctx.can("action:propose") && f.status !== "resolved" ? (
            <button className="btn ghost small" disabled={busy || !note.trim()} title="Dismissal needs an attorney's approval" onClick={async () => { if (await run(() => api.propose(f.case_id, "finding.dismiss", { finding_id: f.id, reason: note }), "Dismissal proposed for approval")) onChanged(); }}>
              Propose dismissal
            </button>
          ) : null}
          <ErrorLine error={error} />
        </div>
      ) : null}
    </div>
  );
}

export function CaseFindings({ c }: { c: CaseCtx }) {
  const q = usePoll(() => api.caseFindings(c.caseId), 60_000, [c.caseId], true);
  const { busy, error, run } = useAction(c.toast);
  const findings = q.data?.findings ?? [];
  return (
    <div className="stack">
      <div className="row">
        {c.can("analysis:run") ? (
          <button className="btn primary" disabled={busy} onClick={async () => { const r = await run(() => api.detect(c.caseId)); if (r) { c.toast(`${r.outcome.created} new · ${r.outcome.updated} updated · ${r.outcome.closed} closed`); void q.refresh(); c.refresh(); } }}>
            Run discrepancy detection
          </button>
        ) : null}
        <span className="faint small">Checks ownership, identifiers, taxes, liens and payments against each other. Re-running keeps your reviews.</span>
      </div>
      <ErrorLine error={error} />
      {findings.length === 0 && !q.loading ? <Empty title="No findings.">{c.detail.case.detection_ran_at ? "Nothing disagrees in the current evidence." : "Detection has not been run on this case."}</Empty> : null}
      <div className="feed">
        {findings.map((f) => <FindingCard key={f.id} f={f} ctx={c} onChanged={() => { void q.refresh(); c.refresh(); }} />)}
      </div>
    </div>
  );
}

export function FindingsPage({ ctx }: { ctx: Ctx }) {
  const [status, setStatus] = useState("open");
  const q = usePoll(() => api.findings(status), 30_000, [status], true);
  return (
    <main className="main route-in">
      <div className="page-h">
        <h1 className="display">
          Where the records <em>disagree.</em>
        </h1>
        <p className="lede">Every finding across your cases, most serious first. None of them is a legal determination.</p>
      </div>
      <div className="chips" role="group">
        {["open", "acknowledged", "resolved", "dismissed"].map((s) => (
          <button key={s} type="button" className={`chip-btn${status === s ? " on" : ""}`} onClick={() => setStatus(s)}>{s}</button>
        ))}
      </div>
      <ErrorLine error={q.error} />
      {q.data && !q.data.findings.length ? <Empty title="Nothing here." /> : null}
      <div className="feed">
        {(q.data?.findings ?? []).map((f) => <FindingCard key={f.id} f={f} ctx={ctx} onChanged={() => void q.refresh()} showCase />)}
      </div>
    </main>
  );
}
