from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
import zipfile
from collections import Counter
from html.parser import HTMLParser
from pathlib import PurePosixPath
from typing import Any, ClassVar
from xml.etree import ElementTree

from pypdf import PdfReader

from app.modules.knowledge_acquisition.domain.models import (
    KnowledgeSourceDefinition,
    ParsedDocument,
    SemanticChunk,
)

TOKEN_PATTERN = re.compile(r"[a-zA-Z0-9çğıöşüÇĞİÖŞÜ]+")
LEGAL_HEADING_PATTERN = re.compile(
    r"^(?P<label>(?:MADDE|GEÇİCİ MADDE|BÖLÜM|KISIM|EK)\s*[A-ZÇĞİÖŞÜ0-9./-]*)",
    re.IGNORECASE,
)
SPEC_HEADING_PATTERN = re.compile(
    r"^(?P<label>\d+(?:\.\d+)*\s+.{3,160}|(?:MALZEMELER|UYGULAMA|ÖLÇÜM|KABUL)\b.*)",
    re.IGNORECASE,
)


def _decode_web_text(content: bytes) -> str:
    """Decode Turkish public web pages without silently preserving mojibake."""
    if content.startswith(b"\xef\xbb\xbf"):
        return content.decode("utf-8-sig")
    candidates: list[tuple[int, int, str]] = []
    for priority, encoding in enumerate(("utf-8", "cp1254", "iso-8859-9")):
        try:
            decoded = content.decode(encoding)
        except UnicodeDecodeError:
            continue
        corruption = decoded.count("\ufffd") + sum(
            decoded.count(marker) for marker in ("Ã", "Â", "Ä", "Å")
        )
        candidates.append((corruption, priority, decoded))
    if candidates:
        return min(candidates, key=lambda candidate: (candidate[0], candidate[1]))[2]
    return content.decode("utf-8", errors="replace")


def normalize_text(value: str) -> str:
    translated = value.casefold().translate(
        str.maketrans({"ı": "i", "ğ": "g", "ü": "u", "ş": "s", "ö": "o", "ç": "c"})
    )
    return " ".join(re.findall(r"[a-z0-9]+", translated))


def hashed_embedding(text: str, dimensions: int) -> tuple[float, ...]:
    vector = [0.0] * dimensions
    counts = Counter(normalize_text(text).split())
    for token, count in counts.items():
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign * (1.0 + math.log(count))
    magnitude = math.sqrt(sum(value * value for value in vector))
    if magnitude:
        vector = [value / magnitude for value in vector]
    return tuple(vector)


def cosine_similarity(left: list[float] | tuple[float, ...], right: tuple[float, ...]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True))


class _TextHtmlParser(HTMLParser):
    SKIP: ClassVar[set[str]] = {"script", "style", "svg", "noscript"}
    BLOCKS: ClassVar[set[str]] = {
        "article",
        "br",
        "div",
        "h1",
        "h2",
        "h3",
        "h4",
        "li",
        "p",
        "section",
        "table",
        "td",
        "th",
        "tr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._skip = 0
        self._href: str | None = None
        self._anchor: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP:
            self._skip += 1
            return
        if self._skip:
            return
        if tag in self.BLOCKS:
            self.parts.append("\n")
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._anchor = []

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self._skip:
            self._skip -= 1
            return
        if self._skip:
            return
        if tag == "a" and self._href:
            self.links.append((" ".join("".join(self._anchor).split()), self._href))
            self._href = None
            self._anchor = []
        if tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        self.parts.append(data)
        if self._href:
            self._anchor.append(data)

    def text(self) -> str:
        return clean_text("".join(self.parts))


class DocumentParser:
    def parse(
        self,
        content: bytes,
        *,
        content_type: str,
        filename: str,
    ) -> ParsedDocument:
        suffix = PurePosixPath(filename.casefold()).suffix
        if content.startswith(b"%PDF") or content_type == "application/pdf" or suffix == ".pdf":
            return self._pdf(content)
        if "html" in content_type or suffix in {".htm", ".html"}:
            return self._html(content)
        if suffix == ".docx":
            return self._docx(content)
        if suffix == ".xlsx":
            return self._xlsx(content)
        if suffix == ".csv" or "csv" in content_type:
            return self._csv(content)
        if suffix == ".json" or "json" in content_type:
            return self._json(content)
        if suffix == ".xml" or "xml" in content_type:
            return self._xml(content)
        if suffix == ".ifc":
            return self._ifc(content)
        if suffix == ".dxf":
            return self._dxf(content)
        if suffix == ".dwg":
            raise ValueError("Native DWG is not supported; convert to DXF through the adapter")
        return ParsedDocument(
            text=clean_text(_decode_web_text(content)),
            content_type=content_type or "text/plain",
            parser_name="plain-text",
        )

    @staticmethod
    def _pdf(content: bytes) -> ParsedDocument:
        reader = PdfReader(io.BytesIO(content))
        pages = [
            f"[Sayfa {index}]\n{page.extract_text() or ''}"
            for index, page in enumerate(reader.pages, start=1)
            if (page.extract_text() or "").strip()
        ]
        if not pages:
            raise ValueError("PDF has no text layer; OCR queue required (OCR_DERIVED)")
        return ParsedDocument(
            text=clean_text("\n\n".join(pages)),
            content_type="application/pdf",
            parser_name="pypdf-text-layer",
        )

    @staticmethod
    def _html(content: bytes) -> ParsedDocument:
        parser = _TextHtmlParser()
        parser.feed(_decode_web_text(content))
        return ParsedDocument(
            text=parser.text(),
            content_type="text/html",
            parser_name="semantic-html",
            metadata={"links": parser.links},
        )

    @staticmethod
    def _docx(content: bytes) -> ParsedDocument:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            root = ElementTree.fromstring(archive.read("word/document.xml"))
        text = "\n".join(
            "".join(node.itertext()) for node in root.iter() if node.tag.endswith("}p")
        )
        return ParsedDocument(
            clean_text(text),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "docx-xml",
        )

    @staticmethod
    def _xlsx(content: bytes) -> ParsedDocument:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            shared: list[str] = []
            if "xl/sharedStrings.xml" in archive.namelist():
                root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
                shared = ["".join(node.itertext()) for node in root if node.tag.endswith("}si")]
            rows: list[str] = []
            for name in sorted(
                item for item in archive.namelist() if item.startswith("xl/worksheets/sheet")
            ):
                root = ElementTree.fromstring(archive.read(name))
                for row in (node for node in root.iter() if node.tag.endswith("}row")):
                    values: list[str] = []
                    for cell in (node for node in row if node.tag.endswith("}c")):
                        raw = next(
                            (node.text or "" for node in cell if node.tag.endswith("}v")), ""
                        )
                        values.append(
                            shared[int(raw)] if cell.attrib.get("t") == "s" and raw else raw
                        )
                    rows.append(" | ".join(values))
        return ParsedDocument(
            clean_text("\n".join(rows)),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "xlsx-xml",
        )

    @staticmethod
    def _csv(content: bytes) -> ParsedDocument:
        rows = csv.reader(io.StringIO(content.decode("utf-8-sig", errors="replace")))
        return ParsedDocument(
            clean_text("\n".join(" | ".join(row) for row in rows)), "text/csv", "csv"
        )

    @staticmethod
    def _json(content: bytes) -> ParsedDocument:
        payload = json.loads(content.decode("utf-8"))
        return ParsedDocument(
            json.dumps(payload, ensure_ascii=False, indent=2), "application/json", "json"
        )

    @staticmethod
    def _xml(content: bytes) -> ParsedDocument:
        root = ElementTree.fromstring(content)
        return ParsedDocument(clean_text(" ".join(root.itertext())), "application/xml", "xml")

    @staticmethod
    def _ifc(content: bytes) -> ParsedDocument:
        import ifcopenshell

        model = ifcopenshell.file.from_string(content.decode("utf-8", errors="replace"))
        types = Counter(entity.is_a() for entity in model)
        lines = [f"{key}: {value}" for key, value in sorted(types.items())]
        return ParsedDocument(
            "\n".join(lines),
            "application/x-step",
            "ifcopenshell",
            metadata={"entity_types": dict(types)},
        )

    @staticmethod
    def _dxf(content: bytes) -> ParsedDocument:
        import ezdxf

        stream = io.StringIO(content.decode("utf-8", errors="replace"))
        document = ezdxf.read(stream)  # pyright: ignore[reportPrivateImportUsage]
        entities = Counter(entity.dxftype() for entity in document.modelspace())
        layers = sorted(layer.dxf.name for layer in document.layers)
        text = "Entities\n" + "\n".join(f"{key}: {value}" for key, value in entities.items())
        text += "\nLayers\n" + "\n".join(layers)
        return ParsedDocument(
            text,
            "application/dxf",
            "ezdxf",
            metadata={"entities": dict(entities), "layers": layers},
        )


def clean_text(text: str) -> str:
    text = text.replace("\x00", " ").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def semantic_chunks(
    parsed: ParsedDocument,
    source: KnowledgeSourceDefinition,
    *,
    target_chars: int,
    overlap_chars: int,
    dimensions: int,
) -> list[SemanticChunk]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", parsed.text) if part.strip()]
    chunks: list[SemanticChunk] = []
    current = ""
    heading: str | None = None
    hierarchy = "paragraph"

    def append_chunk(content: str) -> None:
        if not content.strip():
            return
        chunks.append(
            SemanticChunk(
                index=len(chunks),
                hierarchy_type=hierarchy,
                heading=heading,
                content=content.strip(),
                normalized_content=normalize_text(content),
                embedding=hashed_embedding(content, dimensions),
                taxonomy=source.taxonomy,
            )
        )

    for paragraph in paragraphs:
        legal = LEGAL_HEADING_PATTERN.match(paragraph)
        specification = SPEC_HEADING_PATTERN.match(paragraph)
        new_heading = legal or specification
        if new_heading:
            if current:
                append_chunk(current)
                current = ""
            heading = new_heading.group("label")[:500]
            hierarchy = "article" if legal else "section"
        if len(current) + len(paragraph) + 2 <= target_chars:
            current = f"{current}\n\n{paragraph}".strip()
            continue
        append_chunk(current)
        overlap = current[-overlap_chars:] if overlap_chars else ""
        current = f"{overlap}\n\n{paragraph}".strip()
    append_chunk(current)
    return chunks


def extract_structured_rules(
    chunks: list[SemanticChunk],
    source: KnowledgeSourceDefinition,
    numeric_pattern: str,
) -> list[dict[str, Any]]:
    # A gazette's daily archive is a discovery/index document, not a normative
    # construction text. Extracting every number from it creates false rules.
    normative_classes = {
        "LAW",
        "REGULATION",
        "MANDATORY_STANDARD",
        "TECHNICAL_SPECIFICATION",
        "OFFICIAL_GUIDANCE",
    }
    if (
        source.taxonomy.document_type.casefold().endswith("_index")
        or source.taxonomy.source_class.value not in normative_classes
    ):
        return []
    pattern = re.compile(numeric_pattern, re.IGNORECASE | re.DOTALL)
    rules: list[dict[str, Any]] = []
    seen_rule_ids: set[str] = set()
    for chunk in chunks:
        for match in pattern.finditer(chunk.content):
            requirement = " ".join(match.group(0).split())
            unit_match = re.search(r"\b(m²|m2|mm|cm|m|dakika|saat|kişi|adet|%)\b", requirement)
            topic = _rule_topic(chunk.content, source.taxonomy.topic)
            digest = hashlib.sha256(
                f"{source.key}|{chunk.heading}|{requirement}".encode()
            ).hexdigest()[:20]
            rule_id = f"{source.key}-{digest}"
            if rule_id in seen_rule_ids:
                continue
            seen_rule_ids.add(rule_id)
            rules.append(
                {
                    "rule_id": rule_id,
                    "topic": topic,
                    "building_type": source.taxonomy.building_type,
                    "condition": chunk.content[:500],
                    "requirement": requirement,
                    "unit": unit_match.group(1) if unit_match else None,
                    "article": chunk.heading,
                    "source_class": source.taxonomy.source_class.value,
                    "effective_from": source.effective_from,
                    "status": "VALIDATED",
                    "extraction_method": "DETERMINISTIC_PATTERN_V1",
                }
            )
    return rules


def _rule_topic(text: str, fallback: str) -> str:
    normalized = normalize_text(text)
    for marker, topic in (
        ("kacis mesafe", "escape_distance"),
        ("yangin merdiv", "fire_stair"),
        ("sprinkler", "sprinkler"),
        ("duman kontrol", "smoke_control"),
        ("yangin dayan", "fire_resistance"),
        ("rampa", "accessible_ramp"),
        ("asansor", "elevator"),
        ("otopark", "parking"),
        ("donati", "reinforcement"),
        ("beton", "concrete"),
    ):
        if marker in normalized:
            return topic
    return fallback


def section_diff(old_text: str, new_text: str) -> list[dict[str, str]]:
    old = {
        normalize_text(value): value for value in re.split(r"\n\s*\n", old_text) if value.strip()
    }
    new = {
        normalize_text(value): value for value in re.split(r"\n\s*\n", new_text) if value.strip()
    }
    changes = [
        {"change_type": "REMOVED", "section_key": key[:500], "old_text": value, "new_text": ""}
        for key, value in old.items()
        if key not in new
    ]
    changes.extend(
        {"change_type": "ADDED", "section_key": key[:500], "old_text": "", "new_text": value}
        for key, value in new.items()
        if key not in old
    )
    return changes
