"""Engineering Studio: requirements → stack/BOM → architecture → scaffold → stories, plus
code health, visualisation and unit-test generation.

Every tool has a deterministic rule-based engine that always works offline. When Gemini is
configured (``AI_BACKEND=vertex`` or ``gemini_api``) the rule output is used as a grounded
draft and Gemini refines it against a strict JSON schema. If Gemini fails, the rule output is
returned with ``source="rules"`` and the failure reason — never an invented or empty answer.
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from app.services import ai

log = logging.getLogger(__name__)
SYSTEM = (
    "You are a senior software architect on Google Cloud. Refine the DRAFT for the INPUT. "
    "Be specific, concise and realistic; keep valid draft content; never invent versions you are unsure of "
    "(use 'latest stable'). Answer only with JSON matching the schema."
)

# ── helpers ────────────────────────────────────────────────────────────────

_STR = {"type": "string"}
_STRS = {"type": "array", "items": _STR}


def _obj(props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": list(props)}


class _TTLCache:
    """Small thread-safe LRU with expiry: identical Gemini requests are answered once (cost + latency)."""

    def __init__(self, size: int = 256, ttl: float = 3600.0) -> None:
        self.size, self.ttl = size, ttl
        self._data: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = self.misses = 0

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._data.get(key)
            if item and time.monotonic() - item[0] < self.ttl:
                self._data.move_to_end(key)
                self.hits += 1
                return item[1]
            self._data.pop(key, None)
            self.misses += 1
            return None

    def put(self, key: str, value: dict[str, Any]) -> None:
        with self._lock:
            self._data[key] = (time.monotonic(), value)
            self._data.move_to_end(key)
            while len(self._data) > self.size:
                self._data.popitem(last=False)

    def stats(self) -> dict[str, int]:
        return {"entries": len(self._data), "hits": self.hits, "misses": self.misses}


CACHE = _TTLCache()


def _refine(kind: str, payload: dict[str, Any], draft: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """Return Gemini's refinement of ``draft`` or the draft itself, tagged with its source."""
    if not ai.enabled():
        return {**draft, "source": "rules", "note": "Gemini not configured; deterministic engine output."}
    prompt = f"TASK: {kind}\nINPUT:\n{json.dumps(payload)[:20000]}\nDRAFT:\n{json.dumps(draft)[:30000]}"
    key = hashlib.sha256(f"{ai.model_name()}\0{prompt}".encode()).hexdigest()
    cached = CACHE.get(key)
    if cached is not None:
        return {**cached, "cached": True}
    try:
        refined = ai.generate_json(prompt, schema=schema, system=SYSTEM)
        if isinstance(refined, dict) and refined:
            out = {**draft, **refined, "source": "gemini", "model": ai.model_name()}
            CACHE.put(key, out)
            return out
    except Exception as exc:  # provider errors must never break the tool
        log.warning("studio %s refinement failed: %s", kind, type(exc).__name__)
        return {**draft, "source": "rules", "note": f"Gemini refinement failed ({type(exc).__name__}); rule output shown."}
    return {**draft, "source": "rules"}


def _has(text: str, *words: str) -> bool:
    return any(re.search(rf"\b{re.escape(w)}", text, re.I) for w in words)


def _mm(text: str) -> str:
    """Make text safe inside a quoted Mermaid label."""
    return re.sub(r'["<>{}|`#;]', "", str(text)).strip()[:80]


def _slug(text: str, default: str = "app") -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40].strip("-")
    return s or default


# ── 1. requirements ────────────────────────────────────────────────────────

VAGUE = {"fast": "state a latency target (e.g. p95 < 300 ms)", "quick": "state a time target", "easy": "define a measurable usability goal",
         "user-friendly": "define a measurable usability goal", "simple": "define what simple means for the user",
         "secure": "name the standard or threat (e.g. OWASP ASVS L2)", "scalable": "state the expected load (users, req/s)",
         "etc": "list every item explicitly", "and/or": "choose and, or, or both", "some": "quantify it", "many": "quantify it",
         "robust": "state the failure mode to survive", "flexible": "name the variation to support", "efficient": "state a resource budget",
         "as appropriate": "state the rule", "if possible": "decide whether it is required", "real-time": "state the latency bound"}


def lint_requirements(lines: list[str]) -> list[dict[str, str]]:
    """Flag ambiguous wording (ISO/IEC/IEEE 29148 'unambiguous, verifiable')."""
    found = []
    for line in lines:
        for word, fix in VAGUE.items():
            if re.search(rf"(?<![\w-]){re.escape(word)}(?![\w-])", line, re.I):
                found.append({"text": line[:160], "term": word, "suggestion": fix})
    return found[:30]


def requirements(idea: str, notes: str = "") -> dict[str, Any]:
    text = f"{idea}\n{notes}"
    sentences = [s.strip() for s in re.split(r"[.\n;]+", text) if len(s.strip()) > 8]
    functional = [f"FR-{i + 1}: The system shall {s[0].lower() + s[1:]}" for i, s in enumerate(sentences[:12])]
    features = [
        (("login", "user", "account", "sign"), "Users can register, sign in and manage their profile (Identity Platform / Firebase Auth)."),
        (("upload", "file", "document", "image"), "Users can upload files; files are stored in Cloud Storage with size/type validation."),
        (("search", "find", "filter"), "Users can search and filter records with paginated results."),
        (("pay", "payment", "checkout", "billing"), "Users can pay securely through a PCI-compliant payment provider."),
        (("notify", "email", "alert", "sms"), "The system sends notifications (email / push) on key events."),
        (("report", "dashboard", "analytics"), "Users can view dashboards and export reports (BigQuery / Looker Studio)."),
        (("ai", "chat", "summar", "gemini", "assistant"), "The system offers AI assistance via Gemini with grounded, cited answers."),
        (("admin", "role", "permission"), "Administrators manage roles and permissions (RBAC)."),
    ]
    for keys, req in features:
        if _has(text, *keys):
            functional.append(f"FR-{len(functional) + 1}: {req}")
    nfr = [
        "NFR-1 Performance: p95 API latency < 300 ms at expected load.",
        "NFR-2 Availability: 99.9% monthly (Cloud Run multi-zone, managed DB with HA).",
        "NFR-3 Security: OWASP ASVS L2, TLS everywhere, secrets in Secret Manager, least-privilege IAM.",
        "NFR-4 Privacy: PII minimised, encrypted at rest; DLP scanning where personal data is stored.",
        "NFR-5 Observability: structured logs, metrics and traces in Cloud Logging / Monitoring.",
        "NFR-6 Maintainability: ≥ 80% unit-test coverage on domain logic; CI on every push.",
        "NFR-7 Accessibility: WCAG 2.2 AA for all user-facing screens.",
    ]
    questions = ["Who are the primary user roles and how many concurrent users are expected?",
                 "What data retention and compliance regimes apply (GDPR, DPDP, HIPAA, PCI)?",
                 "Which integrations or legacy systems must be supported at launch?",
                 "What is the budget and the target launch date?"]
    draft = {
        "ambiguities": lint_requirements(sentences),
        "summary": (sentences[0] if sentences else idea)[:300],
        "functional": functional or ["FR-1: Clarify the core user goal — the idea is too short to derive requirements."],
        "non_functional": nfr,
        "assumptions": ["Hosted on Google Cloud.", "Web client first; mobile later.", "Single region to start."],
        "open_questions": questions,
        "quality_score": min(100, 20 + 8 * len(sentences) + 5 * sum(_has(text, *k) for k, _ in features)),
    }
    schema = _obj({"summary": _STR, "functional": _STRS, "non_functional": _STRS, "assumptions": _STRS, "open_questions": _STRS})
    return _refine("requirements generation and refinement", {"idea": idea, "notes": notes}, draft, schema)


# ── 2. stack + bill of materials ──────────────────────────────────────────

STACKS: dict[str, dict[str, tuple[str, str, str]]] = {
    # layer: (choice, version, license)
    "python": {"Backend": ("FastAPI (Python 3.12)", "0.115+", "MIT"), "ORM": ("SQLAlchemy + Alembic", "2.0+", "MIT"),
               "Tests": ("pytest", "8+", "MIT")},
    "node": {"Backend": ("NestJS (Node 20, TypeScript)", "10+", "MIT"), "ORM": ("Prisma", "5+", "Apache-2.0"),
             "Tests": ("Vitest", "2+", "MIT")},
    "java": {"Backend": ("Spring Boot (Java 21)", "3.3+", "Apache-2.0"), "ORM": ("Spring Data JPA + Flyway", "3.3+", "Apache-2.0"),
             "Tests": ("JUnit 5", "5.10+", "EPL-2.0")},
    "go": {"Backend": ("Go 1.22 + chi", "5+", "MIT"), "ORM": ("sqlc + goose", "1.26+", "MIT"), "Tests": ("go test", "1.22", "BSD-3")},
}


def stack(requirements_text: str, language: str = "python", scale: str = "small") -> dict[str, Any]:
    lang = language if language in STACKS else "python"
    base = STACKS[lang]
    rows: list[tuple[str, str, str, str, str, str]] = [  # layer, choice, version, license, cost, why
        ("Frontend", "React 18 + Vite + TypeScript", "18.3", "MIT", "free", "Fast builds, typed UI, large ecosystem."),
        (*("Backend",), *base["Backend"], "free", "Matches team language; first-class on Cloud Run."),
        ("Data access", *base["ORM"], "free", "Typed queries and versioned migrations."),
        ("Database", "Cloud SQL for PostgreSQL 16", "16", "managed", "$$" if scale != "small" else "$",
         "Relational integrity, PITR backups, HA option."),
        ("Hosting", "Cloud Run", "gen2", "managed", "$", "Serverless containers, scale to zero, HTTPS by default."),
        ("Storage", "Cloud Storage", "-", "managed", "$", "Durable object storage with signed URLs."),
        ("Secrets", "Secret Manager", "-", "managed", "$", "No secrets in code or images."),
        ("CI/CD", "GitHub Actions + Artifact Registry + Cloud Build", "-", "managed", "$", "Build, test, scan, deploy on merge."),
        ("Observability", "Cloud Logging / Monitoring / Trace", "-", "managed", "$", "Built in for Cloud Run."),
        ("Testing", *base["Tests"], "free", "Unit tests in CI."),
    ]
    t = requirements_text
    if _has(t, "ai", "gemini", "chat", "summar", "assistant", "llm"):
        rows.append(("AI", "Gemini 2.5 Flash on Vertex AI (google-genai SDK)", "latest stable", "managed", "$$", "Grounded generation with enterprise data controls."))
    if _has(t, "document", "ocr", "invoice", "pdf", "scan"):
        rows.append(("Document AI", "Document AI OCR / Form Parser", "-", "managed", "$$", "Extract text and fields from scans."))
    if _has(t, "search", "semantic", "rag"):
        rows.append(("Search", "Vertex AI Search or pgvector", "-", "managed", "$$", "Semantic retrieval for RAG."))
    if _has(t, "login", "user", "account", "auth"):
        rows.append(("Identity", "Identity Platform (Firebase Auth)", "-", "managed", "$", "Managed sign-in, MFA, social login."))
    if _has(t, "queue", "async", "event", "background", "notify"):
        rows.append(("Messaging", "Pub/Sub + Cloud Tasks", "-", "managed", "$", "Decoupled async work and retries."))
    if _has(t, "analytics", "report", "dashboard"):
        rows.append(("Analytics", "BigQuery + Looker Studio", "-", "managed", "$$", "Warehouse and dashboards."))
    if _has(t, "cache", "realtime", "session", "leaderboard"):
        rows.append(("Cache", "Memorystore for Redis", "7", "managed", "$$", "Low-latency cache."))
    draft = {
        "language": lang,
        "stack": [{"layer": r[0], "choice": r[1], "why": r[5]} for r in rows],
        "bom": [{"component": r[1], "version": r[2], "license": r[3], "cost_tier": r[4], "layer": r[0]} for r in rows],
        "estimated_monthly_cost": {"small": "$30–150", "medium": "$300–1,500", "large": "$3,000+"}.get(scale, "$30–150"),
    }
    item = _obj({"layer": _STR, "choice": _STR, "why": _STR})
    bom = _obj({"component": _STR, "version": _STR, "license": _STR, "cost_tier": _STR, "layer": _STR})
    schema = _obj({"stack": {"type": "array", "items": item}, "bom": {"type": "array", "items": bom}, "estimated_monthly_cost": _STR})
    return _refine("technology stack selection with bill of materials", {"requirements": t, "language": lang, "scale": scale}, draft, schema)


# ── 3. architecture ────────────────────────────────────────────────────────

def architecture(requirements_text: str, stack_rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    layers = {r.get("layer", ""): r.get("choice", "") for r in (stack_rows or stack(requirements_text)["stack"])}
    comps = [("client", "Web client", layers.get("Frontend", "React SPA")), ("api", "API service", layers.get("Backend", "API on Cloud Run")),
             ("db", "Database", layers.get("Database", "Cloud SQL")), ("gcs", "Object storage", "Cloud Storage"),
             ("secrets", "Secrets", "Secret Manager")]
    optional = [("AI", "ai", "Gemini"), ("Document AI", "docai", "Document AI"), ("Identity", "idp", "Identity Platform"),
                ("Messaging", "queue", "Pub/Sub"), ("Analytics", "bq", "BigQuery"), ("Cache", "cache", "Memorystore"), ("Search", "search", "Search")]
    for layer, key, name in optional:
        if layer in layers:
            comps.append((key, name, layers[layer]))
    keys = {c[0] for c in comps}
    flows = [("client", "api", "HTTPS / JSON"), ("api", "db", "SQL (private IP)"), ("api", "gcs", "signed URLs"), ("api", "secrets", "read at start")]
    flows += [(a, b, lbl) for a, b, lbl in [("client", "idp", "sign-in"), ("api", "ai", "generate"), ("api", "docai", "OCR"),
                                            ("api", "queue", "publish"), ("queue", "api", "push"), ("api", "cache", "get/set"),
                                            ("db", "bq", "export"), ("api", "search", "query")] if a in keys and b in keys]
    mermaid = ["flowchart LR"] + [f'  {k}["{_mm(n)} · {_mm(d)}"]' for k, n, d in comps] + [f'  {a} -->|"{_mm(lbl)}"| {b}' for a, b, lbl in flows]
    draft = {
        "style": "Modular monolith on Cloud Run (split into services only when scaling or team boundaries demand it)",
        "components": [{"id": k, "name": n, "technology": d} for k, n, d in comps],
        "flows": [{"from": a, "to": b, "label": lbl} for a, b, lbl in flows],
        "decisions": ["ADR-1: Stateless containers on Cloud Run; all state in managed services.",
                      "ADR-2: Domain layer free of framework imports; adapters wrap every cloud SDK.",
                      "ADR-3: Every external call has a timeout, retry with backoff and a local fallback.",
                      "ADR-4: Least-privilege service account per service; Workload Identity Federation for CI."],
        "mermaid": "\n".join(mermaid),
    }
    comp = _obj({"id": _STR, "name": _STR, "technology": _STR})
    schema = _obj({"style": _STR, "components": {"type": "array", "items": comp}, "decisions": _STRS, "mermaid": _STR})
    return _refine("architecture design (keep mermaid valid flowchart syntax)", {"requirements": requirements_text}, draft, schema)


# ── 4. scaffolding ────────────────────────────────────────────────────────

def scaffold(name: str, language: str = "python") -> dict[str, Any]:
    slug = _slug(name)
    files: dict[str, str] = {
        "README.md": f"# {name}\n\nGenerated by Groundwork Studio.\n\n```bash\ncp .env.example .env\ndocker compose up\n```\n",
        ".env.example": "ENVIRONMENT=development\nGCP_PROJECT=\nGCP_LOCATION=us-central1\nAI_BACKEND=none\nGEMINI_API_KEY=\n",
        ".gitignore": ".env\n__pycache__/\nnode_modules/\ndist/\n.venv/\n",
        ".github/workflows/ci.yml": "name: ci\non: [push, pull_request]\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n",
    }
    if language == "node":
        files.update({
            "package.json": json.dumps({"name": slug, "version": "0.1.0", "type": "module", "scripts": {"start": "node src/server.js", "test": "node --test"},
                                        "dependencies": {"express": "^4.21.0"}}, indent=2) + "\n",
            "src/server.js": "import express from 'express';\nexport const app = express();\napp.get('/health', (_req, res) => res.json({ ok: true }));\n"
                             "if (process.argv[1].endsWith('server.js')) app.listen(process.env.PORT || 8080);\n",
            "test/health.test.js": "import test from 'node:test';\nimport assert from 'node:assert';\nimport { app } from '../src/server.js';\n\n"
                                   "test('app exists', () => assert.ok(app));\n",
            "Dockerfile": "FROM node:20-slim\nWORKDIR /app\nCOPY package*.json ./\nRUN npm ci --omit=dev\nCOPY . .\nUSER node\nCMD [\"npm\",\"start\"]\n",
        })
        files[".github/workflows/ci.yml"] += "      - uses: actions/setup-node@v4\n        with: { node-version: '20' }\n      - run: npm ci && npm test\n"
    else:
        pkg = slug.replace("-", "_")
        files.update({
            "pyproject.toml": f'[project]\nname = "{slug}"\nversion = "0.1.0"\nrequires-python = ">=3.12"\n'
                              'dependencies = ["fastapi>=0.115", "uvicorn[standard]>=0.32", "pydantic-settings>=2.6"]\n\n'
                              '[project.optional-dependencies]\ngcp = ["google-genai>=1.0", "google-cloud-storage>=2.18"]\ndev = ["pytest>=8", "httpx>=0.28", "ruff>=0.8"]\n',
            f"{pkg}/__init__.py": "",
            f"{pkg}/config.py": "from pydantic_settings import BaseSettings\n\n\nclass Settings(BaseSettings):\n    environment: str = \"development\"\n"
                                "    gcp_project: str = \"\"\n    ai_backend: str = \"none\"\n    gemini_api_key: str = \"\"\n\n\nsettings = Settings()\n",
            f"{pkg}/main.py": "from fastapi import FastAPI\n\napp = FastAPI()\n\n\n@app.get(\"/health\")\ndef health() -> dict:\n    return {\"ok\": True}\n",
            "tests/test_health.py": f"from fastapi.testclient import TestClient\n\nfrom {pkg}.main import app\n\n\ndef test_health():\n"
                                    "    assert TestClient(app).get(\"/health\").json() == {\"ok\": True}\n",
            "Dockerfile": f"FROM python:3.12-slim\nWORKDIR /app\nCOPY . .\nRUN pip install --no-cache-dir .\nUSER 10001\n"
                          f"CMD [\"uvicorn\",\"{pkg}.main:app\",\"--host\",\"0.0.0.0\",\"--port\",\"8080\"]\n",
        })
        files[".github/workflows/ci.yml"] += "      - uses: actions/setup-python@v5\n        with: { python-version: '3.12' }\n      - run: pip install '.[dev]' && pytest -q\n"
    files["infra/deploy.sh"] = (f"#!/usr/bin/env bash\nset -euo pipefail\n: \"${{GCP_PROJECT:?set GCP_PROJECT}}\"\n"
                                f"gcloud run deploy {slug} --source . --project \"$GCP_PROJECT\" --region \"${{GCP_LOCATION:-us-central1}}\" --no-allow-unauthenticated\n")
    return {"name": name, "language": language, "files": [{"path": p, "content": c} for p, c in sorted(files.items())], "source": "rules"}


# ── 5. stories ────────────────────────────────────────────────────────────

def stories(requirements_list: list[str], persona: str = "user") -> dict[str, Any]:
    out = []
    for i, req in enumerate(r for r in requirements_list if r.strip()):
        body = re.sub(r"^(FR-\d+:\s*)", "", req).strip()
        body = re.sub(r"^(the system shall|users can|the system)\s+", "", body, flags=re.I).rstrip(".")
        words = len(body.split())
        fr = re.match(r"(FR-\d+)", req)
        out.append({
            "id": f"ST-{i + 1}", "requirement": fr.group(1) if fr else f"FR-{i + 1}", "title": body[:70][0].upper() + body[1:70] if body else f"Story {i + 1}",
            "as_a": persona, "i_want": body, "so_that": "I can achieve my goal without manual workarounds",
            "acceptance": [f"Given a signed-in {persona}, when they {body[:60]}, then the result is saved and confirmed.",
                           "Given invalid input, then a clear validation error is shown and nothing is saved.",
                           "The action is recorded in the audit log."],
            "points": 2 if words < 8 else 3 if words < 15 else 5,
            "priority": "Must" if i < 3 else "Should" if i < 7 else "Could",
        })
    for story in out:
        story["invest_warnings"] = invest(story)
    st = _obj({"id": _STR, "requirement": _STR, "title": _STR, "as_a": _STR, "i_want": _STR, "so_that": _STR, "acceptance": _STRS,
               "points": {"type": "integer"}, "priority": _STR})
    return _refine("feature story creation (INVEST, Gherkin acceptance)", {"requirements": requirements_list, "persona": persona},
                   {"stories": out}, _obj({"stories": {"type": "array", "items": st}}))


# ── 6. code analysis (health, visualisation, tests) ───────────────────────

def _complexity(node: ast.AST) -> int:
    branches = (ast.If, ast.For, ast.While, ast.Try, ast.With, ast.BoolOp, ast.IfExp, ast.ExceptHandler, ast.comprehension, ast.Match)
    return 1 + sum(isinstance(n, branches) for n in ast.walk(node))


def _parse(code: str) -> ast.Module:
    try:
        return ast.parse(code)
    except SyntaxError as exc:
        raise ValueError(f"Python syntax error on line {exc.lineno}: {exc.msg}") from exc


_FN = re.compile(r"(?:function\s+(\w+)|(?:const|let)\s+(\w+)\s*=\s*(?:async\s*)?\(|(?:public|private|static|\s)\s*[\w<>\[\]]+\s+(\w+)\s*\([^)]*\)\s*\{|func\s+(\w+)|def\s+(\w+))")


def _generic_functions(code: str) -> list[str]:
    return [next(g for g in m.groups() if g) for m in _FN.finditer(code)]


def code_health(code: str, language: str = "python") -> dict[str, Any]:
    lines = code.splitlines()
    issues: list[dict[str, Any]] = []
    functions: list[dict[str, Any]] = []
    if language == "python":
        tree = _parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                length = (node.end_lineno or node.lineno) - node.lineno + 1
                cx = _complexity(node)
                functions.append({"name": node.name, "line": node.lineno, "length": length, "complexity": cx,
                                  "documented": ast.get_docstring(node) is not None})
                if cx > 10:
                    issues.append({"line": node.lineno, "severity": "high", "rule": "complexity", "message": f"{node.name}: cyclomatic complexity {cx} > 10"})
                if length > 50:
                    issues.append({"line": node.lineno, "severity": "medium", "rule": "long-function", "message": f"{node.name}: {length} lines"})
                if len(node.args.args) > 5:
                    issues.append({"line": node.lineno, "severity": "low", "rule": "many-params", "message": f"{node.name}: {len(node.args.args)} parameters"})
                for d in node.args.defaults:
                    if isinstance(d, ast.List | ast.Dict | ast.Set):
                        issues.append({"line": node.lineno, "severity": "high", "rule": "mutable-default", "message": f"{node.name}: mutable default argument"})
            elif isinstance(node, ast.ExceptHandler) and node.type is None:
                issues.append({"line": node.lineno, "severity": "medium", "rule": "bare-except", "message": "bare except hides errors"})
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"eval", "exec"}:
                issues.append({"line": node.lineno, "severity": "high", "rule": "security", "message": f"use of {node.func.id}()"})
    else:
        functions = [{"name": n, "line": None, "length": None, "complexity": None, "documented": None} for n in _generic_functions(code)]
    for i, line in enumerate(lines, 1):
        if re.search(r"\b(TODO|FIXME|HACK|XXX)\b", line):
            issues.append({"line": i, "severity": "info", "rule": "todo", "message": line.strip()[:120]})
        if len(line) > 120:
            issues.append({"line": i, "severity": "low", "rule": "line-length", "message": f"{len(line)} characters"})
        if re.search(r"(api[_-]?key|secret|password|token)\s*[:=]\s*['\"][^'\"]{8,}", line, re.I):
            issues.append({"line": i, "severity": "high", "rule": "hardcoded-secret", "message": "possible hard-coded credential"})
    weights = {"high": 15, "medium": 7, "low": 2, "info": 1}
    score = max(0, 100 - sum(weights[i["severity"]] for i in issues))
    draft = {
        "language": language, "lines": len(lines), "code_lines": sum(1 for x in lines if x.strip() and not x.strip().startswith(("#", "//"))),
        "functions": functions, "issues": sorted(issues, key=lambda i: (list(weights).index(i["severity"]), i["line"] or 0)),
        "score": score, "grade": "A" if score >= 90 else "B" if score >= 75 else "C" if score >= 60 else "D" if score >= 40 else "F",
        "recommendations": [],
    }
    return _refine("code health analysis: add actionable recommendations only", {"code": code[:15000], "language": language},
                   draft, _obj({"recommendations": _STRS}))


def visualize(code: str, language: str = "python") -> dict[str, Any]:
    if language != "python":
        names = _generic_functions(code)
        lines = ["flowchart TD", "  module([module])"] + [f"  module --> f{i}[{n}]" for i, n in enumerate(names)]
        return {"mermaid": "\n".join(lines), "kind": "functions", "source": "rules"}
    tree = _parse(code)
    defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)}
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
    out = ["classDiagram"] if classes else ["flowchart TD"]
    if classes:
        for c in classes:
            out.append(f"  class {c.name} {{")
            out += [f"    +{m.name}()" for m in c.body if isinstance(m, ast.FunctionDef | ast.AsyncFunctionDef)]
            out.append("  }")
            out += [f"  {b.id} <|-- {c.name}" for b in c.bases if isinstance(b, ast.Name) and b.id in defined]
    funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]
    edges: set[tuple[str, str]] = set()
    for f in funcs + [m for c in classes for m in c.body if isinstance(m, ast.FunctionDef | ast.AsyncFunctionDef)]:
        for call in ast.walk(f):
            if isinstance(call, ast.Call):
                callee = call.func.id if isinstance(call.func, ast.Name) else call.func.attr if isinstance(call.func, ast.Attribute) else None
                if callee and callee in defined and callee != f.name:
                    edges.add((f.name, callee))
    if classes:
        class_names = {c.name for c in classes}
        if funcs:  # module-level functions as one pseudo-class, linked to the classes they use
            out.append("  class module {")
            out += [f"    +{f.name}()" for f in funcs]
            out.append("  }")
            used = sorted({b for a, b in edges if b in class_names and a in {f.name for f in funcs}})
            out += [f"  module ..> {c} : uses" for c in used]
    else:
        out += [f"  {f.name}[{f.name}]" for f in funcs] + [f"  {a} --> {b}" for a, b in sorted(edges)]
    return {"mermaid": "\n".join(out), "kind": "classes" if classes else "call-graph", "calls": [{"from": a, "to": b} for a, b in sorted(edges)], "source": "rules"}


def unit_tests(code: str, language: str = "python", module: str = "module") -> dict[str, Any]:
    if language == "python":
        tree = _parse(code)
        names = [n for n in tree.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and not n.name.startswith("_")]
        classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
        imports = [n.name for n in names] + [c.name for c in classes]
        body = ["import pytest", ""] + ([f"from {module} import {', '.join(imports)}", ""] if imports else [])
        for f in names:
            args = [a.arg for a in f.args.args if a.arg not in {"self", "cls"}]
            call = f"{'await ' if isinstance(f, ast.AsyncFunctionDef) else ''}{f.name}({', '.join(f'{a}=None' for a in args)})"
            deco = ["@pytest.mark.asyncio"] if isinstance(f, ast.AsyncFunctionDef) else []
            prefix = "async def" if isinstance(f, ast.AsyncFunctionDef) else "def"
            raises = any(isinstance(n, ast.Raise) for n in ast.walk(f))
            body += deco + [f"{prefix} test_{f.name}_returns_expected_value():", "    # Arrange: replace None with realistic inputs",
                            f"    result = {call}", "    assert result is not None  # TODO: assert the exact expected value", "", ""]
            if raises:
                body += deco + [f"{prefix} test_{f.name}_rejects_invalid_input():", "    with pytest.raises(Exception):",
                                f"        {call}", "", ""]
        for c in classes:
            body += [f"def test_{c.name.lower()}_can_be_constructed():", f"    assert {c.name} is not None  # TODO: construct with valid args", "", ""]
        draft = {"framework": "pytest", "filename": f"test_{module}.py", "code": "\n".join(body).rstrip() + "\n", "count": len(names) + len(classes)}
    else:
        names = _generic_functions(code)
        body = [f"import {{ describe, it, expect }} from 'vitest';\nimport {{ {', '.join(names)} }} from './{module}';\n"]
        body += [f"describe('{n}', () => {{\n  it('returns the expected value', () => {{\n    expect({n}()).toBeDefined();\n  }});\n}});\n" for n in names]
        draft = {"framework": "vitest", "filename": f"{module}.test.ts", "code": "\n".join(body), "count": len(names)}
    return _refine("unit test generation: return complete runnable tests with concrete inputs and assertions, edge cases and mocks for I/O",
                   {"code": code[:15000], "language": language, "module": module}, draft, _obj({"code": _STR}))


def invest(story: dict[str, Any]) -> list[str]:
    """Cheap INVEST checks on a story."""
    warn = []
    want = str(story.get("i_want", ""))
    if int(story.get("points") or 0) > 8:
        warn.append("Small: more than 8 points; split the story.")
    if re.search(r"\b(and|also|as well as)\b", want, re.I) and len(want.split()) > 12:
        warn.append("Independent/Small: covers several behaviours; consider splitting.")
    if len(story.get("acceptance") or []) < 2:
        warn.append("Testable: add at least two acceptance criteria.")
    if lint_requirements([want]):
        warn.append("Testable: the goal uses vague wording.")
    return warn


TOOLS: dict[str, Callable[..., dict[str, Any]]] = {
    "requirements": requirements, "stack": stack, "architecture": architecture, "scaffold": scaffold,
    "stories": stories, "code-health": code_health, "visualize": visualize, "tests": unit_tests,
}
