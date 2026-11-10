# Monitoring: what to alert on, and the queries behind it

All signals come from Cloud Run's built-in metrics and Groundwork's structured JSON logs
(`jsonPayload.message`, `.provider`, `.status`, `.duration_ms`, `.request_id`). Create each
policy in Cloud Monitoring → Alerting with the email channel `bootstrap.ps1 -AlertEmail` made.
Log-based metrics: Logging → Log-based metrics → Create (counter) with the filter shown.

| Signal | Source / filter | Alert when |
|---|---|---|
| API availability | Uptime check `GET /health` every 5 min | 2 consecutive failures |
| Readiness (DB, pool, config) | Uptime check `GET /ready` | non-200 for 10 min |
| Error rate | `run.googleapis.com/request_count` with `response_code_class="5xx"` | > 2% of requests over 10 min |
| Latency | `run.googleapis.com/request_latencies` p95 | > 3 s for 15 min |
| Database connectivity | log metric `jsonPayload.message="request" AND jsonPayload.status=503 AND httpRequest.requestUrl:"/ready"` | ≥ 1 in 5 min |
| Pool exhaustion | log text `QueuePool limit` or `/ready` `checks.db_pool.ok=false` | ≥ 1 in 5 min |
| Document AI failures | `jsonPayload.message="provider failed" AND jsonPayload.provider="document_ai"` | ≥ 3 in 15 min |
| AI provider failures | `… provider="vertex_gemini"` | ≥ 5 in 15 min |
| Storage failures | `… provider="cloud_storage"` | ≥ 1 in 5 min |
| PII service failures | `… provider="sensitive_data_protection"` | ≥ 3 in 15 min |
| Approval failures | `jsonPayload.message="request" AND jsonPayload.path:"/approvals/" AND jsonPayload.status>=500` | ≥ 1 |
| Webhook delivery failures | `jsonPayload.message="webhook failed"` | ≥ 3 in 1 h |
| Background job failures | `resource.type="cloud_run_job" AND severity>=ERROR` or job execution failed | any |
| Audit chain broken | `verify-audit` job exits non-zero (`jsonPayload.broken>0`) | any |

Tracing one operation: every response carries `X-Request-ID`; filter Logs Explorer with
`jsonPayload.request_id="<id>"` to see the API request, its provider calls (type, attempt,
duration) and outcome. With Cloud Run's trace header, logs also link to Cloud Trace. No log
line contains document text, prompts, answers, names or credentials (`app/core/logging.py`).
