"""Add governed autonomous dataset acquisition.

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-29
"""

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

TABLES = (
    "discovered_dataset_sources",
    "license_assessments",
    "download_manifests",
    "external_project_packages",
    "external_class_mappings",
    "external_annotation_lineage",
    "ifc_harvest_summaries",
    "cross_modal_project_links",
    "synthetic_projects",
    "acquisition_runs",
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
