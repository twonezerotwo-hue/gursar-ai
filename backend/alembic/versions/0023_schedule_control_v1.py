"""Add resource-leveled baselines, actuals, delays and 4D links.

Revision ID: 0023
Revises: 0022
Create Date: 2026-09-25
"""

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

TABLES = (
    "schedule_baselines",
    "schedule_progress_updates",
    "schedule_delay_events",
    "schedule_activity_element_links",
)


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
