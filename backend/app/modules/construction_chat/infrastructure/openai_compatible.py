from __future__ import annotations

import re

import httpx

from app.modules.construction_chat.domain.ports import ConstructionChatProviderError
from app.modules.construction_chat.infrastructure.ollama import (
    OllamaConstructionChatProvider,
)


class OpenAICompatibleConstructionChatProvider(OllamaConstructionChatProvider):
    """Construction chat over an OpenAI-compatible Chat Completions endpoint."""

    def __init__(
        self,
        *,
        provider_label: str,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float,
        context_window: int,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        super().__init__(
            base_url=base_url,
            model=model,
            timeout_seconds=timeout_seconds,
            fallback_model=None,
            context_window=context_window,
            transport=transport,
        )
        self._provider_label = provider_label
        self._client.headers.update(
            {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
        )

    @property
    def provider_name(self) -> str:
        return f"{self._provider_label}.{self._model}"

    def _request(
        self,
        *,
        messages: list[dict[str, str]],
        schema: dict[str, object],
        temperature: float,
        max_tokens: int,
    ) -> str:
        try:
            response = self._client.post(
                "chat/completions",
                json={
                    "model": self._model,
                    "messages": messages,
                    "temperature": max(temperature, 0.00000001),
                    "max_completion_tokens": max(max_tokens, 256),
                    "reasoning_effort": "low",
                    "include_reasoning": False,
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {
                            "name": self._schema_name(schema),
                            "strict": False,
                            "schema": schema,
                        },
                    },
                },
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise ConstructionChatProviderError(f"{self._provider_label} request failed") from exc

    @staticmethod
    def _schema_name(schema: dict[str, object]) -> str:
        title = str(schema.get("title", "construction_chat_response"))
        normalized = re.sub(r"[^a-zA-Z0-9_-]+", "_", title).strip("_").lower()
        return normalized or "construction_chat_response"
