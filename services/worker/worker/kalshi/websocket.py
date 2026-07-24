from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any, Optional
from urllib.parse import urlparse

from worker.kalshi.auth import KalshiCredentials

logger = logging.getLogger(__name__)

WS_PROD_URL = "wss://api.elections.kalshi.com/trade-api/ws/v2"
WS_DEMO_URL = "wss://demo-api.kalshi.co/trade-api/ws/v2"
RECONNECT_DELAYS = (1, 2, 5, 10, 30)
OPEN_TIMEOUT_SECONDS = 15

MarketUpdateCallback = Callable[[dict[str, Any]], Optional[Awaitable[None]]]
LifecycleCallback = Callable[[], Optional[Awaitable[None]]]


class SequenceGapError(Exception):
    def __init__(self, ticker: str, last_seq: int, current_seq: int) -> None:
        self.ticker = ticker
        self.last_seq = last_seq
        self.current_seq = current_seq
        super().__init__(
            f"Sequence gap detected for {ticker}: expected {last_seq + 1}, got {current_seq}"
        )


class KalshiWebSocketClient:
    def __init__(
        self,
        *,
        tickers: list[str],
        on_market_update: MarketUpdateCallback,
        on_disconnect: LifecycleCallback,
        on_reconnect: LifecycleCallback,
        url: str | None = None,
        credentials: KalshiCredentials | None = None,
        logger_: logging.Logger | None = None,
    ) -> None:
        self._tickers = tickers
        self._on_market_update = on_market_update
        self._on_disconnect = on_disconnect
        self._on_reconnect = on_reconnect
        self._logger = logger_ or logging.getLogger(__name__)
        self._url = url or (WS_DEMO_URL if os.getenv("KALSHI_USE_DEMO") == "true" else WS_PROD_URL)
        self._credentials = credentials
        self._last_sequence: dict[str, int] = {}
        self._sid_to_ticker: dict[int, str] = {}
        self._stop_event = asyncio.Event()
        self._websocket: Any = None

    async def run(self) -> None:
        if not self._tickers:
            self._logger.warning("No websocket tickers configured; skipping websocket connection")
            return

        try:
            import websockets
        except ImportError as exc:
            raise RuntimeError("websockets>=12.0 is required for KalshiWebSocketClient") from exc

        reconnect_index = 0
        has_connected_once = False

        while not self._stop_event.is_set():
            try:
                self._logger.info("Connecting to Kalshi websocket at %s", self._url)
                async with websockets.connect(
                    self._url,
                    additional_headers=self._handshake_headers(),
                    open_timeout=OPEN_TIMEOUT_SECONDS,
                ) as websocket:
                    self._websocket = websocket
                    self._logger.info("Kalshi websocket connected")
                    await self._subscribe(websocket)
                    if has_connected_once:
                        await self._maybe_await(self._on_reconnect())
                    has_connected_once = True
                    reconnect_index = 0
                    await self._consume_messages(websocket)
            except SequenceGapError:
                if not self._stop_event.is_set():
                    await self._maybe_await(self._on_disconnect())
                self._websocket = None
                raise
            except asyncio.CancelledError:
                raise
            except Exception:
                if self._stop_event.is_set():
                    break
                await self._maybe_await(self._on_disconnect())
                delay = RECONNECT_DELAYS[min(reconnect_index, len(RECONNECT_DELAYS) - 1)]
                reconnect_index += 1
                self._logger.warning(
                    "Kalshi websocket disconnected; reconnecting in %ss",
                    delay,
                    exc_info=True,
                )
                self._websocket = None
                await asyncio.sleep(delay)
            else:
                self._websocket = None
                if not self._stop_event.is_set():
                    await self._maybe_await(self._on_disconnect())
                    delay = RECONNECT_DELAYS[min(reconnect_index, len(RECONNECT_DELAYS) - 1)]
                    reconnect_index += 1
                    self._logger.warning(
                        "Kalshi websocket connection closed; reconnecting in %ss",
                        delay,
                    )
                    await asyncio.sleep(delay)

    def _handshake_headers(self) -> dict[str, str] | None:
        if self._credentials is None:
            return None
        return self._credentials.create_headers(
            method="GET",
            path=urlparse(self._url).path,
        )

    async def _subscribe(self, websocket: Any) -> None:
        if not self._tickers:
            self._logger.warning("No websocket tickers configured; skipping subscription")
            return

        self._logger.info(
            "Subscribing to websocket channels for %d tickers",
            len(self._tickers),
        )
        await websocket.send(
            json.dumps(
                {
                    "id": 1,
                    "cmd": "subscribe",
                    "params": {
                        "channels": ["orderbook_delta", "ticker"],
                        "market_tickers": self._tickers,
                    },
                }
            )
        )
        self._logger.info("Kalshi websocket subscription sent")

    async def _consume_messages(self, websocket: Any) -> None:
        async for raw_message in websocket:
            if self._stop_event.is_set():
                break
            try:
                message = json.loads(raw_message)
            except json.JSONDecodeError:
                self._logger.warning("Received non-JSON websocket payload: %r", raw_message)
                continue
            if not isinstance(message, dict):
                continue
            enriched_message = self._enrich_message(message)
            self._logger.debug("Received websocket message type=%s", enriched_message.get("type"))
            self._track_sequence(enriched_message)
            await self._maybe_await(self._on_market_update(enriched_message))

    def _track_sequence(self, message: dict[str, Any]) -> None:
        sequence = message.get("seq")
        if sequence is None:
            return

        stream_key = self._sequence_key(message)
        if stream_key is None:
            return

        current_seq = int(sequence)
        last_seq = self._last_sequence.get(stream_key)
        if last_seq is not None and current_seq > last_seq + 1:
            ticker = (
                message.get("market_ticker")
                or message.get("ticker")
                or _payload_market_ticker(message.get("msg"))
                or stream_key
            )
            self._logger.warning(
                "Sequence gap detected for %s (%s): last=%s current=%s",
                ticker,
                stream_key,
                last_seq,
                current_seq,
            )
            raise SequenceGapError(str(ticker), last_seq, current_seq)
        if last_seq is None or current_seq > last_seq:
            self._last_sequence[stream_key] = current_seq

    def stop(self) -> None:
        self._stop_event.set()
        websocket = self._websocket
        if websocket is not None:
            asyncio.create_task(websocket.close())

    async def _maybe_await(self, result: Awaitable[None] | None) -> None:
        if inspect.isawaitable(result):
            await result

    def _enrich_message(self, message: dict[str, Any]) -> dict[str, Any]:
        if message.get("type") == "subscribed":
            self._register_subscription(message.get("msg"))

        if "market_ticker" not in message:
            payload_ticker = _payload_market_ticker(message.get("msg"))
            if payload_ticker is not None:
                enriched = dict(message)
                enriched["market_ticker"] = payload_ticker
                return enriched

        if "market_ticker" in message or "ticker" in message:
            return message

        sid = message.get("sid")
        if sid is None:
            return message

        ticker = self._sid_to_ticker.get(int(sid))
        if ticker is None:
            return message

        enriched = dict(message)
        enriched["market_ticker"] = ticker
        return enriched

    def _register_subscription(self, payload: Any) -> None:
        if isinstance(payload, list):
            for item in payload:
                self._register_subscription(item)
            return
        if not isinstance(payload, dict):
            return

        sid = payload.get("sid")
        ticker = payload.get("market_ticker") or payload.get("ticker")
        if sid is None or ticker is None:
            return
        self._sid_to_ticker[int(sid)] = str(ticker)

    def _sequence_key(self, message: dict[str, Any]) -> str | None:
        sid = message.get("sid")
        if sid is not None:
            return f"sid:{int(sid)}"

        ticker = (
            message.get("market_ticker")
            or message.get("ticker")
            or _payload_market_ticker(message.get("msg"))
        )
        if ticker is None:
            return None
        return f"ticker:{ticker}"


def _payload_market_ticker(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    ticker = payload.get("market_ticker") or payload.get("ticker")
    if ticker is None:
        return None
    return str(ticker)
