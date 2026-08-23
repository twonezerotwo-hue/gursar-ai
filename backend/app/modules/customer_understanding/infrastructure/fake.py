from __future__ import annotations

from collections import deque

from app.contracts.v1.customer_understanding import (
    CustomerUnderstandingContextV1,
    CustomerUnderstandingResultV1,
)


class FakeCustomerUnderstandingProvider:
    def __init__(self, results: list[CustomerUnderstandingResultV1]) -> None:
        self._results = deque(results)

    @property
    def provider_name(self) -> str:
        return "fake.v1"

    def understand(
        self,
        message: str,
        context: CustomerUnderstandingContextV1,
    ) -> CustomerUnderstandingResultV1:
        del message, context
        if not self._results:
            raise RuntimeError("No fake customer-understanding result configured")
        return self._results.popleft()
