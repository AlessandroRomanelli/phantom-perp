"""Monitoring agent — performance tracking, alerts, funding & fee reporting.

Subscribes to:
  - stream:portfolio_state:a  (PortfolioSnapshot for A)
  - stream:portfolio_state:b  (PortfolioSnapshot for B)
  - stream:funding_payments:a (hourly funding for A)
  - stream:funding_payments:b (hourly funding for B)
  - stream:exchange_events:a  (fills for A — fee tracking)
  - stream:exchange_events:b  (fills for B — fee tracking)

Publishes to:
  - stream:alerts             (monitoring alerts)
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from libs.common.config import get_settings, load_yaml_config
from libs.common.constants import (
    ROUTE_A_DAILY_LOSS_KILL_PCT,
    ROUTE_A_MAX_DRAWDOWN_PCT,
    ROUTE_B_MAX_DAILY_LOSS_PCT,
    ROUTE_B_MAX_DRAWDOWN_PCT,
)
from libs.common.logging import setup_logging
from libs.common.models.enums import OrderSide, Route, PositionSide
from libs.common.models.funding import FundingPayment
from libs.common.models.order import Fill
from libs.common.models.portfolio import PortfolioSnapshot
from libs.common.utils import utc_now
from libs.messaging.channels import Channel
from libs.messaging.redis_streams import RedisConsumer, RedisPublisher

from agents.monitoring.alerting import (
    Alert,
    AlertSeverity,
    AlertType,
    check_daily_loss,
    check_drawdown,
    check_funding_rate,
    check_margin_utilization,
    check_opposing_positions,
)
from agents.monitoring.config import load_monitoring_config
from agents.monitoring.fee_report import DualFeeTracker
from agents.monitoring.funding_report import DualFundingReporter
from agents.monitoring.health_checker import HealthChecker
from agents.monitoring.performance_tracker import DualPerformanceTracker, PerformanceTracker

logger = setup_logging("monitoring", json_output=False)

HEARTBEAT_LOG_INTERVAL = 60  # seconds
PERFORMANCE_LOG_INTERVAL = 300  # 5 minutes


# ---------------------------------------------------------------------------
# Deserialization helpers — consume upstream agent formats
# ---------------------------------------------------------------------------


def deserialize_portfolio_snapshot(payload: dict[str, Any]) -> PortfolioSnapshot:
    """Reconstruct a PortfolioSnapshot from stream:portfolio_state payload.

    Matches the format produced by reconciliation agent's
    portfolio_snapshot_to_dict().
    """
    return PortfolioSnapshot(
        timestamp=datetime.fromisoformat(payload["timestamp"]),
        route=Route(payload["route"]),
        equity_usdc=Decimal(payload["equity_usdc"]),
        used_margin_usdc=Decimal(payload["used_margin_usdc"]),
        available_margin_usdc=Decimal(payload["available_margin_usdc"]),
        margin_utilization_pct=float(payload["margin_utilization_pct"]),
        positions=[],
        unrealized_pnl_usdc=Decimal(payload["unrealized_pnl_usdc"]),
        realized_pnl_today_usdc=Decimal(payload["realized_pnl_today_usdc"]),
        funding_pnl_today_usdc=Decimal(payload["funding_pnl_today_usdc"]),
        fees_paid_today_usdc=Decimal(payload["fees_paid_today_usdc"]),
    )


def deserialize_funding_payment(payload: dict[str, Any]) -> FundingPayment:
    """Reconstruct a FundingPayment from stream:funding_payments payload.

    Matches the format produced by reconciliation agent's
    funding_payment_to_dict().
    """
    return FundingPayment(
        timestamp=datetime.fromisoformat(payload["timestamp"]),
        instrument=payload["instrument"],
        route=Route(payload["route"]),
        rate=Decimal(payload["rate"]),
        payment_usdc=Decimal(payload["payment_usdc"]),
        position_size=Decimal(payload["position_size"]),
        position_side=PositionSide(payload["position_side"]),
        cumulative_24h_usdc=Decimal(payload["cumulative_24h_usdc"]),
    )


def deserialize_fill(payload: dict[str, Any]) -> Fill:
    """Reconstruct a Fill from stream:exchange_events payload.

    Matches the format produced by execution agent's fill_to_dict().
    """
    return Fill(
        fill_id=payload["fill_id"],
        order_id=payload["order_id"],
        route=Route(payload["route"]),
        instrument=payload["instrument"],
        side=OrderSide(payload["side"]),
        size=Decimal(payload["size"]),
        price=Decimal(payload["price"]),
        fee_usdc=Decimal(payload["fee_usdc"]),
        is_maker=payload["is_maker"] == "True"
        if isinstance(payload["is_maker"], str)
        else bool(payload["is_maker"]),
        filled_at=datetime.fromisoformat(payload["filled_at"]),
        trade_id=payload["trade_id"],
    )


# ---------------------------------------------------------------------------
# Alert serialization — for publishing to stream:alerts
# ---------------------------------------------------------------------------


def alert_to_dict(alert: Alert) -> dict[str, Any]:
    """Serialize an Alert for publishing to stream:alerts."""
    return {
        "alert_type": alert.alert_type.value,
        "severity": alert.severity.value,
        "route": alert.route.value if alert.route else "",
        "message": alert.message,
        "timestamp": alert.timestamp.isoformat(),
        "value": str(alert.value) if alert.value is not None else "",
        "threshold": str(alert.threshold) if alert.threshold is not None else "",
    }


def deserialize_alert(payload: dict[str, Any]) -> Alert:
    """Reconstruct an Alert from stream:alerts payload."""
    pt_raw = payload.get("route", "")
    route = Route(pt_raw) if pt_raw else None
    value_raw = payload.get("value", "")
    threshold_raw = payload.get("threshold", "")

    return Alert(
        alert_type=AlertType(payload["alert_type"]),
        severity=AlertSeverity(payload["severity"]),
        route=route,
        message=payload["message"],
        timestamp=datetime.fromisoformat(payload["timestamp"]),
        value=float(value_raw) if value_raw else None,
        threshold=float(threshold_raw) if threshold_raw else None,
    )


# ---------------------------------------------------------------------------
# Agent main loop
# ---------------------------------------------------------------------------


async def run_agent() -> None:
    """Main event loop for the monitoring agent.

    Runs three concurrent tasks:
    1. event_consumer — processes portfolio state, funding, and fill events
    2. heartbeat_reporter — periodic health checks and status logging
    3. performance_reporter — periodic performance summary logging
    """
    settings = get_settings()
    config = load_yaml_config("default")
    mon_config = load_monitoring_config(config)

    consumer = RedisConsumer(redis_url=settings.infra.redis_url)
    publisher = RedisPublisher(redis_url=settings.infra.redis_url)

    # Initialize trackers
    perf = DualPerformanceTracker(
        tracker_a=PerformanceTracker(starting_equity_usdc=Decimal("0")),
        tracker_b=PerformanceTracker(starting_equity_usdc=Decimal("0")),
    )
    funding = DualFundingReporter()
    fees = DualFeeTracker()
    health = HealthChecker()

    # Funding settles hourly — but only when there are open positions.
    # Start as event-only (None = never stale) and arm the 90min threshold
    # after the first payment is received, so no false-stale alerts when
    # there are no open positions to charge.
    # Fills are event-driven (only on trade execution) — never flag as stale
    funding_threshold_armed: set[str] = set()
    for target in Route:
        health.set_threshold(f"funding:{target.value}", None)  # armed on first payment
        health.set_threshold(f"fills:{target.value}", None)  # event-only

    # Track latest portfolio state for alert checks
    latest_snapshot: dict[str, PortfolioSnapshot] = {}

    # Drawdown thresholds per portfolio
    drawdown_limits = {
        Route.A: float(ROUTE_A_MAX_DRAWDOWN_PCT),
        Route.B: float(ROUTE_B_MAX_DRAWDOWN_PCT),
    }
    daily_loss_limits = {
        Route.A: float(ROUTE_A_DAILY_LOSS_KILL_PCT),
        Route.B: float(ROUTE_B_MAX_DAILY_LOSS_PCT),
    }

    # Subscribe to all monitoring-relevant streams
    channels = [
        Channel.portfolio_state(Route.A),
        Channel.portfolio_state(Route.B),
        Channel.funding_payments(Route.A),
        Channel.funding_payments(Route.B),
        Channel.exchange_events(Route.A),
        Channel.exchange_events(Route.B),
    ]
    await consumer.subscribe(
        channels=channels,
        group="monitoring_agent",
        consumer_name="monitoring-0",
    )

    # Channel name → Route mapping for dispatch
    state_a = Channel.portfolio_state(Route.A)
    state_b = Channel.portfolio_state(Route.B)
    fund_a = Channel.funding_payments(Route.A)
    fund_b = Channel.funding_payments(Route.B)
    events_a = Channel.exchange_events(Route.A)
    events_b = Channel.exchange_events(Route.B)

    state_channels = {state_a, state_b}
    funding_channels = {fund_a, fund_b}
    fill_channels = {events_a, events_b}

    logger.info(
        "monitoring_agent_started",
        heartbeat_interval=mon_config.heartbeat_interval_seconds,
        funding_alert_threshold=mon_config.funding_alert_threshold_pct,
        margin_alert_threshold=mon_config.margin_alert_threshold_pct,
        channels=len(channels),
    )

    # Counters
    snapshot_count = 0
    funding_count = 0
    fill_count = 0
    alert_count = 0

    # Alert deduplication: suppress identical alert_type+portfolio combos within cooldown
    ALERT_COOLDOWN_SECONDS = 300  # 5 minutes between same alert type
    _last_alert_times: dict[str, datetime] = {}

    def _alert_dedup_key(alert: Alert) -> str:
        portfolio = alert.route.value if alert.route else "global"
        return f"{alert.alert_type.value}:{portfolio}"

    async def publish_alert(alert: Alert) -> None:
        nonlocal alert_count
        key = _alert_dedup_key(alert)
        now = alert.timestamp
        last_fired = _last_alert_times.get(key)
        if last_fired and (now - last_fired).total_seconds() < ALERT_COOLDOWN_SECONDS:
            return  # Suppress duplicate alert within cooldown
        _last_alert_times[key] = now
        await publisher.publish(Channel.ALERTS, alert_to_dict(alert))
        alert_count += 1
        logger.warning(
            "alert_published",
            alert_type=alert.alert_type.value,
            severity=alert.severity.value,
            portfolio=alert.route.value if alert.route else "global",
            message=alert.message,
        )

    # -- Task: consume events from all 6 streams ----------------------------

    async def event_consumer() -> None:
        nonlocal snapshot_count, funding_count, fill_count

        async for channel, msg_id, payload in consumer.listen():
            try:
                if channel in state_channels:
                    snap = deserialize_portfolio_snapshot(payload)
                    target = snap.route
                    latest_snapshot[target.value] = snap

                    # Record heartbeat
                    health.record_heartbeat(f"portfolio_state:{target.value}", snap.timestamp)

                    # Update performance tracker
                    tracker = (
                        perf.tracker_a if target == Route.A else perf.tracker_b
                    )
                    # Initialize starting equity on first snapshot
                    if tracker.starting_equity_usdc == Decimal("0") and snap.equity_usdc > 0:
                        tracker.starting_equity_usdc = snap.equity_usdc
                    tracker.record_equity(snap.equity_usdc, snap.timestamp)

                    snapshot_count += 1

                    # -- Alert checks on every portfolio update --
                    now = utc_now()

                    # Margin utilization
                    margin_alert = check_margin_utilization(
                        snap.margin_utilization_pct,
                        mon_config.margin_alert_threshold_pct,
                        target,
                        now,
                    )
                    if margin_alert:
                        await publish_alert(margin_alert)

                    # Drawdown
                    summary = tracker.summary()
                    dd_alert = check_drawdown(
                        summary.current_drawdown_pct,
                        drawdown_limits[target],
                        target,
                        now,
                    )
                    if dd_alert:
                        await publish_alert(dd_alert)

                    # Daily loss (net P&L includes realized + unrealized + funding - fees)
                    if tracker.starting_equity_usdc > 0:
                        daily_loss_pct = float(
                            abs(min(Decimal("0"), snap.net_pnl_today_usdc))
                            / tracker.starting_equity_usdc
                            * 100
                        )
                        loss_alert = check_daily_loss(
                            daily_loss_pct,
                            daily_loss_limits[target],
                            target,
                            now,
                        )
                        if loss_alert:
                            await publish_alert(loss_alert)

                    # Opposing positions check
                    snap_a = latest_snapshot.get(Route.A.value)
                    snap_b = latest_snapshot.get(Route.B.value)
                    if snap_a and snap_b:
                        # Use position count as proxy — full position data would need
                        # deserialization of positions array which we skip in the snapshot
                        pass  # Opposing position alerts require position side data
                        # which is not included in the simplified snapshot payload

                    if snapshot_count % 100 == 0:
                        logger.info(
                            "monitoring_snapshot_progress",
                            snapshots=snapshot_count,
                            target=target.value,
                            equity=str(snap.equity_usdc),
                            margin_pct=snap.margin_utilization_pct,
                        )

                elif channel in funding_channels:
                    payment = deserialize_funding_payment(payload)
                    target = payment.route
                    comp_key = f"funding:{target.value}"
                    # Arm the 90min staleness threshold on first payment received
                    if comp_key not in funding_threshold_armed:
                        health.set_threshold(comp_key, timedelta(minutes=90))
                        funding_threshold_armed.add(comp_key)
                    health.record_heartbeat(comp_key, payment.timestamp)

                    reporter = funding.get_reporter(target)
                    reporter.record_payment(payment)
                    funding_count += 1

                    # Alert on high funding rate
                    now = utc_now()
                    rate_alert = check_funding_rate(
                        payment.rate,
                        mon_config.funding_alert_threshold_pct,
                        target,
                        now,
                    )
                    if rate_alert:
                        await publish_alert(rate_alert)

                    logger.info(
                        "funding_payment_recorded",
                        portfolio=target.value,
                        rate=str(payment.rate),
                        payment_usdc=str(payment.payment_usdc),
                        cumulative_24h=str(payment.cumulative_24h_usdc),
                        total_recorded=reporter.payment_count,
                    )

                elif channel in fill_channels:
                    fill = deserialize_fill(payload)
                    target = fill.route
                    health.record_heartbeat(f"fills:{target.value}", fill.filled_at)

                    fee_tracker = fees.get_tracker(target)
                    fee_tracker.record_fill(fill)
                    fill_count += 1

                    logger.info(
                        "fill_recorded",
                        portfolio=target.value,
                        order_id=fill.order_id[:8],
                        side=fill.side.value,
                        size=str(fill.size),
                        price=str(fill.price),
                        fee=str(fill.fee_usdc),
                        is_maker=fill.is_maker,
                        total_fills=fee_tracker.fill_count,
                    )

            except (KeyError, ValueError) as e:
                logger.warning(
                    "deserialize_error",
                    channel=channel,
                    error=str(e),
                )
            except Exception as e:
                logger.error(
                    "event_processing_error",
                    channel=channel,
                    msg_id=msg_id,
                    error=str(e),
                    exc_type=type(e).__name__,
                )

            await consumer.ack(channel, "monitoring_agent", msg_id)

    # -- Task: periodic heartbeat and health checks -------------------------

    async def heartbeat_reporter() -> None:
        while True:
            await asyncio.sleep(mon_config.heartbeat_interval_seconds)
            try:
                now = utc_now()
                sys_health = health.check_all(now)

                logger.info(
                    "heartbeat",
                    is_healthy=sys_health.is_healthy,
                    components=len(sys_health.components),
                    unhealthy=sys_health.unhealthy_count,
                    snapshots=snapshot_count,
                    funding_events=funding_count,
                    fills=fill_count,
                    alerts=alert_count,
                )

                # Publish alerts for unhealthy components
                for comp in sys_health.components:
                    if not comp.is_healthy:
                        alert = Alert(
                            alert_type=AlertType.COMPONENT_DOWN,
                            severity=AlertSeverity.WARNING,
                            route=None,
                            message=f"Component {comp.name}: {comp.detail}",
                            timestamp=now,
                            value=comp.stale_seconds,
                            threshold=float(health.stale_threshold.total_seconds()),
                        )
                        await publish_alert(alert)
            except Exception as e:
                logger.error(
                    "heartbeat_error",
                    error=str(e),
                    exc_type=type(e).__name__,
                )

    # -- Task: periodic performance summary ---------------------------------

    async def performance_reporter() -> None:
        while True:
            await asyncio.sleep(PERFORMANCE_LOG_INTERVAL)
            try:
                await _log_performance()
            except Exception as e:
                logger.error(
                    "performance_reporter_error",
                    error=str(e),
                    exc_type=type(e).__name__,
                )

    async def _log_performance() -> None:
        # Route A summary
        summary_a = perf.tracker_a.summary()
        if summary_a.sample_count > 0:
            logger.info(
                "performance_summary",
                portfolio="A",
                return_pct=f"{summary_a.total_return_pct:.2f}",
                max_drawdown_pct=f"{summary_a.max_drawdown_pct:.2f}",
                current_drawdown_pct=f"{summary_a.current_drawdown_pct:.2f}",
                sharpe=f"{summary_a.sharpe_ratio:.2f}" if summary_a.sharpe_ratio else "N/A",
                wins=summary_a.win_count,
                losses=summary_a.loss_count,
                win_rate=f"{summary_a.win_rate:.1f}",
                equity_samples=summary_a.sample_count,
            )

        # Route B summary
        summary_b = perf.tracker_b.summary()
        if summary_b.sample_count > 0:
            logger.info(
                "performance_summary",
                portfolio="B",
                return_pct=f"{summary_b.total_return_pct:.2f}",
                max_drawdown_pct=f"{summary_b.max_drawdown_pct:.2f}",
                current_drawdown_pct=f"{summary_b.current_drawdown_pct:.2f}",
                sharpe=f"{summary_b.sharpe_ratio:.2f}" if summary_b.sharpe_ratio else "N/A",
                wins=summary_b.win_count,
                losses=summary_b.loss_count,
                win_rate=f"{summary_b.win_rate:.1f}",
                equity_samples=summary_b.sample_count,
            )

        # Fee summary
        fee_a = fees.tracker_a.daily_summary()
        fee_b = fees.tracker_b.daily_summary()
        if fee_a.fill_count > 0 or fee_b.fill_count > 0:
            logger.info(
                "fee_summary_24h",
                total_fees=str(fee_a.total_fees_usdc + fee_b.total_fees_usdc),
                a_fees=str(fee_a.total_fees_usdc),
                b_fees=str(fee_b.total_fees_usdc),
                a_maker_ratio=f"{fee_a.maker_ratio:.0%}",
                b_maker_ratio=f"{fee_b.maker_ratio:.0%}",
                a_savings=str(fee_a.estimated_savings_usdc),
                b_savings=str(fee_b.estimated_savings_usdc),
            )

        # Funding summary
        fund_a = funding.reporter_a.daily_summary()
        fund_b = funding.reporter_b.daily_summary()
        if fund_a.payment_count > 0 or fund_b.payment_count > 0:
            logger.info(
                "funding_summary_24h",
                total_funding=str(fund_a.total_usdc + fund_b.total_usdc),
                a_funding=str(fund_a.total_usdc),
                b_funding=str(fund_b.total_usdc),
                a_payments=fund_a.payment_count,
                b_payments=fund_b.payment_count,
            )

    # -- Run all tasks concurrently -----------------------------------------

    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(event_consumer())
            tg.create_task(heartbeat_reporter())
            tg.create_task(performance_reporter())
    finally:
        await consumer.close()
        await publisher.close()
        logger.info(
            "monitoring_agent_stopped",
            snapshots_processed=snapshot_count,
            funding_events=funding_count,
            fills_processed=fill_count,
            alerts_published=alert_count,
        )


def main() -> None:
    """CLI entrypoint."""
    try:
        import uvloop
        uvloop.install()
    except ImportError:
        pass

    try:
        asyncio.run(run_agent())
    except KeyboardInterrupt:
        logger.info("monitoring_interrupted")
        sys.exit(0)


if __name__ == "__main__":
    main()
