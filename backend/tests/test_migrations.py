from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from alembic import command


def _alembic_config() -> Config:
    return Config(str(Path(__file__).parents[1] / "alembic.ini"))


def test_existing_database_upgrades_to_head_without_data_loss(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "existing.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("GURSAR_DATABASE_URL", database_url)
    config = _alembic_config()
    command.upgrade(config, "0003")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE conversation_turn_receipts"))
        connection.execute(
            text(
                "INSERT INTO conversations (id, locale, qr_source_id, created_at) "
                "VALUES ('existing-conversation', 'tr', NULL, CURRENT_TIMESTAMP)"
            )
        )
    engine.dispose()

    command.upgrade(config, "head")

    upgraded_engine = create_engine(database_url)
    inspector = inspect(upgraded_engine)
    assert "conversation_turn_receipts" in inspector.get_table_names()
    assert {"projects", "documents", "document_versions", "sheets"}.issubset(
        inspector.get_table_names()
    )
    assert "document_analyses" in inspector.get_table_names()
    assert {
        "visitor_sessions",
        "database_backups",
        "project_buildings",
        "project_levels",
        "building_elements",
        "view_regions",
        "scale_calibrations",
        "measurements",
        "measurement_evidence",
        "takeoff_items",
        "quantity_conflicts",
    }.issubset(inspector.get_table_names())
    assert {
        "sheet_number",
        "sheet_name",
        "revision",
        "drawing_type",
        "level_name",
        "classification_confidence",
    }.issubset({column["name"] for column in inspector.get_columns("sheets")})
    assert "collection" in {column["name"] for column in inspector.get_columns("knowledge_sources")}
    unique_constraints = inspector.get_unique_constraints("conversation_turn_receipts")
    assert any(
        constraint["name"] == "uq_conversation_turn_receipt_key"
        for constraint in unique_constraints
    )
    with upgraded_engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT COUNT(*) FROM conversations WHERE id = 'existing-conversation'")
            )
            == 1
        )
    upgraded_engine.dispose()
