import { useState } from "react";
import { usePoll } from "../../hooks";
import { go } from "../../navigation/routes";
import { api } from "../../services/api";
import type { Fact } from "../../types";
import { Empty, ErrorLine, Panel, StatusPill, label, useAction } from "../../components/ui";
import type { CaseCtx } from "../cases/CaseView";

/* Stage 2 · evidence ingestion. InHeir's document upload and extraction, now with page-level
   provenance for every fact and a review step for each one. */

const DOC_TYPES = ["", "deed", "will", "probate", "death_certificate", "tax_record", "lien", "mortgage", "financial_statement", "other"];

export function EvidenceTab({ c, docId }: { c: CaseCtx; docId?: string }) {
  const docs = usePoll(() => api.documents(c.caseId), 30_000, [c.caseId], true);
  const [docType, setDocType] = useState("");
  const [over, setOver] = useState(false);
  const { busy, error, run } = useAction(c.toast);
  const canWrite = c.can("evidence:write");

  const send = async (files: FileList | null) => {
    if (!files) return;
    for (const file of Array.from(files)) {
      const r = await run(() => api.uploadDocument(c.caseId, file, docType || undefined), `${file.name}: stored and extracted`);
      if (r) go(`case/${c.caseId}/evidence/${r.document.id}`);
    }
    void docs.refresh();
    c.refresh();
  };

  const list = docs.data?.documents ?? [];
  const selected = docId ?? list[0]?.id;
  return (
    <div className="split">
      <div className="stack">
        {canWrite ? (
          <div
            className={`dropzone${over ? " over" : ""}`}
            onDragOver={(e) => { e.preventDefault(); setOver(true); }}
            onDragLeave={() => setOver(false)}
            onDrop={(e) => { e.preventDefault(); setOver(false); void send(e.dataTransfer.files); }}
          >
            <p className="dim">Drop deeds, wills, death records, tax bills or liens (PDF, text, images)</p>
            <div className="row" style={{ justifyContent: "center" }}>
              <select className="input" value={docType} onChange={(e) => setDocType(e.target.value)} aria-label="Document type">
                {DOC_TYPES.map((t) => <option key={t} value={t}>{t ? label(t) : "detect type"}</option>)}
              </select>
              <label className="btn primary">
                {busy ? "Extracting…" : "Choose files"}
                <input type="file" multiple hidden accept=".pdf,.txt,.md,.png,.jpg,.jpeg,.tif,.tiff" onChange={(e) => void send(e.target.files)} />
              </label>
            </div>
            <ErrorLine error={error} />
          </div>
        ) : null}
        {list.length === 0 && !docs.loading ? <Empty title="No evidence yet.">Upload the first record to begin.</Empty> : null}
        <div className="doclist">
          {list.map((d) => (
            <button key={d.id} type="button" className={`doc${d.id === selected ? " on" : ""}`} onClick={() => go(`case/${c.caseId}/evidence/${d.id}`)}>
              <div className="row">
                <span className="fn">{d.filename}</span>
                <span style={{ marginLeft: "auto" }}><StatusPill status={d.status} /></span>
              </div>
              <div className="faint small">
                {label(d.doc_type)} · {d.page_count} page(s) · {d.fact_count ?? 0} fact(s) · {d.extraction_engine}
                {d.pii_summary?.total ? ` · ${d.pii_summary.total} PII item(s)` : ""}
              </div>
            </button>
          ))}
        </div>
      </div>
      {selected ? <DocumentDetail c={c} docId={selected} onChange={() => void docs.refresh()} /> : <div />}
    </div>
  );
}

function DocumentDetail({ c, docId, onChange }: { c: CaseCtx; docId: string; onChange: () => void }) {
  const q = usePoll(() => api.document(docId), 60_000, [docId], true);
  const [page, setPage] = useState(1);
  const { busy, error, run } = useAction(c.toast);
  const d = q.data;
  if (!d) return <Panel title="Document"><p className="faint">{q.error ?? "Loading…"}</p></Panel>;
  const pageText = d.pages.find((p) => p.page_number === page)?.text ?? "";
  const review = async (f: Fact, status: string) => {
    let corrected: string | undefined;
    if (status === "corrected") {
      corrected = window.prompt(`Correct ${label(f.fact_type)}`, f.effective_value) ?? undefined;
      if (!corrected) return;
    }
    if (await run(() => api.reviewFact(f.id, { status, corrected_value: corrected }), `Fact ${status}`)) {
      void q.refresh();
      onChange();
    }
  };
  return (
    <div className="stack">
      <Panel
        title={d.document.filename}
        meta={`sha256 ${d.document.sha256.slice(0, 12)}…`}
        actions={
          <>
            <a className="btn ghost small" href={api.documentUrl(docId)}>Download</a>
            {c.can("evidence:write") ? <button className="btn ghost small" disabled={busy} onClick={async () => { if (await run(() => api.reprocess(docId), "Re-extracted")) void q.refresh(); }}>Re-extract</button> : null}
          </>
        }
      >
        <dl className="kv">
          <dt>type</dt><dd>{label(d.document.doc_type)} ({d.document.doc_type_source})</dd>
          <dt>extraction</dt><dd>{d.document.extraction_engine} · {d.document.status}{d.document.extraction_error ? ` — ${d.document.extraction_error}` : ""}</dd>
          <dt>AI extraction</dt><dd>{d.document.ai_extraction?.status ?? "—"}{d.document.ai_extraction?.accepted != null ? ` · ${d.document.ai_extraction.accepted} accepted, ${d.document.ai_extraction.rejected?.length ?? 0} rejected by the grounding gate` : ""}</dd>
          <dt>PII</dt><dd>{d.document.pii_summary?.total ? Object.entries(d.document.pii_summary.by_type ?? {}).map(([k, v]) => `${label(k.toLowerCase())} ×${v}`).join(", ") : "none detected"} · {d.document.pii_summary?.engine}</dd>
        </dl>
        <ErrorLine error={error} />
      </Panel>
      <Panel title="Extracted facts" meta={`${d.facts.length}`}>
        <table className="t">
          <thead><tr><th>Fact</th><th>Value</th><th>Source</th><th>Status</th><th /></tr></thead>
          <tbody>
            {d.facts.map((f) => (
              <tr key={f.id}>
                <td className="nowrap">{label(f.fact_type)}</td>
                <td>
                  <b>{f.effective_value === f.normalized_value || f.status !== "corrected" ? f.value : f.effective_value}</b>
                  {f.status === "corrected" ? <div className="faint small">extracted as “{f.value}”</div> : null}
                </td>
                <td className="small">
                  <button type="button" className="btn ghost small" onClick={() => setPage(f.page_number ?? 1)}>p. {f.page_number ?? "–"}</button>
                  <span className="faint">{Math.round(f.confidence * 100)}% · {f.extractor}</span>
                  <div className="faint small" title={f.snippet}>“{f.snippet.slice(0, 90)}”</div>
                </td>
                <td><StatusPill status={f.status} /></td>
                <td className="nowrap">
                  {c.can("evidence:write") ? (
                    <>
                      <button className="btn ghost small" onClick={() => void review(f, "confirmed")}>Confirm</button>
                      <button className="btn ghost small" onClick={() => void review(f, "corrected")}>Correct</button>
                      <button className="btn ghost small danger" onClick={() => void review(f, "rejected")}>Reject</button>
                    </>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
      <Panel title={`Page ${page}`} meta={`${d.pages.length} page(s)`} actions={d.pages.length > 1 ? (
        <span className="row">{d.pages.map((p) => <button key={p.page_number} className={`chip-btn${p.page_number === page ? " on" : ""}`} onClick={() => setPage(p.page_number)}>{p.page_number}</button>)}</span>
      ) : undefined}>
        <pre className="pagetext">{pageText || "No text on this page."}</pre>
      </Panel>
    </div>
  );
}
