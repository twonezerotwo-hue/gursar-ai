from __future__ import annotations

from collections.abc import Sequence

from app.modules.construction_chat.domain.ports import (
    ConstructionChatClassification,
    ConstructionChatContext,
    ConstructionChatProvider,
    ConstructionChatProviderError,
)

WEAK_ANSWER_MARKERS = (
    "anlayamadım",
    "anlamadım",
    "yanıtı hazırlayamadım",
    "ayrıntılı yanıtlayamıyorum",
    "detaylı yanıtlayamıyorum",
    "tekrar deneyin",
    "bilgi veremiyorum",
)


class FallbackConstructionChatProvider:
    """Uses the local provider first and remote providers only when needed."""

    def __init__(
        self,
        *,
        local: ConstructionChatProvider,
        remotes: Sequence[ConstructionChatProvider],
    ) -> None:
        if not remotes:
            raise ValueError("At least one remote fallback provider is required")
        self._local = local
        self._remotes = tuple(remotes)

    @property
    def provider_name(self) -> str:
        route = "->".join(
            (self._local.provider_name, *(remote.provider_name for remote in self._remotes))
        )
        return f"fallback[{route}]"

    def classify(self, context: ConstructionChatContext) -> ConstructionChatClassification:
        try:
            local_result = self._local.classify(context)
        except ConstructionChatProviderError:
            return self._classify_with_remotes(context)

        if local_result.in_scope:
            return local_result

        try:
            remote_result = self._classify_with_remotes(context)
        except ConstructionChatProviderError:
            return local_result
        return remote_result if remote_result.in_scope else local_result

    def refusal(self, locale: str) -> str:
        return self._local.refusal(locale)

    def respond(self, context: ConstructionChatContext) -> str:
        try:
            local_answer = self._local.respond(context)
        except ConstructionChatProviderError:
            return self._respond_with_remotes(context)

        if self._needs_remote_answer(local_answer, context.locale):
            try:
                return self._respond_with_remotes(context)
            except ConstructionChatProviderError:
                return local_answer
        return local_answer

    def _classify_with_remotes(
        self,
        context: ConstructionChatContext,
    ) -> ConstructionChatClassification:
        last_error: ConstructionChatProviderError | None = None
        for remote in self._remotes:
            try:
                return remote.classify(context)
            except ConstructionChatProviderError as exc:
                last_error = exc
        raise last_error or ConstructionChatProviderError("No remote chat provider is available")

    def _respond_with_remotes(self, context: ConstructionChatContext) -> str:
        last_error: ConstructionChatProviderError | None = None
        for remote in self._remotes:
            try:
                return remote.respond(context)
            except ConstructionChatProviderError as exc:
                last_error = exc
        raise last_error or ConstructionChatProviderError("No remote chat provider is available")

    def _needs_remote_answer(self, answer: str, locale: str) -> bool:
        folded = answer.casefold()
        return self._local.refusal(locale).casefold() == folded or any(
            marker in folded for marker in WEAK_ANSWER_MARKERS
        )
