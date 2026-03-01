"""
WebSocket proxy for ginlix-data real-time market aggregates.

Authenticates the frontend WebSocket via Supabase JWT, then opens a
backend WebSocket to ginlix-data using the internal service token.
Messages are forwarded bidirectionally until either side disconnects.

WS ticks are also written into the Redis OHLCV cache so that REST
reads always reflect near-real-time data (WS-fed cache).

The entire router is only registered when ``GINLIX_DATA_ENABLED`` is
true (i.e. ``GINLIX_DATA_WS_URL`` is set) — see ``setup.py``.
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import websockets
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.config.settings import GINLIX_DATA_WS_URL
from src.server.auth.ws_auth import authenticate_websocket
from src.server.services.cache._ohlcv_envelope import _build_envelope, _parse_envelope
from src.server.services.cache.intraday_cache_service import IntradayCacheKeyBuilder
from src.utils.cache.redis_cache import get_cache_client

logger = logging.getLogger(__name__)

router = APIRouter()

_INTERNAL_SERVICE_TOKEN = os.getenv("INTERNAL_SERVICE_TOKEN", "")
_ALLOWED_MARKETS = {"stock", "index", "crypto", "forex"}

# Map WS interval param → cache interval key
_WS_INTERVAL_TO_CACHE: dict[str, str] = {
    "second": "1s",
    "minute": "1min",
}

_WS_CACHE_TTL = 30  # seconds — longer TTL survives brief WS hiccups
_WS_SOURCE = "ginlix-data"  # must match config.yaml provider name


def _parse_ws_bar(raw_msg: str) -> Optional[dict]:
    """Parse a WS message into a normalised bar dict.

    Returns ``None`` for non-aggregate messages (status, keepalive, etc.).
    """
    try:
        msg = json.loads(raw_msg)
    except (json.JSONDecodeError, TypeError):
        return None

    symbol: Optional[str] = None
    o = h = l = c = v = ts = None

    if isinstance(msg, dict):
        ev = msg.get("ev")
        if ev in ("AM", "A"):
            # Raw Polygon-style aggregate
            symbol = msg.get("sym")
            o, h, l, c, v = msg.get("o"), msg.get("h"), msg.get("l"), msg.get("c"), msg.get("v")
            ts = msg.get("s") or msg.get("e")
        elif msg.get("type") == "aggregate" and isinstance(msg.get("data"), dict):
            # Wrapped format
            d = msg["data"]
            symbol = msg.get("symbol") or d.get("sym") or d.get("symbol")
            o = d.get("open", d.get("o"))
            h = d.get("high", d.get("h"))
            l = d.get("low", d.get("l"))
            c = d.get("close", d.get("c"))
            v = d.get("volume", d.get("v"))
            ts = d.get("timestamp", d.get("s", d.get("e")))

    if not symbol or c is None or ts is None:
        return None

    # Convert ms timestamp to ISO-8601 datetime string matching cache bar format
    if isinstance(ts, (int, float)):
        if ts > 1e12:
            ts = ts / 1000  # ms → s
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    else:
        return None

    return {
        "symbol": symbol.upper(),
        "date": dt.strftime("%Y-%m-%d %H:%M:%S"),
        "open": float(o) if o is not None else 0.0,
        "high": float(h) if h is not None else 0.0,
        "low": float(l) if l is not None else 0.0,
        "close": float(c) if c is not None else 0.0,
        "volume": int(v) if v is not None else 0,
    }


async def _update_cache_from_tick(bar: dict, market: str, cache_interval: str) -> None:
    """Append or update the last bar in the Redis OHLCV envelope."""
    try:
        cache = get_cache_client()
        symbol = bar["symbol"]

        if market == "index":
            cache_key = IntradayCacheKeyBuilder.index_key(
                symbol, cache_interval, source=_WS_SOURCE,
            )
        else:
            cache_key = IntradayCacheKeyBuilder.stock_key(
                symbol, cache_interval, source=_WS_SOURCE,
            )

        raw = await cache.get(cache_key)
        envelope = _parse_envelope(raw) if raw else None

        new_bar = {
            "date": bar["date"],
            "open": bar["open"],
            "high": bar["high"],
            "low": bar["low"],
            "close": bar["close"],
            "volume": bar["volume"],
        }

        if envelope and envelope.get("bars"):
            bars = envelope["bars"]
            if bars[-1]["date"] == new_bar["date"]:
                # Same timestamp — update in place
                bars[-1] = new_bar
            elif new_bar["date"] > bars[-1]["date"]:
                # Newer — append
                bars.append(new_bar)
            # else: out-of-order tick, ignore
        else:
            bars = [new_bar]

        phase = envelope.get("market_phase", "open") if envelope else "open"
        new_envelope = _build_envelope(bars, phase, complete=False, stored_ttl=_WS_CACHE_TTL)
        await cache.set(cache_key, new_envelope, ttl=_WS_CACHE_TTL)
    except Exception:
        logger.debug("WS cache update failed for %s", bar.get("symbol"), exc_info=True)


@router.get("/ws/v1/market-data/status")
async def market_data_ws_status():
    """Lightweight probe — returns 200 when the WS proxy feature is enabled.
    Used by the frontend preflight check to avoid noisy WS handshake failures."""
    return {"enabled": True}


@router.websocket("/ws/v1/market-data/aggregates/{market}")
async def ws_market_data_proxy(websocket: WebSocket, market: str, interval: str = "minute"):
    """Proxy frontend WS to ginlix-data aggregate stream."""

    if market not in _ALLOWED_MARKETS:
        await websocket.close(code=1008, reason=f"Invalid market: {market}")
        return

    # Authenticate before accepting
    try:
        user_id = await authenticate_websocket(websocket)
    except Exception:
        return  # ws_auth already closed the socket

    await websocket.accept()
    logger.info("WS proxy opened: user=%s market=%s interval=%s", user_id, market, interval)

    # Build backend URL
    backend_url = f"{GINLIX_DATA_WS_URL}/ws/v1/data/aggregates/{market}?interval={interval}"
    backend_headers = {"X-User-Id": user_id}
    if _INTERNAL_SERVICE_TOKEN:
        backend_headers["X-Service-Token"] = _INTERNAL_SERVICE_TOKEN

    try:
        async with websockets.connect(
            backend_url,
            additional_headers=backend_headers,
            ping_interval=20,
            ping_timeout=10,
            close_timeout=5,
        ) as backend_ws:

            async def client_to_backend():
                """Forward messages from the frontend client to ginlix-data."""
                try:
                    while True:
                        msg = await websocket.receive_text()
                        await backend_ws.send(msg)
                except WebSocketDisconnect:
                    pass  # Client disconnected
                except Exception as exc:
                    logger.debug("client_to_backend closed: %s", exc)

            cache_interval = _WS_INTERVAL_TO_CACHE.get(interval)

            async def backend_to_client():
                """Forward messages from ginlix-data to the frontend client."""
                try:
                    async for msg in backend_ws:
                        await websocket.send_text(msg)

                        # Fire-and-forget cache update for cacheable intervals
                        if cache_interval:
                            bar = _parse_ws_bar(msg)
                            if bar:
                                asyncio.create_task(
                                    _update_cache_from_tick(bar, market, cache_interval)
                                )
                except websockets.exceptions.ConnectionClosed:
                    pass  # Backend disconnected
                except Exception as exc:
                    logger.debug("backend_to_client closed: %s", exc)

            # Run both directions concurrently; when either finishes, cancel the other
            done, pending = await asyncio.wait(
                [
                    asyncio.create_task(client_to_backend()),
                    asyncio.create_task(backend_to_client()),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()

    except (websockets.exceptions.WebSocketException, OSError) as exc:
        logger.warning("Backend WS connection failed: %s", exc)
    finally:
        # Ensure client socket is closed
        try:
            await websocket.close()
        except Exception:
            pass
        logger.info("WS proxy closed: user=%s market=%s", user_id, market)
