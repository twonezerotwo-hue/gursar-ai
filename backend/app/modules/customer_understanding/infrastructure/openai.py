from __future__ import annotations

from importlib import import_module
from typing import Any

from app.contracts.v1.customer_understanding import (
    CustomerUnderstandingContextV1,
    CustomerUnderstandingResultV1,
)

SYSTEM_INSTRUCTIONS = """You extract construction intake facts only.
Return CustomerUnderstandingResultV1. Never invent or calculate a price.
Separate multiple jobs. Preserve explicit exclusions, additions, quantities, and locations.
Do not include customer-facing prose."""


class OpenAICustomerUnderstandingProvider:
    def __init__(self, *, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model

    @property
    def provider_name(self) -> str:
        return f"openai.{self._model}"

    def understand(
        self,
        message: str,
        context: CustomerUnderstandingContextV1,
    ) -> CustomerUnderstandingResultV1:
        openai: Any = import_module("openai")
        client = openai.OpenAI(api_key=self._api_key)
        response = client.responses.parse(
            model=self._model,
            input=[
                {"role": "system", "content": SYSTEM_INSTRUCTIONS},
                {
                    "role": "user",
                    "content": f"Context: {context.model_dump_json()}\nMessage: {message}",
                },
            ],
            text_format=CustomerUnderstandingResultV1,
        )
        if response.output_parsed is None:
            raise ValueError("OpenAI returned no structured customer understanding")
        return CustomerUnderstandingResultV1.model_validate(response.output_parsed)
