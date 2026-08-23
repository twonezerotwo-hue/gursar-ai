from __future__ import annotations

from typing import Protocol

from app.contracts.v1.customer_understanding import (
    CustomerUnderstandingContextV1,
    CustomerUnderstandingResultV1,
)


class CustomerUnderstandingProvider(Protocol):
    @property
    def provider_name(self) -> str: ...

    def understand(
        self,
        message: str,
        context: CustomerUnderstandingContextV1,
    ) -> CustomerUnderstandingResultV1: ...
