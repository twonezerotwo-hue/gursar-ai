"""Add real-data lineage and IFC 2D ground-truth records.

Revision ID: 0017
Revises: 0016
Create Date: 2026-08-29
"""

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

TABLES = (
    "upstream_license_lineage",
    "ifc_2d_renders",
    "ifc_2d_annotation_lineage",
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
