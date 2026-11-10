"""Test environment: a throwaway database migrated with Alembic, local storage, no cloud.

Set ``GROUNDWORK_TEST_DATABASE_URL`` to run the whole suite against PostgreSQL instead of
SQLite (the schema is dropped and re-migrated at session start).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="groundwork-tests-"))
os.environ["ENVIRONMENT"] = "test"
os.environ["DATABASE_URL"] = os.environ.get("GROUNDWORK_TEST_DATABASE_URL") or f"sqlite:///{(_TMP / 'test.db').as_posix()}"
os.environ["LOCAL_STORAGE_DIR"] = str(_TMP / "storage")
os.environ["STORAGE_BACKEND"] = "local"
os.environ["EXTRACTION_BACKEND"] = "local"
os.environ["AI_BACKEND"] = "none"
os.environ["PII_BACKEND"] = "local"
os.environ["NOTIFY_WEBHOOK_URL"] = ""
os.environ["FRONTEND_DIST"] = str(_TMP / "no-frontend")

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.infrastructure.database.models import Base  # noqa: E402
from app.infrastructure.database.session import configure, session_factory  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session", autouse=True)
def _database():
    get_settings.cache_clear()
    url = get_settings().database_url
    engine = configure(url)
    if url.startswith("postgresql"):
        Base.metadata.drop_all(engine)
        with engine.begin() as connection:
            connection.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    config.cmd_opts = None
    config.attributes["x"] = None
    os.environ["ALEMBIC_URL"] = url
    command.upgrade(config, "head")
    yield engine


@pytest.fixture
def db():
    session = session_factory()()
    try:
        yield session
        session.commit()
    finally:
        session.close()


@pytest.fixture
def app():
    from app.api.v1.deps import credential_limiter
    from app.main import create_app

    credential_limiter.reset()
    return create_app()


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def anon(app):
    return TestClient(app)
