"""engineering studio projects and artifacts

Revision ID: 0005
Revises: 0004
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "studio_projects",
        sa.Column("organization_id", sa.String(length=36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("created_by_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("idea", sa.Text(), nullable=False),
        sa.Column("language", sa.String(length=20), nullable=False),
        sa.Column("scale", sa.String(length=10), nullable=False),
        sa.Column("persona", sa.String(length=60), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_studio_projects_organization_id", "studio_projects", ["organization_id"])
    op.create_table(
        "studio_artifacts",
        sa.Column("project_id", sa.String(length=36), sa.ForeignKey("studio_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(length=12), nullable=False),
        sa.Column("model", sa.String(length=60), nullable=False),
        sa.Column("based_on", sa.JSON(), nullable=False),
        sa.Column("created_by_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("approved_by_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "kind", "version", name="uq_studio_artifact_version"),
    )
    op.create_index("ix_studio_artifacts_project_id", "studio_artifacts", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_studio_artifacts_project_id", table_name="studio_artifacts")
    op.drop_table("studio_artifacts")
    op.drop_index("ix_studio_projects_organization_id", table_name="studio_projects")
    op.drop_table("studio_projects")
