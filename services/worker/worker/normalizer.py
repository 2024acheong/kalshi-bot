from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from core.schemas.market import MarketState, MarketStatus


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
    if abs(decimal_value) > 1:
        return decimal_value / Decimal("100")
    return decimal_value


def _parse_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return value
    return int(Decimal(str(value)))


def _parse_status(value: Any) -> MarketStatus:
    normalized = str(value or "open").strip().lower()
    if normalized in {status.value for status in MarketStatus}:
        return MarketStatus(normalized)
    return MarketStatus.OPEN


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def normalize_market(raw_market: dict[str, Any], *, source: str) -> MarketState:
    timestamp = (
        _parse_datetime(raw_market.get("last_price_ts"))
        or _parse_datetime(raw_market.get("updated_time"))
        or _parse_datetime(raw_market.get("updated_at"))
        or datetime.now(timezone.utc)
    )
    yes_bid_size = _parse_int(
        _first_present(raw_market.get("yes_bid_size"), raw_market.get("yes_bid_size_fp"))
    )
    yes_ask_size = _parse_int(
        _first_present(raw_market.get("yes_ask_size"), raw_market.get("yes_ask_size_fp"))
    )
    no_bid_size = _parse_int(
        _first_present(raw_market.get("no_bid_size"), raw_market.get("no_bid_size_fp"))
    )
    no_ask_size = _parse_int(
        _first_present(raw_market.get("no_ask_size"), raw_market.get("no_ask_size_fp"))
    )
    return MarketState(
        ticker=str(raw_market["ticker"]),
        timestamp=timestamp,
        yes_bid=_parse_price(
            _first_present(raw_market.get("yes_bid"), raw_market.get("yes_bid_dollars"))
        ),
        yes_ask=_parse_price(
            _first_present(raw_market.get("yes_ask"), raw_market.get("yes_ask_dollars"))
        ),
        yes_bid_size=yes_bid_size,
        yes_ask_size=yes_ask_size,
        last_price=_parse_price(
            _first_present(raw_market.get("last_price"), raw_market.get("last_price_dollars"))
        ),
        volume_24h=_parse_int(raw_market.get("volume") or raw_market.get("volume_24h")),
        open_interest=_parse_int(raw_market.get("open_interest")),
        close_time=_parse_datetime(raw_market.get("close_time")),
        status=_parse_status(raw_market.get("status")),
        source=source,
        raw_sequence=_parse_int(raw_market.get("seq") or raw_market.get("sequence")),
        no_bid=_parse_price(
            _first_present(raw_market.get("no_bid"), raw_market.get("no_bid_dollars"))
        ),
        no_ask=_parse_price(
            _first_present(raw_market.get("no_ask"), raw_market.get("no_ask_dollars"))
        ),
        no_bid_size=no_bid_size if no_bid_size is not None else yes_ask_size,
        no_ask_size=no_ask_size if no_ask_size is not None else yes_bid_size,
    )


def normalize_ws_ticker_message(msg: dict[str, Any]) -> MarketState | None:
    message_type = msg.get("type")
    if message_type == "orderbook_snapshot":
        return _normalize_ws_orderbook_snapshot(msg)

    if message_type != "ticker":
        return None
    ticker = msg.get("market_ticker")
    payload = msg.get("msg")
    if ticker is None or not isinstance(payload, dict):
        return None
    timestamp = (
        _parse_datetime(payload.get("time"))
        or _parse_datetime(payload.get("ts"))
        or datetime.now(timezone.utc)
    )
    yes_bid_size = _parse_int(
        _first_present(payload.get("yes_bid_size"), payload.get("yes_bid_size_fp"))
    )
    yes_ask_size = _parse_int(
        _first_present(payload.get("yes_ask_size"), payload.get("yes_ask_size_fp"))
    )
    no_bid_size = _parse_int(
        _first_present(payload.get("no_bid_size"), payload.get("no_bid_size_fp"))
    )
    no_ask_size = _parse_int(
        _first_present(payload.get("no_ask_size"), payload.get("no_ask_size_fp"))
    )
    return MarketState(
        ticker=str(ticker),
        timestamp=timestamp,
        yes_bid=_parse_price(
            _first_present(payload.get("yes_bid"), payload.get("yes_bid_dollars"))
        ),
        yes_ask=_parse_price(
            _first_present(payload.get("yes_ask"), payload.get("yes_ask_dollars"))
        ),
        yes_bid_size=yes_bid_size,
        yes_ask_size=yes_ask_size,
        last_price=_parse_price(
            _first_present(
                payload.get("last_price"),
                payload.get("last_price_dollars"),
                payload.get("price"),
                payload.get("price_dollars"),
            )
        ),
        volume_24h=_parse_int(
            payload.get("volume") or payload.get("volume_fp") or payload.get("dollar_volume")
        ),
        open_interest=_parse_int(
            payload.get("open_interest")
            or payload.get("open_interest_fp")
            or payload.get("dollar_open_interest")
        ),
        close_time=None,
        status=MarketStatus.OPEN,
        source="websocket",
        raw_sequence=_parse_int(msg.get("seq")),
        no_bid=_parse_price(
            _first_present(payload.get("no_bid"), payload.get("no_bid_dollars"))
        ),
        no_ask=_parse_price(
            _first_present(payload.get("no_ask"), payload.get("no_ask_dollars"))
        ),
        no_bid_size=no_bid_size if no_bid_size is not None else yes_ask_size,
        no_ask_size=no_ask_size if no_ask_size is not None else yes_bid_size,
    )


def _normalize_ws_orderbook_snapshot(msg: dict[str, Any]) -> MarketState | None:
    ticker = msg.get("market_ticker")
    payload = msg.get("msg")
    if ticker is None or not isinstance(payload, dict):
        return None

    yes_book = _parse_orderbook_side(payload.get("yes"))
    no_book = _parse_orderbook_side(payload.get("no"))
    yes_bid_price, yes_bid_size = _best_book_level(yes_book)
    no_bid_price, no_bid_size = _best_book_level(no_book)
    yes_ask = Decimal("1") - no_bid_price if no_bid_price is not None else None
    no_ask = Decimal("1") - yes_bid_price if yes_bid_price is not None else None

    timestamp = (
        _parse_datetime(payload.get("time"))
        or _parse_datetime(payload.get("ts"))
        or datetime.now(timezone.utc)
    )
    return MarketState(
        ticker=str(ticker),
        timestamp=timestamp,
        yes_bid=yes_bid_price,
        yes_ask=yes_ask,
        yes_bid_size=yes_bid_size,
        yes_ask_size=no_bid_size,
        last_price=None,
        volume_24h=None,
        open_interest=None,
        close_time=None,
        status=MarketStatus.OPEN,
        source="websocket",
        raw_sequence=_parse_int(msg.get("seq")),
        no_bid=no_bid_price,
        no_ask=no_ask,
        no_bid_size=no_bid_size,
        no_ask_size=yes_bid_size,
    )


def _parse_orderbook_side(levels: Any) -> dict[int, int]:
    if not isinstance(levels, list):
        return {}

    book: dict[int, int] = {}
    for level in levels:
        parsed = _parse_book_level(level)
        if parsed is None:
            continue
        price_cents, quantity = parsed
        if quantity > 0:
            book[price_cents] = quantity
    return book


def _parse_book_level(level: Any) -> tuple[int, int] | None:
    if isinstance(level, dict):
        price = level.get("price") or level.get("price_cents")
        quantity = level.get("quantity") or level.get("qty") or level.get("size")
    elif isinstance(level, (list, tuple)) and len(level) >= 2:
        price = level[0]
        quantity = level[1]
    else:
        return None

    parsed_price = _parse_int(price)
    parsed_quantity = _parse_int(quantity)
    if parsed_price is None or parsed_quantity is None:
        return None
    return parsed_price, parsed_quantity


def _best_book_level(book: dict[int, int]) -> tuple[Decimal | None, int | None]:
    if not book:
        return None, None
    price_cents = max(book)
    return Decimal(price_cents) / Decimal("100"), book[price_cents]


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
        "no_bid": market.no_bid,
        "no_ask": market.no_ask,
        "no_bid_size": market.no_bid_size,
        "no_ask_size": market.no_ask_size,
        "last_price": market.last_price,
        "volume_24h": market.volume_24h,
        "open_interest": market.open_interest,
        "source": market.source,
        "raw_sequence": market.raw_sequence,
    }
