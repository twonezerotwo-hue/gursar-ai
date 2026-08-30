"""Add idempotent conversation turn receipts.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-23
"""

import sqlalchemy as sa

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("conversation_turn_receipts"):
        return
    op.create_table(
        "conversation_turn_receipts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        sa.Column("response_payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "conversation_id",
            "idempotency_key",
            name="uq_conversation_turn_receipt_key",
        ),
    )
    op.create_index(
        "ix_conversation_turn_receipts_conversation_id",
        "conversation_turn_receipts",
        ["conversation_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_conversation_turn_receipts_conversation_id",
        table_name="conversation_turn_receipts",
    )
    op.drop_table("conversation_turn_receipts")
