"""Global kill switches — per-portfolio and system-wide circuit breakers.

Evaluates drawdown, daily loss, stale data, and funding rate thresholds
against the non-negotiable safety constants from libs/common/constants.py.
These are system-level breakers that halt entire portfolios or the whole
pipeline, distinct from the execution agent's per-order circuit breaker.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum

from libs.common.constants import (
    FUNDING_RATE_CIRCUIT_BREAKER_PCT,
    PORTFOLIO_A_DAILY_LOSS_KILL_PCT,
    PORTFOLIO_A_MAX_DRAWDOWN_PCT,
    PORTFOLIO_B_MAX_DAILY_LOSS_PCT,
    PORTFOLIO_B_MAX_DRAWDOWN_PCT,
    STALE_DATA_HALT_SECONDS,
)
from libs.common.models.enums import PortfolioTarget


class KillSwitchReason(str, Enum):
    """Reason a kill switch was triggered."""

    DAILY_LOSS = "daily_loss"
    MAX_DRAWDOWN = "max_drawdown"
    STALE_DATA = "stale_data"
    FUNDING_RATE = "funding_rate"
    EMERGENCY = "emergency"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class KillSwitchEvent:
    """Record of a kill switch activation."""

    reason: KillSwitchReason
    portfolio_target: PortfolioTarget | None  # None = global
    message: str
    triggered_at: datetime
    value: str = ""
    threshold: str = ""


@dataclass(slots=True)
class SystemCircuitBreaker:
    """System-level circuit breaker with per-portfolio and global trips.

    Portfolio A and B can be halted independently. A global halt stops
    both portfolios. Manual reset is required to resume.
    """

    _portfolio_trips: dict[PortfolioTarget, KillSwitchEvent] = field(
        default_factory=dict,
    )
    _global_trip: KillSwitchEvent | None = None

    # ── Trip methods ────────────────────────────────────────────────────

    def trip_portfolio(
        self,
        target: PortfolioTarget,
        reason: KillSwitchReason,
        message: str,
        now: datetime,
        value: str = "",
        threshold: str = "",
    ) -> KillSwitchEvent:
        """Halt a single portfolio."""
        event = KillSwitchEvent(
            reason=reason,
            portfolio_target=target,
            message=message,
            triggered_at=now,
            value=value,
            threshold=threshold,
        )
        self._portfolio_trips[target] = event
        return event

    def trip_global(
        self,
        reason: KillSwitchReason,
        message: str,
        now: datetime,
    ) -> KillSwitchEvent:
        """Halt the entire system (both portfolios)."""
        event = KillSwitchEvent(
            reason=reason,
            portfolio_target=None,
            message=message,
            triggered_at=now,
        )
        self._global_trip = event
        return event

    def emergency_kill(self, now: datetime) -> KillSwitchEvent:
        """Emergency halt — close all positions, halt everything."""
        return self.trip_global(
            KillSwitchReason.EMERGENCY,
            "Emergency kill: closing all positions and halting system",
            now,
        )

    # ── Reset methods ───────────────────────────────────────────────────

    def reset_portfolio(self, target: PortfolioTarget) -> bool:
        """Resume a single portfolio. Returns True if was halted."""
        return self._portfolio_trips.pop(target, None) is not None

    def reset_global(self) -> bool:
        """Resume the entire system. Returns True if was globally halted."""
        was_halted = self._global_trip is not None
        self._global_trip = None
        return was_halted

    def reset_all(self) -> None:
        """Clear all trips (portfolio and global)."""
        self._portfolio_trips.clear()
        self._global_trip = None

    # ── Query methods ───────────────────────────────────────────────────

    def is_halted(self, target: PortfolioTarget) -> bool:
        """Check if a portfolio is halted (own trip OR global trip)."""
        return target in self._portfolio_trips or self._global_trip is not None

    def is_globally_halted(self) -> bool:
        """Check if the entire system is halted."""
        return self._global_trip is not None

    def get_trip(self, target: PortfolioTarget) -> KillSwitchEvent | None:
        """Get the active trip for a portfolio, if any."""
        return self._portfolio_trips.get(target)

    @property
    def global_trip(self) -> KillSwitchEvent | None:
        return self._global_trip

    @property
    def active_trips(self) -> list[KillSwitchEvent]:
        """All active trips (portfolio + global)."""
        trips = list(self._portfolio_trips.values())
        if self._global_trip:
            trips.append(self._global_trip)
        return trips

    @property
    def any_halted(self) -> bool:
        return bool(self._portfolio_trips) or self._global_trip is not None


# ── Evaluation functions ────────────────────────────────────────────────
# Pure functions that check conditions against constants and return
# Optional[KillSwitchEvent] if a trip should be triggered.


def evaluate_daily_loss(
    daily_loss_pct: float,
    portfolio_target: PortfolioTarget,
    now: datetime,
) -> KillSwitchEvent | None:
    """Check if daily loss exceeds the portfolio's kill-switch threshold.

    Portfolio A: 10% (PORTFOLIO_A_DAILY_LOSS_KILL_PCT)
    Portfolio B: 5%  (PORTFOLIO_B_MAX_DAILY_LOSS_PCT)
    """
    threshold = (
        PORTFOLIO_A_DAILY_LOSS_KILL_PCT
        if portfolio_target == PortfolioTarget.A
        else PORTFOLIO_B_MAX_DAILY_LOSS_PCT
    )
    if daily_loss_pct >= float(threshold):
        return KillSwitchEvent(
            reason=KillSwitchReason.DAILY_LOSS,
            portfolio_target=portfolio_target,
            message=(
                f"Daily loss {daily_loss_pct:.1f}% exceeds "
                f"kill switch {threshold}% for {portfolio_target.value}"
            ),
            triggered_at=now,
            value=f"{daily_loss_pct:.1f}",
            threshold=str(threshold),
        )
    return None


def evaluate_drawdown(
    drawdown_pct: float,
    portfolio_target: PortfolioTarget,
    now: datetime,
) -> KillSwitchEvent | None:
    """Check if drawdown exceeds the portfolio's kill-switch threshold.

    Portfolio A: 25% (PORTFOLIO_A_MAX_DRAWDOWN_PCT)
    Portfolio B: 15% (PORTFOLIO_B_MAX_DRAWDOWN_PCT)
    """
    threshold = (
        PORTFOLIO_A_MAX_DRAWDOWN_PCT
        if portfolio_target == PortfolioTarget.A
        else PORTFOLIO_B_MAX_DRAWDOWN_PCT
    )
    if drawdown_pct >= float(threshold):
        return KillSwitchEvent(
            reason=KillSwitchReason.MAX_DRAWDOWN,
            portfolio_target=portfolio_target,
            message=(
                f"Drawdown {drawdown_pct:.1f}% exceeds "
                f"kill switch {threshold}% for {portfolio_target.value}"
            ),
            triggered_at=now,
            value=f"{drawdown_pct:.1f}",
            threshold=str(threshold),
        )
    return None


def evaluate_stale_data(
    last_data_age_seconds: float,
    now: datetime,
) -> KillSwitchEvent | None:
    """Check if market data is too stale (> 30 seconds).

    This is a GLOBAL halt — both portfolios stop.
    """
    if last_data_age_seconds > STALE_DATA_HALT_SECONDS:
        return KillSwitchEvent(
            reason=KillSwitchReason.STALE_DATA,
            portfolio_target=None,
            message=(
                f"Market data {last_data_age_seconds:.0f}s old "
                f"(limit: {STALE_DATA_HALT_SECONDS}s). Trading halted."
            ),
            triggered_at=now,
            value=f"{last_data_age_seconds:.0f}",
            threshold=str(STALE_DATA_HALT_SECONDS),
        )
    return None


def evaluate_funding_rate(
    rate: Decimal,
    now: datetime,
) -> KillSwitchEvent | None:
    """Check if funding rate exceeds the circuit breaker threshold.

    When |rate| >= 0.05% (0.0005), new position-opening trades pause
    for BOTH portfolios until the rate normalizes.
    """
    if abs(rate) >= FUNDING_RATE_CIRCUIT_BREAKER_PCT:
        direction = "positive (longs pay)" if rate > 0 else "negative (shorts pay)"
        return KillSwitchEvent(
            reason=KillSwitchReason.FUNDING_RATE,
            portfolio_target=None,
            message=(
                f"Funding rate {direction}: {float(abs(rate)) * 100:.4f}% "
                f"exceeds circuit breaker {float(FUNDING_RATE_CIRCUIT_BREAKER_PCT) * 100:.2f}%"
            ),
            triggered_at=now,
            value=str(rate),
            threshold=str(FUNDING_RATE_CIRCUIT_BREAKER_PCT),
        )
    return None
