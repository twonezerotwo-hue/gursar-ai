"""Persist conversation-linked document analysis results.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-24
"""

import sqlalchemy as sa

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("document_analyses"):
        return
    op.create_table(
        "document_analyses",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("format", sa.String(length=16), nullable=False),
        sa.Column("media_type", sa.String(length=120), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("tools_used", sa.JSON(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "conversation_id",
            "sha256",
            name="uq_document_analysis_conversation_sha256",
        ),
    )
    op.create_index(
        "ix_document_analyses_conversation_id",
        "document_analyses",
        ["conversation_id"],
    )
    op.create_index("ix_document_analyses_sha256", "document_analyses", ["sha256"])


def downgrade() -> None:
    op.drop_index("ix_document_analyses_sha256", table_name="document_analyses")
    op.drop_index(
        "ix_document_analyses_conversation_id",
        table_name="document_analyses",
    )
    op.drop_table("document_analyses")
