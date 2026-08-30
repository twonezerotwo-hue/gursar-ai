"""Add vector PDF intelligence metadata and analysis artifacts.

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-28
"""

import sqlalchemy as sa

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def _add_columns(table: str, columns: list[sa.Column]) -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}
    for column in columns:
        if column.name not in existing:
            op.add_column(table, column)


def upgrade() -> None:
    _add_columns(
        "sheets",
        [
            sa.Column("page_type", sa.String(20), nullable=False, server_default="UNKNOWN"),
            sa.Column("page_type_confidence", sa.Numeric(5, 4)),
            sa.Column("rotation", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("orientation", sa.String(20), nullable=False, server_default="PORTRAIT"),
            sa.Column("standard_size", sa.String(20), nullable=False, server_default="CUSTOM"),
            sa.Column("discipline_confidence", sa.Numeric(5, 4)),
            sa.Column("discipline_method", sa.String(80)),
            sa.Column("drawing_type_confidence", sa.Numeric(5, 4)),
            sa.Column("drawing_type_method", sa.String(80)),
            sa.Column(
                "review_status", sa.String(30), nullable=False, server_default="REVIEW_REQUIRED"
            ),
            sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
        ],
    )
    _add_columns(
        "view_regions",
        [
            sa.Column("confidence", sa.Numeric(5, 4), nullable=False, server_default="0"),
            sa.Column(
                "review_status", sa.String(30), nullable=False, server_default="REVIEW_REQUIRED"
            ),
            sa.Column("scale_status", sa.String(30), nullable=False, server_default="UNKNOWN"),
            sa.Column("source_type", sa.String(40), nullable=False, server_default="MANUAL"),
            sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
        ],
    )
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "vector_pdf_analyses" not in tables:
        op.create_table(
            "vector_pdf_analyses",
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
            sa.Column("page_count", sa.Integer(), nullable=False),
            sa.Column("vector_page_count", sa.Integer(), nullable=False),
            sa.Column("raster_page_count", sa.Integer(), nullable=False),
            sa.Column("hybrid_page_count", sa.Integer(), nullable=False),
            sa.Column("text_object_count", sa.Integer(), nullable=False),
            sa.Column("vector_object_count", sa.Integer(), nullable=False),
            sa.Column("view_region_count", sa.Integer(), nullable=False),
            sa.Column("measurement_count", sa.Integer(), nullable=False),
            sa.Column("review_required_count", sa.Integer(), nullable=False),
            sa.Column("summary_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "document_version_id", "analysis_version", name="uq_vector_pdf_analysis_version"
            ),
        )
        op.create_index("ix_vector_pdf_analyses_project_id", "vector_pdf_analyses", ["project_id"])
        op.create_index(
            "ix_vector_pdf_analyses_document_version_id",
            "vector_pdf_analyses",
            ["document_version_id"],
        )
    if "extracted_pdf_texts" not in tables:
        op.create_table(
            "extracted_pdf_texts",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "analysis_id",
                sa.String(36),
                sa.ForeignKey("vector_pdf_analyses.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "document_version_id",
                sa.String(36),
                sa.ForeignKey("document_versions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "sheet_id",
                sa.String(36),
                sa.ForeignKey("sheets.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("page_number", sa.Integer(), nullable=False),
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column("bbox_points", sa.JSON(), nullable=False),
            sa.Column("bbox_normalized", sa.JSON(), nullable=False),
            sa.Column("font_size", sa.Numeric(10, 4)),
            sa.Column("font_name", sa.String(200)),
            sa.Column("rotation", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
            sa.Column("source", sa.String(30), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        for column in ("analysis_id", "document_version_id", "sheet_id", "page_number", "source"):
            op.create_index(f"ix_extracted_pdf_texts_{column}", "extracted_pdf_texts", [column])
    if "sheet_metadata_revisions" not in tables:
        op.create_table(
            "sheet_metadata_revisions",
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
            sa.Column(
                "sheet_id",
                sa.String(36),
                sa.ForeignKey("sheets.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column(
                "previous_revision_id",
                sa.String(36),
                sa.ForeignKey("sheet_metadata_revisions.id", ondelete="SET NULL"),
            ),
            sa.Column("original_prediction_json", sa.JSON(), nullable=False),
            sa.Column("corrected_metadata_json", sa.JSON(), nullable=False),
            sa.Column("task_type", sa.String(50), nullable=False),
            sa.Column("reviewer", sa.String(200), nullable=False),
            sa.Column("reviewer_role", sa.String(30), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("review_status", sa.String(30), nullable=False, server_default="VERIFIED"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        for column in ("project_id", "document_version_id", "sheet_id"):
            op.create_index(
                f"ix_sheet_metadata_revisions_{column}", "sheet_metadata_revisions", [column]
            )


def downgrade() -> None:
    for table in ("sheet_metadata_revisions", "extracted_pdf_texts", "vector_pdf_analyses"):
        if table in set(sa.inspect(op.get_bind()).get_table_names()):
            op.drop_table(table)
