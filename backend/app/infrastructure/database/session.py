"""Engine and session factory, shared by the API, migrations, scripts and tests."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


def make_engine(url: str | None = None) -> Engine:
    url = url or get_settings().database_url
    if url.startswith("sqlite"):
        if ":memory:" not in url and "///" in url:
            Path(url.split("///", 1)[1]).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, connect_args={"check_same_thread": False})

        # SQLAlchemy's documented recipe: let SQLAlchemy own BEGIN so SAVEPOINTs work on pysqlite.
        @event.listens_for(engine, "connect")
        def _sqlite_connect(dbapi_connection, _record):  # pragma: no cover - driver hook
            dbapi_connection.isolation_level = None
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

        @event.listens_for(engine, "begin")
        def _sqlite_begin(connection):  # pragma: no cover - driver hook
            connection.exec_driver_sql("BEGIN")

        return engine
    settings = get_settings()
    connect_args: dict = {}
    if url.startswith("postgresql+psycopg"):
        # Bounded connect and per-statement time so a stuck Cloud SQL call cannot pin a worker.
        connect_args = {
            "connect_timeout": settings.db_connect_timeout_seconds,
            "options": f"-c statement_timeout={settings.db_statement_timeout_ms}",
            "application_name": settings.service_name,
        }
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout_seconds,
        pool_recycle=1800,  # below Cloud SQL's idle-connection cutoff
        connect_args=connect_args,
    )


def pool_status() -> dict:
    """Connection-pool occupancy, for readiness and the exhaustion alert."""
    pool = engine().pool
    try:
        return {"size": pool.size(), "checked_out": pool.checkedout(), "overflow": pool.overflow()}  # type: ignore[attr-defined]
    except AttributeError:
        return {"size": None, "checked_out": None, "overflow": None}


_engine: Engine | None = None
_factory: sessionmaker[Session] | None = None


def configure(url: str | None = None) -> Engine:
    """(Re)bind the process-wide engine. Tests call this with a temporary database."""
    global _engine, _factory
    if _engine is not None:
        _engine.dispose()
    _engine = make_engine(url)
    _factory = sessionmaker(bind=_engine, expire_on_commit=False, autoflush=False)
    return _engine


def engine() -> Engine:
    if _engine is None:
        configure()
    assert _engine is not None
    return _engine


def session_factory() -> sessionmaker[Session]:
    if _factory is None:
        configure()
    assert _factory is not None
    return _factory


def get_db() -> Iterator[Session]:
    db = session_factory()()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
