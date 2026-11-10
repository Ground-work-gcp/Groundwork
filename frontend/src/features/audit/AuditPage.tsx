import { usePoll } from "../../hooks";
import { api } from "../../services/api";
import { ErrorLine, Panel } from "../../components/ui";
import { Timeline } from "../../components/Timeline";

/* Stage 8 · the verified history across the organisation (Beacon's audit page + CSV). */

export function AuditPage() {
  const log = usePoll(() => api.audit(), 20_000, [], true);
  const chain = usePoll(api.verifyAudit, 60_000, [], true);
  return (
    <main className="main route-in">
      <div className="page-h">
        <h1 className="display">
          Every step, <em>verifiable.</em>
        </h1>
        <p className="lede">Each event is hash-chained to the one before it. Editing or deleting any record breaks the chain at that record.</p>
      </div>
      <div className="row">
        {chain.data ? (
          <span className={`pill ${chain.data.ok ? "green" : "red"}`}>
            {chain.data.ok ? `chain intact · ${chain.data.count} events` : `chain broken at #${chain.data.broken_at} (${chain.data.reason})`}
          </span>
        ) : null}
        <a className="btn" href={api.auditCsvUrl()}>Export CSV</a>
      </div>
      <ErrorLine error={log.error ?? chain.error} />
      <Panel title="Organisation history" meta={`${log.data?.events.length ?? 0} most recent`}>
        <Timeline events={(log.data?.events ?? []).map((e) => ({ ...e, actor_name: `${e.actor_name ?? e.actor_kind}${e.case_reference ? ` · ${e.case_reference}` : ""}` }))} />
      </Panel>
    </main>
  );
}
