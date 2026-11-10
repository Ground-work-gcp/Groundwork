"""One test per Google Cloud service, each asserting on a real provider response."""

from __future__ import annotations

import hashlib
import uuid

import pytest

SYNTHETIC_DEED = "WARRANTY DEED\nGrantor: SAMPLE SELLER\nGrantee: SAMPLE BUYER\nParcel 00-000-0000\nRecorded 1978-03-14"


def _pdf(text: str) -> bytes:
    from tests.helpers import simple_pdf

    return simple_pdf([text])


def test_identity_is_application_default_credentials_not_a_key_file(settings):
    import os

    import google.auth

    assert not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"), "service-account key files are not allowed; use ADC / Workload Identity"
    credentials, project = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    assert credentials is not None
    assert (project or settings.gcp_project) == settings.gcp_project


def test_cloud_storage_bucket_is_private_and_round_trips_with_integrity(settings):
    from app.infrastructure.google_cloud import storage as gcs

    if not settings.gcs_bucket:
        pytest.fail("GCS_BUCKET is not set")
    posture = gcs.bucket_security()
    assert posture["uniform_bucket_level_access"] is True
    assert posture["public_access_prevention"] == "enforced"

    data = f"groundwork live test {uuid.uuid4()}".encode()
    digest = hashlib.sha256(data).hexdigest()
    key = f"live-tests/{digest}/probe.txt"
    uri = gcs.put(key, data, "text/plain", {"purpose": "live-test"})
    assert gcs.get(uri, digest) == data
    listed = gcs.list_objects("live-tests/")
    assert any(o["key"] == key and o["sha256"] == digest and o["content_type"] == "text/plain" for o in listed)
    with pytest.raises(gcs.IntegrityError):
        gcs.put(key, data + b"changed", "text/plain")
    gcs._bucket().blob(key).delete()  # the probe object this test created


def test_document_ai_processor_is_enabled_and_extracts_a_synthetic_deed(settings):
    from app.infrastructure.google_cloud import documentai

    if not settings.documentai_processor_id:
        pytest.fail("DOCUMENTAI_PROCESSOR_ID is not set")
    info = documentai.describe_processor()
    assert info["state"] == "ENABLED"
    pages = documentai.process(_pdf(SYNTHETIC_DEED), "application/pdf")
    assert pages and pages[0][0] == 1
    assert "WARRANTY DEED" in pages[0][1].upper() and "SAMPLE BUYER" in pages[0][1].upper()


def test_gemini_on_vertex_answers(settings):
    from app.infrastructure.google_cloud import gemini

    text = gemini.generate("Reply with exactly the word GROUNDWORK and nothing else.")
    assert "GROUNDWORK" in text.upper()
    data = gemini.generate_json("Return the number seven.", schema={"type": "OBJECT", "properties": {"n": {"type": "INTEGER"}}, "required": ["n"]})
    assert data == {"n": 7}


def test_sensitive_data_protection_inspects_and_deidentifies(settings):
    from app.infrastructure.google_cloud import dlp

    sample = "Contact sample.person@example.com or (206) 555-0142 about the parcel."
    summary = dlp.inspect_pages([(1, sample)])
    assert summary["engine"] == "google:sensitive-data-protection"
    assert summary["by_type"].get("EMAIL_ADDRESS", 0) >= 1
    protected = dlp.deidentify(sample)
    assert "sample.person@example.com" not in protected and "[EMAIL_ADDRESS]" in protected


def test_secret_manager_secrets_exist_with_enabled_versions(settings):
    """Metadata only — the secret payloads are never read by this test."""
    import google.auth
    from google.auth.transport.requests import AuthorizedSession

    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    session = AuthorizedSession(credentials)
    import os

    env = os.environ.get("GROUNDWORK_ENV", "staging")
    for name in ("groundwork-jwt-secret", f"groundwork-database-url-{env}", "groundwork-webhook-secret", "groundwork-documentai-processor"):
        url = f"https://secretmanager.googleapis.com/v1/projects/{settings.gcp_project}/secrets/{name}/versions/latest"
        response = session.get(url, timeout=30)
        assert response.status_code == 200, f"{name}: HTTP {response.status_code}"
        assert response.json()["state"] == "ENABLED"


def test_cloud_sql_postgres_is_reachable_and_migrated(settings):
    from sqlalchemy import create_engine, text

    if not settings.database_url.startswith("postgresql"):
        pytest.fail("DATABASE_URL must point at Cloud SQL for this test")
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    with engine.connect() as connection:
        version = connection.execute(text("SHOW server_version")).scalar()
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert int(str(version).split(".")[0]) >= 15
    assert revision
