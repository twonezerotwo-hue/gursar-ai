"""Add source-aware productivity records and crew planning inputs.

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-04
"""

import sqlalchemy as sa

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

TABLE = "productivity_records"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {item["name"] for item in inspector.get_columns(TABLE)}
    additions = (
        sa.Column("work_type", sa.String(length=160), nullable=True),
        sa.Column("crew_composition_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("sample_quantity", sa.Numeric(18, 6), nullable=True),
        sa.Column("sample_duration_days", sa.Numeric(18, 6), nullable=True),
        sa.Column("productive_hours", sa.Numeric(18, 6), nullable=True),
        sa.Column("floor", sa.String(length=100), nullable=True),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=False, server_default="0.5"),
        sa.Column(
            "source_tier",
            sa.String(length=40),
            nullable=False,
            server_default="HEURISTIC",
        ),
    )
    for column in additions:
        if column.name not in columns:
            op.add_column(TABLE, column)

    inspector = sa.inspect(bind)
    indexes = {item["name"] for item in inspector.get_indexes(TABLE)}
    if "ix_productivity_records_work_type" not in indexes:
        op.create_index("ix_productivity_records_work_type", TABLE, ["work_type"])
    if "ix_productivity_records_source_tier" not in indexes:
        op.create_index("ix_productivity_records_source_tier", TABLE, ["source_tier"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = {item["name"] for item in inspector.get_indexes(TABLE)}
    columns = {item["name"] for item in inspector.get_columns(TABLE)}
    with op.batch_alter_table(TABLE) as batch:
        if "ix_productivity_records_source_tier" in indexes:
            batch.drop_index("ix_productivity_records_source_tier")
        if "ix_productivity_records_work_type" in indexes:
            batch.drop_index("ix_productivity_records_work_type")
        for name in (
            "source_tier",
            "confidence",
            "floor",
            "productive_hours",
            "sample_duration_days",
            "sample_quantity",
            "crew_composition_json",
            "work_type",
        ):
            if name in columns:
                batch.drop_column(name)
