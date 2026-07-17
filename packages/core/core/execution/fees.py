from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


KALSHI_FEE_RATE = Decimal("0.07")


def compute_kalshi_fee(fill_price: Decimal, fill_qty: int) -> Decimal:
    fee_per_contract = KALSHI_FEE_RATE * fill_price * (Decimal("1") - fill_price)
    total_fee = (fee_per_contract * Decimal(fill_qty)).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )
    return max(total_fee, Decimal("0"))
