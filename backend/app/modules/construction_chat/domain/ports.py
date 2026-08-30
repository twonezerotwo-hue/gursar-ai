from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class ConstructionChatContext:
    locale: str
    messages: list[ChatMessage]
    quote_results: list[dict[str, Any]]
    legal_context: list[dict[str, Any]] = field(default_factory=list)
    document_context: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ConstructionChatClassification:
    in_scope: bool
    quote_intent: bool


class ConstructionChatProvider(Protocol):
    @property
    def provider_name(self) -> str: ...

    def classify(self, context: ConstructionChatContext) -> ConstructionChatClassification: ...

    def refusal(self, locale: str) -> str: ...

    def respond(self, context: ConstructionChatContext) -> str: ...


class ConstructionChatProviderError(RuntimeError):
    pass
