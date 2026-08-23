"""Add versioned legal-knowledge documents and chunks.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-23
"""

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "legal_documents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_key", sa.String(length=180), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=False),
        sa.Column("authority", sa.String(length=240), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("published_on", sa.Date(), nullable=True),
        sa.Column("official_gazette_number", sa.String(length=80), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("content_type", sa.String(length=120), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_key"),
    )
    op.create_index("ix_legal_documents_category", "legal_documents", ["category"])
    op.create_index("ix_legal_documents_checksum_sha256", "legal_documents", ["checksum_sha256"])
    op.create_index("ix_legal_documents_is_active", "legal_documents", ["is_active"])
    op.create_index("ix_legal_documents_source_key", "legal_documents", ["source_key"])
    op.create_index("ix_legal_documents_title", "legal_documents", ["title"])

    op.create_table(
        "legal_chunks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("heading", sa.String(length=500), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("normalized_content", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["legal_documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "chunk_index", name="uq_legal_chunk_document_index"),
    )
    op.create_index("ix_legal_chunks_document_id", "legal_chunks", ["document_id"])


def downgrade() -> None:
    op.drop_index("ix_legal_chunks_document_id", table_name="legal_chunks")
    op.drop_table("legal_chunks")
    op.drop_index("ix_legal_documents_title", table_name="legal_documents")
    op.drop_index("ix_legal_documents_source_key", table_name="legal_documents")
    op.drop_index("ix_legal_documents_is_active", table_name="legal_documents")
    op.drop_index("ix_legal_documents_checksum_sha256", table_name="legal_documents")
    op.drop_index("ix_legal_documents_category", table_name="legal_documents")
    op.drop_table("legal_documents")
