from __future__ import annotations

from sqlalchemy.orm import Session

from app.modules.projects.domain.models import Project, ProjectStatus
from app.modules.projects.infrastructure.models import ProjectORM


class ProjectRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, *, name: str, locale: str) -> Project:
        row = ProjectORM(name=name, locale=locale, status=ProjectStatus.ACTIVE.value)
        self._session.add(row)
        self._session.flush()
        return self._to_domain(row)

    def get(self, project_id: str) -> Project | None:
        row = self._session.get(ProjectORM, project_id)
        return self._to_domain(row) if row is not None else None

    @staticmethod
    def _to_domain(row: ProjectORM) -> Project:
        return Project(
            id=row.id,
            name=row.name,
            locale=row.locale,
            status=ProjectStatus(row.status),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
