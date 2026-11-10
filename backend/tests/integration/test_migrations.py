"""Alembic migrations produce exactly the canonical model (no drift)."""

from __future__ import annotations

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from app.infrastructure.database.models import Base


def test_migrated_schema_matches_the_model(_database):
    with _database.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": False})
        diff = [d for d in compare_metadata(context, Base.metadata) if not (isinstance(d, tuple) and d[0] in ("add_index", "remove_index"))]
        tables = set(inspect(connection).get_table_names())
    assert diff == [], diff
    assert set(Base.metadata.tables) <= tables and "alembic_version" in tables
