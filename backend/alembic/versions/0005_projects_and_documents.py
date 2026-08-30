"""Add projects, versioned PDF documents, and sheets.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-24
"""

import sqlalchemy as sa

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("projects"):
        op.create_table(
            "projects",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("name", sa.String(length=240), nullable=False),
            sa.Column("locale", sa.String(length=12), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_projects_name", "projects", ["name"])
        op.create_index("ix_projects_status", "projects", ["status"])

    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("documents"):
        op.create_table(
            "documents",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("project_id", sa.String(length=36), nullable=False),
            sa.Column("original_name", sa.String(length=255), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("project_id", "original_name", name="uq_document_project_name"),
        )
        op.create_index("ix_documents_project_id", "documents", ["project_id"])

    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("document_versions"):
        op.create_table(
            "document_versions",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("document_id", sa.String(length=36), nullable=False),
            sa.Column("project_id", sa.String(length=36), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("sha256", sa.String(length=64), nullable=False),
            sa.Column("size_bytes", sa.Integer(), nullable=False),
            sa.Column("media_type", sa.String(length=120), nullable=False),
            sa.Column("storage_key", sa.String(length=500), nullable=False),
            sa.Column("page_count", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("document_id", "version", name="uq_document_version_number"),
            sa.UniqueConstraint("project_id", "sha256", name="uq_document_project_sha256"),
            sa.UniqueConstraint("storage_key"),
        )
        op.create_index("ix_document_versions_document_id", "document_versions", ["document_id"])
        op.create_index("ix_document_versions_project_id", "document_versions", ["project_id"])
        op.create_index("ix_document_versions_sha256", "document_versions", ["sha256"])
        op.create_index("ix_document_versions_status", "document_versions", ["status"])

    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("sheets"):
        op.create_table(
            "sheets",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("document_version_id", sa.String(length=36), nullable=False),
            sa.Column("page_number", sa.Integer(), nullable=False),
            sa.Column("title", sa.String(length=500), nullable=True),
            sa.Column("discipline", sa.String(length=40), nullable=False),
            sa.Column("width_points", sa.Numeric(precision=12, scale=3), nullable=False),
            sa.Column("height_points", sa.Numeric(precision=12, scale=3), nullable=False),
            sa.ForeignKeyConstraint(
                ["document_version_id"],
                ["document_versions.id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "document_version_id",
                "page_number",
                name="uq_sheet_document_version_page",
            ),
        )
        op.create_index(
            "ix_sheets_document_version_id",
            "sheets",
            ["document_version_id"],
        )


def downgrade() -> None:
    op.drop_index("ix_sheets_document_version_id", table_name="sheets")
    op.drop_table("sheets")
    op.drop_index("ix_document_versions_status", table_name="document_versions")
    op.drop_index("ix_document_versions_sha256", table_name="document_versions")
    op.drop_index("ix_document_versions_project_id", table_name="document_versions")
    op.drop_index("ix_document_versions_document_id", table_name="document_versions")
    op.drop_table("document_versions")
    op.drop_index("ix_documents_project_id", table_name="documents")
    op.drop_table("documents")
    op.drop_index("ix_projects_status", table_name="projects")
    op.drop_index("ix_projects_name", table_name="projects")
    op.drop_table("projects")
