"""Unit tests for Claude orchestrator client (orch_client.py).

All tests mock the Anthropic API and filesystem — no real HTTP calls or
file I/O outside the project's bounds.yaml.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.signals.orch_client import (
    ORCHESTRATOR_TOOL,
    OrchestratorParams,
    build_orchestrator_context,
    call_claude_orchestrator,
    validate_orchestrator_response,
)
from agents.signals.feature_store import FeatureStore
from libs.common.models.enums import MarketRegime
from libs.common.models.market_snapshot import MarketSnapshot

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_BASE_TS = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
_BOUNDS_PATH = Path(__file__).resolve().parent.parent.parent.parent / "configs" / "bounds.yaml"


def _snap(
    instrument: str = "ETH-PERP",
    mark: float = 2200.0,
    funding: float = 0.0001,
    oi: float = 80_000.0,
    vol_1h: float = 0.18,
    vol_24h: float = 0.45,
    ts: datetime | None = None,
) -> MarketSnapshot:
    """Minimal MarketSnapshot for testing."""
    if ts is None:
        ts = _BASE_TS
    mark_d = Decimal(str(mark))
    return MarketSnapshot(
        timestamp=ts,
        instrument=instrument,
        mark_price=mark_d,
        index_price=mark_d - Decimal("0.50"),
        last_price=mark_d,
        best_bid=mark_d - Decimal("0.25"),
        best_ask=mark_d + Decimal("0.25"),
        spread_bps=2.2,
        volume_24h=Decimal("15000"),
        open_interest=Decimal(str(oi)),
        funding_rate=Decimal(str(funding)),
        next_funding_time=ts + timedelta(minutes=30),
        hours_since_last_funding=0.5,
        orderbook_imbalance=0.05,
        volatility_1h=vol_1h,
        volatility_24h=vol_24h,
    )


def _build_store(
    prices: list[float],
    oi_vals: list[float] | None = None,
    funding_vals: list[float] | None = None,
) -> FeatureStore:
    """Build a FeatureStore pre-loaded with samples (zero sample_interval)."""
    store = FeatureStore(sample_interval=timedelta(seconds=0))
    for i, price in enumerate(prices):
        oi = oi_vals[i] if oi_vals is not None else 80_000.0
        funding = funding_vals[i] if funding_vals is not None else 0.0001
        snap = _snap(
            mark=price,
            ts=_BASE_TS + timedelta(seconds=i),
            oi=oi,
            funding=funding,
        )
        store.update(snap)
    return store


def _make_tool_use_block(raw: dict[str, Any]) -> SimpleNamespace:
    """Fake a tool_use content block from the Anthropic SDK."""
    block = SimpleNamespace()
    block.type = "tool_use"
    block.input = raw
    return block


def _make_claude_response(decisions: list[dict[str, Any]], summary: str = "ok") -> SimpleNamespace:
    """Wrap decisions in a mock Anthropic message response."""
    resp = SimpleNamespace()
    resp.content = [_make_tool_use_block({"decisions": decisions, "summary": summary})]
    return resp


def _make_regime_detector(regimes: dict[str, str] | None = None) -> MagicMock:
    """Create a mock RegimeDetector with configurable per-instrument regimes."""
    det = MagicMock()
    regimes = regimes or {}

    def _regime_for(instr: str) -> MarketRegime:
        val = regimes.get(instr, "ranging")
        return MarketRegime(val)

    det.regime_for.side_effect = _regime_for
    det.current_regime = MarketRegime.RANGING
    return det


# ---------------------------------------------------------------------------
# TestOrchestratorTool
# ---------------------------------------------------------------------------


class TestOrchestratorTool:
    """Validate ORCHESTRATOR_TOOL schema structure."""

    def test_tool_name(self) -> None:
        assert ORCHESTRATOR_TOOL["name"] == "submit_orchestrator_decisions"

    def test_top_level_schema(self) -> None:
        schema = ORCHESTRATOR_TOOL["input_schema"]
        assert schema["type"] == "object"
        assert "decisions" in schema["properties"]
        assert "summary" in schema["properties"]

    def test_required_top_level_fields(self) -> None:
        schema = ORCHESTRATOR_TOOL["input_schema"]
        assert "decisions" in schema["required"]
        assert "summary" in schema["required"]

    def test_decisions_array_type(self) -> None:
        decisions_schema = ORCHESTRATOR_TOOL["input_schema"]["properties"]["decisions"]
        assert decisions_schema["type"] == "array"

    def test_decision_item_required_fields(self) -> None:
        item_schema = ORCHESTRATOR_TOOL["input_schema"]["properties"]["decisions"]["items"]
        required = item_schema["required"]
        assert "instrument" in required
        assert "strategy" in required
        assert "enabled" in required
        assert "reasoning" in required

    def test_decision_item_properties(self) -> None:
        item_props = ORCHESTRATOR_TOOL["input_schema"]["properties"]["decisions"]["items"]["properties"]
        assert item_props["instrument"]["type"] == "string"
        assert item_props["strategy"]["type"] == "string"
        assert item_props["enabled"]["type"] == "boolean"
        assert item_props["param_adjustments"]["type"] == "object"
        assert item_props["reasoning"]["type"] == "string"

    def test_param_adjustments_additionalProperties(self) -> None:
        item_props = ORCHESTRATOR_TOOL["input_schema"]["properties"]["decisions"]["items"]["properties"]
        assert item_props["param_adjustments"]["additionalProperties"]["type"] == "number"


# ---------------------------------------------------------------------------
# TestBuildOrchestratorContext
# ---------------------------------------------------------------------------


class TestBuildOrchestratorContext:
    """Test context assembly from FeatureStore + snapshots + regime_detector."""

    def test_contains_all_instruments(self) -> None:
        instruments = ["ETH-PERP", "BTC-PERP"]
        stores = {i: _build_store([2000.0] * 5) for i in instruments}
        snaps = {i: _snap(instrument=i) for i in instruments}
        det = _make_regime_detector()

        ctx = build_orchestrator_context(instruments, stores, snaps, det)

        assert "ETH-PERP" in ctx
        assert "BTC-PERP" in ctx

    def test_contains_regime_for_each_instrument(self) -> None:
        instruments = ["ETH-PERP"]
        stores = {"ETH-PERP": _build_store([2200.0] * 5)}
        snaps = {"ETH-PERP": _snap()}
        det = _make_regime_detector({"ETH-PERP": "trending_up"})

        ctx = build_orchestrator_context(instruments, stores, snaps, det)

        assert "regime=trending_up" in ctx

    def test_contains_vol_1h(self) -> None:
        instruments = ["ETH-PERP"]
        stores = {"ETH-PERP": _build_store([2200.0] * 5)}
        snaps = {"ETH-PERP": _snap(vol_1h=0.2345)}
        det = _make_regime_detector()

        ctx = build_orchestrator_context(instruments, stores, snaps, det)

        assert "0.2345" in ctx

    def test_contains_funding_direction_rising(self) -> None:
        instruments = ["ETH-PERP"]
        funding_vals = [0.0001 * (i + 1) for i in range(12)]  # strictly rising
        stores = {"ETH-PERP": _build_store([2200.0] * 12, funding_vals=funding_vals)}
        snaps = {"ETH-PERP": _snap()}
        det = _make_regime_detector()

        ctx = build_orchestrator_context(instruments, stores, snaps, det)

        assert "rising" in ctx

    def test_contains_oi_pct_change(self) -> None:
        instruments = ["ETH-PERP"]
        oi_vals = [80_000.0 + i * 1_000 for i in range(12)]  # growing OI
        stores = {"ETH-PERP": _build_store([2200.0] * 12, oi_vals=oi_vals)}
        snaps = {"ETH-PERP": _snap()}
        det = _make_regime_detector()

        ctx = build_orchestrator_context(instruments, stores, snaps, det)

        # OI pct change should appear (positive)
        assert "OI=" in ctx
        assert "%" in ctx

    def test_active_strategies_listed(self) -> None:
        instruments = ["ETH-PERP"]
        stores = {"ETH-PERP": _build_store([2200.0] * 5)}
        snaps = {"ETH-PERP": _snap()}
        det = _make_regime_detector()

        ctx = build_orchestrator_context(instruments, stores, snaps, det)

        assert "Active strategies:" in ctx
        assert "momentum" in ctx

    def test_graceful_missing_snapshot(self) -> None:
        """Missing snapshot for an instrument should not raise."""
        instruments = ["ETH-PERP", "BTC-PERP"]
        stores = {
            "ETH-PERP": _build_store([2200.0] * 5),
            "BTC-PERP": _build_store([]),  # empty store
        }
        snaps = {"ETH-PERP": _snap()}  # BTC-PERP snapshot missing
        det = _make_regime_detector()

        ctx = build_orchestrator_context(instruments, stores, snaps, det)

        assert "ETH-PERP" in ctx
        assert "BTC-PERP" in ctx
        assert "N/A" in ctx  # vol_1h missing

    def test_graceful_empty_store(self) -> None:
        """Empty FeatureStore should not raise and should produce N/A or defaults."""
        instruments = ["ETH-PERP"]
        stores = {"ETH-PERP": _build_store([])}
        snaps = {"ETH-PERP": _snap()}
        det = _make_regime_detector()

        ctx = build_orchestrator_context(instruments, stores, snaps, det)

        assert "ETH-PERP" in ctx

    def test_header_present(self) -> None:
        instruments = ["ETH-PERP"]
        stores = {"ETH-PERP": _build_store([2200.0] * 5)}
        snaps = {"ETH-PERP": _snap()}
        det = _make_regime_detector()

        ctx = build_orchestrator_context(instruments, stores, snaps, det)

        assert "## Orchestrator Context" in ctx
        assert "Instruments: ETH-PERP" in ctx

    def test_multiple_instruments_all_present(self) -> None:
        instruments = ["ETH-PERP", "BTC-PERP", "SOL-PERP"]
        stores = {i: _build_store([1000.0] * 5) for i in instruments}
        snaps = {i: _snap(instrument=i) for i in instruments}
        det = _make_regime_detector({
            "ETH-PERP": "ranging",
            "BTC-PERP": "trending_up",
            "SOL-PERP": "high_volatility",
        })

        ctx = build_orchestrator_context(instruments, stores, snaps, det)

        assert "ETH-PERP" in ctx
        assert "BTC-PERP" in ctx
        assert "SOL-PERP" in ctx
        assert "trending_up" in ctx
        assert "high_volatility" in ctx


# ---------------------------------------------------------------------------
# TestCallClaudeOrchestrator
# ---------------------------------------------------------------------------


class TestCallClaudeOrchestrator:
    """Test async Claude API call with various mock scenarios."""

    def test_valid_response_returns_decisions(self) -> None:
        decisions = [
            {
                "instrument": "ETH-PERP",
                "strategy": "momentum",
                "enabled": True,
                "reasoning": "trending_up",
                "param_adjustments": {},
            }
        ]
        mock_resp = _make_claude_response(decisions)
        params = OrchestratorParams()

        with patch("agents.signals.orch_client.anthropic.AsyncAnthropic") as mock_cls:
            mock_client = AsyncMock()
            mock_cls.return_value = mock_client
            mock_client.messages.create = AsyncMock(return_value=mock_resp)

            with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
                result = asyncio.new_event_loop().run_until_complete(
                    call_claude_orchestrator("context", params)
                )

        assert result is not None
        assert len(result) == 1
        assert result[0]["instrument"] == "ETH-PERP"
        assert result[0]["strategy"] == "momentum"
        assert result[0]["enabled"] is True

    def test_missing_api_key_returns_none(self) -> None:
        params = OrchestratorParams()
        env = {k: v for k, v in __import__("os").environ.items() if k != "ANTHROPIC_API_KEY"}

        with patch.dict("os.environ", env, clear=True):
            result = asyncio.new_event_loop().run_until_complete(
                call_claude_orchestrator("context", params)
            )

        assert result is None

    def test_api_error_returns_none(self) -> None:
        import anthropic as anth

        params = OrchestratorParams()

        with patch("agents.signals.orch_client.anthropic.AsyncAnthropic") as mock_cls:
            mock_client = AsyncMock()
            mock_cls.return_value = mock_client
            mock_client.messages.create = AsyncMock(
                side_effect=anth.APIError("server error", request=MagicMock(), body=None)
            )

            with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
                result = asyncio.new_event_loop().run_until_complete(
                    call_claude_orchestrator("context", params)
                )

        assert result is None

    def test_missing_tool_use_block_returns_none(self) -> None:
        """If Claude returns no tool_use block, return None."""
        mock_resp = SimpleNamespace()
        text_block = SimpleNamespace()
        text_block.type = "text"
        text_block.text = "Sorry, I cannot help."
        mock_resp.content = [text_block]

        params = OrchestratorParams()

        with patch("agents.signals.orch_client.anthropic.AsyncAnthropic") as mock_cls:
            mock_client = AsyncMock()
            mock_cls.return_value = mock_client
            mock_client.messages.create = AsyncMock(return_value=mock_resp)

            with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
                result = asyncio.new_event_loop().run_until_complete(
                    call_claude_orchestrator("context", params)
                )

        assert result is None

    def test_empty_decisions_list_returned(self) -> None:
        """Claude returning empty decisions list is valid."""
        mock_resp = _make_claude_response([], summary="No changes needed.")
        params = OrchestratorParams()

        with patch("agents.signals.orch_client.anthropic.AsyncAnthropic") as mock_cls:
            mock_client = AsyncMock()
            mock_cls.return_value = mock_client
            mock_client.messages.create = AsyncMock(return_value=mock_resp)

            with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
                result = asyncio.new_event_loop().run_until_complete(
                    call_claude_orchestrator("context", params)
                )

        assert result == []


# ---------------------------------------------------------------------------
# TestValidateOrchestratorResponse
# ---------------------------------------------------------------------------


class TestValidateOrchestratorResponse:
    """Test bounds clipping, unknown param rejection, and decision structure."""

    def test_known_param_within_bounds_preserved(self) -> None:
        decisions = [
            {
                "instrument": "ETH-PERP",
                "strategy": "momentum",
                "enabled": True,
                "reasoning": "trending",
                "param_adjustments": {"min_conviction": 0.60},
            }
        ]

        result = validate_orchestrator_response(decisions, _BOUNDS_PATH)

        assert len(result) == 1
        assert result[0]["param_adjustments"]["min_conviction"] == pytest.approx(0.60)

    def test_param_above_max_clipped(self) -> None:
        """min_conviction max is 0.90 — value 0.99 should be clipped."""
        decisions = [
            {
                "instrument": "ETH-PERP",
                "strategy": "momentum",
                "enabled": True,
                "reasoning": "high confidence",
                "param_adjustments": {"min_conviction": 0.99},
            }
        ]

        result = validate_orchestrator_response(decisions, _BOUNDS_PATH)

        # bounds.yaml max for min_conviction is 0.90
        assert result[0]["param_adjustments"]["min_conviction"] == pytest.approx(0.90)

    def test_param_below_min_clipped(self) -> None:
        """min_conviction min is 0.10 — value 0.01 should be clipped."""
        decisions = [
            {
                "instrument": "ETH-PERP",
                "strategy": "momentum",
                "enabled": True,
                "reasoning": "very low",
                "param_adjustments": {"min_conviction": 0.01},
            }
        ]

        result = validate_orchestrator_response(decisions, _BOUNDS_PATH)

        assert result[0]["param_adjustments"]["min_conviction"] == pytest.approx(0.10)

    def test_unknown_param_rejected(self) -> None:
        """Unknown param should not appear in the output."""
        decisions = [
            {
                "instrument": "ETH-PERP",
                "strategy": "momentum",
                "enabled": True,
                "reasoning": "test",
                "param_adjustments": {"totally_unknown_param": 42.0},
            }
        ]

        result = validate_orchestrator_response(decisions, _BOUNDS_PATH)

        assert "totally_unknown_param" not in result[0]["param_adjustments"]

    def test_empty_param_adjustments_preserved(self) -> None:
        decisions = [
            {
                "instrument": "BTC-PERP",
                "strategy": "mean_reversion",
                "enabled": False,
                "reasoning": "ranging market",
                "param_adjustments": {},
            }
        ]

        result = validate_orchestrator_response(decisions, _BOUNDS_PATH)

        assert result[0]["param_adjustments"] == {}

    def test_enabled_false_preserved(self) -> None:
        decisions = [
            {
                "instrument": "SOL-PERP",
                "strategy": "momentum",
                "enabled": False,
                "reasoning": "low volatility",
                "param_adjustments": {},
            }
        ]

        result = validate_orchestrator_response(decisions, _BOUNDS_PATH)

        assert result[0]["enabled"] is False

    def test_enabled_true_preserved(self) -> None:
        decisions = [
            {
                "instrument": "SOL-PERP",
                "strategy": "momentum",
                "enabled": True,
                "reasoning": "good signal",
                "param_adjustments": {},
            }
        ]

        result = validate_orchestrator_response(decisions, _BOUNDS_PATH)

        assert result[0]["enabled"] is True

    def test_multiple_decisions_validated_independently(self) -> None:
        """Each decision is processed independently; one bad param doesn't affect others."""
        decisions = [
            {
                "instrument": "ETH-PERP",
                "strategy": "momentum",
                "enabled": True,
                "reasoning": "trending",
                "param_adjustments": {"min_conviction": 0.65},
            },
            {
                "instrument": "BTC-PERP",
                "strategy": "mean_reversion",
                "enabled": False,
                "reasoning": "ranging",
                "param_adjustments": {"stop_loss_atr_mult": 2.5, "unknown_junk": 99.0},
            },
        ]

        result = validate_orchestrator_response(decisions, _BOUNDS_PATH)

        assert len(result) == 2
        assert result[0]["param_adjustments"]["min_conviction"] == pytest.approx(0.65)
        assert result[1]["param_adjustments"]["stop_loss_atr_mult"] == pytest.approx(2.5)
        assert "unknown_junk" not in result[1]["param_adjustments"]

    def test_none_param_adjustments_treated_as_empty(self) -> None:
        """param_adjustments=None (missing key) should yield empty dict."""
        decisions = [
            {
                "instrument": "ETH-PERP",
                "strategy": "momentum",
                "enabled": True,
                "reasoning": "ok",
                # No param_adjustments key
            }
        ]

        result = validate_orchestrator_response(decisions, _BOUNDS_PATH)

        assert result[0]["param_adjustments"] == {}

    def test_bounds_file_not_found_returns_safe_fallback(self) -> None:
        """Missing bounds.yaml falls back to returning decisions with empty adjustments."""
        decisions = [
            {
                "instrument": "ETH-PERP",
                "strategy": "momentum",
                "enabled": True,
                "reasoning": "test",
                "param_adjustments": {"min_conviction": 0.70},
            }
        ]

        result = validate_orchestrator_response(decisions, Path("/nonexistent/bounds.yaml"))

        # Still returns one entry; param_adjustments is empty (safe fallback)
        assert len(result) == 1
        assert result[0]["param_adjustments"] == {}

    def test_instrument_strategy_reasoning_preserved(self) -> None:
        decisions = [
            {
                "instrument": "QQQ-PERP",
                "strategy": "regime_trend",
                "enabled": True,
                "reasoning": "SPY correlation elevated",
                "param_adjustments": {},
            }
        ]

        result = validate_orchestrator_response(decisions, _BOUNDS_PATH)

        assert result[0]["instrument"] == "QQQ-PERP"
        assert result[0]["strategy"] == "regime_trend"
        assert result[0]["reasoning"] == "SPY correlation elevated"

    def test_cooldown_bars_clipped_to_bounds(self) -> None:
        """cooldown_bars max is 30 — value 50 should be clipped."""
        decisions = [
            {
                "instrument": "ETH-PERP",
                "strategy": "momentum",
                "enabled": True,
                "reasoning": "test",
                "param_adjustments": {"cooldown_bars": 50},
            }
        ]

        result = validate_orchestrator_response(decisions, _BOUNDS_PATH)

        assert result[0]["param_adjustments"]["cooldown_bars"] == pytest.approx(30.0)


# ---------------------------------------------------------------------------
# TestOrchestratorParams
# ---------------------------------------------------------------------------


class TestOrchestratorParams:
    """Test OrchestratorParams dataclass defaults, mutation, and YAML loading."""

    def test_defaults(self) -> None:
        p = OrchestratorParams()
        assert p.enabled is True
        assert p.update_interval_seconds == 14400
        assert p.min_interval_seconds == 3600
        assert p.max_tokens == 1024

    def test_mutable(self) -> None:
        """OrchestratorParams must be mutable (not frozen)."""
        p = OrchestratorParams()
        p.enabled = False
        p.max_tokens = 2048
        assert p.enabled is False
        assert p.max_tokens == 2048

    def test_custom_values(self) -> None:
        p = OrchestratorParams(
            enabled=False,
            update_interval_seconds=7200,
            min_interval_seconds=1800,
            max_tokens=512,
        )
        assert p.enabled is False
        assert p.update_interval_seconds == 7200
        assert p.min_interval_seconds == 1800
        assert p.max_tokens == 512

    def test_from_yaml_config(self) -> None:
        """Params can be constructed from YAML config values."""
        import yaml

        yaml_path = Path(__file__).resolve().parent.parent.parent.parent / "configs" / "strategies" / "orchestrator.yaml"
        with open(yaml_path) as f:
            cfg = yaml.safe_load(f)

        p = OrchestratorParams(
            enabled=cfg["strategy"]["enabled"],
            update_interval_seconds=cfg["parameters"]["update_interval_seconds"],
            min_interval_seconds=cfg["parameters"]["min_interval_seconds"],
            max_tokens=cfg["parameters"]["max_tokens"],
        )

        assert p.enabled is True
        assert p.update_interval_seconds == 14400
        assert p.min_interval_seconds == 3600
        assert p.max_tokens == 1024
