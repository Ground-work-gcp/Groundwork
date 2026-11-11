"""Project pipeline: stage order, staleness, the full GCP-ready repository bundle, traceability,
Markdown export and ZIP packaging. Pure functions over artifact contents — no I/O here."""

from __future__ import annotations

import csv
import io
import re
import zipfile
from typing import Any

from . import _slug, invest, scaffold

STEPS = ("requirements", "stack", "architecture", "stories", "scaffold")
# step -> upstream steps whose versions it is built from
DEPENDS: dict[str, tuple[str, ...]] = {
    "requirements": (),
    "stack": ("requirements",),
    "architecture": ("requirements", "stack"),
    "stories": ("requirements",),
    "scaffold": ("requirements", "stack", "architecture", "stories"),
}


def requirements_text(req: dict[str, Any]) -> str:
    return "\n".join([req.get("summary", ""), *req.get("functional", []), *req.get("non_functional", [])])


def stale(kind: str, based_on: dict[str, int], latest: dict[str, int]) -> list[str]:
    """Upstream steps regenerated since this artifact was built."""
    return [up for up in DEPENDS.get(kind, ()) if up in latest and based_on.get(up) != latest[up]]


# ── traceability ────────────────────────────────────────────────────────────

def _fr_id(text: str, index: int) -> str:
    m = re.match(r"(FR-\d+)", text)
    return m.group(1) if m else f"FR-{index + 1}"


def _test_name(story_id: str, n: int) -> str:
    return f"test_{story_id.lower().replace('-', '_')}_ac{n + 1}"


def traceability(req: dict[str, Any] | None, stories: dict[str, Any] | None) -> dict[str, Any]:
    functional = (req or {}).get("functional", [])
    story_list = (stories or {}).get("stories", [])
    rows = []
    for i, fr in enumerate(functional):
        body = re.sub(r"^FR-\d+:\s*", "", fr).lower()
        linked = [s for s in story_list if s.get("requirement") == _fr_id(fr, i)
                  or (not s.get("requirement") and str(s.get("i_want", "")).lower()[:40] in body)]
        rows.append({
            "requirement": _fr_id(fr, i), "text": fr,
            "stories": [s["id"] for s in linked],
            "tests": [_test_name(s["id"], n) for s in linked for n in range(len(s.get("acceptance", [])))],
        })
    covered = sum(1 for r in rows if r["stories"])
    return {"rows": rows, "coverage": round(100 * covered / len(rows)) if rows else 0,
            "uncovered": [r["requirement"] for r in rows if not r["stories"]]}


# ── the repository bundle ───────────────────────────────────────────────────

def _terraform(slug: str, layers: set[str]) -> str:
    apis = ["run", "sqladmin", "secretmanager", "artifactregistry", "cloudbuild", "storage", "iam", "logging", "monitoring"]
    roles = ["roles/cloudsql.client", "roles/secretmanager.secretAccessor", "roles/storage.objectAdmin", "roles/logging.logWriter",
             "roles/monitoring.metricWriter", "roles/cloudtrace.agent"]
    if "AI" in layers:
        apis.append("aiplatform")
        roles.append("roles/aiplatform.user")
    if "Document AI" in layers:
        apis.append("documentai")
        roles.append("roles/documentai.apiUser")
    if "Messaging" in layers:
        apis += ["pubsub", "cloudtasks"]
        roles += ["roles/pubsub.publisher", "roles/cloudtasks.enqueuer"]
    if "Analytics" in layers:
        apis.append("bigquery")
        roles.append("roles/bigquery.dataEditor")
    if "Cache" in layers:
        apis.append("redis")
    api_list = ", ".join(f'"{a}.googleapis.com"' for a in apis)
    role_list = ", ".join(f'"{r}"' for r in roles)
    return f'''terraform {{
  required_version = ">= 1.6"
  required_providers {{ google = {{ source = "hashicorp/google", version = "~> 6.0" }} }}
  # backend "gcs" {{ bucket = "<state-bucket>" prefix = "{slug}" }}
}}

variable "project" {{ type = string }}
variable "region"  {{ default = "us-central1" }}
variable "image"   {{ description = "Artifact Registry image, set by Cloud Build" }}

provider "google" {{
  project = var.project
  region  = var.region
}}

resource "google_project_service" "apis" {{
  for_each           = toset([{api_list}])
  service            = each.value
  disable_on_destroy = false
}}

resource "google_service_account" "run" {{
  account_id   = "{slug[:24]}-run"
  display_name = "{slug} Cloud Run runtime"
}}

resource "google_project_iam_member" "run" {{
  for_each = toset([{role_list}])
  project  = var.project
  role     = each.value
  member   = "serviceAccount:${{google_service_account.run.email}}"
}}

resource "google_artifact_registry_repository" "images" {{
  repository_id = "{slug}"
  format        = "DOCKER"
  location      = var.region
  depends_on    = [google_project_service.apis]
}}

resource "google_storage_bucket" "files" {{
  name                        = "${{var.project}}-{slug}-files"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  versioning {{ enabled = true }}
}}

resource "google_sql_database_instance" "db" {{
  name             = "{slug}-pg"
  database_version = "POSTGRES_16"
  region           = var.region
  settings {{
    tier              = "db-custom-1-3840"
    availability_type = "REGIONAL"
    backup_configuration {{
      enabled                        = true
      point_in_time_recovery_enabled = true
    }}
    ip_configuration {{ ssl_mode = "ENCRYPTED_ONLY" }}
    insights_config {{ query_insights_enabled = true }}
  }}
  deletion_protection = true
  depends_on          = [google_project_service.apis]
}}

resource "google_secret_manager_secret" "database_url" {{
  secret_id = "{slug}-database-url"
  replication {{
    auto {{}}
  }}
}}

resource "google_cloud_run_v2_service" "app" {{
  name     = "{slug}"
  location = var.region
  template {{
    service_account = google_service_account.run.email
    scaling {{
      min_instance_count = 0
      max_instance_count = 10
    }}
    volumes {{
      name = "cloudsql"
      cloud_sql_instance {{ instances = [google_sql_database_instance.db.connection_name] }}
    }}
    containers {{
      image = var.image
      resources {{ limits = {{ cpu = "1", memory = "1Gi" }} }}
      env {{
        name  = "ENVIRONMENT"
        value = "production"
      }}
      env {{
        name  = "GCP_PROJECT"
        value = var.project
      }}
      env {{
        name = "DATABASE_URL"
        value_source {{
          secret_key_ref {{
            secret  = google_secret_manager_secret.database_url.secret_id
            version = "latest"
          }}
        }}
      }}
      volume_mounts {{
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }}
      startup_probe {{ http_get {{ path = "/health" }} }}
      liveness_probe {{ http_get {{ path = "/health" }} }}
    }}
  }}
}}

output "url" {{ value = google_cloud_run_v2_service.app.uri }}
'''


def _cloudbuild(slug: str, language: str) -> str:
    test = "pip install '.[dev]' && pytest -q" if language != "node" else "npm ci && npm test"
    image = "python:3.12-slim" if language != "node" else "node:20-slim"
    return f'''# Test, build, scan and deploy on every push to main (Cloud Build trigger or `gcloud builds submit`).
steps:
  - id: test
    name: {image}
    entrypoint: bash
    args: ["-c", "{test}"]
  - id: build
    name: gcr.io/cloud-builders/docker
    args: ["build", "-t", "${{_REGION}}-docker.pkg.dev/$PROJECT_ID/{slug}/app:$SHORT_SHA", "."]
  - id: push
    name: gcr.io/cloud-builders/docker
    args: ["push", "${{_REGION}}-docker.pkg.dev/$PROJECT_ID/{slug}/app:$SHORT_SHA"]
  - id: deploy
    name: gcr.io/google.com/cloudsdktool/cloud-sdk:slim
    entrypoint: gcloud
    args: ["run", "deploy", "{slug}", "--image", "${{_REGION}}-docker.pkg.dev/$PROJECT_ID/{slug}/app:$SHORT_SHA",
           "--region", "${{_REGION}}", "--service-account", "{slug[:24]}-run@$PROJECT_ID.iam.gserviceaccount.com"]
substitutions:
  _REGION: us-central1
options:
  logging: CLOUD_LOGGING_ONLY
images: ["${{_REGION}}-docker.pkg.dev/$PROJECT_ID/{slug}/app:$SHORT_SHA"]
'''


def _acceptance_tests(stories: list[dict[str, Any]], language: str) -> tuple[str, str]:
    if language == "node":
        out = ["import test from 'node:test';", ""]
        for s in stories:
            for n, ac in enumerate(s.get("acceptance", [])):
                out.append(f"test('{s['id']} AC{n + 1}: {ac[:90].replace(chr(39), '')}', {{ todo: true }});")
        return "test/acceptance.test.js", "\n".join(out) + "\n"
    out = ['"""Acceptance tests generated from the approved stories (one per acceptance criterion)."""', "", "import pytest", ""]
    for s in stories:
        for n, ac in enumerate(s.get("acceptance", [])):
            out += ["", f'@pytest.mark.skip(reason="not implemented: {s["id"]}")', f"def {_test_name(s['id'], n)}():",
                    f'    """{s["id"]} — {ac.replace(chr(34), chr(39))}"""', "    raise NotImplementedError", ""]
    return "tests/test_acceptance.py", "\n".join(out).rstrip() + "\n"


def bundle(name: str, language: str, artifacts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The scaffold step: a runnable service plus docs, ADRs, BOM, Terraform, Cloud Build and acceptance tests."""
    lang = "node" if language == "node" else "python"
    base = scaffold(name, lang)
    files = {f["path"]: f["content"] for f in base["files"]}
    slug = _slug(name)
    req, stk, arch, sto = (artifacts.get(k) or {} for k in ("requirements", "stack", "architecture", "stories"))
    layers = {r.get("layer", "") for r in stk.get("stack", [])}
    if req:
        files["docs/requirements.md"] = "# Requirements\n\n" + f"{req.get('summary', '')}\n\n## Functional\n\n" + \
            "\n".join(f"- {x}" for x in req.get("functional", [])) + "\n\n## Non-functional\n\n" + \
            "\n".join(f"- {x}" for x in req.get("non_functional", [])) + "\n\n## Open questions\n\n" + \
            "\n".join(f"- [ ] {x}" for x in req.get("open_questions", [])) + "\n"
    if stk:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["layer", "component", "version", "license", "cost_tier"])
        for b in stk.get("bom", []):
            writer.writerow([b.get("layer"), b.get("component"), b.get("version"), b.get("license"), b.get("cost_tier")])
        files["docs/bom.csv"] = buf.getvalue()
    if arch:
        files["docs/architecture.md"] = f"# Architecture\n\n{arch.get('style', '')}\n\n```mermaid\n{arch.get('mermaid', '')}\n```\n"
        for i, d in enumerate(arch.get("decisions", []), 1):
            title = re.sub(r"^ADR-\d+:\s*", "", d)
            files[f"docs/adr/{i:04d}-{_slug(title)[:40]}.md"] = f"# ADR {i}: {title}\n\nStatus: Accepted\n\n## Decision\n\n{title}\n"
    if sto.get("stories"):
        path, code = _acceptance_tests(sto["stories"], lang)
        files[path] = code
        files["docs/stories.md"] = "# Stories\n\n" + "\n".join(
            f"## {s['id']} {s['title']} ({s.get('points')} pts, {s.get('priority')})\n\nAs a {s['as_a']}, I want {s['i_want']}, so that {s['so_that']}.\n\n"
            + "\n".join(f"- [ ] {a}" for a in s.get("acceptance", [])) + "\n" for s in sto["stories"])
    files["infra/main.tf"] = _terraform(slug, layers)
    files["cloudbuild.yaml"] = _cloudbuild(slug, lang)
    files["README.md"] += ("\n## Deploy to Google Cloud\n\n```bash\ncd infra && terraform init && terraform apply -var project=$GCP_PROJECT -var image=<image>\n"
                           "gcloud builds submit --config cloudbuild.yaml\n```\n\nSee `docs/` for requirements, architecture, ADRs, BOM and stories.\n")
    return {"name": name, "language": lang, "files": [{"path": p, "content": c} for p, c in sorted(files.items())],
            "source": "rules", "layers": sorted(layers)}


def to_zip(files: list[dict[str, str]], root: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            path = f["path"].lstrip("/").replace("..", "")
            info = zipfile.ZipInfo(f"{root}/{path}", date_time=(2026, 1, 1, 0, 0, 0))
            info.external_attr = (0o755 if path.endswith(".sh") else 0o644) << 16
            z.writestr(info, f["content"])
    return buf.getvalue()


def to_markdown(project: dict[str, Any], artifacts: dict[str, dict[str, Any]], trace: dict[str, Any]) -> str:
    req, stk, arch, sto = (artifacts.get(k) or {} for k in ("requirements", "stack", "architecture", "stories"))
    out = [f"# {project['name']}", "", f"> {project['idea']}", ""]
    if req:
        out += ["## Requirements", "", req.get("summary", ""), ""] + [f"- {x}" for x in req.get("functional", [])] + [""] + \
               [f"- {x}" for x in req.get("non_functional", [])] + [""]
        if req.get("ambiguities"):
            out += ["### Ambiguities to resolve", ""] + [f"- **{a['term']}** in “{a['text']}” → {a['suggestion']}" for a in req["ambiguities"]] + [""]
    if stk:
        out += ["## Technology stack and BOM", "", f"Estimated monthly cost: {stk.get('estimated_monthly_cost', '?')}", "",
                "| Layer | Component | Version | License | Cost |", "|---|---|---|---|---|"]
        out += [f"| {b.get('layer')} | {b.get('component')} | {b.get('version')} | {b.get('license')} | {b.get('cost_tier')} |" for b in stk.get("bom", [])] + [""]
    if arch:
        out += ["## Architecture", "", arch.get("style", ""), "", "```mermaid", arch.get("mermaid", ""), "```", ""] + \
               [f"- {d}" for d in arch.get("decisions", [])] + [""]
    if sto.get("stories"):
        out += ["## Stories", ""]
        for s in sto["stories"]:
            out += [f"### {s['id']} {s['title']} — {s.get('points')} pts, {s.get('priority')}", "",
                    f"As a {s['as_a']}, I want {s['i_want']}, so that {s['so_that']}.", ""] + [f"- [ ] {a}" for a in s.get("acceptance", [])] + [""]
    out += ["## Traceability", "", f"Coverage: {trace['coverage']}%", "", "| Requirement | Stories | Tests |", "|---|---|---|"]
    out += [f"| {r['requirement']} | {', '.join(r['stories']) or '—'} | {len(r['tests'])} |" for r in trace["rows"]]
    return "\n".join(out) + "\n"


def jira_csv(stories: dict[str, Any]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Issue Type", "Summary", "Description", "Story Points", "Priority", "Labels"])
    for s in stories.get("stories", []):
        desc = f"As a {s['as_a']}, I want {s['i_want']}, so that {s['so_that']}.\n\nAcceptance criteria:\n" + \
               "\n".join(f"* {a}" for a in s.get("acceptance", []))
        w.writerow(["Story", f"{s['id']} {s['title']}", desc, s.get("points", ""), s.get("priority", ""), s.get("requirement", "")])
    return buf.getvalue()


__all__ = ["STEPS", "DEPENDS", "bundle", "invest", "jira_csv", "stale", "to_markdown", "to_zip", "traceability"]
