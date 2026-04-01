#!/usr/bin/env python3
"""Live terminal dashboard for the phantom-perp pipeline.

Polls Redis Streams and displays a real-time summary of all agents,
stream throughput, latest market data, signals, orders, and route
performance.

Usage:
    python scripts/dashboard.py                  # default: redis://localhost:6379
    python scripts/dashboard.py --redis redis://redis:6379
    python scripts/dashboard.py --refresh 3      # refresh every 3 seconds
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import shutil
import sys
from datetime import UTC, datetime
from typing import Any

import orjson
import redis.asyncio as aioredis

# All known streams in the pipeline
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

# ANSI
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"
MAGENTA = "\033[35m"
WHITE = "\033[37m"
BG_RED = "\033[41m"
BG_GREEN = "\033[42m"


def _ts_age(iso_str: str | None) -> str:
    if not iso_str:
        return "n/a"
    try:
        ts = datetime.fromisoformat(iso_str)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        secs = (datetime.now(UTC) - ts).total_seconds()
        if secs < 0:
            return "future"
        if secs < 60:
            return f"{secs:.0f}s ago"
        if secs < 3600:
            return f"{secs / 60:.0f}m ago"
        return f"{secs / 3600:.1f}h ago"
    except (ValueError, TypeError):
        return "?"


def _entry_id_to_ts(entry_id: str) -> datetime | None:
    try:
        ms = int(entry_id.split("-")[0])
        return datetime.fromtimestamp(ms / 1000, tz=UTC)
    except (ValueError, IndexError):
        return None


def _parse_entry(fields: dict[bytes, bytes]) -> dict[str, Any] | None:
    raw = fields.get(b"data")
    if raw is None:
        return None
    try:
        return orjson.loads(raw)
    except Exception:
        return None


def _pnl_color(val: str | float | None) -> str:
    if val is None:
        return DIM
    try:
        v = float(val)
    except (ValueError, TypeError):
        return WHITE
    if v > 0:
        return GREEN
    if v < 0:
        return RED
    return WHITE


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------


async def _get_stream_info(r: aioredis.Redis) -> dict[str, dict[str, Any]]:
    info: dict[str, dict[str, Any]] = {}
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

        info[stream] = {"length": length, "latest": latest, "latest_id": latest_id}
    return info



async def _get_recent_signals(
    r: aioredis.Redis, count: int = 10,
) -> list[dict[str, Any]]:
    """Read recent signals for the activity log."""
    try:
        entries = await r.xrevrange("stream:signals", "+", "-", count=count)
        results = []
        for entry_id, fields in entries:
            parsed = _parse_entry(fields)
            if parsed:
                eid = entry_id.decode() if isinstance(entry_id, bytes) else entry_id
                parsed["_entry_id"] = eid
                results.append(parsed)
        return results  # newest first
    except Exception:
        return []


async def _get_equity_history(
    r: aioredis.Redis, suffix: str, count: int = 30,
) -> list[dict[str, Any]]:
    """Read recent route snapshots for equity sparkline."""
    stream = f"stream:portfolio_state:{suffix}"
    try:
        entries = await r.xrevrange(stream, "+", "-", count=count)
        results = []
        for _, fields in entries:
            parsed = _parse_entry(fields)
            if parsed:
                results.append(parsed)
        return list(reversed(results))  # oldest first
    except Exception:
        return []


async def _get_per_instrument_snapshots(
    r: aioredis.Redis, count: int = 100,
) -> dict[str, dict[str, Any]]:
    """Group recent snapshots by instrument, keeping latest for each."""
    try:
        entries = await r.xrevrange("stream:market_snapshots", "+", "-", count=count)
        by_instrument: dict[str, dict[str, Any]] = {}
        for entry_id, fields in entries:
            parsed = _parse_entry(fields)
            if parsed and parsed.get("instrument") and parsed["instrument"] not in by_instrument:
                eid = entry_id.decode() if isinstance(entry_id, bytes) else entry_id
                parsed["_entry_id"] = eid
                by_instrument[parsed["instrument"]] = parsed
        return by_instrument
    except Exception:
        return {}


async def _get_feature_store_status(r: aioredis.Redis) -> dict[str, int]:
    """Read per-instrument sample counts from Redis hash."""
    try:
        data = await r.hgetall("phantom:feature_store_status")
        return {
            (k.decode() if isinstance(k, bytes) else k): int(v)
            for k, v in data.items()
        }
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------


def _format_strategy_overview() -> list[str]:
    """Show active strategies, session type, and conviction routing."""
    lines: list[str] = []

    strategies = [
        ("momentum", "MOM"),
        ("mean_reversion", "MR"),
        ("contrarian_funding", "CF"),
        ("liquidation_cascade", "LIQ"),
        ("correlation", "CORR"),
        ("regime_trend", "RT"),
        ("orderbook_imbalance", "OBI"),
        ("vwap", "VWAP"),
        ("oi_divergence", "OID"),
        ("claude_market_analysis", "CLAU"),
    ]

    # Determine current session type
    now = datetime.now(UTC)
    weekday = now.weekday()  # 0=Mon, 6=Sun
    hour = now.hour

    # Session classification (matches session_classifier.py logic)
    is_equity_hours = weekday < 5 and 13 <= hour < 20  # 13:30-20:00 UTC approx
    if weekday >= 5:
        session = "crypto_weekend"
        session_color = YELLOW
    elif is_equity_hours:
        session = "equity_market_hours"
        session_color = GREEN
    else:
        session = "crypto_weekday"
        session_color = CYAN

    strat_labels = [f"{CYAN}{abbr}{RESET}" for _, abbr in strategies]
    lines.append(f"  Active: {' '.join(strat_labels)}")
    lines.append(
        f"  Session: {session_color}{session}{RESET}"
        f"  |  Route A threshold: {BOLD}0.70{RESET} (unified)"
        f"  |  Bands: {DIM}L{RESET}<0.50 {YELLOW}M{RESET}<0.70 {GREEN}H{RESET}>=0.70"
    )

    return lines


def _format_instrument_snapshots(
    per_instrument: dict[str, dict[str, Any]],
) -> list[str]:
    """Show per-instrument snapshot status table."""
    lines: list[str] = []
    lines.append(
        f"  {DIM}{'Instrument':<12} {'Mark Price':>12} {'Spread':>8}"
        f" {'Funding':>10} {'Age':>8} {'Status':>8}{RESET}"
    )
    lines.append(
        f"  {DIM}{'---':.<12} {'---':.<12} {'---':.<8}"
        f" {'---':.<10} {'---':.<8} {'---':.<8}{RESET}"
    )

    ordered = ["ETH-PERP", "BTC-PERP", "SOL-PERP", "QQQ-PERP", "SPY-PERP"]
    for iid in ordered:
        snap = per_instrument.get(iid)
        if not snap:
            lines.append(
                f"  {DIM}{iid:<12} {'--':>12} {'--':>8}"
                f" {'--':>10} {'--':>8} {RED}{'NONE':>8}{RESET}"
            )
            continue
        mark = snap.get("mark_price", "?")
        spread = snap.get("spread_bps")
        funding = snap.get("funding_rate", "?")
        ts_str = snap.get("timestamp")
        age_str = _ts_age(ts_str)

        # Determine status from age
        try:
            ts = datetime.fromisoformat(ts_str) if ts_str else None
            if ts and ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            secs = (datetime.now(UTC) - ts).total_seconds() if ts else 999
        except (ValueError, TypeError):
            secs = 999
        if secs < 30:
            status = f"{GREEN}{'OK':>8}{RESET}"
        elif secs < 120:
            status = f"{YELLOW}{'STALE':>8}{RESET}"
        else:
            status = f"{RED}{'DOWN':>8}{RESET}"

        spread_str = f"{spread:.1f}" if spread is not None else "--"
        lines.append(
            f"  {WHITE}{iid:<12}{RESET} {BOLD}${mark:>11}{RESET}"
            f" {spread_str:>8} {funding:>10} {age_str:>8} {status}"
        )
    return lines


def _format_feature_store_status(store_status: dict[str, int]) -> list[str]:
    """Show per-instrument FeatureStore sample counts."""
    lines: list[str] = []
    ordered = ["ETH-PERP", "BTC-PERP", "SOL-PERP", "QQQ-PERP", "SPY-PERP"]
    if not store_status:
        lines.append(f"  {DIM}Awaiting signals agent data{RESET}")
        return lines
    lines.append(f"  {DIM}{'Instrument':<12} {'Samples':>8}{RESET}")
    lines.append(f"  {DIM}{'---':.<12} {'---':.<8}{RESET}")
    for iid in ordered:
        count = store_status.get(iid, 0)
        color = GREEN if count > 0 else RED
        lines.append(f"  {WHITE}{iid:<12}{RESET} {color}{count:>8}{RESET}")
    return lines



def _sparkline(values: list[float], width: int = 20) -> str:
    """Render a mini sparkline chart from values."""
    blocks = " _.-~*"
    if not values:
        return ""
    # Downsample if needed
    if len(values) > width:
        step = len(values) / width
        sampled = [values[int(i * step)] for i in range(width)]
    else:
        sampled = values

    lo, hi = min(sampled), max(sampled)
    span = hi - lo if hi != lo else 1
    chars = []
    for v in sampled:
        idx = int((v - lo) / span * (len(blocks) - 1))
        chars.append(blocks[idx])
    return f"{DIM}[{RESET}{''.join(chars)}{DIM}]{RESET}"


def _format_funding(info: dict[str, dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    data = info.get("stream:funding_updates", {})
    snap = data.get("latest")

    if not snap:
        lines.append(f"  {DIM}No funding data yet{RESET}")
        return lines

    rate = snap.get("rate", "?")
    instrument = snap.get("instrument", "?")
    ts = snap.get("timestamp") or snap.get("event_time")
    lines.append(f"  {instrument}: rate={BOLD}{rate}{RESET}  {DIM}({_ts_age(ts)}){RESET}")
    return lines


def _format_signals(info: dict[str, dict[str, Any]], recent: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    data = info.get("stream:signals", {})
    count = data.get("length", 0)

    if count == 0:
        lines.append(f"  {DIM}No signals emitted yet{RESET}")
        return lines

    lines.append(f"  Total emitted: {BOLD}{count}{RESET}")

    # Show recent signals log (up to 5)
    for sig in recent[:5]:
        direction = sig.get("direction", "?")
        source = sig.get("source", "?")
        conviction = sig.get("conviction", "?")
        suggested_route = sig.get("suggested_route")
        entry_id = sig.get("_entry_id", "")

        ts = _entry_id_to_ts(entry_id)
        age = _ts_age(ts.isoformat()) if ts else ""

        dir_color = GREEN if direction in ("LONG", "BUY") else RED if direction in ("SHORT", "SELL") else WHITE

        # Route indicator
        if suggested_route == "autonomous" or suggested_route == "A":
            target_str = f"{MAGENTA}A{RESET}"
        elif suggested_route == "user_confirmed" or suggested_route == "B":
            target_str = f"{DIM}B{RESET}"
        else:
            target_str = f"{DIM}?{RESET}"

        # Conviction bar with band label
        try:
            conv_val = float(conviction)
            conv_bar = "#" * int(conv_val * 10)
            conv_empty = "." * (10 - int(conv_val * 10))
            if conv_val >= 0.70:
                band = f"{GREEN}H{RESET}"
            elif conv_val >= 0.50:
                band = f"{YELLOW}M{RESET}"
            else:
                band = f"{DIM}L{RESET}"
            conv_str = f"[{conv_bar}{conv_empty}] {conv_val:.2f} {band}"
        except (ValueError, TypeError):
            conv_str = str(conviction)

        lines.append(
            f"  {DIM}{age:>8}{RESET}  {dir_color}{direction:<5}{RESET}"
            f" {CYAN}{source:<22}{RESET} {target_str} {conv_str}"
        )

    return lines


def _format_risk(info: dict[str, dict[str, Any]]) -> list[str]:
    lines: list[str] = []

    for label, stream in [("A", "stream:approved_orders:a"), ("B", "stream:approved_orders:b")]:
        snap = info.get(stream, {}).get("latest")
        if snap:
            side = snap.get("side", "?")
            size = snap.get("size", "?")
            instrument = snap.get("instrument", "?")
            side_color = GREEN if side == "BUY" else RED
            lines.append(
                f"  Latest {label}: {side_color}{side}{RESET} {size} {instrument}"
                f" | conviction: {snap.get('conviction', '?')}"
            )

    if not lines:
        lines.append(f"  {DIM}No approved orders yet{RESET}")

    return lines


def _format_execution(info: dict[str, dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    events_a = info.get("stream:exchange_events:a", {}).get("length", 0)
    events_b = info.get("stream:exchange_events:b", {}).get("length", 0)
    confirmed = info.get("stream:confirmed_orders", {}).get("length", 0)

    if events_a + events_b + confirmed == 0:
        lines.append(f"  {DIM}No execution activity yet{RESET}")
        return lines

    lines.append(f"  Exchange events: A={events_a}, B={events_b}")
    if confirmed:
        lines.append(f"  Confirmed orders (B): {confirmed}")
    return lines


def _format_route_performance(
    info: dict[str, dict[str, Any]],
    equity_a: list[dict[str, Any]],
    equity_b: list[dict[str, Any]],
) -> list[str]:
    """Format unified portfolio and per-route performance.

    Since v1.5, both routes share a single Coinbase portfolio.
    Shows one unified portfolio section, then per-route order/signal activity.
    """
    lines: list[str] = []
    equity_histories = {"a": equity_a, "b": equity_b}

    # ── Unified Portfolio (read from Route A stream — same data as B) ──
    stream_a = "stream:portfolio_state:a"
    data_a = info.get(stream_a, {})
    snap = data_a.get("latest")

    if not snap:
        lines.append(f"  {DIM}Awaiting portfolio data{RESET}")
        return lines

    equity = snap.get("equity_usdc", "?")
    used_margin = snap.get("used_margin_usdc", "0")
    avail_margin = snap.get("available_margin_usdc", "0")
    margin_util = snap.get("margin_utilization_pct")
    unrealized = snap.get("unrealized_pnl_usdc", "0")
    realized = snap.get("realized_pnl_today_usdc", "0")
    funding_pnl = snap.get("funding_pnl_today_usdc", "0")
    fees = snap.get("fees_paid_today_usdc", "0")
    positions = snap.get("position_count", 0)
    ts = snap.get("timestamp")

    lines.append(f"  {BOLD}Unified Portfolio{RESET}  {DIM}(single Coinbase account){RESET}")
    lines.append(
        f"    Equity: {BOLD}${equity}{RESET} USDC"
        f"  |  Margin: ${used_margin} / ${avail_margin}"
        f"  |  Positions: {positions}"
    )

    # Margin utilization bar
    if margin_util is not None:
        mu_color = GREEN if margin_util < 40 else YELLOW if margin_util < 60 else RED
        bar_filled = min(int(margin_util / 5), 20)
        bar_empty = 20 - bar_filled
        lines.append(
            f"    Margin util: {mu_color}[{'|' * bar_filled}{'.' * bar_empty}]{RESET}"
            f" {margin_util:.1f}%"
        )

    # P&L breakdown
    uclr = _pnl_color(unrealized)
    rclr = _pnl_color(realized)
    fclr = _pnl_color(funding_pnl)
    lines.append(
        f"    Unrealized: {uclr}{_fmt_pnl(unrealized)}{RESET}"
        f"  Realized: {rclr}{_fmt_pnl(realized)}{RESET}"
        f"  Funding: {fclr}{_fmt_pnl(funding_pnl)}{RESET}"
    )

    try:
        net = float(realized) + float(unrealized) + float(funding_pnl) - float(fees)
        nclr = GREEN if net > 0 else RED if net < 0 else WHITE
        lines.append(
            f"    Net P&L: {nclr}{BOLD}${net:+,.2f}{RESET} USDC"
            f"  {DIM}(fees: ${float(fees):,.2f}){RESET}"
        )
    except (ValueError, TypeError):
        pass

    # Equity sparkline (use Route A history — same underlying portfolio)
    history = equity_histories.get("a", [])
    if len(history) >= 2:
        try:
            equities = [float(s["equity_usdc"]) for s in history]
            first_eq = equities[0]
            last_eq = equities[-1]
            eq_change = last_eq - first_eq
            eq_pct = (eq_change / first_eq) * 100 if first_eq else 0
            clr = GREEN if eq_change >= 0 else RED
            spark = _sparkline(equities)
            lines.append(
                f"    Equity trend: {spark}"
                f"  {clr}{eq_change:+,.2f} ({eq_pct:+.2f}%){RESET}"
            )
        except (ValueError, KeyError):
            pass

    # Open positions table
    pos_list = snap.get("positions", [])
    if pos_list:
        lines.append(
            f"    {DIM}{'Instrument':<12} {'Side':<6} {'Size':>10}"
            f" {'Entry':>12} {'Mark':>12} {'P&L':>12}"
            f" {'Lev':>5} {'Liq':>12}{RESET}"
        )
        for pos in pos_list:
            p_side = pos.get("side", "?")
            p_pnl = pos.get("unrealized_pnl_usdc", "0")
            p_clr = _pnl_color(p_pnl)
            side_clr = GREEN if p_side == "LONG" else RED
            lines.append(
                f"    {WHITE}{pos.get('instrument', '?'):<12}{RESET}"
                f" {side_clr}{p_side:<6}{RESET}"
                f" {pos.get('size', '?'):>10}"
                f" ${pos.get('entry_price', '?'):>11}"
                f" ${pos.get('mark_price', '?'):>11}"
                f" {p_clr}${p_pnl:>11}{RESET}"
                f" {pos.get('leverage', '?'):>5}"
                f" ${pos.get('liquidation_price', '?'):>11}"
            )
    else:
        lines.append(f"    {DIM}No open positions{RESET}")

    lines.append(f"    {DIM}Updated: {_ts_age(ts)}{RESET}")
    lines.append("")

    # ── Per-Route Activity ──
    lines.append(f"  {BOLD}Route Activity{RESET}")
    for label, suffix in [("A (Autonomous)", "a"), ("B (User-Confirmed)", "b")]:
        ideas_stream = f"stream:ranked_ideas:{suffix}"
        approved_stream = f"stream:approved_orders:{suffix}"
        ideas_count = info.get(ideas_stream, {}).get("length", 0)
        approved_count = info.get(approved_stream, {}).get("length", 0)
        rate_str = f" ({approved_count / ideas_count * 100:.0f}%)" if ideas_count > 0 else ""
        lines.append(
            f"    Route {label}: {ideas_count} ideas"
            f" → {GREEN}{approved_count} approved{RESET}{rate_str}"
        )
    lines.append("")

    # Funding payments summary
    for label, suffix in [("A", "a"), ("B", "b")]:
        fp_stream = f"stream:funding_payments:{suffix}"
        fp_data = info.get(fp_stream, {})
        fp_count = fp_data.get("length", 0)
        fp_snap = fp_data.get("latest")
        if fp_count > 0 and fp_snap:
            cum = fp_snap.get("cumulative_24h_usdc", "0")
            rate = fp_snap.get("rate", "?")
            cclr = _pnl_color(cum)
            lines.append(
                f"  Funding {label}: {fp_count} settlements"
                f" | 24h cumulative: {cclr}${cum}{RESET}"
                f" | last rate: {rate}"
            )

    return lines


def _fmt_pnl(val: str | float | None) -> str:
    try:
        v = float(val) if val else 0
        return f"${v:+,.2f}"
    except (ValueError, TypeError):
        return str(val)


def _format_alerts(info: dict[str, dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    data = info.get("stream:alerts", {})
    count = data.get("length", 0)
    snap = data.get("latest")

    if count == 0:
        lines.append(f"  {GREEN}No alerts{RESET}")
        return lines

    lines.append(f"  Total: {YELLOW}{count}{RESET}")
    if snap:
        severity = snap.get("severity", "?")
        alert_type = snap.get("alert_type", "?")
        message = snap.get("message", "?")
        sev_color = RED if severity == "CRITICAL" else YELLOW if severity == "WARNING" else DIM
        lines.append(f"  Latest: {sev_color}[{severity}]{RESET} {alert_type}: {message}")
    return lines


def _format_stream_table(info: dict[str, dict[str, Any]], prev_lengths: dict[str, int]) -> list[str]:
    lines: list[str] = []
    lines.append(f"  {DIM}{'Stream':<33} {'Count':>8}  {'Rate':>6}  {'Last':>10}{RESET}")
    lines.append(f"  {DIM}{'─' * 33} {'─' * 8}  {'─' * 6}  {'─' * 10}{RESET}")

    for stream in STREAMS:
        data = info.get(stream, {})
        length = data.get("length", 0)
        latest_id = data.get("latest_id")

        # Throughput since last refresh
        prev = prev_lengths.get(stream, length)
        delta = length - prev

        age = ""
        if latest_id:
            ts = _entry_id_to_ts(latest_id)
            if ts:
                age = _ts_age(ts.isoformat())

        name = stream.replace("stream:", "")

        if length > 0:
            color = GREEN if delta > 0 else WHITE
        else:
            color = DIM

        rate_str = f"+{delta}" if delta > 0 else ""

        lines.append(
            f"  {color}{name:<33} {length:>8,}  {rate_str:>6}  {age:>10}{RESET}"
        )

    return lines


# ---------------------------------------------------------------------------
# Main render
# ---------------------------------------------------------------------------


def _render(
    info: dict[str, dict[str, Any]],
    prev_lengths: dict[str, int],
    recent_signals: list[dict[str, Any]],
    equity_a: list[dict[str, Any]],
    equity_b: list[dict[str, Any]],
    per_instrument: dict[str, dict[str, Any]],
    store_status: dict[str, int],
    term_width: int,
) -> str:
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    total_msgs = sum(d.get("length", 0) for d in info.values())
    active = sum(1 for d in info.values() if d.get("length", 0) > 0)

    sep = "─" * min(term_width - 2, 62)

    parts: list[str] = []
    parts.append("")
    parts.append(f" {BOLD}{CYAN}phantom-perp v1.5{RESET}  {DIM}{now}{RESET}")
    parts.append(
        f" {DIM}12 strategies | 4 instruments | single portfolio | "
        f"{total_msgs:,} messages across {active}/{len(STREAMS)} active streams{RESET}"
    )
    parts.append(f" {DIM}{sep}{RESET}")

    parts.append(f" {BOLD}Strategy Overview{RESET}")
    parts.extend(_format_strategy_overview())
    parts.append("")

    parts.append(f" {BOLD}Instruments{RESET}")
    parts.extend(_format_instrument_snapshots(per_instrument))
    parts.append("")

    parts.append(f" {BOLD}Feature Stores{RESET}")
    parts.extend(_format_feature_store_status(store_status))
    parts.append("")

    parts.append(f" {BOLD}Funding{RESET}")
    parts.extend(_format_funding(info))
    parts.append("")

    parts.append(f" {BOLD}Portfolio & Routes{RESET}")
    parts.extend(_format_route_performance(info, equity_a, equity_b))
    parts.append("")

    parts.append(f" {BOLD}Signals{RESET}")
    parts.extend(_format_signals(info, recent_signals))
    parts.append("")

    parts.append(f" {BOLD}Risk / Orders{RESET}")
    parts.extend(_format_risk(info))
    parts.append("")

    parts.append(f" {BOLD}Execution{RESET}")
    parts.extend(_format_execution(info))
    parts.append("")

    parts.append(f" {BOLD}Alerts{RESET}")
    parts.extend(_format_alerts(info))
    parts.append("")

    parts.append(f" {BOLD}Streams{RESET}")
    parts.extend(_format_stream_table(info, prev_lengths))
    parts.append("")

    parts.append(f" {DIM}Ctrl+C to exit | refreshing every cycle{RESET}")
    parts.append("")

    return "\n".join(parts)


_ANSI_RE = re.compile(r"\033\[[0-9;]*[A-Za-z]")


def _visible_len(s: str) -> int:
    """Length of a string excluding ANSI escape sequences."""
    return len(_ANSI_RE.sub("", s))


def _truncate_visible(s: str, width: int) -> str:
    """Truncate a string to `width` visible characters, preserving ANSI codes."""
    vis = 0
    i = 0
    while i < len(s) and vis < width:
        m = _ANSI_RE.match(s, i)
        if m:
            i = m.end()
        else:
            vis += 1
            i += 1
    # Grab any trailing ANSI reset codes
    while i < len(s):
        m = _ANSI_RE.match(s, i)
        if m:
            i = m.end()
        else:
            break
    return s[:i]


async def run_dashboard(redis_url: str, refresh: float) -> None:
    r = aioredis.from_url(redis_url, decode_responses=False)
    prev_lengths: dict[str, int] = {}

    # Hide cursor
    sys.stdout.write("\033[?25l")
    sys.stdout.flush()

    try:
        while True:
            term = shutil.get_terminal_size((80, 24))
            term_width = term.columns

            (
                info, recent_signals, equity_a, equity_b,
                per_instrument, store_status,
            ) = await asyncio.gather(
                _get_stream_info(r),
                _get_recent_signals(r, count=10),
                _get_equity_history(r, "a", count=30),
                _get_equity_history(r, "b", count=30),
                _get_per_instrument_snapshots(r),
                _get_feature_store_status(r),
            )

            output = _render(
                info, prev_lengths, recent_signals,
                equity_a, equity_b, per_instrument, store_status, term_width,
            )

            prev_lengths = {
                stream: data.get("length", 0) for stream, data in info.items()
            }

            # Clear screen and print from top — content is scrollable
            sys.stdout.write("\033[2J\033[H")
            sys.stdout.write(output)
            sys.stdout.flush()

            await asyncio.sleep(refresh)
    except KeyboardInterrupt:
        pass
    finally:
        await r.aclose()
        # Show cursor
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()


def main() -> None:
    parser = argparse.ArgumentParser(description="phantom-perp pipeline dashboard")
    parser.add_argument(
        "--redis",
        default=os.environ.get("REDIS_URL", "redis://localhost:6379"),
        help="Redis URL (default: $REDIS_URL or redis://localhost:6379)",
    )
    parser.add_argument(
        "--refresh",
        type=float,
        default=2.0,
        help="Refresh interval in seconds (default: 2)",
    )
    args = parser.parse_args()

    asyncio.run(run_dashboard(args.redis, args.refresh))


if __name__ == "__main__":
    main()
