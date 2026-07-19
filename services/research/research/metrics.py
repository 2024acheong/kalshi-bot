from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from statistics import mean, pstdev
from typing import Any


@dataclass
class BacktestMetrics:
    total_pnl: float
    sharpe: float | None
    hit_rate: float | None
    brier_score: float | None
    max_drawdown: float
    total_trades: int
    total_fees: float


def _as_float(value: Any) -> float:
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _max_drawdown(daily_pnl_series: list[float]) -> float:
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for daily_pnl in daily_pnl_series:
        cumulative += daily_pnl
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)
    return max_drawdown


def compute_metrics(fills: list[dict], daily_pnl_series: list[float]) -> BacktestMetrics:
    """
    Compute replay metrics from simulated fills.

    These are approximations because this branch does not join against resolved
    market outcomes. In particular, total_pnl is a cash-deployment proxy, hit_rate
    is a direction proxy, and brier_score compares model probability with market
    implied probability rather than true resolved outcomes.
    """
    filled = [fill for fill in fills if int(fill.get("fill_qty", 0)) > 0]
    total_fees = sum(_as_float(fill.get("fee", 0.0)) for fill in filled)

    # TODO: Replace this cash-deployment proxy with resolution-based P&L after
    # resolved market outcomes are available in the replay dataset.
    total_pnl = sum(
        _as_float(fill.get("fill_price", 0.0)) * int(fill.get("fill_qty", 0))
        - _as_float(fill.get("fee", 0.0))
        for fill in filled
    )

    sharpe = None
    if daily_pnl_series:
        daily_std = pstdev(daily_pnl_series)
        if daily_std > 0:
            sharpe = mean(daily_pnl_series) / daily_std * math.sqrt(252)

    hit_rate = None
    brier_score = None
    if filled:
        # This hit-rate proxy only checks whether model and market probabilities
        # sit on the same side of 50%; true win/loss needs resolved outcomes.
        hits = 0
        brier_terms = []
        for fill in filled:
            fill_price = _as_float(fill["fill_price"])
            model_prob = _as_float(fill["model_prob"])
            hits += int((model_prob >= 0.5) == (fill_price >= 0.5))
            # This is market-divergence, not real Brier calibration.
            brier_terms.append((model_prob - fill_price) ** 2)
        hit_rate = hits / len(filled)
        brier_score = sum(brier_terms) / len(brier_terms)

    return BacktestMetrics(
        total_pnl=float(total_pnl),
        sharpe=sharpe,
        hit_rate=hit_rate,
        brier_score=brier_score,
        max_drawdown=_max_drawdown(daily_pnl_series),
        total_trades=len(filled),
        total_fees=float(total_fees),
    )
