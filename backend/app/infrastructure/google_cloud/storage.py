"""Cloud Storage object store (replaces InHeir's Azure Blob containers and Magpie's S3).

Evidence integrity rules:
* objects are keyed by content hash and written with ``if_generation_match=0`` — an original
  can never be silently replaced; a second write of the same key is a no-op if the bytes match
  and an error if they do not;
* uploads send an MD5 so Cloud Storage rejects corrupted transfers, and the stored object's
  metadata carries the SHA-256 the database recorded;
* downloads are re-hashed against the expected SHA-256 when the caller supplies it;
* the bucket is private (uniform bucket-level access + public access prevention, set by
  ``infra/scripts/bootstrap.ps1``) and objects are only reachable through the API.
"""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache
from typing import Any

from app.core.config import get_settings
from app.core.resilience import call_provider

PROVIDER = "cloud_storage"


class IntegrityError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _client():
    from google.cloud import storage

    return storage.Client(project=get_settings().gcp_project or None)


def _bucket():
    return _client().bucket(get_settings().gcs_bucket)


def _prefix() -> str:
    return f"gs://{get_settings().gcs_bucket}/"


def put(key: str, data: bytes, content_type: str, metadata: dict[str, str] | None = None) -> str:
    from google.api_core.exceptions import PreconditionFailed

    settings = get_settings()
    sha256 = hashlib.sha256(data).hexdigest()
    blob = _bucket().blob(key, chunk_size=8 * 1024 * 1024 if len(data) > 8 * 1024 * 1024 else None)
    blob.md5_hash = base64.b64encode(hashlib.md5(data).digest()).decode()  # noqa: S324 - transfer checksum, not security
    blob.metadata = {"sha256": sha256, **(metadata or {})}

    def upload() -> str | None:
        """Returns the existing object's SHA-256 when the key is already taken, else None."""
        try:
            blob.upload_from_string(data, content_type=content_type, if_generation_match=0, timeout=settings.provider_timeout_seconds)
            return None
        except PreconditionFailed:
            existing = _bucket().get_blob(key, timeout=settings.provider_timeout_seconds)
            return (existing.metadata or {}).get("sha256", "") if existing is not None else ""

    existing_sha = call_provider(PROVIDER, "put", upload, attempts=settings.provider_attempts, budget_seconds=settings.provider_timeout_seconds * 2)
    if existing_sha is not None and existing_sha != sha256:
        # Decided outside the retry wrapper: an integrity conflict is never retried or re-labelled.
        raise IntegrityError(f"object {key} already exists with different content")
    return f"{_prefix()}{key}"


def get(uri: str, expected_sha256: str | None = None) -> bytes:
    settings = get_settings()
    if not uri.startswith(_prefix()):
        raise ValueError("object is not in the configured bucket")
    key = uri[len(_prefix()) :]
    data = call_provider(
        PROVIDER, "get", lambda: _bucket().blob(key).download_as_bytes(timeout=settings.provider_timeout_seconds),
        attempts=settings.provider_attempts, budget_seconds=settings.provider_timeout_seconds * 2,
    )
    if expected_sha256 and hashlib.sha256(data).hexdigest() != expected_sha256:
        raise IntegrityError("downloaded object does not match its recorded SHA-256")
    return data


def list_objects(prefix: str, limit: int = 1000) -> list[dict[str, Any]]:
    settings = get_settings()

    def run() -> list[dict[str, Any]]:
        blobs = _client().list_blobs(settings.gcs_bucket, prefix=prefix, max_results=limit, timeout=settings.provider_timeout_seconds)
        return [
            {"key": b.name, "size": b.size, "content_type": b.content_type, "sha256": (b.metadata or {}).get("sha256"), "generation": b.generation}
            for b in blobs
        ]

    return call_provider(PROVIDER, "list", run, attempts=settings.provider_attempts)


def bucket_security() -> dict[str, Any]:
    """The bucket's actual access posture, read from Cloud Storage (used by live tests)."""
    settings = get_settings()

    def run() -> dict[str, Any]:
        bucket = _client().get_bucket(settings.gcs_bucket, timeout=settings.provider_timeout_seconds)
        iam = bucket.iam_configuration
        return {
            "uniform_bucket_level_access": bool(iam.uniform_bucket_level_access_enabled),
            "public_access_prevention": str(iam.public_access_prevention),
            "versioning": bool(bucket.versioning_enabled),
        }

    return call_provider(PROVIDER, "bucket_security", run, attempts=2)
