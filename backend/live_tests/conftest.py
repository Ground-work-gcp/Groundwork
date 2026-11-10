"""Live Google Cloud integration tests — run explicitly, never in the default suite.

    cd backend
    $env:GROUNDWORK_LIVE = "1"            # PowerShell
    .venv\\Scripts\\python -m pytest live_tests -v

They read the real configuration from the environment / ``.env`` (GCP_PROJECT, GCS_BUCKET,
DOCUMENTAI_PROCESSOR_ID, GEMINI_MODEL, DATABASE_URL …) and authenticate with Application
Default Credentials (``gcloud auth application-default login``). Each test asserts on an
actual provider response. Without ``GROUNDWORK_LIVE=1`` every test is skipped and reported as
such — a skipped live test is *blocked*, not passed.
"""

from __future__ import annotations

import os

import pytest

from app.core.config import get_settings


def pytest_collection_modifyitems(config, items):
    if os.environ.get("GROUNDWORK_LIVE") == "1":
        return
    skip = pytest.mark.skip(reason="BLOCKED: set GROUNDWORK_LIVE=1 with real Google Cloud credentials to run")
    for item in items:
        item.add_marker(skip)


@pytest.fixture(scope="session")
def settings():
    get_settings.cache_clear()
    s = get_settings()
    if not s.gcp_project:
        pytest.fail("GCP_PROJECT is not set — live tests need a real project")
    return s
