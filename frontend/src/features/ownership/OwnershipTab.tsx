import { useState } from "react";
import { usePoll } from "../../hooks";
import { api } from "../../services/api";
import { Empty, ErrorLine, Panel, label, useAction } from "../../components/ui";
import type { CaseCtx } from "../cases/CaseView";

/* Stage 3 · ownership reconstruction: the chain of title, checked event by event. */

export function OwnershipTab({ c }: { c: CaseCtx }) {
  const q = usePoll(() => api.ownership(c.caseId), 60_000, [c.caseId], true);
  const { busy, error, run } = useAction(c.toast);
  const [f, setF] = useState({ event_type: "deed_transfer", event_date: "", grantors: "", grantees: "", instrument: "" });
  const d = q.data;
  const rebuild = async () => {
    if (await run(() => api.reconstruct(c.caseId), "Ownership history rebuilt from evidence")) {
      void q.refresh();
      c.refresh();
    }
  };
  return (
    <div className="split">
      <div className="stack">
        <Panel title="Current owners per the evidence" actions={c.can("analysis:run") ? <button className="btn primary" disabled={busy} onClick={() => void rebuild()}>Rebuild from evidence</button> : undefined}>
          <ErrorLine error={error} />
          {d?.current_owners.length ? <div className="quote">{d.current_owners.join(" · ")}</div> : <p className="faint">Not yet determined.</p>}
          {d?.deceased_current_owners.length ? <div className="fix red"><div className="head">Deceased owners still hold title</div><div>{d.deceased_current_owners.join(", ")}</div></div> : null}
          <p className="faint small">This is what the uploaded records show, not a determination of ownership.</p>
        </Panel>
        <Panel title="What does not connect" meta={`${d?.issues.length ?? 0}`}>
          {d?.issues.length ? (
            <ul className="rules">
              {d.issues.map((i, n) => <li key={n}><b>{label(i.kind)}.</b> {i.message}</li>)}
            </ul>
          ) : <p className="faint">No issues in the chain.</p>}
        </Panel>
        {c.can("evidence:write") ? (
          <Panel title="Add an event by hand">
            <form className="form" onSubmit={async (e) => {
              e.preventDefault();
              const body = { ...f, grantors: f.grantors.split(";").map((s) => s.trim()).filter(Boolean), grantees: f.grantees.split(";").map((s) => s.trim()).filter(Boolean) };
              if (await run(() => api.addEvent(c.caseId, body), "Event added")) void q.refresh();
            }}>
              <div className="grid">
                <label>Type<select className="input" value={f.event_type} onChange={(e) => setF({ ...f, event_type: e.target.value })}>{["deed_transfer", "quitclaim", "probate_distribution", "will", "death", "tax_assessment"].map((t) => <option key={t} value={t}>{label(t)}</option>)}</select></label>
                <label>Date<input className="input" value={f.event_date} onChange={(e) => setF({ ...f, event_date: e.target.value })} placeholder="MM/DD/YYYY" /></label>
                <label>Instrument<input className="input" value={f.instrument} onChange={(e) => setF({ ...f, instrument: e.target.value })} /></label>
              </div>
              <label>From (; separated)<input className="input" value={f.grantors} onChange={(e) => setF({ ...f, grantors: e.target.value })} /></label>
              <label>To (; separated)<input className="input" value={f.grantees} onChange={(e) => setF({ ...f, grantees: e.target.value })} /></label>
              <button className="btn" disabled={busy}>Add event</button>
            </form>
          </Panel>
        ) : null}
      </div>
      <Panel title="Chain of title" meta={`${d?.events.length ?? 0} events`}>
        {!d?.events.length ? (
          <Empty title="No history yet.">Upload deeds, death records and tax bills, then rebuild from evidence.</Empty>
        ) : (
          <ul className="chain">
            {d.events.map((e) => (
              <li key={e.id} className={e.event_type === "death" ? "death" : e.chain_status}>
                <div className="when">{e.event_date ?? "undated"}</div>
                <div>
                  <div className="what">{label(e.event_type)}{e.instrument ? <span className="mono faint small"> · {e.instrument}</span> : null}</div>
                  <div className="who">
                    {e.grantors.join(", ") || "—"} {e.grantees.length ? <>→ <b>{e.grantees.join(", ")}</b></> : null}
                  </div>
                  <div className="faint small">
                    {e.source} · confidence {Math.round(e.confidence * 100)}%
                    {e.document_id ? <> · <a href={`#case/${c.caseId}/evidence/${e.document_id}`}>source document{e.page_number ? `, p. ${e.page_number}` : ""}</a></> : null}
                    {e.source === "manual" && c.can("evidence:write") ? <> · <button className="btn ghost small" onClick={async () => { if (await run(() => api.removeEvent(e.id), "Event removed")) void q.refresh(); }}>remove</button></> : null}
                  </div>
                  {e.chain_status !== "ok" ? <div className="note">{label(e.chain_status)}{e.chain_notes.length ? ` — ${e.chain_notes.join("; ")}` : ""}</div> : e.chain_notes.length ? <div className="faint small">{e.chain_notes.join("; ")}</div> : null}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}
