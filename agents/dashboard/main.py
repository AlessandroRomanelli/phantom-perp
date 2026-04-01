"""Web dashboard for phantom-perp — serves HTML + WebSocket live updates.

Reads Redis Streams and pushes JSON snapshots to all connected WebSocket
clients every 2 seconds. Serves a single-page HTML dashboard at /.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from pathlib import Path
from typing import Any

import orjson
import redis.asyncio as aioredis
from aiohttp import web

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
REFRESH_INTERVAL = float(os.environ.get("DASHBOARD_REFRESH", "2"))
PORT = int(os.environ.get("DASHBOARD_PORT", "8080"))

STREAMS = [
    "stream:market_snapshots",
    "stream:funding_updates",
    "stream:signals",
    "stream:ranked_ideas:a",
    "stream:ranked_ideas:b",
    "stream:approved_orders:a",
    "stream:approved_orders:b",
    "stream:confirmed_orders",
    "stream:exchange_events:a",
    "stream:exchange_events:b",
    "stream:portfolio_state:a",
    "stream:portfolio_state:b",
    "stream:funding_payments:a",
    "stream:funding_payments:b",
    "stream:alerts",
    "stream:user_overrides",
]

STATIC_DIR = Path(__file__).parent / "static"


def _parse_entry(fields: dict[bytes, bytes]) -> dict[str, Any] | None:
    raw = fields.get(b"data")
    if raw is None:
        return None
    try:
        return orjson.loads(raw)
    except Exception:
        return None


def _entry_id_to_epoch(entry_id: str) -> float | None:
    try:
        return int(entry_id.split("-")[0]) / 1000.0
    except (ValueError, IndexError):
        return None


async def _collect_state(r: aioredis.Redis) -> dict[str, Any]:
    """Collect full dashboard state from Redis."""
    streams_info: dict[str, Any] = {}
    for stream in STREAMS:
        try:
            length = await r.xlen(stream)
        except Exception:
            length = 0
        latest = None
        latest_id = None
        if length > 0:
            try:
                entries = await r.xrevrange(stream, "+", "-", count=1)
                if entries:
                    latest_id = entries[0][0]
                    if isinstance(latest_id, bytes):
                        latest_id = latest_id.decode()
                    latest = _parse_entry(entries[0][1])
            except Exception:
                pass
        streams_info[stream] = {
            "length": length,
            "latest": latest,
            "latest_id": latest_id,
            "latest_epoch": _entry_id_to_epoch(latest_id) if latest_id else None,
        }

    # Per-instrument snapshots
    per_instrument: dict[str, Any] = {}
    try:
        entries = await r.xrevrange("stream:market_snapshots", "+", "-", count=100)
        for _entry_id, fields in entries:
            parsed = _parse_entry(fields)
            if parsed and parsed.get("instrument") and parsed["instrument"] not in per_instrument:
                per_instrument[parsed["instrument"]] = parsed
    except Exception:
        pass

    # Recent signals
    recent_signals: list[dict[str, Any]] = []
    try:
        entries = await r.xrevrange("stream:signals", "+", "-", count=100)
        for entry_id, fields in entries:
            parsed = _parse_entry(fields)
            if parsed:
                eid = entry_id.decode() if isinstance(entry_id, bytes) else entry_id
                parsed["_entry_epoch"] = _entry_id_to_epoch(eid)
                recent_signals.append(parsed)
    except Exception:
        pass

    # Feature store status
    store_status: dict[str, Any] = {}
    try:
        data = await r.hgetall("phantom:feature_store_status")
        # Redis keys are "ETH-PERP:slow" and "ETH-PERP:fast" — aggregate per instrument
        raw: dict[str, int] = {
            (k.decode() if isinstance(k, bytes) else k): int(v)
            for k, v in data.items()
        }
        # Build per-instrument dict with slow/fast breakdown
        for key, count in raw.items():
            if ":" in key:
                instrument, speed = key.rsplit(":", 1)
            else:
                instrument, speed = key, "slow"
            if instrument not in store_status:
                store_status[instrument] = {"slow": 0, "fast": 0}
            store_status[instrument][speed] = count
    except Exception:
        pass

    # Claude last analysis state per instrument
    claude_state: dict[str, Any] = {}
    try:
        data = await r.hgetall("phantom:claude:last_analysis")
        for k, v in data.items():
            instrument = k.decode() if isinstance(k, bytes) else k
            with contextlib.suppress(Exception):
                claude_state[instrument] = orjson.loads(v)
    except Exception:
        pass

    # Recent fills from both routes
    recent_fills: list[dict[str, Any]] = []
    for suffix in ("a", "b"):
        try:
            entries = await r.xrevrange(
                f"stream:exchange_events:{suffix}", "+", "-", count=20,
            )
            for entry_id, fields in entries:
                parsed = _parse_entry(fields)
                if parsed:
                    eid = entry_id.decode() if isinstance(entry_id, bytes) else entry_id
                    parsed["_entry_epoch"] = _entry_id_to_epoch(eid)
                    recent_fills.append(parsed)
        except Exception:
            pass
    # Sort by epoch descending (newest first)
    recent_fills.sort(key=lambda f: f.get("_entry_epoch", 0) or 0, reverse=True)

    return {
        "streams": streams_info,
        "instruments": per_instrument,
        "signals": recent_signals,
        "feature_stores": store_status,
        "fills": recent_fills[:20],
        "claude_state": claude_state,
    }


# ---- WebSocket handler ----

ws_clients: set[web.WebSocketResponse] = set()


async def ws_handler(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    ws_clients.add(ws)
    try:
        async for _ in ws:
            pass  # We only push, never read
    finally:
        ws_clients.discard(ws)
    return ws


async def broadcast_loop(app: web.Application) -> None:
    """Background task: collect state and broadcast to all WS clients."""
    r: aioredis.Redis = app["redis"]
    while True:
        try:
            state = await _collect_state(r)
            payload = orjson.dumps(state)
            dead: list[web.WebSocketResponse] = []
            for ws in ws_clients:
                try:
                    await ws.send_bytes(payload)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                ws_clients.discard(ws)
        except Exception:
            pass
        await asyncio.sleep(REFRESH_INTERVAL)


# ---- HTTP handlers ----

async def index_handler(request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC_DIR / "index.html")


# ---- App lifecycle ----

async def on_startup(app: web.Application) -> None:
    app["redis"] = aioredis.from_url(REDIS_URL, decode_responses=False)
    app["broadcast_task"] = asyncio.create_task(broadcast_loop(app))


async def on_cleanup(app: web.Application) -> None:
    app["broadcast_task"].cancel()
    await app["redis"].aclose()


def create_app() -> web.Application:
    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    app.router.add_get("/", index_handler)
    app.router.add_get("/ws", ws_handler)
    app.router.add_static("/static", STATIC_DIR)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), port=PORT)
