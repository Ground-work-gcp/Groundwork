import { useEffect, useRef, useState } from "react";

/* Page chrome kept from Beacon: announcement strip, footer with the serif wordmark,
   toasts and the "?" help popover — now explaining the case workflow. */

const FOOTER: Array<{ label: string; links: Array<{ text: string; href: string }> }> = [
  { label: "Work", links: [{ text: "Overview", href: "#overview" }, { text: "Cases", href: "#cases" }, { text: "Properties", href: "#properties" }] },
  { label: "Review", links: [{ text: "Findings", href: "#findings" }, { text: "Approvals", href: "#approvals" }, { text: "Audit log", href: "#audit" }] },
  { label: "Trust", links: [{ text: "Safety model", href: "#settings/safety" }, { text: "Integrations", href: "#settings/integrations" }, { text: "API docs", href: "/api/docs" }] },
  { label: "Organisation", links: [{ text: "Members", href: "#settings" }, { text: "Referrals", href: "#cases/referrals" }] },
];

export function Footer({ version }: { version?: string }) {
  return (
    <footer className="footer">
      <div className="footer-cols">
        {FOOTER.map((col) => (
          <div key={col.label} className="footer-col">
            <div className="footer-label">{col.label}</div>
            {col.links.map((l) => (
              <a key={l.text} href={l.href}>
                {l.text}
              </a>
            ))}
          </div>
        ))}
      </div>
      <div className="wordmark" aria-hidden="true">
        Groundwork
      </div>
      <div className="footer-line">
        <span>© Groundwork · evidence for professional review, not legal advice</span>
        <span className="kbd-hint" title="Keyboard shortcuts">
          <kbd>g</kbd> <kbd>o</kbd> overview · <kbd>g</kbd> <kbd>c</kbd> cases · <kbd>g</kbd> <kbd>f</kbd> findings · <kbd>g</kbd> <kbd>a</kbd> approvals · <kbd>g</kbd> <kbd>u</kbd> audit · <kbd>?</kbd> help
        </span>
        <span className="mono">{version ? `v${version}` : ""}</span>
      </div>
    </footer>
  );
}

export function Announce({ text, href }: { text: string; href?: string }) {
  return (
    <div className="announce">
      <a href={href ?? "#overview"}>
        {text} <span aria-hidden="true">›</span>
      </a>
    </div>
  );
}

export interface ToastItem {
  id: number;
  text: string;
  tone?: "ok" | "info";
}

export function Toasts({ items, onDismiss }: { items: ToastItem[]; onDismiss: (id: number) => void }) {
  if (!items.length) return null;
  return (
    <div className="toasts" role="status" aria-live="polite">
      {items.map((t) => (
        <button key={t.id} type="button" className={`toast ${t.tone ?? "info"}`} onClick={() => onDismiss(t.id)} title="Dismiss">
          <span className="toast-dot" />
          {t.text}
        </button>
      ))}
    </div>
  );
}

let toastSeq = 1;
export function useToasts(ms = 4000) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const dismiss = (id: number) => setToasts((t) => t.filter((x) => x.id !== id));
  const push = (text: string, tone: ToastItem["tone"] = "ok") => {
    const id = toastSeq++;
    setToasts((t) => [...t.slice(-2), { id, text, tone }]);
    window.setTimeout(() => dismiss(id), ms);
  };
  return { toasts, push, dismiss };
}

export function HelpBody() {
  return (
    <>
      <div>
        <b>One case, one record.</b> Every fact Groundwork shows cites the document and page it came from. Findings are checks for a
        professional to review — never legal determinations.
      </div>
      <ol className="rules">
        <li><b>Intake</b> — property identity and parties.</li>
        <li><b>Evidence</b> — upload deeds, wills, death records, tax bills and liens; facts are extracted with provenance.</li>
        <li><b>Ownership</b> — rebuild the chain of title and see where it breaks.</li>
        <li><b>Financials</b> — derive tax and lien obligations, import payments, reconcile.</li>
        <li><b>Discrepancies</b> — run detection across ownership and money.</li>
        <li><b>Review</b> — acknowledge or resolve each finding; correct facts.</li>
        <li><b>Resolution</b> — tasks, decisions, and actions that an attorney approves by typing “approve action N”.</li>
        <li><b>History</b> — a hash-chained audit trail and a case report.</li>
      </ol>
    </>
  );
}

export function HelpFab() {
  const [open, setOpen] = useState(false);
  const pop = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("keydown", onKey);
    pop.current?.querySelector<HTMLElement>("button")?.focus();
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);
  return (
    <>
      <button type="button" className={`fab${open ? " on" : ""}`} onClick={() => setOpen((o) => !o)} aria-label="How Groundwork works" aria-expanded={open}>
        ?
      </button>
      {open ? (
        <div className="popover" ref={pop} role="dialog" aria-label="How Groundwork works">
          <div className="popover-h">
            <span className="eyebrow">The eight stages of a case</span>
            <button type="button" className="btn ghost" onClick={() => setOpen(false)} aria-label="Close">
              ✕
            </button>
          </div>
          <div className="stack small">
            <HelpBody />
          </div>
        </div>
      ) : null}
    </>
  );
}
