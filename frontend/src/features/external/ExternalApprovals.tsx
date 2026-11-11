import { useState } from "react";
import type { Ctx } from "../../app/App";
import { formatStamp, usePoll } from "../../hooks";
import { api } from "../../services/api";
import type * as T from "../../types";
import { Empty, ErrorLine, Panel, label, useAction } from "../../components/ui";
import type { CaseCtx } from "../cases/CaseView";

/* Cross-organisation approvals over Canton. Statuses come from the ledger projection: "submitted"
   means the create is committed; "approved" only once Legal Aid's own participant shows the decision. */

const TONE: Record<string, string> = {
  approved: "green", rejected: "red", changes_requested: "amber", cancelled: "dim", expired: "dim", submission_failed: "red", sync_delayed: "amber",
  awaiting_review: "lilac", submitted: "lilac", decision_submitted: "lilac", pending_submission: "amber", draft: "dim", cancel_submitted: "amber",
};
const STEPS = [
  ["draft", "Proposed in Groundwork"], ["pending_submission", "Second person approved"], ["submitted", "Committed on Legal Aid participant"],
  ["awaiting_review", "Received by registry participant"], ["decision_submitted", "Registry decision committed"], ["decided", "Decision seen by Legal Aid"],
] as const;
const ORDER = ["draft", "pending_submission", "submitted", "awaiting_review", "decision_submitted", "decided"];

export function NetworkBadge({ network }: { network: string }) {
  return <span className={`pill net-${network}`} title="Canton network this ledger reference belongs to">Canton {network.toUpperCase()}</span>;
}
const short = (v: string) => (v ? `${v.slice(0, 10)}…${v.slice(-6)}` : "—");

function Progress({ a }: { a: T.ExternalApproval }) {
  const decided = ["approved", "rejected", "changes_requested"].includes(a.status);
  const at = decided ? 5 : ORDER.indexOf(a.status);
  return (
    <ol className="flow-chips">
      {STEPS.map(([k, l], i) => <li key={k} className={i <= at ? "on" : ""}>{l}</li>)}
    </ol>
  );
}

function LedgerRefs({ a }: { a: T.ExternalApproval }) {
  return (
    <dl className="kv-grid small mono">
      <dt>proposal</dt><dd>{a.proposal_id}</dd>
      <dt>opaque case ref</dt><dd>{a.case_reference}</dd>
      <dt>evidence commitment</dt><dd title={a.evidence_commitment}>{short(a.evidence_commitment)} {a.commitment_verified ? "✓ recomputed" : "✗ mismatch"}</dd>
      <dt>request contract</dt><dd title={a.ledger.request_contract_id}>{short(a.ledger.request_contract_id)}</dd>
      <dt>request update</dt><dd title={a.ledger.request_update_id}>{short(a.ledger.request_update_id)}</dd>
      <dt>decision contract</dt><dd title={a.ledger.decision_contract_id}>{short(a.ledger.decision_contract_id)}</dd>
      <dt>decision update</dt><dd title={a.ledger.decision_update_id}>{short(a.ledger.decision_update_id)}</dd>
      <dt>package</dt><dd title={a.ledger.package_id}>{short(a.ledger.package_id)}</dd>
      <dt>synchronizer</dt><dd title={a.ledger.synchronizer_id}>{short(a.ledger.synchronizer_id)}</dd>
    </dl>
  );
}

function History({ id }: { id: string }) {
  const q = usePoll(() => api.externalEvents(id), 15_000, [id], true);
  return (
    <ul className="ev-list small">
      {(q.data?.events ?? []).map((e, i) => (
        <li key={i}><span className="mono">{formatStamp(e.at)}</span> <b>{label(e.kind)}</b>{e.participant ? ` · ${label(e.participant)} participant` : ""}{e.update_id ? <span className="mono dim"> · update {short(e.update_id)}</span> : null}{e.offset != null ? <span className="mono dim"> · offset {e.offset}</span> : null}</li>
      ))}
    </ul>
  );
}

function Card({ a, children }: { a: T.ExternalApproval; children?: React.ReactNode }) {
  return (
    <div className={`file-card sig-${a.status === "approved" ? "done" : ["rejected", "submission_failed"].includes(a.status) ? "overdue" : "at_risk"}`}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div><b>{label(a.action_type.replace(/([A-Z])/g, " $1").trim())}</b> <span className="small dim">· v{a.case_version} · {a.requesting_organization} → {a.reviewer_organization}</span></div>
        <span className="row"><NetworkBadge network={a.network} /><span className={`pill ${TONE[a.status] ?? "dim"}`}>{a.status_label}</span></span>
      </div>
      <Progress a={a} />
      {a.error ? <div className="fix red small">{a.error}</div> : null}
      {a.decision_reason_code ? <div className="small">Decision code: <b>{a.decision_reason_code}</b>{a.requested_evidence.length ? ` · requested: ${a.requested_evidence.join(", ")}` : ""}{a.decided_at ? ` · ledger time ${formatStamp(a.decided_at)}` : ""}</div> : null}
      <details className="small"><summary>Ledger references and history</summary><LedgerRefs a={a} /><History id={a.id} /></details>
      {children}
    </div>
  );
}

export function CaseExternalApprovals({ c }: { c: CaseCtx }) {
  const q = usePoll(() => api.externalApprovals(c.caseId), 10_000, [c.caseId], true);
  const [action, setAction] = useState("ConfirmSuccession");
  const [why, setWhy] = useState("");
  const { busy, error, run } = useAction(c.toast);
  const d = q.data;
  if (!d) return <p className="faint">{q.error ?? "Loading…"}</p>;
  if (!d.canton.enabled) return <Empty title="Canton is not enabled on this deployment.">External approvals need GROUNDWORK_CANTON_ENABLED and participant credentials — see docs/canton.</Empty>;
  const open = d.approvals.some((a) => !["approved", "rejected", "changes_requested", "cancelled", "expired", "submission_failed"].includes(a.status));
  return (
    <div className="split">
      <div className="stack">
        {d.approvals.length ? d.approvals.map((a) => (
          <Card key={a.id} a={a}>
            {a.status === "draft" ? <p className="small">Waiting for a second Groundwork approver — see <a href="#approvals">Approvals</a>. Nothing has been sent to the ledger yet.</p> : null}
            {["submitted", "awaiting_review"].includes(a.status) && c.can("action:approve") ? (
              <button className="btn ghost small" disabled={busy} onClick={async () => { if (await run(() => api.cancelExternal(a.id), "Cancellation submitted")) void q.refresh(); }}>Cancel request</button>
            ) : null}
          </Card>
        )) : <Empty title="No external approvals yet.">Once the evidence is reviewed, propose the ownership resolution to the registry.</Empty>}
      </div>
      <div className="stack">
        <Panel title="Send to the registry" meta={<NetworkBadge network={d.canton.network} />}>
          {d.canton.role !== "legal_aid" ? <p className="faint small">This organisation is not bound to the Legal Aid party on {d.canton.network}.</p> : c.can("action:propose") ? (
            <form className="form" onSubmit={async (e) => { e.preventDefault(); if (await run(() => api.proposeExternal(c.caseId, { action_type: action, rationale: why }), "Proposed — a second person must approve before it is sent")) { setWhy(""); void q.refresh(); c.refresh(); } }}>
              <label>Proposed action<select className="input" value={action} onChange={(e) => setAction(e.target.value)}>
                {[["ConfirmSuccession", "Confirm succession (record the heirs)"], ["RecordMutation", "Record mutation / transfer"], ["CorrectRecord", "Correct the record"], ["ConfirmOwnership", "Confirm current ownership"]].map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select></label>
              <label>Rationale (stays in Groundwork)<input className="input" value={why} onChange={(e) => setWhy(e.target.value)} /></label>
              <button className="btn primary" disabled={busy || open}>{open ? "A request is already in progress" : "Propose to the registry"}</button>
              <ErrorLine error={error} />
            </form>
          ) : null}
          <p className="faint small">Only salted commitments, an opaque case reference and decision codes go on the ledger. Documents and facts stay in Groundwork and are shared with the registry off-ledger; it recomputes the commitment before it may decide.</p>
          <button className="btn ghost small" onClick={async () => { if (await run(() => api.syncExternal(), "Synchronised with the ledger")) void q.refresh(); }}>Sync with ledger now</button>
        </Panel>
      </div>
    </div>
  );
}

export function RegistryPortal({ ctx }: { ctx: Ctx }) {
  const q = usePoll(api.registryPending, 10_000, [], true);
  const st = usePoll(api.cantonStatus, 30_000, [], true);
  const [form, setForm] = useState<Record<string, { reason: string; requested: string[] }>>({});
  const { busy, error, run } = useAction(ctx.toast);
  const d = q.data;
  return (
    <main className="main route-in">
      <div className="page-h">
        <span className="eyebrow">Registry portal {st.data ? <NetworkBadge network={st.data.network} /> : null}</span>
        <h1 className="display">Requests addressed to your registry.</h1>
        <p className="lede">Each request arrives through your own Canton participant. Your decision is signed by your registry party and committed on the ledger; the requesting organisation reads it from its own participant.</p>
      </div>
      {q.error ? <Empty title="This organisation is not a registry on this network.">A platform administrator binds registry organisations to the registry party.</Empty> : !d ? <p className="faint">Loading…</p> : (
        <div className="stack">
          <ErrorLine error={error} />
          {d.approvals.length ? d.approvals.map((a) => {
            const f = form[a.id] ?? { reason: "EvidenceSufficient", requested: [] };
            const set = (v: Partial<typeof f>) => setForm({ ...form, [a.id]: { ...f, ...v } });
            return (
              <Card key={a.id} a={a}>
                <details className="small" open={a.status === "awaiting_review"}>
                  <summary>Evidence shared with you (off-ledger)</summary>
                  <table className="t small"><tbody>
                    {a.manifest?.documents.map((doc) => <tr key={doc.document_id}><td>{doc.doc_type}</td><td>{doc.filename}</td><td className="mono">{doc.sha256.slice(0, 16)}…</td><td>{doc.pages} p.</td></tr>)}
                  </tbody></table>
                  <table className="t small"><tbody>
                    {a.manifest?.reviewed_facts.map((x) => <tr key={x.fact_id}><td>{label(x.fact_type)}</td><td>{x.value}</td><td className="dim">{x.status}</td></tr>)}
                  </tbody></table>
                  <p className={a.commitment_verified ? "small" : "small err"}>{a.commitment_verified ? "✓ Recomputed from this evidence and salt: matches the commitment on the ledger." : "✗ This evidence does not match the ledger commitment — do not approve."}</p>
                </details>
                {a.status === "awaiting_review" && d.can_decide ? (
                  <div className="row">
                    <select className="input" value={f.reason} onChange={(e) => set({ reason: e.target.value })} style={{ maxWidth: 230 }}>
                      {["EvidenceSufficient", "EvidenceInsufficient", "ConflictingClaim", "JurisdictionMismatch", "MissingDocument", "OtherSeeOffLedgerNote"].map((r) => <option key={r} value={r}>{label(r.replace(/([A-Z])/g, " $1").trim())}</option>)}
                    </select>
                    <button className="btn primary small" disabled={busy || !a.commitment_verified} onClick={async () => { if (await run(() => api.registryDecide(a.id, "approve", { reason_code: "EvidenceSufficient" }), "Approval committed by your participant")) void q.refresh(); }}>Approve</button>
                    <button className="btn small" disabled={busy || f.reason === "EvidenceSufficient"} onClick={async () => { if (await run(() => api.registryDecide(a.id, "reject", { reason_code: f.reason }), "Rejection committed")) void q.refresh(); }}>Reject</button>
                    <select className="input" multiple value={f.requested} onChange={(e) => set({ requested: Array.from(e.target.selectedOptions).map((o) => o.value) })} style={{ maxWidth: 220, height: 64 }} aria-label="evidence to request">
                      {["DeathCertificate", "LegalHeirCertificate", "ConsentOfCoHeirs", "EncumbranceCertificate", "SurveySketch", "TitleDeed", "CourtOrder", "IdentityProof"].map((r) => <option key={r} value={r}>{r}</option>)}
                    </select>
                    <button className="btn ghost small" disabled={busy || !f.requested.length} onClick={async () => { if (await run(() => api.registryDecide(a.id, "request-changes", { reason_code: "MissingDocument", requested: f.requested }), "Request for evidence committed")) void q.refresh(); }}>Request evidence</button>
                  </div>
                ) : a.status === "awaiting_review" ? <p className="faint small">Only registry attorneys or admins can decide.</p> : null}
              </Card>
            );
          }) : <Empty title="No requests.">New proposals appear here once your participant has received them.</Empty>}
        </div>
      )}
    </main>
  );
}
