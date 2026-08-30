"""Add trusted construction knowledge acquisition persistence.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-27
"""

import sqlalchemy as sa

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("knowledge_sources"):
        op.create_table(
            "knowledge_sources",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("source_key", sa.String(180), nullable=False, unique=True),
            sa.Column("name", sa.String(500), nullable=False),
            sa.Column("authority", sa.String(300), nullable=False),
            sa.Column("source_url", sa.Text(), nullable=False),
            sa.Column("source_class", sa.String(50), nullable=False),
            sa.Column("quality_score", sa.Integer(), nullable=False),
            sa.Column("license", sa.String(160), nullable=False),
            sa.Column("copyright_status", sa.String(160), nullable=False),
            sa.Column("redistribution_policy", sa.String(30), nullable=False),
            sa.Column("taxonomy_json", sa.JSON(), nullable=False),
            sa.Column("metadata_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        for column in ("source_key", "name", "authority", "source_class", "redistribution_policy"):
            op.create_index(f"ix_knowledge_sources_{column}", "knowledge_sources", [column])
    if not inspector.has_table("knowledge_document_versions"):
        op.create_table(
            "knowledge_document_versions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "source_id",
                sa.String(36),
                sa.ForeignKey("knowledge_sources.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("version_number", sa.Integer(), nullable=False),
            sa.Column("version_label", sa.String(120), nullable=False),
            sa.Column("status", sa.String(30), nullable=False),
            sa.Column("published_date", sa.Date()),
            sa.Column("effective_from", sa.Date()),
            sa.Column("effective_until", sa.Date()),
            sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("activated_at", sa.DateTime(timezone=True)),
            sa.Column("source_url", sa.Text(), nullable=False),
            sa.Column("checksum_sha256", sa.String(64), nullable=False),
            sa.Column("content_type", sa.String(120), nullable=False),
            sa.Column("parser_name", sa.String(100), nullable=False),
            sa.Column("parsed_text", sa.Text(), nullable=False),
            sa.Column("char_count", sa.Integer(), nullable=False),
            sa.Column("ocr_derived", sa.Boolean(), nullable=False),
            sa.Column("etag", sa.String(240)),
            sa.Column("last_modified", sa.String(240)),
            sa.Column("metadata_json", sa.JSON(), nullable=False),
            sa.UniqueConstraint("source_id", "version_number", name="uq_knowledge_source_version"),
        )
        for column in ("source_id", "status", "effective_from", "checksum_sha256"):
            op.create_index(
                f"ix_knowledge_document_versions_{column}", "knowledge_document_versions", [column]
            )
    if not inspector.has_table("knowledge_chunks"):
        op.create_table(
            "knowledge_chunks",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "document_version_id",
                sa.String(36),
                sa.ForeignKey("knowledge_document_versions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("chunk_index", sa.Integer(), nullable=False),
            sa.Column("hierarchy_type", sa.String(40), nullable=False),
            sa.Column("heading", sa.String(500)),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("normalized_content", sa.Text(), nullable=False),
            sa.Column("embedding_json", sa.JSON(), nullable=False),
            sa.Column("taxonomy_json", sa.JSON(), nullable=False),
            sa.Column("checksum_sha256", sa.String(64), nullable=False),
            sa.UniqueConstraint(
                "document_version_id", "chunk_index", name="uq_knowledge_chunk_index"
            ),
        )
        for column in ("document_version_id", "hierarchy_type", "checksum_sha256"):
            op.create_index(f"ix_knowledge_chunks_{column}", "knowledge_chunks", [column])
    if not inspector.has_table("knowledge_rules"):
        op.create_table(
            "knowledge_rules",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("rule_id", sa.String(160), nullable=False, unique=True),
            sa.Column(
                "document_version_id",
                sa.String(36),
                sa.ForeignKey("knowledge_document_versions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("topic", sa.String(120), nullable=False),
            sa.Column("building_type", sa.String(80), nullable=False),
            sa.Column("condition", sa.Text(), nullable=False),
            sa.Column("requirement", sa.Text(), nullable=False),
            sa.Column("unit", sa.String(40)),
            sa.Column("article", sa.String(240)),
            sa.Column("source_class", sa.String(50), nullable=False),
            sa.Column("effective_from", sa.Date()),
            sa.Column("status", sa.String(30), nullable=False),
            sa.Column("extraction_method", sa.String(50), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        for column in ("rule_id", "document_version_id", "topic", "status"):
            op.create_index(f"ix_knowledge_rules_{column}", "knowledge_rules", [column])
    if not inspector.has_table("knowledge_formulas"):
        op.create_table(
            "knowledge_formulas",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("formula_id", sa.String(160), nullable=False, unique=True),
            sa.Column("name", sa.String(300), nullable=False),
            sa.Column("discipline", sa.String(80), nullable=False),
            sa.Column("formula", sa.Text(), nullable=False),
            sa.Column("variables_json", sa.JSON(), nullable=False),
            sa.Column("units_json", sa.JSON(), nullable=False),
            sa.Column("conditions_json", sa.JSON(), nullable=False),
            sa.Column("source", sa.Text(), nullable=False),
            sa.Column("source_class", sa.String(50), nullable=False),
            sa.Column("version", sa.String(80), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False),
        )
        op.create_index("ix_knowledge_formulas_formula_id", "knowledge_formulas", ["formula_id"])
        op.create_index("ix_knowledge_formulas_discipline", "knowledge_formulas", ["discipline"])
        op.create_index("ix_knowledge_formulas_is_active", "knowledge_formulas", ["is_active"])
    if not inspector.has_table("knowledge_conflicts"):
        op.create_table(
            "knowledge_conflicts",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("conflict_key", sa.String(160), nullable=False),
            sa.Column(
                "left_rule_id", sa.String(36), sa.ForeignKey("knowledge_rules.id"), nullable=False
            ),
            sa.Column(
                "right_rule_id", sa.String(36), sa.ForeignKey("knowledge_rules.id"), nullable=False
            ),
            sa.Column("detail_json", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(30), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index(
            "ix_knowledge_conflicts_conflict_key", "knowledge_conflicts", ["conflict_key"]
        )
        op.create_index("ix_knowledge_conflicts_status", "knowledge_conflicts", ["status"])
    if not inspector.has_table("knowledge_changes"):
        op.create_table(
            "knowledge_changes",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "source_id", sa.String(36), sa.ForeignKey("knowledge_sources.id"), nullable=False
            ),
            sa.Column("old_version_id", sa.String(36), nullable=False),
            sa.Column("new_version_id", sa.String(36), nullable=False),
            sa.Column("change_type", sa.String(30), nullable=False),
            sa.Column("section_key", sa.String(500), nullable=False),
            sa.Column("old_text", sa.Text(), nullable=False),
            sa.Column("new_text", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_knowledge_changes_change_type", "knowledge_changes", ["change_type"])
    if not inspector.has_table("knowledge_acquisition_runs"):
        op.create_table(
            "knowledge_acquisition_runs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("status", sa.String(30), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True)),
            sa.Column("counters_json", sa.JSON(), nullable=False),
            sa.Column("failures_json", sa.JSON(), nullable=False),
        )
        op.create_index(
            "ix_knowledge_acquisition_runs_status", "knowledge_acquisition_runs", ["status"]
        )


def downgrade() -> None:
    for table in (
        "knowledge_acquisition_runs",
        "knowledge_changes",
        "knowledge_conflicts",
        "knowledge_formulas",
        "knowledge_rules",
        "knowledge_chunks",
        "knowledge_document_versions",
        "knowledge_sources",
    ):
        op.drop_table(table)
