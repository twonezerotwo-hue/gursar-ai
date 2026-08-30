"""Add deterministic estimation and calibration persistence.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-24
"""

import sqlalchemy as sa

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("completed_project_records"):
        op.create_table(
            "completed_project_records",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(120), nullable=False),
            sa.Column("project_type", sa.String(40), nullable=False),
            sa.Column("location", sa.String(240)),
            sa.Column("footprint_area_m2", sa.Numeric(18, 4)),
            sa.Column("gross_area_m2", sa.Numeric(18, 4), nullable=False),
            sa.Column("floor_count", sa.Integer(), nullable=False),
            sa.Column("basement_count", sa.Integer(), nullable=False),
            sa.Column("structural_system", sa.String(60), nullable=False),
            sa.Column("structural_intensity", sa.String(40)),
            sa.Column("average_span_m", sa.Numeric(10, 4)),
            sa.Column("foundation_type", sa.String(40), nullable=False),
            sa.Column("soil_condition", sa.String(40), nullable=False),
            sa.Column("seismic_parameters", sa.JSON(), nullable=False),
            sa.Column("actual_quantities", sa.JSON(), nullable=False),
            sa.Column("estimated_quantities", sa.JSON(), nullable=False),
            sa.Column("source", sa.String(40), nullable=False),
            sa.Column("verified", sa.Boolean(), nullable=False),
            sa.Column("verification_level", sa.String(40), nullable=False),
            sa.Column("outlier_flags", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        for column in (
            "project_id",
            "project_type",
            "structural_system",
            "foundation_type",
            "soil_condition",
            "verified",
        ):
            op.create_index(
                f"ix_completed_project_records_{column}", "completed_project_records", [column]
            )
    if not inspector.has_table("estimation_model_versions"):
        op.create_table(
            "estimation_model_versions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("estimation_model_version", sa.String(80), nullable=False, unique=True),
            sa.Column("coefficient_registry_version", sa.String(80), nullable=False),
            sa.Column("training_dataset_version", sa.String(80), nullable=False),
            sa.Column("status", sa.String(20), nullable=False),
            sa.Column("coefficients", sa.JSON(), nullable=False),
            sa.Column("statistics", sa.JSON(), nullable=False),
            sa.Column("calibration_timestamp", sa.DateTime(timezone=True), nullable=False),
            sa.Column("activated_at", sa.DateTime(timezone=True)),
        )
        op.create_index(
            "ix_estimation_model_versions_status", "estimation_model_versions", ["status"]
        )
    if not inspector.has_table("estimation_runs"):
        op.create_table(
            "estimation_runs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("request_id", sa.String(80), nullable=False, unique=True),
            sa.Column("conversation_id", sa.String(36)),
            sa.Column("input_payload", sa.JSON(), nullable=False),
            sa.Column("result_payload", sa.JSON(), nullable=False),
            sa.Column("assumptions", sa.JSON(), nullable=False),
            sa.Column("estimation_model_version", sa.String(80), nullable=False),
            sa.Column("coefficient_registry_version", sa.String(80), nullable=False),
            sa.Column("training_dataset_version", sa.String(80), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_estimation_runs_request_id", "estimation_runs", ["request_id"])
        op.create_index(
            "ix_estimation_runs_conversation_id", "estimation_runs", ["conversation_id"]
        )


def downgrade() -> None:
    op.drop_table("estimation_runs")
    op.drop_table("estimation_model_versions")
    op.drop_table("completed_project_records")
