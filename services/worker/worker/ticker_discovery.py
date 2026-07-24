from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from worker.kalshi.client import KalshiRestClient

MACRO_SERIES_TICKERS = (
    "KXCPI",
    "KXCPIYOY",
    "KXGDP",
    "KXPAYROLLS",
    "KXU3",
    "KXFED",
)

MICROSTRUCTURE_SERIES_TICKERS = (
    "KXBTCD",
    "KXETHD",
    "KXNASDAQ100",
    "KXHIGHNY",
    "KXHIGHCHI",
    "KXHIGHLA",
)

WEATHER_CITY_SERIES = (
    "KXHIGHNY",
    "KXHIGHNYC",
    "KXHIGHCHI",
    "KXHIGHMIA",
    "KXHIGHAUS",
    "KXHIGHPHIL",
    "KXHIGHDAL",
    "KXHIGHHOU",
    "KXHIGHLA",
    "KXLOWNY",
    "KXLOWNYC",
    "KXLOWCHI",
    "KXLOWMIA",
    "KXLOWAUS",
    "KXLOWPHIL",
    "KXLOWDAL",
    "KXLOWHOU",
    "KXLOWLA",
    "KXLOWTNY",
    "KXLOWTNYC",
    "KXLOWTCHI",
    "KXLOWTMIA",
    "KXLOWTAUS",
    "KXLOWTPHIL",
    "KXLOWTDAL",
    "KXLOWTHOU",
    "KXLOWTLA",
)


@dataclass(frozen=True)
class TickerDiscoveryConfig:
    strategy_name: str = "spread_capture"
    limit: int = 5
    min_volume: int = 0
    max_pages: int = 10
    series_tickers: tuple[str, ...] | None = None


async def discover_live_tickers(
    client: KalshiRestClient,
    config: TickerDiscoveryConfig,
) -> list[str]:
    if config.limit <= 0:
        return []

    series_tickers = _series_tickers_for_strategy(config)
    if series_tickers:
        markets = await _fetch_series_markets(client, series_tickers, config)
    else:
        markets = await _fetch_open_markets(client, config)

    ranked = sorted(
        _dedupe_markets(_eligible_markets(markets, config.min_volume)),
        key=_market_score,
        reverse=True,
    )
    return [
        str(market["ticker"])
        for market in ranked[: config.limit]
        if market.get("ticker") is not None
    ]


def _series_tickers_for_strategy(config: TickerDiscoveryConfig) -> tuple[str, ...]:
    if config.series_tickers is not None:
        return config.series_tickers

    strategy = config.strategy_name.lower()
    if "macro" in strategy:
        return MACRO_SERIES_TICKERS
    if "weather" in strategy:
        return WEATHER_CITY_SERIES
    if strategy in {"spread_capture", "mean_reversion", "event_drift"}:
        return MICROSTRUCTURE_SERIES_TICKERS
    return ()


async def _fetch_open_markets(
    client: KalshiRestClient,
    config: TickerDiscoveryConfig,
) -> list[dict[str, Any]]:
    markets: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(config.max_pages):
        page, cursor = await client.list_markets(
            status="open",
            limit=100,
            cursor=cursor,
        )
        markets.extend(page)
        if not cursor:
            break
    return markets


async def _fetch_series_markets(
    client: KalshiRestClient,
    series_tickers: tuple[str, ...],
    config: TickerDiscoveryConfig,
) -> list[dict[str, Any]]:
    markets: list[dict[str, Any]] = []
    for series_ticker in series_tickers:
        cursor: str | None = None
        for _ in range(config.max_pages):
            try:
                page, cursor = await client.list_markets(
                    status="open",
                    limit=100,
                    cursor=cursor,
                    series_ticker=series_ticker,
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429:
                    break
                raise
            markets.extend(page)
            if not cursor:
                break
    return markets


def _eligible_markets(
    markets: list[dict[str, Any]],
    min_volume: int,
) -> list[dict[str, Any]]:
    return [
        market
        for market in markets
        if str(market.get("status") or "open").lower() in {"open", "active"}
        and market.get("ticker") is not None
        and _has_two_sided_quote(market)
        and _market_volume(market) >= min_volume
    ]


def _dedupe_markets(markets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_ticker: dict[str, dict[str, Any]] = {}
    for market in markets:
        ticker = str(market["ticker"])
        existing = by_ticker.get(ticker)
        if existing is None or _market_score(market) > _market_score(existing):
            by_ticker[ticker] = market
    return list(by_ticker.values())


def _market_score(market: dict[str, Any]) -> Decimal:
    volume = Decimal(_market_volume(market))
    open_interest = _decimal_value(market.get("open_interest")) or Decimal("0")
    spread = _market_spread(market)
    spread_penalty = Decimal("0") if spread is None else spread * Decimal("100")
    return volume + (open_interest / Decimal("10")) - spread_penalty


def _market_spread(market: dict[str, Any]) -> Decimal | None:
    bid = _first_positive_decimal(market, ("yes_bid", "yes_bid_dollars"))
    ask = _first_positive_decimal(market, ("yes_ask", "yes_ask_dollars"))
    if bid is None or ask is None:
        return None
    return abs(_normalize_price(ask) - _normalize_price(bid))


def _has_two_sided_quote(market: dict[str, Any]) -> bool:
    return _has_quote_with_size(
        market,
        ("yes_bid", "yes_bid_dollars"),
        ("yes_bid_size", "yes_bid_size_fp"),
    ) and _has_quote_with_size(
        market,
        ("yes_ask", "yes_ask_dollars"),
        ("yes_ask_size", "yes_ask_size_fp"),
    )


def _has_quote_with_size(
    market: dict[str, Any],
    quote_fields: tuple[str, ...],
    size_fields: tuple[str, ...],
) -> bool:
    quote = _first_positive_decimal(market, quote_fields)
    if quote is None:
        return False

    size_values = [
        _decimal_value(market.get(field))
        for field in size_fields
        if market.get(field) is not None
    ]
    if not size_values:
        return True
    return any(size is not None and size > 0 for size in size_values)


def _market_volume(market: dict[str, Any]) -> int:
    for field in (
        "volume",
        "volume_24h",
        "volume_fp",
        "volume_24h_fp",
        "volume_24h_contracts",
        "volume_contracts",
    ):
        parsed = _decimal_value(market.get(field))
        if parsed is not None:
            return int(parsed)
    return 0


def _first_positive_decimal(
    market: dict[str, Any],
    fields: tuple[str, ...],
) -> Decimal | None:
    for field in fields:
        parsed = _decimal_value(market.get(field))
        if parsed is not None and parsed > 0:
            return parsed
    return None


def _normalize_price(value: Decimal) -> Decimal:
    if value > 1:
        return value / Decimal("100")
    return value


def _decimal_value(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
