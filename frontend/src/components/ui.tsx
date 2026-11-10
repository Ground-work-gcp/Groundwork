import { useCallback, useState, type ReactNode } from "react";
import { useCountUp } from "../hooks/motion";
import type { EvidenceRef } from "../types";

/* Shared primitives in Beacon's house style: state is a dot and a word, numbers are serif. */

export function usd(cents: number | null | undefined): string {
  if (cents == null) return "–";
  const sign = cents < 0 ? "-" : "";
  const abs = Math.abs(cents);
  return `${sign}$${Math.floor(abs / 100).toLocaleString("en-US")}.${String(abs % 100).padStart(2, "0")}`;
}

export function label(key: string): string {
  return key.replace(/[._]/g, " ").replace(/\s+/g, " ").trim();
}

const STAGE_CLS: Record<string, string> = { resolved: "green", resolution: "lilac", review: "amber", discrepancies: "amber" };
export function StagePill({ stage, status }: { stage: string; status?: string }) {
  if (status && status !== "open") return <span className={`pill ${status === "resolved" ? "green" : "dim"}`}>{status}</span>;
  return <span className={`pill ${STAGE_CLS[stage] ?? "dim"}`}>{label(stage)}</span>;
}

const SEV_CLS: Record<string, string> = { critical: "red pulse", high: "red", medium: "amber", low: "dim", info: "dim" };
export function SeverityPill({ severity }: { severity: string }) {
  return <span className={`pill ${SEV_CLS[severity] ?? "dim"}`}>{severity}</span>;
}

const OUTCOME_CLS: Record<string, string> = { AUTO_MATCHED: "green", PROPOSED: "lilac", EXCEPTION: "red" };
export function OutcomePill({ outcome }: { outcome: string }) {
  return <span className={`pill ${OUTCOME_CLS[outcome] ?? "dim"}`}>{label(outcome.toLowerCase())}</span>;
}

export function StatusPill({ status }: { status: string }) {
  const cls = ["executed", "confirmed", "done", "resolved", "extracted", "paid", "verified", "accepted"].includes(status)
    ? "green"
    : ["proposed", "open", "unreviewed", "in_progress", "partial", "pending", "acknowledged"].includes(status)
      ? "amber"
      : ["rejected", "failed", "needs_ocr", "dismissed", "not_verified", "expired"].includes(status)
        ? "red"
        : "dim";
  return <span className={`pill ${cls}`}>{label(status)}</span>;
}

export function Panel({ title, meta, actions, children }: { title: string; meta?: ReactNode; actions?: ReactNode; children: ReactNode }) {
  return (
    <section className="panel">
      <div className="panel-h">
        <h2>{title}</h2>
        {actions ? <span className="row panel-actions">{actions}</span> : null}
        {meta ? <span className="meta">{meta}</span> : null}
      </div>
      <div className="panel-b">{children}</div>
    </section>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <h3>{title}</h3>
      {children ? <div className="dim">{children}</div> : null}
    </div>
  );
}

export function ErrorLine({ error }: { error: string | null | undefined }) {
  return error ? <div className="err" role="alert">{error}</div> : null;
}

export function Tile({ n, l, i = 0, fmt, moon }: { n: number | null | undefined; l: string; i?: number; fmt?: (v: number) => string; moon?: boolean }) {
  const v = useCountUp(n ?? null);
  return (
    <div className={`tile${moon ? " moon" : ""}`} style={{ "--i": i } as React.CSSProperties}>
      <div className="n">{v == null ? "–" : fmt ? fmt(Math.round(v)) : Math.round(v)}</div>
      <div className="l">{l}</div>
    </div>
  );
}

export function HBars({ items }: { items: Array<{ label: string; value: number; cls?: string; text?: string }> }) {
  const max = Math.max(1, ...items.map((i) => i.value));
  return (
    <ul className="hbars">
      {items.map((item) => (
        <li key={item.label}>
          <span className="hb-label">{item.label}</span>
          <span className={`hb-track ${item.cls ?? ""}`}>
            <span className="hb-fill" style={{ width: `${(item.value / max) * 100}%` }} />
          </span>
          <span className="hb-val mono">{item.text ?? item.value}</span>
        </li>
      ))}
    </ul>
  );
}

/** Where a finding's claim comes from: document, page, the line itself, and how sure. */
export function EvidenceRefs({ refs, caseId }: { refs: EvidenceRef[]; caseId: string }) {
  if (!refs.length) return <p className="faint small">No supporting records.</p>;
  return (
    <ul className="refs">
      {refs.map((ref, i) => (
        <li key={`${ref.fact_id ?? ref.document_id ?? ""}-${i}`}>
          {ref.document_id ? (
            <a href={`#case/${caseId}/evidence/${ref.document_id}`} className="ref-doc">
              {ref.filename ?? "document"}
              {ref.page ? <span className="faint"> · p. {ref.page}</span> : null}
            </a>
          ) : (
            <span className="ref-doc">{label(ref.label)}</span>
          )}
          {ref.snippet ? <span className="ref-snip">“{ref.snippet}”</span> : null}
          <span className="ref-meta mono">
            {ref.confidence != null ? `${Math.round(ref.confidence * 100)}%` : ""}
            {ref.reviewed ? " · reviewed" : ref.fact_id ? " · unreviewed" : ""}
          </span>
        </li>
      ))}
    </ul>
  );
}

/** Run an async action with a busy flag and a readable error. */
export function useAction(onDone?: (msg: string) => void) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = useCallback(
    async <T,>(fn: () => Promise<T>, success?: string): Promise<T | null> => {
      setBusy(true);
      setError(null);
      try {
        const result = await fn();
        if (success) onDone?.(success);
        return result;
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        return null;
      } finally {
        setBusy(false);
      }
    },
    [onDone],
  );
  return { busy, error, run, setError };
}
