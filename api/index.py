"""Vercel entrypoint: the one Groundwork FastAPI backend as a Python serverless function.

Vercel serves the React build as static files and rewrites /api/*, /health and /ready here.
Until Google Cloud is connected this runs on local implementations: SQLite and file storage
in /tmp, which is EPHEMERAL (per function instance, lost on cold start). Set DATABASE_URL
(PostgreSQL) and the GCP_* / *_BACKEND variables in Vercel to make it persistent.
"""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

os.environ.setdefault("ENVIRONMENT", "development")  # "production" requires Cloud SQL + Cloud Storage
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/groundwork.db")
os.environ.setdefault("LOCAL_STORAGE_DIR", "/tmp/groundwork-storage")
os.environ.setdefault("COOKIE_SECURE", "true")
os.environ.setdefault("FRONTEND_DIST", "/nonexistent")  # Vercel serves the frontend
if not os.environ.get("JWT_SECRET"):
    os.environ["JWT_SECRET"] = secrets.token_urlsafe(48)  # never the dev default on a public URL


def _migrate() -> None:
    from alembic import command
    from alembic.config import Config

    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    os.environ.setdefault("ALEMBIC_URL", os.environ["DATABASE_URL"])
    command.upgrade(config, "head")  # idempotent; forward-only


_migrate()

from app.main import app  # noqa: E402

__all__ = ["app"]
