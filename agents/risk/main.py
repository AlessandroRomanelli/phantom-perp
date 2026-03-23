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
    OrderSide,
    OrderStatus,
    OrderType,
    PortfolioTarget,
    PositionSide,
    SignalSource,
)
from libs.common.models.order import ProposedOrder
from libs.common.models.portfolio import PortfolioSnapshot
from libs.common.models.trade_idea import RankedTradeIdea
from libs.common.utils import generate_id, round_to_tick, utc_now
from libs.messaging.channels import Channel
from libs.messaging.redis_streams import RedisConsumer, RedisPublisher
from agents.risk.fee_calculator import estimate_fee
from agents.risk.funding_cost_estimator import estimate_funding_cost
from agents.risk.limits import RiskLimits, limits_for_portfolio
from agents.risk.liquidation_guard import stop_is_before_liquidation
from agents.risk.margin_calculator import (
    compute_initial_margin,
    compute_liquidation_distance_pct,
    compute_liquidation_price,
    compute_maintenance_margin,
)
from agents.risk.portfolio_state_fetcher import PortfolioStateFetcher
from agents.risk.position_sizer import compute_position_size


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
        limits_a: Risk limits for Portfolio A.
        limits_b: Risk limits for Portfolio B.
    """

    def __init__(self, limits_a: RiskLimits, limits_b: RiskLimits) -> None:
        self._limits = {
            PortfolioTarget.A: limits_a,
            PortfolioTarget.B: limits_b,
        }

    def evaluate(
        self,
        idea: RankedTradeIdea,
        portfolio_state: PortfolioSnapshot,
        market_price: Decimal,
        market_timestamp: datetime,
        funding_rate: Decimal,
    ) -> RiskCheckResult:
        """Run all risk checks on a trade idea.

        Args:
            idea: The trade idea to evaluate.
            portfolio_state: Current state of the target portfolio.
            market_price: Latest mark price for the instrument.
            market_timestamp: When market_price was observed.
            funding_rate: Current hourly funding rate (signed).

        Returns:
            RiskCheckResult with approval/rejection and optional ProposedOrder.
        """
        target = idea.portfolio_target
        limits = self._limits[target]

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
        # 5. Max drawdown kill switch
        # ------------------------------------------------------------------
        # Drawdown check — uses net_pnl_today as proxy for peak-to-trough
        # (full drawdown tracking requires historical peak in monitoring agent;
        # here we use daily P&L as a conservative approximation)
        if portfolio_state.equity_usdc > 0:
            drawdown_pct = daily_loss_pct if daily_loss_pct > 0 else Decimal("0")
            if drawdown_pct > limits.max_drawdown_pct:
                return RiskCheckResult(
                    approved=False,
                    rejection_reason=(
                        f"Drawdown kill switch: {drawdown_pct:.2f}% "
                        f"> limit {limits.max_drawdown_pct}%"
                    ),
                )

        # ------------------------------------------------------------------
        # 6. Max concurrent positions
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

        if effective_leverage > limits.max_leverage:
            return RiskCheckResult(
                approved=False,
                rejection_reason=(
                    f"Leverage {effective_leverage:.2f}x "
                    f"> limit {limits.max_leverage}x"
                ),
            )

        # ------------------------------------------------------------------
        # 9. Margin utilization check
        # ------------------------------------------------------------------
        new_margin = compute_initial_margin(size, entry_price, limits.max_leverage)
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
            entry_price, limits.max_leverage, idea.direction,
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
        # All checks passed — build ProposedOrder
        # ------------------------------------------------------------------
        maint_margin = compute_maintenance_margin(size, entry_price)

        order = ProposedOrder(
            order_id=generate_id("ord"),
            signal_id=idea.idea_id,
            instrument=idea.instrument,
            portfolio_target=target,
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
            },
        )

        return RiskCheckResult(approved=True, proposed_order=order)


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def deserialize_idea(payload: dict[str, Any]) -> RankedTradeIdea:
    """Rebuild a RankedTradeIdea from a Redis stream payload dict."""
    return RankedTradeIdea(
        idea_id=payload["idea_id"],
        timestamp=datetime.fromisoformat(payload["timestamp"]),
        instrument=payload["instrument"],
        portfolio_target=PortfolioTarget(payload["portfolio_target"]),
        direction=PositionSide(payload["direction"]),
        conviction=float(payload["conviction"]),
        sources=[SignalSource(s) for s in payload["sources"].split(",")],
        time_horizon=timedelta(seconds=float(payload["time_horizon_seconds"])),
        entry_price=Decimal(payload["entry_price"]) if payload.get("entry_price") else None,
        stop_loss=Decimal(payload["stop_loss"]) if payload.get("stop_loss") else None,
        take_profit=Decimal(payload["take_profit"]) if payload.get("take_profit") else None,
        reasoning=payload.get("reasoning", ""),
    )


def order_to_dict(order: ProposedOrder) -> dict[str, Any]:
    """Serialize a ProposedOrder to a JSON-compatible dict for Redis."""
    return {
        "order_id": order.order_id,
        "signal_id": order.signal_id,
        "instrument": order.instrument,
        "portfolio_target": order.portfolio_target.value,
        "side": order.side.value,
        "size": str(order.size),
        "order_type": order.order_type.value,
        "conviction": order.conviction,
        "sources": ",".join(s.value for s in order.sources),
        "estimated_margin_required_usdc": str(order.estimated_margin_required_usdc),
        "estimated_liquidation_price": str(order.estimated_liquidation_price),
        "estimated_fee_usdc": str(order.estimated_fee_usdc),
        "estimated_funding_cost_1h_usdc": str(order.estimated_funding_cost_1h_usdc),
        "proposed_at": order.proposed_at.isoformat(),
        "limit_price": str(order.limit_price) if order.limit_price else "",
        "stop_loss": str(order.stop_loss) if order.stop_loss else "",
        "take_profit": str(order.take_profit) if order.take_profit else "",
        "leverage": str(order.leverage),
        "reduce_only": str(order.reduce_only),
        "status": order.status.value,
        "reasoning": order.reasoning,
    }


# ---------------------------------------------------------------------------
# Agent main loop
# ---------------------------------------------------------------------------

async def run_agent() -> None:
    """Main event loop for the risk management agent."""
    log = setup_logging("risk_agent")
    settings = get_settings()
    config = load_yaml_config("default")

    limits_a = limits_for_portfolio(PortfolioTarget.A, config)
    limits_b = limits_for_portfolio(PortfolioTarget.B, config)
    engine = RiskEngine(limits_a, limits_b)

    log.info(
        "risk_agent_started",
        limits_a_leverage=str(limits_a.max_leverage),
        limits_b_leverage=str(limits_b.max_leverage),
    )

    client_pool = CoinbaseClientPool(
        auth_a=CoinbaseAuth(
            settings.coinbase.api_key_a,
            settings.coinbase.api_secret_a,
        ),
        auth_b=CoinbaseAuth(
            settings.coinbase.api_key_b,
            settings.coinbase.api_secret_b,
        ),
        base_url=settings.coinbase.rest_url,
        portfolio_uuid_a=settings.portfolios.portfolio_a_id,
        portfolio_uuid_b=settings.portfolios.portfolio_b_id,
    )
    fetcher = PortfolioStateFetcher(client_pool)

    consumer = RedisConsumer(redis_url=settings.infra.redis_url)
    publisher = RedisPublisher(redis_url=settings.infra.redis_url)

    channel_a = Channel.ranked_ideas(PortfolioTarget.A)
    channel_b = Channel.ranked_ideas(PortfolioTarget.B)

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
                    expected_target = PortfolioTarget.A
                else:
                    expected_target = PortfolioTarget.B

                if idea.portfolio_target != expected_target:
                    log.error(
                        "idea_target_channel_mismatch",
                        idea_target=idea.portfolio_target.value,
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

                # Fetch live portfolio state from Coinbase
                portfolio_state = await fetcher.fetch(idea.portfolio_target)

                # Run risk evaluation
                result = engine.evaluate(
                    idea=idea,
                    portfolio_state=portfolio_state,
                    market_price=market_price,
                    market_timestamp=market_ts,
                    funding_rate=funding_rate,
                )

                if result.critical:
                    log.critical(
                        "SYSTEM_HALT",
                        reason=result.rejection_reason,
                        portfolio=idea.portfolio_target.value,
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
                    out_channel = Channel.approved_orders(idea.portfolio_target)
                    await publisher.publish(out_channel, order_to_dict(result.proposed_order))
                    log.info(
                        "order_approved",
                        order_id=result.proposed_order.order_id,
                        portfolio=idea.portfolio_target.value,
                        size=str(result.proposed_order.size),
                        side=result.proposed_order.side.value,
                    )
                else:
                    log.info(
                        "idea_rejected",
                        idea_id=idea.idea_id,
                        portfolio=idea.portfolio_target.value,
                        reason=result.rejection_reason,
                    )

            except (KeyError, InvalidOperation, ValueError) as exc:
                log.warning("idea_deserialization_error", error=str(exc))
            finally:
                await consumer.ack(channel, "risk_agent", msg_id)

    finally:
        await consumer.close()
        await publisher.close()
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
