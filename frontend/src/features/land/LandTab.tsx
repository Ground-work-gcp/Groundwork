import { useEffect, useState } from "react";
import { usePoll } from "../../hooks";
import { api } from "../../services/api";
import type * as T from "../../types";
import { Empty, ErrorLine, Panel, SeverityPill, Tile, label, useAction } from "../../components/ui";
import type { CaseCtx } from "../cases/CaseView";

/* Rural land records (India). The system prepares the case; ownership decisions stay with
   revenue authorities and courts, and every valuation carries a stated range and confidence. */

function Pill({ tone, text }: { tone: string; text: string }) {
  return <span className={`pill ${tone}`}>{text}</span>;
}
function ConsentPill({ status }: { status: string }) {
  return <Pill tone={status === "consented" ? "green" : status === "objected" ? "red" : status === "unknown" ? "dim" : "amber"} text={label(status)} />;
}

export function inr(n: number | null | undefined): string {
  return n == null ? "–" : `₹${Math.round(n).toLocaleString("en-IN")}`;
}
const SECTIONS = [
  { key: "records", label: "Records" },
  { key: "family", label: "Family & shares" },
  { key: "value", label: "Value" },
  { key: "lender", label: "Lender readiness" },
  { key: "costs", label: "Costs" },
  { key: "explain", label: "Explain" },
  { key: "agent", label: "Agent" },
] as const;
type Section = (typeof SECTIONS)[number]["key"];

export function LandTab({ c, section }: { c: CaseCtx; section?: string }) {
  const land = usePoll(() => api.land(c.caseId), 30_000, [c.caseId], true);
  const cat = usePoll(api.landCatalog, 600_000, [], true);
  const s: Section = (SECTIONS.find((x) => x.key === section)?.key ?? "records") as Section;
  const refresh = () => { void land.refresh(); c.refresh(); };
  const d = land.data;
  return (
    <div className="stack land-root">
      <nav className="land-sections" aria-label="Land sections">
        {SECTIONS.map((x) => <a key={x.key} href={`#case/${c.caseId}/land/${x.key}`} className={s === x.key ? "on" : ""}>{x.label}</a>)}
      </nav>
      <p className="faint small">Groundwork prepares the case. Ownership decisions stay with the revenue authorities and the courts; every value is a range with a confidence.</p>
      {!d || !cat.data ? <p className="faint">Loading…</p> : s === "records" ? <Records c={c} d={d} cat={cat.data} refresh={refresh} />
        : s === "family" ? <Family c={c} d={d} refresh={refresh} />
        : s === "value" ? <Value c={c} d={d} refresh={refresh} />
        : s === "lender" ? <Lender c={c} d={d} cat={cat.data} refresh={refresh} />
        : s === "costs" ? <Costs c={c} cat={cat.data} />
        : s === "explain" ? <Explain c={c} cat={cat.data} />
        : <Agent c={c} d={d} cat={cat.data} refresh={refresh} />}
    </div>
  );
}

interface P { c: CaseCtx; d: T.LandOverview; refresh: () => void }

/* ── records: parcel, derived records, concordance, boundaries ────────────── */

function Records({ c, d, cat, refresh }: P & { cat: T.LandCatalog }) {
  const p = d.parcel;
  const [form, setForm] = useState({
    region_code: p?.region_code ?? "TN", district: p?.district ?? "", taluk: p?.taluk ?? "", village: p?.village ?? "", survey_number: p?.survey_number ?? "",
    extent_text: p?.extent_text ?? "", land_class: p?.land_class ?? "", guideline_inr_per_sqm: p?.guideline_inr_per_sqm?.toString() ?? "",
    personal_law: p?.personal_law ?? "hindu", property_nature: p?.property_nature ?? "self_acquired", recorded_holder: p?.recorded_holder ?? "", road_access: p?.road_access ?? false,
  });
  const [conc, setConc] = useState<T.Concordance | null>(d.latest.concordance?.result ?? null);
  const { busy, error, run } = useAction(c.toast);
  const region = cat.regions.find((r) => r.code === form.region_code);
  const write = c.can("evidence:write");
  const save = async () => {
    const body = { ...form, guideline_inr_per_sqm: form.guideline_inr_per_sqm ? Number(form.guideline_inr_per_sqm) : null };
    if (await run(() => api.saveParcel(c.caseId, body), "Parcel saved")) refresh();
  };
  const compare = async () => {
    const r = await run(() => api.landAnalysis<T.Concordance>(c.caseId, "concordance"), "Records compared; findings updated");
    if (r) { setConc(r.analysis.result); refresh(); }
  };
  const field = (k: keyof typeof form, l: string, ph = "") => (
    <label>{l}<input className="input" value={String(form[k])} placeholder={ph} onChange={(e) => setForm({ ...form, [k]: e.target.value })} disabled={!write} /></label>
  );
  return (
    <div className="split">
      <div className="stack">
        <Panel title="The plot" meta={p ? `S.No. ${p.survey_key || "—"}` : "not entered"}>
          <form className="form" onSubmit={(e) => { e.preventDefault(); void save(); }}>
            <div className="grid2">
              <label>State
                <select className="input" value={form.region_code} onChange={(e) => setForm({ ...form, region_code: e.target.value })} disabled={!write}>
                  {cat.regions.map((r) => <option key={r.code} value={r.code}>{r.name}</option>)}
                </select>
              </label>
              {field("district", "District")}
              {field("taluk", "Taluk / tehsil / mandal")}
              {field("village", "Village")}
              {field("survey_number", "Survey number", "S.F. No. 123/4A")}
              {field("extent_text", "Extent", region?.units.includes("gunta") ? "2 acres 15 guntas" : "1 acre 20 cents")}
              {field("guideline_inr_per_sqm", "Guideline value (₹ per m²)", "150")}
              {field("recorded_holder", "Name on the record")}
              <label>Personal law
                <select className="input" value={form.personal_law} onChange={(e) => setForm({ ...form, personal_law: e.target.value })} disabled={!write}>
                  {["hindu", "christian", "muslim", "other"].map((x) => <option key={x} value={x}>{label(x)}</option>)}
                </select>
              </label>
              <label>Nature of the land
                <select className="input" value={form.property_nature} onChange={(e) => setForm({ ...form, property_nature: e.target.value })} disabled={!write}>
                  <option value="self_acquired">self-acquired</option><option value="ancestral">ancestral (coparcenary)</option>
                </select>
              </label>
            </div>
            <label className="row small"><input type="checkbox" checked={form.road_access} onChange={(e) => setForm({ ...form, road_access: e.target.checked })} disabled={!write} /> road access</label>
            {write ? <div className="row"><button className="btn" disabled={busy}>Save plot</button></div> : null}
          </form>
          {region ? <p className="faint small">In {region.name} the record of rights is the <b>{region.records.ror}</b>; the {region.offices.village_official} keeps it and the {region.offices.revenue_office} changes it ({region.records.mutation}).</p> : null}
        </Panel>
        <Panel title="Records" meta={`${d.records.length}`} actions={write ? <button className="btn small" disabled={busy} onClick={async () => { const r = await run(() => api.deriveLandRecords(c.caseId)); if (r) { c.toast(`${r.created} record(s), ${r.comparables} comparable sale(s) read from the documents`); refresh(); } }}>Read uploaded documents</button> : undefined}>
          <ErrorLine error={error} />
          {d.records.length ? (
            <table className="t">
              <thead><tr><th>Record</th><th>S.No.</th><th>Extent</th><th>Holder(s)</th><th>Date</th><th /></tr></thead>
              <tbody>
                {d.records.map((r) => (
                  <tr key={r.id}>
                    <td>{label(r.source_type)}{r.document_id ? <a className="small" href={`#case/${c.caseId}/evidence/${r.document_id}`}> · source{r.page_number ? ` p.${r.page_number}` : ""}</a> : null}</td>
                    <td className="mono">{r.survey_key || "—"}</td>
                    <td className="small">{r.extent_text || "—"}</td>
                    <td className="small">{r.holders.join(", ") || "—"}</td>
                    <td className="small mono">{r.record_date ?? ""}</td>
                    <td><div className="row" style={{ flexWrap: "nowrap" }}><span className="faint small">{r.source}</span>{write ? <button className="btn ghost small" aria-label="Remove record" onClick={async () => { if (await run(() => api.removeLandRecord(r.id), "Record removed")) refresh(); }}>×</button> : null}</div></td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <Empty title="No land records yet.">Upload the {region?.records.ror ?? "record of rights"}, {region?.records.cultivation ?? "cultivation record"}, {region?.records.sketch ?? "sketch"} and {region?.records.encumbrance ?? "EC"} on the Evidence tab, then read them here.</Empty>}
        </Panel>
      </div>
      <div className="stack">
        <Panel title="Where the records disagree" meta={conc ? `${conc.issues.length} issue(s)` : ""} actions={c.can("analysis:run") ? <button className="btn small primary" disabled={busy || !d.records.length} onClick={() => void compare()}>Compare records</button> : undefined}>
          {conc ? (
            <div className="stack">
              <div className="chips">
                {Object.entries(conc.counts).filter(([, v]) => v).map(([k, v]) => <span key={k} className="chip">{label(k)} · {v}</span>)}
                <span className="chip">EC covers {conc.ec_years} yr</span>
              </div>
              {conc.issues.map((i) => (
                <div key={i.key} className={`fix ${i.severity === "critical" || i.severity === "high" ? "red" : "lilac"}`}>
                  <div className="head"><SeverityPill severity={i.severity} /> {i.title}</div>
                  <div className="small">{i.detail}</div>
                  <div className="small dim">Who can correct it: <b>{i.office}</b> · {i.records.map((r) => r.document_id ? <a key={r.record_id} href={`#case/${c.caseId}/evidence/${r.document_id}`}>{r.label} </a> : <span key={r.record_id}>{r.label} </span>)}</div>
                </div>
              ))}
              <Matrix conc={conc} />
            </div>
          ) : <p className="faint">Compare the records to see, attribute by attribute, where they agree and which office corrects each difference.</p>}
        </Panel>
        {conc ? <Panel title="Boundaries" meta="checked against the neighbours' own records"><Compass conc={conc} /></Panel> : null}
      </div>
    </div>
  );
}

function Matrix({ conc }: { conc: T.Concordance }) {
  if (!conc.columns.length) return null;
  return (
    <div className="scroll-x">
      <table className="t land-matrix">
        <thead><tr><th /> {conc.columns.map((col) => <th key={col.record_id} className="small">{col.label}</th>)}</tr></thead>
        <tbody>
          {conc.rows.map((row) => (
            <tr key={row.attribute}>
              <th className="small">{label(row.attribute)}</th>
              {row.cells.map((cell) => <td key={cell.record_id} className={`small cell-${cell.status}`}>{cell.value || "·"}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Compass({ conc }: { conc: T.Concordance }) {
  const at: Record<string, [number, number]> = { north: [150, 28], south: [150, 276], east: [270, 152], west: [30, 152] };
  const byDir: Record<string, T.Concordance["boundary_edges"]> = {};
  conc.boundary_edges.forEach((e) => { (byDir[e.direction] ??= []).push(e); });
  const tone = (s: string) => (s === "reciprocal" ? "var(--green)" : s === "conflict" ? "var(--red)" : "var(--faint)");
  return (
    <div className="stack">
      <svg viewBox="-70 0 440 300" className="compass" role="img" aria-label={`Boundaries of survey number ${conc.survey_key}`}>
        <rect x="95" y="95" width="110" height="110" rx="6" fill="var(--surface)" stroke="var(--ink)" />
        <text x="150" y="146" textAnchor="middle" className="cmp-key">S.No.</text>
        <text x="150" y="168" textAnchor="middle" className="cmp-key strong">{conc.survey_key}</text>
        {Object.entries(at).map(([dir, [x, y]]) => {
          const edges = byDir[dir] ?? [];
          return (
            <g key={dir}>
              <text x={x} y={y - 12} textAnchor="middle" className="cmp-dir">{dir}</text>
              {edges.length ? edges.slice(0, 2).map((e, i) => (
                <text key={e.neighbour} x={x} y={y + 4 + i * 15} textAnchor="middle" className="cmp-n" fill={tone(e.status)}>{e.neighbour.length > 18 ? e.neighbour.slice(0, 17) + "…" : e.neighbour}</text>
              )) : <text x={x} y={y + 4} textAnchor="middle" className="cmp-n" fill="var(--faint)">—</text>}
            </g>
          );
        })}
      </svg>
      <ul className="refs small">
        {conc.boundary_edges.map((e) => <li key={`${e.direction}-${e.neighbour}`}><Pill tone={e.status === "reciprocal" ? "green" : e.status === "conflict" ? "red" : "dim"} text={e.status} /> {e.direction}: {e.neighbour} — <span className="dim">{e.detail}</span></li>)}
      </ul>
    </div>
  );
}

/* ── family tree, draft shares, co-heir consent ───────────────────────────── */

const EMPTY_MEMBER = { name: "", sex: "male", is_alive: true, date_of_death: "", parent_ids: [] as string[], spouse_ids: [] as string[], is_recorded_holder: false, lives_in_village: true, location: "" };

function Family({ c, d, refresh }: P) {
  const [m, setM] = useState(EMPTY_MEMBER);
  const [succ, setSucc] = useState<T.Succession | null>(d.latest.succession?.result ?? null);
  const [link, setLink] = useState<{ name: string; link: string } | null>(null);
  const { busy, error, run } = useAction(c.toast);
  const names = Object.fromEntries(d.members.map((x) => [x.id, x.name]));
  const write = c.can("evidence:write");
  const compute = async () => { const r = await run(() => api.landAnalysis<T.Succession>(c.caseId, "succession")); if (r) setSucc(r.analysis.result); };
  useEffect(() => { if (!succ && d.members.length) void compute(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const toggle = (k: "parent_ids" | "spouse_ids", id: string) => setM({ ...m, [k]: m[k].includes(id) ? m[k].filter((x) => x !== id) : [...m[k], id] });
  return (
    <div className="split">
      <div className="stack">
        <Panel title="Family tree" meta={`${d.members.length} people`}>
          <ErrorLine error={error} />
          <table className="t">
            <tbody>
              {d.members.map((x) => (
                <tr key={x.id}>
                  <td>{x.name}{x.is_recorded_holder ? <span className="chip">on the record</span> : null}<div className="faint small">{[x.parent_ids.length ? `child of ${x.parent_ids.map((p) => names[p]).join(" & ")}` : "", x.spouse_ids.length ? `spouse of ${x.spouse_ids.map((p) => names[p]).join(", ")}` : ""].filter(Boolean).join(" · ")}</div></td>
                  <td className="small">{x.is_alive ? (x.lives_in_village ? "in the village" : `away${x.location ? ` · ${x.location}` : ""}`) : `died ${x.date_of_death ?? "(date unknown)"}`}</td>
                  <td>{x.is_alive ? <ConsentPill status={x.consent_status} /> : null}</td>
                  <td><div className="row" style={{ flexWrap: "nowrap" }}>
                    {x.is_alive && c.can("case:write") ? <button className="btn ghost small" disabled={busy} onClick={async () => { const r = await run(() => api.consentLink(x.id)); if (r) { setLink({ name: x.name, link: r.link }); refresh(); } }}>Consent link</button> : null}
                    {write ? <button className="btn ghost small" aria-label={`Remove ${x.name}`} onClick={async () => { if (await run(() => api.removeMember(x.id), "Removed")) refresh(); }}>×</button> : null}
                  </div></td>
                </tr>
              ))}
            </tbody>
          </table>
          {link ? <div className="fix green"><div className="head">Single-use link for {link.name} (30 days)</div><div className="mono small" style={{ overflowWrap: "anywhere" }}>{link.link}</div><div className="small dim">Send it by SMS or WhatsApp. They see their draft share and reply; the reply goes into the case history.</div></div> : null}
          {write ? (
            <form className="form" style={{ marginTop: 12 }} onSubmit={async (e) => { e.preventDefault(); if (await run(() => api.addMember(c.caseId, { ...m, date_of_death: m.is_alive ? null : m.date_of_death || null }), "Added to the tree")) { setM(EMPTY_MEMBER); refresh(); } }}>
              <div className="grid2">
                <label>Name<input className="input" required minLength={2} value={m.name} onChange={(e) => setM({ ...m, name: e.target.value })} /></label>
                <label>Sex<select className="input" value={m.sex} onChange={(e) => setM({ ...m, sex: e.target.value })}><option value="male">male</option><option value="female">female</option><option value="unknown">unknown</option></select></label>
                <label className="row small"><input type="checkbox" checked={!m.is_alive} onChange={(e) => setM({ ...m, is_alive: !e.target.checked })} /> has died</label>
                {!m.is_alive ? <label>Date of death<input className="input" placeholder="DD-MM-YYYY" value={m.date_of_death} onChange={(e) => setM({ ...m, date_of_death: e.target.value })} /></label> : <label>Lives<select className="input" value={m.lives_in_village ? "in" : "away"} onChange={(e) => setM({ ...m, lives_in_village: e.target.value === "in" })}><option value="in">in the village</option><option value="away">away from the village</option></select></label>}
              </div>
              {d.members.length ? (
                <div className="small">
                  <div className="eyebrow">Parents</div>
                  <div className="chips">{d.members.map((x) => <button type="button" key={x.id} className={`chip-btn${m.parent_ids.includes(x.id) ? " on" : ""}`} onClick={() => toggle("parent_ids", x.id)}>{x.name}</button>)}</div>
                  <div className="eyebrow">Spouse</div>
                  <div className="chips">{d.members.map((x) => <button type="button" key={x.id} className={`chip-btn${m.spouse_ids.includes(x.id) ? " on" : ""}`} onClick={() => toggle("spouse_ids", x.id)}>{x.name}</button>)}</div>
                </div>
              ) : null}
              <label className="row small"><input type="checkbox" checked={m.is_recorded_holder} onChange={(e) => setM({ ...m, is_recorded_holder: e.target.checked })} /> this is the person named on the record</label>
              <button className="btn" disabled={busy}>Add person</button>
            </form>
          ) : null}
        </Panel>
      </div>
      <div className="stack">
        <Panel title="Draft shares" meta={succ?.law_name ?? ""} actions={c.can("analysis:run") ? <button className="btn small primary" disabled={busy} onClick={() => void compute()}>Work out shares</button> : undefined}>
          {succ ? (
            <div className="stack">
              <p className="faint small">{succ.disclaimer}</p>
              {succ.shares.length ? (
                <table className="t">
                  <thead><tr><th>Co-owner</th><th>Draft share</th><th>Where</th><th>Consent</th></tr></thead>
                  <tbody>{succ.shares.map((x) => (
                    <tr key={x.member_id}>
                      <td>{x.name}{!x.alive ? <span className="faint small"> (deceased — unresolved)</span> : null}</td>
                      <td className="mono">{x.share}</td>
                      <td className="small">{x.lives_in_village ? "village" : "away"}</td>
                      <td><ConsentPill status={x.consent_status} /></td>
                    </tr>
                  ))}</tbody>
                </table>
              ) : null}
              {succ.steps.length ? <ol className="small stack" style={{ gap: 6 }}>{succ.steps.map((st, i) => <li key={i}>{st.text}</li>)}</ol> : null}
              {succ.unresolved.map((u, i) => <div key={i} className="fix red small"><b>{u.person}</b>: {u.reason}</div>)}
              {succ.warnings.map((w, i) => <div key={i} className="fix lilac small">{w}</div>)}
              <details className="small"><summary>Assumptions</summary><ul>{succ.assumptions.map((a, i) => <li key={i}>{a}</li>)}</ul></details>
            </div>
          ) : <p className="faint">Add the family — who died, when, and who their children and spouses were — then work out the draft shares.</p>}
        </Panel>
      </div>
    </div>
  );
}

/* ── value: a range with confidence, never one number ─────────────────────── */

function Value({ c, d, refresh }: P) {
  const [val, setVal] = useState<T.Valuation | null>(d.latest.valuation?.result ?? null);
  const comps = usePoll(() => api.comparables(), 60_000, [], true);
  const [claimed, setClaimed] = useState("");
  const [cmp, setCmp] = useState({ village: d.parcel?.village ?? "", survey_number: "", sale_date: "", extent_text: "", consideration_inr: "", land_class: d.parcel?.land_class ?? "wet", distance_km: "" });
  const { busy, error, run } = useAction(c.toast);
  const estimate = async () => { const r = await run(() => api.landAnalysis<T.Valuation>(c.caseId, "valuation", { claimed_value_inr: claimed ? Number(claimed) : null })); if (r) { setVal(r.analysis.result); refresh(); } };
  return (
    <div className="split">
      <div className="stack">
        <Panel title="What the land is carefully worth" actions={c.can("analysis:run") ? <button className="btn small primary" disabled={busy} onClick={() => void estimate()}>Estimate</button> : undefined}>
          <ErrorLine error={error} />
          <label className="small">A value someone has claimed (optional, ₹)<input className="input" value={claimed} onChange={(e) => setClaimed(e.target.value.replace(/\D/g, ""))} placeholder="e.g. what a broker quoted" /></label>
          {val?.status === "estimated" ? (
            <div className="stack">
              <div className="money-band land-tiles">
                <Tile i={0} n={val.p10_inr} l="P10 (low)" fmt={inr} />
                <Tile i={1} n={val.p50_inr} l="P50 (most likely)" fmt={inr} />
                <Tile i={2} n={val.p90_inr} l="P90 (high)" fmt={inr} />
                <Tile i={3} n={val.lending_value_inr} l="lending value" fmt={inr} moon />
              </div>
              <RangeBar v={val} />
              <p className="small">Confidence <b>{val.confidence}</b> · {val.effective_comparables} effective comparable sale(s) · market weight {Math.round(val.market_weight * 100)}% (the rest is the guideline value) · loan ceiling at {Math.round(val.ltv * 100)}% LTV: <b>{inr(val.loan_ceiling_inr)}</b></p>
              {val.haircut_reasons.length ? <div className="fix lilac small"><div className="head">Title haircut {Math.round(val.title_haircut * 100)}% on the lending value</div>{val.haircut_reasons.map((h) => <div key={h.kind}>−{Math.round(h.cut * 100)}% · {h.why}</div>)}</div> : null}
              {val.warnings.map((w, i) => <div key={i} className="fix red small">{w}</div>)}
              <details className="small"><summary>Method and comparables</summary><p className="dim">{val.method}</p>
                <table className="t"><thead><tr><th>Sale</th><th>₹/m² declared</th><th>adjusted</th><th>weight</th></tr></thead><tbody>
                  {val.comparables.map((x) => <tr key={x.id}><td>{x.sale_date} · {x.survey_number || x.village}{x.censored_at_guideline ? <span className="chip">at guideline</span> : null}{x.outlier_downweighted ? <span className="chip">outlier</span> : null}</td><td className="mono">{x.price_per_sqm}</td><td className="mono">{x.adjusted_per_sqm}</td><td className="mono">{x.weight}</td></tr>)}
                </tbody></table>
              </details>
            </div>
          ) : val ? <Empty title="Not enough to value yet.">{val.reason}</Empty> : <p className="faint">Enter the guideline value on the plot and a few nearby sales, then estimate.</p>}
        </Panel>
      </div>
      <div className="stack">
        <Panel title="Nearby sales" meta={`${comps.data?.comparables.length ?? 0} in your organisation`}>
          <table className="t"><tbody>
            {(comps.data?.comparables ?? []).map((x) => (
              <tr key={x.id}><td className="small">{x.sale_date} · {x.village} {x.survey_number}</td><td className="small mono">{inr(x.consideration_inr)}</td><td className="small mono">₹{x.price_per_sqm}/m²</td>
                <td>{c.can("analysis:run") ? <button className="btn ghost small" onClick={async () => { if (await run(() => api.removeComparable(x.id))) void comps.refresh(); }}>×</button> : null}</td></tr>
            ))}
          </tbody></table>
          {c.can("analysis:run") ? (
            <form className="form" style={{ marginTop: 10 }} onSubmit={async (e) => { e.preventDefault(); if (await run(() => api.addComparable({ ...cmp, region_code: d.parcel?.region_code ?? "TN", taluk: d.parcel?.taluk ?? "", consideration_inr: Number(cmp.consideration_inr), distance_km: cmp.distance_km ? Number(cmp.distance_km) : null }), "Sale recorded")) { setCmp({ ...cmp, survey_number: "", sale_date: "", extent_text: "", consideration_inr: "", distance_km: "" }); void comps.refresh(); } }}>
              <div className="grid2">
                <label>Village<input className="input" required value={cmp.village} onChange={(e) => setCmp({ ...cmp, village: e.target.value })} /></label>
                <label>Survey no.<input className="input" value={cmp.survey_number} onChange={(e) => setCmp({ ...cmp, survey_number: e.target.value })} /></label>
                <label>Sale date<input className="input" required placeholder="DD-MM-YYYY" value={cmp.sale_date} onChange={(e) => setCmp({ ...cmp, sale_date: e.target.value })} /></label>
                <label>Extent<input className="input" required placeholder="50 cents" value={cmp.extent_text} onChange={(e) => setCmp({ ...cmp, extent_text: e.target.value })} /></label>
                <label>Price declared (₹)<input className="input" required value={cmp.consideration_inr} onChange={(e) => setCmp({ ...cmp, consideration_inr: e.target.value.replace(/\D/g, "") })} /></label>
                <label>Distance (km)<input className="input" value={cmp.distance_km} onChange={(e) => setCmp({ ...cmp, distance_km: e.target.value })} /></label>
              </div>
              <button className="btn" disabled={busy}>Add sale</button>
            </form>
          ) : null}
          <p className="faint small">Sale deeds uploaded to any case are added here automatically. Deeds registered at the guideline value are treated as floors, not prices.</p>
        </Panel>
      </div>
    </div>
  );
}

function RangeBar({ v }: { v: T.Valuation }) {
  const lo = Math.min(v.lending_value_inr, v.p10_inr, v.guideline_total_inr ?? Infinity) * 0.9;
  const hi = Math.max(v.p90_inr, v.claimed_value_inr ?? 0, v.guideline_total_inr ?? 0) * 1.05;
  const x = (n: number) => `${((n - lo) / (hi - lo)) * 100}%`;
  return (
    <div className="land-range" aria-label={`Range ${inr(v.p10_inr)} to ${inr(v.p90_inr)}`}>
      <div className="rb-band" style={{ left: x(v.p10_inr), width: `calc(${x(v.p90_inr)} - ${x(v.p10_inr)})` }} />
      <div className="rb-mark p50" style={{ left: x(v.p50_inr) }}><span>P50</span></div>
      <div className="rb-mark lend" style={{ left: x(v.lending_value_inr) }}><span>lend</span></div>
      {v.guideline_total_inr ? <div className="rb-mark guide" style={{ left: x(v.guideline_total_inr) }}><span>guideline</span></div> : null}
      {v.claimed_value_inr ? <div className="rb-mark claim" style={{ left: x(v.claimed_value_inr) }}><span>claimed</span></div> : null}
    </div>
  );
}

/* ── lender readiness, the fix plan, the passport ─────────────────────────── */

function Lender({ c, d, cat, refresh }: P & { cat: T.LandCatalog }) {
  const [lender, setLender] = useState((d.latest.readiness?.result.lender as string) ?? "bank_crop_loan");
  const [amount, setAmount] = useState("");
  const [r, setR] = useState<T.Readiness | null>(d.latest.readiness?.result ?? null);
  const [doc, setDoc] = useState<string | null>(null);
  const { busy, error, run } = useAction(c.toast);
  const check = async () => { const x = await run(() => api.landAnalysis<T.Readiness>(c.caseId, "readiness", { lender, loan_amount_inr: amount ? Number(amount) : null })); if (x) { setR(x.analysis.result); refresh(); } };
  const span = Math.max(1, r?.days_to_ready ?? 1);
  return (
    <div className="split">
      <div className="stack">
        <Panel title="What the branch will ask for" actions={c.can("analysis:run") ? <button className="btn small primary" disabled={busy} onClick={() => void check()}>Check</button> : undefined}>
          <div className="row">
            <select className="input" value={lender} onChange={(e) => setLender(e.target.value)}>{Object.entries(cat.lenders).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}</select>
            <input className="input" style={{ maxWidth: 180 }} placeholder="loan amount ₹" value={amount} onChange={(e) => setAmount(e.target.value.replace(/\D/g, ""))} />
          </div>
          <ErrorLine error={error} />
          {r ? (
            <div className="stack" style={{ marginTop: 10 }}>
              <div className="money-band land-tiles">
                <Tile i={0} n={r.score} l="readiness score /100" />
                <Tile i={1} n={r.days_to_ready} l="days to fix (indicative)" />
                <Tile i={2} n={r.official_fees_inr} l="official fees" fmt={inr} />
              </div>
              <div className={`fix ${r.verdict === "not_ready" ? "red" : "green"}`}><div className="head">{label(r.verdict)} · {r.lender_label}</div>{r.notes.map((n, i) => <div key={i} className="small">{n}</div>)}</div>
              <table className="t"><tbody>
                {r.requirements.map((q) => <tr key={q.id}><td>{q.label}{q.critical ? <span className="chip">critical</span> : null}<div className="faint small">{q.why}</div></td><td><Pill tone={q.status === "met" ? "green" : q.status === "not_needed" ? "dim" : q.status === "partial" ? "amber" : "red"} text={label(q.status)} /></td><td className="small dim">{q.detail}</td></tr>)}
              </tbody></table>
              {r.local_branch_asks.length ? <div className="fix lilac small"><div className="head">Also asked for by the local branch (field visits)</div>{r.local_branch_asks.map((a) => <div key={a}>· {a}</div>)}</div> : null}
            </div>
          ) : <p className="faint">Pick a lender and check.</p>}
        </Panel>
      </div>
      <div className="stack">
        {r?.plan.length ? (
          <Panel title="Fix plan" meta={`critical path · ${r.days_to_ready} days`}>
            <ul className="gantt">
              {r.plan.map((p) => (
                <li key={p.service} className={p.critical ? "crit" : ""}>
                  <div className="g-label"><b>{p.name}</b><span className="small dim"> · {p.office} · ₹{p.fee_inr} · {p.days} d</span>{p.documents.length ? <div className="faint small">carry: {p.documents.join(", ")}</div> : null}</div>
                  <div className="g-track"><span className="g-bar" style={{ left: `${(p.start_day / span) * 100}%`, width: `${Math.max(2, (p.days / span) * 100)}%` }} /></div>
                </li>
              ))}
            </ul>
            <p className="faint small">{r.disclaimer}</p>
          </Panel>
        ) : null}
        <Panel title="Land credit passport" meta="issued after a second person approves">
          {c.can("action:propose") ? <button className="btn" disabled={busy} onClick={async () => { const x = await run(() => api.requestPassport(c.caseId, { lender, loan_amount_inr: amount ? Number(amount) : 0 })); if (x) c.toast(`Proposed as action ${x.action.seq} — an attorney approves it on the Approvals page`); }}>Request a passport for this lender</button> : null}
          {d.passports.map((p) => (
            <div key={p.id} className={`fix ${p.status === "active" ? "green" : ""}`}>
              <div className="head mono">{p.code} <Pill tone={p.status === "active" ? "green" : "dim"} text={p.status} /></div>
              <div className="mono small" style={{ overflowWrap: "anywhere" }}>sha256 {p.sha256}</div>
              <div className="row"><button className="btn ghost small" onClick={async () => setDoc(await run(() => api.passportDocument(p.id)))}>View</button><a className="btn ghost small" href={`#verify/${p.code}`}>Lender's check</a></div>
            </div>
          ))}
          {doc ? <pre className="pagetext">{doc}</pre> : null}
        </Panel>
      </div>
    </div>
  );
}

/* ── costs: following the money ───────────────────────────────────────────── */

function Costs({ c, cat }: { c: CaseCtx; cat: T.LandCatalog }) {
  const q = usePoll(() => api.landCosts(c.caseId), 60_000, [c.caseId], true);
  const [f, setF] = useState({ entry_date: "", category: "official_fee", payee_kind: "office", service: "", amount_inr: "", days_spent: "", has_receipt: false, principal_inr: "", monthly_rate_pct: "", months: "", notes: "" });
  const { busy, error, run } = useAction(c.toast);
  const sm = q.data?.summary;
  const loan = f.category === "informal_loan";
  return (
    <div className="split">
      <div className="stack">
        <Panel title="What the family has spent" meta="rupees and days">
          {sm ? (
            <div className="stack">
              <div className="money-band land-tiles">
                <Tile i={0} n={sm.total_inr} l="spent" fmt={inr} />
                <Tile i={1} n={sm.days} l="days lost" />
                <Tile i={2} n={sm.unreceipted_inr} l="without a receipt" fmt={inr} />
                <Tile i={3} n={sm.informal_premium_inr} l="extra interest paid" fmt={inr} moon />
              </div>
              {sm.excess_over_official.map((x) => <div key={x.service} className="fix red small"><b>{x.name}</b>: paid {inr(x.paid_inr)} against an indicative official fee of {inr(x.official_fee_inr)} — {inr(x.excess_inr)} more.</div>)}
              {sm.informal_loans.map((l) => <div key={l.id} className="fix lilac small"><b>Informal loan of {inr(l.principal_inr)}</b> at {l.annual_rate_pct}%/yr for {l.months} months: {inr(l.interest_paid_inr)} interest, against {inr(l.formal_interest_inr)} at the {sm.formal_rate_pct}% formal rate — the cost of land that could not be pledged.</div>)}
              <p className="faint small">{sm.note}</p>
            </div>
          ) : null}
          <table className="t"><tbody>
            {(q.data?.entries ?? []).map((e) => <tr key={e.id}><td className="small mono">{e.entry_date}</td><td className="small">{label(e.category)}{e.service ? ` · ${label(e.service)}` : ""}</td><td className="small">{e.notes}</td><td className="small mono">{e.category === "informal_loan" ? "loan" : inr(e.amount_inr)}</td><td className="small">{e.has_receipt ? "receipt" : ""}</td>
              <td>{c.can("financials:write") ? <button className="btn ghost small" onClick={async () => { if (await run(() => api.removeLandCost(e.id))) void q.refresh(); }}>×</button> : null}</td></tr>)}
          </tbody></table>
        </Panel>
      </div>
      <div className="stack">
        {c.can("financials:write") ? (
          <Panel title="Log a cost">
            <ErrorLine error={error} />
            <form className="form" onSubmit={async (e) => { e.preventDefault(); const body = { ...f, amount_inr: Number(f.amount_inr || 0), days_spent: Number(f.days_spent || 0), principal_inr: loan ? Number(f.principal_inr) : null, monthly_rate_pct: loan ? Number(f.monthly_rate_pct) : null, months: loan ? Number(f.months) : null }; if (await run(() => api.addLandCost(c.caseId, body), "Logged")) { setF({ ...f, amount_inr: "", days_spent: "", notes: "", principal_inr: "", monthly_rate_pct: "", months: "" }); void q.refresh(); } }}>
              <div className="grid2">
                <label>Date<input className="input" required placeholder="DD-MM-YYYY" value={f.entry_date} onChange={(e) => setF({ ...f, entry_date: e.target.value })} /></label>
                <label>What for<select className="input" value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}>{cat.cost_categories.map((x) => <option key={x} value={x}>{label(x)}</option>)}</select></label>
                <label>Paid to<select className="input" value={f.payee_kind} onChange={(e) => setF({ ...f, payee_kind: e.target.value })}>{cat.payee_kinds.map((x) => <option key={x} value={x}>{label(x)}</option>)}</select></label>
                <label>Service<select className="input" value={f.service} onChange={(e) => setF({ ...f, service: e.target.value })}><option value="">—</option>{["death_certificate", "legal_heir_certificate", "mutation", "encumbrance_certificate", "sketch_copy", "survey_measurement", "cultivation_record", "tax_receipt", "valuation_report", "legal_scrutiny", "title_documents"].map((x) => <option key={x} value={x}>{label(x)}</option>)}</select></label>
                {loan ? (<>
                  <label>Principal (₹)<input className="input" required value={f.principal_inr} onChange={(e) => setF({ ...f, principal_inr: e.target.value.replace(/\D/g, "") })} /></label>
                  <label>Interest per month (%)<input className="input" required value={f.monthly_rate_pct} onChange={(e) => setF({ ...f, monthly_rate_pct: e.target.value })} /></label>
                  <label>Months<input className="input" required value={f.months} onChange={(e) => setF({ ...f, months: e.target.value })} /></label>
                </>) : (<>
                  <label>Amount (₹)<input className="input" value={f.amount_inr} onChange={(e) => setF({ ...f, amount_inr: e.target.value.replace(/\D/g, "") })} /></label>
                  <label>Days spent<input className="input" value={f.days_spent} onChange={(e) => setF({ ...f, days_spent: e.target.value })} /></label>
                </>)}
                <label>Note<input className="input" value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></label>
              </div>
              {!loan ? <label className="row small"><input type="checkbox" checked={f.has_receipt} onChange={(e) => setF({ ...f, has_receipt: e.target.checked })} /> there is a receipt</label> : null}
              <button className="btn" disabled={busy}>Log</button>
            </form>
          </Panel>
        ) : null}
      </div>
    </div>
  );
}

/* ── explain, in the family's language ────────────────────────────────────── */

function Explain({ c, cat }: { c: CaseCtx; cat: T.LandCatalog }) {
  const [lang, setLang] = useState("ta");
  const ex = usePoll(() => api.explainLand(c.caseId, lang), 120_000, [c.caseId, lang], true);
  const [packet, setPacket] = useState<string | null>(null);
  const { error, run } = useAction();
  const names: Record<string, string> = { en: "English", ta: "தமிழ் (Tamil)", hi: "हिन्दी (Hindi)", kn: "ಕನ್ನಡ (Kannada, via AI)", te: "తెలుగు (Telugu, via AI)", mr: "मराठी (Marathi, via AI)" };
  const download = (text: string, name: string) => { const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([text], { type: "text/markdown" })); a.download = name; a.click(); };
  return (
    <div className="split">
      <Panel title="Your land, explained" actions={<select className="input" value={lang} onChange={(e) => setLang(e.target.value)}>{Object.entries(names).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>}>
        {ex.data ? (
          <div className="stack letter" lang={ex.data.language}>
            {ex.data.lines.map((l, i) => <p key={i}>{l}</p>)}
            {ex.data.shares.length ? <ul>{ex.data.shares.map((s) => <li key={s}>{s}</li>)}</ul> : null}
            <p className="faint small">{ex.data.disclaimer}{ex.data.machine_translated ? " · machine translated" : ""}</p>
            {ex.data.note ? <p className="faint small">{ex.data.note}</p> : null}
            <div className="row"><button className="btn ghost small" onClick={() => window.print()}>Print</button></div>
          </div>
        ) : <p className="faint">Loading…</p>}
        <details className="small"><summary>What the records are</summary><ul>{Object.entries(cat.glossary).map(([k, v]) => <li key={k}><b>{label(k)}</b> — {v}</li>)}</ul></details>
      </Panel>
      <Panel title="Mutation packet" meta="a draft for the family to sign and carry" actions={<button className="btn small" onClick={async () => setPacket(await run(() => api.mutationPacket(c.caseId)))}>Prepare</button>}>
        <ErrorLine error={error} />
        {packet ? <div className="letter"><div className="row letter-tools"><button className="btn ghost small" onClick={() => download(packet, `${c.detail.case.reference}-mutation.md`)}>Download .md</button></div><pre className="pagetext">{packet}</pre></div>
          : <p className="faint">The covering application to the revenue office, the draft heirs and shares with how they were worked out, every supporting document marked enclosed or still to obtain, and the differences between the records for the office's attention. Groundwork never files it.</p>}
      </Panel>
    </div>
  );
}

/* ── the agent ────────────────────────────────────────────────────────────── */

function Agent({ c, d, cat, refresh }: P & { cat: T.LandCatalog }) {
  const [runRes, setRun] = useState<T.AgentRun | null>(d.latest.agent_run ? { ...d.latest.agent_run.result, result_hash: d.latest.agent_run.result_hash } : null);
  const [lender, setLender] = useState("bank_crop_loan");
  const [amount, setAmount] = useState("");
  const { busy, error, run } = useAction(c.toast);
  const go = async () => { const r = await run(() => api.landAgent(c.caseId, { lender, loan_amount_inr: amount ? Number(amount) : null }), "Agent finished"); if (r) { setRun(r.run); refresh(); } };
  return (
    <div className="split">
      <div className="stack">
        <Panel title="Land case agent" meta={runRes ? runRes.mode : "plans, runs the tools, hands off the rest"} actions={c.can("analysis:run") ? <button className="btn small primary" disabled={busy} onClick={() => void go()}>{busy ? "Working…" : "Prepare this case"}</button> : undefined}>
          <div className="row">
            <select className="input" value={lender} onChange={(e) => setLender(e.target.value)}>{Object.entries(cat.lenders).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}</select>
            <input className="input" style={{ maxWidth: 180 }} placeholder="loan amount ₹" value={amount} onChange={(e) => setAmount(e.target.value.replace(/\D/g, ""))} />
          </div>
          <ErrorLine error={error} />
          {runRes ? (
            <div className="stack" style={{ marginTop: 10 }}>
              <div className="answer">{runRes.narrative}</div>
              <ol className="stack small" style={{ gap: 6 }}>
                {runRes.steps.map((s) => <li key={s.n}><Pill tone={s.status === "ran" ? "green" : s.status === "blocked" ? "red" : "dim"} text={s.status} /> <b>{label(s.tool)}</b> — {s.summary || s.why}</li>)}
              </ol>
              {runRes.proposed_actions.length ? <div className="fix lilac small">Proposed action(s) {runRes.proposed_actions.join(", ")} — they run only after a person approves them on the <a href="#approvals">Approvals</a> page.</div> : null}
              <p className="faint small mono">run hash {runRes.result_hash?.slice(0, 16)}… · every step is in the case history as an AI action</p>
            </div>
          ) : <p className="faint">The agent reads new documents, compares the records, works out the draft shares, values the land, checks what the lender asks for and builds the fix plan. Anything only a person or an office can do becomes a hand-off below. It never changes ownership and never executes an action.</p>}
        </Panel>
      </div>
      <div className="stack">
        <Panel title="Hand-offs" meta={`${runRes?.handoffs.length ?? 0}`}>
          {runRes?.handoffs.length ? (
            <ul className="needs-list">
              {runRes.handoffs.map((h, i) => <li key={i}><span className="stack" style={{ gap: 2 }}><span className="t">{h.what}</span><span className="s">{h.where} · {h.why}</span></span><span className="pill dim">{h.who}</span></li>)}
            </ul>
          ) : <p className="faint">Nothing waiting on people yet.</p>}
        </Panel>
      </div>
    </div>
  );
}
