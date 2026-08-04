from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


KALSHI_FEE_RATE = Decimal("0.07")
CENTS_PER_DOLLAR = 100
PAYOUT_CENTS_PER_CONTRACT = 100


def decimal_to_cents(value: Decimal) -> int:
    return int((value * CENTS_PER_DOLLAR).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def compute_kalshi_fee(
    fill_price: Decimal,
    fill_qty: int,
    fee_per_contract: Decimal = KALSHI_FEE_RATE,
) -> Decimal:
    if fill_price <= 0 or fill_price >= 1 or fill_qty <= 0:
        return Decimal("0")
    # Kalshi's taker fee scales with the binary-contract variance p(1-p), so a
    # flat per-contract fee materially misprices trades near 0 or 1.
    price_factor = fill_price * (Decimal("1") - fill_price)
    total_fee = (fee_per_contract * price_factor * Decimal(fill_qty)).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )
    return max(total_fee, Decimal("0"))


def compute_kalshi_fee_cents(
    fill_price: Decimal,
    fill_qty: int,
    fee_per_contract: Decimal = KALSHI_FEE_RATE,
) -> int:
    return decimal_to_cents(
        compute_kalshi_fee(fill_price, fill_qty, fee_per_contract=fee_per_contract)
    )
