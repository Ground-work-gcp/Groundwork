import { useState } from "react";
import type { Ctx } from "../../app/App";
import { usePoll } from "../../hooks";
import { api } from "../../services/api";
import { ErrorLine, Panel, StatusPill, label, useAction } from "../../components/ui";

/* Members and roles, the honest state of every integration, and the safety model
   (Beacon's Safety page: kill switch, allowlist, and the test that proves each control). */

export function SettingsPage({ ctx, tab = "members" }: { ctx: Ctx; tab?: string }) {
  return (
    <main className="main route-in">
      <div className="page-h">
        <h1 className="display">
          {ctx.me.organization.name}, <em>configured.</em>
        </h1>
      </div>
      <nav className="tabs">
        {[["members", "Members"], ["integrations", "Integrations"], ["safety", "Safety"]].map(([k, l]) => (
          <a key={k} href={`#settings/${k}`} className={tab === k ? "on" : ""}>{l}</a>
        ))}
      </nav>
      {tab === "integrations" ? <Integrations /> : tab === "safety" ? <SafetyPanel /> : <Members ctx={ctx} />}
    </main>
  );
}

function Members({ ctx }: { ctx: Ctx }) {
  const q = usePoll(api.users, 60_000, [], true);
  const [f, setF] = useState({ username: "", full_name: "", email: "", password: "", role: "paralegal" });
  const { busy, error, run } = useAction(ctx.toast);
  const admin = ctx.can("users:manage");
  return (
    <div className="split">
      <Panel title="Members" meta={`${q.data?.users.length ?? 0}`}>
        <table className="t">
          <thead><tr><th>Name</th><th>Role</th><th>Status</th></tr></thead>
          <tbody>
            {(q.data?.users ?? []).map((u) => (
              <tr key={u.id}>
                <td>{u.full_name}<div className="faint small mono">{u.username} · {u.email}</div></td>
                <td>
                  {admin && u.id !== ctx.me.id ? (
                    <select className="input" value={u.role} onChange={async (e) => { if (await run(() => api.updateUser(u.id, { role: e.target.value }), "Role updated")) void q.refresh(); }}>
                      {(q.data?.roles ?? []).map((r) => <option key={r} value={r}>{r}</option>)}
                    </select>
                  ) : u.role}
                </td>
                <td>
                  <StatusPill status={u.is_active ? "active" : "disabled"} />
                  {admin && u.id !== ctx.me.id ? <button className="btn ghost small" onClick={async () => { if (await run(() => api.updateUser(u.id, { is_active: !u.is_active }), "Updated")) void q.refresh(); }}>{u.is_active ? "Disable" : "Enable"}</button> : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <ErrorLine error={error} />
        <p className="faint small">viewer: read · paralegal: evidence, financials, analysis, tasks, propose actions · attorney: + approve actions, record decisions · admin: + members</p>
      </Panel>
      {admin ? (
        <Panel title="Add a member">
          <form className="form" onSubmit={async (e) => { e.preventDefault(); if (await run(() => api.addUser(f), "Member added")) { setF({ username: "", full_name: "", email: "", password: "", role: "paralegal" }); void q.refresh(); } }}>
            <label>Full name<input className="input" required value={f.full_name} onChange={(e) => setF({ ...f, full_name: e.target.value })} /></label>
            <label>Username<input className="input" required value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} /></label>
            <label>Email<input className="input" type="email" required value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></label>
            <label>Temporary password<input className="input" type="password" required minLength={10} value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} /></label>
            <label>Role<select className="input" value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })}>{(q.data?.roles ?? []).map((r) => <option key={r} value={r}>{r}</option>)}</select></label>
            <button className="btn primary" disabled={busy}>Add member</button>
          </form>
        </Panel>
      ) : null}
    </div>
  );
}

function Integrations() {
  const q = usePoll(api.integrations, 60_000, [], true);
  return (
    <Panel title="What each integration is actually running on">
      <ErrorLine error={q.error} />
      <table className="t">
        <tbody>
          {Object.entries(q.data ?? {}).map(([k, v]) => (
            <tr key={k}>
              <td className="nowrap"><b>{label(k)}</b></td>
              <td className="mono small">
                {v && typeof v === "object" ? Object.entries(v).map(([a, b]) => `${a}: ${b === null ? "—" : String(b)}`).join(" · ") : String(v ?? "—")}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="faint small">Local modes are real local implementations, labelled as such — nothing here pretends to be a cloud service that is not configured.</p>
    </Panel>
  );
}

function SafetyPanel() {
  const q = usePoll(api.safety, 120_000, [], true);
  const [sel, setSel] = useState(0);
  const s = q.data;
  const ctl = s?.controls[sel];
  return (
    <div className="stack">
      <Panel title="Kill switch">
        {s ? <span className={`pill ${s.execution_enabled ? "green" : "red"}`}>{s.execution_enabled ? "approved actions can execute" : "all execution blocked"}</span> : null}
        {s ? <span className="pill dim">{s.separate_approver ? "proposer cannot approve" : "self-approval allowed"}</span> : null}
        <span className="faint small"> WORKFLOW_EXECUTION_ENABLED=false stops every execution path.</span>
      </Panel>
      <Panel title="Allowlist" meta="nothing else can run">
        <table className="t">
          <thead><tr><th>action</th><th>what it does</th><th>params</th><th>approved by</th></tr></thead>
          <tbody>
            {(s?.allowlist ?? []).map((a) => (
              <tr key={a.id}><td className="mono">{a.id}</td><td>{a.description}</td><td className="mono small">{Object.entries(a.params).map(([k, t]) => `${k}: ${t}`).join(", ")}</td><td className="small">{a.approver_roles.join(", ")}</td></tr>
            ))}
          </tbody>
        </table>
        {s ? <p className="small dim">Never: {s.never.join(" · ")}.</p> : null}
      </Panel>
      <div className="faq">
        <div className="faq-q">
          <h3>Controls</h3>
          <ul>
            {(s?.controls ?? []).map((c, i) => <li key={c.id}><button type="button" className={i === sel ? "on" : ""} onClick={() => setSel(i)}>{c.title}</button></li>)}
          </ul>
        </div>
        <div className="faq-a">
          <h3>Proof</h3>
          {ctl ? (
            <div className="faq-bubble">
              <p>{ctl.rule}</p>
              <dl className="kv"><dt>enforced in</dt><dd>{ctl.file}</dd><dt>proven by</dt><dd>{ctl.test}</dd></dl>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
