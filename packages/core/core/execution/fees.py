from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


KALSHI_FEE_RATE = Decimal("0.07")


def compute_kalshi_fee(
    fill_price: Decimal,
    fill_qty: int,
    fee_per_contract: Decimal = KALSHI_FEE_RATE,
) -> Decimal:
    if fill_price <= 0 or fill_price >= 1 or fill_qty <= 0:
        return Decimal("0")
    total_fee = (fee_per_contract * Decimal(fill_qty)).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )
    return max(total_fee, Decimal("0"))
