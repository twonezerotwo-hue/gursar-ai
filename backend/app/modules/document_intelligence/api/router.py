from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.core.persistence import append_audit, append_outbox
from app.modules.conversation.infrastructure.repository import ConversationRepository
from app.modules.document_intelligence.api.schemas import (
    DocumentAnalysisResponse,
    DocumentToolsResponse,
    ToolCapabilityResponse,
)
from app.modules.document_intelligence.application.service import (
    AnalysisUploadTooLargeError,
    DocumentIntelligenceService,
    EmptyAnalysisUploadError,
    InvalidAnalysisDocumentError,
    UnsupportedAnalysisFormatError,
)
from app.modules.document_intelligence.domain.models import DocumentAnalysis
from app.modules.document_intelligence.infrastructure.repository import (
    SqlDocumentAnalysisRepository,
)

router = APIRouter(prefix="/api/v1/document-tools", tags=["document intelligence"])


def _service(request: Request) -> DocumentIntelligenceService:
    return request.app.state.document_intelligence


@router.get("", response_model=DocumentToolsResponse)
def list_document_tools(request: Request) -> DocumentToolsResponse:
    service = _service(request)
    return DocumentToolsResponse(
        accepted_extensions=service.supported_extensions(),
        max_upload_bytes=request.app.state.settings.document_max_upload_bytes,
        tools=[
            ToolCapabilityResponse(
                id=tool.id,
                name=tool.name,
                purpose=tool.purpose,
                github_url=tool.github_url,
                formats=list(tool.formats),
                available=tool.available,
                version=tool.version,
            )
            for tool in service.capabilities()
        ],
    )


@router.post("/analyze", response_model=DocumentAnalysisResponse)
def analyze_document(
    request: Request,
    file: UploadFile = File(...),
    conversation_id: str | None = Form(default=None),
    session: Session = Depends(get_session),
) -> DocumentAnalysisResponse:
    repository = SqlDocumentAnalysisRepository(session)
    if conversation_id is not None and ConversationRepository(session).get(conversation_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Görüşme bulunamadı.",
        )
    try:
        result = _service(request).analyze(
            filename=file.filename or "project-file",
            source=file.file,
            conversation_id=conversation_id,
            repository=repository if conversation_id is not None else None,
        )
    except AnalysisUploadTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Dosya izin verilen boyut sınırını aşıyor.",
        ) from exc
    except EmptyAnalysisUploadError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Yüklenen dosya boş.",
        ) from exc
    except UnsupportedAnalysisFormatError as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Dosya uzantısı ile gerçek içeriği eşleşmiyor veya tür desteklenmiyor.",
        ) from exc
    except InvalidAnalysisDocumentError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Dosya bozuk veya güvenli biçimde okunamadı.",
        ) from exc
    if result.analysis_id is not None and not result.cached:
        append_audit(
            session,
            action="DOCUMENT_ANALYSIS_CREATED",
            aggregate_id=result.analysis_id,
            detail={
                "conversation_id": result.conversation_id,
                "filename": result.filename,
                "sha256": result.sha256,
                "tools_used": list(result.tools_used),
            },
        )
        append_outbox(
            session,
            event_type="document.analysis.created.v1",
            aggregate_id=result.analysis_id,
            payload={
                "conversation_id": result.conversation_id,
                "analysis_id": result.analysis_id,
            },
        )
    return _analysis_response(result)


@router.get(
    "/conversations/{conversation_id}/analyses",
    response_model=list[DocumentAnalysisResponse],
)
def list_conversation_analyses(
    conversation_id: str,
    session: Session = Depends(get_session),
) -> list[DocumentAnalysisResponse]:
    if ConversationRepository(session).get(conversation_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Görüşme bulunamadı.",
        )
    return [
        _analysis_response(analysis)
        for analysis in SqlDocumentAnalysisRepository(session).list_for_conversation(
            conversation_id
        )
    ]


def _analysis_response(result: DocumentAnalysis) -> DocumentAnalysisResponse:
    return DocumentAnalysisResponse(
        filename=result.filename,
        format=result.format,
        media_type=result.media_type,
        tools_used=list(result.tools_used),
        summary=result.summary,
        extracted_text=result.extracted_text,
        metrics=result.metrics,
        analysis_id=result.analysis_id,
        conversation_id=result.conversation_id,
        sha256=result.sha256,
        size_bytes=result.size_bytes,
        cached=result.cached,
    )
