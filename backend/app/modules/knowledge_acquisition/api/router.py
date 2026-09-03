from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.core.persistence import append_audit, append_outbox
from app.core.security import require_admin_key
from app.modules.knowledge_acquisition.api.schemas import (
    AcquisitionRequest,
    KnowledgeRuleEvaluationRequest,
    KnowledgeSearchRequest,
)
from app.modules.knowledge_acquisition.application.pipeline import (
    KnowledgeAcquisitionPipeline,
)
from app.modules.knowledge_acquisition.application.retrieval import (
    HybridKnowledgeSearch,
    route_knowledge_query,
)
from app.modules.knowledge_acquisition.application.rules import RuleApplicabilityEngine
from app.modules.knowledge_acquisition.domain.models import RuleApplicabilityContext
from app.modules.knowledge_acquisition.infrastructure.config import KnowledgeConfig
from app.modules.knowledge_acquisition.infrastructure.repository import KnowledgeRepository

router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])


@router.post("/search")
def search_knowledge(
    payload: KnowledgeSearchRequest,
    session: Session = Depends(get_session),
) -> dict:
    config = KnowledgeConfig()
    routing = route_knowledge_query(payload.query)
    hits = HybridKnowledgeSearch(KnowledgeRepository(session), config).search(
        payload.query,
        limit=payload.limit,
        filters=payload.filters or routing["filters"],
        as_of=payload.as_of,
    )
    return {
        "routing": routing,
        "source_confidence_label": "SOURCE_CONFIDENCE",
        "data_confidence_label": "DATA_CONFIDENCE",
        "results": [asdict(hit) for hit in hits],
    }


@router.get("/route")
def route_query(q: str) -> dict:
    return route_knowledge_query(q)


@router.post("/rules/evaluate")
def evaluate_rule_applicability(
    payload: KnowledgeRuleEvaluationRequest,
    session: Session = Depends(get_session),
) -> dict:
    result = RuleApplicabilityEngine(KnowledgeRepository(session), KnowledgeConfig()).evaluate(
        RuleApplicabilityContext(**payload.model_dump())
    )
    return asdict(result)


@router.get("/admin/dashboard", dependencies=[Depends(require_admin_key)])
def knowledge_dashboard(session: Session = Depends(get_session)) -> dict:
    return KnowledgeRepository(session).dashboard()


@router.post("/admin/acquire", dependencies=[Depends(require_admin_key)])
def acquire_knowledge(
    payload: AcquisitionRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> dict:
    config = KnowledgeConfig()
    sources = config.sources()
    if payload.source_keys:
        wanted = set(payload.source_keys)
        sources = tuple(source for source in sources if source.key in wanted)
        missing = wanted - {source.key for source in sources}
        if missing:
            raise HTTPException(status_code=422, detail=f"Unknown source keys: {sorted(missing)}")
    report = KnowledgeAcquisitionPipeline(
        KnowledgeRepository(session),
        config,
        storage_root=request.app.state.settings.document_storage_root.parent / "knowledge",
    ).run(sources, approve_official=payload.approve_official)
    append_audit(
        session,
        action="KNOWLEDGE_ACQUISITION_RUN",
        aggregate_id=report.run_id,
        detail={
            "approve_official": payload.approve_official,
            "ingested": report.ingested,
            "active": report.active,
            "failed": len(report.failed),
        },
        actor_type="ADMIN",
    )
    append_outbox(
        session,
        event_type="knowledge.acquisition.completed.v1",
        aggregate_id=report.run_id,
        payload={"ingested": report.ingested, "active": report.active},
    )
    return asdict(report)


@router.post(
    "/admin/versions/{version_id}/approve",
    dependencies=[Depends(require_admin_key)],
)
def approve_version(version_id: str, session: Session = Depends(get_session)) -> dict:
    repository = KnowledgeRepository(session)
    try:
        version = repository.approve(version_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Knowledge version not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    repository.bridge_to_legal_knowledge(version.id)
    conflicts = repository.detect_conflicts(version.id)
    append_audit(
        session,
        action="KNOWLEDGE_VERSION_APPROVED",
        aggregate_id=version.id,
        detail={"version": version.version_label, "conflicts": conflicts},
        actor_type="ADMIN",
    )
    return {"id": version.id, "status": version.status, "conflicts_created": conflicts}
