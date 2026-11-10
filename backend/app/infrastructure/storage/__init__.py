"""Object storage for evidence files. Database rows reference objects by URI.

``STORAGE_BACKEND=gcs`` → Cloud Storage (``gs://bucket/key``); ``local`` → a directory on
disk (``local://key``) for development and tests.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from app.core.config import get_settings


def object_key(case_id: str, sha256: str, filename: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", filename)[-120:] or "file"
    return f"cases/{case_id}/{sha256}/{safe}"


def _local_path(key: str) -> Path:
    root = Path(get_settings().local_storage_dir).resolve()
    path = (root / key).resolve()
    if root not in path.parents:
        raise ValueError("object key escapes the storage root")
    return path


class IntegrityError(RuntimeError):
    """Stored bytes do not match the recorded hash, or an original would be overwritten."""


def put(key: str, data: bytes, content_type: str, metadata: dict[str, str] | None = None) -> str:
    """Store an original. Never replaces different bytes under the same key."""
    if get_settings().storage_backend == "gcs":
        from app.infrastructure.google_cloud import storage as gcs

        try:
            return gcs.put(key, data, content_type, metadata)
        except gcs.IntegrityError as exc:
            raise IntegrityError(str(exc)) from exc
    path = _local_path(key)
    if path.exists():
        if hashlib.sha256(path.read_bytes()).digest() != hashlib.sha256(data).digest():
            raise IntegrityError(f"object {key} already exists with different content")
        return f"local://{key}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return f"local://{key}"


def get(uri: str, expected_sha256: str | None = None) -> bytes:
    if uri.startswith("gs://"):
        from app.infrastructure.google_cloud import storage as gcs

        try:
            return gcs.get(uri, expected_sha256)
        except gcs.IntegrityError as exc:
            raise IntegrityError(str(exc)) from exc
    if uri.startswith("local://"):
        data = _local_path(uri[len("local://") :]).read_bytes()
        if expected_sha256 and hashlib.sha256(data).hexdigest() != expected_sha256:
            raise IntegrityError("stored object does not match its recorded SHA-256")
        return data
    raise ValueError("unsupported storage URI")


def status() -> dict[str, str]:
    settings = get_settings()
    if settings.storage_backend == "gcs":
        return {"backend": "gcs", "bucket": settings.gcs_bucket}
    return {"backend": "local", "path": settings.local_storage_dir}
