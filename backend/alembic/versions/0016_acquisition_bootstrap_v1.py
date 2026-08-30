"""Add acquisition bootstrap ranking and checkpoints.

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-29
"""

import sqlalchemy as sa

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    source_columns = {item["name"] for item in inspector.get_columns("discovered_dataset_sources")}
    source_indexes = {item["name"] for item in inspector.get_indexes("discovered_dataset_sources")}
    with op.batch_alter_table("discovered_dataset_sources") as batch:
        if "source_family_id" not in source_columns:
            batch.add_column(sa.Column("source_family_id", sa.String(120), nullable=True))
        if "acquisition_priority_score" not in source_columns:
            batch.add_column(
                sa.Column(
                    "acquisition_priority_score", sa.Integer(), nullable=False, server_default="0"
                )
            )
        if "score_breakdown_json" not in source_columns:
            batch.add_column(
                sa.Column("score_breakdown_json", sa.JSON(), nullable=False, server_default="{}")
            )
        if "ix_discovered_dataset_sources_source_family_id" not in source_indexes:
            batch.create_index(
                "ix_discovered_dataset_sources_source_family_id", ["source_family_id"]
            )
        if "ix_discovered_dataset_sources_acquisition_priority_score" not in source_indexes:
            batch.create_index(
                "ix_discovered_dataset_sources_acquisition_priority_score",
                ["acquisition_priority_score"],
            )
    run_columns = {item["name"] for item in inspector.get_columns("acquisition_runs")}
    run_indexes = {item["name"] for item in inspector.get_indexes("acquisition_runs")}
    with op.batch_alter_table("acquisition_runs") as batch:
        if "mode" not in run_columns:
            batch.add_column(
                sa.Column("mode", sa.String(30), nullable=False, server_default="DISCOVERY_ONLY")
            )
        if "status" not in run_columns:
            batch.add_column(
                sa.Column("status", sa.String(50), nullable=False, server_default="RUNNING")
            )
        if "checkpoint_json" not in run_columns:
            batch.add_column(
                sa.Column("checkpoint_json", sa.JSON(), nullable=False, server_default="{}")
            )
        if "ix_acquisition_runs_mode" not in run_indexes:
            batch.create_index("ix_acquisition_runs_mode", ["mode"])
        if "ix_acquisition_runs_status" not in run_indexes:
            batch.create_index("ix_acquisition_runs_status", ["status"])


def downgrade() -> None:
    with op.batch_alter_table("acquisition_runs") as batch:
        batch.drop_index("ix_acquisition_runs_status")
        batch.drop_index("ix_acquisition_runs_mode")
        batch.drop_column("checkpoint_json")
        batch.drop_column("status")
        batch.drop_column("mode")
    with op.batch_alter_table("discovered_dataset_sources") as batch:
        batch.drop_index("ix_discovered_dataset_sources_acquisition_priority_score")
        batch.drop_index("ix_discovered_dataset_sources_source_family_id")
        batch.drop_column("score_breakdown_json")
        batch.drop_column("acquisition_priority_score")
        batch.drop_column("source_family_id")
