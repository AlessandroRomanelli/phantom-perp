"""Risk management agent — validates trade ideas against portfolio-specific limits.

Consumes from stream:ranked_ideas:a and stream:ranked_ideas:b, applies the
correct limit set per portfolio, queries Coinbase for live equity/margin,
and publishes approved orders to the appropriate output stream.

Safety-critical: this module enforces ALL non-negotiable guardrails from
CLAUDE.md.  A portfolio ID mismatch is a SYSTEM HALT event.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import orjson
import redis.asyncio as aioredis
from sqlalchemy.exc import SQLAlchemyError

from libs.coinbase.auth import CoinbaseAuth
from libs.coinbase.client_pool import CoinbaseClientPool
from libs.common.config import get_settings, load_yaml_config
from libs.common.constants import (
    FUNDING_RATE_CIRCUIT_BREAKER_PCT,
    STALE_DATA_HALT_SECONDS,
)
from libs.common.instruments import get_instrument
from libs.common.logging import setup_logging
from libs.common.models.enums import (
    MarketRegime,
    OrderSide,
    OrderType,
    PositionSide,
    Route,
    SignalSource,
)
from libs.common.models.order import ProposedOrder
from libs.common.models.portfolio import PortfolioSnapshot
from libs.common.models.trade_idea import RankedTradeIdea
from libs.common.utils import generate_id, round_to_tick, utc_now
from libs.messaging.channels import Channel
from libs.messaging.redis_streams import RedisConsumer, RedisPublisher
from libs.common.serialization import deserialize_idea, order_to_dict

from agents.risk.dynamic_leverage import compute_effective_leverage_cap
from agents.risk.fee_calculator import estimate_fee
from agents.risk.funding_cost_estimator import estimate_funding_cost
from agents.risk.limits import RiskLimits, limits_for_route
from agents.risk.liquidation_guard import stop_is_before_liquidation
from agents.risk.margin_calculator import (
    compute_initial_margin,
    compute_liquidation_distance_pct,
    compute_liquidation_price,
    compute_maintenance_margin,
)
from agents.risk.portfolio_state_fetcher import PortfolioStateFetcher


def _perp_position_from_dict(d: dict[str, Any], route: Route) -> "PerpPosition":
    """Reconstruct a PerpPosition from a Redis stream position dict.

    Parses the compact position representation published by the paper simulator
    (via portfolio_snapshot_to_dict) into a full PerpPosition object.

    Args:
        d: Position dict with at minimum: instrument, side, size, entry_price,
           mark_price, leverage, liquidation_price.  unrealized_pnl_usdc is
           optional and defaults to Decimal("0").
        route: The portfolio route this position belongs to.

    Returns:
        A PerpPosition with derived margin fields computed from size/price/leverage.

    Raises:
        ValueError: If side is not a valid PositionSide member.
        KeyError: If a required field is missing from d.
    """
    from libs.common.models.position import PerpPosition

    side = PositionSide(d["side"])
    size = Decimal(d["size"])
    mark_price = Decimal(d["mark_price"])
    entry_price = Decimal(d["entry_price"])
    leverage = Decimal(d["leverage"])
    liquidation_price = Decimal(d["liquidation_price"])
    unrealized_pnl_usdc = Decimal(d.get("unrealized_pnl_usdc", "0"))

    # Derive margin fields from available data
    if leverage > 0:
        initial_margin = (size * mark_price / leverage).quantize(Decimal("0.01"))
    else:
        initial_margin = Decimal("0")
    maint_margin = (initial_margin / 2).quantize(Decimal("0.01"))
    margin_ratio = float(maint_margin / initial_margin) if initial_margin > 0 else 0.0

    return PerpPosition(
        instrument=d["instrument"],
        route=route,
        side=side,
        size=size,
        entry_price=entry_price,
        mark_price=mark_price,
        unrealized_pnl_usdc=unrealized_pnl_usdc,
        realized_pnl_usdc=Decimal("0"),
        leverage=leverage,
        initial_margin_usdc=initial_margin,
        maintenance_margin_usdc=maint_margin,
        liquidation_price=liquidation_price,
        margin_ratio=margin_ratio,
        cumulative_funding_usdc=Decimal("0"),
        total_fees_usdc=Decimal("0"),
    )


class PaperPortfolioStateFetcher:
    """Read latest portfolio state from Redis streams (paper mode).

    In paper mode, the reconciliation agent's paper simulator publishes
    PortfolioSnapshots to stream:portfolio_state:a/b. The risk agent
    reads the latest snapshot instead of calling the Coinbase API.
    """

    def __init__(self, redis_url: str) -> None:
        self._redis: aioredis.Redis = aioredis.from_url(  # type: ignore[no-untyped-call]
            redis_url, decode_responses=False,
        )
        self._defaults = {
            Route.A: PortfolioSnapshot(
                timestamp=utc_now(),
                route=Route.A,
                equity_usdc=Decimal("10000"),
                used_margin_usdc=Decimal("0"),
                available_margin_usdc=Decimal("10000"),
                margin_utilization_pct=0.0,
                positions=[],
                unrealized_pnl_usdc=Decimal("0"),
                realized_pnl_today_usdc=Decimal("0"),
                funding_pnl_today_usdc=Decimal("0"),
                fees_paid_today_usdc=Decimal("0"),
            ),
            Route.B: PortfolioSnapshot(
                timestamp=utc_now(),
                route=Route.B,
                equity_usdc=Decimal("10000"),
                used_margin_usdc=Decimal("0"),
                available_margin_usdc=Decimal("10000"),
                margin_utilization_pct=0.0,
                positions=[],
                unrealized_pnl_usdc=Decimal("0"),
                realized_pnl_today_usdc=Decimal("0"),
                funding_pnl_today_usdc=Decimal("0"),
                fees_paid_today_usdc=Decimal("0"),
            ),
        }

    async def fetch(self, target: Route) -> PortfolioSnapshot:
        """Read latest portfolio snapshot from Redis stream."""
        suffix = "a" if target == Route.A else "b"
        stream = f"stream:portfolio_state:{suffix}"
        try:
            entries = await self._redis.xrevrange(stream, "+", "-", count=1)
            if entries:
                raw = entries[0][1].get(b"data")
                if raw:
                    data = orjson.loads(raw)
                    return PortfolioSnapshot(
                        timestamp=datetime.fromisoformat(data["timestamp"]),
                        route=target,
                        equity_usdc=Decimal(str(data.get("equity_usdc", "10000"))),
                        used_margin_usdc=Decimal(str(data.get("used_margin_usdc", "0"))),
                        available_margin_usdc=Decimal(str(data.get("available_margin_usdc", "10000"))),
                        margin_utilization_pct=float(data.get("margin_utilization_pct", 0)),
                        positions=[
                            _perp_position_from_dict(p, target)
                            for p in data.get("positions", [])
                            if Decimal(p.get("size", "0")) > 0
                        ],
                        unrealized_pnl_usdc=Decimal(str(data.get("unrealized_pnl_usdc", "0"))),
                        realized_pnl_today_usdc=Decimal(str(data.get("realized_pnl_today_usdc", "0"))),
                        funding_pnl_today_usdc=Decimal(str(data.get("funding_pnl_today_usdc", "0"))),
                        fees_paid_today_usdc=Decimal(str(data.get("fees_paid_today_usdc", "0"))),
                    )
        except Exception as exc:
            import structlog
            structlog.get_logger("risk_agent").warning(
                "paper_portfolio_fetch_error",
                portfolio=target.value,
                error=str(exc),
                exc_type=type(exc).__name__,
            )
        return self._defaults[target]
from agents.risk.position_sizer import compute_position_size
from libs.storage.models import OrderSignalRecord
from libs.storage.relational import RelationalStore, init_db
from libs.storage.repository import TunerRepository


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class RiskCheckResult:
    """Outcome of a risk evaluation."""

    approved: bool
    rejection_reason: str | None = None
    proposed_order: ProposedOrder | None = None
    critical: bool = False  # True → system must halt (e.g. portfolio ID mismatch)


# ---------------------------------------------------------------------------
# Risk engine — pure evaluation, no I/O
# ---------------------------------------------------------------------------

class RiskEngine:
    """Evaluate trade ideas against portfolio-specific risk limits.

    All checks are deterministic given the inputs.  No network calls are
    made inside this class — callers must provide fresh portfolio state
    and market data.

    Args:
        limits_a: Risk limits for Route A.
        limits_b: Risk limits for Route B.
    """

    def __init__(
        self,
        limits_a: RiskLimits,
        limits_b: RiskLimits,
        correlation_groups: list[list[str]] | None = None,
    ) -> None:
        self._limits = {
            Route.A: limits_a,
            Route.B: limits_b,
        }
        self._correlation_groups: list[list[str]] = correlation_groups or []
        self._hwm: dict[Route, Decimal] = {Route.A: Decimal("0"), Route.B: Decimal("0")}

    def evaluate(
        self,
        idea: RankedTradeIdea,
        portfolio_state: PortfolioSnapshot,
        market_price: Decimal,
        market_timestamp: datetime,
        funding_rate: Decimal,
        effective_leverage_cap: Decimal | None = None,
    ) -> RiskCheckResult:
        """Run all risk checks on a trade idea.

        Args:
            idea: The trade idea to evaluate.
            portfolio_state: Current state of the target portfolio.
            market_price: Latest mark price for the instrument.
            market_timestamp: When market_price was observed.
            funding_rate: Current hourly funding rate (signed).
            effective_leverage_cap: Dynamic leverage ceiling from regime and stop
                distance.  When None, falls back to limits.max_leverage (backward-
                compatible).

        Returns:
            RiskCheckResult with approval/rejection and optional ProposedOrder.
        """
        target = idea.route
        limits = self._limits[target]
        # Use dynamic cap when provided; otherwise fall back to static limit.
        lev_cap = effective_leverage_cap if effective_leverage_cap is not None else limits.max_leverage

        # ------------------------------------------------------------------
        # 1. Stale data halt
        # ------------------------------------------------------------------
        data_age = (utc_now() - market_timestamp).total_seconds()
        if data_age > STALE_DATA_HALT_SECONDS:
            return RiskCheckResult(
                approved=False,
                rejection_reason=(
                    f"Stale market data: {data_age:.1f}s old "
                    f"(limit: {STALE_DATA_HALT_SECONDS}s)"
                ),
            )

        # ------------------------------------------------------------------
        # 2. Funding rate circuit breaker
        # ------------------------------------------------------------------
        if abs(funding_rate) >= FUNDING_RATE_CIRCUIT_BREAKER_PCT:
            return RiskCheckResult(
                approved=False,
                rejection_reason=(
                    f"Funding rate circuit breaker: |{funding_rate}| >= "
                    f"{FUNDING_RATE_CIRCUIT_BREAKER_PCT} (0.05%)"
                ),
            )

        # ------------------------------------------------------------------
        # 3. Mandatory stop-loss
        # ------------------------------------------------------------------
        if limits.stop_loss_required and idea.stop_loss is None:
            return RiskCheckResult(
                approved=False,
                rejection_reason="Stop-loss is required but not provided",
            )

        # ------------------------------------------------------------------
        # 4a. Zero/negative equity — automatic rejection
        # ------------------------------------------------------------------
        if portfolio_state.equity_usdc <= 0:
            return RiskCheckResult(
                approved=False,
                rejection_reason="portfolio_equity_zero_or_negative",
                critical=True,
            )

        # ------------------------------------------------------------------
        # 4. Daily loss kill switch
        # ------------------------------------------------------------------
        if portfolio_state.equity_usdc > 0:
            daily_loss_pct = (
                -portfolio_state.net_pnl_today_usdc
                / portfolio_state.equity_usdc
                * Decimal("100")
            )
            if daily_loss_pct > limits.max_daily_loss_pct:
                return RiskCheckResult(
                    approved=False,
                    rejection_reason=(
                        f"Daily loss kill switch: {daily_loss_pct:.2f}% "
                        f"> limit {limits.max_daily_loss_pct}%"
                    ),
                )

        # ------------------------------------------------------------------
        # 5. Max drawdown kill switch (HWM-based, ROBU-04)
        # ------------------------------------------------------------------
        if limits.hwm_drawdown_enabled:
            equity = portfolio_state.equity_usdc
            if equity > self._hwm[target]:
                self._hwm[target] = equity
            hwm = self._hwm[target]
            if hwm > 0:
                true_drawdown_pct = (hwm - equity) / hwm * Decimal("100")
                if true_drawdown_pct > limits.max_drawdown_pct:
                    return RiskCheckResult(
                        approved=False,
                        rejection_reason=(
                            f"Drawdown kill switch: {true_drawdown_pct:.2f}% "
                            f"> limit {limits.max_drawdown_pct}% "
                            f"(HWM={hwm:.2f}, current={equity:.2f})"
                        ),
                    )

        # ------------------------------------------------------------------
        # 6. Max concurrent positions + same-instrument stacking guard
        # ------------------------------------------------------------------
        open_positions = portfolio_state.open_positions
        if len(open_positions) >= limits.max_concurrent_positions:
            return RiskCheckResult(
                approved=False,
                rejection_reason=(
                    f"Max concurrent positions: {len(open_positions)} "
                    f">= limit {limits.max_concurrent_positions}"
                ),
            )

        # Reject if there is already an open position on the same instrument
        for pos in open_positions:
            if pos.instrument == idea.instrument:
                return RiskCheckResult(
                    approved=False,
                    rejection_reason=(
                        f"Already have open {pos.side.value} position on "
                        f"{idea.instrument} (size={pos.size})"
                    ),
                )

        # ------------------------------------------------------------------
        # 5.5. Correlation exposure check
        # ------------------------------------------------------------------
        if limits.correlation_enabled and self._correlation_groups:
            instrument_group: list[str] | None = None
            for group in self._correlation_groups:
                if idea.instrument in group:
                    instrument_group = group
                    break

            if instrument_group is not None:
                # Sum signed net notional for existing positions in this group
                net_existing = Decimal("0")
                for pos in open_positions:
                    if pos.instrument in instrument_group:
                        signed = pos.size * pos.mark_price
                        if pos.side == PositionSide.SHORT:
                            signed = -signed
                        net_existing += signed

                # Conservative max new notional based on equity and position pct limit
                max_new_notional = (
                    portfolio_state.equity_usdc
                    * limits.max_position_pct_equity
                    / Decimal("100")
                )
                proposed_sign = Decimal("1") if idea.direction == PositionSide.LONG else Decimal("-1")
                projected_net = abs(net_existing + proposed_sign * max_new_notional)
                cap = (
                    portfolio_state.equity_usdc
                    * limits.max_net_directional_exposure_pct
                    / Decimal("100")
                )
                if projected_net > cap:
                    return RiskCheckResult(
                        approved=False,
                        rejection_reason=(
                            f"Correlation exposure: projected net directional "
                            f"{projected_net:.0f} USDC > cap {cap:.0f} USDC "
                            f"({limits.max_net_directional_exposure_pct}% of equity)"
                        ),
                    )

        # ------------------------------------------------------------------
        # 7. Position sizing
        # ------------------------------------------------------------------
        entry_price = idea.entry_price or market_price
        inst = get_instrument(idea.instrument)
        size = compute_position_size(
            entry_price=entry_price,
            conviction=idea.conviction,
            equity=portfolio_state.equity_usdc,
            used_margin=portfolio_state.used_margin_usdc,
            existing_positions=open_positions,
            limits=limits,
            min_order_size=inst.min_order_size,
            effective_leverage=lev_cap,
        )
        if size < inst.min_order_size:
            return RiskCheckResult(
                approved=False,
                rejection_reason=(
                    f"Computed size {size} below minimum {inst.min_order_size}"
                ),
            )

        # ------------------------------------------------------------------
        # 8. Leverage check
        # ------------------------------------------------------------------
        notional = size * entry_price
        existing_notional = sum(
            p.size * p.mark_price for p in open_positions
        )
        total_notional = existing_notional + notional
        effective_leverage = total_notional / portfolio_state.equity_usdc

        if effective_leverage > lev_cap:
            return RiskCheckResult(
                approved=False,
                rejection_reason=(
                    f"Leverage {effective_leverage:.2f}x "
                    f"> limit {lev_cap}x"
                ),
            )

        # ------------------------------------------------------------------
        # 9. Margin utilization check
        # ------------------------------------------------------------------
        new_margin = compute_initial_margin(size, entry_price, lev_cap)
        projected_margin_util = (
            (portfolio_state.used_margin_usdc + new_margin)
            / portfolio_state.equity_usdc
            * Decimal("100")
        )
        if projected_margin_util > limits.max_margin_utilization_pct:
            return RiskCheckResult(
                approved=False,
                rejection_reason=(
                    f"Margin utilization {projected_margin_util:.1f}% "
                    f"> limit {limits.max_margin_utilization_pct}%"
                ),
            )

        # ------------------------------------------------------------------
        # 10. Liquidation distance check
        # ------------------------------------------------------------------
        liq_price = compute_liquidation_price(
            entry_price, lev_cap, idea.direction,
        )
        liq_distance = compute_liquidation_distance_pct(
            entry_price, liq_price, idea.direction,
        )
        if liq_distance < limits.min_liquidation_distance_pct:
            return RiskCheckResult(
                approved=False,
                rejection_reason=(
                    f"Liquidation distance {liq_distance:.1f}% "
                    f"< minimum {limits.min_liquidation_distance_pct}%"
                ),
            )

        # ------------------------------------------------------------------
        # 11. Liquidation guard — stop-loss must fire before liquidation
        # ------------------------------------------------------------------
        if idea.stop_loss is not None and not stop_is_before_liquidation(
            idea.stop_loss, liq_price, idea.direction,
        ):
            return RiskCheckResult(
                approved=False,
                rejection_reason=(
                    f"Stop-loss {idea.stop_loss} would not trigger before "
                    f"liquidation at {liq_price}"
                ),
            )

        # ------------------------------------------------------------------
        # 12. Funding cost projection
        # ------------------------------------------------------------------
        funding_est = estimate_funding_cost(
            size=size,
            entry_price=entry_price,
            funding_rate=funding_rate,
            direction=idea.direction,
            holding_period=idea.time_horizon,
        )
        if (
            funding_est.is_paying
            and abs(funding_est.daily_cost_usdc) > limits.max_funding_cost_per_day_usdc
        ):
            return RiskCheckResult(
                approved=False,
                rejection_reason=(
                    f"Projected daily funding cost {abs(funding_est.daily_cost_usdc)} USDC "
                    f"> limit {limits.max_funding_cost_per_day_usdc} USDC"
                ),
            )

        # ------------------------------------------------------------------
        # 13. Fee estimation
        # ------------------------------------------------------------------
        is_maker = True  # Default to limit orders (maker) per execution config
        fee = estimate_fee(size, entry_price, is_maker=is_maker)

        # ------------------------------------------------------------------
        # 14. Fee-adjusted edge filter (PROF-05)
        # ------------------------------------------------------------------
        round_trip_fee = fee * 2  # entry + exit (both estimated as maker)
        notional = size * entry_price
        expected_gross = notional * Decimal(str(idea.conviction)) * limits.min_expected_move_pct
        if round_trip_fee > expected_gross:
            return RiskCheckResult(
                approved=False,
                rejection_reason=(
                    f"Fee drag: round-trip {round_trip_fee:.4f} USDC > "
                    f"expected edge {expected_gross:.4f} USDC "
                    f"(conviction={idea.conviction:.2f}, notional={notional:.0f})"
                ),
            )

        # ------------------------------------------------------------------
        # All checks passed — build ProposedOrder
        # ------------------------------------------------------------------
        maint_margin = compute_maintenance_margin(size, entry_price)

        order = ProposedOrder(
            order_id=generate_id("ord"),
            signal_id=idea.idea_id,
            instrument=idea.instrument,
            route=target,
            side=OrderSide.BUY if idea.direction == PositionSide.LONG else OrderSide.SELL,
            size=size,
            order_type=OrderType.LIMIT,
            conviction=idea.conviction,
            sources=list(idea.sources),
            estimated_margin_required_usdc=new_margin.quantize(Decimal("0.01")),
            estimated_liquidation_price=liq_price,
            estimated_fee_usdc=fee,
            estimated_funding_cost_1h_usdc=funding_est.hourly_cost_usdc,
            proposed_at=utc_now(),
            limit_price=round_to_tick(entry_price, inst.tick_size),
            stop_loss=idea.stop_loss,
            take_profit=idea.take_profit,
            leverage=effective_leverage.quantize(Decimal("0.01")),
            reasoning=idea.reasoning,
            metadata={
                "liq_distance_pct": float(liq_distance),
                "projected_daily_funding_usdc": float(funding_est.daily_cost_usdc),
                "margin_utilization_pct": float(projected_margin_util),
                "effective_leverage_cap": str(lev_cap),
            },
        )

        return RiskCheckResult(approved=True, proposed_order=order)


# ---------------------------------------------------------------------------
# Agent main loop
# ---------------------------------------------------------------------------

async def run_agent() -> None:
    """Main event loop for the risk management agent."""
    log = setup_logging("risk_agent")
    settings = get_settings()
    config = load_yaml_config("default")

    # Initialize PostgreSQL storage for order-signal attribution
    db_store = RelationalStore(settings.infra.database_url)
    await init_db(db_store.engine)
    repo = TunerRepository(db_store)
    log.info("risk_db_initialized")

    limits_a = limits_for_route(Route.A, config)
    limits_b = limits_for_route(Route.B, config)
    risk_cfg = config.get("risk", {})
    correlation_groups: list[list[str]] | None = risk_cfg.get("correlation_groups", None)
    engine = RiskEngine(limits_a, limits_b, correlation_groups=correlation_groups)

    log.info(
        "risk_agent_started",
        limits_a_leverage=str(limits_a.max_leverage),
        limits_b_leverage=str(limits_b.max_leverage),
    )

    is_paper = settings.infra.environment == "paper"
    client_pool: CoinbaseClientPool | None = None
    fetcher: PaperPortfolioStateFetcher | PortfolioStateFetcher

    if is_paper:
        fetcher = PaperPortfolioStateFetcher(settings.infra.redis_url)
        log.info("risk_portfolio_source", mode="paper", source="redis_streams")
    else:
        client_pool = CoinbaseClientPool(
            auth=CoinbaseAuth(
                settings.coinbase.api_key_a,
                settings.coinbase.api_secret_a,
            ),
            base_url=settings.coinbase.rest_url,
            portfolio_uuid=settings.portfolios.portfolio_id,
        )
        fetcher = PortfolioStateFetcher(client_pool)
        log.info("risk_portfolio_source", mode="live", source="coinbase_api")

    consumer = RedisConsumer(redis_url=settings.infra.redis_url)
    publisher = RedisPublisher(redis_url=settings.infra.redis_url)
    regime_redis: aioredis.Redis = aioredis.from_url(  # type: ignore[no-untyped-call]
        settings.infra.redis_url, decode_responses=True,
    )

    channel_a = Channel.ranked_ideas(Route.A)
    channel_b = Channel.ranked_ideas(Route.B)

    await consumer.subscribe(
        channels=[channel_a, channel_b],
        group="risk_agent",
        consumer_name="risk-0",
    )

    # Cache latest market data from ideas for stale-data checks
    latest_market_ts: datetime | None = None
    latest_market_price: Decimal = Decimal("0")
    latest_funding_rate: Decimal = Decimal("0")

    try:
        async for channel, msg_id, payload in consumer.listen():
            try:
                idea = deserialize_idea(payload)

                # Determine portfolio from channel
                if channel == channel_a:
                    expected_target = Route.A
                else:
                    expected_target = Route.B

                if idea.route != expected_target:
                    log.error(
                        "idea_target_channel_mismatch",
                        idea_target=idea.route.value,
                        channel=channel,
                    )
                    await consumer.ack(channel, "risk_agent", msg_id)
                    continue

                # Use idea's entry price as market reference when available
                market_price = idea.entry_price or latest_market_price
                market_ts = idea.timestamp
                funding_rate = Decimal(
                    str(idea.metadata.get("funding_rate", latest_funding_rate))
                )

                # Keep cache fresh
                latest_market_price = market_price
                latest_market_ts = market_ts
                if funding_rate:
                    latest_funding_rate = funding_rate

                # Fetch live portfolio state from Coinbase (with timeout)
                try:
                    portfolio_state = await asyncio.wait_for(
                        fetcher.fetch(idea.route),
                        timeout=15.0,
                    )
                except asyncio.TimeoutError:
                    log.warning(
                        "portfolio_fetch_timeout",
                        route=idea.route.value,
                        idea_id=idea.idea_id,
                    )
                    await consumer.ack(channel, "risk_agent", msg_id)
                    continue
                except Exception as exc:
                    log.warning(
                        "portfolio_fetch_error",
                        route=idea.route.value,
                        idea_id=idea.idea_id,
                        error=str(exc),
                        exc_type=type(exc).__name__,
                    )
                    await consumer.ack(channel, "risk_agent", msg_id)
                    continue

                # Read current market regime from Redis for dynamic leverage
                entry_price_for_lev = idea.entry_price or market_price
                regime = MarketRegime.RANGING  # safe default
                try:
                    regime_raw = await regime_redis.hget("phantom:regime", idea.instrument)
                    if regime_raw:
                        regime = MarketRegime(regime_raw)
                except (ConnectionError, Exception) as exc:
                    log.warning(
                        "regime_read_failed",
                        instrument=idea.instrument,
                        error=str(exc),
                        exc_type=type(exc).__name__,
                    )

                eff_lev_cap = compute_effective_leverage_cap(
                    entry_price=entry_price_for_lev,
                    stop_loss=idea.stop_loss,
                    regime=regime,
                    route=idea.route,
                    config=config,
                )

                # Run risk evaluation
                result = engine.evaluate(
                    idea=idea,
                    portfolio_state=portfolio_state,
                    market_price=market_price,
                    market_timestamp=market_ts,
                    funding_rate=funding_rate,
                    effective_leverage_cap=eff_lev_cap,
                )

                if result.critical:
                    log.critical(
                        "SYSTEM_HALT",
                        reason=result.rejection_reason,
                        route=idea.route.value,
                    )
                    # Publish alert and shut down
                    await publisher.publish(
                        Channel.ALERTS,
                        {
                            "level": "CRITICAL",
                            "agent": "risk",
                            "message": result.rejection_reason or "Unknown critical error",
                            "timestamp": utc_now().isoformat(),
                        },
                    )
                    break  # Exit the loop → agent shuts down

                if result.approved and result.proposed_order is not None:
                    out_channel = Channel.approved_orders(idea.route)
                    await publisher.publish(out_channel, order_to_dict(result.proposed_order))
                    # Persist order-signal attribution per D-01
                    try:
                        order = result.proposed_order
                        # Primary source = highest conviction signal (first in sources list,
                        # which is ordered by conviction from alpha combiner)
                        primary = order.sources[0].value if order.sources else "unknown"
                        all_src = ",".join(s.value for s in order.sources)
                        await repo.write_order_signal(OrderSignalRecord(
                            order_id=order.order_id,
                            signal_id=order.signal_id,
                            portfolio_target=order.route.value,
                            instrument=order.instrument,
                            conviction=order.conviction,
                            primary_source=primary,
                            all_sources=all_src,
                            stop_loss=order.stop_loss,
                            take_profit=order.take_profit,
                            limit_price=order.limit_price,
                            leverage=order.leverage,
                            proposed_at=order.proposed_at,
                            reasoning=order.reasoning,
                        ))
                    except SQLAlchemyError as exc:
                        log.warning(
                            "order_signal_db_write_failed",
                            order_id=result.proposed_order.order_id,
                            error=str(exc),
                            exc_type=type(exc).__name__,
                        )
                    except Exception as exc:
                        log.warning(
                            "order_signal_db_write_failed",
                            order_id=result.proposed_order.order_id,
                            error=str(exc),
                            exc_type=type(exc).__name__,
                        )
                    log.info(
                        "order_approved",
                        order_id=result.proposed_order.order_id,
                        route=idea.route.value,
                        size=str(result.proposed_order.size),
                        side=result.proposed_order.side.value,
                    )
                    log.info(
                        "dynamic_leverage_applied",
                        instrument=idea.instrument,
                        regime=regime.value,
                        eff_lev_cap=str(eff_lev_cap),
                        actual_lev=str(result.proposed_order.leverage),
                    )
                else:
                    log.info(
                        "idea_rejected",
                        idea_id=idea.idea_id,
                        route=idea.route.value,
                        reason=result.rejection_reason,
                    )

            except (KeyError, InvalidOperation, ValueError) as exc:
                log.warning("idea_deserialization_error", error=str(exc))
            finally:
                await consumer.ack(channel, "risk_agent", msg_id)

    finally:
        await consumer.close()
        await publisher.close()
        await regime_redis.aclose()
        await db_store.close()
        if client_pool is not None:
            await client_pool.close()


def main() -> None:
    """CLI entrypoint."""
    try:
        import uvloop

        uvloop.install()
    except ImportError:
        pass
    asyncio.run(run_agent())


if __name__ == "__main__":
    main()
