import { useEffect, useState } from "react";
import { api } from "../../services/api";
import type { Me } from "../../types";
import { ErrorLine } from "../../components/ui";
import { Marquee } from "../../components/Marquee";

/* Beacon's landing page structure (hero, ink band, green stats band, pillars, FAQ) with
   Groundwork's own story; and InHeir's sign-in / sign-up form, in the house style. */

const PILLARS = [
  { title: "Every fact has a page", text: "Grantors, parcel numbers, amounts and dates are extracted with the document, page and line they came from — and a reviewer can confirm or correct each one." },
  { title: "The chain of title, checked", text: "Deeds, death records, wills and tax rolls become one timeline. Where a grantor was never an owner, or title is still in a decedent's name, it says so." },
  { title: "Money reconciled, not summarised", text: "Tax bills and liens are matched to payments by a deterministic matcher with three outcomes — matched, proposed, exception — and a reason for each." },
  { title: "Consent is a record", text: "Closing a case or dismissing a finding is an action an attorney approves by typing its exact phrase. It executes once, and the chain of every step is verifiable." },
];

const FAQ = [
  { q: "Does Groundwork decide who owns the property?", a: "No. It shows what the evidence says, where records disagree, and what is missing. Determinations belong to the attorney and, where needed, the court." },
  { q: "Where do the findings come from?", a: "Deterministic rules over the extracted facts and the reconciliation. If Gemini is enabled it can propose extra facts, but each must quote its page exactly or it is rejected." },
  { q: "What happens to personal information?", a: "Documents are stored as uploaded and inspected for identifiers such as Social Security numbers; only counts are kept. Text sent to a model is redacted first." },
  { q: "Can one organisation see another's cases?", a: "No. Every record is scoped to its organisation and a case from elsewhere is indistinguishable from one that does not exist." },
];

export function Landing() {
  const [sel, setSel] = useState(0);
  return (
    <main key="home" className="landing route-in">
      <section className="hero">
        <div className="eyebrow">Groundwork</div>
        <h1 className="display hero-h">
          Inherited property, <em>resolved on the evidence.</em>
        </h1>
        <p className="lede hero-sub">
          One record for each case — the property, the parties, the deeds and death records, the taxes and liens — so the places where they disagree are found before a family loses the home.
        </p>
        <div className="row hero-cta">
          <a className="btn primary" href="#signin">
            Sign in
          </a>
          <a className="btn" href="#signin?new">
            Create an organisation
          </a>
        </div>
      </section>
      <div className="band">
        <Marquee label="Built on" />
      </div>
      <section className="green-band">
        <h2 className="display">
          The documents agree <em>less often than you think.</em>
        </h2>
        <p className="lede">A deed names one owner, the tax roll another, and a payment history nobody reconciled. Groundwork connects them.</p>
      </section>
      <section className="section">
        <h2 className="display center">
          Built around <em>how title work happens.</em>
        </h2>
        <div className="pillars">
          {PILLARS.map((p, i) => (
            <div key={p.title} className="pillar reveal" style={{ "--i": i } as React.CSSProperties}>
              <h3>{p.title}</h3>
              <p>{p.text}</p>
            </div>
          ))}
        </div>
      </section>
      <section className="section">
        <div className="eyebrow center">FAQs</div>
        <h2 className="display center">
          Good <em>questions.</em>
        </h2>
        <div className="faq">
          <div className="faq-q">
            <h3>Questions</h3>
            <ul role="tablist" aria-orientation="vertical">
              {FAQ.map((f, i) => (
                <li key={f.q}>
                  <button type="button" role="tab" aria-selected={i === sel} className={i === sel ? "on" : ""} onClick={() => setSel(i)}>
                    {f.q}
                  </button>
                </li>
              ))}
            </ul>
          </div>
          <div className="faq-a" role="tabpanel">
            <h3>Answer</h3>
            <div className="faq-ask">{FAQ[sel].q}</div>
            <div className="faq-bubble" key={sel}>
              <p>{FAQ[sel].a}</p>
            </div>
          </div>
        </div>
      </section>
    </main>
  );
}

export function SignIn({ onSignedIn }: { onSignedIn: (u: Me) => void }) {
  const [mode, setMode] = useState<"in" | "up">(window.location.hash.includes("new") ? "up" : "in");
  const [form, setForm] = useState({ organization: "", username: "", full_name: "", email: "", password: "" });
  const [available, setAvailable] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: e.target.value });

  useEffect(() => {
    if (mode !== "up" || form.username.length < 3) return setAvailable(null);
    const t = window.setTimeout(() => void api.usernameAvailable(form.username).then((r) => setAvailable(r.available)).catch(() => setAvailable(null)), 350);
    return () => window.clearTimeout(t);
  }, [form.username, mode]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = mode === "in" ? await api.signIn(form.username, form.password) : await api.signUp(form);
      onSignedIn(r.user);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="main route-in">
      <div className="auth-wrap">
        <div className="page-h">
          <h1 className="display">{mode === "in" ? <>Welcome <em>back.</em></> : <>Start an <em>organisation.</em></>}</h1>
          <p className="lede">{mode === "in" ? "Sign in to your legal-aid organisation's workspace." : "The first account administers the organisation and can invite colleagues."}</p>
        </div>
        <form className="form card-form" onSubmit={submit}>
          {mode === "up" ? (
            <>
              <label>Organisation<input className="input" required value={form.organization} onChange={set("organization")} placeholder="Hollins County Legal Aid" /></label>
              <label>Full name<input className="input" required value={form.full_name} onChange={set("full_name")} /></label>
              <label>Email<input className="input" type="email" required value={form.email} onChange={set("email")} /></label>
            </>
          ) : null}
          <label>
            Username
            <input className="input" required autoComplete="username" value={form.username} onChange={set("username")} />
            {mode === "up" && available !== null ? <span className={`pill ${available ? "green" : "red"}`}>{available ? "available" : "taken"}</span> : null}
          </label>
          <label>Password<input className="input" type="password" required minLength={mode === "up" ? 10 : 1} autoComplete={mode === "in" ? "current-password" : "new-password"} value={form.password} onChange={set("password")} /></label>
          <ErrorLine error={error} />
          <div className="row">
            <button className="btn primary" type="submit" disabled={busy}>
              {busy ? "…" : mode === "in" ? "Sign in" : "Create organisation"}
            </button>
            <button type="button" className="btn ghost" onClick={() => setMode(mode === "in" ? "up" : "in")}>
              {mode === "in" ? "New organisation?" : "Have an account?"}
            </button>
          </div>
        </form>
      </div>
    </main>
  );
}
