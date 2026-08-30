from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from app.bootstrap import initialize_database
from app.core.config import Settings
from app.core.database import create_database_engine, create_session_factory
from app.modules.knowledge_acquisition.application.pipeline import (
    KnowledgeAcquisitionPipeline,
)
from app.modules.knowledge_acquisition.infrastructure.config import KnowledgeConfig
from app.modules.knowledge_acquisition.infrastructure.repository import KnowledgeRepository


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Acquire trusted, versioned construction knowledge from controlled sources."
    )
    parser.add_argument("--source", action="append", default=[])
    parser.add_argument(
        "--approve-official",
        action="store_true",
        help="Operator approval: activate validated official sources after ingestion.",
    )
    args = parser.parse_args()
    settings = Settings.from_environment()
    engine = create_database_engine(settings.database_url)
    factory = create_session_factory(engine)
    initialize_database(engine, factory)
    config = KnowledgeConfig()
    sources = config.sources()
    if args.source:
        requested = set(args.source)
        sources = tuple(source for source in sources if source.key in requested)
        missing = requested - {source.key for source in sources}
        if missing:
            parser.error(f"Unknown source keys: {sorted(missing)}")
    with factory.begin() as session:
        repository = KnowledgeRepository(session)
        report = KnowledgeAcquisitionPipeline(
            repository,
            config,
            storage_root=Path(settings.document_storage_root).parent / "knowledge",
        ).run(sources, approve_official=args.approve_official)
        dashboard = repository.dashboard()
    print(
        json.dumps(
            {"report": asdict(report), "dashboard": dashboard},
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )
    return 1 if report.failed and report.ingested == 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
