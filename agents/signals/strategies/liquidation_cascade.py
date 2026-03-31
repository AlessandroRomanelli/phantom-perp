"""Liquidation cascade strategy — fade or follow forced liquidation events.

Signal logic:
  1. Track rolling open interest change rate over a short window.
  2. Detect OI drop rate exceeding threshold (mass liquidation event).
  3. Classify cascade into Tier 1/2/3 based on severity (LIQ-01).
  4. Require volume surge confirmation to filter organic OI reduction (LIQ-02).
  5. Combine with volatility spike and extreme orderbook imbalance.
  6. Fade mode: after sharp OI drop + price dump -> LONG (expect bounce).
  7. Follow mode: during accelerating OI drop + extreme imbalance -> SHORT.
  8. Conviction scales with OI drop rate, volatility spike, imbalance, and tier.
  9. Tier-specific stop/TP widths: Tier 3 gets widest stops and biggest targets.
  10. Short time horizon (<=2h) -> routes to Portfolio A (autonomous).
  11. Heatmap magnet mode (optional): nearby liquidation clusters boost conviction
      and add metadata when Coinglass heatmap data is wired via set_heatmap_store().
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import numpy as np
import structlog

from libs.common.instruments import get_instrument
from libs.common.models.enums import PortfolioTarget, PositionSide, SignalSource
from libs.common.models.market_snapshot import MarketSnapshot
from libs.common.models.signal import StandardSignal
from libs.common.utils import generate_id, round_to_tick, utc_now
from libs.indicators.volatility import atr

from agents.signals.feature_store import FeatureStore
from agents.signals.strategies.base import SignalStrategy

if TYPE_CHECKING:
    from agents.signals.coinglass_poller import LiquidationCluster

_logger = structlog.get_logger(__name__)


@dataclass
class LiquidationCascadeParams:
    """Tunable parameters for the liquidation cascade strategy."""

    oi_lookback: int = 10
    imbalance_threshold: float = 0.3
    vol_spike_mult: float = 1.5
    atr_period: int = 14
    min_conviction: float = 0.55
    cooldown_bars: int = 15

    # Tier classification thresholds (LIQ-01)
    tier1_min_oi_drop_pct: float = 2.0
    tier2_min_oi_drop_pct: float = 4.0
    tier3_min_oi_drop_pct: float = 8.0

    # Tier-specific stop/TP ATR multipliers (LIQ-01)
    tier1_stop_atr_mult: float = 1.5
    tier1_tp_atr_mult: float = 2.0
    tier2_stop_atr_mult: float = 2.0
    tier2_tp_atr_mult: float = 3.0
    tier3_stop_atr_mult: float = 3.0
    tier3_tp_atr_mult: float = 4.5

    # Volume surge confirmation (LIQ-02)
    vol_lookback: int = 10
    vol_surge_min_ratio: float = 1.5

    # Heatmap magnet mode — conviction boost from Coinglass cluster proximity
    heatmap_magnet_enabled: bool = True
    cluster_min_notional_usd: float = 500_000.0
    magnet_proximity_pct: float = 3.0
    cluster_score_weight: float = 0.20
    heatmap_fallback_on_missing: bool = True

    # Portfolio A routing — require high conviction for autonomous execution
    portfolio_a_min_conviction: float = 0.85

    # Backward compatibility aliases
    @property
    def oi_drop_threshold_pct(self) -> float:
        """Alias for tier1_min_oi_drop_pct for backward compatibility."""
        return self.tier1_min_oi_drop_pct


class LiquidationCascadeStrategy(SignalStrategy):
    """Detects liquidation cascades via OI drops and fades or follows them.

    Classifies cascades into 3 tiers based on severity:
      - Tier 1 (2-4% OI drop): tighter stops, smaller targets
      - Tier 2 (4-8% OI drop): moderate stops and targets
      - Tier 3 (>8% OI drop): widest stops, biggest targets, conviction boost

    Requires volume surge confirmation to filter organic OI reduction.

    Args:
        params: Strategy parameters. Uses defaults if None.
        config: YAML config dict override.
    """

    def __init__(
        self,
        params: LiquidationCascadeParams | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self._params = params or LiquidationCascadeParams()

        if config:
            p = config.get("parameters", {})
            self._params = LiquidationCascadeParams(
                oi_lookback=p.get("oi_lookback", self._params.oi_lookback),
                imbalance_threshold=p.get(
                    "imbalance_threshold", self._params.imbalance_threshold,
                ),
                vol_spike_mult=p.get("vol_spike_mult", self._params.vol_spike_mult),
                atr_period=p.get("atr_period", self._params.atr_period),
                min_conviction=p.get("min_conviction", self._params.min_conviction),
                cooldown_bars=p.get("cooldown_bars", self._params.cooldown_bars),
                # Tier thresholds
                tier1_min_oi_drop_pct=p.get(
                    "tier1_min_oi_drop_pct",
                    p.get("oi_drop_threshold_pct", self._params.tier1_min_oi_drop_pct),
                ),
                tier2_min_oi_drop_pct=p.get(
                    "tier2_min_oi_drop_pct", self._params.tier2_min_oi_drop_pct,
                ),
                tier3_min_oi_drop_pct=p.get(
                    "tier3_min_oi_drop_pct", self._params.tier3_min_oi_drop_pct,
                ),
                # Tier stop/TP mults
                tier1_stop_atr_mult=p.get(
                    "tier1_stop_atr_mult", self._params.tier1_stop_atr_mult,
                ),
                tier1_tp_atr_mult=p.get(
                    "tier1_tp_atr_mult", self._params.tier1_tp_atr_mult,
                ),
                tier2_stop_atr_mult=p.get(
                    "tier2_stop_atr_mult", self._params.tier2_stop_atr_mult,
                ),
                tier2_tp_atr_mult=p.get(
                    "tier2_tp_atr_mult", self._params.tier2_tp_atr_mult,
                ),
                tier3_stop_atr_mult=p.get(
                    "tier3_stop_atr_mult", self._params.tier3_stop_atr_mult,
                ),
                tier3_tp_atr_mult=p.get(
                    "tier3_tp_atr_mult", self._params.tier3_tp_atr_mult,
                ),
                # Volume surge
                vol_lookback=p.get("vol_lookback", self._params.vol_lookback),
                vol_surge_min_ratio=p.get(
                    "vol_surge_min_ratio", self._params.vol_surge_min_ratio,
                ),
                # Heatmap magnet
                heatmap_magnet_enabled=p.get(
                    "heatmap_magnet_enabled", self._params.heatmap_magnet_enabled,
                ),
                cluster_min_notional_usd=p.get(
                    "cluster_min_notional_usd", self._params.cluster_min_notional_usd,
                ),
                magnet_proximity_pct=p.get(
                    "magnet_proximity_pct", self._params.magnet_proximity_pct,
                ),
                cluster_score_weight=p.get(
                    "cluster_score_weight", self._params.cluster_score_weight,
                ),
                heatmap_fallback_on_missing=p.get(
                    "heatmap_fallback_on_missing", self._params.heatmap_fallback_on_missing,
                ),
                portfolio_a_min_conviction=p.get(
                    "portfolio_a_min_conviction", self._params.portfolio_a_min_conviction,
                ),
            )

        self._enabled = True
        self._bars_since_signal = self._params.cooldown_bars
        # Heatmap store — wired externally via set_heatmap_store()
        self._heatmap_store: dict[str, list[LiquidationCluster]] | None = None

    @property
    def name(self) -> str:
        return "liquidation_cascade"

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def min_history(self) -> int:
        return max(self._params.oi_lookback, self._params.atr_period) + 5

    # ------------------------------------------------------------------
    # Heatmap store injection
    # ------------------------------------------------------------------

    def set_heatmap_store(
        self, store: dict[str, list[LiquidationCluster]]
    ) -> None:
        """Wire the shared heatmap cluster dict from the Coinglass poller.

        Must be called before the first ``evaluate()`` tick if heatmap-boosted
        conviction is desired.  Idempotent — calling again replaces the ref.

        Args:
            store: Shared mutable dict mapping instrument IDs to their latest
                ``LiquidationCluster`` lists (maintained by the poller).
        """
        self._heatmap_store = store
        _logger.info("heatmap_store_wired", strategy=self.name)

    @staticmethod
    def _find_nearby_clusters(
        clusters: list[LiquidationCluster],
        current_price: float,
        proximity_pct: float,
    ) -> list[LiquidationCluster]:
        """Return clusters within ``proximity_pct`` of ``current_price``, sorted by notional.

        Args:
            clusters: Full cluster list for the instrument.
            current_price: Current mark/last price.
            proximity_pct: Distance threshold in percent.

        Returns:
            Filtered list sorted descending by notional.
        """
        nearby = [c for c in clusters if c.distance_pct <= proximity_pct]
        nearby.sort(key=lambda c: c.notional_usd, reverse=True)
        return nearby

    # ------------------------------------------------------------------
    # Tier classification
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_tier(abs_oi_drop_pct: float) -> int:
        """Classify OI drop into tier.

        Tier 1: [2%, 4%), Tier 2: [4%, 8%), Tier 3: [8%, inf).
        """
        if abs_oi_drop_pct >= 8.0:
            return 3
        elif abs_oi_drop_pct >= 4.0:
            return 2
        else:
            return 1

    def evaluate(
        self,
        snapshot: MarketSnapshot,
        store: FeatureStore,
    ) -> list[StandardSignal]:
        """Detect liquidation cascade and signal fade or follow."""
        self._bars_since_signal += 1

        if store.sample_count < self.min_history:
            return []

        if self._bars_since_signal < self._params.cooldown_bars:
            return []

        p = self._params
        tick_size = get_instrument(snapshot.instrument).tick_size
        closes = store.closes
        highs = store.highs
        lows = store.lows
        ois = store.open_interests

        # OI change rate over lookback window
        oi_change_pct = self._compute_oi_change_pct(ois, p.oi_lookback)
        if oi_change_pct is None:
            return []

        # Not a liquidation event if OI hasn't dropped enough
        if oi_change_pct > -p.tier1_min_oi_drop_pct:
            return []

        # Volume surge confirmation (LIQ-02)
        bar_vols = store.bar_volumes
        if len(bar_vols) < p.vol_lookback:
            return []
        recent_vols = np.abs(bar_vols[-p.vol_lookback:])
        vol_avg = float(np.mean(recent_vols))
        cur_vol = float(np.abs(bar_vols[-1]))
        vol_surge_ratio = cur_vol / vol_avg if vol_avg > 0 else 0.0
        if vol_surge_ratio < p.vol_surge_min_ratio:
            return []  # No volume surge = likely organic OI reduction

        # Price change over same window
        price_change_pct = self._compute_price_change_pct(closes, p.oi_lookback)
        if price_change_pct is None:
            return []

        # Orderbook imbalance
        cur_imbalance = snapshot.orderbook_imbalance

        # Volatility check
        atr_vals = atr(highs, lows, closes, p.atr_period)
        cur_atr = atr_vals[-1]
        if np.isnan(cur_atr):
            return []

        # Classify cascade tier (LIQ-01)
        tier = self._classify_tier(abs(oi_change_pct))

        # Determine direction: fade or follow
        direction, mode = self._determine_direction(
            oi_change_pct, price_change_pct, cur_imbalance, p,
        )
        if direction is None:
            return []

        conviction = self._compute_conviction(
            oi_change_pct, price_change_pct, cur_imbalance,
            snapshot.volatility_1h, p, tier=tier,
        )

        # ── Heatmap magnet mode ──────────────────────────────────────────────
        # Runs AFTER base conviction is computed, BEFORE min_conviction gate.
        heatmap_metadata: dict[str, Any] = {}
        current_price = float(snapshot.last_price)

        if p.heatmap_magnet_enabled:
            instrument_clusters: list[LiquidationCluster] | None = None

            if self._heatmap_store is not None:
                instrument_clusters = self._heatmap_store.get(snapshot.instrument)

            if instrument_clusters is None:
                # No heatmap data available
                if not p.heatmap_fallback_on_missing:
                    return []
                # else: proceed with base conviction (graceful fallback)
            else:
                nearby = self._find_nearby_clusters(
                    instrument_clusters, current_price, p.magnet_proximity_pct,
                )
                if nearby:
                    largest = nearby[0]
                    # Boost scales with cluster size, capped at cluster_score_weight
                    raw_boost = (largest.notional_usd / 10_000_000.0) * p.cluster_score_weight
                    boost = min(p.cluster_score_weight, raw_boost)

                    # Directional alignment bonus: cluster on the "target" side
                    # SHORT follow: cluster below current price (liq wall beneath)
                    # LONG fade: cluster above current price (liq wall overhead)
                    if (
                        direction == PositionSide.SHORT
                        and largest.price_level < current_price
                    ) or (
                        direction == PositionSide.LONG
                        and largest.price_level > current_price
                    ):
                        boost = min(p.cluster_score_weight, boost * 1.25)

                    conviction = min(1.0, conviction + boost)

                    heatmap_metadata = {
                        "heatmap_clusters_nearby": len(nearby),
                        "nearest_cluster_price": round(largest.price_level, 2),
                        "nearest_cluster_notional": round(largest.notional_usd, 0),
                        "heatmap_conviction_boost": round(boost, 4),
                    }
                else:
                    heatmap_metadata = {
                        "heatmap_clusters_nearby": 0,
                        "nearest_cluster_price": None,
                        "nearest_cluster_notional": None,
                        "heatmap_conviction_boost": 0.0,
                    }
        # ────────────────────────────────────────────────────────────────────

        if conviction < p.min_conviction:
            return []

        entry = snapshot.last_price
        atr_d = Decimal(str(cur_atr))

        # Tier-specific stop/TP widths (LIQ-01)
        stop_mult: float = getattr(p, f"tier{tier}_stop_atr_mult")
        tp_mult: float = getattr(p, f"tier{tier}_tp_atr_mult")

        if direction == PositionSide.LONG:
            stop_loss = round_to_tick(entry - atr_d * Decimal(str(stop_mult)), tick_size)
            take_profit = round_to_tick(entry + atr_d * Decimal(str(tp_mult)), tick_size)
        else:
            stop_loss = round_to_tick(entry + atr_d * Decimal(str(stop_mult)), tick_size)
            take_profit = round_to_tick(entry - atr_d * Decimal(str(tp_mult)), tick_size)

        reasoning = (
            f"Liquidation cascade {mode} (Tier {tier}): OI dropped {oi_change_pct:.2f}% "
            f"over {p.oi_lookback} bars, vol surge {vol_surge_ratio:.1f}x, "
            f"price {price_change_pct:+.2f}%, imbalance={cur_imbalance:+.2f}"
        )

        metadata: dict[str, Any] = {
            "tier": tier,
            "oi_change_pct": round(oi_change_pct, 3),
            "price_change_pct": round(price_change_pct, 3),
            "orderbook_imbalance": round(cur_imbalance, 3),
            "vol_surge_ratio": round(vol_surge_ratio, 3),
            "mode": mode,
            "atr": round(cur_atr, 2),
        }
        metadata.update(heatmap_metadata)

        suggested_target = (
            PortfolioTarget.A
            if conviction >= p.portfolio_a_min_conviction
            else PortfolioTarget.B
        )

        signal = StandardSignal(
            signal_id=generate_id("sig"),
            timestamp=utc_now(),
            instrument=snapshot.instrument,
            direction=direction,
            conviction=conviction,
            source=SignalSource.LIQUIDATION_CASCADE,
            time_horizon=timedelta(hours=2),
            reasoning=reasoning,
            suggested_target=suggested_target,
            entry_price=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            metadata=metadata,
        )

        self._bars_since_signal = 0
        return [signal]

    @staticmethod
    def _compute_oi_change_pct(
        ois: np.ndarray, lookback: int,
    ) -> float | None:
        """Compute percentage OI change over lookback window."""
        if len(ois) < lookback + 1:
            return None
        old_oi = ois[-(lookback + 1)]
        cur_oi = ois[-1]
        if old_oi <= 0:
            return None
        return ((cur_oi - old_oi) / old_oi) * 100.0

    @staticmethod
    def _compute_price_change_pct(
        closes: np.ndarray, lookback: int,
    ) -> float | None:
        """Compute percentage price change over lookback window."""
        if len(closes) < lookback + 1:
            return None
        old = closes[-(lookback + 1)]
        cur = closes[-1]
        if old <= 0:
            return None
        return ((cur - old) / old) * 100.0

    @staticmethod
    def _determine_direction(
        oi_change_pct: float,
        price_change_pct: float,
        imbalance: float,
        params: LiquidationCascadeParams,
    ) -> tuple[PositionSide | None, str]:
        """Decide whether to fade or follow the cascade.

        Fade: OI drops and price already dumped -> expect bounce (LONG).
               OI drops and price already pumped -> expect pullback (SHORT).
        Follow: extreme imbalance suggests cascade is accelerating.
        """
        # Fade: OI dropped, price dumped -> long the bounce
        if price_change_pct < -1.0 and imbalance < -params.imbalance_threshold:
            return PositionSide.LONG, "fade"

        # Fade: OI dropped, price pumped -> short squeeze exhaustion
        if price_change_pct > 1.0 and imbalance > params.imbalance_threshold:
            return PositionSide.SHORT, "fade"

        # Follow: accelerating cascade with heavy sell imbalance
        if oi_change_pct < -params.tier1_min_oi_drop_pct * 2 and imbalance < -0.5:
            return PositionSide.SHORT, "follow"

        return None, ""

    @staticmethod
    def _compute_conviction(
        oi_change_pct: float,
        price_change_pct: float,
        imbalance: float,
        volatility_1h: float,
        params: LiquidationCascadeParams,
        tier: int = 1,
    ) -> float:
        """Compute conviction from cascade strength indicators.

        OI drop component (0-0.4): how severe the OI drop is.
        Imbalance component (0-0.3): how extreme the orderbook is.
        Volatility component (0-0.3): elevated vol confirms cascade.
        Tier boost: T1=0.0, T2=0.05, T3=0.10 base conviction addition.
        """
        # OI component: bigger drop = higher conviction
        oi_score = min(abs(oi_change_pct) / 10.0, 0.4)

        # Imbalance component
        imb_score = min(abs(imbalance) / 1.0, 0.3)

        # Volatility component: higher vol = more confidence it's a real event
        vol_score = min(volatility_1h / 1.0, 0.3)

        # Tier boost: higher tier = more conviction (LIQ-01)
        tier_boost = {1: 0.0, 2: 0.05, 3: 0.10}[tier]

        return round(min(oi_score + imb_score + vol_score + tier_boost, 1.0), 3)
