from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Check GÜRSAR SQLite integrity and key counts")
    parser.add_argument("database", type=Path)
    args = parser.parse_args()
    database = args.database.resolve()
    if not database.is_file():
        raise SystemExit(f"Database not found: {database}")
    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        print(f"integrity={connection.execute('PRAGMA integrity_check').fetchone()[0]}")
        print(
            "alembic="
            f"{connection.execute('SELECT version_num FROM alembic_version').fetchone()[0]}"
        )
        for table in (
            "knowledge_chunks",
            "productivity_records",
            "conversation_project_contexts",
            "conversation_plans",
        ):
            count = connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            print(f"{table}={count}")
        required = {
            "projects",
            "conversations",
            "knowledge_chunks",
            "productivity_records",
            "conversation_project_contexts",
            "conversation_plans",
        }
        missing = required - tables
        if missing:
            raise SystemExit(f"Missing required tables: {sorted(missing)}")


if __name__ == "__main__":
    main()
