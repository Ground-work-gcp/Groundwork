# Running Groundwork on a $300 Google Cloud credit

Estimates from list prices as of this writing — confirm in the Google Cloud pricing calculator
before relying on them. Set a budget alert first:
`gcloud billing budgets create --billing-account <BILLING_ACCOUNT_ID> --display-name groundwork --budget-amount 50USD --threshold-rule percent=0.5 --threshold-rule percent=0.9`

## Monthly cost at staging / pilot scale (a few users, hundreds of documents)

| Item | Choice | ≈ per month |
|---|---|---|
| Cloud SQL PostgreSQL | db-f1-micro, 10 GB SSD, backups | $9–12 (the main fixed cost) |
| Cloud Run | scale-to-zero, 1 vCPU | $0–3 (free tier covers light use) |
| Document AI OCR | ~$1.50 per 1,000 pages | $1–3 for 1–2k pages |
| Gemini 2.5 Flash (Vertex) | prompts capped at 60k chars, output 2k tokens | $1–5 |
| Sensitive Data Protection | inspection by bytes, small text volumes | $0–2 |
| Cloud Storage | evidence, a few GB | < $1 |
| Artifact Registry | keep ~10 images | < $1 |
| Cloud Build | ~10 min per deploy | $0 (free minutes) to $2 |
| Secret Manager, Scheduler, Logging | small | < $1 |
| **Total** | | **≈ $15–30 / month** |

So $300 funds roughly 10–18 months of a staging + pilot environment. The two levers that move
it: Cloud SQL (stop the instance when idle — `--activation-policy NEVER` — or keep only one
instance hosting both `groundwork_staging` and `groundwork_prod` databases, as the scripts do)
and Document AI page volume.

## Efficient pathway

1. One region (`us-central1`) for everything except Document AI (`us` multi-region).
2. One Cloud SQL instance, two databases (staging, prod). Upgrade the tier only when `/ready`
   or the pool alert shows pressure.
3. Cloud Run min instances 0, max 3. Accept a ~2 s cold start; set `minScale: 1` in production
   only when users notice (≈ +$10–15/month).
4. Gemini through **Vertex AI with the service account** — no Gemini API key. An AI Studio API
   key is a long-lived secret that would have to be stored, rotated and could leak; Vertex uses
   the Cloud Run identity, bills to the same credit, and keeps data in your project.
5. Keep `EXTRACTION_BACKEND=local` for text PDFs in dev; Document AI only in staging/prod.
6. Keep 10 images in Artifact Registry (cleanup policy) and 30 days of logs (default).
7. Watch Billing → Reports filtered by service weekly for the first month.

## Hosting the frontend elsewhere (optional)

Groundwork's image serves the frontend itself, so Vercel/Render are not needed. If you host the
built `frontend/dist` elsewhere, set `CORS_ORIGINS=https://<that-origin>` on Cloud Run and add a
rewrite of `/api/*` to the Cloud Run URL; the session cookie then needs a shared parent domain.
Same-origin on Cloud Run is simpler and safer.
