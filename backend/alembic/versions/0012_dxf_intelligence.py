"""Add DXF intelligence analysis, units, and semantic mappings.

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-28
"""

import sqlalchemy as sa

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    required = {
        "dxf_analyses",
        "dxf_unit_resolutions",
        "layer_semantic_mappings",
        "block_semantic_mappings",
    }
    if required.issubset(set(sa.inspect(op.get_bind()).get_table_names())):
        return
    op.create_table(
        "dxf_analyses",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_version_id",
            sa.String(36),
            sa.ForeignKey("document_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("analysis_version", sa.String(80), nullable=False),
        sa.Column("raw_unit", sa.String(30), nullable=False),
        sa.Column("normalized_unit", sa.String(30)),
        sa.Column("unit_status", sa.String(30), nullable=False),
        sa.Column("unit_confidence", sa.Numeric(5, 4), nullable=False),
        sa.Column("entity_count", sa.Integer(), nullable=False),
        sa.Column("recognized_count", sa.Integer(), nullable=False),
        sa.Column("unknown_count", sa.Integer(), nullable=False),
        sa.Column("measurement_count", sa.Integer(), nullable=False),
        sa.Column("summary_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "document_version_id", "analysis_version", name="uq_dxf_analysis_version"
        ),
    )
    op.create_index("ix_dxf_analyses_project_id", "dxf_analyses", ["project_id"])
    op.create_index("ix_dxf_analyses_document_version_id", "dxf_analyses", ["document_version_id"])
    op.create_index("ix_dxf_analyses_unit_status", "dxf_analyses", ["unit_status"])
    op.create_table(
        "dxf_unit_resolutions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_version_id",
            sa.String(36),
            sa.ForeignKey("document_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("unit", sa.String(30), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("reviewer", sa.String(200), nullable=False),
        sa.Column("reviewer_role", sa.String(30), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_dxf_unit_resolutions_project_id", "dxf_unit_resolutions", ["project_id"])
    op.create_index(
        "ix_dxf_unit_resolutions_document_version_id",
        "dxf_unit_resolutions",
        ["document_version_id"],
    )
    _create_mapping_table("layer_semantic_mappings", "raw_layer_name")
    _create_mapping_table("block_semantic_mappings", "block_name")


def _create_mapping_table(table: str, source_column: str) -> None:
    op.create_table(
        table,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(source_column, sa.String(300), nullable=False),
        sa.Column("normalized_element_type", sa.String(40), nullable=False),
        sa.Column("scope", sa.String(30), nullable=False),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE")),
        sa.Column("office_profile", sa.String(160)),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.String(200), nullable=False),
        sa.Column("reviewed_by", sa.String(200)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in (
        source_column,
        "normalized_element_type",
        "scope",
        "project_id",
        "office_profile",
        "verified",
    ):
        op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade() -> None:
    op.drop_table("block_semantic_mappings")
    op.drop_table("layer_semantic_mappings")
    op.drop_table("dxf_unit_resolutions")
    op.drop_table("dxf_analyses")
