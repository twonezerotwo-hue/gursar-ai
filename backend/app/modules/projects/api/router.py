from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.modules.projects.api.schemas import CreateProjectRequest, ProjectResponse
from app.modules.projects.application.service import ProjectNotFoundError, ProjectService
from app.modules.projects.domain.models import Project
from app.modules.projects.infrastructure.repository import ProjectRepository

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
def create_project(
    payload: CreateProjectRequest,
    session: Session = Depends(get_session),
) -> ProjectResponse:
    project = ProjectService(
        session=session,
        repository=ProjectRepository(session),
    ).create(name=payload.name, locale=payload.locale)
    return _response(project)


@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(
    project_id: str,
    session: Session = Depends(get_session),
) -> ProjectResponse:
    try:
        project = ProjectService(
            session=session,
            repository=ProjectRepository(session),
        ).get(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Proje bulunamadı.",
        ) from exc
    return _response(project)


def _response(project: Project) -> ProjectResponse:
    return ProjectResponse(
        id=project.id,
        name=project.name,
        locale=project.locale,
        status=project.status.value,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )
