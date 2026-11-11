"""Google Cloud adapter behaviour with the real SDK exception types and faked clients.

These prove the adapters' own logic (integrity, limits, retries, failure handling). They do
NOT prove the services work — that is what ``tests/live`` does against a real project.
"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

pytest.importorskip("google.api_core")
from google.api_core.exceptions import PreconditionFailed, ResourceExhausted

from app.core.config import Settings, get_settings
from app.core.resilience import ProviderError


@pytest.fixture
def gcp_env(monkeypatch):
    monkeypatch.setenv("GCP_PROJECT", "demo-project")
    monkeypatch.setenv("GCS_BUCKET", "demo-bucket")
    monkeypatch.setenv("DOCUMENTAI_PROCESSOR_ID", "abc123")
    monkeypatch.setenv("PROVIDER_ATTEMPTS", "3")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class FakeBlob:
    def __init__(self, store, key):
        self.store, self.name, self.metadata, self.md5_hash = store, key, None, None

    def upload_from_string(self, data, content_type, if_generation_match, timeout):
        assert if_generation_match == 0 and timeout > 0
        if self.name in self.store:
            raise PreconditionFailed("exists")
        self.store[self.name] = (data, dict(self.metadata or {}))

    def download_as_bytes(self, timeout):
        return self.store[self.name][0]


class FakeBucket:
    def __init__(self):
        self.store = {}

    def blob(self, key, chunk_size=None):
        return FakeBlob(self.store, key)

    def get_blob(self, key, timeout):
        if key not in self.store:
            return None
        return SimpleNamespace(metadata=self.store[key][1])


def test_storage_never_replaces_an_original(gcp_env, monkeypatch):
    from app.infrastructure.google_cloud import storage as gcs

    bucket = FakeBucket()
    monkeypatch.setattr(gcs, "_bucket", lambda: bucket)
    uri = gcs.put("cases/c1/h/deed.pdf", b"original", "application/pdf", {"case_id": "c1"})
    assert uri == "gs://demo-bucket/cases/c1/h/deed.pdf"
    assert bucket.store["cases/c1/h/deed.pdf"][1] == {"sha256": hashlib.sha256(b"original").hexdigest(), "case_id": "c1"}
    assert gcs.put("cases/c1/h/deed.pdf", b"original", "application/pdf") == uri  # idempotent re-upload
    with pytest.raises(gcs.IntegrityError):
        gcs.put("cases/c1/h/deed.pdf", b"tampered", "application/pdf")
    assert bucket.store["cases/c1/h/deed.pdf"][0] == b"original"


def test_storage_download_is_hash_verified(gcp_env, monkeypatch):
    from app.infrastructure.google_cloud import storage as gcs

    bucket = FakeBucket()
    bucket.store["k"] = (b"bytes", {})
    monkeypatch.setattr(gcs, "_bucket", lambda: bucket)
    assert gcs.get("gs://demo-bucket/k", hashlib.sha256(b"bytes").hexdigest()) == b"bytes"
    with pytest.raises(gcs.IntegrityError):
        gcs.get("gs://demo-bucket/k", "0" * 64)
    with pytest.raises(ValueError):
        gcs.get("gs://another-bucket/k")


def test_documentai_rejects_oversize_and_long_documents_before_calling(gcp_env, monkeypatch):
    from app.infrastructure.google_cloud import documentai
    from tests.helpers import simple_pdf

    monkeypatch.setenv("DOCUMENTAI_MAX_PAGES", "2")
    monkeypatch.setenv("DOCUMENTAI_MAX_MB", "1")
    get_settings.cache_clear()
    monkeypatch.setattr(documentai, "_client", lambda: pytest.fail("must not call Document AI"))
    with pytest.raises(documentai.DocumentRejected):
        documentai.process(b"x" * (2 * 1024 * 1024), "application/pdf")
    with pytest.raises(documentai.DocumentRejected):
        documentai.process(simple_pdf(["a", "b", "c"]), "application/pdf")
    with pytest.raises(documentai.DocumentRejected):
        documentai.process(b"text", "text/plain")


def test_documentai_returns_pages_with_provenance_and_retries_throttling(gcp_env, monkeypatch):
    from app.infrastructure.google_cloud import documentai

    seg = lambda a, b: SimpleNamespace(start_index=a, end_index=b)  # noqa: E731
    page = lambda n, a, b, c: SimpleNamespace(page_number=n, layout=SimpleNamespace(confidence=c, text_anchor=SimpleNamespace(text_segments=[seg(a, b)])))  # noqa: E731
    doc = SimpleNamespace(text="WARRANTY DEEDGrantee: Parker", pages=[page(1, 0, 13, 0.98), page(2, 13, 28, 0.91)])
    calls = []

    class Client:
        def process_document(self, request, timeout):
            calls.append(timeout)
            if len(calls) == 1:
                raise ResourceExhausted("429")
            return SimpleNamespace(document=doc)

    monkeypatch.setattr(documentai, "_client", lambda: Client())
    monkeypatch.setattr("app.core.resilience.time.sleep", lambda _s: None)
    pages = documentai.process(b"%PDF-1.4 tiny", "image/png")
    assert pages == [(1, "WARRANTY DEED", 0.98), (2, "Grantee: Parker", 0.91)]
    assert len(calls) == 2 and calls[0] == get_settings().provider_timeout_seconds


def test_extraction_records_document_ai_failure_as_failed_not_extracted(gcp_env, monkeypatch):
    from app.infrastructure.google_cloud import documentai
    from app.services.extraction import extract_text

    monkeypatch.setenv("EXTRACTION_BACKEND", "documentai")
    get_settings.cache_clear()

    class Client:
        def process_document(self, request, timeout):
            raise ResourceExhausted("quota")

    monkeypatch.setattr(documentai, "_client", lambda: Client())
    monkeypatch.setattr("app.core.resilience.time.sleep", lambda _s: None)
    result = extract_text(b"\x89PNG fake", "image/png")
    assert result.status == "failed" and result.pages == [] and "ResourceExhausted" in result.error


def test_gemini_empty_or_blocked_response_raises(gcp_env, monkeypatch):
    from app.infrastructure.google_cloud import gemini

    blocked = SimpleNamespace(text=None, candidates=[SimpleNamespace(finish_reason="SAFETY")])
    monkeypatch.setattr(gemini, "_client", lambda: SimpleNamespace(models=SimpleNamespace(generate_content=lambda **_k: blocked)))
    with pytest.raises(gemini.EmptyResponse, match="SAFETY"):
        gemini.generate("question")


def test_gemini_prompt_is_bounded_and_timeouts_surface_as_provider_error(gcp_env, monkeypatch):
    from app.infrastructure.google_cloud import gemini

    monkeypatch.setenv("GEMINI_MAX_PROMPT_CHARS", "100")
    get_settings.cache_clear()
    seen = {}

    def gen(model, contents, config):
        seen["len"] = len(contents)
        raise TimeoutError("deadline")

    monkeypatch.setattr(gemini, "_client", lambda: SimpleNamespace(models=SimpleNamespace(generate_content=gen)))
    monkeypatch.setattr("app.core.resilience.time.sleep", lambda _s: None)
    with pytest.raises(ProviderError) as info:
        gemini.generate("x" * 10_000)
    assert info.value.transient and seen["len"] <= 100 + len("\n[truncated]")


def test_rag_falls_back_to_passages_when_gemini_fails(gcp_env, monkeypatch):
    """A model outage never produces an invented answer: passages are returned, labelled."""
    from app.services import rag

    hit = SimpleNamespace(document_id="d", filename="deed.txt", page=1, text="Grantee: Parker", score=1.0)
    monkeypatch.setattr(rag, "search_case", lambda *_a, **_k: [hit])
    monkeypatch.setattr(rag, "evidence_status", lambda *_a: {("d", 1): "awaiting_review"})
    monkeypatch.setattr(rag.ai, "enabled", lambda: True)
    monkeypatch.setattr(rag.ai, "generate_text", lambda *_a, **_k: (_ for _ in ()).throw(ProviderError("vertex_gemini", "generate", TimeoutError(), transient=True)))
    out = rag.answer(None, "case", "who owns it?")
    assert out["mode"] == "extractive" and "ProviderError" in out["answer"] and out["citations"][0]["evidence_status"] == "awaiting_review"


def test_dlp_failure_falls_back_to_local_redaction_never_raw(gcp_env, monkeypatch):
    from app.infrastructure.google_cloud import dlp
    from app.services import pii

    monkeypatch.setenv("PII_BACKEND", "dlp")
    get_settings.cache_clear()
    monkeypatch.setattr(dlp, "_client", lambda: SimpleNamespace(deidentify_content=lambda **_k: (_ for _ in ()).throw(PermissionError("denied"))))
    out = pii.protect_for_model("SSN 123-45-6789 on file")
    assert "123-45-6789" not in out and "[REDACTED:US_SOCIAL_SECURITY_NUMBER]" in out


def test_dlp_deidentify_uses_replace_with_info_type(gcp_env, monkeypatch):
    from app.infrastructure.google_cloud import dlp

    captured = {}

    def deid(request, timeout):
        captured.update(request)
        return SimpleNamespace(item=SimpleNamespace(value="call [PHONE_NUMBER]"))

    monkeypatch.setattr(dlp, "_client", lambda: SimpleNamespace(deidentify_content=deid))
    assert dlp.deidentify("call 404-555-0101") == "call [PHONE_NUMBER]"
    names = {t["name"] for t in captured["inspect_config"]["info_types"]}
    assert "US_SOCIAL_SECURITY_NUMBER" in names and "PERSON_NAME" not in names
    assert captured["parent"] == "projects/demo-project/locations/global"


# ── production startup configuration ────────────────────────────────────────


def _prod(**overrides):
    base = dict(
        environment="production", jwt_secret="x" * 48, database_url="postgresql+psycopg://u:p@/db?host=/cloudsql/p:r:i",
        storage_backend="gcs", gcs_bucket="b", gcp_project="p", extraction_backend="documentai", documentai_processor_id="id",
        ai_backend="vertex", pii_backend="dlp", cors_origins="https://groundwork.example.org",
    )
    base.update(overrides)
    return Settings(_env_file=None, **base)


def test_complete_production_configuration_starts():
    _prod().validate_runtime()


@pytest.mark.parametrize(
    "override, message",
    [
        ({"jwt_secret": "dev-only-insecure-secret-change-me-0000000"}, "JWT_SECRET"),
        ({"jwt_secret": "short"}, "JWT_SECRET"),
        ({"database_url": "sqlite:///x.db"}, "PostgreSQL"),
        ({"storage_backend": "local"}, "STORAGE_BACKEND"),
        ({"gcs_bucket": ""}, "GCS_BUCKET"),
        ({"documentai_processor_id": ""}, "DOCUMENTAI_PROCESSOR_ID"),
        ({"gcp_project": "", "extraction_backend": "local", "ai_backend": "vertex"}, "Gemini"),
        ({"gcp_project": "", "extraction_backend": "local", "ai_backend": "none", "pii_backend": "dlp"}, "Sensitive Data Protection"),
        ({"cors_origins": "*"}, "CORS_ORIGINS"),
        ({"cookie_secure": False}, "COOKIE_SECURE"),
        ({"notify_webhook_url": "http://insecure.example"}, "https"),
    ],
)
def test_production_refuses_to_start_on_missing_or_unsafe_secrets(override, message):
    with pytest.raises(RuntimeError, match=message):
        _prod(**override).validate_runtime()
