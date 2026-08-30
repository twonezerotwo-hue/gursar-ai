from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from sqlalchemy.orm import Session

from app.composition import build_document_service
from app.core.database import get_session
from app.modules.documents.api.schemas import (
    DocumentCapabilitiesResponse,
    DocumentResponse,
    DocumentVersionResponse,
    SheetResponse,
)
from app.modules.documents.application.service import (
    DocumentNotFoundError,
    DuplicateDocumentError,
    EmptyUploadError,
    EncryptedPDFError,
    InvalidPDFError,
    PDFPageLimitError,
    ProjectNotFoundForUploadError,
    UnsupportedDocumentTypeError,
    UploadTooLargeError,
)
from app.modules.documents.domain.models import DocumentUploadResult, Sheet

router = APIRouter(prefix="/api/v1", tags=["documents"])


@router.get(
    "/documents/capabilities",
    response_model=DocumentCapabilitiesResponse,
)
def document_capabilities(request: Request) -> DocumentCapabilitiesResponse:
    settings = request.app.state.settings
    return DocumentCapabilitiesResponse(
        accepted_extensions=[".pdf", ".ifc", ".dxf"],
        accepted_media_types=["application/pdf", "application/x-step", "image/vnd.dxf"],
        max_upload_bytes=settings.document_max_upload_bytes,
        max_pdf_pages=settings.document_max_pdf_pages,
        encrypted_pdf_supported=False,
    )


@router.post(
    "/projects/{project_id}/documents",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
def upload_document(
    project_id: str,
    request: Request,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
) -> DocumentResponse:
    try:
        result = build_document_service(session, request.app).upload_document(
            project_id=project_id,
            original_name=file.filename or "project.pdf",
            source=file.file,
        )
    except ProjectNotFoundForUploadError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Proje bulunamadı.",
        ) from exc
    except UploadTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Dosya izin verilen boyut sınırını aşıyor.",
        ) from exc
    except DuplicateDocumentError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "Bu dosya proje içinde daha önce yüklenmiş.",
                "existing_document_version_id": exc.version_id,
            },
        ) from exc
    except EmptyUploadError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Yüklenen dosya boş.",
        ) from exc
    except UnsupportedDocumentTypeError as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Yalnızca gerçek PDF veya IFC STEP dosyaları kabul edilir.",
        ) from exc
    except EncryptedPDFError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Şifreli PDF dosyaları kabul edilmez.",
        ) from exc
    except InvalidPDFError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="PDF bozuk veya okunamıyor.",
        ) from exc
    except PDFPageLimitError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="PDF izin verilen sayfa sayısı sınırını aşıyor.",
        ) from exc
    return _response(result)


@router.get("/documents/{document_id}", response_model=DocumentResponse)
def get_document(
    document_id: str,
    request: Request,
    session: Session = Depends(get_session),
) -> DocumentResponse:
    try:
        result = build_document_service(session, request.app).get_document(document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Belge bulunamadı.",
        ) from exc
    return _response(result)


@router.get("/documents/{document_id}/sheets", response_model=list[SheetResponse])
def list_sheets(
    document_id: str,
    request: Request,
    session: Session = Depends(get_session),
) -> list[SheetResponse]:
    try:
        sheets = build_document_service(session, request.app).list_sheets(document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Belge bulunamadı.",
        ) from exc
    return [_sheet_response(sheet) for sheet in sheets]


def _response(result: DocumentUploadResult) -> DocumentResponse:
    version = result.version
    return DocumentResponse(
        id=result.document.id,
        project_id=result.document.project_id,
        original_name=result.document.original_name,
        created_at=result.document.created_at,
        latest_version=DocumentVersionResponse(
            id=version.id,
            version=version.version,
            sha256=version.sha256,
            size_bytes=version.size_bytes,
            media_type=version.media_type,
            page_count=version.page_count,
            status=version.status.value,
            created_at=version.created_at,
        ),
        sheets=[_sheet_response(sheet) for sheet in result.sheets],
    )


def _sheet_response(sheet: Sheet) -> SheetResponse:
    return SheetResponse(
        id=sheet.id,
        document_version_id=sheet.document_version_id,
        page_number=sheet.page_number,
        title=sheet.title,
        discipline=sheet.discipline.value,
        width_points=sheet.width_points,
        height_points=sheet.height_points,
        sheet_number=sheet.sheet_number,
        sheet_name=sheet.sheet_name,
        revision=sheet.revision,
        drawing_type=sheet.drawing_type,
        level_name=sheet.level_name,
        classification_confidence=sheet.classification_confidence,
    )
