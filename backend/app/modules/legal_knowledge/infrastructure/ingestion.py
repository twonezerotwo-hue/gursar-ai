from __future__ import annotations

import hashlib
import re
import ssl
from dataclasses import dataclass, field
from html.parser import HTMLParser
from io import BytesIO
from typing import ClassVar
from urllib.parse import urljoin, urlparse

import httpx
import truststore
from pypdf import PdfReader
from sqlalchemy.orm import Session

from app.modules.legal_knowledge.domain.models import LegalSourceDefinition
from app.modules.legal_knowledge.infrastructure.repository import LegalKnowledgeRepository

OFFICIAL_HOST_SUFFIX = ".gov.tr"
MAX_DOWNLOAD_BYTES = 40 * 1024 * 1024
MAX_DOCUMENT_CHARS = 6_000_000


@dataclass(slots=True)
class IngestionResult:
    imported: int = 0
    unchanged: int = 0
    failed: list[dict[str, str]] = field(default_factory=list)
    discovered: int = 0


class _OfficialHtmlParser(HTMLParser):
    BLOCK_TAGS: ClassVar[set[str]] = {
        "article",
        "br",
        "div",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "li",
        "p",
        "section",
        "table",
        "td",
        "th",
        "tr",
    }
    SKIP_TAGS: ClassVar[set[str]] = {"script", "style", "svg", "noscript"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._skip_depth = 0
        self._current_href: str | None = None
        self._anchor_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "a":
            self._current_href = dict(attrs).get("href")
            self._anchor_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag == "a" and self._current_href:
            title = " ".join("".join(self._anchor_parts).split())
            self.links.append((title, self._current_href))
            self._current_href = None
            self._anchor_parts = []
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        self.parts.append(data)
        if self._current_href:
            self._anchor_parts.append(data)

    def text(self) -> str:
        lines = [" ".join(line.split()) for line in "".join(self.parts).splitlines()]
        return "\n".join(line for line in lines if line)


class LegalCorpusIngestor:
    def __init__(
        self,
        session: Session,
        *,
        timeout_seconds: float = 90,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._repository = LegalKnowledgeRepository(session)
        ssl_context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        self._client = httpx.Client(
            follow_redirects=True,
            timeout=timeout_seconds,
            transport=transport,
            verify=ssl_context,
            headers={"User-Agent": "GURSAR-AI-LegalCorpus/1.0 (+official-source-indexer)"},
        )

    def ingest(
        self,
        sources: tuple[LegalSourceDefinition, ...],
        *,
        missing_only: bool = False,
    ) -> IngestionResult:
        result = IngestionResult()
        queue = list(sources)
        seen_urls: set[str] = set()
        while queue:
            source = queue.pop(0)
            canonical_url = self._canonical_url(source.source_url)
            if canonical_url in seen_urls:
                continue
            seen_urls.add(canonical_url)
            if (
                missing_only
                and not source.discover_official_links
                and self._repository.has_document(source.key)
            ):
                result.unchanged += 1
                continue
            try:
                response = self._fetch(source.source_url)
                text, content_type, links = self._extract(response)
                chunks = chunk_legal_text(text)
                if not chunks:
                    raise ValueError("No searchable text could be extracted")
                checksum = hashlib.sha256(text.encode("utf-8")).hexdigest()
                _, changed = self._repository.replace_document(
                    source_key=source.key,
                    title=source.title,
                    category=source.category,
                    authority=source.authority,
                    source_url=str(response.url),
                    published_on=source.published_on,
                    official_gazette_number=source.official_gazette_number,
                    checksum_sha256=checksum,
                    content_type=content_type,
                    text=text,
                    chunks=chunks,
                    metadata={**source.metadata, "requested_url": source.source_url},
                )
                if changed:
                    result.imported += 1
                else:
                    result.unchanged += 1
                if source.discover_official_links:
                    discovered = self._discover_sources(source, links)
                    result.discovered += len(discovered)
                    queue.extend(discovered)
            except (httpx.HTTPError, OSError, ValueError) as exc:
                result.failed.append(
                    {"key": source.key, "url": source.source_url, "error": str(exc)[:300]}
                )
        return result

    def _fetch(self, url: str) -> httpx.Response:
        self._assert_official_url(url)
        response = self._client.get(url)
        response.raise_for_status()
        content_length = int(response.headers.get("content-length", len(response.content)))
        if content_length > MAX_DOWNLOAD_BYTES or len(response.content) > MAX_DOWNLOAD_BYTES:
            raise ValueError(f"Document exceeds {MAX_DOWNLOAD_BYTES} byte safety limit")
        return response

    @staticmethod
    def _extract(response: httpx.Response) -> tuple[str, str, list[tuple[str, str]]]:
        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        is_pdf = content_type == "application/pdf" or response.content.startswith(b"%PDF")
        if is_pdf:
            reader = PdfReader(BytesIO(response.content))
            pages = []
            for index, page in enumerate(reader.pages, start=1):
                page_text = page.extract_text() or ""
                if page_text.strip():
                    pages.append(f"[Sayfa {index}]\n{page_text}")
            text = "\n\n".join(pages)
            return clean_document_text(text), "application/pdf", []

        parser = _OfficialHtmlParser()
        parser.feed(response.text)
        links = [(title, urljoin(str(response.url), href)) for title, href in parser.links]
        return clean_document_text(parser.text()), content_type or "text/html", links

    def _discover_sources(
        self,
        parent: LegalSourceDefinition,
        links: list[tuple[str, str]],
    ) -> list[LegalSourceDefinition]:
        discovered: list[LegalSourceDefinition] = []
        seen: set[str] = set()
        for title, url in links:
            canonical = self._canonical_url(url)
            if canonical in seen or not self._looks_like_official_document(url):
                continue
            seen.add(canonical)
            digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
            display_title = title if len(title) >= 5 and not title.startswith("http") else url
            discovered.append(
                LegalSourceDefinition(
                    key=f"{parent.key}-link-{digest}",
                    title=display_title[:500],
                    category=(
                        "technical_specification"
                        if parent.category == "technical_specification_catalog"
                        else "linked_official_legislation"
                    ),
                    authority=parent.authority,
                    source_url=url,
                    metadata={"discovered_from": parent.source_url},
                )
            )
        return discovered

    @classmethod
    def _looks_like_official_document(cls, url: str) -> bool:
        try:
            cls._assert_official_url(url)
        except ValueError:
            return False
        parsed = urlparse(url)
        path = parsed.path.casefold()
        return path.endswith(".pdf") or (
            parsed.hostname is not None
            and parsed.hostname.casefold().endswith("resmigazete.gov.tr")
            and "/eskiler/" in path
            and path.endswith((".htm", ".html"))
        )

    @staticmethod
    def _assert_official_url(url: str) -> None:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").casefold()
        if parsed.scheme != "https" or not hostname.endswith(OFFICIAL_HOST_SUFFIX):
            raise ValueError(f"Non-official or insecure source URL rejected: {url}")

    @staticmethod
    def _canonical_url(url: str) -> str:
        parsed = urlparse(url)
        return parsed._replace(fragment="").geturl()


def clean_document_text(text: str) -> str:
    text = text.replace("\x00", " ").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:MAX_DOCUMENT_CHARS]


def chunk_legal_text(
    text: str,
    *,
    target_chars: int = 1600,
    overlap_chars: int = 180,
) -> list[tuple[str | None, str]]:
    paragraphs = [
        paragraph.strip() for paragraph in re.split(r"\n\s*\n", text) if paragraph.strip()
    ]
    chunks: list[tuple[str | None, str]] = []
    current = ""
    current_heading: str | None = None
    for paragraph in paragraphs:
        heading = _heading_from_paragraph(paragraph)
        if heading:
            current_heading = heading
        if len(current) + len(paragraph) + 2 <= target_chars:
            current = f"{current}\n\n{paragraph}".strip()
            continue
        if current:
            chunks.append((current_heading, current))
            overlap = current[-overlap_chars:] if overlap_chars else ""
            current = f"{overlap}\n\n{paragraph}".strip()
        else:
            for start in range(0, len(paragraph), target_chars - overlap_chars):
                piece = paragraph[start : start + target_chars]
                if piece:
                    chunks.append((current_heading, piece))
            current = ""
    if current:
        chunks.append((current_heading, current))
    return chunks


def _heading_from_paragraph(paragraph: str) -> str | None:
    first_line = paragraph.splitlines()[0].strip()
    if len(first_line) > 180:
        return None
    if re.match(r"^(MADDE|BÖLÜM|KISIM|EK|[0-9]+(?:\.[0-9]+)*)\b", first_line, re.IGNORECASE):
        return first_line
    return None
