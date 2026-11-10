/* Mirrors backend/app/api/v1/serializers.py — the API contract. */

export interface User { id: string; username: string; full_name: string; email: string; role: string; is_active: boolean; is_platform_admin: boolean; organization_id: string; created_at: string; last_login_at: string | null }
export interface Me extends User { organization: { id: string; name: string }; permissions: string[] }
export interface Health { ok: boolean; service: string; version: string; environment: string; database: { ok: boolean; dialect?: string }; storage: string; extraction: string; ai: string; pii: string }

export interface Case { id: string; reference: string; title: string; description: string; stage: string; status: string; resolution_summary: string; assigned_to_id: string | null; detection_ran_at: string | null; created_at: string; updated_at: string; resolved_at: string | null }
export interface Property { id: string; case_id: string; address_line: string; city: string; county: string; state: string; postal_code: string; parcel_number: string; legal_description: string }
export interface CaseRow extends Case { property: Property | null; findings_open: number }
export interface PropertyRow extends Property { case: { id: string; reference: string; title: string; stage: string; status: string } }
export interface Party { id: string; name: string; party_type: string; roles: string[]; is_deceased: boolean; date_of_death: string | null; notes: string; source: string }
export interface Stage { key: string; label: string }
export interface Gate { stage: string; done: boolean; detail: string }
export interface MoneySummary { obligations_total_cents: number; confirmed_paid_cents: number; outstanding_cents: number; unverified_cents: number; obligation_count?: number }
export interface CaseDetail { case: Case; property: Property | null; parties: Party[]; stages: Stage[]; checklist: Gate[]; counts: Record<string, number>; financials: MoneySummary }

export interface Doc { id: string; filename: string; content_type: string; size_bytes: number; sha256: string; storage_uri: string; doc_type: string; doc_type_source: string; status: string; page_count: number; extraction_engine: string; extraction_error: string; pii_summary: { engine?: string; total?: number; by_type?: Record<string, number> }; ai_extraction: { status?: string; accepted?: number; rejected?: unknown[] }; created_at: string; fact_count?: number }
export interface Page { page_number: number; text: string; confidence: number | null }
export interface Fact { id: string; document_id: string | null; page_number: number | null; fact_type: string; value: string; normalized_value: string; effective_value: string; snippet: string; confidence: number; extractor: string; status: string; corrected_value: string | null; review_note: string }
export interface Hit { document_id: string; filename: string; page: number; text: string; score: number }

export interface OwnershipEvent { id: string; event_type: string; event_date: string | null; grantors: string[]; grantees: string[]; instrument: string; document_id: string | null; page_number: number | null; fact_ids: string[]; confidence: number; source: string; notes: string; chain_status: string; chain_notes: string[] }
export interface Ownership { events: OwnershipEvent[]; current_owners: string[]; deceased_current_owners: string[]; issues: Array<{ kind: string; message: string; event_id?: string }> }

export interface Obligation { id: string; obligation_type: string; creditor: string; debtor_name: string; reference: string; tax_year: number | null; amount_cents: number; assessed_date: string | null; due_date: string | null; status: string; document_id: string | null; page_number: number | null; fact_ids: string[]; source: string }
export interface Txn { id: string; txn_date: string; description: string; reference: string; amount_cents: number; payer: string; source: string }
export interface Financials { obligations: Obligation[]; transactions: Txn[]; summary: MoneySummary; latest_run_id: string | null }
export interface ImportResult { imported: number; rejections: Array<{ line: number; reason: string; detail: string; raw: string }>; import_batch: string }
export interface Run { id: string; case_id: string | null; kind: string; stats: { byOutcome?: Record<string, number> } & Record<string, unknown>; tolerances: Record<string, unknown>; summary: Record<string, unknown>; created_at: string }
export interface ReconResult { id: string; seq: number; lane: string; tier: string; rule: string; outcome: "AUTO_MATCHED" | "PROPOSED" | "EXCEPTION"; confidence: number; left: string[]; right: string[]; evidence: string[]; class: string | null; amount_cents: number; review_status: string }
export type QueueCommand = { type: "Resolve"; ids: string[]; to: "accepted" | "rejected"; scope?: string } | { type: "Restore"; states: Record<string, string>; scope?: string };

export interface EvidenceRef { document_id: string | null; filename: string | null; page: number | null; fact_id: string | null; snippet: string; label: string; confidence?: number; reviewed?: boolean }
export interface Finding { id: string; case_id: string; case_reference?: string; finding_type: string; severity: string; title: string; description: string; evidence: EvidenceRef[]; confidence: number; uncertainty: string; requires_professional_review: boolean; status: string; status_note: string; source: string; detected_at: string }

export interface Task { id: string; title: string; description: string; kind: string; status: string; assignee_id: string | null; due_date: string | null; created_at: string }
export interface Comment { id: string; body: string; author_id: string; author_name?: string; created_at: string }
export interface Decision { id: string; title: string; decision: string; rationale: string; decided_by_id: string; created_at: string }
export interface Action { id: string; case_id: string; case_reference?: string; case_title?: string; seq: number; action: string; params: Record<string, unknown>; rationale: string; summary: string | null; proposed_by_id: string | null; proposed_by_kind: string; proposed_by_name?: string | null; status: string; confirmation_phrase: string; expires_at: string; result: Record<string, unknown>; created_at: string; approver_roles?: string[]; reversible?: boolean | null; decision?: { decision: string; by: string; statement: string; at: string } | null }
export interface ActionSpec { id: string; description: string; params: Record<string, string>; approver_roles: string[]; reversible: boolean }
export interface Suggestion { action: string; params: Record<string, unknown>; rationale: string; finding_id: string }
export interface Workflow { stage: string; status: string; stages: Stage[]; checklist: Gate[]; tasks: Task[]; decisions: Decision[]; actions: Action[]; suggestions: Suggestion[] }

export interface Question { id: string; question: string; answer: string; citations: Array<{ ref: string; document_id: string; filename: string; page: number; text: string }>; mode: string; verified: boolean; created_at: string }
export interface Referral { id: string; full_name: string; email: string; address: string; description: string; verdict: string; reason: string; case_id: string | null; created_at: string }
export interface AuditEvent { id: string; seq: number; case_id: string | null; case_reference?: string; actor_id: string | null; actor_name: string | null; actor_kind: string; event: string; entity_type: string; entity_id: string; detail: Record<string, unknown>; occurred_at: string; hash: string }
export interface Overview { cases: { total: number; open: number; resolved: number; closed: number; by_stage: Record<string, number> }; findings_open: Record<string, number>; approvals_pending: number; money: MoneySummary; recent: AuditEvent[]; stages: Stage[] }
/* The centralized workflow graph (backend/app/domain/flow) — every value computed live. */
export type FlowStatus = "done" | "attention" | "blocked" | "idle";
export interface FlowNode { id: string; layer: "evidence" | "finance" | "analysis" | "control" | "system"; col: number; row: number; label: string; metric: number; unit: string; remaining: number; sub: string; stats: Record<string, number | string>; status: FlowStatus; href: string }
export interface FlowEdge { from: string; to: string; label: string; critical: boolean; shortest: boolean; alt: boolean; audit?: boolean }
export interface NeedsItem { kind: string; case_id: string; case_reference: string; title: string; suggests: string; severity: string; amount_cents: number; href: string; created_at: string; count: number; priority: number; id?: string }
export interface NextAction { node: string; label: string; remaining: number; step: string; href: string }
export interface Flow {
  nodes: FlowNode[]; edges: FlowEdge[]; critical_path: string[]; shortest_path: { cost: number | null; nodes: string[] }; remaining_work: number;
  next_action: NextAction | null; needs_you: NeedsItem[]; algorithms: Record<string, string>; generated_at: string;
  scope: { kind: "case" | "organization"; id: string; reference?: string; title?: string; open_cases?: number };
  cases?: Array<{ id: string; reference: string; title: string; stage: string; remaining: number; next_action: NextAction | null }>;
}
export interface MemoryNode { id: string; kind: string; label: string; score: number; href: string | null }
export interface MemorySuggestion { text: string; href?: string; prompt?: string; reason: string }
export interface MemoryGraph { nodes: MemoryNode[]; edges: Array<{ from: string; to: string; weight: number }>; activity: Record<string, number>; by_month: Record<string, number>; topics: string[]; events_considered: number; questions_considered: number; algorithm: string; suggestions: MemorySuggestion[] }
export interface Citation { ref: string; document_id: string; filename: string; page: number; text: string; evidence_status?: string }
export interface AssistantReply {
  reply: string; intent: string; mode: string; verified?: boolean; items: Array<{ title: string; sub?: string; href: string | null; severity?: string; stage?: string }>;
  citations: Citation[]; suggestions: MemorySuggestion[]; memory: { nodes: MemoryNode[]; topics: string[]; algorithm: string };
}

export interface Safety { execution_enabled: boolean; separate_approver: boolean; action_ttl_days: number; allowlist: ActionSpec[]; controls: Array<{ id: string; title: string; rule: string; file: string; test: string }>; never: string[] }
