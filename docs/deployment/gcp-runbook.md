# Groundwork on Google Cloud — staging runbook (Windows PowerShell 5.1)

Audience: the operator deploying Groundwork. Every command is copy-pasteable. Values in
`<ANGLE_BRACKETS>` are the only things you must supply.

## Architecture (what actually gets created)

| Capability | Service | Notes |
|---|---|---|
| App (frontend + API, one image) | Cloud Run `groundwork-staging` | scale 0–3, 1 vCPU / 1 GiB, `/ready` startup probe, `/health` liveness |
| Database | Cloud SQL PostgreSQL 16 `groundwork-pg` (db-f1-micro) | Unix-socket connector from Cloud Run, daily backups + PITR, SSL required |
| Evidence | Cloud Storage `<PROJECT>-groundwork-evidence-staging` | uniform access, public-access prevention, versioning; write-once keys |
| OCR | Document AI OCR processor `groundwork-ocr` (us) | online processing ≤ 20 MB / 15 pages |
| AI | Gemini 2.5 Flash on Vertex AI | ADC, no API key |
| PII | Sensitive Data Protection | inspect counts + de-identify prompts |
| Secrets | Secret Manager | jwt, database URL, webhook key, processor id |
| Images | Artifact Registry `groundwork` | |
| Build/deploy | Cloud Build (`infra/cloudbuild.yaml`) triggered by GitHub Actions via Workload Identity Federation | |
| Jobs | Cloud Run Jobs `expire-actions` (hourly), `verify-audit` (daily) + Cloud Scheduler | |
| Logs/metrics | Cloud Logging (JSON with request + trace id), Cloud Monitoring | |

Not used: Pub/Sub (no asynchronous fan-out exists yet — every operation completes in its request or a job), GKE, VPC connectors, load balancers.

## 1. Install and sign in

```powershell
winget install --id Google.CloudSDK -e
# open a NEW PowerShell window, then:
gcloud auth login
gcloud auth application-default login          # for live tests from this machine; no key files
gcloud config set project <PROJECT_ID>
gcloud config set run/region us-central1
gcloud config list
gcloud auth list
gcloud billing projects describe <PROJECT_ID>   # billingEnabled must be True
```

## 2. Inspect, then provision

```powershell
cd C:\Users\Administrator\Downloads\DAYOPS
.\infra\scripts\bootstrap.ps1 -ProjectId <PROJECT_ID>                                     # dry run: prints what is missing
.\infra\scripts\bootstrap.ps1 -ProjectId <PROJECT_ID> -GitHubRepo <OWNER/REPO> -CreateBillableResources
```

It prints the four GitHub secrets to add (Settings → Secrets → Actions):
`GCP_PROJECT_ID`, `GCP_WIF_PROVIDER`, `GCP_DEPLOYER_SA`, `GCP_SQL_INSTANCE`. Create GitHub
environments `staging` and `production` (add required reviewers to `production`).

## 3. First deploy (from your machine, or push to `main`)

```powershell
$sha = (git rev-parse --short HEAD)
gcloud builds submit --config infra/cloudbuild.yaml --substitutions "_INSTANCE=<PROJECT_ID>:us-central1:groundwork-pg,SHORT_SHA=$sha"
# Browsers reach the app directly; Groundwork has its own sign-in. Allow public invocation
# (skip if your organisation policy forbids allUsers — then use IAP or an authenticated proxy):
gcloud run services add-iam-policy-binding groundwork-staging --member allUsers --role roles/run.invoker
$url = gcloud run services describe groundwork-staging --format "value(status.url)"
Invoke-RestMethod "$url/health"; Invoke-RestMethod "$url/ready"
```

Scheduled jobs (after the first image exists — `bootstrap.ps1` prints the exact commands):

```powershell
$img = "us-central1-docker.pkg.dev/<PROJECT_ID>/groundwork/groundwork:$sha"
# run the two "gcloud run jobs deploy" and two "gcloud scheduler jobs create http" lines it printed, with <IMAGE> = $img
```

## 4. Verify against the live services

```powershell
cd backend
$env:GROUNDWORK_LIVE = "1"; $env:GCP_PROJECT = "<PROJECT_ID>"; $env:GCS_BUCKET = "<PROJECT_ID>-groundwork-evidence-staging"
$env:DOCUMENTAI_PROCESSOR_ID = (gcloud secrets versions access latest --secret groundwork-documentai-processor)
.venv\Scripts\python -m pytest live_tests -v           # Cloud SQL test also needs DATABASE_URL via the Cloud SQL Auth Proxy
cd ..
backend\.venv\Scripts\python scripts\smoke_e2e.py --base-url $url --report smoke-staging.json
```

The smoke report labels each of the 12 steps `live:<service>` or `local:<impl>` from what the
server actually reports — a staging run should show `live:cloud-sql`, `live:cloud-storage`,
`live:document-ai`, `live:sensitive-data-protection`, `live:vertex-gemini`.

## 5. Promote to production (explicit, digest-pinned)

```powershell
$digest = gcloud run revisions describe (gcloud run services describe groundwork-staging --format "value(status.latestReadyRevisionName)") --format "value(status.imageDigest)"
# one-time: create the prod database + secret
gcloud sql databases create groundwork_prod --instance groundwork-pg
.\infra\scripts\promote.ps1 -ProjectId <PROJECT_ID> -ImageDigest $digest
# rollback
gcloud run revisions list --service groundwork-prod
gcloud run services update-traffic groundwork-prod --to-revisions <PREVIOUS_REVISION>=100
```

Migrations are forward-only Alembic revisions run by a job before traffic moves; nothing drops
or recreates tables. Test every migration on staging first (the pipeline does this).

## 6. Cost control

`.\infra\scripts\cleanup-staging.ps1 -ProjectId <PROJECT_ID>` prints (never runs) teardown
commands. Stopping Cloud SQL (`--activation-policy NEVER`) removes most of the fixed cost.
See `docs/deployment/budget-300.md` for the $300-credit plan.
