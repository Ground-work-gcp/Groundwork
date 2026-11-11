import { useState } from "react";
import type { Ctx } from "../../app/App";
import { formatAge, useClock, usePoll } from "../../hooks";
import { go } from "../../navigation/routes";
import { api } from "../../services/api";
import { Empty, ErrorLine, Panel, StagePill, StatusPill, useAction } from "../../components/ui";

/* Case intake (Stage 1) and the case list; InHeir's "new case" form and referral dashboard live here. */

export function CaseList({ ctx, tab }: { ctx: Ctx; tab?: string }) {
  const [filter, setFilter] = useState("open");
  const [search, setSearch] = useState("");
  const now = useClock();
  const q = usePoll(() => api.cases({ status: filter === "all" ? undefined : filter, q: search || undefined }), 20_000, [filter, search], true);
  if (tab === "new") return <NewCase ctx={ctx} />;
  if (tab === "referrals") return <Referrals ctx={ctx} />;
  const cases = q.data?.cases ?? [];
  return (
    <main className="main route-in">
      <div className="page-h row">
        <h1 className="display">
          Cases, <em>one record each.</em>
        </h1>
        <span style={{ marginLeft: "auto" }} className="row">
          {ctx.can("case:write") ? <a className="btn primary" href="#cases/new">New case</a> : null}
          <a className="btn ghost" href="#cases/referrals">Referrals</a>
        </span>
      </div>
      <div className="row">
        <div className="chips" role="group" aria-label="Filter cases">
          {["open", "resolved", "closed", "all"].map((k) => (
            <button key={k} type="button" className={`chip-btn${filter === k ? " on" : ""}`} aria-pressed={filter === k} onClick={() => setFilter(k)}>
              {k}
            </button>
          ))}
        </div>
        <input className="input" style={{ maxWidth: 280, marginLeft: "auto" }} placeholder="search title or GW-…" value={search} onChange={(e) => setSearch(e.target.value)} />
      </div>
      <ErrorLine error={q.error} />
      {!q.loading && cases.length === 0 ? (
        <Empty title="No cases here.">{ctx.can("case:write") ? <a className="btn primary" href="#cases/new">Open the first case</a> : "Nothing matches this filter."}</Empty>
      ) : (
        <div className="feed">
          {cases.map((c, i) => (
            <div key={c.id} className="card rise" style={{ "--i": Math.min(i, 8) } as React.CSSProperties} role="button" tabIndex={0} onClick={() => go(`case/${c.id}`)} onKeyDown={(e) => e.key === "Enter" && go(`case/${c.id}`)}>
              <div className="title">
                <span className="mono faint">{c.reference}</span>
                <span className="alarm">{c.title}</span>
                <span className="state-col">
                  <StagePill stage={c.stage} status={c.status} />
                  {c.findings_open ? <span className="moon">{c.findings_open} open finding{c.findings_open === 1 ? "" : "s"}</span> : null}
                  <time className="age" dateTime={c.updated_at}>{formatAge(c.updated_at, now)}</time>
                </span>
              </div>
              <div className="summary">
                {[c.property?.address_line, c.property?.county && `${c.property.county} County`, c.property?.parcel_number && `parcel ${c.property.parcel_number}`].filter(Boolean).join(" · ") || "property not yet identified"}
              </div>
            </div>
          ))}
        </div>
      )}
    </main>
  );
}

function NewCase({ ctx }: { ctx: Ctx }) {
  const [kind, setKind] = useState<"rural_land" | "heirs_property">("rural_land");
  const [f, setF] = useState({ title: "", description: "", address_line: "", city: "", county: "", state: "", parcel_number: "", client: "", decedent: "" });
  const [land, setLand] = useState({ region_code: "TN", district: "", taluk: "", village: "", survey_number: "", recorded_holder: "" });
  const { busy, error, run } = useAction(ctx.toast);
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => setF({ ...f, [k]: e.target.value });
  const setL = (k: keyof typeof land) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setLand({ ...land, [k]: e.target.value });
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (kind === "rural_land") {
      const created = await run(async () => {
        const c = await api.createCase({ kind, title: f.title, description: f.description, parties: f.client ? [{ name: f.client, roles: ["client", "heir"] }] : [] });
        await api.saveParcel(c.case.id, { ...land, personal_law: "hindu", property_nature: "self_acquired" });
        return c;
      }, "Land case opened");
      if (created) go(`case/${created.case.id}/land/journey`);
      return;
    }
    const parties = [
      ...(f.client ? [{ name: f.client, roles: ["client"] }] : []),
      ...(f.decedent ? [{ name: f.decedent, roles: ["decedent"], is_deceased: true }] : []),
    ];
    const created = await run(
      () => api.createCase({ title: f.title, description: f.description, property: { address_line: f.address_line, city: f.city, county: f.county, state: f.state, parcel_number: f.parcel_number }, parties }),
      "Case opened",
    );
    if (created) go(`case/${created.case.id}/evidence`);
  };
  return (
    <main className="main route-in">
      <div className="page-h">
        <h1 className="display">
          Open a case. <em>Start with what you know.</em>
        </h1>
        <p className="lede">Property identifiers and the people involved. Documents come next, and every fact in them will be traced back to its page.</p>
      </div>
      <form className="form card-form" onSubmit={submit}>
        <div className="seg" role="radiogroup" aria-label="Kind of case">
          <button type="button" role="radio" aria-checked={kind === "rural_land"} className={`chip-btn${kind === "rural_land" ? " on" : ""}`} onClick={() => setKind("rural_land")}>Rural land (India)</button>
          <button type="button" role="radio" aria-checked={kind === "heirs_property"} className={`chip-btn${kind === "heirs_property" ? " on" : ""}`} onClick={() => setKind("heirs_property")}>Inherited home (US title)</button>
        </div>
        <label>Case title<input className="input" required minLength={3} value={f.title} onChange={set("title")} placeholder={kind === "rural_land" ? "Ramasamy heirs — S.No. 123/4A, Keezhaiyur" : "Parker heirs — 418 Magnolia Rd"} /></label>
        <label>What the family needs<textarea className="input" value={f.description} onChange={set("description")} placeholder={kind === "rural_land" ? "Patta still in the grandfather's name; Kannan needs a crop loan." : ""} /></label>
        {kind === "rural_land" ? (
          <>
            <div className="grid">
              <label>State<select className="input" value={land.region_code} onChange={setL("region_code")}>
                {[["TN", "Tamil Nadu"], ["KA", "Karnataka"], ["AP", "Andhra Pradesh"], ["MH", "Maharashtra"], ["UP", "Uttar Pradesh"], ["IN", "Other state"]].map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select></label>
              <label>District<input className="input" value={land.district} onChange={setL("district")} /></label>
              <label>Taluk / tehsil / mandal<input className="input" value={land.taluk} onChange={setL("taluk")} /></label>
              <label>Village<input className="input" required value={land.village} onChange={setL("village")} /></label>
              <label>Survey number<input className="input" required value={land.survey_number} onChange={setL("survey_number")} placeholder="123/4A" /></label>
              <label>Name on the record<input className="input" value={land.recorded_holder} onChange={setL("recorded_holder")} /></label>
            </div>
            <label>Who came to us (applicant)<input className="input" value={f.client} onChange={set("client")} /></label>
            <ErrorLine error={error} />
            <div className="row">
              <button className="btn primary" type="submit" disabled={busy}>Open land case</button>
              <a className="btn ghost" href="#cases">Cancel</a>
            </div>
          </>
        ) : null}
        {kind === "heirs_property" ? (<>
        <div className="grid">
          <label>Street address<input className="input" value={f.address_line} onChange={set("address_line")} /></label>
          <label>City<input className="input" value={f.city} onChange={set("city")} /></label>
          <label>County<input className="input" value={f.county} onChange={set("county")} /></label>
          <label>State<input className="input" value={f.state} onChange={set("state")} /></label>
          <label>Parcel number (APN)<input className="input" value={f.parcel_number} onChange={set("parcel_number")} /></label>
        </div>
        <div className="grid">
          <label>Client<input className="input" value={f.client} onChange={set("client")} /></label>
          <label>Decedent (if known)<input className="input" value={f.decedent} onChange={set("decedent")} /></label>
        </div>
        <ErrorLine error={error} />
        <div className="row">
          <button className="btn primary" type="submit" disabled={busy}>Open case</button>
          <a className="btn ghost" href="#cases">Cancel</a>
        </div>
        </>) : null}
      </form>
    </main>
  );
}

function Referrals({ ctx }: { ctx: Ctx }) {
  const q = usePoll(api.referrals, 30_000, [], true);
  const [f, setF] = useState({ address: "", description: "", full_name: "", email: "" });
  const { busy, error, run } = useAction(ctx.toast);
  const judge = async (id: string, verdict: "verify" | "unverify" | "convert") => {
    const r = await run(() => api.judgeReferral(id, verdict, verdict === "unverify" ? "could not be substantiated" : ""), verdict === "convert" ? "Case opened from referral" : "Referral updated");
    if (r && verdict === "convert" && r.referral.case_id) go(`case/${r.referral.case_id}`);
    else void q.refresh();
  };
  return (
    <main className="main route-in">
      <div className="page-h">
        <h1 className="display">
          Referrals, <em>verified before they become cases.</em>
        </h1>
      </div>
      <div className="split">
        <Panel title="Record a referral">
          <form
            className="form"
            onSubmit={async (e) => {
              e.preventDefault();
              if (await run(() => api.addReferral(f), "Referral recorded")) {
                setF({ address: "", description: "", full_name: "", email: "" });
                void q.refresh();
              }
            }}
          >
            <label>Property address<input className="input" required value={f.address} onChange={(e) => setF({ ...f, address: e.target.value })} /></label>
            <label>What was reported<textarea className="input" required value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></label>
            <div className="grid">
              <label>Name<input className="input" value={f.full_name} onChange={(e) => setF({ ...f, full_name: e.target.value })} /></label>
              <label>Email<input className="input" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></label>
            </div>
            <ErrorLine error={error} />
            <button className="btn primary" disabled={busy}>Record</button>
          </form>
        </Panel>
        <Panel title="Queue" meta={`${q.data?.referrals.length ?? 0}`}>
          <table className="t">
            <thead><tr><th>Address</th><th>Report</th><th>Verdict</th><th /></tr></thead>
            <tbody>
              {(q.data?.referrals ?? []).map((r) => (
                <tr key={r.id}>
                  <td>{r.address}</td>
                  <td className="dim">{r.description}</td>
                  <td><StatusPill status={r.verdict} /></td>
                  <td className="nowrap">
                    {r.case_id ? (
                      <a className="btn ghost small" href={`#case/${r.case_id}`}>Open case</a>
                    ) : ctx.can("decision:record") ? (
                      <>
                        <button className="btn ghost small" onClick={() => void judge(r.id, "convert")}>Open case</button>
                        <button className="btn ghost small" onClick={() => void judge(r.id, "unverify")}>Not verified</button>
                      </>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      </div>
    </main>
  );
}
