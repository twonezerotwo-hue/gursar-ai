"""Add the architectural spatial graph vertical slice.

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-03
"""

import sqlalchemy as sa

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

TABLES = (
    "architectural_zones",
    "architectural_zone_memberships",
    "architecture_analysis_runs",
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    relationship_columns = {
        column["name"] for column in inspector.get_columns("element_relationships")
    }
    if "properties_json" not in relationship_columns:
        op.add_column(
            "element_relationships",
            sa.Column(
                "properties_json",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            ),
        )

    from app.core.database import Base
    from app.infrastructure import model_registry  # noqa: F401

    for table_name in TABLES:
        Base.metadata.tables[table_name].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())
    for table_name in reversed(TABLES):
        if table_name in existing:
            op.drop_table(table_name)
    if "element_relationships" in existing:
        columns = {column["name"] for column in inspector.get_columns("element_relationships")}
        if "properties_json" in columns:
            with op.batch_alter_table("element_relationships") as batch:
                batch.drop_column("properties_json")
