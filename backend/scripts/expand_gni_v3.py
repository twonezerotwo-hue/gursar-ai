from __future__ import annotations

import argparse
import hashlib
import io
import json
import zipfile
from pathlib import PurePosixPath

from sqlalchemy import select

from app.core.config import Settings
from app.core.database import create_database_engine, create_session_factory
from app.modules.acquisition.application.real_data import curated_gni_ifc_archive
from app.modules.acquisition.application.service import IfcArchiveImporter
from app.modules.acquisition.infrastructure.models import (
    DiscoveredDatasetSourceORM,
    ExternalProjectPackageORM,
)
from app.modules.documents.infrastructure.storage import LocalDocumentStorage


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Resume-safe TUM GNI IFC expansion")
    parser.add_argument("--target", type=int, default=100)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--download-only", action="store_true")
    parser.add_argument("--skip-download", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = _arguments()
    settings = Settings.from_environment()
    engine = create_database_engine(settings.database_url)
    factory = create_session_factory(engine)
    storage = LocalDocumentStorage(settings.document_storage_root)
    with factory.begin() as session:
        source = session.scalar(
            select(DiscoveredDatasetSourceORM).where(
                DiscoveredDatasetSourceORM.name == "TUM GNI BIM Dataset"
            )
        )
        if source is None:
            raise RuntimeError("TUM GNI source is not registered")
        package = session.scalar(
            select(ExternalProjectPackageORM).where(
                ExternalProjectPackageORM.source_id == source.id,
                ExternalProjectPackageORM.external_project_id == "2025_BIMfundamentals",
            )
        )
        if package is None:
            raise RuntimeError("TUM GNI root package is not registered")
        additions: list[dict[str, object]] = []
        if not args.skip_download:
            existing_hashes = {str(item["sha256"]) for item in package.files_json}
            archive = next(iter(curated_gni_ifc_archive(source.source_url, limit=args.target)))
            with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
                for info in bundle.infolist():
                    if info.is_dir() or PurePosixPath(info.filename).suffix.lower() != ".ifc":
                        continue
                    content = bundle.read(info)
                    digest = hashlib.sha256(content).hexdigest()
                    if digest in existing_hashes:
                        continue
                    key = f"extracted/{source.id}/gni-v3/{digest}.ifc"
                    storage.save(storage_key=key, source=io.BytesIO(content))
                    additions.append(
                        {
                            "path": info.filename,
                            "storage_key": key,
                            "sha256": digest,
                            "size": len(content),
                        }
                    )
                    existing_hashes.add(digest)
            package.files_json = [*package.files_json, *additions]
            package.ingestion_status = "PACKAGED"
        source_id, package_id = source.id, package.id
        print(json.dumps({"downloaded": len(package.files_json), "added": len(additions)}))
    if args.download_only:
        engine.dispose()
        return
    totals = {"imported": 0, "reused": 0, "failed": 0, "elements": 0}
    for offset in range(0, args.target, args.batch_size):
        with factory.begin() as session:
            result = IfcArchiveImporter(session, storage).ingest(
                source_id=source_id,
                package_id=package_id,
                limit=args.batch_size,
                offset=offset,
            )
            for key in totals:
                totals[key] += int(result[key])
            print(json.dumps({"batch_offset": offset, **result}, default=str))
    print(json.dumps({"target": args.target, **totals}))
    engine.dispose()


if __name__ == "__main__":
    main()
