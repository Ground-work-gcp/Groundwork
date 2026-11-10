import { useState } from "react";
import { usePoll } from "../../hooks";
import { api } from "../../services/api";
import type { QueueCommand, ReconResult } from "../../types";
import { ErrorLine, OutcomePill, Panel, StatusPill, Tile, label, usd, useAction } from "../../components/ui";
import type { CaseCtx } from "../cases/CaseView";

/* Stage 4 · financial reconciliation — Magpie's matcher and review queue, on the case's
   tax bills, liens and payments. Three outcomes, a reason on every line, undo on every review. */

export function FinancialsTab({ c }: { c: CaseCtx }) {
  const fin = usePoll(() => api.financials(c.caseId), 60_000, [c.caseId], true);
  const runId = fin.data?.latest_run_id;
  const run = usePoll(runId ? () => api.run(runId) : null, 60_000, [runId], !!runId);
  const [history, setHistory] = useState<Array<{ inverse: QueueCommand; label: string }>>([]);
  const [csv, setCsv] = useState<{ file: File | null; fmt: string; payer: string }>({ file: null, fmt: "ISO", payer: "" });
  const [importNote, setImportNote] = useState<string | null>(null);
  const [ob, setOb] = useState({ obligation_type: "property_tax", reference: "", amount: "", due_date: "", creditor: "", debtor_name: "" });
  const { busy, error, run: act } = useAction(c.toast);
  const canWrite = c.can("financials:write");
  const refreshAll = () => { void fin.refresh(); void run.refresh(); c.refresh(); };
  const s = run.data?.summary ?? fin.data?.summary;
  const obById = Object.fromEntries((fin.data?.obligations ?? []).map((o) => [o.id, o]));
  const txById = Object.fromEntries((fin.data?.transactions ?? []).map((t) => [t.id, t]));

  const review = async (command: QueueCommand) => {
    if (!runId) return;
    const r = await act(() => api.review(runId, command));
    if (r) {
      if (command.type === "Resolve") setHistory((h) => [...h, { inverse: r.inverse, label: r.label }]);
      c.toast(r.label);
      void run.refresh();
      c.refresh();
    }
  };
  const undo = async () => {
    const last = history[history.length - 1];
    if (!last || !runId) return;
    if (await act(() => api.review(runId, last.inverse), `Undone: ${last.label}`)) {
      setHistory((h) => h.slice(0, -1));
      void run.refresh();
    }
  };
  const describe = (r: ReconResult) => [...r.left, ...r.right].map((id) => obById[id] ? `${label(obById[id].obligation_type)} ${obById[id].reference}` : txById[id] ? `${txById[id].txn_date} ${usd(txById[id].amount_cents)}` : id.slice(0, 8)).join(" ↔ ");

  return (
    <div className="stack">
      <div className="money-band">
        <Tile i={0} n={s?.obligations_total_cents} l="obligations recorded" fmt={usd} />
        <Tile i={1} n={s?.confirmed_paid_cents} l="confirmed paid" fmt={usd} />
        <Tile i={2} n={s?.outstanding_cents} l="outstanding" fmt={usd} />
        <Tile i={3} n={s?.unverified_cents} l="unverified band" fmt={usd} moon />
      </div>
      <ErrorLine error={error} />
      <div className="split">
        <div className="stack">
          <Panel title="Obligations" meta={`${fin.data?.obligations.length ?? 0}`} actions={canWrite ? <button className="btn small" disabled={busy} onClick={async () => { const r = await act(() => api.deriveObligations(c.caseId)); if (r) { c.toast(`${r.created.length} obligation(s) derived from evidence`); refreshAll(); } }}>Derive from evidence</button> : undefined}>
            <table className="t">
              <thead><tr><th>Type</th><th>Reference</th><th>Amount</th><th>Due</th><th>Billed to</th><th>Status</th></tr></thead>
              <tbody>
                {(fin.data?.obligations ?? []).map((o) => (
                  <tr key={o.id}>
                    <td>{label(o.obligation_type)}{o.tax_year ? ` ${o.tax_year}` : ""}</td>
                    <td className="mono small">{o.reference || "—"}{o.document_id ? <div><a className="small" href={`#case/${c.caseId}/evidence/${o.document_id}`}>source{o.page_number ? ` p. ${o.page_number}` : ""}</a></div> : null}</td>
                    <td className="mono">{usd(o.amount_cents)}</td>
                    <td className="mono small">{o.due_date ?? "—"}</td>
                    <td className="small">{o.debtor_name}</td>
                    <td><StatusPill status={o.status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
            {canWrite ? (
              <form className="form" style={{ marginTop: 12 }} onSubmit={async (e) => {
                e.preventDefault();
                const cents = Math.round(Number(ob.amount.replace(/[$,]/g, "")) * 100);
                if (await act(() => api.addObligation(c.caseId, { ...ob, amount_cents: cents }), "Obligation added")) refreshAll();
              }}>
                <div className="grid">
                  <label>Type<select className="input" value={ob.obligation_type} onChange={(e) => setOb({ ...ob, obligation_type: e.target.value })}>{["property_tax", "lien", "mortgage", "judgment", "hoa", "other"].map((t) => <option key={t} value={t}>{label(t)}</option>)}</select></label>
                  <label>Reference<input className="input" value={ob.reference} onChange={(e) => setOb({ ...ob, reference: e.target.value })} /></label>
                  <label>Amount ($)<input className="input" required value={ob.amount} onChange={(e) => setOb({ ...ob, amount: e.target.value })} /></label>
                  <label>Due<input className="input" value={ob.due_date} onChange={(e) => setOb({ ...ob, due_date: e.target.value })} placeholder="MM/DD/YYYY" /></label>
                  <label>Billed to<input className="input" value={ob.debtor_name} onChange={(e) => setOb({ ...ob, debtor_name: e.target.value })} /></label>
                </div>
                <button className="btn" disabled={busy}>Add obligation</button>
              </form>
            ) : null}
          </Panel>
          <Panel title="Payments" meta={`${fin.data?.transactions.length ?? 0}`}>
            <table className="t">
              <thead><tr><th>Date</th><th>Description</th><th>Reference</th><th>Amount</th><th>Payer</th></tr></thead>
              <tbody>
                {(fin.data?.transactions ?? []).map((t) => (
                  <tr key={t.id}><td className="mono small">{t.txn_date}</td><td className="small">{t.description}</td><td className="mono small">{t.reference}</td><td className="mono">{usd(t.amount_cents)}</td><td className="small">{t.payer}</td></tr>
                ))}
              </tbody>
            </table>
            {canWrite ? (
              <form className="row" style={{ marginTop: 12 }} onSubmit={async (e) => {
                e.preventDefault();
                if (!csv.file) return;
                const r = await act(() => api.importTransactions(c.caseId, csv.file as File, csv.fmt, csv.payer));
                if (r) {
                  setImportNote(`${r.imported} imported · ${r.rejections.length} rejected${r.rejections.length ? ": " + r.rejections.map((x) => `line ${x.line} ${x.detail}`).join("; ") : ""}`);
                  refreshAll();
                }
              }}>
                <input type="file" accept=".csv" onChange={(e) => setCsv({ ...csv, file: e.target.files?.[0] ?? null })} />
                <select className="input" value={csv.fmt} onChange={(e) => setCsv({ ...csv, fmt: e.target.value })} aria-label="Date format"><option value="ISO">YYYY-MM-DD</option><option value="MDY">MM/DD/YYYY</option><option value="DMY">DD/MM/YYYY</option></select>
                <input className="input" style={{ maxWidth: 200 }} placeholder="default payer" value={csv.payer} onChange={(e) => setCsv({ ...csv, payer: e.target.value })} />
                <button className="btn" disabled={busy || !csv.file}>Import CSV</button>
              </form>
            ) : null}
            {importNote ? <p className="small dim">{importNote}</p> : null}
            <p className="faint small">Columns: date, amount, and optionally description, reference, payer. A row that cannot be read is listed as a rejection, never silently dropped.</p>
          </Panel>
        </div>
        <Panel
          title="Reconciliation"
          meta={run.data ? `${run.data.results.length} results · ${new Date(run.data.run.created_at).toLocaleString()}` : "not run"}
          actions={
            <>
              {history.length ? <button className="btn ghost small" onClick={() => void undo()}>Undo: {history[history.length - 1].label}</button> : null}
              {c.can("analysis:run") ? <button className="btn primary small" disabled={busy} onClick={async () => { if (await act(() => api.runReconciliation(c.caseId), "Reconciliation complete")) { setHistory([]); refreshAll(); } }}>Run reconciliation</button> : null}
            </>
          }
        >
          {!run.data ? <p className="faint">Derive obligations, import payments, then run.</p> : null}
          <div className="feed">
            {(run.data?.results ?? []).map((r) => (
              <div key={r.id} className="card">
                <div className="title">
                  <span className="mono faint">{r.tier}</span>
                  <span className="alarm" style={{ fontSize: 17 }}>{label(r.rule.replace(/^T\d_/, "").toLowerCase())}</span>
                  <span className="state-col">
                    <OutcomePill outcome={r.outcome} />
                    {r.class ? <span className="moon">{label(r.class.toLowerCase())}</span> : null}
                    <span className="mono small">{usd(r.amount_cents)}</span>
                  </span>
                </div>
                <div className="summary small">{describe(r)} · confidence {Math.round(r.confidence * 100)}%</div>
                <ul className="evidence-lines">{r.evidence.map((line, i) => <li key={i}>{line}</li>)}</ul>
                {r.outcome === "PROPOSED" && c.can("finding:review") ? (
                  <div className="foot">
                    {r.review_status === "open" ? (
                      <>
                        <button className="btn small" onClick={() => void review({ type: "Resolve", ids: [r.id], to: "accepted" })}>Accept link</button>
                        <button className="btn ghost small" onClick={() => void review({ type: "Resolve", ids: [r.id], to: "rejected" })}>Reject</button>
                      </>
                    ) : <StatusPill status={r.review_status} />}
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        </Panel>
      </div>
    </div>
  );
}
