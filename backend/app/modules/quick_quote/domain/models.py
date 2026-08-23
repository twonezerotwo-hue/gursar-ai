from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

MONEY_STEP = Decimal("0.01")


def calculate_total(quantity: Decimal, unit_price: Decimal) -> Decimal:
    if quantity <= 0 or unit_price < 0:
        raise ValueError("Quantity must be positive and unit price cannot be negative")
    return (quantity * unit_price).quantize(MONEY_STEP, rounding=ROUND_HALF_UP)
