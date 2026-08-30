from __future__ import annotations

import hashlib
import mimetypes
import ssl
import time
import zipfile
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
import truststore

from app.modules.knowledge_acquisition.application.processing import (
    DocumentParser,
    extract_structured_rules,
    semantic_chunks,
)
from app.modules.knowledge_acquisition.domain.models import (
    AcquisitionReport,
    KnowledgeSourceDefinition,
    KnowledgeStatus,
    RedistributionPolicy,
)
from app.modules.knowledge_acquisition.infrastructure.config import KnowledgeConfig
from app.modules.knowledge_acquisition.infrastructure.repository import KnowledgeRepository


class SourcePolicy:
    def __init__(self, config: KnowledgeConfig) -> None:
        trusted = config.trusted
        tier_1 = trusted["trusted_hosts"]["tier_1"]
        tier_2 = trusted["trusted_hosts"]["tier_2"]
        self._exact = {host.casefold() for host in tier_1.get("exact", [])}
        self._suffixes = tuple(
            suffix.casefold()
            for suffix in (*tier_1.get("suffixes", []), *tier_2.get("suffixes", []))
        )
        self._blacklisted = {host.casefold() for host in trusted.get("blacklisted_hosts", [])}

    def validate(self, source: KnowledgeSourceDefinition) -> None:
        if not 0 <= source.quality_score <= 100:
            raise ValueError("SourceQualityScore must be between 0 and 100")
        parsed = urlparse(source.source_url)
        host = (parsed.hostname or "").casefold()
        if parsed.scheme != "https":
            raise ValueError("Only HTTPS sources are accepted")
        if not host or host in self._blacklisted:
            raise ValueError(f"Blacklisted or missing host: {host}")
        if host not in self._exact and not any(host.endswith(suffix) for suffix in self._suffixes):
            raise ValueError(f"Source host is not trusted: {host}")
        if (
            source.redistribution_policy == RedistributionPolicy.ALLOWED
            and source.copyright_status == "copyrighted"
        ):
            raise ValueError("Copyrighted sources require METADATA_ONLY or explicit license")


class KnowledgeAcquisitionPipeline:
    def __init__(
        self,
        repository: KnowledgeRepository,
        config: KnowledgeConfig,
        *,
        storage_root: Path = Path("./data/knowledge"),
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._repository = repository
        self._config = config
        self._policy = SourcePolicy(config)
        self._parser = DocumentParser()
        self._storage_root = storage_root
        self._storage_root.mkdir(parents=True, exist_ok=True)
        network = config.pipeline["network"]
        ssl_context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        self._client = httpx.Client(
            follow_redirects=True,
            timeout=float(network["timeout_seconds"]),
            transport=transport,
            verify=ssl_context,
            headers={"User-Agent": str(config.trusted["user_agent"])},
        )
        self._transport_supplied = transport is not None
        self._last_request_at = 0.0

    def run(
        self,
        sources: tuple[KnowledgeSourceDefinition, ...] | None = None,
        *,
        approve_official: bool = False,
    ) -> AcquisitionReport:
        run = self._repository.start_run()
        report = AcquisitionReport(run_id=run.id, started_at=run.started_at)
        queue = list(sources or self._config.sources())
        report.discovered = len(queue)
        seen_keys: set[str] = set()
        while queue:
            source = queue.pop(0)
            if source.key in seen_keys:
                continue
            seen_keys.add(source.key)
            try:
                discovered = self._process_source(
                    source,
                    report,
                    approve_official=approve_official,
                )
                queue.extend(discovered)
                report.discovered += len(discovered)
            except (httpx.HTTPError, OSError, ValueError, zipfile.BadZipFile) as exc:
                report.failed.append(
                    {"key": source.key, "url": source.source_url, "error": str(exc)[:500]}
                )
                report.rejected += 1
        report.completed_at = datetime.now(UTC)
        report.failed = list(report.failed)
        status = "COMPLETED_WITH_ERRORS" if report.failed else "COMPLETED"
        counters = {
            key: value
            for key, value in asdict(report).items()
            if key not in {"failed", "started_at", "completed_at"}
        }
        self._repository.finish_run(
            run.id,
            status=status,
            counters=counters,
            failures=report.failed,
        )
        self._repository.seed_formulas()
        return report

    def _process_source(
        self,
        source: KnowledgeSourceDefinition,
        report: AcquisitionReport,
        *,
        approve_official: bool,
    ) -> list[KnowledgeSourceDefinition]:
        self._policy.validate(source)
        source_row = self._repository.upsert_source(source)
        latest = self._repository.latest_version(source_row.id)
        if source.redistribution_policy == RedistributionPolicy.METADATA_ONLY:
            checksum = hashlib.sha256(
                f"{source.key}|{source.name}|{source.version_label}".encode()
            ).hexdigest()
            if latest is not None and latest.checksum_sha256 == checksum:
                report.unchanged += 1
                return []
            self._repository.create_version(
                source=source_row,
                definition=source,
                checksum=checksum,
                content_type="application/x-metadata-only",
                parser_name="metadata-only",
                parsed_text="",
                ocr_derived=False,
                chunks=[],
                rules=[],
                status=KnowledgeStatus.METADATA_ONLY,
                metadata={"full_text_ingestion": "REJECTED_BY_LICENSE_POLICY"},
            )
            report.metadata_only += 1
            report.ingested += 1
            return []

        response = self._download(source, latest)
        if response.status_code == 304:
            report.unchanged += 1
            return []
        report.downloaded += 1
        checksum = hashlib.sha256(response.content).hexdigest()
        if latest is not None and latest.checksum_sha256 == checksum:
            report.unchanged += 1
            return []
        duplicate = self._repository.find_by_checksum(checksum)
        if duplicate is not None:
            report.duplicates += 1
            return []
        filename = Path(urlparse(str(response.url)).path).name or f"{source.key}.html"
        suffix = (
            Path(filename).suffix
            or mimetypes.guess_extension(response.headers.get("content-type", "").split(";", 1)[0])
            or ".bin"
        )
        raw_path = self._storage_root / "documents" / f"{checksum}{suffix}"
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(response.content)
        parsed = self._parser.parse(
            response.content,
            content_type=response.headers.get("content-type", "").split(";", 1)[0],
            filename=filename,
        )
        if not parsed.text.strip():
            raise ValueError("Parser returned no searchable content")
        chunking = self._config.pipeline["chunking"]
        chunks = semantic_chunks(
            parsed,
            source,
            target_chars=int(chunking["target_chars"]),
            overlap_chars=int(chunking["overlap_chars"]),
            dimensions=int(self._config.pipeline["embedding"]["dimensions"]),
        )
        rules = extract_structured_rules(
            chunks,
            source,
            str(self._config.pipeline["rule_extraction"]["numeric_pattern"]),
        )
        version = self._repository.create_version(
            source=source_row,
            definition=replace(source, source_url=str(response.url)),
            checksum=checksum,
            content_type=parsed.content_type,
            parser_name=parsed.parser_name,
            parsed_text=parsed.text,
            ocr_derived=parsed.ocr_derived,
            chunks=chunks,
            rules=rules,
            status=KnowledgeStatus.VALIDATED,
            etag=response.headers.get("etag"),
            last_modified=response.headers.get("last-modified"),
            metadata={
                **parsed.metadata,
                "requested_url": source.source_url,
                "raw_storage_path": str(raw_path),
                "license": source.license,
                "copyright_status": source.copyright_status,
                "redistribution_policy": source.redistribution_policy.value,
            },
        )
        report.ingested += 1
        report.chunks_created += len(chunks)
        report.rules_created += len(rules)
        if approve_official:
            self._repository.approve(version.id)
            self._repository.bridge_to_legal_knowledge(version.id)
            report.conflicts_created += self._repository.detect_conflicts(version.id)
            report.active += 1
        return self._discovered_sources(source, parsed.metadata.get("links", []))

    def _download(
        self,
        source: KnowledgeSourceDefinition,
        latest: Any | None,
    ) -> httpx.Response:
        if self._config.pipeline["network"]["respect_robots_txt"] and not self._robots_allowed(
            source.source_url
        ):
            raise ValueError("robots.txt disallows acquisition")
        headers: dict[str, str] = {}
        if latest is not None:
            if getattr(latest, "etag", None):
                headers["If-None-Match"] = latest.etag
            if getattr(latest, "last_modified", None):
                headers["If-Modified-Since"] = latest.last_modified
        retries = int(self._config.pipeline["network"]["retries"])
        last_error: httpx.HTTPError | None = None
        for attempt in range(retries + 1):
            try:
                self._rate_limit()
                response = self._client.get(source.source_url, headers=headers)
                if response.status_code == 304:
                    return response
                response.raise_for_status()
                limit = int(self._config.pipeline["network"]["max_download_bytes"])
                if len(response.content) > limit:
                    raise ValueError(f"Document exceeds {limit} byte safety limit")
                return response
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt == retries:
                    raise
        assert last_error is not None
        raise last_error

    def _robots_allowed(self, url: str) -> bool:
        if self._transport_supplied:
            return True
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        try:
            response = self._client.get(robots_url)
            if response.status_code >= 400:
                return True
            parser = RobotFileParser()
            parser.set_url(robots_url)
            parser.parse(response.text.splitlines())
            return parser.can_fetch(str(self._config.trusted["user_agent"]), url)
        except httpx.HTTPError:
            return True

    def _rate_limit(self) -> None:
        if self._transport_supplied:
            return
        interval = float(self._config.pipeline["network"]["rate_limit_seconds"])
        remaining = interval - (time.monotonic() - self._last_request_at)
        if remaining > 0:
            time.sleep(remaining)
        self._last_request_at = time.monotonic()

    def _discovered_sources(
        self,
        parent: KnowledgeSourceDefinition,
        links: object,
    ) -> list[KnowledgeSourceDefinition]:
        if not parent.discover_links or not isinstance(links, list):
            return []
        maximum = int(self._config.pipeline["network"]["max_discovered_per_source"])
        discovered: list[KnowledgeSourceDefinition] = []
        for title, href in links:
            url = urljoin(parent.source_url, str(href))
            path = urlparse(url).path.casefold()
            if not path.endswith((".pdf", ".docx", ".xlsx", ".csv", ".json", ".xml")):
                continue
            digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
            candidate = replace(
                parent,
                key=f"{parent.key}-link-{digest}",
                name=(str(title).strip() or Path(path).name)[:500],
                source_url=url,
                discover_links=False,
                version_label=None,
                metadata={**parent.metadata, "discovered_from": parent.source_url},
            )
            try:
                self._policy.validate(candidate)
            except ValueError:
                continue
            discovered.append(candidate)
            if len(discovered) >= maximum:
                break
        return discovered
