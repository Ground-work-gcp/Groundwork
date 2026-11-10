import type { Ctx } from "../../app/App";
import { usePoll } from "../../hooks";
import { api } from "../../services/api";
import { Timeline } from "../../components/Timeline";
import { ErrorLine, HBars, Panel, Tile, usd } from "../../components/ui";

export function Overview({ ctx }: { ctx: Ctx }) {
  const q = usePoll(api.overview, 15_000, [], true);
  const d = q.data;
  return (
    <main className="main route-in">
      <div className="page-h">
        <h1 className="display">
          Good to see you, <em>{ctx.me.full_name.split(" ")[0]}.</em>
        </h1>
        <p className="lede">Every open case, what is unresolved in it, and what is waiting for a decision.</p>
      </div>
      <ErrorLine error={q.error} />
      <div className="tally reveal in">
        <Tile i={0} n={d?.cases.open} l="open cases" />
        <Tile i={1} n={d ? d.findings_open.critical + d.findings_open.high : null} l="critical & high findings open" />
        <Tile i={2} n={d?.approvals_pending} l="actions awaiting approval" moon />
        <Tile i={3} n={d?.money.outstanding_cents} l="outstanding obligations" fmt={usd} />
        <Tile i={4} n={d?.money.unverified_cents} l="unverified — needs review" fmt={usd} moon />
      </div>
      <div className="split">
        <Panel title="Open cases by stage" meta={d ? `${d.cases.total} total · ${d.cases.resolved} resolved` : ""}>
          {d ? (
            <HBars items={d.stages.map((s) => ({ label: s.label, value: d.cases.by_stage[s.key] ?? 0, cls: s.key === "review" ? "amber" : s.key === "resolved" ? "green" : "" }))} />
          ) : (
            <p className="faint">Loading…</p>
          )}
          <div className="row" style={{ marginTop: 16 }}>
            <a className="btn primary" href="#cases/new">New case</a>
            <a className="btn" href="#findings">Review findings</a>
            <a className="btn ghost" href="#approvals">Approvals</a>
          </div>
        </Panel>
        <Panel title="Recent activity" meta="audit chain">
          <Timeline events={(d?.recent ?? []).map((e) => ({ ...e, actor_name: `${e.actor_name ?? e.actor_kind}${e.case_reference ? ` · ${e.case_reference}` : ""}` }))} />
        </Panel>
      </div>
    </main>
  );
}
