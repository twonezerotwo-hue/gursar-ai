"""Add customer job lifecycle status.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-23
"""

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("customer_jobs")}
    if "status" not in columns:
        op.add_column(
            "customer_jobs",
            sa.Column("status", sa.String(length=20), nullable=False, server_default="ACTIVE"),
        )
        op.execute("UPDATE customer_jobs SET status = 'PENDING' WHERE quantity IS NULL")
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("customer_jobs")}
    if "ix_customer_jobs_status" not in indexes:
        op.create_index("ix_customer_jobs_status", "customer_jobs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_customer_jobs_status", table_name="customer_jobs")
    op.drop_column("customer_jobs", "status")
