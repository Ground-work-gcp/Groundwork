import { usePoll } from "../../hooks";
import { api } from "../../services/api";
import { Empty, ErrorLine, StagePill } from "../../components/ui";

/* Every property under a case in this organisation, by its canonical identity. */

export function PropertiesPage() {
  const q = usePoll(api.properties, 60_000, [], true);
  const rows = q.data?.properties ?? [];
  return (
    <main className="main route-in">
      <div className="page-h">
        <h1 className="display">
          Properties, <em>by parcel.</em>
        </h1>
        <p className="lede">The identity each case asserts. Conflicting parcel numbers found in evidence appear as findings on the case.</p>
      </div>
      <ErrorLine error={q.error} />
      {q.data && !rows.length ? <Empty title="No properties yet." /> : null}
      <table className="t">
        <thead><tr><th>Address</th><th>County</th><th>Parcel</th><th>Case</th><th>Stage</th></tr></thead>
        <tbody>
          {rows.map((p) => (
            <tr key={p.id}>
              <td>{p.address_line || "—"}{p.city ? `, ${p.city}` : ""}</td>
              <td>{p.county}{p.state ? `, ${p.state}` : ""}</td>
              <td className="mono">{p.parcel_number || "—"}</td>
              <td><a href={`#case/${p.case.id}`}>{p.case.reference}</a> <span className="dim small">{p.case.title}</span></td>
              <td><StagePill stage={p.case.stage} status={p.case.status} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </main>
  );
}
