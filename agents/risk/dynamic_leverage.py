"""Pure functions for regime-aware dynamic leverage computation.

No I/O, no Redis — all inputs are plain Python values so callers can test and
compose these helpers without side effects.
"""

from decimal import Decimal

from libs.common.constants import MAX_LEVERAGE_GLOBAL, MAX_LEVERAGE_ROUTE_B
from libs.common.models.enums import MarketRegime, Route

# Safe defaults used when a regime key is absent from the config.
_DEFAULT_REGIME_CAP: dict[Route, Decimal] = {
    Route.A: Decimal("3.0"),
    Route.B: Decimal("2.0"),
}

_HARD_CAP: dict[Route, Decimal] = {
    Route.A: MAX_LEVERAGE_GLOBAL,
    Route.B: MAX_LEVERAGE_ROUTE_B,
}


def get_regime_leverage_cap(
    regime: MarketRegime,
    route: Route,
    config: dict,
) -> Decimal:
    """Return the leverage ceiling for *regime* and *route* from *config*.

    Reads ``config["risk"]["regime_leverage"][route_key][regime.value]``
    where *route_key* is ``"route_a"`` or ``"route_b"``.

    Falls back to ``3.0`` (Route A) / ``2.0`` (Route B) when the key is
    missing.  The returned value is always ``≤ hard_cap`` (``MAX_LEVERAGE_GLOBAL``
    for Route A, ``MAX_LEVERAGE_ROUTE_B`` for Route B).
    """
    route_key = "route_a" if route is Route.A else "route_b"
    hard_cap = _HARD_CAP[route]
    default_cap = _DEFAULT_REGIME_CAP[route]

    try:
        raw = config["risk"]["regime_leverage"][route_key][regime.value]
        cfg_cap = Decimal(str(raw))
    except (KeyError, TypeError, ValueError):
        cfg_cap = default_cap

    return min(cfg_cap, hard_cap)


def compute_stop_distance_leverage(
    entry_price: Decimal,
    stop_loss: Decimal | None,
    regime_cap: Decimal,
    risk_budget_pct: Decimal = Decimal("0.02"),
) -> Decimal:
    """Return the leverage implied by the stop distance, clamped to [1, regime_cap].

    Formula: ``leverage = risk_budget_pct / stop_distance_fraction``
    where ``stop_distance_fraction = |entry - stop| / entry``.

    * If *stop_loss* is ``None``, returns *regime_cap* unchanged.
    * If the stop distance is zero (or entry_price is zero), returns *regime_cap*
      to avoid division by zero.
    * Result is clamped to ``[Decimal("1.0"), regime_cap]``.
    """
    if stop_loss is None:
        return regime_cap

    if entry_price == Decimal("0"):
        return regime_cap

    stop_distance_fraction = abs(entry_price - stop_loss) / entry_price

    if stop_distance_fraction == Decimal("0"):
        return regime_cap

    leverage = risk_budget_pct / stop_distance_fraction
    return max(Decimal("1.0"), min(leverage, regime_cap))


def compute_effective_leverage_cap(
    entry_price: Decimal,
    stop_loss: Decimal | None,
    regime: MarketRegime,
    route: Route,
    config: dict,
) -> Decimal:
    """Return the final effective leverage cap for a proposed trade.

    Combines regime ceiling and stop-distance-implied leverage:

    1. Compute regime cap via :func:`get_regime_leverage_cap`.
    2. Compute stop-distance leverage via :func:`compute_stop_distance_leverage`.
    3. Enforce hard cap as a final safety guard.

    The result is always ``≤ hard_cap`` for the route.
    """
    regime_cap = get_regime_leverage_cap(regime, route, config)
    result = compute_stop_distance_leverage(entry_price, stop_loss, regime_cap)
    hard_cap = _HARD_CAP[route]
    return min(result, hard_cap)
