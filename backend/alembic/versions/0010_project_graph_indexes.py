"""Add query indexes for project graph and knowledge collections.

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-28
"""

import sqlalchemy as sa

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


INDEXES: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "database_backups": (
        ("ix_database_backups_database_kind", ("database_kind",)),
        ("ix_database_backups_checksum_sha256", ("checksum_sha256",)),
        ("ix_database_backups_validation_status", ("validation_status",)),
    ),
    "knowledge_sources": (("ix_knowledge_sources_collection", ("collection",)),),
    "sheets": (
        ("ix_sheets_sheet_number", ("sheet_number",)),
        ("ix_sheets_drawing_type", ("drawing_type",)),
    ),
    "project_buildings": (
        ("ix_project_buildings_project_id", ("project_id",)),
        ("ix_project_buildings_source_document_id", ("source_document_id",)),
    ),
    "project_levels": (
        ("ix_project_levels_project_id", ("project_id",)),
        ("ix_project_levels_building_id", ("building_id",)),
    ),
    "view_regions": (("ix_view_regions_sheet_id", ("sheet_id",)),),
    "scale_calibrations": (("ix_scale_calibrations_view_region_id", ("view_region_id",)),),
    "building_elements": (
        ("ix_building_elements_project_id", ("project_id",)),
        ("ix_building_elements_building_id", ("building_id",)),
        ("ix_building_elements_level_id", ("level_id",)),
        ("ix_building_elements_discipline", ("discipline",)),
        ("ix_building_elements_element_type", ("element_type",)),
        ("ix_building_elements_source_type", ("source_type",)),
        ("ix_building_elements_source_document_id", ("source_document_id",)),
        ("ix_building_elements_source_sheet_id", ("source_sheet_id",)),
        ("ix_building_elements_source_view_region_id", ("source_view_region_id",)),
        ("ix_building_elements_source_external_id", ("source_external_id",)),
        ("ix_building_elements_review_status", ("review_status",)),
    ),
    "element_relationships": (
        ("ix_element_relationships_project_id", ("project_id",)),
        ("ix_element_relationships_source_element_id", ("source_element_id",)),
        ("ix_element_relationships_target_element_id", ("target_element_id",)),
        ("ix_element_relationships_relationship_type", ("relationship_type",)),
    ),
    "measurements": (
        ("ix_measurements_project_id", ("project_id",)),
        ("ix_measurements_element_id", ("element_id",)),
        ("ix_measurements_measurement_type", ("measurement_type",)),
        ("ix_measurements_scale_calibration_id", ("scale_calibration_id",)),
        ("ix_measurements_source_document_id", ("source_document_id",)),
        ("ix_measurements_source_sheet_id", ("source_sheet_id",)),
        ("ix_measurements_source_view_region_id", ("source_view_region_id",)),
        ("ix_measurements_review_status", ("review_status",)),
    ),
    "measurement_evidence": (
        ("ix_measurement_evidence_measurement_id", ("measurement_id",)),
        ("ix_measurement_evidence_view_region_id", ("view_region_id",)),
        ("ix_measurement_evidence_review_status", ("review_status",)),
    ),
    "takeoff_items": (
        ("ix_takeoff_items_project_id", ("project_id",)),
        ("ix_takeoff_items_service_code", ("service_code",)),
        ("ix_takeoff_items_quantity_source", ("quantity_source",)),
        ("ix_takeoff_items_review_status", ("review_status",)),
    ),
    "quantity_conflicts": (
        ("ix_quantity_conflicts_project_id", ("project_id",)),
        ("ix_quantity_conflicts_element_id", ("element_id",)),
        ("ix_quantity_conflicts_status", ("status",)),
    ),
}


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    for table, definitions in INDEXES.items():
        if table not in tables:
            continue
        existing = {item["name"] for item in inspector.get_indexes(table)}
        for name, columns in definitions:
            if name not in existing:
                op.create_index(name, table, list(columns))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    for table, definitions in reversed(tuple(INDEXES.items())):
        if table not in tables:
            continue
        existing = {item["name"] for item in inspector.get_indexes(table)}
        for name, _ in reversed(definitions):
            if name in existing:
                op.drop_index(name, table_name=table)
