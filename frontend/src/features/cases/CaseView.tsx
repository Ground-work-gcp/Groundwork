import { useState } from "react";
import type { Ctx } from "../../app/App";
import { usePoll } from "../../hooks";
import { CASE_TABS } from "../../navigation/routes";
import { api } from "../../services/api";
import type { CaseDetail } from "../../types";
import { Timeline } from "../../components/Timeline";
import { Empty, ErrorLine, Panel, StagePill, Tile, label, usd, useAction } from "../../components/ui";
import { EvidenceTab } from "../evidence/EvidenceTab";
import { OwnershipTab } from "../ownership/OwnershipTab";
import { FinancialsTab } from "../financials/FinancialsTab";
import { CaseFindings } from "../risk-analysis/Findings";
import { WorkflowTab } from "../workflows/WorkflowTab";
import { FlowCanvas } from "../flow/FlowCanvas";

export interface CaseCtx extends Ctx {
  caseId: string;
  detail: CaseDetail;
  refresh: () => void;
}

export function CaseView({ ctx, caseId, tab, item }: { ctx: Ctx; caseId: string; tab: string; item?: string }) {
  const q = usePoll(() => api.caseDetail(caseId), 30_000, [caseId], true);
  const d = q.data;
  if (!d) return <main className="main route-in">{q.error ? <Empty title="Case not found.">{q.error}</Empty> : <p className="faint">Loading…</p>}</main>;
  const cctx: CaseCtx = { ...ctx, caseId, detail: d, refresh: () => void q.refresh() };
  const current = d.stages.findIndex((s) => s.key === d.case.stage);
  return (
    <main className="main route-in">
      <div className="page-h">
        <div className="row">
          <span className="mono faint">{d.case.reference}</span>
          <StagePill stage={d.case.stage} status={d.case.status} />
        </div>
        <h1 className="display">{d.case.title}</h1>
        <p className="lede">
          {[d.property?.address_line, d.property?.county && `${d.property.county} County`, d.property?.state, d.property?.parcel_number && `parcel ${d.property.parcel_number}`].filter(Boolean).join(" · ")}
        </p>
      </div>
      <ol className="stepper" aria-label="Case stages">
        {d.stages.map((s, i) => {
          const gate = d.checklist.find((g) => g.stage === s.key);
          return (
            <li key={s.key} className={`${gate?.done ? "done" : ""}${i === current ? " current" : ""}`}>
              <div className="n">{i + 1}</div>
              <div className="t">{s.label}</div>
              <div className="d">{gate?.detail}</div>
            </li>
          );
        })}
      </ol>
      <nav className="tabs" aria-label="Case sections">
        {CASE_TABS.map((t) => (
          <a key={t.key} href={`#case/${caseId}/${t.key}`} className={tab === t.key ? "on" : ""}>
            {t.label}
            {t.key === "findings" && d.counts.findings_open ? <span className="count">{d.counts.findings_open}</span> : null}
            {t.key === "workflow" && d.counts.actions_pending ? <span className="count">{d.counts.actions_pending}</span> : null}
          </a>
        ))}
      </nav>
      {tab === "flow" ? (
        <FlowCanvas ctx={ctx} caseId={caseId} />
      ) : tab === "evidence" ? (
        <EvidenceTab c={cctx} docId={item} />
      ) : tab === "ownership" ? (
        <OwnershipTab c={cctx} />
      ) : tab === "financials" ? (
        <FinancialsTab c={cctx} />
      ) : tab === "findings" ? (
        <CaseFindings c={cctx} />
      ) : tab === "workflow" ? (
        <WorkflowTab c={cctx} />
      ) : tab === "history" ? (
        <History c={cctx} />
      ) : (
        <CaseOverview c={cctx} />
      )}
    </main>
  );
}

function CaseOverview({ c }: { c: CaseCtx }) {
  const d = c.detail;
  const [question, setQuestion] = useState("");
  const qs = usePoll(() => api.questions(c.caseId), 60_000, [c.caseId], true);
  const [party, setParty] = useState({ name: "", role: "heir" });
  const { busy, error, run } = useAction(c.toast);
  const next = d.stages[d.stages.findIndex((s) => s.key === d.case.stage) + 1];
  return (
    <div className="split">
      <div className="stack">
        <div className="money-band">
          <Tile i={0} n={d.financials.obligations_total_cents} l="obligations recorded" fmt={usd} />
          <Tile i={1} n={d.financials.confirmed_paid_cents} l="confirmed paid" fmt={usd} />
          <Tile i={2} n={d.financials.outstanding_cents} l="outstanding" fmt={usd} />
          <Tile i={3} n={d.financials.unverified_cents} l="unverified" fmt={usd} moon />
        </div>
        <Panel title="Stage" meta={label(d.case.stage)}>
          <p className="dim small">{d.checklist.find((g) => g.stage === d.case.stage)?.detail}</p>
          {next && c.can("case:write") && d.case.status === "open" && !["resolution", "resolved"].includes(next.key) ? (
            <button className="btn" disabled={busy} onClick={async () => { if (await run(() => api.moveStage(c.caseId, next.key), `Moved to ${next.label}`)) c.refresh(); }}>
              Advance to {next.label}
            </button>
          ) : next ? <p className="faint small">Moving to {next.label} is an approved action — see Workflow.</p> : null}
          <ErrorLine error={error} />
        </Panel>
        <Panel title="Parties" meta={`${d.parties.length}`}>
          <table className="t">
            <tbody>
              {d.parties.map((p) => (
                <tr key={p.id}>
                  <td>{p.name}{p.is_deceased ? <span className="faint"> · deceased {p.date_of_death ?? ""}</span> : null}</td>
                  <td className="dim small">{p.roles.map(label).join(", ")}</td>
                  <td className="faint small">{p.source}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {c.can("case:write") ? (
            <form className="row" style={{ marginTop: 10 }} onSubmit={async (e) => { e.preventDefault(); if (await run(() => api.addParty(c.caseId, { name: party.name, roles: [party.role] }), "Party added")) { setParty({ name: "", role: "heir" }); c.refresh(); } }}>
              <input className="input" style={{ maxWidth: 240 }} placeholder="name" value={party.name} onChange={(e) => setParty({ ...party, name: e.target.value })} required minLength={2} />
              <select className="input" value={party.role} onChange={(e) => setParty({ ...party, role: e.target.value })}>
                {["heir", "client", "decedent", "owner_of_record", "lienholder", "occupant", "other"].map((r) => <option key={r} value={r}>{label(r)}</option>)}
              </select>
              <button className="btn" disabled={busy}>Add party</button>
            </form>
          ) : null}
        </Panel>
      </div>
      <div className="stack">
        <Panel title="Ask the evidence" meta="answers cite the case's own pages">
          <form className="composer" onSubmit={async (e) => { e.preventDefault(); if (await run(() => api.ask(c.caseId, question))) { setQuestion(""); void qs.refresh(); } }}>
            <input className="input" placeholder="Who signed the 2016 deed?" value={question} onChange={(e) => setQuestion(e.target.value)} minLength={3} required />
            <button className="btn primary" disabled={busy}>Ask</button>
          </form>
          <div className="stack" style={{ marginTop: 12 }}>
            {[...(qs.data?.questions ?? [])].reverse().map((q) => (
              <div key={q.id} className="stack" style={{ gap: 6 }}>
                <div className="eyebrow">{q.question}</div>
                <div className="answer">
                  {q.answer}
                  {!q.verified ? <span className="unverified"> unverified</span> : null}
                  <span className="faint small"> · {q.mode}</span>
                </div>
                {q.citations.map((s) => (
                  <a key={s.ref} className="small" href={`#case/${c.caseId}/evidence/${s.document_id}`}>
                    <span className="chip">{s.ref}</span> {s.filename} · p. {s.page} — <span className="dim">{s.text.slice(0, 180)}…</span>
                  </a>
                ))}
              </div>
            ))}
          </div>
        </Panel>
        <Panel title="Description">
          <p className="dim">{d.case.description || "—"}</p>
          {d.case.resolution_summary ? <div className="fix green"><div className="head">Resolution</div><div>{d.case.resolution_summary}</div></div> : null}
        </Panel>
      </div>
    </div>
  );
}

function History({ c }: { c: CaseCtx }) {
  const t = usePoll(() => api.timeline(c.caseId), 30_000, [c.caseId], true);
  const [report, setReport] = useState<string | null>(null);
  const { error, run } = useAction();
  return (
    <div className="split">
      <Panel title="Verified history" meta={`${t.data?.events.length ?? 0} events`} actions={<a className="btn ghost small" href={api.auditCsvUrl(c.caseId)}>CSV</a>}>
        <Timeline events={t.data?.events ?? []} />
      </Panel>
      <Panel title="Case report" actions={<button className="btn small" onClick={async () => setReport(await run(() => api.report(c.caseId)))}>Generate</button>}>
        <ErrorLine error={error} />
        {report ? (
          <div className="letter">
            <div className="row letter-tools">
              <button className="btn ghost small" onClick={() => { const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([report], { type: "text/markdown" })); a.download = `${c.detail.case.reference}.md`; a.click(); }}>Download .md</button>
            </div>
            <pre className="pagetext">{report}</pre>
          </div>
        ) : (
          <p className="faint">Rendered from the case record — evidence, ownership chain, financial position, findings with citations, decisions, approvals and the audit-chain check.</p>
        )}
      </Panel>
    </div>
  );
}
