import { useState } from "react";
import type { Ctx } from "../../app/App";
import { formatStamp, usePoll } from "../../hooks";
import { api } from "../../services/api";
import type { Action } from "../../types";
import { Empty, ErrorLine, StatusPill, useAction } from "../../components/ui";

/* The approval queue: Beacon's "approve fix 1", for legal workflow actions. */

function ApprovalCard({ a, ctx, onDone }: { a: Action; ctx: Ctx; onDone: () => void }) {
  const [phrase, setPhrase] = useState("");
  const [reason, setReason] = useState("");
  const { busy, error, run } = useAction(ctx.toast);
  const mine = a.proposed_by_id === ctx.me.id;
  const canApprove = (a.approver_roles ?? []).includes(ctx.me.role) && !mine;
  return (
    <div className="contract">
      <div className="row">
        <a className="mono small" href={`#case/${a.case_id}/workflow`}>{a.case_reference}</a>
        <StatusPill status={a.status} />
        <span className="faint small">{a.reversible ? "reversible" : "not reversible"}</span>
      </div>
      <div className="contract-alarm">action {a.seq}: {a.summary ?? a.action}</div>
      <div className="mono small dim">{a.action}</div>
      <dl className="kv">
        {Object.entries(a.params).filter(([, v]) => v !== "").map(([k, v]) => (
          <span key={k} style={{ display: "contents" }}><dt>{k}</dt><dd>{String(v)}</dd></span>
        ))}
      </dl>
      <div className="faint small">
        {a.case_title} · proposed by {a.proposed_by_name ?? "—"} · {formatStamp(a.created_at)} · expires {formatStamp(a.expires_at)}
        {a.rationale ? ` · ${a.rationale}` : ""}
      </div>
      {a.decision ? <div className="quote">“{a.decision.statement}” — {a.decision.by}</div> : null}
      {a.status === "proposed" ? (
        <div className="stack" style={{ marginTop: 10 }}>
          {canApprove ? (
            <div className="phrase-box">
              <span className="small">type <span className="phrase">{a.confirmation_phrase}</span></span>
              <input className="input" value={phrase} onChange={(e) => setPhrase(e.target.value)} placeholder={a.confirmation_phrase} aria-label="Confirmation phrase" />
              <button className="btn primary" disabled={busy || !phrase.trim()} onClick={async () => { if (await run(() => api.approve(a.id, phrase), `Action ${a.seq} approved and executed`)) onDone(); }}>Approve</button>
            </div>
          ) : (
            <p className="faint small">{mine ? "You proposed this; another approver must decide it." : `Approved by: ${(a.approver_roles ?? []).join(", ")}.`}</p>
          )}
          {canApprove || mine ? (
            <div className="phrase-box">
              <input className="input" value={reason} onChange={(e) => setReason(e.target.value)} placeholder={mine ? "why withdraw" : "why reject"} />
              <button className="btn danger" disabled={busy || !reason.trim()} onClick={async () => { if (await run(() => api.reject(a.id, reason), mine ? "Withdrawn" : "Rejected")) onDone(); }}>{mine ? "Withdraw" : "Reject"}</button>
            </div>
          ) : null}
          <ErrorLine error={error} />
        </div>
      ) : null}
    </div>
  );
}

export function ApprovalsPage({ ctx, onChanged }: { ctx: Ctx; onChanged: () => void }) {
  const [status, setStatus] = useState("proposed");
  const q = usePoll(() => api.approvals(status), 15_000, [status], true);
  const done = () => { void q.refresh(); onChanged(); };
  return (
    <main className="main route-in">
      <div className="page-h">
        <h1 className="display">
          Approvals, <em>in your own words.</em>
        </h1>
        <p className="lede">Nothing on this list changes a case until a qualified approver types its exact phrase. Each action executes once, and the words are kept.</p>
      </div>
      <div className="chips">
        {["proposed", "executed", "rejected", "withdrawn", "failed"].map((s) => (
          <button key={s} type="button" className={`chip-btn${status === s ? " on" : ""}`} onClick={() => setStatus(s)}>{s}</button>
        ))}
      </div>
      <ErrorLine error={q.error} />
      {q.data && !q.data.actions.length ? <Empty title="Nothing waiting.">Actions proposed on a case's Workflow tab appear here.</Empty> : null}
      <div className="grid2">
        {(q.data?.actions ?? []).map((a) => <ApprovalCard key={a.id} a={a} ctx={ctx} onDone={done} />)}
      </div>
    </main>
  );
}
