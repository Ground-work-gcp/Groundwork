/* The one API client. Every screen talks to the single Groundwork backend through here
   (Beacon's request/ApiError/timeout pattern, now same-origin with a session cookie). */
import type * as T from "../types";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

const BASE = "/api/v1";
const TIMEOUT_MS = 30_000;

async function call<R>(method: string, path: string, body?: unknown, isForm = false): Promise<R> {
  const headers: Record<string, string> = { "x-groundwork-client": "web" };
  if (body !== undefined && !isForm) headers["content-type"] = "application/json";
  const ctl = new AbortController();
  const timer = window.setTimeout(() => ctl.abort(), TIMEOUT_MS);
  let resp: Response;
  try {
    resp = await fetch(BASE + path, {
      method,
      headers,
      credentials: "same-origin",
      signal: ctl.signal,
      body: body === undefined ? undefined : isForm ? (body as FormData) : JSON.stringify(body),
    });
  } catch (e) {
    throw new ApiError(0, ctl.signal.aborted ? "the server took too long to answer" : e instanceof Error ? e.message : String(e));
  } finally {
    window.clearTimeout(timer);
  }
  const raw = await resp.text();
  let parsed: unknown = raw;
  try {
    parsed = raw ? JSON.parse(raw) : null;
  } catch {
    /* text responses (reports, CSV) */
  }
  if (!resp.ok) {
    const msg = (parsed as { error?: string } | null)?.error ?? `${resp.status} ${resp.statusText}`;
    if (resp.status === 401) window.dispatchEvent(new CustomEvent("gw:signed-out"));
    throw new ApiError(resp.status, typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return parsed as R;
}

const get = <R>(path: string) => call<R>("GET", path);
const text = (path: string) => call<string>("GET", path);
const post = <R>(path: string, body: unknown = {}) => call<R>("POST", path, body);
const patch = <R>(path: string, body: unknown) => call<R>("PATCH", path, body);
const del = <R>(path: string) => call<R>("DELETE", path);
const upload = <R>(path: string, form: FormData) => call<R>("POST", path, form, true);

const q = (params: Record<string, string | undefined | null>) => {
  const s = new URLSearchParams(Object.entries(params).filter(([, v]) => v) as [string, string][]).toString();
  return s ? `?${s}` : "";
};

export const api = {
  health: () => get<T.Health>(`/health`),
  // auth
  me: () => get<{ user: T.Me }>(`/auth/me`),
  signIn: (username: string, password: string) => post<{ user: T.Me }>(`/auth/sign-in`, { username, password }),
  signUp: (body: { organization: string; username: string; full_name: string; email: string; password: string }) => post<{ user: T.Me }>(`/auth/sign-up`, body),
  signOut: () => post<{ ok: boolean }>(`/auth/sign-out`),
  usernameAvailable: (u: string) => get<{ available: boolean }>(`/auth/username/${encodeURIComponent(u)}/available`),
  // overview, cases
  overview: () => get<T.Overview>(`/overview`),
  cases: (params: { status?: string; stage?: string; q?: string } = {}) => get<{ cases: T.CaseRow[] }>(`/cases${q(params)}`),
  createCase: (body: unknown) => post<T.CaseDetail>(`/cases`, body),
  caseDetail: (id: string) => get<T.CaseDetail>(`/cases/${id}`),
  updateCase: (id: string, body: unknown) => patch<T.CaseDetail>(`/cases/${id}`, body),
  moveStage: (id: string, to_stage: string) => post<T.CaseDetail>(`/cases/${id}/stage`, { to_stage }),
  timeline: (id: string) => get<{ events: T.AuditEvent[] }>(`/cases/${id}/timeline`),
  report: (id: string) => text(`/cases/${id}/report`),
  questions: (id: string) => get<{ questions: T.Question[] }>(`/cases/${id}/questions`),
  ask: (id: string, question: string) => post<{ question: T.Question }>(`/cases/${id}/questions`, { question }),
  addParty: (id: string, body: unknown) => post<{ party: T.Party }>(`/cases/${id}/parties`, body),
  updateParty: (partyId: string, body: unknown) => patch<{ party: T.Party }>(`/parties/${partyId}`, body),
  removeParty: (partyId: string) => del<{ ok: boolean }>(`/parties/${partyId}`),
  // referrals (intake)
  referrals: () => get<{ referrals: T.Referral[] }>(`/referrals`),
  addReferral: (body: unknown) => post<{ referral: T.Referral }>(`/referrals`, body),
  judgeReferral: (id: string, verdict: "verify" | "unverify" | "convert", reason = "") => post<{ referral: T.Referral }>(`/referrals/${id}/${verdict}`, { reason }),
  // properties
  properties: () => get<{ properties: T.PropertyRow[] }>(`/properties`),
  updateProperty: (id: string, body: unknown) => patch<{ property: T.Property }>(`/properties/${id}`, body),
  // evidence
  documents: (id: string) => get<{ documents: T.Doc[] }>(`/cases/${id}/documents`),
  uploadDocument: (id: string, file: File, docType?: string) => {
    const form = new FormData();
    form.append("file", file);
    if (docType) form.append("doc_type", docType);
    return upload<{ document: T.Doc }>(`/cases/${id}/documents`, form);
  },
  document: (docId: string) => get<{ document: T.Doc; facts: T.Fact[]; pages: T.Page[] }>(`/documents/${docId}`),
  documentUrl: (docId: string) => `${BASE}/documents/${docId}/content`,
  reprocess: (docId: string) => post<{ document: T.Doc }>(`/documents/${docId}/reprocess`),
  facts: (id: string, params: { document_id?: string; fact_type?: string; status?: string } = {}) => get<{ facts: T.Fact[]; fact_types: string[] }>(`/cases/${id}/facts${q(params)}`),
  addFact: (id: string, body: unknown) => post<{ fact: T.Fact }>(`/cases/${id}/facts`, body),
  reviewFact: (factId: string, body: { status: string; corrected_value?: string; review_note?: string }) => patch<{ fact: T.Fact }>(`/facts/${factId}`, body),
  search: (id: string, query: string) => get<{ hits: T.Hit[] }>(`/cases/${id}/search${q({ q: query })}`),
  // ownership
  ownership: (id: string) => get<T.Ownership>(`/cases/${id}/ownership`),
  reconstruct: (id: string) => post<T.Ownership>(`/cases/${id}/ownership/reconstruct`),
  addEvent: (id: string, body: unknown) => post<T.Ownership>(`/cases/${id}/ownership/events`, body),
  removeEvent: (eventId: string) => del<T.Ownership>(`/ownership/events/${eventId}`),
  // financials & reconciliation
  financials: (id: string) => get<T.Financials>(`/cases/${id}/financials`),
  addObligation: (id: string, body: unknown) => post<{ obligation: T.Obligation }>(`/cases/${id}/obligations`, body),
  deriveObligations: (id: string) => post<{ created: T.Obligation[] }>(`/cases/${id}/obligations/derive`),
  updateObligation: (obId: string, body: unknown) => patch<{ obligation: T.Obligation }>(`/obligations/${obId}`, body),
  removeObligation: (obId: string) => del<{ ok: boolean }>(`/obligations/${obId}`),
  addTransaction: (id: string, body: unknown) => post<{ transaction: T.Txn }>(`/cases/${id}/transactions`, body),
  removeTransaction: (txnId: string) => del<{ ok: boolean }>(`/transactions/${txnId}`),
  importTransactions: (id: string, file: File, dateFormat: string, payer: string) => {
    const form = new FormData();
    form.append("file", file);
    form.append("date_format", dateFormat);
    form.append("payer", payer);
    return upload<T.ImportResult>(`/cases/${id}/transactions/import`, form);
  },
  runReconciliation: (id: string) => post<{ run: T.Run; results: T.ReconResult[] }>(`/cases/${id}/reconciliation/runs`),
  runs: (id: string) => get<{ runs: T.Run[] }>(`/cases/${id}/reconciliation/runs`),
  run: (runId: string) => get<{ run: T.Run; results: T.ReconResult[]; summary?: T.MoneySummary }>(`/reconciliation/runs/${runId}`),
  review: (runId: string, command: T.QueueCommand) => post<{ label: string; inverse: T.QueueCommand; results: T.ReconResult[] }>(`/reconciliation/runs/${runId}/review`, command),
  tolerances: () => get<Record<string, Record<string, unknown>>>(`/reconciliation/tolerances`),
  // findings
  detect: (id: string) => post<{ outcome: Record<string, number>; findings: T.Finding[] }>(`/cases/${id}/risks/detect`),
  caseFindings: (id: string) => get<{ findings: T.Finding[] }>(`/cases/${id}/risks`),
  findings: (status = "open", severity?: string) => get<{ findings: T.Finding[] }>(`/risks${q({ status, severity })}`),
  reviewFinding: (fid: string, status: string, note: string) => patch<{ finding: T.Finding }>(`/risks/${fid}`, { status, note }),
  // workflow
  stages: () => get<{ stages: T.Stage[] }>(`/workflows/stages`),
  actionCatalog: () => get<{ actions: T.ActionSpec[] }>(`/workflows/actions`),
  workflow: (id: string) => get<T.Workflow>(`/cases/${id}/workflow`),
  addTask: (id: string, body: unknown) => post<{ task: T.Task }>(`/cases/${id}/tasks`, body),
  updateTask: (taskId: string, body: unknown) => patch<{ task: T.Task }>(`/tasks/${taskId}`, body),
  comments: (id: string) => get<{ comments: T.Comment[] }>(`/cases/${id}/comments`),
  addComment: (id: string, body: string) => post<{ comment: T.Comment }>(`/cases/${id}/comments`, { body }),
  addDecision: (id: string, body: unknown) => post<{ decision: T.Decision }>(`/cases/${id}/decisions`, body),
  propose: (id: string, action: string, params: Record<string, unknown>, rationale = "") => post<{ action: T.Action }>(`/cases/${id}/actions`, { action, params, rationale }),
  proposeSuggested: (id: string) => post<{ created: T.Action[] }>(`/cases/${id}/actions/suggested`),
  // approvals
  approvals: (status = "proposed") => get<{ actions: T.Action[] }>(`/approvals${q({ status })}`),
  approve: (actionId: string, confirmation: string) => post<{ action: T.Action }>(`/approvals/${actionId}/approve`, { confirmation }),
  reject: (actionId: string, reason: string) => post<{ action: T.Action }>(`/approvals/${actionId}/reject`, { reason }),
  // audit
  audit: (caseId?: string) => get<{ events: T.AuditEvent[] }>(`/audit${q({ case_id: caseId })}`),
  auditCsvUrl: (caseId?: string) => `${BASE}/audit/export.csv${q({ case_id: caseId })}`,
  verifyAudit: () => get<{ ok: boolean; count: number; broken_at?: number; reason?: string }>(`/audit/verify`),
  // settings
  users: () => get<{ users: T.User[]; roles: string[] }>(`/settings/users`),
  addUser: (body: unknown) => post<{ user: T.User }>(`/settings/users`, body),
  updateUser: (userId: string, body: unknown) => patch<{ user: T.User }>(`/settings/users/${userId}`, body),
  integrations: () => get<Record<string, Record<string, unknown>>>(`/settings/integrations`),
  safety: () => get<T.Safety>(`/admin/safety`),
  // the centralized workflow graph, the assistant and memory
  flow: () => get<T.Flow>(`/flow`),
  caseFlow: (id: string) => get<T.Flow>(`/cases/${id}/flow`),
  assistant: (message: string, caseId?: string) => post<T.AssistantReply>(`/assistant`, { message, case_id: caseId ?? null }),
  memory: () => get<T.MemoryGraph>(`/memory/graph`),
};
