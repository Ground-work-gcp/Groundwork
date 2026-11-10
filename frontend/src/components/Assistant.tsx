/* The right-hand assistant (a bottom sheet on phones). Every answer comes from POST /assistant,
   which routes to grounded sources — case evidence with citations, the live Needs-you queue,
   the workflow graph, case search, or the user's memory graph. Memory is a personalized
   PageRank over the user's own audit trail (GET /memory/graph), drawn here as a graph. */
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { api } from "../services/api";
import type { AssistantReply, MemoryGraph, MemorySuggestion } from "../types";
import { go } from "../navigation/routes";

interface Turn { role: "user" | "assistant"; text: string; reply?: AssistantReply; error?: boolean }

const STATUS_LABEL: Record<string, string> = { verified: "verified", awaiting_review: "awaiting review", conflicting: "conflicting", unextracted: "not extracted" };
const KIND_COLOR: Record<string, string> = { user: "var(--ink)", case: "var(--deep)", document: "#5b6cff", finding: "var(--coral)", workflow_action: "#b04fd6", property: "#2f8f6f", topic: "var(--warn)" };

function load(key: string): Turn[] {
  try {
    const raw = window.sessionStorage.getItem(key);
    return raw ? (JSON.parse(raw) as Turn[]) : [];
  } catch {
    return [];
  }
}

export function Assistant({ userId, caseId }: { userId: string; caseId?: string }) {
  const storeKey = `gw-assistant-${userId}`;
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<"chat" | "memory">("chat");
  const [turns, setTurns] = useState<Turn[]>(() => load(storeKey));
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [scopeCase, setScopeCase] = useState(true);
  const [memory, setMemory] = useState<MemoryGraph | null>(null);
  const [memErr, setMemErr] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const inCase = !!caseId && scopeCase;

  useEffect(() => {
    try {
      window.sessionStorage.setItem(storeKey, JSON.stringify(turns.slice(-30)));
    } catch {
      /* storage unavailable: the chat still works for this view */
    }
    endRef.current?.scrollIntoView({ block: "end" });
  }, [turns, storeKey]);

  const refreshMemory = () =>
    api.memory().then((m) => { setMemory(m); setMemErr(null); }).catch((e: Error) => setMemErr(e.message));

  useEffect(() => {
    if (open) {
      void refreshMemory();
      window.setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [open]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const send = async (message: string) => {
    const m = message.trim();
    if (!m || busy) return;
    setText("");
    setTurns((t) => [...t, { role: "user", text: m }]);
    setBusy(true);
    try {
      const reply = await api.assistant(m, inCase ? caseId : undefined);
      setTurns((t) => [...t, { role: "assistant", text: reply.reply, reply }]);
      void refreshMemory();
    } catch (e) {
      setTurns((t) => [...t, { role: "assistant", text: e instanceof Error ? e.message : String(e), error: true }]);
    } finally {
      setBusy(false);
    }
  };

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    void send(text);
  };

  const follow = (href: string | null | undefined) => {
    if (!href) return;
    go(href.replace(/^#/, ""));
    if (window.matchMedia("(max-width: 720px)").matches) setOpen(false);
  };

  const last = [...turns].reverse().find((t) => t.reply)?.reply;
  const suggestions: MemorySuggestion[] = (last?.suggestions ?? memory?.suggestions ?? []).slice(0, 4);
  const starters = inCase
    ? ["Who are the current owners?", "What tax years are unpaid?", "What conflicts are in the evidence?"]
    : ["What needs me?", "Give me the status", "What did I work on recently?"];

  return (
    <>
      <button type="button" className={`ask-fab${open ? " on" : ""}`} aria-expanded={open} aria-controls="gw-assistant" onClick={() => setOpen((o) => !o)}>
        {open ? "Close" : "Ask"}
      </button>
      <aside id="gw-assistant" className={`assistant${open ? " open" : ""}`} aria-label="Assistant" aria-hidden={!open}>
        <header className="assistant-h">
          <div className="tabs-mini" role="tablist">
            <button type="button" role="tab" aria-selected={tab === "chat"} className={tab === "chat" ? "on" : ""} onClick={() => setTab("chat")}>Assistant</button>
            <button type="button" role="tab" aria-selected={tab === "memory"} className={tab === "memory" ? "on" : ""} onClick={() => setTab("memory")}>Memory</button>
          </div>
          {caseId ? (
            <label className="scope">
              <input type="checkbox" checked={scopeCase} onChange={(e) => setScopeCase(e.target.checked)} /> this case's evidence
            </label>
          ) : null}
          <button type="button" className="btn ghost small" onClick={() => setOpen(false)} aria-label="Close assistant">✕</button>
        </header>

        {tab === "chat" ? (
          <>
            <div className="assistant-body" aria-live="polite">
              {turns.length === 0 ? (
                <div className="assistant-empty">
                  <p className="dim">
                    {inCase ? "Answers come only from this case's evidence, with citations and each source's review status." : "Answers come from your live workflow, cases and history — nothing is invented."}
                  </p>
                </div>
              ) : null}
              {turns.map((t, i) => (
                <div key={i} className={`turn t-${t.role}${t.error ? " error" : ""}`}>
                  <div className="msg">{t.text}</div>
                  {t.reply ? <ReplyDetail reply={t.reply} onOpen={follow} /> : null}
                </div>
              ))}
              {busy ? <div className="turn t-assistant"><div className="msg faint">Working…</div></div> : null}
              <div ref={endRef} />
            </div>
            <div className="assistant-chips">
              {(turns.length ? [] : starters).map((s) => (
                <button key={s} type="button" className="chip" onClick={() => void send(s)}>{s}</button>
              ))}
              {suggestions.map((s, i) => (
                <button key={`s${i}`} type="button" className="chip suggest" title={s.reason} onClick={() => (s.prompt ? void send(s.prompt) : follow(s.href))}>
                  {s.text}
                </button>
              ))}
            </div>
            <form className="assistant-form" onSubmit={onSubmit}>
              <textarea
                ref={inputRef}
                rows={2}
                value={text}
                maxLength={2000}
                placeholder={inCase ? "Ask about this case's evidence…" : "Ask: what needs me, status, find 418 Magnolia…"}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    void send(text);
                  }
                }}
              />
              <button type="submit" className="btn primary small" disabled={busy || !text.trim()}>Send</button>
            </form>
          </>
        ) : (
          <div className="assistant-body">
            {memErr ? <p className="err">{memErr}</p> : null}
            {memory ? <MemoryView memory={memory} onOpen={follow} onAsk={(p) => { setTab("chat"); void send(p); }} /> : <p className="faint">Building your memory graph…</p>}
          </div>
        )}
      </aside>
    </>
  );
}

function ReplyDetail({ reply, onOpen }: { reply: AssistantReply; onOpen: (href: string | null) => void }) {
  return (
    <div className="reply">
      <span className="mode mono">{reply.mode.startsWith("gemini") ? `Gemini · ${reply.intent}` : `${reply.mode} · ${reply.intent}`}{reply.verified === false ? " · unverified" : ""}</span>
      {reply.items.length ? (
        <ul className="reply-items">
          {reply.items.map((it, i) => (
            <li key={i}>
              <button type="button" className="linkish" onClick={() => onOpen(it.href)} disabled={!it.href}>
                <span className="t">{it.title}</span>
                {it.sub ? <span className="s">{it.sub}</span> : null}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      {reply.citations.length ? (
        <ul className="reply-cites">
          {reply.citations.map((c) => (
            <li key={c.ref}>
              <span className="mono">[{c.ref}]</span> {c.filename}, p.{c.page}
              {c.evidence_status ? <span className={`ev ${c.evidence_status}`}>{STATUS_LABEL[c.evidence_status] ?? c.evidence_status}</span> : null}
              <blockquote>{c.text.slice(0, 220)}{c.text.length > 220 ? "…" : ""}</blockquote>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function MemoryView({ memory, onOpen, onAsk }: { memory: MemoryGraph; onOpen: (href: string | null) => void; onAsk: (prompt: string) => void }) {
  const size = 320;
  const c = size / 2;
  const layout = useMemo(() => {
    const others = memory.nodes.slice(1);
    const maxScore = Math.max(...others.map((n) => n.score), 1e-9);
    const placed: Record<string, { x: number; y: number; r: number }> = { [memory.nodes[0]?.id ?? "me"]: { x: c, y: c, r: 14 } };
    others.forEach((n, i) => {
      const ring = i < 8 ? 78 : 132;
      const count = i < 8 ? Math.min(8, others.length) : Math.max(1, others.length - 8);
      const idx = i < 8 ? i : i - 8;
      const angle = (idx / count) * Math.PI * 2 - Math.PI / 2 + (i < 8 ? 0 : 0.3);
      placed[n.id] = { x: c + ring * Math.cos(angle), y: c + ring * Math.sin(angle), r: 5 + 9 * Math.sqrt(n.score / maxScore) };
    });
    return placed;
  }, [memory, c]);
  const months = Object.entries(memory.by_month).slice(-12);
  const peak = Math.max(1, ...months.map(([, v]) => v));

  if (memory.nodes.length <= 1) return <p className="dim">No history yet. As you work on cases, the things you touch most recently and most often appear here and shape the assistant's suggestions.</p>;

  return (
    <div className="memory">
      <svg viewBox={`0 0 ${size} ${size}`} className="memory-graph" role="img" aria-label="Your context graph">
        {memory.edges.map((e, i) => {
          const a = layout[e.from];
          const b = layout[e.to];
          return a && b ? <line key={i} x1={a.x} y1={a.y} x2={b.x} y2={b.y} strokeWidth={0.6 + Math.min(3, e.weight * 2)} /> : null;
        })}
        {memory.nodes.map((n) => {
          const p = layout[n.id];
          if (!p) return null;
          return (
            <g key={n.id} className={n.href ? "clickable" : ""} onClick={() => onOpen(n.href)}>
              <circle cx={p.x} cy={p.y} r={p.r} fill={KIND_COLOR[n.kind] ?? "var(--faint)"} />
              <title>{`${n.kind}: ${n.label} (rank ${n.score})`}</title>
            </g>
          );
        })}
      </svg>
      <ul className="memory-legend">
        {memory.nodes.slice(1, 9).map((n) => (
          <li key={n.id}>
            <span className="sw" style={{ background: KIND_COLOR[n.kind] ?? "var(--faint)" }} />
            <button type="button" className="linkish" onClick={() => onOpen(n.href)} disabled={!n.href}>{n.label}</button>
            <span className="mono faint">{n.kind.replace("_", " ")}</span>
          </li>
        ))}
      </ul>
      {months.length ? (
        <div className="memory-months" aria-label="Your activity by month">
          {months.map(([m, v]) => (
            <span key={m} title={`${m}: ${v} action(s)`} style={{ height: `${Math.round((v / peak) * 100)}%` }} />
          ))}
        </div>
      ) : null}
      {memory.topics.length ? (
        <div className="assistant-chips">
          {memory.topics.slice(0, 6).map((t) => (
            <button key={t} type="button" className="chip" onClick={() => onAsk(`What does the evidence say about ${t}?`)}>{t}</button>
          ))}
        </div>
      ) : null}
      {memory.suggestions.length ? (
        <ul className="memory-suggest">
          {memory.suggestions.map((s, i) => (
            <li key={i}>
              <button type="button" className="linkish" onClick={() => (s.prompt ? onAsk(s.prompt) : onOpen(s.href ?? null))}>{s.text}</button>
              <span className="faint">{s.reason}</span>
            </li>
          ))}
        </ul>
      ) : null}
      <p className="faint small">{memory.algorithm} · from your last {memory.events_considered} action(s) and {memory.questions_considered} question(s). Nothing extra is stored.</p>
    </div>
  );
}
