/* Mirrors backend/app/api/v1/serializers.py — the API contract. */

export interface User { id: string; username: string; full_name: string; email: string; role: string; is_active: boolean; is_platform_admin: boolean; organization_id: string; created_at: string; last_login_at: string | null }
export interface Me extends User { organization: { id: string; name: string }; permissions: string[] }
export interface Health { ok: boolean; service: string; version: string; environment: string; database: { ok: boolean; dialect?: string }; storage: string; extraction: string; ai: string; pii: string }

export interface Case { id: string; reference: string; kind?: string; title: string; description: string; stage: string; status: string; resolution_summary: string; assigned_to_id: string | null; detection_ran_at: string | null; created_at: string; updated_at: string; resolved_at: string | null }
export interface Property { id: string; case_id: string; address_line: string; city: string; county: string; state: string; postal_code: string; parcel_number: string; legal_description: string }
export interface CaseRow extends Case { property: Property | null; findings_open: number }
export interface PropertyRow extends Property { case: { id: string; reference: string; title: string; stage: string; status: string } }
export interface Party { id: string; name: string; party_type: string; roles: string[]; is_deceased: boolean; date_of_death: string | null; notes: string; source: string }
export interface Stage { key: string; label: string }
export interface Gate { stage: string; done: boolean; detail: string }
export interface MoneySummary { obligations_total_cents: number; confirmed_paid_cents: number; outstanding_cents: number; unverified_cents: number; obligation_count?: number }
export interface CaseDetail { case: Case; property: Property | null; parties: Party[]; stages: Stage[]; checklist: Gate[]; counts: Record<string, number>; financials: MoneySummary; land?: { region: string | null; district?: string; taluk?: string; village?: string; survey?: string; extent?: string; holder?: string } | null }

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

/* ── rural land records (India) ─────────────────────────────────────────── */
export interface LandParcel {
  id: string; region_code: string; district: string; taluk: string; village: string; survey_number: string; survey_key: string;
  extent_text: string; extent_sqm: number | null; land_class: string; patta_number: string; guideline_inr_per_sqm: number | null;
  road_access: boolean; irrigated: boolean; property_nature: string; personal_law: string; recorded_holder: string; notes: string;
}
export interface LandRecord {
  id: string; source_type: string; document_id: string | null; page_number: number | null; record_date: string | null; issuing_office: string;
  survey_number: string; survey_key: string; extent_text: string; extent_sqm: number | null; holders: string[];
  boundaries: Partial<Record<"north" | "east" | "south" | "west", string>>; land_class: string; extra: Record<string, unknown>; source: string; snippet: string;
}
export interface FamilyMember {
  id: string; name: string; sex: string; is_alive: boolean; date_of_death: string | null; parent_ids: string[]; spouse_ids: string[];
  is_recorded_holder: boolean; lives_in_village: boolean; location: string; consent_status: string; consent_note: string; consent_at: string | null;
  consent_link_active: boolean; consent_evidence: string; consent_document_id: string | null;
}
export interface Analysis<R = Record<string, unknown>> { id: string; kind: string; created_at: string; result_hash: string; inputs: Record<string, unknown>; result: R }
export interface LandIssue {
  type: string; severity: string; title: string; detail: string; office: string; fix_service: string; key: string;
  records: Array<{ record_id: string; source_type: string; label: string; document_id: string | null; page: number | null; date: string | null }>;
}
export interface Concordance {
  survey_key: string; records_compared: number; neighbour_records: number; issues: LandIssue[]; counts: Record<string, number>;
  columns: Array<{ record_id: string; label: string; source_type: string; document_id: string | null; date: string | null }>;
  rows: Array<{ attribute: string; cells: Array<{ record_id: string; value: string; status: string }> }>;
  boundary_edges: Array<{ direction: string; neighbour: string; status: string; detail: string }>; ec_years: number; deceased_holders: string[]; holders: string[];
}
export interface Share { member_id: string; name: string; share: string; value: number; alive: boolean; lives_in_village: boolean; consent_status: string; consent_evidence?: string }
export interface Succession {
  status: string; law?: string; law_name?: string; holder?: string; shares: Share[]; total?: string;
  steps: Array<{ date: string; person: string; held: string; rule: string; text: string }>; assumptions: string[]; warnings: string[];
  unresolved: Array<{ person: string; share?: string; reason: string }>; disclaimer: string; away_from_village?: string[];
}
export interface Valuation {
  status: string; reason?: string; p10_inr: number; p20_inr: number; p50_inr: number; p90_inr: number; confidence: string; effective_comparables: number;
  market_weight: number; prior: string; guideline_total_inr: number | null; lending_value_inr: number; title_haircut: number; loan_ceiling_inr: number; ltv: number;
  haircut_reasons: Array<{ kind: string; cut: number; why: string }>; warnings: string[]; method: string; range_ratio: number; claimed_value_inr?: number;
  comparables: Array<{ id: string; sale_date: string; survey_number: string; village: string; price_per_sqm: number; adjusted_per_sqm: number; weight: number; censored_at_guideline: boolean; outlier_downweighted?: boolean }>;
}
export interface Readiness {
  lender: string; lender_label: string; loan_amount_inr: number | null; collateral_free: boolean; score: number; verdict: string;
  requirements: Array<{ id: string; label: string; why: string; fix: string; status: string; detail: string; critical: boolean }>;
  plan: Array<{ service: string; name: string; office: string; fee_inr: number; days: number; start_day: number; finish_day: number; needs: string[]; documents: string[]; critical: boolean; unblocks: string[] }>;
  days_to_ready: number; official_fees_inr: number; local_branch_asks: string[]; notes: string[]; disclaimer: string;
}
export interface AgentRun {
  goal: string; mode: string; narrative: string; result_hash: string; proposed_actions: number[];
  steps: Array<{ n: number; tool: string; status: string; why: string; summary: string }>;
  handoffs: Array<{ who: string; what: string; where: string; why: string }>;
}
export interface Passport { id: string; code: string; sha256: string; status: string; created_at: string; payload: Record<string, unknown> }
export interface LandOverview {
  parcel: LandParcel | null; records: LandRecord[]; members: FamilyMember[]; passports: Passport[];
  latest: { concordance: Analysis<Concordance> | null; succession: Analysis<Succession> | null; valuation: Analysis<Valuation> | null; readiness: Analysis<Readiness> | null; agent_run: Analysis<AgentRun> | null };
}
export interface LandCatalog {
  regions: Array<{ code: string; name: string; language: string; units: string[]; records: Record<string, string>; offices: Record<string, string> }>;
  lenders: Record<string, { label: string; requirements: string[]; ltv: number; collateral_free_limit_inr: number }>;
  source_types: string[]; land_classes: string[]; cost_categories: string[]; payee_kinds: string[]; languages: string[]; glossary: Record<string, string>; note: string;
}
export interface CostEntry { id: string; entry_date: string; category: string; payee_kind: string; service: string; amount_inr: number; days_spent: number; has_receipt: boolean; notes: string; extra: Record<string, unknown> }
export interface CostSummary {
  total_inr: number; days: number; receipted_inr: number; unreceipted_inr: number; unreceipted_share: number; by_category: Record<string, number>;
  excess_over_official: Array<{ service: string; name: string; paid_inr: number; official_fee_inr: number; excess_inr: number }>; excess_total_inr: number;
  informal_loans: Array<{ id: string; principal_inr: number; annual_rate_pct: number; months: number; interest_paid_inr: number; formal_interest_inr: number; premium_inr: number; lender: string }>;
  informal_premium_inr: number; formal_rate_pct: number; paid_to_intermediaries_inr: number; note: string;
}
export interface Explanation { language: string; lines: string[]; shares: string[]; disclaimer: string; machine_translated: boolean; note?: string }
export interface FieldObservation {
  id: string; village: string; taluk: string; district: string; region_code: string; observed_on: string; households_surveyed: number; households_deceased_holder: number;
  correction_barriers: string[]; branch_name: string; lender_kind: string; branch_requirements: string[]; valuation_practices: string[];
  disagreement_counts: Record<string, number>; dispute_settlers: string[]; dispute_days_typical: number | null; family_spend_inr_typical: number | null;
  family_days_typical: number | null; record_readers: string[]; record_reading_fee_inr: number | null; notes: string;
}
export type Tally = Array<[string, number]>;
export interface LandInsights {
  observations: number; villages: string[];
  q1_deceased_holder: { field_share: number | null; households_surveyed: number; households_deceased_holder: number; case_share: number | null; land_cases: number; barriers: Tally };
  q2_branch_asks: Record<string, Tally>; q3_valuation_practices: Tally;
  q4_disagreements: { field: Record<string, number>; cases: Record<string, number> };
  q5_disputes: { settlers: Tally; median_days: number | null };
  q6_family_cost: { field_median_inr: number | null; field_median_days: number | null; case_median_inr: number | null; case_median_days: number | null; cases_with_costs: number };
  following_the_money: { record_readers: Tally; median_reading_fee_inr: number | null };
}
export interface Comparable { id: string; village: string; taluk: string; survey_number: string; sale_date: string; extent_sqm: number; consideration_inr: number; land_class: string; source: string; price_per_sqm: number | null; distance_km: number | null }
export interface ConsentView {
  name: string; plot: { survey_number: string; village: string; taluk: string; region: string }; recorded_holder: string; law: string | null;
  your_share: string | null; co_owners: Array<{ name: string; share: string }>; how: string[]; status: string; letter: string; disclaimer: string; what_consent_means: string;
}

export interface ProceedingHealth {
  status_label: string; flow: Array<{ status: string; label: string; reached: boolean }>; elapsed_days: number; sla_days: number; due_on: string | null;
  days_left: number | null; last_event_days: number | null; progress: number; signal: string; waiting_on: string; overdue_days?: number;
  objection_days_left?: number; objection?: boolean;
}
export interface ProceedingEvent { id: string; event_date: string; kind: string; note: string; document_id: string | null; status_after: string; created_at: string }
export interface Proceeding {
  id: string; kind: string; label: string; office: string; application_no: string; status: string; filed_on: string | null; sla_days: number; due_on: string | null;
  objection_until: string | null; hearing_on: string | null; decided_on: string | null; outcome: string; parent_id: string | null; escalation_level: number;
  applicants: string[]; notes: string; events: ProceedingEvent[]; health: ProceedingHealth;
}
export interface ProceedingsPayload {
  proceedings: Proceeding[]; kinds: Record<string, { label: string; flow: string[]; office: string; sla_days: number }>; status_labels: Record<string, string>;
  event_kinds: string[]; region: { code: string; objection_days: number; appellate_office: string; collector: string; grievance_portal: string; rts_law: string; online: string }; note: string;
}
export interface RouteStep { id: string; name: string; office: string; days: number; fee_inr: number; needs: string[]; start_day: number; finish_day: number; critical: boolean }
export interface Route {
  id: string; label: string; summary: string; steps: RouteStep[]; days: number; cost_inr: number; duty_inr: number; signatures: string[]; away_signatures: string[];
  borrower: string; capacity_inr: number; per_heir: Array<{ name: string; capacity_inr: number; share?: string; area_sqm?: number; below_min_plot?: boolean }>;
  value_transfers: Array<{ from: string; share: string; market_value_inr: number; guideline_value_inr: number; stamp_inr: number; registration_inr: number }>;
  fair_settlement_inr?: number; risks: string[]; score?: number; score_parts?: Record<string, number>; recommended?: boolean; bridge?: boolean;
}
export interface Pathways {
  status: string; reason?: string; routes: Route[]; weights: Record<string, number>; beneficiary: { name: string; why: string } | null;
  lending_value_after_fix_inr: number; first_rupee_days: number; assumptions: string[]; disclaimer: string;
}
export interface Journey { stage: string; status: string; stages: Stage[]; checklist: Gate[]; proceedings: Proceeding[]; next_action: { node: string; label: string; step: string; href: string; remaining: number } | null; critical_path: string[]; needs_you: NeedsItem[] }
