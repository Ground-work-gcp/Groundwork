# Security model

* **Tenancy.** Every case-owned record is read via `app/repositories` with a join to the case's
  organisation; another organisation's ids return 404 (`tests/integration/test_permissions.py`).
* **Authentication.** bcrypt passwords; HS256 JWT in an httpOnly, SameSite=Lax cookie (Secure in
  production) or a bearer token. Roles are read from the database on every request.
* **CSRF.** Cookie-authenticated writes require the `x-groundwork-client` header (blocked cross-site
  by CORS preflight). CORS is an explicit allowlist.
* **Roles.** viewer < paralegal < attorney < admin (`app/core/security.py`); platform operations are a
  separate `is_platform_admin` flag and 404 for everyone else.
* **Consequential actions** go through the allowlist: exact params, exact confirmation phrase,
  proposer ≠ approver, single use, expiry, re-checked preconditions, kill switch.
* **AI boundary.** No model writes to the case. Model-proposed facts must quote their page; answers
  must cite numbered sources; text sent to a model is PII-redacted first.
* **PII.** Only counts by type and page are stored outside the document itself.
* **Files.** Size and type limits, SHA-256 integrity check on download, `Content-Disposition:
  attachment`, path-traversal-safe local storage.
* **History.** Hash-chained audit events; `/audit/verify` names the first broken record.
* **Headers.** CSP, X-Frame-Options DENY, nosniff, no-store on API responses.
* **Secrets.** Secret Manager in production; production refuses to start on development defaults.
* **Not done:** DAST scan (HawkScan) — no API key in this environment.
