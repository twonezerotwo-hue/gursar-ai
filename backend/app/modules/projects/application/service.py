from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.persistence import append_audit, append_outbox
from app.modules.projects.domain.models import Project
from app.modules.projects.infrastructure.repository import ProjectRepository


class ProjectNotFoundError(LookupError):
    pass


class ProjectService:
    def __init__(self, *, session: Session, repository: ProjectRepository) -> None:
        self._session = session
        self._repository = repository

    def create(self, *, name: str, locale: str) -> Project:
        project = self._repository.create(name=name.strip(), locale=locale)
        append_audit(
            self._session,
            action="PROJECT_CREATED",
            aggregate_id=project.id,
            detail={"name": project.name},
        )
        append_outbox(
            self._session,
            event_type="project.created.v1",
            aggregate_id=project.id,
            payload={"project_id": project.id},
        )
        return project

    def get(self, project_id: str) -> Project:
        project = self._repository.get(project_id)
        if project is None:
            raise ProjectNotFoundError(project_id)
        return project
