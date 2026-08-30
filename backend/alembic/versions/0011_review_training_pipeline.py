"""Add immutable expert review and governed training dataset pipeline.

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-28
"""

import sqlalchemy as sa

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("building_elements")}
    if "revision" not in columns:
        op.add_column(
            "building_elements",
            sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        )
    if "supersedes_element_id" not in columns:
        op.add_column(
            "building_elements", sa.Column("supersedes_element_id", sa.String(36), nullable=True)
        )
    if op.get_bind().dialect.name != "sqlite":
        op.create_foreign_key(
            "fk_building_elements_supersedes_element_id_building_elements",
            "building_elements",
            "building_elements",
            ["supersedes_element_id"],
            ["id"],
            ondelete="SET NULL",
        )
    indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("building_elements")}
    if "ix_building_elements_supersedes_element_id" not in indexes:
        op.create_index(
            "ix_building_elements_supersedes_element_id",
            "building_elements",
            ["supersedes_element_id"],
        )
    required_tables = {
        "review_decisions",
        "training_project_manifests",
        "anonymized_derivatives",
        "training_samples",
        "active_learning_items",
        "dataset_versions",
        "dataset_memberships",
    }
    if required_tables.issubset(set(sa.inspect(op.get_bind()).get_table_names())):
        return

    op.create_table(
        "review_decisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("subject_type", sa.String(30), nullable=False),
        sa.Column("subject_id", sa.String(36), nullable=False),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("from_revision", sa.Integer(), nullable=False),
        sa.Column("to_revision", sa.Integer(), nullable=False),
        sa.Column("resulting_subject_id", sa.String(36)),
        sa.Column("reviewer", sa.String(200), nullable=False),
        sa.Column("reviewer_role", sa.String(30), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("original_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("corrected_snapshot_json", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("project_id", "subject_type", "subject_id", "action", "resulting_subject_id"):
        op.create_index(f"ix_review_decisions_{column}", "review_decisions", [column])

    op.create_table(
        "training_project_manifests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("rights_status", sa.String(40), nullable=False),
        sa.Column("training_allowed", sa.Boolean(), nullable=False),
        sa.Column("building_use", sa.String(120)),
        sa.Column("gross_area", sa.Float()),
        sa.Column("footprint_area", sa.Float()),
        sa.Column("floor_count", sa.Integer()),
        sa.Column("basement_count", sa.Integer()),
        sa.Column("location_region", sa.String(160)),
        sa.Column("structural_system", sa.String(160)),
        sa.Column("foundation_type", sa.String(160)),
        sa.Column("soil_info", sa.Text()),
        sa.Column("architectural_documents", sa.Boolean(), nullable=False),
        sa.Column("structural_documents", sa.Boolean(), nullable=False),
        sa.Column("mep_documents", sa.Boolean(), nullable=False),
        sa.Column("geotechnical_documents", sa.Boolean(), nullable=False),
        sa.Column("boq_available", sa.Boolean(), nullable=False),
        sa.Column("final_account_available", sa.Boolean(), nullable=False),
        sa.Column("as_built_available", sa.Boolean(), nullable=False),
        sa.Column("verified_quantity_available", sa.Boolean(), nullable=False),
        sa.Column("data_completeness_score", sa.Integer(), nullable=False),
        sa.Column("anonymization_status", sa.String(30), nullable=False),
        sa.Column("eligibility_override", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_training_project_manifests_project_id", "training_project_manifests", ["project_id"]
    )
    op.create_index(
        "ix_training_project_manifests_rights_status",
        "training_project_manifests",
        ["rights_status"],
    )

    op.create_table(
        "anonymized_derivatives",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_document_version_id",
            sa.String(36),
            sa.ForeignKey("document_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("anonymized_document_id", sa.String(36), nullable=False, unique=True),
        sa.Column("anonymization_version", sa.String(40), nullable=False),
        sa.Column("anonymized_metadata_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "source_document_version_id",
            "anonymization_version",
            name="uq_anonymized_source_version",
        ),
    )
    op.create_index(
        "ix_anonymized_derivatives_project_id", "anonymized_derivatives", ["project_id"]
    )
    op.create_index(
        "ix_anonymized_derivatives_source_document_version_id",
        "anonymized_derivatives",
        ["source_document_version_id"],
    )

    op.create_table(
        "training_samples",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_document_id",
            sa.String(36),
            sa.ForeignKey("document_versions.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "source_sheet_id", sa.String(36), sa.ForeignKey("sheets.id", ondelete="SET NULL")
        ),
        sa.Column(
            "source_view_region_id",
            sa.String(36),
            sa.ForeignKey("view_regions.id", ondelete="SET NULL"),
        ),
        sa.Column("task_type", sa.String(50), nullable=False),
        sa.Column("input_reference", sa.String(500), nullable=False),
        sa.Column("annotation_json", sa.JSON(), nullable=False),
        sa.Column("source_prediction", sa.JSON(), nullable=False),
        sa.Column("verified_output", sa.JSON(), nullable=False),
        sa.Column("review_revision", sa.Integer(), nullable=False),
        sa.Column("reviewer", sa.String(200), nullable=False),
        sa.Column("rights_status", sa.String(40), nullable=False),
        sa.Column("anonymization_status", sa.String(30), nullable=False),
        sa.Column("dataset_version_id", sa.String(36)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("project_id", "source_document_id", "task_type", "dataset_version_id"):
        op.create_index(f"ix_training_samples_{column}", "training_samples", [column])

    op.create_table(
        "active_learning_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_reference", sa.String(500), nullable=False),
        sa.Column("prediction", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(40), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
    )
    for column in ("project_id", "source_reference", "priority", "reason", "status"):
        op.create_index(f"ix_active_learning_items_{column}", "active_learning_items", [column])

    op.create_table(
        "dataset_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("dataset_name", sa.String(160), nullable=False),
        sa.Column("version", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("project_count", sa.Integer(), nullable=False),
        sa.Column("sheet_count", sa.Integer(), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("class_distribution_json", sa.JSON(), nullable=False),
        sa.Column("rights_summary_json", sa.JSON(), nullable=False),
        sa.Column("task_types_json", sa.JSON(), nullable=False),
        sa.Column(
            "parent_version_id",
            sa.String(36),
            sa.ForeignKey("dataset_versions.id", ondelete="SET NULL"),
        ),
        sa.Column("checksum", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("dataset_name", "version", name="uq_dataset_name_version"),
    )
    op.create_index("ix_dataset_versions_dataset_name", "dataset_versions", ["dataset_name"])
    op.create_index("ix_dataset_versions_status", "dataset_versions", ["status"])

    op.create_table(
        "dataset_memberships",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "dataset_version_id",
            sa.String(36),
            sa.ForeignKey("dataset_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "sample_id",
            sa.String(36),
            sa.ForeignKey("training_samples.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            sa.String(36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("dataset_split", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("dataset_version_id", "sample_id", name="uq_dataset_sample"),
    )
    for column in ("dataset_version_id", "sample_id", "project_id", "dataset_split"):
        op.create_index(f"ix_dataset_memberships_{column}", "dataset_memberships", [column])


def downgrade() -> None:
    op.drop_table("dataset_memberships")
    op.drop_table("dataset_versions")
    op.drop_table("active_learning_items")
    op.drop_table("training_samples")
    op.drop_table("anonymized_derivatives")
    op.drop_table("training_project_manifests")
    op.drop_table("review_decisions")
    op.drop_index("ix_building_elements_supersedes_element_id", table_name="building_elements")
    if op.get_bind().dialect.name != "sqlite":
        op.drop_constraint(
            "fk_building_elements_supersedes_element_id_building_elements",
            "building_elements",
            type_="foreignkey",
        )
    op.drop_column("building_elements", "supersedes_element_id")
    op.drop_column("building_elements", "revision")
