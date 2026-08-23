from __future__ import annotations

import argparse
import json

from app.bootstrap import initialize_database
from app.core.config import Settings
from app.core.database import create_database_engine, create_session_factory
from app.modules.legal_knowledge.infrastructure.ingestion import LegalCorpusIngestor
from app.modules.legal_knowledge.infrastructure.repository import LegalKnowledgeRepository
from app.modules.legal_knowledge.infrastructure.sources import OFFICIAL_SOURCES


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import the official Turkish construction-law corpus into GÜRSAR AI."
    )
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument(
        "--missing-only",
        action="store_true",
        help="Skip documents already present while still refreshing discovery catalogs.",
    )
    args = parser.parse_args()

    settings = Settings.from_environment()
    engine = create_database_engine(settings.database_url)
    factory = create_session_factory(engine)
    initialize_database(engine, factory)

    with factory.begin() as session:
        result = LegalCorpusIngestor(session, timeout_seconds=args.timeout).ingest(
            OFFICIAL_SOURCES,
            missing_only=args.missing_only,
        )
        document_count, chunk_count = LegalKnowledgeRepository(session).counts()

    print(
        json.dumps(
            {
                "imported": result.imported,
                "unchanged": result.unchanged,
                "discovered": result.discovered,
                "failed": result.failed,
                "document_count": document_count,
                "chunk_count": chunk_count,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if result.failed and result.imported == 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
