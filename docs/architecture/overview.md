# Architecture

## Decisions

| Decision | Choice | Why |
|---|---|---|
| Backend host | **FastAPI modular monolith** (Python) | InHeir was FastAPI; Beacon's workflow code was Python; Magpie's only irreplaceable logic (the matcher) is a pure function — ported with result-for-result regression tests rather than keeping a second Node server. |
| Frontend | **Beacon's Vite + React 18 app** | Required foundation. Theme, chrome, motion, hooks, hash routing, timeline and FAQ/safety patterns retained; InHeir/Magpie features rebuilt inside it. |
| Database | **PostgreSQL (Cloud SQL)**, SQLite for zero-setup local dev | One relational model replaces InHeir's MongoDB, Beacon's DynamoDB and Magpie's Prisma schema. Alembic migrations; suite passes on PostgreSQL 16 and SQLite. |
| Files | **Cloud Storage** (local directory in dev) | Rows reference objects by URI; every object is stored under its SHA-256 and re-verified on download. |
| Retrieval | **BM25 over the case's own pages, computed on demand** | A case corpus is tens of pages and permissions are per case; a separate index (Azure AI Search / Vertex AI Search) would add drift between index and permissions. Vertex AI Search is the documented path if an org-wide legal knowledge base is added. |
| Events | **No Pub/Sub / Eventarc / Workflows** | Every workflow step is synchronous and short; nothing needed a queue. Adding them would be infrastructure without function. |

## Modules (`backend/app`)

```
api/v1/            one router per area, one /api/v1 surface (78 operations), one error shape {"error": …}
core/              settings · bcrypt + JWT (cookie or bearer) · role → permission map
repositories/      tenant-scoped access: every case-owned row is read through its organisation
domain/
  cases/           intake, stage moves, organisation overview
  evidence/        normalisation of names, dates, money, parcels, addresses (shared by all modules)
  ownership/       chain-of-title reconstruction and analysis
  financials/      obligations derived from evidence; CSV payment import (Magpie rejection-as-record)
  reconciliation/  engine/ = Magpie's matcher, ported; property_lane.py = obligations ↔ payments; service.py
  risk/            discrepancy detection with evidence references and idempotent fingerprints
  workflows/       the 8 stages, gates, and the allowlisted action registry (Beacon pattern)
  approvals/       propose / approve-by-phrase / reject; single use; kill switch
  audit/           hash-chained append-only history and verification
services/          extraction (local | Document AI, + Gemini behind a grounding gate) · search · rag · ai · pii · reporting
infrastructure/    database · storage · google_cloud (Document AI, DLP, Gemini, GCS) · webhook notifications
```

Dependency direction: `api → domain/services → repositories/infrastructure`. Ownership never
imports financial code; risk reads both through their public functions; workflows coordinate
through the action registry; AI output enters only as grounded facts or cited answers.

## Data model

22 tables (`backend/app/infrastructure/database/models.py`, migration `0001`): organizations, users,
cases, properties, parties, documents, document_pages, extracted_facts, ownership_events,
financial_obligations, transactions, reconciliation_runs/results, risk_findings, tasks, comments,
workflow_actions, approvals, decisions, case_questions, referrals, audit_events. UUID keys,
foreign keys to the case, ISO dates, integer cents, provenance (document, page, snippet,
confidence, extractor, reviewer) on every fact.

Legacy mapping: InHeir `user/case_details/case_summary/chat_history/report` → `users/cases/
documents+extracted_facts+parties/case_questions/referrals`; Beacon incidents/approvals/contracts →
`cases+audit_events/workflow_actions+approvals` (contracts not migrated, see inventory);
Magpie recon (in-memory queue, CSV batches) → `reconciliation_runs/results` with persisted reviews.
No production data existed in this workspace to migrate; the mapping is implemented as the schema.
