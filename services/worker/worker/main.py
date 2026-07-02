from __future__ import annotations

import asyncio
import logging
import os

import httpx

from worker.config import WorkerSettings
from worker.kalshi import KalshiRestClient
from worker.service import build_runtime


logger = logging.getLogger(__name__)
DISCOVERY_PAGE_DELAY_SECONDS = 0.25
DISCOVERY_RETRY_DELAYS_SECONDS = (1, 2, 5)
DEFAULT_DISCOVERY_CATEGORY = "Weather"


def _parse_watched_tickers() -> list[str]:
    raw_value = os.getenv("WATCHED_TICKERS", "")
    return [ticker.strip() for ticker in raw_value.split(",") if ticker.strip()]


def _parse_csv_env(name: str) -> list[str]:
    raw_value = os.getenv(name, "")
    return [value.strip() for value in raw_value.split(",") if value.strip()]


def _discovery_category() -> str:
    return os.getenv("MARKET_DISCOVERY_CATEGORY", DEFAULT_DISCOVERY_CATEGORY).strip() or DEFAULT_DISCOVERY_CATEGORY


async def _fetch_open_tickers(settings: WorkerSettings) -> list[str]:
    category = _discovery_category()
    logger.info(
        "WATCHED_TICKERS not set; discovering currently open Kalshi markets for category=%s",
        category,
    )
    discovered: list[str] = []
    async with httpx.AsyncClient(timeout=10.0) as http_client:
        client = KalshiRestClient(http_client=http_client, base_url=settings.kalshi_base_url)
        series_tickers = await _fetch_series_by_category(client, category=category)
        if not series_tickers:
            fallback_series = _fallback_series_tickers(category)
            if fallback_series:
                logger.warning(
                    "No series discovered for category=%s; falling back to configured series tickers: %s",
                    category,
                    ",".join(fallback_series),
                )
                series_tickers = fallback_series
        logger.info("Discovered %d series for category=%s", len(series_tickers), category)
        for series_ticker in series_tickers:
            markets = await _fetch_open_series_markets(client, series_ticker=series_ticker)
            discovered.extend(str(market["ticker"]) for market in markets if market.get("ticker"))
            logger.info(
                "Discovered %d open market tickers so far for category=%s",
                len(discovered),
                category,
            )

    unique_tickers = list(dict.fromkeys(discovered))
    if not unique_tickers:
        raise RuntimeError(f"Kalshi returned no open market tickers for category={category}")
    logger.info(
        "Discovered %d currently open Kalshi tickers for category=%s",
        len(unique_tickers),
        category,
    )
    return unique_tickers


def _fallback_series_tickers(category: str) -> list[str]:
    specific = _parse_csv_env(f"{category.upper()}_SERIES_TICKERS")
    if specific:
        return specific
    return _parse_csv_env("MARKET_DISCOVERY_SERIES_TICKERS")


async def _fetch_series_by_category(
    client: KalshiRestClient,
    *,
    category: str,
) -> list[str]:
    discovered: list[str] = []
    cursor: str | None = None

    while True:
        series_page, cursor = await _list_series_with_backoff(client, cursor=cursor, category=category)
        discovered.extend(
            str(series["ticker"])
            for series in series_page
            if series.get("ticker")
        )
        if not cursor:
            break
        await asyncio.sleep(DISCOVERY_PAGE_DELAY_SECONDS)
    return list(dict.fromkeys(discovered))


async def _fetch_open_series_markets(
    client: KalshiRestClient,
    *,
    series_ticker: str,
) -> list[dict[str, object]]:
    discovered: list[dict[str, object]] = []
    cursor: str | None = None
    while True:
        markets, cursor = await _list_markets_with_backoff(client, cursor=cursor, series_ticker=series_ticker)
        discovered.extend(markets)
        if not cursor:
            break
        await asyncio.sleep(DISCOVERY_PAGE_DELAY_SECONDS)
    return discovered


async def _list_series_with_backoff(
    client: KalshiRestClient,
    *,
    cursor: str | None,
    category: str,
) -> tuple[list[dict[str, object]], str | None]:
    for attempt, delay in enumerate((0, *DISCOVERY_RETRY_DELAYS_SECONDS), start=1):
        if delay:
            logger.warning(
                "Rate limited while discovering series for category=%s; retrying in %ss (attempt %d)",
                category,
                delay,
                attempt,
            )
            await asyncio.sleep(delay)
        try:
            return await client.list_series(
                category=category,
                cursor=cursor,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 429:
                raise
    raise RuntimeError(f"Kalshi rate-limited series discovery too many times for category={category}")


async def _list_markets_with_backoff(
    client: KalshiRestClient,
    *,
    cursor: str | None,
    series_ticker: str,
) -> tuple[list[dict[str, object]], str | None]:
    for attempt, delay in enumerate((0, *DISCOVERY_RETRY_DELAYS_SECONDS), start=1):
        if delay:
            logger.warning(
                "Rate limited while discovering markets for %s; retrying in %ss (attempt %d)",
                series_ticker,
                delay,
                attempt,
            )
            await asyncio.sleep(delay)
        try:
            return await client.list_markets(
                cursor=cursor,
                status="open",
                series_ticker=series_ticker,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 429:
                raise
    raise RuntimeError(f"Kalshi rate-limited weather market discovery too many times for {series_ticker}")


async def _run() -> None:
    settings = WorkerSettings.from_env()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    watched_tickers = _parse_watched_tickers()
    if not watched_tickers:
        watched_tickers = await _fetch_open_tickers(settings)
    service = await build_runtime(settings=settings, watched_tickers=watched_tickers)
    try:
        logger.info("Starting ingestion service for %d tickers", len(watched_tickers))
        await service.run()
    finally:
        service.stop()
        await service.close()


def main() -> None:
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        logger.info("Ingestion service interrupted, shutting down")


if __name__ == "__main__":
    main()
