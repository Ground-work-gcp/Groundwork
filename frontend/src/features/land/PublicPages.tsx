import { useEffect, useState } from "react";
import { api } from "../../services/api";
import type * as T from "../../types";
import { ErrorLine, Panel, useAction } from "../../components/ui";

/* Pages that work without signing in: a co-heir's single-use consent link, and a lender's
   check of a land credit passport. Both show only what the link or code entitles. */

export function ConsentPage({ token }: { token: string }) {
  const [v, setV] = useState<T.ConsentView | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const { busy, error, run } = useAction();
  useEffect(() => {
    document.title = "Your share · Groundwork";
    api.publicConsent(token).then(setV).catch((e: Error) => setFailed(e.message));
  }, [token]);
  const reply = async (decision: string) => { const r = await run(() => api.replyConsent(token, decision, note)); if (r) setDone(decision); };
  return (
    <main className="main route-in">
      <div className="page-h">
        <span className="eyebrow">Family land · draft shares</span>
        <h1 className="display">{v ? `${v.name}, this is your share.` : "Your share"}</h1>
      </div>
      {failed ? <div className="fix red">{failed}</div> : !v ? <p className="faint">Loading…</p> : done ? (
        <div className="fix green"><div className="head">Thank you — your reply is recorded.</div>You answered: <b>{done.replace("_", " ")}</b>. The family's paralegal will see it.</div>
      ) : (
        <div className="split">
          <Panel title={`Survey no. ${v.plot.survey_number}, ${v.plot.village}`} meta={v.plot.region}>
            <p>The land is recorded in the name of <b>{v.recorded_holder}</b>. Under {v.law ?? "the applicable law"}, your draft share is <b className="mono">{v.your_share ?? "—"}</b>.</p>
            <table className="t"><tbody>{v.co_owners.map((o) => <tr key={o.name}><td>{o.name}</td><td className="mono">{o.share}</td></tr>)}</tbody></table>
            <details className="small"><summary>How this was worked out</summary><ol>{v.how.map((h, i) => <li key={i}>{h}</li>)}</ol></details>
            <p className="faint small">{v.disclaimer}</p>
          </Panel>
          <Panel title="Your reply">
            <p className="small">{v.what_consent_means}</p>
            <pre className="pagetext small">{v.letter}</pre>
            <textarea className="input" placeholder="A note for the family (optional)" value={note} onChange={(e) => setNote(e.target.value)} maxLength={1000} />
            <ErrorLine error={error} />
            <div className="row">
              <button className="btn primary" disabled={busy} onClick={() => void reply("consented")}>I consent</button>
              <button className="btn" disabled={busy} onClick={() => void reply("needs_info")}>I need more information</button>
              <button className="btn ghost" disabled={busy} onClick={() => void reply("objected")}>I object</button>
            </div>
            <p className="faint small">This link works once and expires in 30 days.</p>
          </Panel>
        </div>
      )}
    </main>
  );
}

export function VerifyPage({ initial }: { initial?: string }) {
  const [code, setCode] = useState(initial ?? "");
  const [sha, setSha] = useState("");
  const [r, setR] = useState<Record<string, unknown> | null>(null);
  const { busy, error, run } = useAction();
  useEffect(() => { document.title = "Verify a land passport · Groundwork"; }, []);
  const band = (r?.valuation_band ?? {}) as Record<string, number | string | null>;
  const fmt = (n: unknown) => (typeof n === "number" ? `₹${n.toLocaleString("en-IN")}` : "–");
  return (
    <main className="main route-in">
      <div className="page-h">
        <span className="eyebrow">For lenders</span>
        <h1 className="display">Check a land credit passport.</h1>
        <p className="lede">Enter the code and the SHA-256 printed on the passport. A genuine, current passport matches both.</p>
      </div>
      <Panel title="Verify">
        <form className="row" onSubmit={async (e) => { e.preventDefault(); setR(await run(() => api.verifyPassport(code.trim(), sha.trim()))); }}>
          <input className="input mono" style={{ maxWidth: 200 }} placeholder="GWP-XXXX-XXXX" value={code} onChange={(e) => setCode(e.target.value)} required />
          <input className="input mono" placeholder="sha256" value={sha} onChange={(e) => setSha(e.target.value)} />
          <button className="btn primary" disabled={busy}>Check</button>
        </form>
        <ErrorLine error={error} />
        {r ? (
          <div className={`fix ${r.valid ? "green" : "red"}`} style={{ marginTop: 12 }}>
            <div className="head">{r.valid ? "Genuine and current" : String(r.reason ?? `Not valid (${String(r.status ?? "")}${r.sha256_matches === false ? ", hash does not match" : ""})`)}</div>
            {r.plot ? <div className="small">S.No. {String((r.plot as Record<string, unknown>).survey_number)} · {String((r.plot as Record<string, unknown>).village)} · issued {String(r.issued_at ?? "").slice(0, 10)}</div> : null}
            {r.valuation_band ? <div className="small">Value P10–P90 {fmt(band.p10_inr)} – {fmt(band.p90_inr)} · lending value {fmt(band.lending_value_inr)} · confidence {String(band.confidence ?? "–")}</div> : null}
            {r.readiness ? <div className="small">Readiness {String((r.readiness as Record<string, unknown>).score)}/100 for {String((r.readiness as Record<string, unknown>).lender)}</div> : null}
            <div className="faint small">{String(r.note ?? "")}</div>
          </div>
        ) : null}
      </Panel>
    </main>
  );
}
