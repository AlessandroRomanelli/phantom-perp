"""Tests verifying all instruments load per-instrument configs correctly.

Validates that:
- Every active strategy has overrides for all relevant instruments
- ETH-PERP is never running on bare defaults
- Liquidation cascade is disabled for equity perps
- Momentum is globally disabled
- All YAML configs pass schema validation (no typos)
- Conviction thresholds are lowered for signal frequency (D-04)
"""

from __future__ import annotations

import pytest

from libs.common.config import (
    load_strategy_config,
    load_strategy_config_for_instrument,
    validate_strategy_config,
)

from agents.signals.strategies.correlation import CorrelationParams
from agents.signals.strategies.liquidation_cascade import LiquidationCascadeParams
from agents.signals.strategies.mean_reversion import MeanReversionParams
from agents.signals.strategies.regime_trend import RegimeTrendParams

ALL_INSTRUMENTS = ["ETH-PERP", "BTC-PERP", "SOL-PERP", "QQQ-PERP", "SPY-PERP"]
STRATEGIES_WITH_ALL_INSTRUMENTS = ["mean_reversion", "correlation", "regime_trend"]


class TestAllInstrumentsHaveOverrides:
    """Every active strategy must have per-instrument config for all relevant instruments."""

    @pytest.mark.parametrize("strategy_name", STRATEGIES_WITH_ALL_INSTRUMENTS)
    @pytest.mark.parametrize("instrument_id", ALL_INSTRUMENTS)
    def test_all_instruments_have_overrides(
        self, strategy_name: str, instrument_id: str
    ) -> None:
        config = load_strategy_config_for_instrument(strategy_name, instrument_id)
        assert config.get("_instrument") == instrument_id, (
            f"{strategy_name} config for {instrument_id} missing _instrument key"
        )
        assert config.get("parameters"), (
            f"{strategy_name} config for {instrument_id} has empty parameters"
        )


class TestEthPerpNotBareDefaults:
    """ETH-PERP must have explicit overrides, not just inherit base defaults (Pitfall 4)."""

    @pytest.mark.parametrize("strategy_name", ["mean_reversion", "regime_trend"])
    def test_eth_perp_not_using_bare_defaults(self, strategy_name: str) -> None:
        base = load_strategy_config(strategy_name)
        base_params = base.get("parameters", {})

        eth_config = load_strategy_config_for_instrument(strategy_name, "ETH-PERP")
        eth_params = eth_config.get("parameters", {})

        # Count how many parameters differ from base
        diffs = {
            k: v for k, v in eth_params.items() if base_params.get(k) != v
        }
        assert len(diffs) >= 2, (
            f"{strategy_name} ETH-PERP has fewer than 2 overrides "
            f"(got {len(diffs)}): {diffs}"
        )


class TestLiquidationCascadeDisabledForEquity:
    """Liquidation cascade is a crypto-native phenomenon (D-11)."""

    @pytest.mark.parametrize("instrument_id", ["QQQ-PERP", "SPY-PERP"])
    def test_liquidation_cascade_disabled_for_equity(
        self, instrument_id: str
    ) -> None:
        config = load_strategy_config_for_instrument(
            "liquidation_cascade", instrument_id
        )
        strategy_meta = config.get("strategy", {})
        assert strategy_meta.get("enabled") is False, (
            f"liquidation_cascade should be disabled for {instrument_id}"
        )


class TestMomentumDisabledGlobally:
    """Momentum stays disabled until Phase 2 (D-09)."""

    def test_momentum_disabled_globally(self) -> None:
        config = load_strategy_config("momentum")
        assert config["strategy"]["enabled"] is False, (
            "momentum should be globally disabled"
        )


class TestConfigValidation:
    """All strategy YAML configs must pass schema validation (no typos)."""

    STRATEGY_PARAMS = {
        "mean_reversion": MeanReversionParams,
        "liquidation_cascade": LiquidationCascadeParams,
        "correlation": CorrelationParams,
        "regime_trend": RegimeTrendParams,
    }

    @pytest.mark.parametrize(
        "strategy_name,params_cls",
        [
            ("mean_reversion", MeanReversionParams),
            ("liquidation_cascade", LiquidationCascadeParams),
            ("correlation", CorrelationParams),
            ("regime_trend", RegimeTrendParams),
        ],
    )
    def test_config_validation_passes_for_all_strategies(
        self, strategy_name: str, params_cls: type
    ) -> None:
        config = load_strategy_config(strategy_name)
        # Should not raise ValueError — proves YAML has no typos
        validate_strategy_config(strategy_name, config, params_cls)


class TestPerInstrumentMinConvictionLowered:
    """Signal frequency improvement (D-04): min_conviction must be lowered."""

    def test_mean_reversion_eth_min_conviction(self) -> None:
        config = load_strategy_config_for_instrument("mean_reversion", "ETH-PERP")
        min_conv = config["parameters"]["min_conviction"]
        assert min_conv <= 0.35, (
            f"mean_reversion ETH-PERP min_conviction={min_conv}, expected <= 0.35"
        )

    def test_regime_trend_eth_min_conviction(self) -> None:
        config = load_strategy_config_for_instrument("regime_trend", "ETH-PERP")
        min_conv = config["parameters"]["min_conviction"]
        assert min_conv <= 0.40, (
            f"regime_trend ETH-PERP min_conviction={min_conv}, expected <= 0.40"
        )
