"""Add real drawing and cross-modal V3 records.

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-30
"""

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

TABLES = (
    "real_drawing_imports",
    "ifc_geometry_cache",
    "cross_modal_alignments",
    "cross_modal_annotation_lineage",
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
