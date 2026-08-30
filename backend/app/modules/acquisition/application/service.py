from __future__ import annotations

import hashlib
import io
import json
import math
import random
import re
import shutil
import time
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar, Protocol
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

import yaml
from PIL import Image, ImageDraw
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.persistence import append_audit, new_id, utc_now
from app.modules.acquisition.application.real_data import (
    UpstreamLicenseLineageService,
    canonical_source_tier,
)
from app.modules.acquisition.infrastructure.models import (
    AcquisitionRunORM,
    CrossModalAnnotationLineageORM,
    DiscoveredDatasetSourceORM,
    DownloadManifestORM,
    ExternalAnnotationLineageORM,
    ExternalClassMappingORM,
    ExternalProjectPackageORM,
    Ifc2DAnnotationLineageORM,
    Ifc2DRenderORM,
    IfcHarvestSummaryORM,
    LicenseAssessmentORM,
    SyntheticProjectORM,
)
from app.modules.documents.domain.ports import DocumentStorage
from app.modules.documents.infrastructure.models import (
    DocumentVersionORM,
    ProjectDocumentORM,
    SheetORM,
)
from app.modules.project_graph.application.service import ProjectGraphService
from app.modules.project_graph.infrastructure.models import BuildingElementORM
from app.modules.projects.infrastructure.models import ProjectORM
from app.modules.training.infrastructure.models import DatasetVersionORM, TrainingProjectManifestORM
from app.modules.vision.infrastructure.models import (
    TrainingImageDerivativeORM,
    VisionAnnotationORM,
    VisionDatasetItemORM,
)

ACQUISITION_VERSION = "AUTONOMOUS_ACQUISITION_V1"
LICENSE_CHECKER_VERSION = "LICENSE_GATE_V1"
IMPORTER_VERSION = "EXTERNAL_ANNOTATION_IMPORT_V1"
MAPPING_VERSION = "GURSAR_ARCH_ONTOLOGY_V1"
NORMALIZATION_VERSION = "NORMALIZED_COORDINATES_V1"
SYNTHETIC_VERSION = "GURSAR_SYNTHETIC_ARCH_V1"
BOOTSTRAP_VERSION = "AUTONOMOUS_BOOTSTRAP_V1"
TRAINING_RIGHTS = {"OPEN_LICENSED", "LICENSED_BY_GURSAR", "INTERNAL_PROJECT"}
ARCH_CLASSES = {"WALL", "DOOR", "WINDOW", "COLUMN", "ROOM", "STAIR"}
ALLOWED_EXTENSIONS = {
    ".ifc",
    ".pdf",
    ".dxf",
    ".svg",
    ".png",
    ".jpg",
    ".jpeg",
    ".json",
    ".xml",
    ".txt",
    ".csv",
    ".zip",
}
EXECUTABLE_EXTENSIONS = {".exe", ".dll", ".bat", ".cmd", ".ps1", ".sh", ".js", ".html"}


class AcquisitionRuleError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DiscoveredSource:
    provider: str
    source_url: str
    name: str
    repository_url: str | None = None
    description: str = ""
    publisher: str | None = None
    formats: tuple[str, ...] = ()
    annotation_available: bool = False
    ground_truth_available: bool = False
    license_name: str | None = None
    license_url: str | None = None
    license_text: str | None = None
    discovery_query: str = "seed"
    reputation_score: int = 50
    access_status: str = "PUBLIC"
    upstream_license: str | None = None
    upstream_url: str | None = None
    derived_from: str | None = None
    lineage_verified: bool = False


class DiscoveryProvider(Protocol):
    provider_name: str

    def discover(self, queries: list[str], limit: int) -> Iterable[DiscoveredSource]: ...


class StaticSeedSourceProvider:
    provider_name = "STATIC_SEED"

    def __init__(self, seeds: list[dict[str, Any]]) -> None:
        self._seeds = seeds

    def discover(self, queries: list[str], limit: int) -> Iterable[DiscoveredSource]:
        del queries
        for item in self._seeds[:limit]:
            yield DiscoveredSource(
                provider=self.provider_name,
                source_url=str(item["source_url"]),
                repository_url=item.get("repository_url"),
                name=str(item["name"]),
                description=str(item.get("description", "")),
                publisher=item.get("publisher"),
                formats=tuple(item.get("formats", [])),
                annotation_available=bool(item.get("annotation_available", False)),
                ground_truth_available=bool(item.get("ground_truth_available", False)),
                license_name=item.get("license_name"),
                license_url=item.get("license_url"),
                license_text=item.get("license_text"),
                reputation_score=int(item.get("reputation_score", 50)),
                access_status=str(item.get("access_status", "PUBLIC")),
                upstream_license=item.get("upstream_license"),
                upstream_url=item.get("upstream_url"),
                derived_from=item.get("derived_from"),
                lineage_verified=bool(item.get("lineage_verified", False)),
            )


class GitHubDiscoveryProvider:
    provider_name = "GITHUB"

    def __init__(self, *, timeout: float = 10, allowed_domains: set[str] | None = None) -> None:
        self._timeout = timeout
        self._allowed_domains = allowed_domains or {"api.github.com", "github.com"}

    def discover(self, queries: list[str], limit: int) -> Iterable[DiscoveredSource]:
        remaining = limit
        for query in queries:
            if remaining <= 0:
                break
            search = query.replace(" ", "+")
            url = (
                "https://api.github.com/search/repositories"
                f"?q={search}&per_page={min(remaining, 10)}"
            )
            request = Request(
                url, headers={"Accept": "application/vnd.github+json", "User-Agent": "GURSAR-AI/1"}
            )
            with urlopen(request, timeout=self._timeout) as response:
                payload = json.load(response)
            for item in payload.get("items", []):
                html_url = str(item.get("html_url", ""))
                if urlparse(html_url).hostname not in self._allowed_domains:
                    continue
                license_data = item.get("license") or {}
                yield DiscoveredSource(
                    provider=self.provider_name,
                    source_url=html_url,
                    repository_url=html_url,
                    name=str(item.get("full_name", "GitHub dataset")),
                    description=str(item.get("description") or ""),
                    publisher=str((item.get("owner") or {}).get("login") or "GitHub"),
                    license_name=license_data.get("spdx_id"),
                    discovery_query=query,
                    reputation_score=60,
                )
                remaining -= 1
                if remaining <= 0:
                    break


class HuggingFaceDiscoveryProvider:
    provider_name = "HUGGINGFACE"

    def __init__(self, *, timeout: float = 10) -> None:
        self._timeout = timeout

    def discover(self, queries: list[str], limit: int) -> Iterable[DiscoveredSource]:
        remaining = limit
        for query in queries:
            if remaining <= 0:
                break
            search = query.replace(" ", "%20")
            url = f"https://huggingface.co/api/datasets?search={search}&limit={min(remaining, 10)}"
            request = Request(url, headers={"User-Agent": "GURSAR-AI/1"})
            with urlopen(request, timeout=self._timeout) as response:
                payload = json.load(response)
            for item in payload:
                dataset_id = str(item.get("id", ""))
                if not dataset_id:
                    continue
                yield DiscoveredSource(
                    provider=self.provider_name,
                    source_url=f"https://huggingface.co/datasets/{dataset_id}",
                    repository_url=f"https://huggingface.co/datasets/{dataset_id}",
                    name=dataset_id,
                    publisher=dataset_id.split("/", 1)[0],
                    discovery_query=query,
                    reputation_score=65,
                )
                remaining -= 1
                if remaining <= 0:
                    break


class OfficialDatasetProvider(StaticSeedSourceProvider):
    provider_name = "OFFICIAL_DATASET"


class LicenseGate:
    _permissive: ClassVar[dict[str, tuple[bool, bool, bool, bool, bool]]] = {
        "MIT": (True, True, True, True, False),
        "APACHE-2.0": (True, True, True, True, True),
        "BSD-2-CLAUSE": (True, True, True, True, True),
        "BSD-3-CLAUSE": (True, True, True, True, True),
        "CC0-1.0": (True, True, True, True, False),
        "CC-BY-4.0": (True, True, True, True, True),
        "ODC-BY-1.0": (True, True, True, True, True),
    }
    _research: ClassVar[set[str]] = {"CC-BY-NC-4.0", "RESEARCH-ONLY", "ACADEMIC-ONLY"}

    def assess(self, source: DiscoveredSource) -> dict[str, Any]:
        name = (source.license_name or "").strip().upper()
        text_hash = (
            hashlib.sha256(source.license_text.encode()).hexdigest()
            if source.license_text
            else None
        )
        if source.access_status != "PUBLIC":
            return self._decision(source, "REFERENCE_ONLY", text_hash, manual=True)
        if name in self._permissive and (
            source.license_url or source.license_text or name == "MIT"
        ):
            commercial, derivative, training, redistribution, attribution = self._permissive[name]
            return {
                **self._decision(source, "OPEN_LICENSED", text_hash, manual=False),
                "commercial_use_allowed": commercial,
                "derivative_use_allowed": derivative,
                "training_use_allowed": training,
                "redistribution_allowed": redistribution,
                "attribution_required": attribution,
            }
        if name in self._research or "NONCOMMERCIAL" in (source.license_text or "").upper():
            return {
                **self._decision(source, "RESEARCH_ONLY", text_hash, manual=False),
                "research_only": True,
            }
        return self._decision(source, "LICENSE_UNKNOWN", text_hash, manual=True)

    @staticmethod
    def _decision(
        source: DiscoveredSource, rights: str, text_hash: str | None, *, manual: bool
    ) -> dict[str, Any]:
        allowed = rights in TRAINING_RIGHTS
        return {
            "source_url": source.source_url,
            "source_name": source.name,
            "publisher": source.publisher,
            "repository_url": source.repository_url,
            "license_name": source.license_name,
            "license_url": source.license_url,
            "license_text_hash": text_hash,
            "commercial_use_allowed": True if allowed else None,
            "derivative_use_allowed": True if allowed else None,
            "training_use_allowed": allowed,
            "redistribution_allowed": True if allowed else None,
            "attribution_required": False,
            "research_only": False,
            "manual_review_required": manual,
            "rights_status": rights,
            "checker_version": LICENSE_CHECKER_VERSION,
        }


class DatasetDiscoveryService:
    def __init__(self, session: Session, providers: list[DiscoveryProvider]) -> None:
        self._session = session
        self._providers = providers

    def run_discovery(
        self, *, queries: list[str], max_sources: int, dry_run: bool = False
    ) -> dict[str, Any]:
        found: list[DiscoveredSource] = []
        for provider in self._providers:
            if len(found) >= max_sources:
                break
            found.extend(provider.discover(queries, max_sources - len(found)))
        unique = {item.source_url: item for item in found}
        if dry_run:
            return {"would_discover": len(unique), "sources": sorted(unique)}
        created = reused = unknown = approved = 0
        gate = LicenseGate()
        for item in unique.values():
            row = self._session.scalar(
                select(DiscoveredDatasetSourceORM).where(
                    DiscoveredDatasetSourceORM.source_url == item.source_url
                )
            )
            decision = gate.assess(item)
            score = self._score(item, decision)
            family_id = self._source_family(item)
            if row is None:
                row = DiscoveredDatasetSourceORM(
                    provider=item.provider,
                    source_url=item.source_url,
                    repository_url=item.repository_url,
                    name=item.name,
                    description=item.description,
                    publisher=item.publisher,
                    detected_formats_json=list(item.formats),
                    annotation_available=item.annotation_available,
                    ground_truth_available=item.ground_truth_available,
                    license_status=decision["rights_status"],
                    quality_score=self._quality(item),
                    relevance_score=self._relevance(item),
                    reputation_score=item.reputation_score,
                    source_family_id=family_id,
                    acquisition_priority_score=score["total"],
                    score_breakdown_json=score,
                    discovery_query=item.discovery_query,
                    access_status=item.access_status,
                    status="APPROVED" if decision["training_use_allowed"] else "REVIEWING",
                )
                self._session.add(row)
                self._session.flush()
                created += 1
            else:
                reused += 1
                row.source_family_id = row.source_family_id or family_id
                row.acquisition_priority_score = score["total"]
                row.score_breakdown_json = score
                if (
                    row.license_status in TRAINING_RIGHTS
                    and row.license_status != decision["rights_status"]
                ):
                    row.status = "SUSPENDED"
                row.last_checked_at = utc_now()
            assessment = LicenseAssessmentORM(source_id=row.id, **decision)
            self._session.add(assessment)
            if item.upstream_license or item.derived_from or item.lineage_verified:
                lineage = UpstreamLicenseLineageService(self._session).record(
                    source_id=row.id,
                    declared_license=item.license_name,
                    upstream_license=item.upstream_license,
                    upstream_url=item.upstream_url,
                    derived_from=item.derived_from,
                    lineage_verified=item.lineage_verified,
                )
                if not lineage.training_allowed:
                    decision["training_use_allowed"] = False
                    row.status = "REVIEW_REQUIRED"
            unknown += int(decision["rights_status"] == "LICENSE_UNKNOWN")
            approved += int(decision["training_use_allowed"])
        self._session.flush()
        return {
            "discovered": len(unique),
            "created": created,
            "reused": reused,
            "approved": approved,
            "license_unknown": unknown,
        }

    @staticmethod
    def _quality(item: DiscoveredSource) -> int:
        return min(
            100,
            item.reputation_score // 2
            + 15 * int(item.annotation_available)
            + 15 * int(item.ground_truth_available),
        )

    @staticmethod
    def _relevance(item: DiscoveredSource) -> int:
        text = f"{item.name} {item.description}".lower()
        return min(
            100,
            25 + 15 * sum(token in text for token in ("floor", "plan", "ifc", "bim", "architect")),
        )

    @classmethod
    def _score(cls, item: DiscoveredSource, decision: dict[str, Any]) -> dict[str, int]:
        description = f"{item.name} {item.description}".upper()
        class_coverage = sum(name in description for name in ARCH_CLASSES)
        parts = {
            "license_score": 25 if decision["training_use_allowed"] else 0,
            "annotation_score": 20 if item.annotation_available else 0,
            "architecture_relevance": round(cls._relevance(item) * 0.15),
            "class_coverage": min(15, class_coverage * 3),
            "project_count": 5 if item.ground_truth_available else 0,
            "resolution_quality": 5 if any(x in item.formats for x in ("PNG", "JPG", "SVG")) else 2,
            "format_quality": 8 if any(x in item.formats for x in ("IFC", "SVG", "JSON")) else 3,
            "source_reputation": round(item.reputation_score * 0.07),
        }
        return {**parts, "total": min(100, sum(parts.values()))}

    @staticmethod
    def _source_family(item: DiscoveredSource) -> str:
        parsed = urlparse(item.repository_url or item.source_url)
        path = parsed.path.strip("/").split("/")
        identity = f"{parsed.hostname}:{'/'.join(path[:2])}".lower()
        return hashlib.sha256(identity.encode()).hexdigest()[:24]


class DatasetDownloadManager:
    def __init__(
        self,
        session: Session,
        storage: DocumentStorage,
        *,
        max_single_file_bytes: int,
        max_archive_files: int,
        max_total_bytes: int,
        allowed_domains: set[str],
        timeout: float = 30,
        max_storage_bytes: int | None = None,
        storage_root: Path | None = None,
        min_free_bytes: int = 0,
    ) -> None:
        self._session = session
        self._storage = storage
        self._max_single = max_single_file_bytes
        self._max_archive_files = max_archive_files
        self._max_total = max_total_bytes
        self._allowed_domains = allowed_domains
        self._timeout = timeout
        self._max_storage = max_storage_bytes or max_total_bytes
        self._storage_root = storage_root
        self._min_free_bytes = min_free_bytes

    def download(
        self,
        source_id: str,
        *,
        dry_run: bool = False,
        fetcher: Callable[[str], Iterable[bytes]] | None = None,
    ) -> tuple[DownloadManifestORM | None, dict[str, Any]]:
        source = self._session.get(DiscoveredDatasetSourceORM, source_id)
        if source is None:
            raise AcquisitionRuleError("SOURCE_NOT_FOUND")
        assessment = self._session.scalar(
            select(LicenseAssessmentORM)
            .where(LicenseAssessmentORM.source_id == source_id)
            .order_by(LicenseAssessmentORM.checked_at.desc())
        )
        lineage = UpstreamLicenseLineageService(self._session).latest(source_id)
        if (
            source.status not in {"APPROVED", "DOWNLOADED"}
            or assessment is None
            or not assessment.training_use_allowed
            or (lineage is not None and not lineage.training_allowed)
        ):
            raise AcquisitionRuleError("LICENSE_RESTRICTED")
        existing_source_manifest = self._session.scalar(
            select(DownloadManifestORM)
            .where(DownloadManifestORM.source_id == source_id)
            .order_by(DownloadManifestORM.downloaded_at.desc())
        )
        if existing_source_manifest is not None and not dry_run:
            return existing_source_manifest, {"reused": True, "error": "DUPLICATE_DATASET"}
        host = urlparse(source.source_url).hostname or ""
        if host not in self._allowed_domains and fetcher is None:
            raise AcquisitionRuleError("DOMAIN_NOT_ALLOWED")
        if dry_run:
            return None, {"would_download": source.source_url, "max_bytes": self._max_single}
        if self._storage_root is not None:
            self._storage_root.mkdir(parents=True, exist_ok=True)
            free = shutil.disk_usage(self._storage_root).free
            if free < self._min_free_bytes:
                raise AcquisitionRuleError("MIN_FREE_STORAGE_GUARD")
        chunks = fetcher(source.source_url) if fetcher else self._http_chunks(source.source_url)
        data, digest = self._bounded_download(chunks)
        used_storage = int(
            self._session.scalar(select(func.coalesce(func.sum(DownloadManifestORM.total_size), 0)))
            or 0
        )
        if used_storage + len(data) > self._max_storage:
            raise AcquisitionRuleError("STORAGE_QUOTA_EXCEEDED")
        duplicate = self._session.scalar(
            select(DownloadManifestORM).where(DownloadManifestORM.archive_hash == digest)
        )
        if duplicate:
            return duplicate, {"reused": True, "error": "DUPLICATE_DATASET"}
        files, formats = self._inspect(data, source.source_url)
        key = f"quarantine/{source.id}/{digest}.bin"
        self._storage.save(storage_key=key, source=io.BytesIO(data))
        files = self._extract_validated(data, source.id, digest, files)
        row = DownloadManifestORM(
            source_id=source.id,
            license_snapshot_json={
                "rights_status": assessment.rights_status,
                "license_name": assessment.license_name,
                "license_url": assessment.license_url,
                "attribution_required": assessment.attribution_required,
                "upstream_lineage_id": lineage.id if lineage is not None else None,
                "upstream_license": lineage.upstream_license if lineage is not None else None,
                "effective_license": (
                    lineage.effective_license if lineage is not None else assessment.license_name
                ),
                "lineage_verified": lineage.lineage_verified if lineage is not None else False,
                "lineage_conflict_code": lineage.conflict_code if lineage is not None else None,
            },
            archive_hash=digest,
            storage_key=key,
            files_json=files,
            total_size=len(data),
            formats_json=formats,
            extraction_status="EXTRACTED",
        )
        self._session.add(row)
        source.status = "DOWNLOADED"
        self._session.flush()
        return row, {"reused": False, "files": len(files), "bytes": len(data)}

    def _extract_validated(
        self,
        data: bytes,
        source_id: str,
        archive_hash: str,
        files: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        extracted: list[dict[str, Any]] = []
        if data.startswith(b"PK\x03\x04"):
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                for item in files:
                    safe_name = re.sub(
                        r"[^A-Za-z0-9._-]", "_", PurePosixPath(str(item["path"])).name
                    )
                    suffix = PurePosixPath(safe_name).suffix[:10]
                    storage_key = (
                        f"a/{source_id[:8]}/{archive_hash[:12]}/{str(item['sha256'])[:16]}{suffix}"
                    )
                    with archive.open(str(item["path"])) as stream:
                        self._storage.save(storage_key=storage_key, source=stream)
                    extracted.append({**item, "storage_key": storage_key})
            return extracted
        item = files[0]
        storage_key = f"approved_sources/{source_id}/{archive_hash}/{item['path']}"
        self._storage.save(storage_key=storage_key, source=io.BytesIO(data))
        return [{**item, "storage_key": storage_key}]

    def _http_chunks(self, url: str) -> Iterable[bytes]:
        request = Request(url, headers={"User-Agent": "GURSAR-AI/1"})
        with urlopen(request, timeout=self._timeout) as response:
            content_type = str(response.headers.get("Content-Type", ""))
            if "text/html" in content_type:
                raise AcquisitionRuleError("UNSUPPORTED_FORMAT")
            while chunk := response.read(1024 * 1024):
                yield chunk

    def _bounded_download(self, chunks: Iterable[bytes]) -> tuple[bytes, str]:
        output = io.BytesIO()
        digest = hashlib.sha256()
        total = 0
        for chunk in chunks:
            total += len(chunk)
            if total > min(self._max_single, self._max_total):
                raise AcquisitionRuleError("DOWNLOAD_LIMIT_EXCEEDED")
            output.write(chunk)
            digest.update(chunk)
        if total == 0:
            raise AcquisitionRuleError("DOWNLOAD_FAILED")
        return output.getvalue(), digest.hexdigest()

    def _inspect(self, data: bytes, source_url: str) -> tuple[list[dict[str, Any]], list[str]]:
        if data.startswith(b"PK\x03\x04"):
            return self._inspect_zip(data)
        suffix = Path(urlparse(source_url).path).suffix.lower()
        if suffix in EXECUTABLE_EXTENSIONS or suffix not in ALLOWED_EXTENSIONS - {".zip"}:
            raise AcquisitionRuleError("UNSUPPORTED_FORMAT")
        return [
            {
                "path": Path(urlparse(source_url).path).name,
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
            }
        ], [suffix]

    def _inspect_zip(self, data: bytes) -> tuple[list[dict[str, Any]], list[str]]:
        files: list[dict[str, Any]] = []
        formats: set[str] = set()
        total_uncompressed = 0
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) > self._max_archive_files:
                raise AcquisitionRuleError("ARCHIVE_INVALID")
            for info in infos:
                path = PurePosixPath(info.filename.replace("\\", "/"))
                suffix = path.suffix.lower()
                if path.is_absolute() or ".." in path.parts or info.is_dir():
                    if info.is_dir():
                        continue
                    raise AcquisitionRuleError("ARCHIVE_UNSAFE")
                if suffix in EXECUTABLE_EXTENSIONS or suffix not in ALLOWED_EXTENSIONS - {".zip"}:
                    raise AcquisitionRuleError("UNSUPPORTED_FORMAT")
                total_uncompressed += info.file_size
                if total_uncompressed > self._max_total or info.file_size > self._max_single:
                    raise AcquisitionRuleError("ARCHIVE_INVALID")
                with archive.open(info) as stream:
                    digest = hashlib.sha256()
                    while chunk := stream.read(1024 * 1024):
                        digest.update(chunk)
                files.append(
                    {"path": str(path), "sha256": digest.hexdigest(), "size": info.file_size}
                )
                formats.add(suffix)
        if not files:
            raise AcquisitionRuleError("ARCHIVE_INVALID")
        return files, sorted(formats)


class ExternalProjectPackageBuilder:
    def __init__(self, session: Session) -> None:
        self._session = session

    def build(self, manifest_id: str, *, dry_run: bool = False) -> dict[str, Any]:
        manifest = self._session.get(DownloadManifestORM, manifest_id)
        if manifest is None:
            raise AcquisitionRuleError("DOWNLOAD_MANIFEST_NOT_FOUND")
        source = self._session.get(DiscoveredDatasetSourceORM, manifest.source_id)
        if source is None:
            raise AcquisitionRuleError("SOURCE_NOT_FOUND")
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for file_info in manifest.files_json:
            path = PurePosixPath(str(file_info["path"]))
            project_key = path.parts[0] if len(path.parts) > 1 else path.stem
            grouped[project_key].append(file_info)
        if dry_run:
            return {
                "would_create_projects": len(grouped),
                "would_ingest_files": len(manifest.files_json),
            }
        created = reused = 0
        for external_id, files in grouped.items():
            existing = self._session.scalar(
                select(ExternalProjectPackageORM).where(
                    ExternalProjectPackageORM.source_id == source.id,
                    ExternalProjectPackageORM.external_project_id == external_id,
                )
            )
            if existing:
                reused += 1
                continue
            suffixes = {PurePosixPath(str(item["path"])).suffix.lower() for item in files}
            annotations = [
                item
                for item in files
                if PurePosixPath(str(item["path"])).suffix.lower() in {".json", ".xml", ".svg"}
            ]
            criteria = {
                "architectural_drawing": bool(
                    suffixes & {".pdf", ".dxf", ".svg", ".png", ".jpg", ".jpeg"}
                ),
                "ifc": ".ifc" in suffixes,
                "annotations": bool(annotations),
                "metadata": bool(suffixes & {".json", ".xml", ".csv", ".txt"}),
            }
            completeness = round(100 * sum(criteria.values()) / len(criteria))
            project = ProjectORM(
                name=f"External {source.name}: {external_id}", locale="tr", status="ACTIVE"
            )
            self._session.add(project)
            self._session.flush()
            training_allowed = source.license_status in TRAINING_RIGHTS
            self._session.add(
                TrainingProjectManifestORM(
                    project_id=project.id,
                    rights_status=source.license_status,
                    training_allowed=training_allowed,
                    building_use="UNKNOWN",
                    architectural_documents=criteria["architectural_drawing"],
                    structural_documents=criteria["ifc"],
                    data_completeness_score=completeness,
                    anonymization_status="PENDING",
                )
            )
            package = ExternalProjectPackageORM(
                source_id=source.id,
                external_project_id=external_id,
                project_id=project.id,
                discipline_availability_json=[
                    name
                    for name, available in (
                        ("ARCHITECTURAL", criteria["architectural_drawing"]),
                        ("BIM", criteria["ifc"]),
                    )
                    if available
                ],
                files_json=files,
                annotation_files_json=annotations,
                project_quality_score=min(source.quality_score, completeness),
                training_eligibility="ELIGIBLE"
                if source.license_status in TRAINING_RIGHTS
                else "BLOCKED",
                rights_status=source.license_status,
                ingestion_status="PACKAGED",
                completeness_json={"score": completeness, "criteria": criteria},
            )
            self._session.add(package)
            created += 1
        self._session.flush()
        return {"created": created, "reused": reused, "files": len(manifest.files_json)}


class AcquisitionCoordinator:
    def __init__(self, session: Session, providers: list[DiscoveryProvider]) -> None:
        self._session = session
        self._providers = providers

    def execute(
        self, *, queries: list[str], max_sources: int, dry_run: bool, job_id: str | None = None
    ) -> dict[str, Any]:
        result = DatasetDiscoveryService(self._session, self._providers).run_discovery(
            queries=queries, max_sources=max_sources, dry_run=dry_run
        )
        run = AcquisitionRunORM(
            job_id=job_id,
            provider="MULTI_PROVIDER",
            query_json=queries,
            source_count=int(result.get("discovered", result.get("would_discover", 0))),
            license_unknown=int(result.get("license_unknown", 0)),
            dry_run=dry_run,
            result_json=result,
        )
        self._session.add(run)
        self._session.flush()
        return {**result, "run_id": run.id}


def curated_buildingsmart_ifc_archive(*, limit: int = 40, timeout: float = 30) -> Iterable[bytes]:
    """Build a bounded archive from the official repository's IFC files only."""
    tree_url = "https://api.github.com/repos/buildingSMART/Certification-datasets/git/trees/main?recursive=1"
    request = Request(
        tree_url, headers={"Accept": "application/vnd.github+json", "User-Agent": "GURSAR-AI/1"}
    )
    with urlopen(request, timeout=timeout) as response:
        tree = json.load(response).get("tree", [])
    paths = [
        str(item["path"]) for item in tree if str(item.get("path", "")).lower().endswith(".ifc")
    ][:limit]
    if not paths:
        raise AcquisitionRuleError("NO_IFC_FILES_DISCOVERED")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in paths:
            raw_url = (
                "https://raw.githubusercontent.com/buildingSMART/Certification-datasets/main/"
                f"{quote(path, safe='/')}"
            )
            with urlopen(
                Request(raw_url, headers={"User-Agent": "GURSAR-AI/1"}), timeout=timeout
            ) as response:
                content = response.read(64 * 1024 * 1024 + 1)
            if len(content) > 64 * 1024 * 1024 or b"ISO-10303-21" not in content[:512]:
                continue
            archive.writestr(path, content)
    yield output.getvalue()


class DatasetCandidateBuilder:
    def __init__(self, session: Session) -> None:
        self._session = session

    def build_ready_only(self, readiness: dict[str, Any]) -> dict[str, Any] | None:
        readiness_rules = load_acquisition_rules().get("readiness", {})
        if (
            readiness_rules.get("foundational_all_required", True)
            and "foundational_classes_ready" in readiness
            and not readiness.get("foundational_classes_ready")
        ):
            return None
        ready_classes = list(readiness.get("ready_classes", []))
        if not ready_classes:
            return None
        name, version = "gursar_arch_vision_v0.1_candidate", "0.1-candidate"
        existing = self._session.scalar(
            select(DatasetVersionORM).where(
                DatasetVersionORM.dataset_name == name,
                DatasetVersionORM.version == version,
            )
        )
        if existing:
            return {"id": existing.id, "name": name, "ready_classes": ready_classes, "reused": True}
        eligible = list(
            self._session.scalars(
                select(VisionAnnotationORM).where(
                    VisionAnnotationORM.class_name.in_(ready_classes),
                    VisionAnnotationORM.review_status.in_(("VERIFIED", "CORRECTED")),
                    VisionAnnotationORM.training_allowed.is_(True),
                    VisionAnnotationORM.anonymized.is_(True),
                    VisionAnnotationORM.rights_status.in_(TRAINING_RIGHTS),
                    VisionAnnotationORM.source_type != "GOLDEN_BENCHMARK",
                )
            )
        )
        if not eligible:
            return None
        rows, selection = self._balanced_selection(eligible)
        if not rows:
            return None
        projects = {row.project_id for row in rows}
        sheets = {row.sheet_id for row in rows}
        class_distribution = dict(Counter(row.class_name for row in rows))
        rights = dict(Counter(row.rights_status for row in rows))
        manifest = {
            "dataset_version": version,
            "ready_classes": ready_classes,
            "excluded_classes": sorted(ARCH_CLASSES - set(ready_classes)),
            "project_count": len(projects),
            "source_families": sorted(selection["source_families"]),
            "sample_count": len(rows),
            "real_sample_count": selection["non_synthetic_count"],
            "synthetic_count": selection["synthetic_selected"],
            "synthetic_stored": selection["synthetic_stored"],
            "synthetic_ratio": selection["synthetic_ratio"],
            "bim_derived_stored": selection["bim_stored"],
            "bim_derived_selected": selection["bim_selected"],
            "bim_derived_ratio": selection["bim_derived_ratio"],
            "quality_tiers": selection["source_type_counts"],
            "license_distribution": rights,
            "class_distribution": class_distribution,
            "class_distribution_by_source": selection["class_distribution_by_source"],
            "selection_strategy": "CLASS_SOURCE_PROJECT_BALANCED_V1",
        }
        checksum = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
        dataset = DatasetVersionORM(
            dataset_name=name,
            version=version,
            status="DRAFT",
            project_count=len(projects),
            sheet_count=len(sheets),
            sample_count=len(rows),
            class_distribution_json=class_distribution,
            rights_summary_json=rights,
            task_types_json={"manifest": manifest, "weak_included": False, "golden_excluded": True},
            checksum=checksum,
        )
        self._session.add(dataset)
        self._session.flush()
        split_counts: Counter[str] = Counter()
        for row in rows:
            derivative = self._session.get(TrainingImageDerivativeORM, row.image_derivative_id)
            if derivative is None:
                continue
            bucket = int(hashlib.sha256(row.project_id.encode()).hexdigest()[:8], 16) % 100
            split = "TRAIN" if bucket < 70 else ("VALIDATION" if bucket < 85 else "TEST")
            split_counts[split] += 1
            self._session.add(
                VisionDatasetItemORM(
                    dataset_version_id=dataset.id,
                    annotation_id=row.id,
                    image_derivative_id=derivative.id,
                    project_id=row.project_id,
                    dataset_split=split,
                )
            )
        manifest["split_distribution"] = dict(split_counts)
        dataset.task_types_json = {
            "manifest": manifest,
            "weak_included": False,
            "golden_excluded": True,
        }
        self._session.flush()
        return {
            "id": dataset.id,
            "name": name,
            "ready_classes": ready_classes,
            "manifest": manifest,
            "reused": False,
        }

    def _balanced_selection(
        self, rows: list[VisionAnnotationORM]
    ) -> tuple[list[VisionAnnotationORM], dict[str, Any]]:
        sampling = load_acquisition_rules().get("readiness", {}).get("sampling", {})
        max_synthetic_ratio = float(sampling.get("maximum_synthetic_ratio", 0.40))
        max_bim_ratio = float(sampling.get("maximum_bim_derived_ratio", 0.50))
        governed_non_bim = [
            row
            for row in rows
            if canonical_source_tier(row.source_type)
            not in {"SYNTHETIC", "BIM_DERIVED_SILVER", "WEAK"}
        ]
        bim_rows = [
            row for row in rows if canonical_source_tier(row.source_type) == "BIM_DERIVED_SILVER"
        ]
        synthetic = [row for row in rows if canonical_source_tier(row.source_type) == "SYNTHETIC"]
        # BIM cannot manufacture its own denominator. A candidate with only BIM data is blocked.
        bim_cap = math.floor(len(governed_non_bim) * max_bim_ratio / max(1e-9, 1 - max_bim_ratio))
        selected_bim = self._round_robin_by_class(bim_rows, bim_cap)
        non_synthetic = governed_non_bim + selected_bim
        synthetic_cap = math.floor(
            len(non_synthetic) * max_synthetic_ratio / max(1e-9, 1 - max_synthetic_ratio)
        )
        selected_synthetic = self._round_robin_by_class(synthetic, synthetic_cap)
        selected = sorted(
            non_synthetic + selected_synthetic,
            key=lambda row: (
                row.class_name,
                canonical_source_tier(row.source_type),
                row.project_id,
                row.id,
            ),
        )
        type_counts = Counter(canonical_source_tier(row.source_type) for row in selected)
        by_class: dict[str, Counter[str]] = defaultdict(Counter)
        for row in selected:
            by_class[row.class_name][canonical_source_tier(row.source_type)] += 1
        source_families = self._source_families(selected)
        return selected, {
            "non_synthetic_count": len(non_synthetic),
            "bim_stored": len(bim_rows),
            "bim_selected": len(selected_bim),
            "bim_derived_ratio": round(len(selected_bim) / max(1, len(selected)), 6),
            "synthetic_stored": len(synthetic),
            "synthetic_selected": len(selected_synthetic),
            "synthetic_ratio": round(len(selected_synthetic) / max(1, len(selected)), 6),
            "source_type_counts": {
                name: type_counts.get(name, 0)
                for name in (
                    "GURSAR_GOLD",
                    "REAL_DRAWING_SILVER",
                    "BIM_DERIVED_SILVER",
                    "EXTERNAL_OBJECT_SILVER",
                    "SYNTHETIC",
                    "WEAK",
                )
            },
            "class_distribution_by_source": {
                name: dict(counts) for name, counts in sorted(by_class.items())
            },
            "source_families": source_families,
        }

    @staticmethod
    def _round_robin_by_class(
        rows: list[VisionAnnotationORM], cap: int
    ) -> list[VisionAnnotationORM]:
        per_class: dict[str, list[VisionAnnotationORM]] = defaultdict(list)
        for row in rows:
            per_class[row.class_name].append(row)
        selected: list[VisionAnnotationORM] = []
        class_order = sorted(per_class)
        cursors = {name: 0 for name in class_order}
        for bucket in per_class.values():
            bucket.sort(key=lambda row: (row.project_id, row.id))
        while len(selected) < cap and class_order:
            progressed = False
            for class_name in class_order:
                bucket = per_class[class_name]
                index = cursors[class_name]
                if index >= len(bucket) or len(selected) >= cap:
                    continue
                selected.append(bucket[index])
                cursors[class_name] += 1
                progressed = True
            if not progressed:
                break
        return selected

    def _source_families(self, rows: list[VisionAnnotationORM]) -> set[str]:
        ids = {row.id for row in rows}
        external = {
            item.annotation_id: item.source_id
            for item in self._session.scalars(
                select(ExternalAnnotationLineageORM).where(
                    ExternalAnnotationLineageORM.annotation_id.in_(ids)
                )
            )
        }
        ifc_sources = {
            item.annotation_id: render.source_id
            for item in self._session.scalars(
                select(Ifc2DAnnotationLineageORM).where(
                    Ifc2DAnnotationLineageORM.annotation_id.in_(ids)
                )
            )
            if (render := self._session.get(Ifc2DRenderORM, item.render_id)) is not None
        }
        source_rows = {
            item.id: item.source_family_id or item.id
            for item in self._session.scalars(select(DiscoveredDatasetSourceORM))
        }
        families: set[str] = set()
        for row in rows:
            tier = canonical_source_tier(row.source_type)
            if tier == "SYNTHETIC":
                families.add("GURSAR_SYNTHETIC_ARCH_V1")
            elif tier == "GURSAR_GOLD":
                families.add("GURSAR_INTERNAL")
            else:
                source_id = external.get(row.id) or ifc_sources.get(row.id)
                family = source_rows.get(source_id) if source_id is not None else None
                families.add(family or source_id or tier)
        return families


class AutonomousAcquisitionOrchestrator:
    stages = (
        "DISCOVERY",
        "LICENSE_CHECK",
        "SOURCE_RANKING",
        "DOWNLOAD",
        "IMPORT",
        "IFC_HARVEST",
        "SYNTHETIC_GENERATION",
        "QA",
        "READINESS",
        "DATASET_BUILD",
        "COMPLETE",
    )

    def __init__(
        self,
        session: Session,
        storage: DocumentStorage,
        *,
        storage_root: Path,
        providers: list[DiscoveryProvider],
    ) -> None:
        self._session = session
        self._storage = storage
        self._storage_root = storage_root
        self._providers = providers

    def execute(self, *, mode: str = "FULL_BOOTSTRAP", job: Any | None = None) -> dict[str, Any]:
        if mode not in {"DISCOVERY_ONLY", "SAFE_ACQUIRE", "FULL_BOOTSTRAP"}:
            raise AcquisitionRuleError("INVALID_ACQUISITION_MODE")
        rules = load_acquisition_rules()
        run = AcquisitionRunORM(
            job_id=getattr(job, "id", None),
            provider="MULTI_PROVIDER",
            query_json=rules["search_terms"],
            mode=mode,
            status="RUNNING",
            dry_run=False,
            checkpoint_json={"stage": "DISCOVERY"},
        )
        self._session.add(run)
        self._session.flush()
        started = time.monotonic()
        failures: list[dict[str, str]] = []
        self._stage(job, run, "DISCOVERY", 3)
        discovery = DatasetDiscoveryService(self._session, self._providers).run_discovery(
            queries=list(rules["search_terms"]), max_sources=int(rules["max_sources_per_run"])
        )
        run.source_count = int(discovery["discovered"])
        run.license_unknown = int(discovery["license_unknown"])
        if mode == "DISCOVERY_ONLY":
            return self._finish(run, job, {"discovery": discovery}, started)
        self._stage(job, run, "SOURCE_RANKING", 12)
        sources = list(
            self._session.scalars(
                select(DiscoveredDatasetSourceORM)
                .where(DiscoveredDatasetSourceORM.status.in_(("APPROVED", "DOWNLOADED")))
                .order_by(DiscoveredDatasetSourceORM.acquisition_priority_score.desc())
            )
        )[: int(rules["acquisition"]["max_downloads"])]
        manager = DatasetDownloadManager(
            self._session,
            self._storage,
            max_single_file_bytes=int(
                float(rules["acquisition"]["max_single_dataset_gb"]) * 1024**3
            ),
            max_archive_files=50_000,
            max_total_bytes=int(float(rules["acquisition"]["max_total_download_gb"]) * 1024**3),
            allowed_domains=set(rules["allowed_domains"]),
            timeout=60,
            max_storage_bytes=int(float(rules["acquisition"]["max_total_download_gb"]) * 1024**3),
            storage_root=self._storage_root,
            min_free_bytes=int(float(rules["acquisition"]["min_free_storage_gb"]) * 1024**3),
        )
        manifests: list[DownloadManifestORM] = []
        self._stage(job, run, "DOWNLOAD", 18)
        for source in sources:
            try:
                fetcher = (
                    (lambda _url: curated_buildingsmart_ifc_archive())
                    if "buildingSMART/Certification-datasets" in source.source_url
                    else None
                )
                manifest, _ = manager.download(source.id, fetcher=fetcher)
                if manifest and manifest not in manifests:
                    manifests.append(manifest)
            except Exception as exc:
                failures.append(
                    {"source": source.name, "stage": "DOWNLOAD", "error": str(exc)[:300]}
                )
        run.download_count = len(manifests)
        run.bytes_downloaded = sum(row.total_size for row in manifests)
        self._stage(job, run, "IMPORT", 30)
        packages: list[ExternalProjectPackageORM] = []
        annotation_result = {"imported": 0, "reused": 0, "invalid": 0}
        ifc_result = {"imported": 0, "reused": 0, "failed": 0, "elements": 0}
        for manifest in manifests:
            try:
                with self._session.begin_nested():
                    ExternalProjectPackageBuilder(self._session).build(manifest.id)
                    source_packages = list(
                        self._session.scalars(
                            select(ExternalProjectPackageORM).where(
                                ExternalProjectPackageORM.source_id == manifest.source_id
                            )
                        )
                    )
                    packages.extend(source_packages)
                    source = self._session.get(DiscoveredDatasetSourceORM, manifest.source_id)
                    for package in source_packages:
                        if source and "PERDAW" in source.name.upper():
                            result = ExternalAnnotationImporter(
                                self._session
                            ).import_perdaw_archive(
                                source_id=source.id,
                                package_id=package.id,
                                storage=self._storage,
                            )
                            for key in annotation_result:
                                annotation_result[key] += int(result.get(key, 0))
                        if any(
                            PurePosixPath(str(item["path"])).suffix.lower() == ".ifc"
                            for item in package.files_json
                        ):
                            result = IfcArchiveImporter(self._session, self._storage).ingest(
                                source_id=manifest.source_id, package_id=package.id, limit=40
                            )
                            for key in ifc_result:
                                ifc_result[key] += int(result.get(key, 0))
            except Exception as exc:
                failures.append(
                    {"source": str(manifest.source_id), "stage": "IMPORT", "error": str(exc)[:300]}
                )
        synthetic_result = {"generated": 0, "reused": 0, "annotations": 0}
        if mode == "FULL_BOOTSTRAP" and bool(rules["synthetic"].get("enabled_by_default", False)):
            synthetic_rules = rules["synthetic"]
            total = int(synthetic_rules["project_count"])
            batch_size = int(synthetic_rules["batch_size"])
            types = list(synthetic_rules["building_types"])
            for offset in range(0, total, batch_size):
                self._stage(
                    job,
                    run,
                    "SYNTHETIC_GENERATION",
                    45 + round(40 * offset / total),
                    {"generated": offset, "target": total},
                )
                result = SyntheticProjectFactory(self._session, self._storage).generate(
                    count=min(batch_size, total - offset),
                    seed=int(synthetic_rules["seed_start"]) + offset,
                    building_type=types[(offset // batch_size) % len(types)],
                    floors_min=1,
                    floors_max=8,
                )
                for key in synthetic_result:
                    synthetic_result[key] += int(result.get(key, 0))
                run.checkpoint_json = {
                    "stage": "SYNTHETIC_GENERATION",
                    "generated": offset + min(batch_size, total - offset),
                    "target": total,
                }
                self._session.flush()
                self._session.commit()
        self._stage(job, run, "READINESS", 90)
        readiness = DatasetReadinessEngine(self._session, rules["readiness"]).evaluate()
        self._stage(job, run, "DATASET_BUILD", 96)
        candidate = DatasetCandidateBuilder(self._session).build_ready_only(readiness)
        result = {
            "discovery": discovery,
            "downloads": len(manifests),
            "projects_imported": len({row.id for row in packages}),
            "external_annotations": annotation_result,
            "ifc": ifc_result,
            "synthetic": synthetic_result,
            "readiness": readiness,
            "candidate": candidate,
            "failures": failures,
        }
        run.failed_sources = len(failures)
        return self._finish(run, job, result, started)

    def _stage(
        self,
        job: Any | None,
        run: AcquisitionRunORM,
        stage: str,
        progress: int,
        detail: dict[str, Any] | None = None,
    ) -> None:
        run.checkpoint_json = {"stage": stage, **(detail or {})}
        if job is not None:
            job.current_stage = stage
            job.progress = progress
            job.heartbeat_at = utc_now()
        self._session.flush()

    def _finish(
        self, run: AcquisitionRunORM, job: Any | None, result: dict[str, Any], started: float
    ) -> dict[str, Any]:
        annotations = int(result.get("external_annotations", {}).get("imported", 0)) + int(
            result.get("synthetic", {}).get("annotations", 0)
        )
        run.status = "COMPLETED" if annotations else "COMPLETED_WITHOUT_TRAINING_DATA"
        run.checkpoint_json = {"stage": "COMPLETE"}
        run.result_json = {**result, "duration_seconds": round(time.monotonic() - started, 3)}
        if job is not None:
            job.current_stage = "COMPLETE"
            job.progress = 99
        self._session.flush()
        return run.result_json


def manual_license_review(
    session: Session,
    *,
    source_id: str,
    rights_status: str,
    license_name: str,
    license_url: str | None,
    reviewer: str,
) -> LicenseAssessmentORM:
    if rights_status not in {
        "OPEN_LICENSED",
        "LICENSED_BY_GURSAR",
        "INTERNAL_PROJECT",
        "RESEARCH_ONLY",
        "REFERENCE_ONLY",
        "LICENSE_UNKNOWN",
        "REJECTED",
    }:
        raise AcquisitionRuleError("INVALID_RIGHTS_STATUS")
    source = session.get(DiscoveredDatasetSourceORM, source_id)
    if source is None:
        raise AcquisitionRuleError("SOURCE_NOT_FOUND")
    allowed = rights_status in TRAINING_RIGHTS
    row = LicenseAssessmentORM(
        source_id=source.id,
        source_url=source.source_url,
        source_name=source.name,
        publisher=source.publisher,
        repository_url=source.repository_url,
        license_name=license_name,
        license_url=license_url,
        license_text_hash=None,
        commercial_use_allowed=True if allowed else None,
        derivative_use_allowed=True if allowed else None,
        training_use_allowed=allowed,
        redistribution_allowed=True if allowed else None,
        attribution_required=False,
        research_only=rights_status == "RESEARCH_ONLY",
        manual_review_required=False,
        rights_status=rights_status,
        checker_version=f"{LICENSE_CHECKER_VERSION}:MANUAL:{reviewer}",
    )
    session.add(row)
    source.license_status = rights_status
    source.status = (
        "APPROVED" if allowed else ("REJECTED" if rights_status == "REJECTED" else "REVIEWING")
    )
    append_audit(
        session,
        action="DATASET_LICENSE_REVIEWED",
        aggregate_id=source.id,
        detail={"rights_status": rights_status, "reviewer": reviewer},
    )
    session.flush()
    return row


def acquisition_status_summary(session: Session) -> dict[str, Any]:
    source_statuses = {
        str(status): int(count)
        for status, count in session.execute(
            select(DiscoveredDatasetSourceORM.status, func.count()).group_by(
                DiscoveredDatasetSourceORM.status
            )
        )
    }
    license_statuses = {
        str(license_status): int(count)
        for license_status, count in session.execute(
            select(DiscoveredDatasetSourceORM.license_status, func.count()).group_by(
                DiscoveredDatasetSourceORM.license_status
            )
        )
    }
    annotations = (
        session.scalar(select(func.count()).select_from(ExternalAnnotationLineageORM)) or 0
    )
    ifc_files = session.scalar(select(func.count()).select_from(IfcHarvestSummaryORM)) or 0
    packages = session.scalar(select(func.count()).select_from(ExternalProjectPackageORM)) or 0
    manifests = (
        session.scalar(select(func.count(func.distinct(DownloadManifestORM.source_id)))) or 0
    )
    archive_manifests = session.scalar(select(func.count()).select_from(DownloadManifestORM)) or 0
    bytes_downloaded = (
        session.scalar(select(func.coalesce(func.sum(DownloadManifestORM.total_size), 0))) or 0
    )
    source_quality: list[dict[str, Any]] = []
    for source in session.scalars(
        select(DiscoveredDatasetSourceORM).order_by(
            DiscoveredDatasetSourceORM.acquisition_priority_score.desc()
        )
    ):
        latest_manifest = session.scalar(
            select(DownloadManifestORM)
            .where(DownloadManifestORM.source_id == source.id)
            .order_by(DownloadManifestORM.downloaded_at.desc())
        )
        source_packages = list(
            session.scalars(
                select(ExternalProjectPackageORM).where(
                    ExternalProjectPackageORM.source_id == source.id
                )
            )
        )
        lineage = list(
            session.scalars(
                select(ExternalAnnotationLineageORM).where(
                    ExternalAnnotationLineageORM.source_id == source.id
                )
            )
        )
        class_counts: Counter[str] = Counter()
        for item in lineage:
            annotation = session.get(VisionAnnotationORM, item.annotation_id)
            if annotation:
                class_counts[annotation.class_name] += 1
        files = latest_manifest.files_json if latest_manifest else []
        unique_files = len({str(item.get("sha256")) for item in files})
        image_files = sum(
            PurePosixPath(str(item.get("path", ""))).suffix.lower() in {".png", ".jpg", ".jpeg"}
            for item in files
        )
        ifc_files_for_source = sum(
            PurePosixPath(str(item.get("path", ""))).suffix.lower() == ".ifc" for item in files
        )
        ifc_valid = int(
            session.scalar(
                select(func.count())
                .select_from(IfcHarvestSummaryORM)
                .where(IfcHarvestSummaryORM.source_id == source.id)
            )
            or 0
        )
        valid_count = len(lineage) if image_files else ifc_valid
        valid_denominator = image_files or ifc_files_for_source
        source_quality.append(
            {
                "source_id": source.id,
                "name": source.name,
                "source_family_id": source.source_family_id,
                "license": source.license_status,
                "files": len(files),
                "projects": len({row.project_id for row in source_packages if row.project_id}),
                "samples": len(lineage),
                "valid_ratio": round(valid_count / max(1, valid_denominator), 4),
                "duplicate_ratio": round((len(files) - unique_files) / max(1, len(files)), 4),
                "classes": dict(class_counts),
                "quality_score": source.quality_score,
                "priority_score": source.acquisition_priority_score,
            }
        )
    synthetic_rows = list(session.scalars(select(SyntheticProjectORM)))
    synthetic_class_distribution = {
        str(name): int(count)
        for name, count in session.execute(
            select(VisionAnnotationORM.class_name, func.count())
            .where(VisionAnnotationORM.source_type == "SYNTHETIC_GROUND_TRUTH")
            .group_by(VisionAnnotationORM.class_name)
        )
    }
    synthetic_annotations = sum(synthetic_class_distribution.values())
    augmentation_distribution = dict(
        Counter(str(row.spec_json.get("drawing_style", "unknown")) for row in synthetic_rows)
    )
    source_types = {
        str(source_type): int(count)
        for source_type, count in session.execute(
            select(VisionAnnotationORM.source_type, func.count()).group_by(
                VisionAnnotationORM.source_type
            )
        )
    }
    canonical_types = Counter()
    for source_type, count in source_types.items():
        canonical_types[canonical_source_tier(source_type)] += count
    ifc_renders = list(session.scalars(select(Ifc2DRenderORM)))
    bim_annotations = (
        session.scalar(
            select(func.count())
            .select_from(VisionAnnotationORM)
            .where(VisionAnnotationORM.source_type == "BIM_DERIVED_SILVER")
        )
        or 0
    )
    real_annotations = (
        session.scalar(
            select(func.count())
            .select_from(VisionAnnotationORM)
            .where(VisionAnnotationORM.source_type == "REAL_DRAWING_SILVER")
        )
        or 0
    )
    return {
        "sources": {
            "total": sum(source_statuses.values()),
            "by_status": source_statuses,
            "by_license": license_statuses,
        },
        "datasets_downloaded": manifests,
        "archive_manifests": archive_manifests,
        "downloaded_bytes": bytes_downloaded,
        "project_packages": packages,
        "ifc_files": ifc_files,
        "ifc_levels_rendered": len(ifc_renders),
        "bim_derived_plan_images": len(ifc_renders),
        "bim_derived_annotations": int(bim_annotations),
        "real_drawing_annotations": int(real_annotations),
        "annotations_imported": annotations,
        "source_quality": source_quality,
        "data_source_breakdown": {
            name: canonical_types.get(name, 0)
            for name in (
                "GURSAR_GOLD",
                "REAL_DRAWING_SILVER",
                "BIM_DERIVED_SILVER",
                "EXTERNAL_OBJECT_SILVER",
                "SYNTHETIC",
                "WEAK",
            )
        },
        "synthetic_quality": {
            "generated": len(synthetic_rows),
            "valid": sum(bool(row.validation_json.get("valid")) for row in synthetic_rows),
            "rejected": sum(row.status != "GENERATED" for row in synthetic_rows),
            "class_counts": synthetic_class_distribution,
            "average_elements_per_project": round(
                synthetic_annotations / max(1, len(synthetic_rows)), 3
            ),
            "augmentation_distribution": augmentation_distribution,
        },
    }


class ExternalAnnotationImporter:
    def __init__(self, session: Session) -> None:
        self._session = session

    @staticmethod
    def detect_format(file_name: str, content: bytes) -> str:
        suffix = Path(file_name).suffix.lower()
        if suffix == ".svg" or content.lstrip().startswith(b"<svg"):
            return "SVG_SEMANTIC"
        if suffix in {".png", ".jpg", ".jpeg"}:
            return "MASK_IMAGE"
        if suffix == ".json":
            payload = json.loads(content)
            if isinstance(payload, dict) and {"images", "categories", "annotations"}.issubset(
                payload
            ):
                return "COCO"
            return "JSON_GEOMETRY"
        return "CUSTOM_ADAPTER_REQUIRED"

    @staticmethod
    def validate_payload(format_name: str, payload: Any) -> dict[str, Any]:
        if format_name in {"COCO", "JSON_GEOMETRY"}:
            valid = isinstance(payload, dict) and isinstance(payload.get("annotations"), list)
        elif format_name == "SVG_SEMANTIC":
            valid = isinstance(payload, (str, bytes)) and "<svg" in str(payload)[:500]
        elif format_name == "MASK_IMAGE":
            valid = isinstance(payload, Image.Image)
        else:
            valid = False
        return {"valid": valid, "format": format_name}

    @staticmethod
    def parse_svg(content: bytes) -> list[dict[str, Any]]:
        root = ET.fromstring(content)
        width = float(re.sub(r"[^0-9.]", "", root.attrib.get("width", "1")) or 1)
        height = float(re.sub(r"[^0-9.]", "", root.attrib.get("height", "1")) or 1)
        parsed: list[dict[str, Any]] = []
        for element in root.iter():
            tag = element.tag.rsplit("}", 1)[-1]
            external_class = element.attrib.get("data-class") or element.attrib.get("class")
            if not external_class:
                continue
            if tag == "rect":
                x = float(element.attrib.get("x", 0))
                y = float(element.attrib.get("y", 0))
                w = float(element.attrib.get("width", 0))
                h = float(element.attrib.get("height", 0))
                bbox = [x / width, y / height, (x + w) / width, (y + h) / height]
                if ExternalAnnotationImporter._valid_bbox(bbox):
                    parsed.append(
                        {
                            "external_class": external_class,
                            "geometry_type": "BBOX",
                            "geometry": {"bbox": bbox},
                        }
                    )
            elif tag == "polygon":
                points = []
                for pair in element.attrib.get("points", "").split():
                    x_value, y_value = pair.split(",")
                    points.append([float(x_value) / width, float(y_value) / height])
                if len(points) >= 3 and all(0 <= value <= 1 for point in points for value in point):
                    parsed.append(
                        {
                            "external_class": external_class,
                            "geometry_type": "POLYGON",
                            "geometry": {"points": points},
                        }
                    )
        return parsed

    @staticmethod
    def parse_json_geometry(payload: dict[str, Any]) -> list[dict[str, Any]]:
        parsed: list[dict[str, Any]] = []
        for item in payload.get("annotations", []):
            external_class = item.get("class_name") or item.get("class")
            if not external_class:
                continue
            if "bbox_normalized" in item and ExternalAnnotationImporter._valid_bbox(
                list(map(float, item["bbox_normalized"]))
            ):
                parsed.append(
                    {
                        "external_class": str(external_class),
                        "geometry_type": "BBOX",
                        "geometry": {"bbox": item["bbox_normalized"]},
                    }
                )
            elif "polygon_normalized" in item:
                points = item["polygon_normalized"]
                if len(points) >= 3 and all(
                    0 <= float(value) <= 1 for point in points for value in point
                ):
                    parsed.append(
                        {
                            "external_class": str(external_class),
                            "geometry_type": "POLYGON",
                            "geometry": {"points": points},
                        }
                    )
        return parsed

    @staticmethod
    def parse_mask(
        image: Image.Image, palette: dict[tuple[int, int, int], str]
    ) -> list[dict[str, Any]]:
        rgb = image.convert("RGB")
        pixels = rgb.load()
        if pixels is None:
            raise AcquisitionRuleError("ANNOTATION_INVALID")
        parsed: list[dict[str, Any]] = []
        for color, external_class in palette.items():
            xs: list[int] = []
            ys: list[int] = []
            for y in range(rgb.height):
                for x in range(rgb.width):
                    if pixels[x, y] == color:
                        xs.append(x)
                        ys.append(y)
            if xs:
                parsed.append(
                    {
                        "external_class": external_class,
                        "geometry_type": "BBOX",
                        "geometry": {
                            "bbox": [
                                min(xs) / rgb.width,
                                min(ys) / rgb.height,
                                (max(xs) + 1) / rgb.width,
                                (max(ys) + 1) / rgb.height,
                            ]
                        },
                    }
                )
        return parsed

    def import_coco(
        self,
        *,
        source_id: str,
        package_id: str,
        document_version_id: str,
        sheet_id: str,
        image_derivative_id: str,
        payload: dict[str, Any],
        annotation_file: str = "annotations.json",
    ) -> dict[str, Any]:
        source = self._session.get(DiscoveredDatasetSourceORM, source_id)
        package = self._session.get(ExternalProjectPackageORM, package_id)
        image = self._session.get(TrainingImageDerivativeORM, image_derivative_id)
        if source is None or package is None or image is None:
            raise AcquisitionRuleError("ANNOTATION_INVALID")
        if package.project_id is None:
            raise AcquisitionRuleError("PROJECT_PACKAGE_INVALID")
        if package.rights_status not in TRAINING_RIGHTS:
            raise AcquisitionRuleError("LICENSE_RESTRICTED")
        categories = {int(item["id"]): str(item["name"]) for item in payload.get("categories", [])}
        imported = invalid = unknown = 0
        for item in payload.get("annotations", []):
            external_class = categories.get(int(item.get("category_id", -1)))
            if external_class is None:
                invalid += 1
                continue
            mapping = self._mapping(source_id, external_class)
            if mapping is None or not mapping.verified:
                unknown += 1
                continue
            bbox = item.get("bbox")
            if not isinstance(bbox, list) or len(bbox) != 4:
                invalid += 1
                continue
            x, y, width, height = map(float, bbox)
            normalized = {
                "bbox": [
                    x / image.width,
                    y / image.height,
                    (x + width) / image.width,
                    (y + height) / image.height,
                ]
            }
            if not self._valid_bbox(normalized["bbox"]):
                invalid += 1
                continue
            annotation = VisionAnnotationORM(
                project_id=package.project_id,
                document_version_id=document_version_id,
                sheet_id=sheet_id,
                image_derivative_id=image.id,
                task_type=f"{mapping.gursar_class}_DETECTION",
                class_name=mapping.gursar_class,
                geometry_type="BBOX",
                geometry_normalized=normalized,
                original_width=image.width,
                original_height=image.height,
                source_type="EXTERNAL_GROUND_TRUTH",
                annotation_source="EXTERNAL_GROUND_TRUTH",
                source_element_id=str(item.get("id")),
                confidence=1,
                review_status="VERIFIED",
                reviewer="external-ground-truth",
                training_allowed=True,
                anonymized=image.anonymization_status == "COMPLETED",
                rights_status=package.rights_status,
                quality_score=80,
            )
            self._session.add(annotation)
            self._session.flush()
            self._session.add(
                ExternalAnnotationLineageORM(
                    annotation_id=annotation.id,
                    source_id=source.id,
                    project_package_id=package.id,
                    original_file=image.storage_key,
                    annotation_file=annotation_file,
                    external_class=external_class,
                    importer_version=IMPORTER_VERSION,
                    mapping_version=mapping.mapping_version,
                    normalization_version=NORMALIZATION_VERSION,
                    quality_tier="SILVER",
                )
            )
            imported += 1
        total = imported + invalid + unknown
        broken_ratio = invalid / max(1, total)
        if imported == 0 or broken_ratio > 0.30:
            raise AcquisitionRuleError("LOW_QUALITY_DATASET")
        self._session.flush()
        return {
            "imported": imported,
            "invalid": invalid,
            "unknown_classes": unknown,
            "quality_tier": "SILVER",
        }

    def _mapping(self, source_id: str, external_class: str) -> ExternalClassMappingORM | None:
        return self._session.scalar(
            select(ExternalClassMappingORM).where(
                ExternalClassMappingORM.source_id == source_id,
                func.upper(ExternalClassMappingORM.external_class) == external_class.upper(),
                ExternalClassMappingORM.mapping_version == MAPPING_VERSION,
            )
        )

    @staticmethod
    def _valid_bbox(bbox: list[float]) -> bool:
        return (
            len(bbox) == 4
            and all(0 <= value <= 1 for value in bbox)
            and bbox[0] < bbox[2]
            and bbox[1] < bbox[3]
        )

    def import_perdaw_archive(
        self,
        *,
        source_id: str,
        package_id: str,
        storage: DocumentStorage,
        limit: int = 1000,
    ) -> dict[str, Any]:
        """Import PERDAW object crops as externally labelled classification samples."""
        source = self._session.get(DiscoveredDatasetSourceORM, source_id)
        package = self._session.get(ExternalProjectPackageORM, package_id)
        if source is None or package is None or package.project_id is None:
            raise AcquisitionRuleError("PROJECT_PACKAGE_INVALID")
        if package.rights_status not in TRAINING_RIGHTS:
            raise AcquisitionRuleError("LICENSE_RESTRICTED")
        project_manifest = self._session.scalar(
            select(TrainingProjectManifestORM).where(
                TrainingProjectManifestORM.project_id == package.project_id
            )
        )
        if project_manifest:
            project_manifest.anonymization_status = "COMPLETED"
            project_manifest.training_allowed = True
            project_manifest.data_completeness_score = max(
                project_manifest.data_completeness_score, 70
            )
        for external, mapped in (("door", "DOOR"), ("window", "WINDOW")):
            mapping = self._mapping(source.id, external)
            if mapping is None:
                self._session.add(
                    ExternalClassMappingORM(
                        source_id=source.id,
                        external_class=external,
                        gursar_class=mapped,
                        metadata_json={"dataset": "PERDAW", "view": "plan_or_elevation"},
                        mapping_confidence=1,
                        mapping_method="DATASET_ADAPTER",
                        verified=True,
                        mapping_version=MAPPING_VERSION,
                    )
                )
        self._session.flush()
        imported = reused = invalid = 0
        for info in package.files_json:
            if imported >= limit:
                break
            path = str(info.get("path", ""))
            suffix = PurePosixPath(path).suffix.lower()
            name = PurePosixPath(path).name.lower()
            if suffix not in {".jpg", ".jpeg", ".png"}:
                continue
            external_class = (
                "door"
                if name.startswith("door_")
                else ("window" if name.startswith("window_") else "")
            )
            if not external_class:
                invalid += 1
                continue
            original_name = (
                f"{hashlib.sha256(path.encode()).hexdigest()[:12]}-{PurePosixPath(path).name}"[:255]
            )
            document = self._session.scalar(
                select(ProjectDocumentORM).where(
                    ProjectDocumentORM.project_id == package.project_id,
                    ProjectDocumentORM.original_name == original_name,
                )
            )
            if document is not None:
                reused += 1
                continue
            storage_key = str(info["storage_key"])
            if self._session.scalar(
                select(DocumentVersionORM).where(DocumentVersionORM.storage_key == storage_key)
            ):
                reused += 1
                continue
            image_path = storage.path_for_read(storage_key=storage_key)
            try:
                with Image.open(image_path) as image:
                    width, height = image.size
                    image.verify()
            except Exception:
                invalid += 1
                continue
            document = ProjectDocumentORM(
                project_id=package.project_id, original_name=original_name
            )
            self._session.add(document)
            self._session.flush()
            version_id = new_id()
            self._session.add(
                DocumentVersionORM(
                    id=version_id,
                    document_id=document.id,
                    project_id=package.project_id,
                    version=1,
                    sha256=str(info["sha256"]),
                    size_bytes=int(info["size"]),
                    media_type="image/jpeg" if suffix in {".jpg", ".jpeg"} else "image/png",
                    storage_key=storage_key,
                    page_count=1,
                    status="READY",
                )
            )
            sheet = SheetORM(
                document_version_id=version_id,
                page_number=1,
                title=PurePosixPath(path).stem,
                discipline="ARCHITECTURAL",
                drawing_type="DETAIL",
                page_type="RASTER",
                review_status="VERIFIED",
                width_points=width,
                height_points=height,
            )
            self._session.add(sheet)
            self._session.flush()
            derivative = TrainingImageDerivativeORM(
                project_id=package.project_id,
                document_version_id=version_id,
                sheet_id=sheet.id,
                page_number=1,
                render_profile="PERDAW_ORIGINAL_V1",
                render_version="PERDAW_ADAPTER_V1",
                width=width,
                height=height,
                dpi=144,
                storage_key=storage_key,
                checksum_sha256=str(info["sha256"]),
                perceptual_hash=str(info["sha256"])[:16],
                rights_status=package.rights_status,
                anonymization_status="COMPLETED",
            )
            self._session.add(derivative)
            self._session.flush()
            mapping = self._mapping(source.id, external_class)
            if mapping is None:
                raise AcquisitionRuleError("MAPPING_REVIEW_REQUIRED")
            annotation = VisionAnnotationORM(
                project_id=package.project_id,
                document_version_id=version_id,
                sheet_id=sheet.id,
                image_derivative_id=derivative.id,
                task_type=f"{mapping.gursar_class}_CLASSIFICATION",
                class_name=mapping.gursar_class,
                geometry_type="BBOX",
                geometry_normalized={"bbox": [0.0, 0.0, 1.0, 1.0]},
                original_width=width,
                original_height=height,
                source_type="EXTERNAL_GROUND_TRUTH",
                annotation_source="EXTERNAL_GROUND_TRUTH",
                source_element_id=path,
                confidence=1,
                review_status="VERIFIED",
                reviewer="perdaw-dataset-adapter",
                training_allowed=True,
                anonymized=True,
                rights_status=package.rights_status,
                quality_score=82,
            )
            self._session.add(annotation)
            self._session.flush()
            self._session.add(
                ExternalAnnotationLineageORM(
                    annotation_id=annotation.id,
                    source_id=source.id,
                    project_package_id=package.id,
                    original_file=storage_key,
                    annotation_file="PERDAW_FOLDER_LABEL",
                    external_class=external_class,
                    importer_version="PERDAW_ADAPTER_V1",
                    mapping_version=MAPPING_VERSION,
                    normalization_version=NORMALIZATION_VERSION,
                    quality_tier="SILVER",
                )
            )
            imported += 1
        package.ingestion_status = "INGESTED"
        self._session.flush()
        if imported == 0 and reused == 0:
            raise AcquisitionRuleError("LOW_QUALITY_DATASET")
        return {
            "imported": imported,
            "reused": reused,
            "invalid": invalid,
            "quality_tier": "SILVER",
        }


class IfcArchiveImporter:
    def __init__(self, session: Session, storage: DocumentStorage) -> None:
        self._session = session
        self._storage = storage

    def ingest(
        self, *, source_id: str, package_id: str, limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        package = self._session.get(ExternalProjectPackageORM, package_id)
        if package is None or package.project_id is None:
            raise AcquisitionRuleError("PROJECT_PACKAGE_INVALID")
        imported = reused = failed = elements = 0
        errors: list[dict[str, str]] = []
        ifc_files = [
            info
            for info in package.files_json
            if PurePosixPath(str(info["path"])).suffix.lower() == ".ifc"
        ]
        selected_files = ifc_files[offset : offset + limit]
        for info in selected_files:
            path = self._storage.path_for_read(storage_key=str(info["storage_key"]))
            try:
                with self._session.begin_nested():
                    header = path.read_bytes()[:512]
                    if b"ISO-10303-21" not in header:
                        raise AcquisitionRuleError("INVALID_IFC")
                    external_id = f"IFC-{str(info['sha256'])[:20]}"
                    child = self._session.scalar(
                        select(ExternalProjectPackageORM).where(
                            ExternalProjectPackageORM.source_id == source_id,
                            ExternalProjectPackageORM.external_project_id == external_id,
                        )
                    )
                    if child is None:
                        project = ProjectORM(
                            name=f"External IFC {PurePosixPath(str(info['path'])).stem}"[:300],
                            locale="tr",
                            status="ACTIVE",
                        )
                        self._session.add(project)
                        self._session.flush()
                        self._session.add(
                            TrainingProjectManifestORM(
                                project_id=project.id,
                                rights_status=package.rights_status,
                                training_allowed=True,
                                building_use="UNKNOWN",
                                structural_documents=True,
                                data_completeness_score=45,
                                anonymization_status="COMPLETED",
                            )
                        )
                        child = ExternalProjectPackageORM(
                            source_id=source_id,
                            external_project_id=external_id,
                            project_id=project.id,
                            discipline_availability_json=["BIM"],
                            files_json=[info],
                            annotation_files_json=[],
                            project_quality_score=package.project_quality_score,
                            training_eligibility="ELIGIBLE",
                            rights_status=package.rights_status,
                            ingestion_status="PACKAGED",
                        )
                        self._session.add(child)
                        self._session.flush()
                    if child.project_id is None:
                        raise AcquisitionRuleError("PROJECT_PACKAGE_INVALID")
                    name = (f"{str(info['sha256'])[:12]}-{PurePosixPath(str(info['path'])).name}")[
                        :255
                    ]
                    document = self._session.scalar(
                        select(ProjectDocumentORM).where(
                            ProjectDocumentORM.project_id == child.project_id,
                            ProjectDocumentORM.original_name == name,
                        )
                    )
                    if document:
                        reused += 1
                        continue
                    document = ProjectDocumentORM(project_id=child.project_id, original_name=name)
                    self._session.add(document)
                    self._session.flush()
                    version = DocumentVersionORM(
                        id=new_id(),
                        document_id=document.id,
                        project_id=child.project_id,
                        version=1,
                        sha256=str(info["sha256"]),
                        size_bytes=int(info["size"]),
                        media_type="application/x-step",
                        storage_key=str(info["storage_key"]),
                        page_count=0,
                        status="READY",
                    )
                    self._session.add(version)
                    self._session.flush()
                    summary, was_reused = IfcProjectHarvester(self._session).harvest(
                        source_id=source_id,
                        package_id=child.id,
                        document_version_id=version.id,
                        path=path,
                    )
                    child.ingestion_status = "INGESTED"
                    imported += int(not was_reused)
                    reused += int(was_reused)
                    elements += sum(
                        int(value) for value in summary.summary_json.get("elements", {}).values()
                    )
            except Exception as exc:
                failed += 1
                errors.append({"file": str(info["path"]), "error": str(exc)[:300]})
        package.ingestion_status = "INGESTED" if imported or reused else "FAILED"
        self._session.flush()
        return {
            "imported": imported,
            "reused": reused,
            "failed": failed,
            "elements": elements,
            "errors": errors[:20],
            "offset": offset,
            "processed": len(selected_files),
            "next_offset": offset + len(selected_files),
            "complete": offset + len(selected_files) >= len(ifc_files),
            "total_ifc_files": len(ifc_files),
        }


class IfcProjectHarvester:
    def __init__(self, session: Session) -> None:
        self._session = session

    def harvest(
        self, *, source_id: str, package_id: str, document_version_id: str, path: Path
    ) -> tuple[IfcHarvestSummaryORM, bool]:
        existing = self._session.scalar(
            select(IfcHarvestSummaryORM).where(
                IfcHarvestSummaryORM.document_version_id == document_version_id
            )
        )
        if existing:
            return existing, True
        package = self._session.get(ExternalProjectPackageORM, package_id)
        if package is None or package.project_id is None:
            raise AcquisitionRuleError("PROJECT_PACKAGE_INVALID")
        summary = ProjectGraphService(self._session).analyze_ifc(
            project_id=package.project_id, document_version_id=document_version_id, path=path
        )
        elements = list(
            self._session.scalars(
                select(BuildingElementORM).where(
                    BuildingElementORM.project_id == package.project_id
                )
            )
        )
        counts = Counter(row.element_type for row in elements)
        global_ids = [row.source_external_id for row in elements if row.source_external_id]
        uniqueness = len(set(global_ids)) / max(1, len(global_ids))
        qto = sum(1 for row in elements if row.extraction_method == "IFC_QUANTITY_SET") / max(
            1, len(elements)
        )
        geometry = sum(1 for row in elements if row.geometry_json) / max(1, len(elements))
        quality = round(25 + 25 * uniqueness + 25 * qto + 25 * geometry)
        row = IfcHarvestSummaryORM(
            source_id=source_id,
            project_package_id=package_id,
            document_version_id=document_version_id,
            summary_json={
                "storeys": summary.get("level_count", 0),
                "elements": dict(counts),
                "qto_coverage": qto,
                "geometry_coverage": geometry,
                "global_id_uniqueness": uniqueness,
            },
            quality_score=min(100, quality),
            estimator_calibration_allowed=False,
        )
        self._session.add(row)
        self._session.flush()
        return row, False


class DatasetReadinessEngine:
    def __init__(self, session: Session, rules: dict[str, Any]) -> None:
        self._session = session
        self._rules = rules

    def evaluate(self) -> dict[str, Any]:
        classes: dict[str, Any] = {}
        warnings: set[str] = set()
        targets = self._rules.get("class_targets", {})
        real_targets = self._rules.get("real_data_targets", {})
        source_family_by_id = {
            row.id: (row.source_family_id or row.id)
            for row in self._session.scalars(select(DiscoveredDatasetSourceORM))
        }
        annotation_family = {
            row.annotation_id: source_family_by_id.get(row.source_id, row.source_id)
            for row in self._session.scalars(select(ExternalAnnotationLineageORM))
        }
        ifc_render_source = {
            row.id: row.source_id for row in self._session.scalars(select(Ifc2DRenderORM))
        }
        annotation_family.update(
            {
                row.annotation_id: source_family_by_id.get(
                    ifc_render_source.get(row.render_id, ""),
                    ifc_render_source.get(row.render_id, "BIM_DERIVED"),
                )
                for row in self._session.scalars(select(Ifc2DAnnotationLineageORM))
            }
        )
        cross_modal_ids = {
            row.annotation_id
            for row in self._session.scalars(select(CrossModalAnnotationLineageORM))
        }
        for class_name in sorted(ARCH_CLASSES):
            rows = list(
                self._session.scalars(
                    select(VisionAnnotationORM).where(
                        VisionAnnotationORM.class_name == class_name,
                        VisionAnnotationORM.review_status.in_(("VERIFIED", "CORRECTED")),
                        VisionAnnotationORM.training_allowed.is_(True),
                        VisionAnnotationORM.anonymized.is_(True),
                        VisionAnnotationORM.rights_status.in_(TRAINING_RIGHTS),
                    )
                )
            )
            projects = len({row.project_id for row in rows})
            quality = round(sum(row.quality_score for row in rows) / max(1, len(rows)))
            tier_counts = Counter(canonical_source_tier(row.source_type) for row in rows)
            gold = tier_counts["GURSAR_GOLD"]
            real_drawing = tier_counts["REAL_DRAWING_SILVER"]
            bim_derived = tier_counts["BIM_DERIVED_SILVER"]
            external_object = tier_counts["EXTERNAL_OBJECT_SILVER"]
            synthetic_stored = tier_counts["SYNTHETIC"]
            weak = tier_counts["WEAK"]
            cross_modal_reviewed = sum(
                1
                for row in rows
                if row.id in cross_modal_ids or row.previous_annotation_id in cross_modal_ids
            )
            governed = gold + real_drawing + bim_derived + external_object
            real_gold_silver = governed / max(1, len(rows))
            synthetic = synthetic_stored / max(1, len(rows))
            source_families: Counter[str] = Counter()
            for row in rows:
                tier = canonical_source_tier(row.source_type)
                if tier == "SYNTHETIC":
                    source_families["GURSAR_SYNTHETIC_ARCH_V1"] += 1
                    continue
                family = annotation_family.get(row.id)
                if family:
                    source_families[family] += 1
                elif tier == "GURSAR_GOLD":
                    source_families["GURSAR_INTERNAL"] += 1
                else:
                    source_families[tier] += 1
            governed_families = Counter(
                {
                    name: count
                    for name, count in source_families.items()
                    if name != "GURSAR_SYNTHETIC_ARCH_V1"
                }
            )
            concentration = max(governed_families.values(), default=0) / max(1, governed)
            target = int(
                targets.get(class_name, self._rules.get("minimum_annotations_per_class", 500))
            )
            class_real_target = real_targets.get(class_name, {})
            real_requirement = int(class_real_target.get("real_drawing", 0))
            bim_or_real_requirement = int(class_real_target.get("bim_or_real", 0))
            real_target_met = real_drawing >= real_requirement
            bim_target_met = real_drawing + bim_derived >= bim_or_real_requirement
            duplicate_ratio = 0.0
            ready = (
                projects >= int(self._rules["minimum_projects"])
                and len(rows) >= target
                and len(governed_families) >= int(self._rules.get("minimum_source_families", 1))
                and real_target_met
                and bim_target_met
                and quality >= int(self._rules["minimum_quality_score"])
                and duplicate_ratio <= float(self._rules.get("maximum_duplicate_ratio", 1))
            )
            partial = bool(rows) and not ready
            if synthetic > float(self._rules["maximum_synthetic_ratio"]):
                warnings.add("SYNTHETIC_DOMINANCE")
            maximum_bim_ratio = float(self._rules.get("maximum_bim_derived_ratio", 0.50))
            if bim_derived / max(1, governed) > maximum_bim_ratio:
                warnings.add("BIM_DERIVED_DOMINANCE")
            if real_drawing < real_requirement:
                warnings.add("REAL_DRAWING_LOW")
            if bim_or_real_requirement and bim_derived > real_drawing and not bim_target_met:
                warnings.add("BIM_DERIVED_DOMINANCE")
            if governed == 0:
                warnings.add("REAL_DATA_LOW")
            if len(governed_families) < int(self._rules.get("minimum_source_families", 1)):
                warnings.add("SOURCE_DIVERSITY_LOW")
            if concentration > float(self._rules.get("source_concentration_warning", 0.6)):
                warnings.add("SOURCE_CONCENTRATION")
            non_bim_governed = gold + real_drawing + external_object
            bim_cap = math.floor(
                non_bim_governed * maximum_bim_ratio / max(1e-9, 1 - maximum_bim_ratio)
            )
            bim_selected = min(bim_derived, bim_cap)
            selected_governed = non_bim_governed + bim_selected
            maximum_synthetic_ratio = float(self._rules.get("maximum_synthetic_ratio", 0.40))
            synthetic_cap = math.floor(
                selected_governed * maximum_synthetic_ratio / max(1e-9, 1 - maximum_synthetic_ratio)
            )
            synthetic_selected = min(synthetic_stored, synthetic_cap)
            candidate_total = selected_governed + synthetic_selected
            classes[class_name] = {
                "projects": projects,
                "annotations": len(rows),
                "target": target,
                "quality": quality,
                "gold_silver_ratio": round(real_gold_silver, 4),
                "synthetic_ratio": round(synthetic, 4),
                "candidate_synthetic_ratio": round(synthetic_selected / max(1, candidate_total), 4),
                "source_families": len(governed_families),
                "source_concentration": round(concentration, 4),
                "duplicate_ratio": duplicate_ratio,
                "total_annotations": len(rows),
                "real_drawing_annotations": real_drawing,
                "bim_derived_annotations": bim_derived,
                "bim_derived_selected": bim_selected,
                "cross_modal_reviewed": cross_modal_reviewed,
                "gold_annotations": gold,
                "external_object_annotations": external_object,
                "synthetic_annotations": synthetic_stored,
                "synthetic_selected": synthetic_selected,
                "weak_annotations": weak,
                "real_project_count": len(
                    {
                        row.project_id
                        for row in rows
                        if canonical_source_tier(row.source_type) != "SYNTHETIC"
                    }
                ),
                "real_drawing_ratio": round(real_drawing / max(1, len(rows)), 4),
                "bim_derived_ratio": round(bim_derived / max(1, len(rows)), 4),
                "candidate_bim_derived_ratio": round(bim_selected / max(1, candidate_total), 4),
                "real_target": real_requirement,
                "bim_or_real_target": bim_or_real_requirement,
                "status": "READY" if ready else ("PARTIALLY_READY" if partial else "NOT_READY"),
            }
        statuses = {item["status"] for item in classes.values()}
        overall = (
            "READY"
            if statuses == {"READY"}
            else ("NOT_READY" if statuses == {"NOT_READY"} else "PARTIALLY_READY")
        )
        source_distribution_raw = Counter(
            canonical_source_tier(row.source_type)
            for row in self._session.scalars(select(VisionAnnotationORM))
        )
        source_distribution = {
            name: source_distribution_raw.get(name, 0)
            for name in (
                "GURSAR_GOLD",
                "REAL_DRAWING_SILVER",
                "BIM_DERIVED_SILVER",
                "EXTERNAL_OBJECT_SILVER",
                "SYNTHETIC",
                "WEAK",
            )
        }
        ready_classes = sorted(name for name, item in classes.items() if item["status"] == "READY")
        foundational_ready = all(
            name in ready_classes for name in ("WALL", "ROOM", "DOOR", "WINDOW")
        )
        return {
            "status": overall,
            "classes": classes,
            "source_distribution": source_distribution,
            "warnings": sorted(warnings),
            "ready_classes": ready_classes,
            "next_recommendation": "COLLECT_MORE_REAL_DATA"
            if {"REAL_DATA_LOW", "REAL_DRAWING_LOW"} & warnings
            else "REVIEW_AND_BUILD_CANDIDATE",
            "training_event": "TRAINING_READY" if foundational_ready else None,
            "foundational_classes_ready": foundational_ready,
            "dataset_candidate_name": (
                "gursar_arch_vision_v0.1_candidate" if foundational_ready else None
            ),
        }


class SyntheticProjectFactory:
    def __init__(self, session: Session, storage: DocumentStorage) -> None:
        self._session = session
        self._storage = storage

    def generate(
        self, *, count: int, seed: int, building_type: str, floors_min: int, floors_max: int
    ) -> dict[str, Any]:
        generated = reused = annotations = 0
        ids: list[str] = []
        for index in range(count):
            spec = self._spec(seed + index, building_type, floors_min, floors_max)
            spec_bytes = json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()
            spec_hash = hashlib.sha256(spec_bytes).hexdigest()
            existing = self._session.scalar(
                select(SyntheticProjectORM).where(
                    SyntheticProjectORM.generator_version == SYNTHETIC_VERSION,
                    SyntheticProjectORM.spec_hash == spec_hash,
                )
            )
            if existing:
                reused += 1
                ids.append(existing.id)
                continue
            image, items = self._render(spec)
            validation = self._validate(items)
            if not validation["valid"]:
                raise AcquisitionRuleError("SYNTHETIC_GEOMETRY_INVALID")
            png = io.BytesIO()
            image.save(png, "PNG", optimize=True)
            image_bytes = png.getvalue()
            ground_truth = {
                "version": SYNTHETIC_VERSION,
                "spec": spec,
                "annotations": items,
                "transforms": spec["transforms"],
            }
            ground_bytes = json.dumps(ground_truth, sort_keys=True).encode()
            output_checksum = hashlib.sha256(image_bytes + ground_bytes).hexdigest()
            image_key = f"training-derivatives/synthetic/{spec_hash}.png"
            annotation_key = f"training-derivatives/synthetic/{spec_hash}.json"
            self._storage.save(storage_key=image_key, source=io.BytesIO(image_bytes))
            self._storage.save(storage_key=annotation_key, source=io.BytesIO(ground_bytes))
            project = ProjectORM(
                name=f"Synthetic {building_type} {seed + index}", locale="tr", status="ACTIVE"
            )
            self._session.add(project)
            self._session.flush()
            document = ProjectDocumentORM(
                project_id=project.id, original_name=f"synthetic-{seed + index}.png"
            )
            self._session.add(document)
            self._session.flush()
            version_id = new_id()
            version = DocumentVersionORM(
                id=version_id,
                document_id=document.id,
                project_id=project.id,
                version=1,
                sha256=hashlib.sha256(image_bytes).hexdigest(),
                size_bytes=len(image_bytes),
                media_type="image/png",
                storage_key=image_key,
                page_count=1,
                status="READY",
            )
            self._session.add(version)
            sheet = SheetORM(
                document_version_id=version_id,
                page_number=1,
                title="Synthetic floor plan",
                discipline="ARCHITECTURAL",
                sheet_number="SYN-001",
                sheet_name="Synthetic Plan",
                revision="00",
                drawing_type="PLAN",
                page_type="RASTER",
                rotation=0,
                orientation="LANDSCAPE",
                standard_size="CUSTOM",
                review_status="VERIFIED",
                metadata_json={"synthetic": True},
                width_points=image.width,
                height_points=image.height,
            )
            self._session.add(sheet)
            self._session.flush()
            derivative = TrainingImageDerivativeORM(
                project_id=project.id,
                document_version_id=version_id,
                sheet_id=sheet.id,
                page_number=1,
                render_profile=SYNTHETIC_VERSION,
                render_version=SYNTHETIC_VERSION,
                width=image.width,
                height=image.height,
                dpi=144,
                storage_key=image_key,
                checksum_sha256=version.sha256,
                perceptual_hash=version.sha256[:16],
                rights_status="LICENSED_BY_GURSAR",
                anonymization_status="COMPLETED",
                redaction_version=None,
            )
            self._session.add(derivative)
            self._session.add(
                TrainingProjectManifestORM(
                    project_id=project.id,
                    rights_status="LICENSED_BY_GURSAR",
                    training_allowed=True,
                    building_use=building_type,
                    floor_count=spec["floors"],
                    architectural_documents=True,
                    data_completeness_score=75,
                    anonymization_status="COMPLETED",
                )
            )
            synthetic = SyntheticProjectORM(
                project_id=project.id,
                generator_version=SYNTHETIC_VERSION,
                seed=seed + index,
                spec_hash=spec_hash,
                spec_json=spec,
                manifest_json={"source": "synthetic", "rights": "LICENSED_BY_GURSAR"},
                output_checksum=output_checksum,
                image_storage_key=image_key,
                annotation_storage_key=annotation_key,
                validation_json=validation,
                status="GENERATED",
            )
            self._session.add(synthetic)
            self._session.flush()
            for item in items:
                self._session.add(
                    VisionAnnotationORM(
                        project_id=project.id,
                        document_version_id=version_id,
                        sheet_id=sheet.id,
                        image_derivative_id=derivative.id,
                        task_type=f"{item['class_name']}_DETECTION",
                        class_name=item["class_name"],
                        geometry_type="BBOX",
                        geometry_normalized={"bbox": item["bbox"]},
                        original_width=image.width,
                        original_height=image.height,
                        source_type="SYNTHETIC_GROUND_TRUTH",
                        annotation_source="SYNTHETIC_GROUND_TRUTH",
                        source_element_id=item["id"],
                        confidence=1,
                        review_status="VERIFIED",
                        reviewer="synthetic-factory",
                        training_allowed=True,
                        anonymized=True,
                        rights_status="LICENSED_BY_GURSAR",
                        quality_score=80,
                    )
                )
                annotations += 1
            generated += 1
            ids.append(synthetic.id)
        self._session.flush()
        return {
            "generated": generated,
            "reused": reused,
            "annotations": annotations,
            "project_ids": ids,
            "generator_version": SYNTHETIC_VERSION,
        }

    @staticmethod
    def _spec(seed: int, building_type: str, floors_min: int, floors_max: int) -> dict[str, Any]:
        rng = random.Random(seed)
        return {
            "seed": seed,
            "building_type": building_type,
            "footprint_width": rng.randint(12, 24),
            "footprint_length": rng.randint(16, 30),
            "floors": rng.randint(floors_min, floors_max),
            "floor_height": 3.0,
            "room_count": rng.randint(4, 10),
            "corridor_width": round(rng.uniform(1.1, 2.2), 2),
            "wall_thickness": round(rng.uniform(0.12, 0.30), 2),
            "column_size": round(rng.choice((0.3, 0.35, 0.4, 0.5)), 2),
            "stair_count": 1 if rng.random() < 0.8 else 0,
            "paper_size": rng.choice(["A1", "A2", "A3"]),
            "drawing_scale": rng.choice([50, 100]),
            "drawing_style": rng.choice(["clean", "dimensioned", "low-contrast", "scan-like"]),
            "transforms": {
                "rotation": round(rng.uniform(-0.6, 0.6), 2),
                "compression": rng.randint(78, 95),
                "noise": rng.randint(0, 5),
                "grayscale": rng.random() < 0.35,
            },
        }

    @staticmethod
    def _render(spec: dict[str, Any]) -> tuple[Image.Image, list[dict[str, Any]]]:
        style = str(spec["drawing_style"])
        background = (
            (244, 242, 235)
            if style == "scan-like"
            else ((248, 248, 246) if style == "low-contrast" else "white")
        )
        image = Image.new("RGB", (1024, 768), background)
        draw = ImageDraw.Draw(image)
        items: list[dict[str, Any]] = []
        line_color = (65, 65, 65) if style == "low-contrast" else "black"
        line_width = 2 if style in {"low-contrast", "scan-like"} else 3

        def add(cls: str, bbox: list[float], idx: int) -> None:
            items.append({"id": f"{cls}-{idx}", "class_name": cls, "bbox": bbox})
            draw.rectangle(
                (bbox[0] * 1024, bbox[1] * 768, bbox[2] * 1024, bbox[3] * 768),
                outline=line_color,
                width=line_width,
            )

        add("WALL", [0.05, 0.05, 0.95, 0.08], 1)
        add("WALL", [0.05, 0.92, 0.95, 0.95], 2)
        add("WALL", [0.05, 0.08, 0.08, 0.92], 3)
        add("WALL", [0.92, 0.08, 0.95, 0.92], 4)
        room_count = int(spec["room_count"])
        cols = 2
        rows = (room_count + 1) // 2
        for idx in range(room_count):
            col, row = idx % cols, idx // cols
            x1, x2 = 0.09 + col * 0.42, 0.49 + col * 0.42
            y1, y2 = 0.10 + row * (0.78 / rows), 0.10 + (row + 1) * (0.78 / rows)
            add("ROOM", [x1, y1, x2, y2], idx)
            add("DOOR", [x2 - 0.04, y2 - 0.02, x2, y2 + 0.02], idx)
            add("WINDOW", [x1 + 0.08, y1 - 0.01, x1 + 0.16, y1 + 0.01], idx)
            add("WALL", [x2 - 0.006, y1, x2 + 0.006, y2], 100 + idx)
            if row < rows - 1:
                add("WALL", [x1, y2 - 0.006, x2, y2 + 0.006], 200 + idx)
        for idx, bbox in enumerate(
            (
                [0.09, 0.10, 0.12, 0.14],
                [0.88, 0.10, 0.91, 0.14],
                [0.09, 0.86, 0.12, 0.90],
                [0.88, 0.86, 0.91, 0.90],
            )
        ):
            add("COLUMN", list(bbox), idx)
        if int(spec["stair_count"]):
            add("STAIR", [0.42, 0.38, 0.58, 0.62], 1)
        if style == "dimensioned":
            draw.text((80, 730), f"OLCEK 1:{spec['drawing_scale']}", fill=line_color)
        return image, items

    @staticmethod
    def _validate(items: list[dict[str, Any]]) -> dict[str, Any]:
        errors: list[str] = []
        for item in items:
            bbox = item["bbox"]
            if not (0 <= bbox[0] < bbox[2] <= 1 and 0 <= bbox[1] < bbox[3] <= 1):
                errors.append(f"INVALID_BBOX:{item['id']}")
        if not any(item["class_name"] == "ROOM" for item in items):
            errors.append("NO_ROOM")
        rooms = [item["bbox"] for item in items if item["class_name"] == "ROOM"]
        for index, first in enumerate(rooms):
            for second in rooms[index + 1 :]:
                overlap_width = min(first[2], second[2]) - max(first[0], second[0])
                overlap_height = min(first[3], second[3]) - max(first[1], second[1])
                if overlap_width > 0 and overlap_height > 0:
                    errors.append("OVERLAPPING_ROOMS")
        for item in items:
            if item["class_name"] not in {"DOOR", "WINDOW"}:
                continue
            bbox = item["bbox"]
            near_room = any(
                bbox[2] >= room[0]
                and bbox[0] <= room[2]
                and bbox[3] >= room[1]
                and bbox[1] <= room[3]
                for room in rooms
            )
            if not near_room:
                errors.append(f"FLOATING_{item['class_name']}:{item['id']}")
        return {"valid": not errors, "errors": errors}


def load_acquisition_rules() -> dict[str, Any]:
    path = Path(__file__).parents[1] / "config" / "acquisition.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))
