"""Add deterministic structural load-path analysis.

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-04
"""

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

TABLES = ("structural_analysis_runs", "structural_loads")


def upgrade() -> None:
    bind = op.get_bind()
    from app.core.database import Base
    from app.infrastructure import model_registry  # noqa: F401

    for table_name in TABLES:
        Base.metadata.tables[table_name].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    from sqlalchemy import inspect

    existing = set(inspect(bind).get_table_names())
    for table_name in reversed(TABLES):
        if table_name in existing:
            op.drop_table(table_name)
