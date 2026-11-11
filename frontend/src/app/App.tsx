import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../services/api";
import type { Me } from "../types";
import { useClock, usePoll } from "../hooks";
import { useCursorMotion, useNavIndicator, useReveal, useScrolled } from "../hooks/motion";
import { NAV, go, useRoute } from "../navigation/routes";
import { Announce, Footer, HelpFab, Toasts, useToasts } from "../components/Chrome";
import { Marquee } from "../components/Marquee";
import { Landing, SignIn } from "../features/auth/SignIn";
import { Overview } from "../features/overview/Overview";
import { CaseList } from "../features/cases/CaseList";
import { CaseView } from "../features/cases/CaseView";
import { PropertiesPage } from "../features/properties/PropertiesPage";
import { FindingsPage } from "../features/risk-analysis/Findings";
import { ApprovalsPage } from "../features/approvals/ApprovalsPage";
import { AuditPage } from "../features/audit/AuditPage";
import { SettingsPage } from "../features/settings/SettingsPage";
import { StudioPage } from "../features/studio/StudioPage";
import { FlowPage } from "../features/flow/FlowCanvas";
import { Assistant } from "../components/Assistant";
import { FieldPage } from "../features/land/FieldPage";
import { RegistryPortal } from "../features/external/ExternalApprovals";
import { ConsentPage, VerifyPage } from "../features/land/PublicPages";

export interface Ctx {
  me: Me;
  toast: (text: string, tone?: "ok" | "info") => void;
  can: (permission: string) => boolean;
}

const TITLES: Record<string, string> = {
  home: "Groundwork — inherited property, resolved on the evidence",
  signin: "Sign in · Groundwork",
  overview: "Overview · Groundwork",
  flow: "Flow · Groundwork",
  cases: "Cases · Groundwork",
  case: "Case · Groundwork",
  properties: "Properties · Groundwork",
  field: "Field · Groundwork",
  registry: "Registry portal · Groundwork",
  consent: "Your share · Groundwork",
  verify: "Verify a land passport · Groundwork",
  findings: "Findings · Groundwork",
  approvals: "Approvals · Groundwork",
  audit: "Audit · Groundwork",
  settings: "Settings · Groundwork",
  studio: "Engineering studio · Groundwork",
  notfound: "Nothing here · Groundwork",
};

export default function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [checked, setChecked] = useState(false);
  const route = useRoute();
  const wall = useClock();
  const scrolled = useScrolled();
  const { toasts, push, dismiss } = useToasts();
  const navRef = useRef<HTMLElement>(null);
  const mainRef = useRef<HTMLDivElement>(null);
  const topbarRef = useRef<HTMLElement>(null);
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    api
      .me()
      .then((r) => setMe(r.user))
      .catch(() => setMe(null))
      .finally(() => setChecked(true));
    const out = () => setMe(null);
    window.addEventListener("gw:signed-out", out);
    return () => window.removeEventListener("gw:signed-out", out);
  }, []);

  const health = usePoll(api.health, 60_000, [], true);
  const pending = usePoll(me ? () => api.approvals("proposed") : null, 20_000, [me?.id], !!me);

  useEffect(() => setMenuOpen(false), [route.page, route.id]);
  useEffect(() => {
    window.scrollTo({ top: 0 });
    document.title = TITLES[route.page] ?? "Groundwork";
  }, [route.page, route.id, route.tab]);
  useEffect(() => {
    if (me && (route.page === "home" || route.page === "signin")) go("overview");
  }, [me, route.page]);

  // keyboard: g then a nav key routes; "?" opens help; Esc blurs (Beacon's shortcuts)
  useEffect(() => {
    let pendingG = 0;
    const typing = (el: Element | null) => !!el && ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName);
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "Escape") return (document.activeElement as HTMLElement | null)?.blur?.();
      if (typing(document.activeElement)) return;
      if (e.key === "g") {
        pendingG = Date.now();
        return;
      }
      const target = NAV.find((n) => n.key === e.key);
      if (pendingG && Date.now() - pendingG < 1500 && target) go(target.page);
      else if (e.key === "?") document.querySelector<HTMLElement>(".fab")?.click();
      pendingG = 0;
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  useNavIndicator(navRef, `${route.page}:${pending.data?.actions.length ?? 0}:${me?.id ?? ""}`);
  useReveal(mainRef, [route.page, route.id, route.tab, !!me]);
  useCursorMotion();

  const can = useCallback((permission: string) => !!me?.permissions.includes(permission), [me]);
  const signOut = async () => {
    await api.signOut().catch(() => undefined);
    setMe(null);
    go("home");
  };

  const ctx: Ctx | null = useMemo(() => (me ? { me, toast: push, can } : null), [me, push, can]);
  const pendingCount = pending.data?.actions.length ?? 0;
  const active = route.page === "case" ? "cases" : route.page;

  let body: JSX.Element;
  if (route.page === "consent" && route.id) body = <ConsentPage token={route.id} />;
  else if (route.page === "verify") body = <VerifyPage initial={route.tab} />;
  else if (!checked) body = <main className="main route-in"><p className="faint">Loading…</p></main>;
  else if (!ctx) body = route.page === "signin" ? <SignIn onSignedIn={(u) => { setMe(u); push(`Signed in as ${u.full_name}`); }} /> : <Landing />;
  else if (route.page === "overview" || route.page === "home" || route.page === "signin") body = <Overview ctx={ctx} />;
  else if (route.page === "flow") body = <FlowPage ctx={ctx} />;
  else if (route.page === "cases") body = <CaseList ctx={ctx} tab={route.tab} />;
  else if (route.page === "case" && route.id) body = <CaseView key={route.id} ctx={ctx} caseId={route.id} tab={route.tab ?? "overview"} item={route.item} />;
  else if (route.page === "properties") body = <PropertiesPage />;
  else if (route.page === "field") body = <FieldPage ctx={ctx} />;
  else if (route.page === "registry") body = <RegistryPortal ctx={ctx} />;
  else if (route.page === "findings") body = <FindingsPage ctx={ctx} />;
  else if (route.page === "approvals") body = <ApprovalsPage ctx={ctx} onChanged={() => void pending.refresh()} />;
  else if (route.page === "audit") body = <AuditPage />;
  else if (route.page === "studio") body = <StudioPage ctx={ctx} tab={route.tab} projectId={route.id} />;
  else if (route.page === "settings") body = <SettingsPage ctx={ctx} tab={route.tab} />;
  else
    body = (
      <main className="main route-in">
        <div className="empty notfound">
          <h3>Nothing here.</h3>
          <p>
            There is no page at <code className="mono">{window.location.hash}</code>.
          </p>
          <a className="btn primary" href={ctx ? "#overview" : "#home"}>
            Back to Groundwork
          </a>
        </div>
      </main>
    );

  return (
    <div className={`shell${scrolled ? " scrolled" : ""}`} ref={mainRef}>
      {ctx && pendingCount ? (
        <Announce text={`${pendingCount} action${pendingCount === 1 ? "" : "s"} awaiting approval`} href="#approvals" />
      ) : (
        <Announce text={health.data ? `Groundwork ${health.data.version} · ${health.data.database.dialect ?? "database"} · storage ${health.data.storage} · extraction ${health.data.extraction} · AI ${health.data.ai}` : "Groundwork"} href={ctx ? "#settings/integrations" : "#home"} />
      )}
      <header className={`topbar${menuOpen ? " open" : ""}`} ref={topbarRef}>
        <a className="brand" href={ctx ? "#overview" : "#home"} aria-label="Groundwork, home">
          <span className="dot" />
          Groundwork <span className="sub">{ctx ? ctx.me.organization.name : "Property resolution"}</span>
        </a>
        <button type="button" className={`burger${menuOpen ? " x" : ""}`} aria-label={menuOpen ? "Close menu" : "Open menu"} aria-expanded={menuOpen} aria-controls="site-nav" onClick={() => setMenuOpen((o) => !o)}>
          <span />
          <span />
          <span />
        </button>
        <nav className="nav" id="site-nav" aria-label="Sections" ref={navRef}>
          {ctx ? (
            NAV.map((n) => (
              <a key={n.page} href={`#${n.page}`} className={active === n.page ? "active" : ""} aria-current={active === n.page ? "page" : undefined}>
                {n.label}
                {n.page === "approvals" && pendingCount ? <span className="count">{pendingCount}</span> : null}
              </a>
            ))
          ) : (
            <>
              <a href="#home" className={route.page === "home" ? "active" : ""}>Home</a>
              <a href="#signin" className={route.page === "signin" ? "active" : ""}>Sign in</a>
            </>
          )}
        </nav>
        <div className="status" role="status" aria-live="polite">
          {health.error ? <span className="pill red pulse">API unreachable</span> : health.data ? <span className="pill green">live</span> : null}
          {ctx ? (
            <>
              <span className="status-meta mono">{ctx.me.full_name} · {ctx.me.role}</span>
              <button type="button" className="btn ghost small" onClick={() => void signOut()}>
                Sign out
              </button>
            </>
          ) : null}
          <span className="clock">{wall.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
        </div>
      </header>
      {body}
      {ctx && (route.page === "overview" || route.page === "audit") ? (
        <div className="band">
          <Marquee label="Built on" />
        </div>
      ) : null}
      <Footer version={health.data?.version} />
      <Toasts items={toasts} onDismiss={dismiss} />
      <HelpFab />
      {ctx ? <Assistant userId={ctx.me.id} caseId={route.page === "case" ? route.id : undefined} /> : null}
    </div>
  );
}
