"""Add conversation ownership, bounded RAG indexes, and project graph entities.

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-27
"""

import sqlalchemy as sa

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "visitor_sessions" not in tables:
        op.create_table(
            "visitor_sessions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "conversation_id",
                sa.String(36),
                sa.ForeignKey("conversations.id", ondelete="CASCADE"),
                nullable=False,
                unique=True,
            ),
            sa.Column("owner_token_sha256", sa.String(64), nullable=False, unique=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index(
            "ix_visitor_sessions_conversation_id", "visitor_sessions", ["conversation_id"]
        )
        op.create_index(
            "ix_visitor_sessions_owner_token_sha256", "visitor_sessions", ["owner_token_sha256"]
        )
        op.create_index("ix_visitor_sessions_expires_at", "visitor_sessions", ["expires_at"])
    if "database_backups" not in tables:
        op.create_table(
            "database_backups",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("database_kind", sa.String(30), nullable=False),
            sa.Column("storage_path", sa.String(500), nullable=False, unique=True),
            sa.Column("checksum_sha256", sa.String(64), nullable=False),
            sa.Column("size_bytes", sa.Integer(), nullable=False),
            sa.Column("validation_status", sa.String(30), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )

    _add_column_if_missing(
        "knowledge_sources",
        "collection",
        sa.Column(
            "collection", sa.String(50), nullable=False, server_default="TECHNICAL_SPECIFICATIONS"
        ),
    )
    op.execute(
        "UPDATE knowledge_sources SET collection = CASE "
        "WHEN source_class IN ('LAW', 'REGULATION') THEN 'REGULATIONS' "
        "WHEN source_class IN ('MANDATORY_STANDARD', 'REFERENCE_STANDARD') THEN 'STANDARDS_METADATA' "
        "WHEN source_class = 'OFFICIAL_UNIT_PRICE' THEN 'YFK_UNIT_PRICES' "
        "WHEN source_class = 'ACADEMIC_REFERENCE' THEN 'ACADEMIC' "
        "WHEN source_class IN ('VERIFIED_PROJECT', 'SITE_EXPERIENCE') THEN 'PROJECT_KNOWLEDGE' "
        "WHEN source_class IN ('MANUFACTURER_DATA', 'PROFESSIONAL_REFERENCE') THEN 'COMPANY_KNOWLEDGE' "
        "ELSE 'TECHNICAL_SPECIFICATIONS' END"
    )
    if bind.dialect.name == "postgresql":
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_fts ON knowledge_chunks USING GIN (to_tsvector('simple', normalized_content))"
        )
    elif bind.dialect.name == "sqlite":
        op.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_chunks_fts USING fts5(chunk_id UNINDEXED, normalized_content)"
        )
        op.execute(
            "INSERT INTO knowledge_chunks_fts(chunk_id, normalized_content) SELECT id, normalized_content FROM knowledge_chunks WHERE id NOT IN (SELECT chunk_id FROM knowledge_chunks_fts)"
        )
        op.execute(
            "CREATE TRIGGER IF NOT EXISTS knowledge_chunks_fts_insert AFTER INSERT ON knowledge_chunks BEGIN INSERT INTO knowledge_chunks_fts(chunk_id, normalized_content) VALUES (new.id, new.normalized_content); END"
        )
        op.execute(
            "CREATE TRIGGER IF NOT EXISTS knowledge_chunks_fts_delete AFTER DELETE ON knowledge_chunks BEGIN DELETE FROM knowledge_chunks_fts WHERE chunk_id = old.id; END"
        )
        op.execute(
            "CREATE TRIGGER IF NOT EXISTS knowledge_chunks_fts_update AFTER UPDATE OF normalized_content ON knowledge_chunks BEGIN UPDATE knowledge_chunks_fts SET normalized_content = new.normalized_content WHERE chunk_id = new.id; END"
        )

    for name, column in (
        ("sheet_number", sa.Column("sheet_number", sa.String(120))),
        ("sheet_name", sa.Column("sheet_name", sa.String(500))),
        ("revision", sa.Column("revision", sa.String(80))),
        (
            "drawing_type",
            sa.Column("drawing_type", sa.String(40), nullable=False, server_default="UNKNOWN"),
        ),
        ("level_name", sa.Column("level_name", sa.String(300))),
        ("classification_confidence", sa.Column("classification_confidence", sa.Numeric(5, 4))),
    ):
        _add_column_if_missing("sheets", name, column)

    _create_project_graph_tables(set(sa.inspect(bind).get_table_names()))


def _create_project_graph_tables(tables: set[str]) -> None:
    if "project_buildings" not in tables:
        op.create_table(
            "project_buildings",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "project_id",
                sa.String(36),
                sa.ForeignKey("projects.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("name", sa.String(300), nullable=False),
            sa.Column("source_key", sa.String(240), nullable=False),
            sa.Column(
                "source_document_id",
                sa.String(36),
                sa.ForeignKey("document_versions.id", ondelete="SET NULL"),
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("project_id", "source_key", name="uq_project_building_source"),
        )
    if "project_levels" not in tables:
        op.create_table(
            "project_levels",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "project_id",
                sa.String(36),
                sa.ForeignKey("projects.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "building_id",
                sa.String(36),
                sa.ForeignKey("project_buildings.id", ondelete="SET NULL"),
            ),
            sa.Column("name", sa.String(300), nullable=False),
            sa.Column("elevation", sa.Numeric(18, 6)),
            sa.Column("source_key", sa.String(240), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("project_id", "source_key", name="uq_project_level_source"),
        )
    if "view_regions" not in tables:
        op.create_table(
            "view_regions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "sheet_id",
                sa.String(36),
                sa.ForeignKey("sheets.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("bbox_normalized", sa.JSON(), nullable=False),
            sa.Column("view_type", sa.String(40), nullable=False),
            sa.Column("level_name", sa.String(300)),
            sa.Column("scale", sa.Numeric(18, 6)),
            sa.Column("scale_confidence", sa.Numeric(5, 4)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    if "scale_calibrations" not in tables:
        op.create_table(
            "scale_calibrations",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "view_region_id",
                sa.String(36),
                sa.ForeignKey("view_regions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("method", sa.String(40), nullable=False),
            sa.Column("known_distance", sa.Numeric(18, 6), nullable=False),
            sa.Column("pixel_distance", sa.Numeric(18, 6), nullable=False),
            sa.Column("drawing_units", sa.String(24), nullable=False),
            sa.Column("scale_ratio", sa.Numeric(18, 9), nullable=False),
            sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
            sa.Column("verified", sa.Boolean(), nullable=False),
            sa.Column("reviewer", sa.String(200)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    if "building_elements" not in tables:
        op.create_table(
            "building_elements",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "project_id",
                sa.String(36),
                sa.ForeignKey("projects.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "building_id",
                sa.String(36),
                sa.ForeignKey("project_buildings.id", ondelete="SET NULL"),
            ),
            sa.Column(
                "level_id", sa.String(36), sa.ForeignKey("project_levels.id", ondelete="SET NULL")
            ),
            sa.Column("discipline", sa.String(40), nullable=False),
            sa.Column("element_type", sa.String(40), nullable=False),
            sa.Column("subtype", sa.String(200)),
            sa.Column("name", sa.String(500)),
            sa.Column("geometry_type", sa.String(80)),
            sa.Column("geometry_json", sa.JSON(), nullable=False),
            sa.Column("length", sa.Numeric(18, 6)),
            sa.Column("width", sa.Numeric(18, 6)),
            sa.Column("height", sa.Numeric(18, 6)),
            sa.Column("thickness", sa.Numeric(18, 6)),
            sa.Column("area", sa.Numeric(18, 6)),
            sa.Column("volume", sa.Numeric(18, 6)),
            sa.Column("material", sa.String(500)),
            sa.Column("properties_json", sa.JSON(), nullable=False),
            sa.Column("source_type", sa.String(40), nullable=False),
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
            sa.Column("source_external_id", sa.String(120)),
            sa.Column("source_coordinates", sa.JSON(), nullable=False),
            sa.Column("extraction_method", sa.String(80), nullable=False),
            sa.Column("extraction_model", sa.String(160)),
            sa.Column("extraction_version", sa.String(80), nullable=False),
            sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
            sa.Column("review_status", sa.String(30), nullable=False),
            sa.Column("reviewer", sa.String(200)),
            sa.Column("reviewed_at", sa.DateTime(timezone=True)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "source_document_id", "source_external_id", name="uq_element_source_external"
            ),
        )
    if "element_relationships" not in tables:
        op.create_table(
            "element_relationships",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "project_id",
                sa.String(36),
                sa.ForeignKey("projects.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "source_element_id",
                sa.String(36),
                sa.ForeignKey("building_elements.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "target_element_id",
                sa.String(36),
                sa.ForeignKey("building_elements.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("relationship_type", sa.String(40), nullable=False),
            sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
            sa.Column("source_type", sa.String(40), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "source_element_id",
                "relationship_type",
                "target_element_id",
                name="uq_element_relationship",
            ),
        )
    if "measurements" not in tables:
        op.create_table(
            "measurements",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "project_id",
                sa.String(36),
                sa.ForeignKey("projects.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "element_id",
                sa.String(36),
                sa.ForeignKey("building_elements.id", ondelete="SET NULL"),
            ),
            sa.Column("measurement_type", sa.String(30), nullable=False),
            sa.Column("geometry_json", sa.JSON(), nullable=False),
            sa.Column("raw_value", sa.Numeric(18, 6), nullable=False),
            sa.Column("unit", sa.String(24), nullable=False),
            sa.Column(
                "scale_calibration_id",
                sa.String(36),
                sa.ForeignKey("scale_calibrations.id", ondelete="SET NULL"),
            ),
            sa.Column("calculated_value", sa.Numeric(18, 6), nullable=False),
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
            sa.Column("method", sa.String(80), nullable=False),
            sa.Column("algorithm_version", sa.String(80), nullable=False),
            sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
            sa.Column("review_status", sa.String(30), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column(
                "supersedes_measurement_id",
                sa.String(36),
                sa.ForeignKey("measurements.id", ondelete="SET NULL"),
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    if "measurement_evidence" not in tables:
        op.create_table(
            "measurement_evidence",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "measurement_id",
                sa.String(36),
                sa.ForeignKey("measurements.id", ondelete="CASCADE"),
                nullable=False,
                unique=True,
            ),
            sa.Column("source_coordinates", sa.JSON(), nullable=False),
            sa.Column("geometry_json", sa.JSON(), nullable=False),
            sa.Column("page_number", sa.Integer()),
            sa.Column(
                "view_region_id",
                sa.String(36),
                sa.ForeignKey("view_regions.id", ondelete="SET NULL"),
            ),
            sa.Column("scale_json", sa.JSON(), nullable=False),
            sa.Column("thumbnail_reference", sa.String(500)),
            sa.Column("algorithm", sa.String(160), nullable=False),
            sa.Column("model_version", sa.String(100), nullable=False),
            sa.Column("review_status", sa.String(30), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    if "takeoff_items" not in tables:
        op.create_table(
            "takeoff_items",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "project_id",
                sa.String(36),
                sa.ForeignKey("projects.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("service_code", sa.String(160), nullable=False),
            sa.Column("description", sa.String(500), nullable=False),
            sa.Column("quantity", sa.Numeric(18, 6), nullable=False),
            sa.Column("unit", sa.String(24), nullable=False),
            sa.Column("measurement_ids", sa.JSON(), nullable=False),
            sa.Column("calculation_rule", sa.Text(), nullable=False),
            sa.Column("calculation_rule_version", sa.String(80), nullable=False),
            sa.Column("price_reference", sa.String(240)),
            sa.Column("quantity_source", sa.String(50), nullable=False),
            sa.Column("review_status", sa.String(30), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    if "quantity_conflicts" not in tables:
        op.create_table(
            "quantity_conflicts",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "project_id",
                sa.String(36),
                sa.ForeignKey("projects.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "element_id",
                sa.String(36),
                sa.ForeignKey("building_elements.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("quantity_name", sa.String(80), nullable=False),
            sa.Column("stated_value", sa.Numeric(18, 6), nullable=False),
            sa.Column("calculated_value", sa.Numeric(18, 6), nullable=False),
            sa.Column("difference_ratio", sa.Numeric(10, 6), nullable=False),
            sa.Column("status", sa.String(30), nullable=False),
            sa.Column("detail_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )


def _add_column_if_missing(table: str, column_name: str, column: sa.Column) -> None:
    if column_name not in {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}:
        op.add_column(table, column)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS knowledge_chunks_fts_update")
        op.execute("DROP TRIGGER IF EXISTS knowledge_chunks_fts_delete")
        op.execute("DROP TRIGGER IF EXISTS knowledge_chunks_fts_insert")
        op.execute("DROP TABLE IF EXISTS knowledge_chunks_fts")
    for table in (
        "quantity_conflicts",
        "takeoff_items",
        "measurement_evidence",
        "measurements",
        "element_relationships",
        "building_elements",
        "scale_calibrations",
        "view_regions",
        "project_levels",
        "project_buildings",
        "database_backups",
        "visitor_sessions",
    ):
        if sa.inspect(bind).has_table(table):
            op.drop_table(table)
