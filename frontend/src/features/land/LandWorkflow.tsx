import { useState } from "react";
import { usePoll } from "../../hooks";
import { api } from "../../services/api";
import type * as T from "../../types";
import { Empty, ErrorLine, Panel, SeverityPill, label, useAction } from "../../components/ui";
import type { CaseCtx } from "../cases/CaseView";
import { inr } from "./LandTab";

/* The land case as it moves through the real world: the lifecycle, the files sitting with
   offices (and how long they have sat there), and the routes the family can choose. */

const SIGNAL: Record<string, { tone: string; text: string }> = {
  on_track: { tone: "green", text: "on track" }, at_risk: { tone: "amber", text: "at risk" }, overdue: { tone: "red", text: "overdue" },
  stalled: { tone: "red", text: "stalled" }, done: { tone: "green", text: "order passed" }, not_filed: { tone: "dim", text: "not filed" },
  rejected: { tone: "red", text: "rejected" }, withdrawn: { tone: "dim", text: "withdrawn" },
};
const Signal = ({ s }: { s: string }) => <span className={`pill ${SIGNAL[s]?.tone ?? "dim"}`}>{SIGNAL[s]?.text ?? label(s)}</span>;
const today = () => new Date().toISOString().slice(0, 10);

/* ── journey ──────────────────────────────────────────────────────────────── */

export function Journey({ c, rail = true }: { c: CaseCtx; rail?: boolean }) {
  const j = usePoll(() => api.landJourney(c.caseId), 30_000, [c.caseId], true);
  const d = j.data;
  if (!d) return <p className="faint">{j.error ?? "Loading…"}</p>;
  const current = d.stages.findIndex((s) => s.key === d.stage);
  const active = d.proceedings.filter((p) => !["ordered", "rejected", "withdrawn"].includes(p.status));
  return (
    <div className="stack">
      {rail ? <ol className="journey-rail" aria-label="Land case journey">
        {d.stages.map((s, i) => {
          const g = d.checklist.find((x) => x.stage === s.key);
          return (
            <li key={s.key} className={`${g?.done ? "done" : ""}${i === current ? " current" : ""}`}>
              <span className="jr-dot">{g?.done ? "✓" : i + 1}</span>
              <span className="jr-t">{s.label}</span>
              <span className="jr-d">{g?.detail}</span>
            </li>
          );
        })}
      </ol> : null}
      <div className="split">
        <div className="stack">
          {d.next_action ? (
            <a className="next-card" href={d.next_action.href}>
              <span className="eyebrow">Next step · on the critical path</span>
              <strong>{d.next_action.label}</strong>
              <span>{d.next_action.step}</span>
            </a>
          ) : <div className="fix green"><div className="head">Every stage on the critical path is clear.</div></div>}
          <Panel title="Files with offices" meta={`${active.length} active`} actions={<a className="btn ghost small" href={`#case/${c.caseId}/land/proceedings`}>Open proceedings</a>}>
            {d.proceedings.length ? d.proceedings.map((p) => <FileCard key={p.id} p={p} compact />) : <Empty title="Nothing filed yet.">Run the agent or open a proceeding — the legal heir certificate usually comes first.</Empty>}
          </Panel>
        </div>
        <Panel title="Waiting on people" meta={`${d.needs_you.length}`}>
          {d.needs_you.length ? (
            <ul className="needs-list">
              {d.needs_you.map((i) => (
                <li key={`${i.kind}-${i.id}`}><a href={i.href}><span className="t">{i.title}</span><span className="s">{i.suggests}</span></a><span className="r"><SeverityPill severity={i.severity} /></span></li>
              ))}
            </ul>
          ) : <p className="faint">Nothing is waiting on a person.</p>}
        </Panel>
      </div>
    </div>
  );
}

function SlaBar({ h }: { h: T.ProceedingHealth }) {
  if (h.signal === "not_filed") return null;
  const pct = Math.min(100, (h.elapsed_days / Math.max(1, h.sla_days)) * 100);
  const over = h.elapsed_days > h.sla_days;
  return (
    <div className="sla" aria-label={`${h.elapsed_days} of ${h.sla_days} days`}>
      <div className={`sla-track${over ? " over" : ""}`}><span className={`sla-fill ${SIGNAL[h.signal]?.tone ?? ""}`} style={{ width: `${pct}%` }} /></div>
      <span className="small mono">
        day {h.elapsed_days} of {h.sla_days}
        {h.overdue_days ? ` · ${h.overdue_days} overdue` : h.days_left != null && h.days_left >= 0 && h.signal !== "done" ? ` · ${h.days_left} left` : ""}
      </span>
    </div>
  );
}

function FileCard({ p, compact, children }: { p: T.Proceeding; compact?: boolean; children?: React.ReactNode }) {
  const h = p.health;
  return (
    <div className={`file-card sig-${h.signal}`}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div><b>{p.label}</b> <span className="small dim">· {p.office}{p.application_no ? ` · ${p.application_no}` : ""}</span></div>
        <Signal s={h.signal} />
      </div>
      <ol className="flow-chips">
        {h.flow.map((f) => <li key={f.status} className={f.reached ? "on" : ""} title={f.label}>{label(f.status)}</li>)}
      </ol>
      <SlaBar h={h} />
      <div className="small dim">
        {h.status_label}{h.waiting_on && h.waiting_on !== "nobody" ? ` · waiting on ${h.waiting_on}` : ""}
        {h.objection_days_left != null && h.objection_days_left >= 0 ? ` · objection window: ${h.objection_days_left} day(s) left` : ""}
        {p.hearing_on ? ` · hearing ${p.hearing_on}` : ""}
        {h.objection ? " · objection filed" : ""}
      </div>
      {!compact ? children : null}
    </div>
  );
}

/* ── proceedings ──────────────────────────────────────────────────────────── */

export function Proceedings({ c }: { c: CaseCtx }) {
  const q = usePoll(() => api.proceedings(c.caseId), 30_000, [c.caseId], true);
  const [f, setF] = useState({ kind: "legal_heir_certificate", application_no: "", filed_on: "", applicants: "" });
  const [letter, setLetter] = useState<{ title: string; text: string } | null>(null);
  const { busy, error, run } = useAction(c.toast);
  const d = q.data;
  const write = c.can("evidence:write");
  if (!d) return <p className="faint">{q.error ?? "Loading…"}</p>;
  const refresh = () => { void q.refresh(); c.refresh(); };
  return (
    <div className="split">
      <div className="stack">
        {d.proceedings.length ? d.proceedings.map((p) => (
          <FileCard key={p.id} p={p}>
            <ProceedingDetail p={p} d={d} c={c} onChanged={refresh} onLetter={setLetter} />
          </FileCard>
        )) : <Empty title="No applications yet.">Open one on the right, or let the agent draft them.</Empty>}
      </div>
      <div className="stack">
        {write ? (
          <Panel title="Open a proceeding">
            <ErrorLine error={error} />
            <form className="form" onSubmit={async (e) => { e.preventDefault(); if (await run(() => api.addProceeding(c.caseId, { ...f, filed_on: f.filed_on || null, applicants: f.applicants.split(",").map((x) => x.trim()).filter(Boolean) }), "Proceeding opened")) { setF({ ...f, application_no: "", filed_on: "" }); refresh(); } }}>
              <label>What<select className="input" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>{Object.entries(d.kinds).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}</select></label>
              <p className="small dim">{d.kinds[f.kind]?.office} · indicative timeline {d.kinds[f.kind]?.sla_days} days</p>
              <div className="grid2">
                <label>Application / acknowledgement no.<input className="input" value={f.application_no} onChange={(e) => setF({ ...f, application_no: e.target.value })} /></label>
                <label>Filed on (blank = draft)<input className="input" placeholder="DD-MM-YYYY" value={f.filed_on} onChange={(e) => setF({ ...f, filed_on: e.target.value })} /></label>
              </div>
              <label>Applicants (comma-separated)<input className="input" value={f.applicants} onChange={(e) => setF({ ...f, applicants: e.target.value })} /></label>
              <button className="btn" disabled={busy}>Open</button>
            </form>
          </Panel>
        ) : null}
        <Panel title="When a file stalls" meta={d.region.code}>
          <ol className="small stack" style={{ gap: 6 }}>
            <li>Follow up at the office and log it — a dated trail is the basis of every escalation.</li>
            <li>Representation to the <b>{d.region.appellate_office}</b>{d.region.rts_law ? <> — an appeal under the <b>{d.region.rts_law}</b></> : null}.</li>
            <li>Representation to the <b>{d.region.collector}</b>.</li>
            <li>Grievance on <b>{d.region.grievance_portal}</b>.</li>
          </ol>
          <p className="faint small">Online services: {d.region.online}. Mutation objection window: {d.region.objection_days} days. {d.note}</p>
        </Panel>
        {letter ? (
          <Panel title={letter.title} actions={<button className="btn ghost small" onClick={() => { const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([letter.text], { type: "text/markdown" })); a.download = "representation.md"; a.click(); }}>Download</button>}>
            <pre className="pagetext">{letter.text}</pre>
          </Panel>
        ) : null}
      </div>
    </div>
  );
}

function ProceedingDetail({ p, d, c, onChanged, onLetter }: { p: T.Proceeding; d: T.ProceedingsPayload; c: CaseCtx; onChanged: () => void; onLetter: (l: { title: string; text: string }) => void }) {
  const [ev, setEv] = useState({ kind: p.status === "draft" ? "filed" : "follow_up", event_date: today(), note: "", hearing_on: "" });
  const [check, setCheck] = useState<{ items: Array<{ item: string; ok: boolean; where: string }>; ready: boolean } | null>(null);
  const [next, setNext] = useState<string | null>(null);
  const { busy, error, run } = useAction(c.toast);
  const write = c.can("evidence:write");
  const terminal = ["ordered", "rejected", "withdrawn"].includes(p.status);
  return (
    <div className="stack" style={{ gap: 8, marginTop: 8 }}>
      <ErrorLine error={error} />
      {p.events.length ? (
        <ul className="ev-list small">
          {p.events.map((e) => <li key={e.id}><span className="mono">{e.event_date}</span> <b>{label(e.kind)}</b>{e.note ? ` — ${e.note}` : ""}</li>)}
        </ul>
      ) : null}
      {next ? <div className="fix lilac small">{next}</div> : null}
      {check ? (
        <div className={`fix ${check.ready ? "green" : "red"} small`}>
          <div className="head">{check.ready ? "Ready to file" : "Missing before filing"}</div>
          {check.items.map((i) => <div key={i.item}>{i.ok ? "✓" : "✗"} {i.item} <span className="dim">— {i.where}</span></div>)}
        </div>
      ) : null}
      {write ? (
        <div className="row">
          {!terminal ? (
            <form className="row" onSubmit={async (e) => { e.preventDefault(); const r = await run(() => api.proceedingEvent(p.id, { ...ev, hearing_on: ev.hearing_on || null }), "Logged"); if (r) { setNext(r.next ?? null); setEv({ ...ev, note: "" }); onChanged(); } }}>
              <select className="input" value={ev.kind} onChange={(e) => setEv({ ...ev, kind: e.target.value })}>{d.event_kinds.filter((k) => k !== "escalated").map((k) => <option key={k} value={k}>{label(k)}</option>)}</select>
              <input className="input" style={{ maxWidth: 140 }} value={ev.event_date} onChange={(e) => setEv({ ...ev, event_date: e.target.value })} aria-label="event date" />
              {ev.kind === "hearing_scheduled" ? <input className="input" style={{ maxWidth: 150 }} placeholder="hearing date" value={ev.hearing_on} onChange={(e) => setEv({ ...ev, hearing_on: e.target.value })} /> : null}
              <input className="input" placeholder="what happened" value={ev.note} onChange={(e) => setEv({ ...ev, note: e.target.value })} />
              <button className="btn small" disabled={busy}>Log</button>
            </form>
          ) : null}
          <button className="btn ghost small" onClick={async () => setCheck(await run(() => api.filingCheck(p.id)))}>Filing check</button>
          {["overdue", "stalled", "at_risk"].includes(p.health.signal) && !terminal ? (
            <button className="btn small primary" disabled={busy} onClick={async () => { const r = await run(() => api.escalate(p.id), "Escalation drafted"); if (r) { onLetter({ title: `Representation to the ${r.authority}`, text: r.letter }); onChanged(); } }}>Escalate</button>
          ) : null}
          {p.status === "draft" ? <button className="btn ghost small" onClick={async () => { if (await run(() => api.removeProceeding(p.id), "Draft removed")) onChanged(); }}>Delete draft</button> : null}
        </div>
      ) : null}
    </div>
  );
}

/* ── pathways ─────────────────────────────────────────────────────────────── */

export function PathwaysView({ c, d }: { c: CaseCtx; d: T.LandOverview }) {
  const [res, setRes] = useState<T.Pathways | null>((d.latest as unknown as Record<string, T.Analysis<T.Pathways> | null>).pathways?.result ?? null);
  const [ben, setBen] = useState("");
  const { busy, error, run } = useAction(c.toast);
  const living = d.members.filter((m) => m.is_alive);
  const go = async () => { const r = await run(() => api.pathways(c.caseId, { lender: "bank_term_loan", beneficiary_id: ben || null })); if (r) setRes(r.analysis.result); };
  return (
    <div className="stack">
      <Panel title="How the land can become usable again" meta="options to discuss — the family decides" actions={c.can("analysis:run") ? <button className="btn small primary" disabled={busy} onClick={() => void go()}>Compare routes</button> : undefined}>
        <div className="row small">
          <span>Release route in favour of</span>
          <select className="input" value={ben} onChange={(e) => setBen(e.target.value)} style={{ maxWidth: 240 }}>
            <option value="">the heir who farms it (from the adangal)</option>
            {living.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
          </select>
        </div>
        <ErrorLine error={error} />
        {res?.status === "insufficient" ? <p className="faint">{res.reason}</p> : null}
        {res?.status === "compared" ? <p className="small dim">First rupee in about <b>{res.first_rupee_days} days</b> · lending value once the title is fixed: <b>{inr(res.lending_value_after_fix_inr)}</b></p> : null}
      </Panel>
      {res?.status === "compared" ? (
        <div className="routes">
          {res.routes.map((r) => {
            const span = Math.max(1, r.days);
            return (
              <section key={r.id} className={`route${r.recommended ? " best" : ""}${r.bridge ? " bridge" : ""}`}>
                <div className="route-h">
                  <h3>{r.label}</h3>
                  {r.recommended ? <span className="pill green">highest score {r.score}</span> : r.score != null ? <span className="pill dim">score {r.score}</span> : <span className="pill lilac">alongside any route</span>}
                </div>
                <p className="small dim">{r.summary}</p>
                <dl className="route-kv">
                  <div><dt>time</dt><dd>{r.days} days</dd></div>
                  <div><dt>fees + duty</dt><dd>{inr(r.cost_inr)}</dd></div>
                  <div><dt>can borrow</dt><dd>{inr(r.capacity_inr)}</dd></div>
                  <div><dt>signatures</dt><dd>{r.signatures.length}{r.away_signatures.length ? ` (${r.away_signatures.length} away)` : ""}</dd></div>
                </dl>
                <p className="small"><b>Borrower:</b> {r.borrower}{r.away_signatures.length ? <> · <b>must travel or give a POA:</b> {r.away_signatures.join(", ")}</> : null}</p>
                <ul className="gantt small">
                  {r.steps.map((s) => (
                    <li key={s.id} className={s.critical ? "crit" : ""}>
                      <div className="g-label">{s.name}<span className="dim"> · {s.office}{s.fee_inr ? ` · ${inr(s.fee_inr)}` : ""}</span></div>
                      <div className="g-track"><span className="g-bar" style={{ left: `${(s.start_day / span) * 100}%`, width: `${Math.max(3, (s.days / span) * 100)}%` }} /></div>
                    </li>
                  ))}
                </ul>
                {r.value_transfers.length ? (
                  <table className="t small"><thead><tr><th>Releasing heir</th><th>share</th><th>gives up (P50)</th><th>stamp + reg.</th></tr></thead>
                    <tbody>{r.value_transfers.map((t) => <tr key={t.from}><td>{t.from}</td><td className="mono">{t.share}</td><td className="mono">{inr(t.market_value_inr)}</td><td className="mono">{inr(t.stamp_inr + t.registration_inr)}</td></tr>)}</tbody>
                  </table>
                ) : null}
                {r.fair_settlement_inr ? <p className="small">A fair settlement to the releasing heirs would be about <b>{inr(r.fair_settlement_inr)}</b> at today's median value.</p> : null}
                {r.per_heir.length > 1 ? (
                  <table className="t small"><tbody>{r.per_heir.map((h) => <tr key={h.name}><td>{h.name} <span className="mono dim">{h.share}</span></td><td className="mono">{h.area_sqm ? `${(h.area_sqm / 4046.86).toFixed(2)} ac` : ""}</td><td className="mono">{inr(h.capacity_inr)}{h.below_min_plot ? " · small plot" : ""}</td></tr>)}</tbody></table>
                ) : null}
                <ul className="small risks">{r.risks.map((x) => <li key={x}>{x}</li>)}</ul>
              </section>
            );
          })}
        </div>
      ) : null}
      {res?.status === "compared" ? (
        <details className="small"><summary>How routes are scored and costed</summary>
          <p>Score = {Object.entries(res.weights).map(([k, v]) => `${Math.round(v * 100)}% ${k}`).join(" + ")} (each normalised across the routes; coordination counts signatures, with heirs away from the village counted three times).</p>
          <ul>{res.assumptions.map((a) => <li key={a}>{a}</li>)}</ul>
          <p className="faint">{res.disclaimer}</p>
        </details>
      ) : null}
    </div>
  );
}
