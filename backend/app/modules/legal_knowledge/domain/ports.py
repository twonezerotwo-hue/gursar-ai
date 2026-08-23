from __future__ import annotations

from typing import Protocol

from app.modules.legal_knowledge.domain.models import LegalContextChunk


class LegalKnowledgeSearch(Protocol):
    def search(self, query: str, *, limit: int = 3) -> list[LegalContextChunk]: ...
