import { useState } from "react";
import { formatStamp, usePoll } from "../../hooks";
import { api } from "../../services/api";
import { ErrorLine, Panel, StatusPill, label, useAction } from "../../components/ui";
import type { CaseCtx } from "../cases/CaseView";

/* Stage 7 · resolution workflow: tasks, comments, decisions, and allowlisted actions that
   only execute after an approver types their exact phrase (Beacon's approval ledger). */

export function WorkflowTab({ c }: { c: CaseCtx }) {
  const w = usePoll(() => api.workflow(c.caseId), 30_000, [c.caseId], true);
  const comments = usePoll(() => api.comments(c.caseId), 30_000, [c.caseId], true);
  const users = usePoll(api.users, 120_000, [], true);
  const { busy, error, run } = useAction(c.toast);
  const [task, setTask] = useState({ title: "", assignee_id: "", due_date: "" });
  const [comment, setComment] = useState("");
  const [decision, setDecision] = useState({ title: "", decision: "", rationale: "" });
  const [resolution, setResolution] = useState("");
  const names = Object.fromEntries((users.data?.users ?? []).map((u) => [u.id, u.full_name]));
  const refresh = () => { void w.refresh(); c.refresh(); };
  const d = w.data;
  const propose = async (action: string, params: Record<string, unknown>, msg: string) => {
    if (await run(() => api.propose(c.caseId, action, params), msg)) refresh();
  };

  return (
    <div className="split">
      <div className="stack">
        <Panel title="Actions" meta="approved by phrase, executed once" actions={c.can("action:propose") && d?.suggestions.length ? <button className="btn small" disabled={busy} onClick={async () => { const r = await run(() => api.proposeSuggested(c.caseId)); if (r) { c.toast(`${r.created.length} evidence request(s) proposed`); refresh(); } }}>Propose {d.suggestions.length} suggested</button> : undefined}>
          <ErrorLine error={error} />
          {c.can("action:propose") && c.detail.case.status === "open" ? (
            <div className="stack small">
              {c.detail.case.stage === "review" ? <button className="btn" disabled={busy} onClick={() => void propose("case.advance_stage", { to_stage: "resolution" }, "Proposed: move to resolution")}>Propose: move to resolution</button> : null}
              {c.detail.case.stage === (c.detail.stages[c.detail.stages.length - 2]?.key ?? "resolution") ? (
                <div className="row">
                  <input className="input" style={{ maxWidth: 360 }} placeholder="written resolution" value={resolution} onChange={(e) => setResolution(e.target.value)} />
                  <button className="btn" disabled={busy || !resolution.trim()} onClick={() => void propose("case.resolve", { resolution }, "Proposed: resolve the case")}>Propose: resolve</button>
                </div>
              ) : null}
            </div>
          ) : null}
          <div className="feed" style={{ marginTop: 10 }}>
            {(d?.actions ?? []).map((a) => (
              <div key={a.id} className={`fix${a.status === "executed" ? " green" : a.status === "proposed" ? " lilac" : " red"}`}>
                <div className="head">
                  action {a.seq}: {a.summary ?? a.action} <StatusPill status={a.status} />
                </div>
                <div className="small dim">
                  {a.proposed_by_kind === "system" ? (a.rationale?.includes("readiness") ? "proposed by the land case agent" : "proposed by detection rules") : `proposed by ${names[a.proposed_by_id ?? ""] ?? "—"}`} · {formatStamp(a.created_at)}
                  {a.rationale ? ` · ${a.rationale}` : ""}
                </div>
                {a.status === "proposed" ? <div className="small">awaiting approval: <span className="phrase">{a.confirmation_phrase}</span> on the <a href="#approvals">Approvals</a> page</div> : null}
              </div>
            ))}
          </div>
        </Panel>
        <Panel title="Decisions" meta={`${d?.decisions.length ?? 0}`}>
          {(d?.decisions ?? []).map((x) => (
            <div key={x.id} className="fix green">
              <div className="head">{x.title}</div>
              <div>{x.decision}</div>
              <div className="faint small">{x.rationale} · {names[x.decided_by_id] ?? ""} · {formatStamp(x.created_at)}</div>
            </div>
          ))}
          {c.can("decision:record") ? (
            <form className="form" style={{ marginTop: 10 }} onSubmit={async (e) => { e.preventDefault(); if (await run(() => api.addDecision(c.caseId, decision), "Decision recorded")) { setDecision({ title: "", decision: "", rationale: "" }); refresh(); } }}>
              <label>Decision<input className="input" required minLength={3} value={decision.title} onChange={(e) => setDecision({ ...decision, title: e.target.value })} placeholder="Pursue quiet title" /></label>
              <label>What was decided<textarea className="input" required value={decision.decision} onChange={(e) => setDecision({ ...decision, decision: e.target.value })} /></label>
              <label>Why<input className="input" value={decision.rationale} onChange={(e) => setDecision({ ...decision, rationale: e.target.value })} /></label>
              <button className="btn" disabled={busy}>Record decision</button>
            </form>
          ) : <p className="faint small">Decisions are recorded by attorneys.</p>}
        </Panel>
      </div>
      <div className="stack">
        <Panel title="Tasks" meta={`${d?.tasks.filter((t) => t.status !== "done" && t.status !== "cancelled").length ?? 0} open`}>
          <table className="t">
            <tbody>
              {(d?.tasks ?? []).map((t) => (
                <tr key={t.id}>
                  <td>{t.title}<div className="faint small">{label(t.kind)}{t.assignee_id ? ` · ${names[t.assignee_id] ?? ""}` : ""}{t.due_date ? ` · due ${t.due_date}` : ""}</div></td>
                  <td><StatusPill status={t.status} /></td>
                  <td className="nowrap">
                    {c.can("task:write") && t.status !== "done" ? <button className="btn ghost small" onClick={async () => { if (await run(() => api.updateTask(t.id, { status: "done" }), "Task done")) refresh(); }}>Done</button> : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {c.can("task:write") ? (
            <form className="row" style={{ marginTop: 10 }} onSubmit={async (e) => { e.preventDefault(); if (await run(() => api.addTask(c.caseId, { ...task, assignee_id: task.assignee_id || null, due_date: task.due_date || null }), "Task created")) { setTask({ title: "", assignee_id: "", due_date: "" }); refresh(); } }}>
              <input className="input" style={{ maxWidth: 260 }} required minLength={3} placeholder="new task" value={task.title} onChange={(e) => setTask({ ...task, title: e.target.value })} />
              <select className="input" value={task.assignee_id} onChange={(e) => setTask({ ...task, assignee_id: e.target.value })} aria-label="Assignee">
                <option value="">unassigned</option>
                {(users.data?.users ?? []).map((u) => <option key={u.id} value={u.id}>{u.full_name}</option>)}
              </select>
              <input className="input" type="date" style={{ maxWidth: 160 }} value={task.due_date} onChange={(e) => setTask({ ...task, due_date: e.target.value })} />
              <button className="btn" disabled={busy}>Add</button>
            </form>
          ) : null}
        </Panel>
        <Panel title="Comments" meta={`${comments.data?.comments.length ?? 0}`}>
          <div className="bubbles">
            {(comments.data?.comments ?? []).map((m) => (
              <div key={m.id} className="bubble user">
                <div className="who">{m.author_name}<span className="when">{formatStamp(m.created_at)}</span></div>
                <div>{m.body}</div>
              </div>
            ))}
          </div>
          {c.me.role !== "viewer" ? (
            <form className="composer" onSubmit={async (e) => { e.preventDefault(); if (await run(() => api.addComment(c.caseId, comment))) { setComment(""); void comments.refresh(); } }}>
              <input className="input" required value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Add a note for the team" />
              <button className="btn primary">Post</button>
            </form>
          ) : null}
        </Panel>
      </div>
    </div>
  );
}
