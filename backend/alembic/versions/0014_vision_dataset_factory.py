"""Add vision dataset factory, job queue, and measurement purpose.

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-28
"""

import sqlalchemy as sa

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

TABLES = (
    "geometry_artifacts",
    "analysis_jobs",
    "training_image_derivatives",
    "vision_annotations",
    "vision_dataset_items",
    "vision_dataset_exports",
    "vision_model_versions",
    "vision_predictions",
    "detection_conflicts",
    "vision_benchmark_runs",
    "project_archive_imports",
)


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("measurements")}
    if "measurement_purpose" not in columns:
        op.add_column(
            "measurements",
            sa.Column(
                "measurement_purpose",
                sa.String(40),
                nullable=False,
                server_default="ENGINEERING_QUANTITY",
            ),
        )
        op.create_index(
            "ix_measurements_measurement_purpose", "measurements", ["measurement_purpose"]
        )
    from app.core.database import Base
    from app.infrastructure import model_registry  # noqa: F401

    for table_name in TABLES:
        Base.metadata.tables[table_name].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    for table_name in reversed(TABLES):
        if table_name in existing:
            op.drop_table(table_name)
    columns = {column["name"] for column in sa.inspect(bind).get_columns("measurements")}
    if "measurement_purpose" in columns:
        with op.batch_alter_table("measurements") as batch:
            batch.drop_column("measurement_purpose")
