# Connecting Groundwork to Google Cloud and Gemini (manual integration)

Groundwork runs fully offline with no credentials (SQLite, local storage, rule engines).
Each Google product is switched on independently with an environment variable, so you can
connect one at a time. For the full scripted staging deployment see `gcp-runbook.md`.

## 0. What you need

| Feature | Google product | Switch (`.env`) | Auth |
|---|---|---|---|
| Gemini (assistant, Studio refinement, case Q&A) | Gemini API (AI Studio) **or** Vertex AI | `AI_BACKEND=gemini_api` / `vertex` | API key / ADC |
| OCR of scans | Document AI | `EXTRACTION_BACKEND=documentai` | ADC |
| PII scanning | Sensitive Data Protection (DLP) | `PII_BACKEND=dlp` | ADC |
| File storage | Cloud Storage | `STORAGE_BACKEND=gcs` | ADC |
| Database | Cloud SQL for PostgreSQL | `DATABASE_URL=postgresql+psycopg://…` | password / socket |
| Hosting | Cloud Run (+ Artifact Registry, Secret Manager) | — | service account |

Install the Google SDKs once: `pip install -e "backend[gcp,postgres]"`.

## 1. Fastest path: Gemini with an API key (5 minutes, local dev)

1. Open https://aistudio.google.com/apikey → **Create API key** (pick or create a Google Cloud project).
2. In the repo root: `cp .env.example .env`, then set
   ```
   AI_BACKEND=gemini_api
   GEMINI_API_KEY=<your key>          # never commit .env
   GEMINI_MODEL=gemini-2.5-flash
   ```
3. Restart the backend. **Studio** (top nav, or `g` then `e`) shows "Gemini connected".

## 2. Production path: Vertex AI with Application Default Credentials (no keys)

```bash
gcloud auth login
gcloud config set project <PROJECT_ID>
gcloud services enable aiplatform.googleapis.com documentai.googleapis.com dlp.googleapis.com \
  storage.googleapis.com sqladmin.googleapis.com run.googleapis.com secretmanager.googleapis.com \
  artifactregistry.googleapis.com cloudbuild.googleapis.com
gcloud auth application-default login     # local machine only; Cloud Run uses its service account
```
`.env`:
```
GCP_PROJECT=<PROJECT_ID>
GCP_LOCATION=us-central1
AI_BACKEND=vertex
```

## 3. Other products (optional, one at a time)

**Cloud Storage**
```bash
gcloud storage buckets create gs://<PROJECT_ID>-groundwork --location=us-central1 --uniform-bucket-level-access --public-access-prevention
```
`STORAGE_BACKEND=gcs`, `GCS_BUCKET=<PROJECT_ID>-groundwork`

**Document AI** — Console → Document AI → *Create processor* → **Document OCR**, region `us`. Copy the processor ID.
`EXTRACTION_BACKEND=documentai`, `DOCUMENTAI_LOCATION=us`, `DOCUMENTAI_PROCESSOR_ID=<id>`

**DLP** — `PII_BACKEND=dlp` (API enabled above; nothing else to create).

**Cloud SQL**
```bash
gcloud sql instances create groundwork-pg --database-version=POSTGRES_16 --tier=db-f1-micro --region=us-central1
gcloud sql databases create groundwork --instance=groundwork-pg
gcloud sql users create groundwork --instance=groundwork-pg --password=<PASSWORD>
```
Local (via Cloud SQL Auth Proxy on 5432): `DATABASE_URL=postgresql+psycopg://groundwork:<PASSWORD>@localhost:5432/groundwork`
Cloud Run: `DATABASE_URL=postgresql+psycopg://groundwork:<PASSWORD>@/groundwork?host=/cloudsql/<PROJECT_ID>:us-central1:groundwork-pg`
Then `cd backend && alembic upgrade head`.

## 4. IAM for the Cloud Run service account

```bash
SA=groundwork-run@<PROJECT_ID>.iam.gserviceaccount.com
gcloud iam service-accounts create groundwork-run
for ROLE in roles/aiplatform.user roles/documentai.apiUser roles/dlp.user roles/storage.objectAdmin \
            roles/cloudsql.client roles/secretmanager.secretAccessor roles/logging.logWriter; do
  gcloud projects add-iam-policy-binding <PROJECT_ID> --member="serviceAccount:$SA" --role="$ROLE"
done
```

## 5. Secrets (never put keys in images or git)

```bash
printf '%s' "$(openssl rand -hex 32)" | gcloud secrets create jwt-secret --data-file=-
printf '%s' "<GEMINI_KEY>"            | gcloud secrets create gemini-api-key --data-file=-   # only if using gemini_api
```

## 6. Deploy to Cloud Run

```bash
gcloud run deploy groundwork --source . --region us-central1 --service-account "$SA" \
  --add-cloudsql-instances <PROJECT_ID>:us-central1:groundwork-pg \
  --set-env-vars ENVIRONMENT=production,GCP_PROJECT=<PROJECT_ID>,AI_BACKEND=vertex,STORAGE_BACKEND=gcs,GCS_BUCKET=<PROJECT_ID>-groundwork,PII_BACKEND=dlp \
  --set-secrets JWT_SECRET=jwt-secret:latest,DATABASE_URL=database-url:latest
```
Production refuses to start on unsafe defaults (SQLite, local storage, dev JWT secret) and prints exactly which variable is wrong.

## 7. Verify

* `GET /health` and `/ready` → ok.
* Settings → Integrations shows each backend as configured.
* Studio header says "Gemini connected"; generated sections say "Refined by gemini-…".
* If Gemini fails (quota, network), Studio still returns the rule-engine output and shows the reason.

## Engineering Studio API (`/api/v1/studio/*`, signed-in)

| Endpoint | Input | Output |
|---|---|---|
| `POST /requirements` | `idea`, `notes` (send previous output as notes to refine) | functional / non-functional reqs, assumptions, open questions |
| `POST /stack` | `requirements`, `language`, `scale` | stack table + bill of materials (version, license, cost tier) |
| `POST /architecture` | `requirements`, `stack` | components, flows, ADRs, Mermaid diagram |
| `POST /scaffold` | `name`, `language` (python/node) | runnable project files (app, tests, Dockerfile, CI, Cloud Run deploy) |
| `POST /stories` | `requirements[]`, `persona` | INVEST stories with Gherkin-style acceptance and points |
| `POST /code-health` | `code`, `language` | score/grade, complexity per function, issues (secrets, bare except, …) |
| `POST /visualize` | `code`, `language` | Mermaid class diagram / call graph |
| `POST /tests` | `code`, `language`, `module` | pytest or vitest test file |
