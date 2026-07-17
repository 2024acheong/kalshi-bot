from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from core.schemas.market import FeatureVector, MarketState


def _compute_mid_price_decimal(market: MarketState) -> Decimal | None:
    if market.yes_bid is None or market.yes_ask is None:
        return None
    return (market.yes_bid + market.yes_ask) / Decimal("2")


def compute_mid_price(market: MarketState) -> float | None:
    mid = _compute_mid_price_decimal(market)
    if mid is None:
        return None
    return float(mid)


def compute_spread_ticks(market: MarketState) -> float | None:
    if market.yes_bid is None or market.yes_ask is None:
        return None
    return float(market.yes_ask - market.yes_bid)


def compute_spread_pct(market: MarketState) -> float | None:
    mid = _compute_mid_price_decimal(market)
    if mid is None or mid == 0:
        return None

    spread_ticks = market.yes_ask - market.yes_bid
    return float((spread_ticks / mid) * Decimal("100"))


def compute_bid_ask_imbalance(market: MarketState) -> float | None:
    if market.yes_bid_size is None or market.yes_ask_size is None:
        return None

    total = market.yes_bid_size + market.yes_ask_size
    if total == 0:
        return None

    return (market.yes_bid_size - market.yes_ask_size) / total


def compute_time_to_close_hours(market: MarketState) -> float | None:
    if market.close_time is None:
        return None

    hours = (market.close_time - market.timestamp).total_seconds() / 3600
    return max(hours, 0.0)


def compute_implied_probability(market: MarketState) -> float | None:
    return compute_mid_price(market)


def compute_liquidity_score(market: MarketState) -> float | None:
    if not market.yes_bid_size or not market.yes_ask_size:
        return None

    bid = market.yes_bid_size
    ask = market.yes_ask_size
    return (2 * bid * ask) / (bid + ask)


def compute_price_momentum(
    history: list[MarketState],
    window_seconds: int = 3600,
) -> float | None:
    if len(history) < 2:
        return None

    now_mid = _compute_mid_price_decimal(history[0])
    if now_mid is None:
        return None

    cutoff = history[0].timestamp - timedelta(seconds=window_seconds)
    past_mid = None
    for snapshot in history[1:]:
        if snapshot.timestamp < cutoff:
            continue

        snapshot_mid = _compute_mid_price_decimal(snapshot)
        if snapshot_mid is not None:
            past_mid = snapshot_mid

    if past_mid is None or past_mid == 0:
        return None

    return float((now_mid - past_mid) / past_mid)


def compute_volume_zscore(
    history: list[MarketState],
    window: int = 20,
) -> float | None:
    current = history[0].volume_24h
    if current is None:
        return None

    volumes = [
        snapshot.volume_24h
        for snapshot in history[:window]
        if snapshot.volume_24h is not None
    ]
    if len(volumes) < 3:
        return None

    mean = sum(volumes) / len(volumes)
    variance = sum((volume - mean) ** 2 for volume in volumes) / len(volumes)
    std = variance**0.5
    if std == 0:
        return 0.0

    return (current - mean) / std


def compute_open_interest_delta(history: list[MarketState]) -> float | None:
    if len(history) < 2:
        return None

    current = history[0].open_interest
    previous = history[1].open_interest
    if current is None or previous is None:
        return None

    return float(current - previous)


def compute_features(
    market: MarketState,
    history: list[MarketState] | None = None,
) -> FeatureVector:
    snapshots = history if history else [market]

    return FeatureVector(
        ticker=market.ticker,
        timestamp=market.timestamp,
        mid_price=compute_mid_price(market),
        spread_pct=compute_spread_pct(market),
        spread_ticks=compute_spread_ticks(market),
        bid_ask_imbalance=compute_bid_ask_imbalance(market),
        time_to_close_hours=compute_time_to_close_hours(market),
        implied_probability=compute_implied_probability(market),
        liquidity_score=compute_liquidity_score(market),
        price_momentum_1h=compute_price_momentum(snapshots, window_seconds=3600),
        price_momentum_24h=compute_price_momentum(snapshots, window_seconds=86400),
        volume_zscore=compute_volume_zscore(snapshots),
        open_interest_delta=compute_open_interest_delta(snapshots),
    )
