/* The centralized workflow: Groundwork's real pipeline as a live node graph.
   Every number comes from GET /flow or /cases/:id/flow and refreshes every 10 s — nothing here
   is static. The critical path (CPM) and least-effort route (Dijkstra) are computed server-side. */
import { useMemo, useState } from "react";
import type { Ctx } from "../../app/App";
import { usePoll } from "../../hooks";
import { api } from "../../services/api";
import type { Flow, FlowEdge, FlowNode, NeedsItem } from "../../types";
import { Empty, ErrorLine, SeverityPill, usd } from "../../components/ui";

const CELL_W = 232;
const CELL_H = 156;
const NODE_W = 200;
const NODE_H = 118;
const PAD = 16;

type Highlight = "critical" | "shortest" | "none";

function pos(n: FlowNode) {
  return { x: PAD + n.col * CELL_W, y: PAD + n.row * CELL_H };
}

function edgePath(a: FlowNode, b: FlowNode): string {
  const pa = pos(a);
  const pb = pos(b);
  if (a.col === b.col) {
    const down = b.row > a.row;
    const x = pa.x + NODE_W / 2;
    const y1 = down ? pa.y + NODE_H : pa.y;
    const y2 = down ? pb.y : pb.y + NODE_H;
    return `M ${x} ${y1} C ${x + 40} ${(y1 + y2) / 2}, ${x + 40} ${(y1 + y2) / 2}, ${x} ${y2}`;
  }
  const x1 = pa.x + NODE_W;
  const y1 = pa.y + NODE_H / 2;
  const x2 = pb.x;
  const y2 = pb.y + NODE_H / 2;
  const mid = (x1 + x2) / 2;
  return `M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`;
}

function ago(iso: string, now: number): string {
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  return s < 60 ? `${s}s ago` : `${Math.round(s / 60)}m ago`;
}

export function FlowCanvas({ ctx, caseId }: { ctx: Ctx; caseId?: string }) {
  const q = usePoll<Flow>(() => (caseId ? api.caseFlow(caseId) : api.flow()), 10_000, [caseId], true);
  const [mode, setMode] = useState<Highlight>("critical");
  const [hover, setHover] = useState<string | null>(null);
  const f = q.data;
  const byId = useMemo(() => Object.fromEntries((f?.nodes ?? []).map((n) => [n.id, n])), [f]);

  if (!f) return q.error ? <ErrorLine error={q.error} /> : <p className="faint">Computing the workflow…</p>;

  const cols = Math.max(...f.nodes.map((n) => n.col)) + 1;
  const rows = Math.max(...f.nodes.map((n) => n.row)) + 1;
  const width = PAD * 2 + (cols - 1) * CELL_W + NODE_W;
  const height = PAD * 2 + (rows - 1) * CELL_H + NODE_H;
  const onPath = new Set(mode === "critical" ? f.critical_path : mode === "shortest" ? f.shortest_path.nodes : []);
  const lit = (e: FlowEdge) => (mode === "critical" ? e.critical : mode === "shortest" ? e.shortest : false);
  const ordered = [...f.nodes].sort((a, b) => a.col - b.col || a.row - b.row);

  return (
    <div className="flow">
      <div className="flow-top">
        {f.next_action ? (
          <a className="flow-next" href={f.next_action.href}>
            <span className="k">Next best action · {f.next_action.label}</span>
            <span className="v">{f.next_action.step}</span>
            <span className="m mono">{f.next_action.remaining} item(s) on the critical path</span>
          </a>
        ) : (
          <div className="flow-next done">
            <span className="k">Nothing blocks resolution</span>
            <span className="v">Every stage on the critical path is clear.</span>
          </div>
        )}
        <div className="flow-stats">
          <div><span className="n">{f.remaining_work}</span><span className="l">open work items</span></div>
          <div><span className="n">{f.needs_you.length}</span><span className="l">need you</span></div>
          <div><span className="n">{f.shortest_path.cost ?? "–"}</span><span className="l">least-effort cost</span></div>
        </div>
      </div>

      <div className="flow-toolbar" role="radiogroup" aria-label="Highlight a path">
        {(["critical", "shortest", "none"] as Highlight[]).map((m) => (
          <button key={m} type="button" role="radio" aria-checked={mode === m} className={`chip${mode === m ? " on" : ""}`} onClick={() => setMode(m)}>
            {m === "critical" ? "Critical path (CPM)" : m === "shortest" ? "Least effort (Dijkstra)" : "No highlight"}
          </button>
        ))}
        <span className="faint mono flow-updated" aria-live="polite">
          live · updated {ago(f.generated_at, Date.now())}{q.error ? " · reconnecting" : ""}
        </span>
      </div>

      <div className="flow-canvas-wrap">
        <div className="flow-canvas" style={{ width, height }}>
          <svg className="flow-edges" width={width} height={height} aria-hidden="true">
            <defs>
              <marker id="gw-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                <path d="M0,0 L8,4 L0,8 z" fill="currentColor" />
              </marker>
            </defs>
            {f.edges.map((e) => {
              const a = byId[e.from];
              const b = byId[e.to];
              if (!a || !b) return null;
              const active = b.status === "attention";
              const cls = ["edge", lit(e) ? "lit" : "", e.alt ? "alt" : "", e.audit ? "audit" : "", active ? "active" : "", hover && (hover === e.from || hover === e.to) ? "hover" : ""].filter(Boolean).join(" ");
              const d = edgePath(a, b);
              return (
                <g key={`${e.from}-${e.to}`} className={cls}>
                  <path d={d} markerEnd="url(#gw-arrow)" />
                  {e.label && a.col !== b.col ? (
                    <text x={(pos(a).x + NODE_W + pos(b).x) / 2} y={(pos(a).y + pos(b).y) / 2 + NODE_H / 2 - 6} textAnchor="middle">
                      {e.label}
                    </text>
                  ) : null}
                </g>
              );
            })}
          </svg>
          {f.nodes.map((n) => {
            const p = pos(n);
            return (
              <a
                key={n.id}
                href={n.href}
                className={`fnode ${n.status} layer-${n.layer}${onPath.has(n.id) ? " onpath" : ""}${n.id === "needs_you" && n.remaining ? " urgent" : ""}`}
                style={{ left: p.x, top: p.y, width: NODE_W, height: NODE_H }}
                onMouseEnter={() => setHover(n.id)}
                onMouseLeave={() => setHover(null)}
                onFocus={() => setHover(n.id)}
                onBlur={() => setHover(null)}
                aria-label={`${n.label}: ${n.metric} ${n.unit}. ${n.sub}. ${n.remaining} remaining.`}
              >
                <span className="fnode-h">
                  <span className={`dot ${n.status}`} />
                  {n.label}
                  {n.remaining ? <span className="badge">{n.remaining}</span> : null}
                </span>
                <span className="fnode-n">
                  {n.metric.toLocaleString("en-US")} <small>{n.unit}</small>
                </span>
                <span className="fnode-s">{n.sub}</span>
              </a>
            );
          })}
        </div>
      </div>

      {/* Phone layout: the same live nodes as a list, in pipeline order. */}
      <ol className="flow-list">
        {ordered.map((n) => (
          <li key={n.id} className={`${n.status}${onPath.has(n.id) ? " onpath" : ""}`}>
            <a href={n.href}>
              <span className={`dot ${n.status}`} />
              <span className="t">{n.label}</span>
              <span className="n mono">{n.metric.toLocaleString("en-US")} {n.unit}</span>
              {n.remaining ? <span className="badge">{n.remaining}</span> : null}
              <span className="s">{n.sub}</span>
            </a>
          </li>
        ))}
      </ol>

      <div className="flow-lower">
        <NeedsYou items={f.needs_you} />
        {f.cases ? (
          <section className="panel">
            <div className="panel-h">
              <h2>Open cases by remaining work</h2>
              <span className="meta">{f.scope.open_cases ?? 0} open</span>
            </div>
            <div className="panel-b">
              {f.cases.length ? (
                <ul className="flow-cases">
                  {f.cases.map((c) => (
                    <li key={c.id}>
                      <a href={`#case/${c.id}/flow`}>
                        <span className="mono faint">{c.reference}</span> <strong>{c.title}</strong>
                        <span className="s">{c.next_action ? `next: ${c.next_action.label} — ${c.next_action.step}` : "clear"}</span>
                      </a>
                      <span className="badge">{c.remaining}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <Empty title="No open cases.">{ctx.can("case:write") ? <a href="#cases">Open the first case</a> : null}</Empty>
              )}
            </div>
          </section>
        ) : null}
      </div>

      <details className="flow-how">
        <summary>How this is computed</summary>
        <ul>
          {Object.entries(f.algorithms).map(([k, v]) => (
            <li key={k}><strong>{k.replace(/_/g, " ")}:</strong> {v}</li>
          ))}
          <li>Critical path now: <span className="mono">{f.critical_path.join(" → ")}</span></li>
          <li>Least-effort route now: <span className="mono">{f.shortest_path.nodes.join(" → ")}</span></li>
        </ul>
      </details>
    </div>
  );
}

export function NeedsYou({ items }: { items: NeedsItem[] }) {
  const waiting = items.reduce((sum, i) => sum + Math.abs(i.amount_cents), 0);
  return (
    <section className="needs">
      <div className="needs-h">
        <span className="k">Needs you</span>
        <strong>{items.length ? `${items.length} question${items.length === 1 ? "" : "s"} waiting on you.` : "Nothing is waiting on you."}</strong>
        {waiting ? <span className="m mono">{usd(waiting)} involved</span> : null}
      </div>
      {items.length ? (
        <ul className="needs-list">
          {items.map((i) => (
            <li key={`${i.kind}-${i.id ?? i.href}`}>
              <a href={i.href}>
                <span className="t">{i.title}</span>
                <span className="s">
                  <span className="mono faint">{i.case_reference}</span> · Suggests: {i.suggests}
                </span>
              </a>
              <span className="r">
                <SeverityPill severity={i.severity} />
                {i.amount_cents ? <span className="mono">{usd(i.amount_cents)}</span> : null}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

export function FlowPage({ ctx }: { ctx: Ctx }) {
  return (
    <main className="main route-in">
      <div className="page-h">
        <h1 className="display">Flow</h1>
        <p className="lede">Every open case through one pipeline — evidence, finance and control — computed live from the records.</p>
      </div>
      <FlowCanvas ctx={ctx} />
    </main>
  );
}
