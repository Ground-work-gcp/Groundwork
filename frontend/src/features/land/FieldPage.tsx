import { useEffect, useState } from "react";
import type { Ctx } from "../../app/App";
import { usePoll } from "../../hooks";
import { api } from "../../services/api";
import type * as T from "../../types";
import { Empty, ErrorLine, HBars, Panel, Tile, label, useAction } from "../../components/ui";
import { inr } from "./LandTab";

/* Questions to take into the field — answered twice: by what field visits report, and by what
   the organisation's own land cases show. Where the two disagree is itself a finding. */

const list = (v: string) => v.split(/[\n,;]+/).map((x) => x.trim()).filter(Boolean);
const EMPTY = {
  village: "", taluk: "", district: "", region_code: "TN", observed_on: "", households_surveyed: "", households_deceased_holder: "",
  correction_barriers: "", branch_name: "", lender_kind: "", branch_requirements: "", valuation_practices: "",
  names: "", extents: "", boundaries: "", survey_numbers: "", dispute_settlers: "", dispute_days_typical: "",
  family_spend_inr_typical: "", family_days_typical: "", record_readers: "", record_reading_fee_inr: "", notes: "",
};

export function FieldPage({ ctx }: { ctx: Ctx }) {
  const ins = usePoll(api.landInsights, 60_000, [], true);
  const obs = usePoll(api.fieldObservations, 60_000, [], true);
  const cat = usePoll(api.landCatalog, 600_000, [], true);
  const [f, setF] = useState(EMPTY);
  const { busy, error, run } = useAction(ctx.toast);
  const i = ins.data;
  const n = (v: string) => (v === "" ? null : Number(v));
  const submit = async () => {
    const body = {
      village: f.village, taluk: f.taluk, district: f.district, region_code: f.region_code, observed_on: f.observed_on,
      households_surveyed: Number(f.households_surveyed || 0), households_deceased_holder: Number(f.households_deceased_holder || 0),
      correction_barriers: list(f.correction_barriers), branch_name: f.branch_name, lender_kind: f.lender_kind, branch_requirements: list(f.branch_requirements),
      valuation_practices: list(f.valuation_practices),
      disagreement_counts: Object.fromEntries((["names", "extents", "boundaries", "survey_numbers"] as const).filter((k) => f[k] !== "").map((k) => [k, Number(f[k])])),
      dispute_settlers: list(f.dispute_settlers), dispute_days_typical: n(f.dispute_days_typical), family_spend_inr_typical: n(f.family_spend_inr_typical),
      family_days_typical: n(f.family_days_typical), record_readers: list(f.record_readers), record_reading_fee_inr: n(f.record_reading_fee_inr), notes: f.notes,
    };
    if (await run(() => api.addFieldObservation(body), "Field visit recorded")) { setF(EMPTY); void ins.refresh(); void obs.refresh(); }
  };
  useEffect(() => { document.title = "Field · Groundwork"; }, []);
  const txt = (k: keyof typeof EMPTY, l: string, ph = "", area = false) => (
    <label>{l}{area ? <textarea className="input" placeholder={ph} value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} /> : <input className="input" placeholder={ph} value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} />}</label>
  );
  const pct = (x: number | null | undefined) => (x == null ? "–" : `${Math.round(x * 100)}%`);
  const bars = (t: T.Tally | undefined) => <HBars items={(t ?? []).map(([l, v]) => ({ label: l, value: v }))} />;
  return (
    <main className="main route-in">
      <div className="page-h">
        <span className="eyebrow">Field research</span>
        <h1 className="display">What the village says, and what the records show.</h1>
        <p className="lede">Six questions to take into the field. Each is answered from field visits and, side by side, from this organisation's own land cases. What a local branch asks for flows straight into every nearby case's lender checklist.</p>
      </div>
      {i ? (
        <div className="stack">
          <div className="money-band">
            <Tile i={0} n={i.observations} l="field visits" />
            <Tile i={1} n={i.q1_deceased_holder.households_surveyed} l="households surveyed" />
            <Tile i={2} n={i.q1_deceased_holder.land_cases} l="land cases compared" />
            <Tile i={3} n={i.q6_family_cost.case_median_inr} l="median family spend (cases)" fmt={inr} moon />
          </div>
          <div className="split">
            <div className="stack">
              <Panel title="1 · Land still in a deceased person's name" meta={`field ${pct(i.q1_deceased_holder.field_share)} · cases ${pct(i.q1_deceased_holder.case_share)}`}>
                <p className="small dim">{i.q1_deceased_holder.households_deceased_holder} of {i.q1_deceased_holder.households_surveyed} households surveyed. What stops them correcting it:</p>
                {bars(i.q1_deceased_holder.barriers)}
              </Panel>
              <Panel title="2 · What the local branch asks for">
                {Object.entries(i.q2_branch_asks).length ? Object.entries(i.q2_branch_asks).map(([k, v]) => <div key={k}><div className="eyebrow">{cat.data?.lenders[k]?.label ?? label(k)}</div>{bars(v)}</div>) : <p className="faint small">No branch visits yet.</p>}
              </Panel>
              <Panel title="3 · How the village decides what a plot is worth">{bars(i.q3_valuation_practices)}</Panel>
            </div>
            <div className="stack">
              <Panel title="4 · Where records disagree most" meta="field vs. cases">
                <HBars items={["names", "extents", "boundaries", "survey_numbers", "deceased_holder", "encumbrance", "land_class"].flatMap((k) => [
                  ...(i.q4_disagreements.field[k] ? [{ label: `${label(k)} · field`, value: i.q4_disagreements.field[k] }] : []),
                  ...(i.q4_disagreements.cases[k] ? [{ label: `${label(k)} · cases`, value: i.q4_disagreements.cases[k], cls: "green" }] : []),
                ])} />
              </Panel>
              <Panel title="5 · Who settles boundary disputes" meta={i.q5_disputes.median_days != null ? `median ${i.q5_disputes.median_days} days` : ""}>{bars(i.q5_disputes.settlers)}</Panel>
              <Panel title="6 · What a family spends to sort out a land issue">
                <table className="t"><tbody>
                  <tr><td>Field (typical)</td><td className="mono">{inr(i.q6_family_cost.field_median_inr)}</td><td className="mono">{i.q6_family_cost.field_median_days ?? "–"} days</td></tr>
                  <tr><td>Cases ({i.q6_family_cost.cases_with_costs} logged)</td><td className="mono">{inr(i.q6_family_cost.case_median_inr)}</td><td className="mono">{i.q6_family_cost.case_median_days ?? "–"} days</td></tr>
                </tbody></table>
              </Panel>
              <Panel title="Following the money" meta={`median reading fee ${inr(i.following_the_money.median_reading_fee_inr)}`}>
                <p className="small dim">Who families pay to read their own records:</p>
                {bars(i.following_the_money.record_readers)}
              </Panel>
            </div>
          </div>
        </div>
      ) : <p className="faint">Loading…</p>}
      <div className="split" style={{ marginTop: 18 }}>
        {ctx.can("case:write") ? (
          <Panel title="Record a field visit">
            <ErrorLine error={error} />
            <form className="form" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
              <div className="grid2">
                {txt("village", "Village")}{txt("taluk", "Taluk")}{txt("district", "District")}
                <label>State<select className="input" value={f.region_code} onChange={(e) => setF({ ...f, region_code: e.target.value })}>{(cat.data?.regions ?? []).map((r) => <option key={r.code} value={r.code}>{r.name}</option>)}</select></label>
                {txt("observed_on", "Date", "DD-MM-YYYY")}{txt("households_surveyed", "Households surveyed")}{txt("households_deceased_holder", "…with land in a deceased person's name")}
              </div>
              {txt("correction_barriers", "1 · What stops them correcting it (one per line)", "co-heirs in cities\ncost of legal heir certificate", true)}
              <div className="grid2">
                {txt("branch_name", "2 · Bank / co-op branch")}
                <label>Lender kind<select className="input" value={f.lender_kind} onChange={(e) => setF({ ...f, lender_kind: e.target.value })}><option value="">—</option>{Object.entries(cat.data?.lenders ?? {}).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}</select></label>
              </div>
              {txt("branch_requirements", "What the branch asks for before accepting land (one per line)", "13-year EC\npatta in borrower's name", true)}
              {txt("valuation_practices", "3 · How the village decides what a plot is worth", "guideline value\nbroker word of mouth", true)}
              <div className="grid2">{txt("names", "4 · Disagreements: names")}{txt("extents", "extents")}{txt("boundaries", "boundaries")}{txt("survey_numbers", "survey numbers")}</div>
              {txt("dispute_settlers", "5 · Who settles boundary disputes", "panchayat elders\nVAO\ncivil court", true)}
              <div className="grid2">{txt("dispute_days_typical", "…typical days to settle")}{txt("family_spend_inr_typical", "6 · Typical family spend (₹)")}{txt("family_days_typical", "…and days")}{txt("record_reading_fee_inr", "Fee to have records read (₹)")}</div>
              {txt("record_readers", "Who families pay to read their records", "document writer\nmiddleman", true)}
              {txt("notes", "Notes", "", true)}
              <button className="btn primary" disabled={busy}>Save visit</button>
            </form>
          </Panel>
        ) : <div />}
        <Panel title="Visits" meta={`${obs.data?.observations.length ?? 0}`}>
          {obs.data?.observations.length ? (
            <table className="t"><tbody>{obs.data.observations.map((o) => (
              <tr key={o.id}><td>{o.village}<div className="faint small">{o.observed_on} · {o.branch_name}</div></td><td className="small">{o.households_deceased_holder}/{o.households_surveyed} households</td>
                <td>{ctx.can("case:write") ? <button className="btn ghost small" onClick={async () => { if (await run(() => api.removeFieldObservation(o.id))) { void obs.refresh(); void ins.refresh(); } }}>×</button> : null}</td></tr>
            ))}</tbody></table>
          ) : <Empty title="No visits yet.">Spend time with the people who turn rural land away as collateral, and with those who charge families to read their own records.</Empty>}
        </Panel>
      </div>
    </main>
  );
}
