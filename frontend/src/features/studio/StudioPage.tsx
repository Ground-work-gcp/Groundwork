import { useEffect, useState } from "react";
import type { Ctx } from "../../app/App";
import { usePoll } from "../../hooks";
import { api, studioExport, studioProjects, type StudioStep } from "../../services/api";
import { ErrorLine, Panel, useAction } from "../../components/ui";
import { Mermaid } from "./Mermaid";

/* Engineering Studio.
   Projects: a gated, versioned, approvable pipeline (requirements → stack → architecture →
   stories → scaffold) with staleness, traceability and exports. Code: one-off analysis tools. */

type Any = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
const STEPS: Array<{ key: StudioStep; label: string; blurb: string }> = [
  { key: "requirements", label: "Requirements", blurb: "Functional + non-functional, ambiguity lint" },
  { key: "stack", label: "Stack & BOM", blurb: "Google Cloud stack, versions, licences, cost" },
  { key: "architecture", label: "Architecture", blurb: "Components, flows, ADRs, diagram" },
  { key: "stories", label: "Stories", blurb: "INVEST stories with acceptance criteria" },
  { key: "scaffold", label: "Scaffold", blurb: "Repo with Terraform, Cloud Build, tests" },
];

function download(name: string, text: string, type = "text/plain") {
  const url = URL.createObjectURL(new Blob([text], { type }));
  Object.assign(document.createElement("a"), { href: url, download: name }).click();
  URL.revokeObjectURL(url);
}

function Source({ r }: { r?: Any | null }) {
  if (!r) return null;
  const s = r.source === "gemini" ? `Refined by ${r.model ?? "Gemini"}${r.cached ? " (cached)" : ""}` : r.source === "edited" ? "Edited by a person" : r.note ?? "Deterministic engine";
  return <p className="faint">{s}</p>;
}

export function StudioPage({ ctx, tab = "projects", projectId }: { ctx: Ctx; tab?: string; projectId?: string }) {
  const status = usePoll(api.studioStatus, 120_000, [], true);
  const ai = status.data?.ai;
  const active = tab === "project" ? "projects" : tab;
  return (
    <main className="main route-in">
      <div className="page-h">
        <h1 className="display">Engineering <em>studio.</em></h1>
        <p className="dim">
          {ai?.configured ? `Gemini connected · ${ai.backend} · ${ai.model}` : "Gemini not connected — deterministic engines only (see docs/deployment/google-cloud-setup.md)."}
          {status.data?.cache?.hits ? ` · ${status.data.cache.hits} cached answers reused` : ""}
        </p>
      </div>
      <nav className="tabs">
        {[["projects", "Projects"], ["code", "Code analysis"]].map(([k, l]) => <a key={k} href={`#studio/${k}`} className={active === k ? "on" : ""}>{l}</a>)}
      </nav>
      {tab === "code" ? <CodeTools ctx={ctx} /> : tab === "project" && projectId ? <ProjectView key={projectId} ctx={ctx} id={projectId} /> : <Projects ctx={ctx} />}
    </main>
  );
}

// ── project list ────────────────────────────────────────────────────────────

function Projects({ ctx }: { ctx: Ctx }) {
  const [archived, setArchived] = useState(false);
  const list = usePoll(() => studioProjects.list(archived), 60_000, [archived], true);
  const { busy, error, run } = useAction(ctx.toast);
  const [f, setF] = useState({ name: "", idea: "", language: "python", scale: "small", persona: "user" });
  const canWrite = ctx.can("studio:write");
  const create = async (thenRun: boolean) => {
    const p = await run(() => studioProjects.create(f), "Project created");
    if (!p) return;
    if (thenRun) await run(() => studioProjects.runAll(p.id), "Pipeline generated");
    window.location.hash = `studio/p/${p.id}`;
  };
  return (
    <div className="split">
      <Panel title="Projects" meta={`${list.data?.projects.length ?? 0}`} actions={<label className="faint"><input type="checkbox" checked={archived} onChange={(e) => setArchived(e.target.checked)} /> archived</label>}>
        {(list.data?.projects ?? []).length === 0 ? <p className="dim">No projects yet. Describe an idea to start the pipeline.</p> : (
          <table className="t">
            <thead><tr><th>Project</th><th>Stack</th><th>Progress</th><th>Approved</th><th>Updated</th></tr></thead>
            <tbody>{(list.data?.projects ?? []).map((p) => (
              <tr key={p.id} className="clickable" onClick={() => (window.location.hash = `studio/p/${p.id}`)}>
                <td><a href={`#studio/p/${p.id}`}>{p.name}</a>{p.status === "archived" ? <span className="faint"> · archived</span> : null}</td>
                <td>{p.language} · {p.scale}</td>
                <td><Meter pct={p.progress.percent} /> {p.progress.next ? <span className="faint">next: {p.progress.next}</span> : "complete"}</td>
                <td>{p.progress.approved.length}/{STEPS.length}</td>
                <td className="faint">{new Date(p.updated_at).toLocaleString()}</td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </Panel>
      {canWrite ? (
        <Panel title="New project">
          <div className="form">
            <label>Name<input value={f.name} maxLength={120} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="Invoice Hub" /></label>
            <label>Idea<textarea rows={6} value={f.idea} maxLength={8000} onChange={(e) => setF({ ...f, idea: e.target.value })} placeholder="Who uses it, what they do, data involved, constraints, integrations…" /></label>
            <div className="row">
              <label>Language<select value={f.language} onChange={(e) => setF({ ...f, language: e.target.value })}>{["python", "node", "java", "go"].map((l) => <option key={l}>{l}</option>)}</select></label>
              <label>Scale<select value={f.scale} onChange={(e) => setF({ ...f, scale: e.target.value })}>{["small", "medium", "large"].map((l) => <option key={l}>{l}</option>)}</select></label>
              <label>Persona<input value={f.persona} maxLength={60} onChange={(e) => setF({ ...f, persona: e.target.value })} /></label>
            </div>
            <div className="row">
              <button className="btn" disabled={busy || !f.name.trim() || f.idea.trim().length < 10} onClick={() => void create(true)}>{busy ? "Working…" : "Create & generate all"}</button>
              <button className="btn ghost" disabled={busy || !f.name.trim() || f.idea.trim().length < 10} onClick={() => void create(false)}>Create only</button>
            </div>
            <ErrorLine error={error} />
          </div>
        </Panel>
      ) : null}
    </div>
  );
}

function Meter({ pct, wide = false }: { pct: number; wide?: boolean }) {
  return <span className={`meter${wide ? " wide" : ""}`} role="meter" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}><span style={{ width: `${pct}%` }} /></span>;
}

// ── project view ────────────────────────────────────────────────────────────

function ProjectView({ ctx, id }: { ctx: Ctx; id: string }) {
  const [p, setP] = useState<Any | null>(null);
  const [sel, setSel] = useState<StudioStep | "trace">("requirements");
  const { busy, error, run } = useAction(ctx.toast);
  const [loadError, setLoadError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    studioProjects.get(id).then((r) => alive && setP(r)).catch((e) => alive && setLoadError(e instanceof Error ? e.message : String(e)));
    return () => {
      alive = false;
    };
  }, [id]);
  if (!p) return loadError ? <ErrorLine error={loadError} /> : <p className="faint">Loading…</p>;
  const canWrite = ctx.can("studio:write") && p.status === "active";
  const act = async (fn: () => Promise<Any>, msg: string) => {
    const r = await run(fn, msg);
    if (r) setP(r);
  };
  const step = sel === "trace" ? null : p.steps[sel];
  const blocked = sel !== "trace" && (p.dependencies[sel] as string[]).filter((d) => !p.steps[d]);
  return (
    <>
      <Panel title={p.name} meta={`${p.language} · ${p.scale} · persona: ${p.persona}`} actions={<>
        {canWrite ? <button className="btn" disabled={busy} onClick={() => void act(() => studioProjects.runAll(id), "Pipeline up to date")}>{busy ? "Working…" : p.progress.next ? "Generate missing & stale" : "Refresh stale"}</button> : null}
        <a className="btn ghost" href={studioExport(id, "export.md")}>Brief .md</a>
        {p.steps.stories ? <a className="btn ghost" href={studioExport(id, "stories.csv")}>Stories .csv</a> : null}
        {p.steps.scaffold ? <a className="btn ghost" href={studioExport(id, "scaffold.zip")}>Repo .zip</a> : null}
        {ctx.can("studio:write") ? <button className="btn ghost" disabled={busy} onClick={() => void act(() => studioProjects.update(id, { status: p.status === "active" ? "archived" : "active" }), p.status === "active" ? "Archived" : "Restored")}>{p.status === "active" ? "Archive" : "Restore"}</button> : null}
      </>}>
        <p className="dim">{p.idea}</p>
        <ol className="stepper">
          {STEPS.map((s) => {
            const a = p.steps[s.key];
            const state = !a ? "todo" : a.stale.length ? "stale" : a.approved ? "approved" : "done";
            return (
              <li key={s.key} className={`st-${state} ${sel === s.key ? "on" : ""}`}>
                <button onClick={() => setSel(s.key)}>
                  <strong>{s.label}</strong>
                  <span className="faint">{a ? `v${a.version} · ${state}` : "not generated"}</span>
                </button>
              </li>
            );
          })}
          <li className={`st-trace ${sel === "trace" ? "on" : ""}`}><button onClick={() => setSel("trace")}><strong>Traceability</strong><span className="faint">{p.traceability.coverage}% covered</span></button></li>
        </ol>
        <Meter pct={p.progress.percent} wide />
        <ErrorLine error={error} />
      </Panel>

      {sel === "trace" ? <Trace t={p.traceability} /> : (
        <StepPanel ctx={ctx} p={p} kind={sel} a={step} blocked={blocked || []} canWrite={canWrite} busy={busy} act={act} />
      )}
    </>
  );
}

function StepPanel({ ctx, p, kind, a, blocked, canWrite, busy, act }: {
  ctx: Ctx; p: Any; kind: StudioStep; a: Any | null; blocked: string[]; canWrite: boolean; busy: boolean;
  act: (fn: () => Promise<Any>, msg: string) => Promise<void>;
}) {
  const [notes, setNotes] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [history, setHistory] = useState<Any[] | null>(null);
  const [viewing, setViewing] = useState<Any | null>(null);
  const meta = STEPS.find((s) => s.key === kind)!;
  useEffect(() => { setEditing(null); setHistory(null); setViewing(null); }, [kind, a?.version]);
  const shown = viewing ?? a;
  const canApprove = ctx.can("studio:approve") && a && !a.approved && !a.stale.length && p.status === "active";
  let parseError: string | null = null;
  if (editing !== null) try { JSON.parse(editing); } catch (e) { parseError = (e as Error).message; }
  return (
    <Panel title={`${meta.label}${shown ? ` · v${shown.version}` : ""}`} meta={meta.blurb} actions={<>
      {canWrite && !blocked.length ? <button className="btn" disabled={busy} onClick={() => void act(() => studioProjects.run(p.id, kind, notes), `${meta.label} generated`)}>{a ? "Regenerate" : "Generate"}</button> : null}
      {canWrite && a ? <button className="btn ghost" onClick={() => setEditing(editing === null ? JSON.stringify(a.content, null, 2) : null)}>{editing === null ? "Edit" : "Cancel edit"}</button> : null}
      {canApprove ? <button className="btn ghost" disabled={busy} onClick={() => void act(() => studioProjects.approve(p.id, kind), `${meta.label} v${a.version} approved`)}>Approve v{a.version}</button> : null}
      {a ? <button className="btn ghost" onClick={() => void studioProjects.versions(p.id, kind).then((r) => setHistory(r.versions))}>History</button> : null}
    </>}>
      {blocked.length ? <p className="err">Generate {blocked.join(", ")} first.</p> : null}
      {a?.stale.length ? <p className="err">Stale: {a.stale.join(", ")} changed since v{a.version} was built. Regenerate to bring it up to date.</p> : null}
      {a?.approved ? <p className="ok">Approved baseline · {new Date(a.approved_at).toLocaleString()}</p> : null}
      {canWrite && kind !== "scaffold" && kind !== "stories" ? (
        <label className="faint">Refinement notes (optional, sent with the next generation)
          <textarea rows={2} style={{ width: "100%" }} value={notes} maxLength={8000} onChange={(e) => setNotes(e.target.value)} placeholder="e.g. must support offline mode; budget under $200/month" />
        </label>
      ) : null}
      {history ? (
        <table className="t">
          <thead><tr><th>Version</th><th>Source</th><th>Built from</th><th>Approved</th><th>Created</th><th /></tr></thead>
          <tbody>{history.map((h) => (
            <tr key={h.version}>
              <td>v{h.version}</td><td>{h.source}{h.model ? ` · ${h.model}` : ""}</td>
              <td className="faint">{Object.entries(h.based_on).map(([k, v]) => `${k} v${v}`).join(", ") || "—"}</td>
              <td>{h.approved ? "yes" : ""}</td><td className="faint">{new Date(h.created_at).toLocaleString()}</td>
              <td><button className="btn ghost" onClick={() => void studioProjects.version(p.id, kind, h.version).then(setViewing)}>View</button></td>
            </tr>
          ))}</tbody>
        </table>
      ) : null}
      {viewing ? <p className="faint">Viewing v{viewing.version} (read-only). <button className="btn ghost" onClick={() => setViewing(null)}>Back to latest</button></p> : null}
      {editing !== null ? (
        <div className="form">
          <textarea rows={18} className="mono" value={editing} onChange={(e) => setEditing(e.target.value)} spellCheck={false} />
          {parseError ? <p className="err">{parseError}</p> : null}
          <button className="btn" disabled={busy || !!parseError} onClick={() => void act(() => studioProjects.edit(p.id, kind, JSON.parse(editing)), "Saved as a new version").then(() => setEditing(null))}>Save as v{(a?.version ?? 0) + 1}</button>
        </div>
      ) : shown ? <Artifact kind={kind} c={shown.content} /> : !blocked.length ? <p className="dim">Not generated yet.</p> : null}
    </Panel>
  );
}

function Artifact({ kind, c }: { kind: StudioStep; c: Any }) {
  if (kind === "requirements") return (
    <>
      <Source r={c} />
      <p><strong>{c.summary}</strong> {c.quality_score != null ? <span className="faint">· clarity {c.quality_score}/100</span> : null}</p>
      {c.ambiguities?.length ? (
        <details open><summary className="err">{c.ambiguities.length} ambiguous term(s) to resolve</summary>
          <ul>{c.ambiguities.map((a: Any, i: number) => <li key={i}><strong>{a.term}</strong> — {a.suggestion} <span className="faint">“{a.text}”</span></li>)}</ul>
        </details>
      ) : null}
      <h3>Functional</h3><ul>{(c.functional ?? []).map((x: string) => <li key={x}>{x}</li>)}</ul>
      <h3>Non-functional</h3><ul>{(c.non_functional ?? []).map((x: string) => <li key={x}>{x}</li>)}</ul>
      <h3>Assumptions</h3><ul>{(c.assumptions ?? []).map((x: string) => <li key={x}>{x}</li>)}</ul>
      <h3>Open questions</h3><ul>{(c.open_questions ?? []).map((x: string) => <li key={x}>{x}</li>)}</ul>
    </>
  );
  if (kind === "stack") return (
    <>
      <Source r={c} />
      <p>Estimated monthly cost: <strong>{c.estimated_monthly_cost}</strong></p>
      <table className="t">
        <thead><tr><th>Layer</th><th>Component</th><th>Version</th><th>License</th><th>Cost</th><th>Why</th></tr></thead>
        <tbody>{(c.stack ?? []).map((s: Any, i: number) => (
          <tr key={i}><td>{s.layer}</td><td>{s.choice}</td><td>{c.bom?.[i]?.version}</td><td>{c.bom?.[i]?.license}</td><td>{c.bom?.[i]?.cost_tier}</td><td className="dim">{s.why}</td></tr>
        ))}</tbody>
      </table>
      <button className="btn ghost" onClick={() => download("bom.csv", ["layer,component,version,license,cost_tier", ...(c.bom ?? []).map((b: Any) => [b.layer, b.component, b.version, b.license, b.cost_tier].map((v) => `"${String(v ?? "").replace(/"/g, '""')}"`).join(","))].join("\n"), "text/csv")}>BOM .csv</button>
    </>
  );
  if (kind === "architecture") return (
    <>
      <Source r={c} />
      <p><strong>{c.style}</strong></p>
      <Mermaid code={c.mermaid ?? ""} title="Architecture" />
      <div className="split">
        <div><h3>Components</h3><ul>{(c.components ?? []).map((x: Any) => <li key={x.id}><strong>{x.name}</strong> — {x.technology}</li>)}</ul></div>
        <div><h3>Decisions</h3><ul>{(c.decisions ?? []).map((d: string) => <li key={d}>{d}</li>)}</ul></div>
      </div>
    </>
  );
  if (kind === "stories") {
    const st = (c.stories ?? []) as Any[];
    return (
      <>
        <Source r={c} />
        <p className="faint">{st.length} stories · {st.reduce((n, s) => n + (Number(s.points) || 0), 0)} points · {st.filter((s) => s.invest_warnings?.length).length} with INVEST warnings</p>
        {st.map((s) => (
          <details key={s.id}>
            <summary><strong>{s.id}</strong> {s.title} <span className="faint">· {s.requirement} · {s.priority} · {s.points} pts</span>{s.invest_warnings?.length ? <span className="err"> · ⚠ {s.invest_warnings.length}</span> : null}</summary>
            <p>As a <em>{s.as_a}</em>, I want {s.i_want}, so that {s.so_that}.</p>
            <ul>{(s.acceptance ?? []).map((x: string) => <li key={x}>{x}</li>)}</ul>
            {s.invest_warnings?.length ? <ul className="err">{s.invest_warnings.map((w: string) => <li key={w}>{w}</li>)}</ul> : null}
          </details>
        ))}
      </>
    );
  }
  return (
    <>
      <p className="faint">{(c.files ?? []).length} files · Google Cloud layers: {(c.layers ?? []).join(", ") || "core"}</p>
      {(c.files ?? []).map((f: Any) => (
        <details key={f.path}><summary><code>{f.path}</code></summary>
          <div className="row"><button className="btn ghost" onClick={() => void navigator.clipboard?.writeText(f.content)}>Copy</button></div>
          <pre className="code">{f.content}</pre>
        </details>
      ))}
    </>
  );
}

function Trace({ t }: { t: Any }) {
  return (
    <Panel title="Traceability matrix" meta={`${t.coverage}% of requirements have stories`}>
      {t.uncovered.length ? <p className="err">Uncovered: {t.uncovered.join(", ")} — regenerate stories.</p> : <p className="ok">Every functional requirement traces to a story and acceptance tests.</p>}
      <table className="t">
        <thead><tr><th>Requirement</th><th>Stories</th><th>Acceptance tests</th></tr></thead>
        <tbody>{t.rows.map((r: Any) => (
          <tr key={r.requirement}><td><strong>{r.requirement}</strong> <span className="dim">{r.text.replace(/^FR-\d+:\s*/, "")}</span></td><td>{r.stories.join(", ") || "—"}</td><td className="faint">{r.tests.length ? r.tests.join(", ") : "—"}</td></tr>
        ))}</tbody>
      </table>
    </Panel>
  );
}

// ── one-off code tools ──────────────────────────────────────────────────────

function CodeTools({ ctx }: { ctx: Ctx }) {
  const { busy, error, run } = useAction(ctx.toast);
  const [code, setCode] = useState("");
  const [language, setLanguage] = useState("python");
  const [module, setModule] = useState("module");
  const [health, setHealth] = useState<Any | null>(null);
  const [viz, setViz] = useState<Any | null>(null);
  const [tests, setTests] = useState<Any | null>(null);
  const body = { code, language, module };
  const ok = code.trim().length > 0 && /^[A-Za-z_][\w.]*$/.test(module);
  const all = async () => {
    const [h, v, t] = await Promise.all([run(() => api.studio("code-health", body)), run(() => api.studio("visualize", body)), run(() => api.studio("tests", body))]);
    setHealth(h); setViz(v); setTests(t);
  };
  return (
    <>
      <Panel title="Source" actions={<>
        <button className="btn" disabled={busy || !ok} onClick={() => void all()}>{busy ? "Analysing…" : "Analyse all"}</button>
        <button className="btn ghost" disabled={busy || !ok} onClick={() => void run(() => api.studio("code-health", body)).then((r) => r && setHealth(r))}>Health</button>
        <button className="btn ghost" disabled={busy || !ok} onClick={() => void run(() => api.studio("visualize", body)).then((r) => r && setViz(r))}>Visualise</button>
        <button className="btn ghost" disabled={busy || !ok} onClick={() => void run(() => api.studio("tests", body)).then((r) => r && setTests(r))}>Unit tests</button>
      </>}>
        <div className="row">
          <label>Language <select value={language} onChange={(e) => setLanguage(e.target.value)}>{["python", "javascript", "typescript", "java", "go"].map((l) => <option key={l}>{l}</option>)}</select></label>
          <label>Module <input value={module} onChange={(e) => setModule(e.target.value)} /></label>
          <label className="faint">or load a file <input type="file" accept=".py,.js,.ts,.tsx,.java,.go" onChange={(e) => {
            const file = e.target.files?.[0];
            if (!file) return;
            if (file.size > 100_000) return ctx.toast("File is larger than 100 KB", "info");
            void file.text().then(setCode);
            setModule(file.name.replace(/\.\w+$/, "").replace(/[^\w]/g, "_"));
            const ext = file.name.split(".").pop() ?? "";
            setLanguage({ py: "python", js: "javascript", ts: "typescript", tsx: "typescript", java: "java", go: "go" }[ext] ?? "python");
          }} /></label>
        </div>
        <textarea rows={14} className="mono" style={{ width: "100%" }} placeholder="Paste code here…" value={code} onChange={(e) => setCode(e.target.value)} spellCheck={false} />
        <ErrorLine error={error} />
      </Panel>
      {health && (
        <Panel title="Code health" meta={`${health.grade} · ${health.score}/100 · ${health.code_lines} LOC · ${health.functions.length} functions`}>
          <Source r={health} />
          {health.recommendations?.length ? <ul>{health.recommendations.map((r: string) => <li key={r}>{r}</li>)}</ul> : null}
          {health.issues.length === 0 ? <p className="ok">No issues found.</p> : (
            <table className="t">
              <thead><tr><th>Line</th><th>Severity</th><th>Rule</th><th>Message</th></tr></thead>
              <tbody>{health.issues.map((i: Any, n: number) => <tr key={n}><td>{i.line}</td><td className={`sev-${i.severity}`}>{i.severity}</td><td>{i.rule}</td><td>{i.message}</td></tr>)}</tbody>
            </table>
          )}
          <h3>Functions</h3>
          <table className="t">
            <thead><tr><th>Name</th><th>Line</th><th>Length</th><th>Complexity</th><th>Docstring</th></tr></thead>
            <tbody>{health.functions.map((f: Any, n: number) => <tr key={n}><td>{f.name}</td><td>{f.line ?? "–"}</td><td>{f.length ?? "–"}</td><td>{f.complexity ?? "–"}</td><td>{f.documented == null ? "–" : f.documented ? "yes" : "no"}</td></tr>)}</tbody>
          </table>
        </Panel>
      )}
      {viz && <Panel title="System visualisation" meta={viz.kind}><Mermaid code={viz.mermaid} title="Code structure" /></Panel>}
      {tests && (
        <Panel title="Generated unit tests" meta={`${tests.framework} · ${tests.filename} · ${tests.count} targets`} actions={<>
          <button className="btn ghost" onClick={() => void navigator.clipboard?.writeText(tests.code)}>Copy</button>
          <button className="btn ghost" onClick={() => download(tests.filename, tests.code)}>Download</button>
        </>}>
          <Source r={tests} />
          <pre className="code">{tests.code}</pre>
        </Panel>
      )}
    </>
  );
}
