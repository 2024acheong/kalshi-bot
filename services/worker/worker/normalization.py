from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from core.schemas import MarketState, MarketStatus


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _parse_price(value: Any) -> Decimal | None:
    decimal_value = _parse_decimal(value)
    if decimal_value is None:
        return None
    # Kalshi prices may appear either as cents like "45" or dollars like "0.45".
    if abs(decimal_value) > 1:
        return decimal_value / Decimal("100")
    return decimal_value


def _parse_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _parse_status(value: Any) -> MarketStatus:
    normalized = str(value or "open").strip().lower()
    if normalized in {status.value for status in MarketStatus}:
        return MarketStatus(normalized)
    return MarketStatus.OPEN


def normalize_market(raw_market: dict[str, Any], *, source: str) -> MarketState:
    timestamp = (
        _parse_datetime(raw_market.get("last_price_ts"))
        or _parse_datetime(raw_market.get("updated_at"))
        or datetime.now(timezone.utc)
    )
    return MarketState(
        ticker=str(raw_market["ticker"]),
        timestamp=timestamp,
        yes_bid=_parse_price(raw_market.get("yes_bid")),
        yes_ask=_parse_price(raw_market.get("yes_ask")),
        yes_bid_size=_parse_int(raw_market.get("yes_bid_size")),
        yes_ask_size=_parse_int(raw_market.get("yes_ask_size")),
        last_price=_parse_price(raw_market.get("last_price")),
        volume_24h=_parse_int(raw_market.get("volume") or raw_market.get("volume_24h")),
        open_interest=_parse_int(raw_market.get("open_interest")),
        close_time=_parse_datetime(raw_market.get("close_time")),
        status=_parse_status(raw_market.get("status")),
        source=source,
        raw_sequence=_parse_int(raw_market.get("seq") or raw_market.get("sequence")),
    )


def market_catalog_row(raw_market: dict[str, Any], market: MarketState) -> dict[str, Any]:
    return {
        "ticker": market.ticker,
        "title": raw_market.get("title"),
        "category": raw_market.get("category"),
        "close_time": market.close_time,
        "status": market.status.value,
        "synced_at": market.timestamp,
    }


def market_snapshot_row(market: MarketState) -> dict[str, Any]:
    return {
        "ticker": market.ticker,
        "timestamp": market.timestamp,
        "yes_bid": market.yes_bid,
        "yes_ask": market.yes_ask,
        "yes_bid_size": market.yes_bid_size,
        "yes_ask_size": market.yes_ask_size,
        "last_price": market.last_price,
        "volume_24h": market.volume_24h,
        "open_interest": market.open_interest,
        "source": market.source,
    }
