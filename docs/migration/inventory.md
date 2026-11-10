# Migration inventory

Originals are preserved outside the product at `../Groundwork-legacy-backup/` (copied before any
change). Status key: **T** implemented & tested · **C** implemented, awaiting cloud credentials ·
**L** local implementation for development · **N** not migrated (reason given).

## InHeir (`inheir.ai-main`) → property intelligence and evidence

| Capability | Original files | Groundwork location | Status |
|---|---|---|---|
| Sign-up / sign-in / sign-out / username check, bcrypt, JWT cookie | `backend/.../routers/auth.py`, `helpers/auth.py`, `middleware/auth.py` | `app/api/v1/auth.py`, `app/core/security.py`, `api/v1/deps.py` (+ roles, CSRF header, bearer tokens, rate limit) | T |
| Case create with document + supporting documents | `routers/case.py::create_case` | `POST /cases`, `POST /cases/{id}/documents` | T |
| Blob storage of uploads | `services/storage.py` (Azure Blob) | `infrastructure/storage` (local / Cloud Storage), SHA-256 keyed | T (local) · C (GCS) |
| Document text extraction | `services/rag.py` (Azure Document Intelligence) | `services/extraction` (pypdf/text locally; Document AI) | T (local) · C (Document AI) |
| PII entities | `routers/case.py` (Azure Text Analytics) | `services/pii` (patterns locally; Sensitive Data Protection) — counts only, values never stored | T (local) · C (DLP) |
| LLM case summary, entities, recommendations | `routers/case.py` (Azure OpenAI, LangChain) | Replaced by deterministic facts/ownership/findings with citations; Gemini proposes facts through a grounding gate | T (rules, gate) · C (Gemini) |
| Case chatbot / RAG | `routers/chatbot.py`, `functions/ChatBotFunction` (Azure AI Search + OpenAI) | `services/search` + `services/rag`, `POST /cases/{id}/questions` with citations; extractive when AI off | T (extractive) · C (Gemini) |
| Case history, resolve / abort with remarks | `routers/case.py` | `GET /cases`, `case.resolve` / `case.close_unresolved` approved actions | T |
| Fraud "report" + admin verify | `routers/report.py`, `app/home/report/*` | Referrals: `/referrals`, verify / convert to case | T |
| Old-anonymous-case cleanup | `functions/DeleteOldEntries` (Azure Function) | N — anonymous use removed; every case belongs to an organisation |
| GIS "risk scores" | `routers/gis.py` (OpenCage + LLM-invented crime/flood scores) | N — scores were ungrounded model output and real-estate investment analysis is out of scope |
| Frontend pages (Next.js, Fluent UI) | `frontend/src/app/*` | Rebuilt in Beacon: Cases, New case, Case view tabs, Referrals, Sign-in | T (build) |

## Magpie (`magpie-main`) → financial reconciliation

| Capability | Original files | Groundwork location | Status |
|---|---|---|---|
| Deterministic matcher (T0–T4, three outcomes, evidence) | `lib/recon/match.ts`, `candidates.ts`, `tolerance.ts`, `money.ts` | `domain/reconciliation/engine/*` | **T — 456/456 results identical to the TypeScript (golden test), 0 false matches vs `truth.json`** |
| CSV parsing / ingestion with rejections as records | `parse.ts`, `ingest.ts` | `engine/parse.py`, `engine/ingest.py`; reused for case payment import | T |
| Cash position (reconciled / in flight / at risk) | `cash.ts` | `engine/cash.py` (identical); case version = confirmed / outstanding / unverified band | T |
| Review queue commands with undo | `queue-commands.ts`, `components/recon/review-queue.tsx` | `engine/queue.py`, `POST /reconciliation/runs/{id}/review` — now persisted (Magpie kept it in memory) | T |
| Adaptation to property cases | — | `property_lane.py`: tax bills/liens ↔ payments, installments, shortfalls, near-miss references, escalation, payer-vs-owner lane | T |
| Seven-source settlement batch | `scripts/recon-*.ts` | `POST /admin/reconciliation/settlement-batch` (platform admins only) | T (engine) |
| LLM adjudication tier | `adjudicate.ts`, `openai.ts` | N — validation gate design reused for document facts; settlement adjudication is out of domain |
| S3 statement upload | `lib/recon/s3.ts` | N — AWS; uploads go through Groundwork's storage adapter |
| Modelling grid/engine, boards, databases, agents (CopilotKit), learning site, marketing | `lib/model`, `lib/board`, `app/(app)/*`, `app/learning` | N — general finance workspace, outside the property-resolution product |
| Better Auth + Prisma | `lib/auth.ts`, `prisma/` | Superseded by the unified auth and schema |

## Beacon (`Beacon-assemblyai`) → frontend foundation and workflow

| Capability | Original files | Groundwork location | Status |
|---|---|---|---|
| Theme, typography, colour system, chrome, motion | `web/src/theme.css`, `components/Chrome.tsx`, `motion.ts`, `hooks.ts` | `frontend/src/styles/theme.css` (unchanged), `components/Chrome.tsx`, `hooks/*` | T (build + browser) |
| Hash routing, nav indicator, burger, keyboard shortcuts, toasts, help popover, footer wordmark | `web/src/App.tsx` | `frontend/src/app/App.tsx`, `navigation/routes.ts` | T |
| Timeline, tally tiles, FAQ / Safety controls-and-proof | `Timeline.tsx`, `NightBoard.tsx`, `Safety.tsx` | `components/Timeline.tsx` (audit events), `components/ui.tsx`, `features/settings` | T |
| Approval ledger: exact phrase, params hash, single use, expiry | `src/beacon/approvals.py`, `remediation/registry.py`, `remediate.py` | `domain/workflows`, `domain/approvals` | T |
| Kill switch (`APPLY_ENABLED`) | `remediate.py` | `WORKFLOW_EXECUTION_ENABLED` | T |
| Audit log + CSV, postmortem/report rendered from records | `dashboard_api.py`, `reports.py` | `domain/audit` (now hash-chained), `services/reporting` | T |
| Notifications (Slack-style webhook) | `channels.py` | `infrastructure/external_integrations` | L (fire-and-forget; not exercised against a real webhook) |
| AWS remediation (security groups, ECS), Step Functions, CloudWatch verify, CloudTrail | `remediation/*`, templates | N — infrastructure SRE controls, not exposed to legal customers |
| Voice (AssemblyAI, Transcribe, Polly), phone bridge, Telegram, Sleep Contracts | `voice_*`, `phone/*`, `telegram*.py`, `contracts.py`, `web/src/voice/*` | N — on-call voice product; standing approvals would weaken legal consent |
| Bedrock/Nova triage, Cordon log reduction | `triage.py`, `budget.py` | N — log triage is out of domain |

## Remaining gaps

* Document AI, Gemini, Sensitive Data Protection and Cloud Storage adapters are written against the
  current SDKs and import cleanly but have **not been called against a live project** (no
  credentials in this environment).
* The Docker image and Cloud Build/Cloud Run definitions were not built here (Docker daemon unavailable).
* The settlement-batch admin endpoint has no UI (API only).
