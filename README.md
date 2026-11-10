# Groundwork

**Inherited-property resolution on the evidence.** One *Property Resolution Case* connects a
property's identity, its parties, the deeds, death records, wills and tax records, the facts
extracted from them (each traced to its document and page), the chain of title rebuilt from
those facts, the tax and lien obligations reconciled against payments, the discrepancies
between all of them, and the human decisions and approvals that resolve the case — with a
hash-chained history of every step.

Groundwork was formed by merging three codebases into one product: InHeir (property and
legal-document intelligence), Magpie (financial reconciliation) and Beacon (frontend and
approval workflow). See `docs/migration/inventory.md` for what came from where.

```
Groundwork/
├── frontend/   the only frontend — Beacon's React/Vite app, extended
├── backend/    the only backend — one FastAPI modular monolith, one /api/v1
├── docs/       architecture · workflows · migration · security
├── infra/      Cloud Build + Cloud Run (Google Cloud only)
├── scripts/    demo seeder · golden-fixture exporter for the reconciliation port
├── Dockerfile  one image: built frontend served by the backend
└── .env.example
```

## Run locally

Requirements: Python 3.12+ (with `uv` or `pip`) and Node 20+. No cloud credentials needed.

```bash
# backend
cd backend
uv venv .venv && uv pip install --python .venv/Scripts/python.exe -e ".[dev,postgres]"   # .venv/bin/python on macOS/Linux
.venv/Scripts/alembic upgrade head
.venv/Scripts/python -m uvicorn app.main:app --reload --port 8000

# frontend (second terminal)
cd frontend
npm install
npm run dev            # http://localhost:5173 — proxies /api to :8000

# optional: a demo case with synthetic evidence (third terminal, from the repo root)
backend/.venv/Scripts/python scripts/seed_demo.py      # users avery / pat / alex, password demo-password-1
```

Single-process mode (as in production): `cd frontend && npm run build`, then start only the
backend — it serves `frontend/dist` at http://127.0.0.1:8000.

## Test

```bash
cd backend
.venv/Scripts/python -m pytest -q                       # SQLite
GROUNDWORK_TEST_DATABASE_URL=postgresql+psycopg://user:pw@host:5432/db .venv/Scripts/python -m pytest -q
.venv/Scripts/ruff check app tests
cd ../frontend && npm run build                          # tsc --strict + vite
```

## Configuration and Google Cloud

All settings are environment variables (`.env.example`). Each integration has a local mode and a
Google Cloud mode: Cloud SQL (PostgreSQL), Cloud Storage, Document AI, Gemini on Vertex AI,
Sensitive Data Protection, Secret Manager, Artifact Registry, Cloud Run. Authentication is
Application Default Credentials / Workload Identity — no keys in code. Deploy with
`infra/cloudbuild.yaml`. The Settings → Integrations page shows what each one is actually running on.

**Groundwork does not make legal determinations, file documents or move money.** Findings are
for professional review; consequential actions require an approver to type `approve action N`.
