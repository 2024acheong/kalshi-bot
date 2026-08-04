from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from statistics import mean, pstdev
from typing import Any


@dataclass
class BacktestMetrics:
    total_pnl: float | None
    sharpe: float | None
    hit_rate: float | None
    brier_score: float | None
    max_drawdown: float
    total_trades: int
    total_fees: float
    resolved_trades: int = 0
    unresolved_trades: int = 0


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


def compute_metrics(
    fills: list[dict],
    daily_pnl_series: list[float],
    resolved_trades: list[dict] | None = None,
    unresolved_trades: int = 0,
) -> BacktestMetrics:
    """
    Compute replay metrics from simulated fills.

    P&L and calibration metrics are only calculated from resolved trades. A
    replay without outcomes is intentionally reported as unresolved.
    """
    filled = [fill for fill in fills if int(fill.get("fill_qty", 0)) > 0]
    total_fees = sum(_as_float(fill.get("fee", 0.0)) for fill in filled)

    resolved = resolved_trades or []
    total_pnl = sum(_as_float(trade["pnl"]) for trade in resolved) if resolved else None

    sharpe = None
    if daily_pnl_series:
        daily_std = pstdev(daily_pnl_series)
        if daily_std > 0:
            sharpe = mean(daily_pnl_series) / daily_std * math.sqrt(252)

    hit_rate = None
    brier_score = None
    if resolved:
        hit_rate = sum(_as_float(trade["pnl"]) > 0 for trade in resolved) / len(resolved)
        scored = [
            trade
            for trade in resolved
            if trade.get("model_prob") is not None and trade.get("outcome") is not None
        ]
        if scored:
            brier_score = sum(
                (_as_float(trade["model_prob"]) - float(trade["outcome"])) ** 2
                for trade in scored
                if trade.get("outcome") is not None
            ) / len(scored)

    return BacktestMetrics(
        total_pnl=float(total_pnl) if total_pnl is not None else None,
        sharpe=sharpe,
        hit_rate=hit_rate,
        brier_score=brier_score,
        max_drawdown=_max_drawdown(daily_pnl_series),
        total_trades=len(filled),
        total_fees=float(total_fees),
        resolved_trades=len(resolved),
        unresolved_trades=unresolved_trades,
    )
