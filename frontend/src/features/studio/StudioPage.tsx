import { useState } from "react";
import type { Ctx } from "../../app/App";
import { usePoll } from "../../hooks";
import { api } from "../../services/api";
import { ErrorLine, Panel, useAction } from "../../components/ui";

/* Engineering Studio: idea → requirements → stack/BOM → architecture → scaffold → stories,
   plus code health, visualisation and unit-test generation. Each step feeds the next. */

type Any = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
const TABS = [["plan", "Plan a project"], ["code", "Analyse code"]] as const;

function Source({ r }: { r?: Any | null }) {
  if (!r) return null;
  return <p className="faint">{r.source === "gemini" ? `Refined by ${r.model ?? "Gemini"}` : r.note ?? "Deterministic engine"}</p>;
}

function Copy({ text }: { text: string }) {
  return <button className="btn ghost" onClick={() => void navigator.clipboard?.writeText(text)}>Copy</button>;
}

function download(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
  const a = Object.assign(document.createElement("a"), { href: url, download: name });
  a.click();
  URL.revokeObjectURL(url);
}

export function StudioPage({ ctx, tab = "plan" }: { ctx: Ctx; tab?: string }) {
  const status = usePoll(api.studioStatus, 120_000, [], true);
  const ai = status.data?.ai;
  return (
    <main className="main route-in">
      <div className="page-h">
        <h1 className="display">Engineering <em>studio.</em></h1>
        <p className="dim">{ai?.configured ? `Gemini connected (${ai.backend}, ${ai.model}).` : "Gemini not connected — rule engine only. See docs/deployment/google-cloud-setup.md."}</p>
      </div>
      <nav className="tabs">
        {TABS.map(([k, l]) => <a key={k} href={`#studio/${k}`} className={tab === k ? "on" : ""}>{l}</a>)}
      </nav>
      {tab === "code" ? <CodeTools ctx={ctx} /> : <Planner ctx={ctx} />}
    </main>
  );
}

function Planner({ ctx }: { ctx: Ctx }) {
  const { busy, error, run } = useAction(ctx.toast);
  const [idea, setIdea] = useState("");
  const [language, setLanguage] = useState("python");
  const [scale, setScale] = useState("small");
  const [req, setReq] = useState<Any | null>(null);
  const [stack, setStack] = useState<Any | null>(null);
  const [arch, setArch] = useState<Any | null>(null);
  const [scaffold, setScaffold] = useState<Any | null>(null);
  const [stories, setStories] = useState<Any | null>(null);
  const reqText = req ? [req.summary, ...req.functional, ...req.non_functional].join("\n") : idea;

  const all = async () => {
    const r = await run(() => api.studio("requirements", { idea }));
    if (!r) return;
    setReq(r);
    const text = [r.summary, ...r.functional, ...r.non_functional].join("\n");
    const s = await run(() => api.studio("stack", { requirements: text, language, scale }));
    if (!s) return;
    setStack(s);
    const [a, st, sc] = await Promise.all([
      run(() => api.studio("architecture", { requirements: text, stack: s.stack })),
      run(() => api.studio("stories", { requirements: r.functional })),
      run(() => api.studio("scaffold", { name: idea.split(/[.\n]/)[0].slice(0, 60) || "app", language: language === "node" ? "node" : "python" })),
    ]);
    setArch(a); setStories(st); setScaffold(sc);
    ctx.toast("Project plan generated");
  };

  return (
    <>
      <Panel title="1 · Idea" actions={<button className="btn" disabled={busy || idea.trim().length < 3} onClick={() => void all()}>{busy ? "Working…" : "Generate everything"}</button>}>
        <textarea rows={5} style={{ width: "100%" }} placeholder="Describe the product: who uses it, what they do, constraints…" value={idea} onChange={(e) => setIdea(e.target.value)} />
        <div className="row">
          <label>Language <select value={language} onChange={(e) => setLanguage(e.target.value)}>{["python", "node", "java", "go"].map((l) => <option key={l}>{l}</option>)}</select></label>
          <label>Scale <select value={scale} onChange={(e) => setScale(e.target.value)}>{["small", "medium", "large"].map((l) => <option key={l}>{l}</option>)}</select></label>
          <button className="btn ghost" disabled={busy || idea.trim().length < 3} onClick={() => void run(() => api.studio("requirements", { idea, notes: req ? req.functional.join("\n") : "" })).then((r) => r && setReq(r))}>Requirements only / refine</button>
        </div>
        <ErrorLine error={error} />
      </Panel>

      {req && (
        <Panel title="2 · Requirements" meta={req.quality_score != null ? `clarity ${req.quality_score}/100` : undefined}>
          <Source r={req} />
          <p><strong>{req.summary}</strong></p>
          <h3>Functional</h3><ul>{req.functional.map((x: string) => <li key={x}>{x}</li>)}</ul>
          <h3>Non-functional</h3><ul>{req.non_functional.map((x: string) => <li key={x}>{x}</li>)}</ul>
          <h3>Open questions</h3><ul>{req.open_questions.map((x: string) => <li key={x}>{x}</li>)}</ul>
        </Panel>
      )}

      {stack && (
        <Panel title="3 · Technology stack & BOM" meta={`est. ${stack.estimated_monthly_cost}/month`} actions={<button className="btn ghost" onClick={() => download("bom.csv", ["layer,component,version,license,cost_tier", ...stack.bom.map((b: Any) => [b.layer, b.component, b.version, b.license, b.cost_tier].map((v) => `"${v}"`).join(","))].join("\n"))}>BOM .csv</button>}>
          <Source r={stack} />
          <table className="t">
            <thead><tr><th>Layer</th><th>Choice</th><th>Version</th><th>License</th><th>Cost</th><th>Why</th></tr></thead>
            <tbody>{stack.stack.map((s: Any, i: number) => (
              <tr key={i}><td>{s.layer}</td><td>{s.choice}</td><td>{stack.bom[i]?.version}</td><td>{stack.bom[i]?.license}</td><td>{stack.bom[i]?.cost_tier}</td><td className="dim">{s.why}</td></tr>
            ))}</tbody>
          </table>
          <button className="btn ghost" disabled={busy} onClick={() => void run(() => api.studio("stack", { requirements: reqText, language, scale })).then((s) => s && setStack(s))}>Re-select for {language}/{scale}</button>
        </Panel>
      )}

      {arch && (
        <Panel title="4 · Architecture" meta={arch.style}>
          <Source r={arch} />
          <ul>{arch.components.map((c: Any) => <li key={c.id}><strong>{c.name}</strong> — {c.technology}</li>)}</ul>
          {arch.flows && <ul className="dim">{arch.flows.map((f: Any, i: number) => <li key={i}>{f.from} → {f.to}: {f.label}</li>)}</ul>}
          <h3>Decisions</h3><ul>{arch.decisions.map((d: string) => <li key={d}>{d}</li>)}</ul>
          <Mermaid code={arch.mermaid} />
        </Panel>
      )}

      {scaffold && (
        <Panel title="5 · Project scaffold" meta={`${scaffold.files.length} files`} actions={<button className="btn ghost" onClick={() => download("scaffold.txt", scaffold.files.map((f: Any) => `===== ${f.path} =====\n${f.content}`).join("\n"))}>Download all</button>}>
          {scaffold.files.map((f: Any) => (
            <details key={f.path}><summary><code>{f.path}</code></summary><pre className="code">{f.content}</pre></details>
          ))}
        </Panel>
      )}

      {stories && (
        <Panel title="6 · Feature stories" meta={`${stories.stories.length} stories · ${stories.stories.reduce((n: number, s: Any) => n + (s.points || 0), 0)} pts`}>
          <Source r={stories} />
          {stories.stories.map((s: Any) => (
            <details key={s.id}>
              <summary><strong>{s.id}</strong> {s.title} <span className="faint">· {s.priority} · {s.points} pts</span></summary>
              <p>As a <em>{s.as_a}</em>, I want {s.i_want}, so that {s.so_that}.</p>
              <ul>{s.acceptance.map((a: string) => <li key={a}>{a}</li>)}</ul>
            </details>
          ))}
        </Panel>
      )}
    </>
  );
}

function Mermaid({ code }: { code: string }) {
  return (
    <div>
      <div className="row"><h3>Mermaid diagram</h3><Copy text={code} /><a className="btn ghost" href="https://mermaid.live" target="_blank" rel="noreferrer">Open mermaid.live</a></div>
      <pre className="code">{code}</pre>
    </div>
  );
}

function CodeTools({ ctx }: { ctx: Ctx }) {
  const { busy, error, run } = useAction(ctx.toast);
  const [code, setCode] = useState("");
  const [language, setLanguage] = useState("python");
  const [module, setModule] = useState("module");
  const [health, setHealth] = useState<Any | null>(null);
  const [viz, setViz] = useState<Any | null>(null);
  const [tests, setTests] = useState<Any | null>(null);
  const body = { code, language, module };
  const ok = code.trim().length > 0;
  return (
    <>
      <Panel title="Source" actions={<>
        <button className="btn" disabled={busy || !ok} onClick={() => void run(() => api.studio("code-health", body)).then((r) => r && setHealth(r))}>Health</button>
        <button className="btn" disabled={busy || !ok} onClick={() => void run(() => api.studio("visualize", body)).then((r) => r && setViz(r))}>Visualise</button>
        <button className="btn" disabled={busy || !ok} onClick={() => void run(() => api.studio("tests", body)).then((r) => r && setTests(r))}>Unit tests</button>
      </>}>
        <div className="row">
          <label>Language <select value={language} onChange={(e) => setLanguage(e.target.value)}>{["python", "javascript", "typescript", "java", "go"].map((l) => <option key={l}>{l}</option>)}</select></label>
          <label>Module <input value={module} onChange={(e) => setModule(e.target.value)} /></label>
        </div>
        <textarea rows={14} style={{ width: "100%", fontFamily: "monospace" }} placeholder="Paste code here…" value={code} onChange={(e) => setCode(e.target.value)} />
        <ErrorLine error={error} />
      </Panel>
      {health && (
        <Panel title="Code health" meta={`${health.grade} · ${health.score}/100 · ${health.code_lines} LOC`}>
          <Source r={health} />
          {health.recommendations?.length ? <ul>{health.recommendations.map((r: string) => <li key={r}>{r}</li>)}</ul> : null}
          <table className="t">
            <thead><tr><th>Line</th><th>Severity</th><th>Rule</th><th>Message</th></tr></thead>
            <tbody>{health.issues.map((i: Any, n: number) => <tr key={n}><td>{i.line}</td><td>{i.severity}</td><td>{i.rule}</td><td>{i.message}</td></tr>)}</tbody>
          </table>
          {health.issues.length === 0 && <p className="dim">No issues found.</p>}
          <h3>Functions</h3>
          <table className="t">
            <thead><tr><th>Name</th><th>Line</th><th>Length</th><th>Complexity</th><th>Docstring</th></tr></thead>
            <tbody>{health.functions.map((f: Any, n: number) => <tr key={n}><td>{f.name}</td><td>{f.line ?? "–"}</td><td>{f.length ?? "–"}</td><td>{f.complexity ?? "–"}</td><td>{f.documented == null ? "–" : f.documented ? "yes" : "no"}</td></tr>)}</tbody>
          </table>
        </Panel>
      )}
      {viz && <Panel title="System visualisation" meta={viz.kind}><Mermaid code={viz.mermaid} /></Panel>}
      {tests && (
        <Panel title="Generated unit tests" meta={`${tests.framework} · ${tests.filename}`} actions={<><Copy text={tests.code} /><button className="btn ghost" onClick={() => download(tests.filename, tests.code)}>Download</button></>}>
          <Source r={tests} />
          <pre className="code">{tests.code}</pre>
        </Panel>
      )}
    </>
  );
}
