from __future__ import annotations

from decimal import Decimal
from typing import IO

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.modules.documents.domain.models import PDFMetadata, PDFPageMetadata


class InvalidPDFError(ValueError):
    pass


class EncryptedPDFError(ValueError):
    pass


class PypdfInspector:
    def inspect(self, source: IO[bytes]) -> PDFMetadata:
        try:
            source.seek(0)
            reader = PdfReader(source, strict=True)
            if reader.is_encrypted:
                raise EncryptedPDFError("Encrypted PDF files are not accepted")
            pages = [
                PDFPageMetadata(
                    page_number=index,
                    width_points=Decimal(str(float(page.mediabox.width))).quantize(
                        Decimal("0.001")
                    ),
                    height_points=Decimal(str(float(page.mediabox.height))).quantize(
                        Decimal("0.001")
                    ),
                )
                for index, page in enumerate(reader.pages, start=1)
            ]
        except EncryptedPDFError:
            raise
        except (PdfReadError, TypeError, ValueError, OSError, KeyError) as exc:
            raise InvalidPDFError("PDF structure could not be read") from exc
        finally:
            source.seek(0)
        if not pages:
            raise InvalidPDFError("PDF must contain at least one page")
        return PDFMetadata(pages=pages)
