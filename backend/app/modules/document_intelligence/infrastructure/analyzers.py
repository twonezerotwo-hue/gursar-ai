from __future__ import annotations

from collections import Counter
from pathlib import Path
from statistics import fmean
from typing import Any

import cv2
import ifcopenshell
import numpy as np
import pdfplumber
from ezdxf.filemanagement import readfile
from PIL import Image
from rapidocr import RapidOCR

from app.modules.document_intelligence.domain.models import DocumentAnalysis
from app.modules.document_intelligence.domain.ports import DocumentAnalyzer, OcrEngine

MAX_IMAGE_PIXELS = 40_000_000
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS


class RapidOcrEngine:
    tool_id = "rapidocr"

    def __init__(self) -> None:
        self._engine: RapidOCR | None = None

    def read(self, image: object) -> tuple[str, float | None]:
        if self._engine is None:
            self._engine = RapidOCR()
        result: Any = self._engine(image)  # type: ignore[arg-type]
        texts = tuple(result.txts or ())
        scores = tuple(float(score) for score in (result.scores or ()))
        return "\n".join(texts), fmean(scores) if scores else None


class OpenCVToolkit:
    tool_id = "opencv"

    @staticmethod
    def decode(path: Path) -> Any:
        with Image.open(path) as source:
            width, height = source.size
            if width * height > MAX_IMAGE_PIXELS:
                raise ValueError("Image pixel limit exceeded")
        image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("Image cannot be decoded")
        return image

    @staticmethod
    def preprocess(image: Any) -> tuple[Any, dict[str, int | float]]:
        height, width = image.shape[:2]
        if width * height > MAX_IMAGE_PIXELS:
            raise ValueError("Image pixel limit exceeded")
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        denoised = cv2.GaussianBlur(gray, (3, 3), 0)
        binary = cv2.threshold(
            denoised,
            0,
            255,
            cv2.THRESH_BINARY + cv2.THRESH_OTSU,
        )[1]
        edges = cv2.Canny(denoised, 50, 150)
        edge_pixels = int(cv2.countNonZero(edges))
        return binary, {
            "width_px": int(width),
            "height_px": int(height),
            "edge_pixels": edge_pixels,
            "edge_density": round(edge_pixels / max(width * height, 1), 5),
        }


class ImageDrawingAnalyzer:
    formats: tuple[str, ...] = (".png", ".jpg", ".jpeg")

    def __init__(self, *, vision: OpenCVToolkit, ocr: OcrEngine) -> None:
        self._vision = vision
        self._ocr = ocr

    def analyze(self, *, path: Path, filename: str, detected_format: str) -> DocumentAnalysis:
        image = self._vision.decode(path)
        prepared, metrics = self._vision.preprocess(image)
        text, confidence = self._ocr.read(prepared)
        if confidence is not None:
            metrics["ocr_confidence"] = round(confidence, 4)
        metrics["ocr_lines"] = len(text.splitlines()) if text else 0
        media_type = "image/png" if detected_format == ".png" else "image/jpeg"
        summary = (
            f"{metrics['width_px']}×{metrics['height_px']} px çizim; "
            f"OCR ile {metrics['ocr_lines']} metin satırı bulundu."
        )
        return DocumentAnalysis(
            filename=filename,
            format=detected_format,
            media_type=media_type,
            tools_used=(self._vision.tool_id, self._ocr.tool_id),
            summary=summary,
            extracted_text=text[:20_000],
            metrics=metrics,
        )


class PdfPlumberAnalyzer:
    formats: tuple[str, ...] = (".pdf",)

    def __init__(
        self,
        *,
        vision: OpenCVToolkit,
        ocr: OcrEngine,
        max_pages: int,
    ) -> None:
        self._vision = vision
        self._ocr = ocr
        self._max_pages = max_pages

    def analyze(self, *, path: Path, filename: str, detected_format: str) -> DocumentAnalysis:
        page_texts: list[str] = []
        ocr_pages: list[int] = []
        table_count = 0
        vector_objects = 0
        tools = ["pdfplumber"]
        with pdfplumber.open(path) as document:
            page_count = len(document.pages)
            for page_number, page in enumerate(document.pages[: self._max_pages], start=1):
                text = page.extract_text(layout=True) or ""
                table_count += len(page.find_tables())
                vector_objects += sum(
                    len(page.objects.get(object_type, ()))
                    for object_type in ("line", "rect", "curve")
                )
                if len(text.strip()) < 20:
                    rendered = np.asarray(page.to_image(resolution=144).original.convert("RGB"))
                    bgr = cv2.cvtColor(rendered, cv2.COLOR_RGB2BGR)
                    prepared, _ = self._vision.preprocess(bgr)
                    ocr_text, _ = self._ocr.read(prepared)
                    if ocr_text.strip():
                        text = ocr_text
                        ocr_pages.append(page_number)
                if text.strip():
                    page_texts.append(f"[Sayfa {page_number}]\n{text.strip()}")
        if ocr_pages:
            tools.extend((self._vision.tool_id, self._ocr.tool_id))
        extracted_text = "\n\n".join(page_texts)[:20_000]
        metrics: dict[str, Any] = {
            "page_count": page_count,
            "analyzed_pages": min(page_count, self._max_pages),
            "text_characters": len(extracted_text),
            "tables": table_count,
            "vector_objects": vector_objects,
            "ocr_pages": ocr_pages,
        }
        summary = (
            f"{page_count} sayfalı PDF; {len(extracted_text)} metin karakteri, "
            f"{table_count} tablo ve {vector_objects} vektör öğe tespit edildi."
        )
        return DocumentAnalysis(
            filename=filename,
            format=detected_format,
            media_type="application/pdf",
            tools_used=tuple(tools),
            summary=summary,
            extracted_text=extracted_text,
            metrics=metrics,
        )


class DxfAnalyzer:
    formats: tuple[str, ...] = (".dxf",)

    def analyze(self, *, path: Path, filename: str, detected_format: str) -> DocumentAnalysis:
        document = readfile(path)
        audit = document.audit()
        entities = Counter(entity.dxftype() for entity in document.modelspace())
        entity_count = sum(entities.values())
        layers = [layer.dxf.name for layer in document.layers]
        metrics = {
            "dxf_version": document.dxfversion,
            "layers": layers[:500],
            "layer_count": len(layers),
            "entity_count": entity_count,
            "entity_types": dict(entities.most_common(100)),
            "audit_errors": len(audit.errors),
            "audit_fixes": len(audit.fixes),
        }
        return DocumentAnalysis(
            filename=filename,
            format=detected_format,
            media_type="image/vnd.dxf",
            tools_used=("ezdxf",),
            summary=(
                f"DXF {document.dxfversion}; {len(layers)} katman ve "
                f"{entity_count} çizim öğesi bulundu."
            ),
            metrics=metrics,
        )


class IfcAnalyzer:
    formats: tuple[str, ...] = (".ifc",)

    def analyze(self, *, path: Path, filename: str, detected_format: str) -> DocumentAnalysis:
        model = ifcopenshell.open(path)
        counts = {
            "projects": len(model.by_type("IfcProject")),
            "sites": len(model.by_type("IfcSite")),
            "buildings": len(model.by_type("IfcBuilding")),
            "storeys": len(model.by_type("IfcBuildingStorey")),
            "spaces": len(model.by_type("IfcSpace")),
            "elements": len(model.by_type("IfcElement")),
        }
        element_types = Counter(element.is_a() for element in model.by_type("IfcElement"))
        metrics: dict[str, Any] = {
            "schema": model.schema,
            **counts,
            "element_types": dict(element_types.most_common(100)),
        }
        return DocumentAnalysis(
            filename=filename,
            format=detected_format,
            media_type="application/x-step",
            tools_used=("ifcopenshell",),
            summary=(
                f"{model.schema} BIM modeli; {counts['buildings']} bina, "
                f"{counts['storeys']} kat, {counts['spaces']} mahal ve "
                f"{counts['elements']} yapı elemanı bulundu."
            ),
            metrics=metrics,
        )


def build_default_analyzers(*, max_pdf_pages: int) -> list[DocumentAnalyzer]:
    vision = OpenCVToolkit()
    ocr = RapidOcrEngine()
    return [
        PdfPlumberAnalyzer(vision=vision, ocr=ocr, max_pages=max_pdf_pages),
        ImageDrawingAnalyzer(vision=vision, ocr=ocr),
        DxfAnalyzer(),
        IfcAnalyzer(),
    ]
