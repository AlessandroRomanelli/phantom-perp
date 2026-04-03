"""Unit tests for Claude Market Analysis — strategy, client, scheduler, validation.

All tests mock the Anthropic API; no real HTTP calls are made.
Pre-existing 4 test failures (unrelated files) are not affected by this module.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import anthropic
import httpx
import pytest

from agents.signals.claude_market_client import (
    MARKET_ANALYSIS_TOOL,
    build_market_context,
    build_system_prompt,
    call_claude_analysis,
    validate_claude_response,
)
from agents.signals.claude_scheduler import _process_instrument
from agents.signals.feature_store import FeatureStore
from agents.signals.strategies.claude_market_analysis import (
    ClaudeMarketAnalysisParams,
    ClaudeMarketAnalysisStrategy,
)
from libs.common.models.enums import (
    MarketRegime,
    Route,
    PositionSide,
    SignalSource,
)
from libs.common.models.market_snapshot import MarketSnapshot
from libs.common.models.signal import StandardSignal

# ---------------------------------------------------------------------------
# Shared helpers (mirrors test_momentum.py patterns)
# ---------------------------------------------------------------------------

TEST_INSTRUMENT = "ETH-PERP"
_BASE_TS = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
_MARK = Decimal("2200.00")


def _snap(
    mark: float = 2200.0,
    ts: datetime | None = None,
    funding: float = 0.0001,
    oi: float = 80_000.0,
    vol_1h: float = 0.15,
    vol_24h: float = 0.45,
    instrument: str = TEST_INSTRUMENT,
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


def _build_store_with_prices(
    prices: list[float],
    oi_vals: list[float] | None = None,
) -> FeatureStore:
    """Build a FeatureStore pre-loaded with price samples (zero sample_interval)."""
    store = FeatureStore(sample_interval=timedelta(seconds=0))
    for i, price in enumerate(prices):
        oi = oi_vals[i] if oi_vals is not None else 80_000.0
        snap = _snap(mark=price, ts=_BASE_TS + timedelta(seconds=i), oi=oi)
        store.update(snap)
    return store


def _make_tool_use_block(raw: dict[str, Any]) -> SimpleNamespace:
    """Fake a tool_use response block from the Anthropic SDK."""
    block = SimpleNamespace()
    block.type = "tool_use"
    block.input = raw
    return block


def _make_claude_response(raw: dict[str, Any]) -> SimpleNamespace:
    """Wrap a tool_use block in a mock Anthropic message response."""
    resp = SimpleNamespace()
    resp.content = [_make_tool_use_block(raw)]
    resp.usage = SimpleNamespace(
        input_tokens=100,
        output_tokens=20,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )
    return resp


def _make_regime_detector(regime: MarketRegime = MarketRegime.RANGING) -> MagicMock:
    """Return a mock RegimeDetector that always returns the given regime."""
    detector = MagicMock()
    detector.regime_for.return_value = regime
    return detector


# ---------------------------------------------------------------------------
# 1. Strategy — evaluate() drains queue
# ---------------------------------------------------------------------------


class TestClaudeMarketAnalysisStrategy:
    """Tests for ClaudeMarketAnalysisStrategy.evaluate() queue-bridge behavior."""

    def _make_signal(self, suffix: str = "a") -> StandardSignal:
        return StandardSignal(
            signal_id=f"test-{suffix}",
            timestamp=_BASE_TS,
            instrument=TEST_INSTRUMENT,
            direction=PositionSide.LONG,
            conviction=0.72,
            source=SignalSource.CLAUDE_MARKET_ANALYSIS,
            time_horizon=timedelta(hours=4),
            reasoning="test",
        )

    def test_evaluate_drains_queue_returns_both_signals(self) -> None:
        """evaluate() returns all signals queued since last call."""
        strategy = ClaudeMarketAnalysisStrategy()
        queue: asyncio.Queue[StandardSignal] = asyncio.Queue(maxsize=50)
        strategy.set_queue(queue)

        sig_a = self._make_signal("a")
        sig_b = self._make_signal("b")
        queue.put_nowait(sig_a)
        queue.put_nowait(sig_b)

        snap = _snap()
        store = FeatureStore(sample_interval=timedelta(seconds=0))
        results = strategy.evaluate(snap, store)

        assert results == [sig_a, sig_b]

    def test_evaluate_returns_empty_when_queue_empty(self) -> None:
        """evaluate() returns [] when queue has been wired but is empty."""
        strategy = ClaudeMarketAnalysisStrategy()
        strategy.set_queue(asyncio.Queue(maxsize=50))

        results = strategy.evaluate(_snap(), FeatureStore())
        assert results == []

    def test_evaluate_returns_empty_when_no_queue_set(self) -> None:
        """evaluate() returns [] safely when set_queue() was never called."""
        strategy = ClaudeMarketAnalysisStrategy()
        results = strategy.evaluate(_snap(), FeatureStore())
        assert results == []

    def test_evaluate_drains_completely_on_second_call(self) -> None:
        """Second evaluate() call returns [] after queue was drained."""
        strategy = ClaudeMarketAnalysisStrategy()
        queue: asyncio.Queue[StandardSignal] = asyncio.Queue(maxsize=50)
        strategy.set_queue(queue)

        queue.put_nowait(self._make_signal("x"))
        snap = _snap()
        store = FeatureStore(sample_interval=timedelta(seconds=0))

        first = strategy.evaluate(snap, store)
        second = strategy.evaluate(snap, store)

        assert len(first) == 1
        assert second == []

    def test_set_queue_replaces_previous_queue(self) -> None:
        """Calling set_queue() again replaces the wired queue."""
        strategy = ClaudeMarketAnalysisStrategy()
        q1: asyncio.Queue[StandardSignal] = asyncio.Queue()
        q2: asyncio.Queue[StandardSignal] = asyncio.Queue()
        strategy.set_queue(q1)
        strategy.set_queue(q2)
        assert strategy.get_queue() is q2

    def test_strategy_properties(self) -> None:
        """Basic property contract."""
        strategy = ClaudeMarketAnalysisStrategy()
        assert strategy.name == "claude_market_analysis"
        assert strategy.enabled is True
        assert strategy.min_history == 1

    def test_config_overrides_defaults(self) -> None:
        """YAML config dict is applied over defaults."""
        config = {
            "parameters": {
                "enabled": False,
                "weight": 0.30,
                "analysis_interval_seconds": 120,
                "min_conviction": 0.60,
            }
        }
        strategy = ClaudeMarketAnalysisStrategy(config=config)
        p = strategy.params
        assert strategy.enabled is False
        assert p.weight == 0.30
        assert p.analysis_interval_seconds == 120
        assert p.min_conviction == 0.60


# ---------------------------------------------------------------------------
# 2. Validation — validate_claude_response()
# ---------------------------------------------------------------------------


class TestValidateClaudeResponse:
    """Tests for validate_claude_response() sanity checks."""

    def _store_with_atr(self) -> FeatureStore:
        """Build store with 20 samples so ATR can be computed."""
        prices = [2200.0 + i * 0.5 for i in range(20)]
        return _build_store_with_prices(prices)

    def test_valid_long_response_returns_dict(self) -> None:
        """Valid LONG raw response → returns normalised dict."""
        raw = {
            "instrument": TEST_INSTRUMENT,
            "direction": "LONG",
            "conviction": 0.75,
            "entry_price": 2201.0,
            "stop_loss": 2180.0,
            "take_profit": 2240.0,
            "time_horizon_hours": 4.0,
            "reasoning": "Bullish momentum confirmed.",
        }
        snap = _snap(mark=2200.0)
        store = self._store_with_atr()

        result = validate_claude_response(raw, snap, store)

        assert result is not None
        assert result["direction"] == PositionSide.LONG
        assert result["conviction"] == 0.75
        assert result["entry_price"] == pytest.approx(Decimal("2201.0"), abs=Decimal("0.01"))
        assert result["stop_loss"] is not None
        assert result["stop_loss"] < result["entry_price"]
        assert result["take_profit"] is not None
        assert result["take_profit"] > result["entry_price"]

    def test_no_signal_returns_no_signal_dict(self) -> None:
        """direction=NO_SIGNAL returns a tagged dict with reasoning (not None)."""
        raw = {
            "instrument": TEST_INSTRUMENT,
            "direction": "NO_SIGNAL",
            "conviction": 0.30,
            "entry_price": None,
            "stop_loss": None,
            "take_profit": None,
            "time_horizon_hours": 4.0,
            "reasoning": "No clear setup.",
        }
        snap = _snap()
        store = self._store_with_atr()

        result = validate_claude_response(raw, snap, store)
        assert result is not None
        assert result["direction"] == "NO_SIGNAL"
        assert result["reasoning"] == "No clear setup."
        assert result["conviction"] == 0.0

    def test_entry_price_too_far_from_mark_falls_back_to_mark(self) -> None:
        """entry_price 20% off mark_price → validation falls back to mark price."""
        raw = {
            "instrument": TEST_INSTRUMENT,
            "direction": "LONG",
            "conviction": 0.70,
            "entry_price": 2200.0 * 1.20,  # 20% above mark — rejected
            "stop_loss": 2100.0,
            "take_profit": 2400.0,
            "time_horizon_hours": 4.0,
            "reasoning": "hallucinated entry",
        }
        snap = _snap(mark=2200.0)
        store = self._store_with_atr()

        result = validate_claude_response(raw, snap, store)
        # Validation falls back to mark_price rather than erroring
        assert result is not None
        assert result["entry_price"] == snap.mark_price

    def test_long_stop_above_entry_is_rejected_uses_atr_default(self) -> None:
        """LONG signal with stop_loss > entry → invalid, ATR default used."""
        raw = {
            "instrument": TEST_INSTRUMENT,
            "direction": "LONG",
            "conviction": 0.65,
            "entry_price": 2200.0,
            "stop_loss": 2300.0,   # above entry — wrong for LONG
            "take_profit": 2250.0,
            "time_horizon_hours": 4.0,
            "reasoning": "bad stop",
        }
        snap = _snap(mark=2200.0)
        store = self._store_with_atr()

        result = validate_claude_response(raw, snap, store)
        assert result is not None
        # stop_loss should be below entry (ATR fallback applied)
        assert result["stop_loss"] is not None
        assert result["stop_loss"] < result["entry_price"]

    def test_short_stop_below_entry_is_rejected_uses_atr_default(self) -> None:
        """SHORT signal with stop_loss < entry → invalid, ATR default used."""
        raw = {
            "instrument": TEST_INSTRUMENT,
            "direction": "SHORT",
            "conviction": 0.65,
            "entry_price": 2200.0,
            "stop_loss": 2100.0,   # below entry — wrong for SHORT
            "take_profit": 2150.0,
            "time_horizon_hours": 4.0,
            "reasoning": "bad short stop",
        }
        snap = _snap(mark=2200.0)
        store = self._store_with_atr()

        result = validate_claude_response(raw, snap, store)
        assert result is not None
        assert result["stop_loss"] is not None
        assert result["stop_loss"] > result["entry_price"]

    def test_missing_prices_get_atr_defaults(self) -> None:
        """No entry/stop/TP in Claude response → ATR-computed defaults applied."""
        raw = {
            "instrument": TEST_INSTRUMENT,
            "direction": "LONG",
            "conviction": 0.60,
            "entry_price": None,
            "stop_loss": None,
            "take_profit": None,
            "time_horizon_hours": 4.0,
            "reasoning": "ATR defaults test",
        }
        snap = _snap(mark=2200.0)
        store = self._store_with_atr()

        result = validate_claude_response(raw, snap, store)
        assert result is not None
        # entry falls back to mark_price
        assert result["entry_price"] == snap.mark_price
        # ATR-based stop and TP should be populated (enough samples for ATR)
        assert result["stop_loss"] is not None
        assert result["take_profit"] is not None
        # LONG: stop below entry, TP above entry
        assert result["stop_loss"] < result["entry_price"]
        assert result["take_profit"] > result["entry_price"]

    def test_conviction_clamped_above_one(self) -> None:
        """conviction=1.5 from Claude is clamped to 1.0."""
        raw = {
            "instrument": TEST_INSTRUMENT,
            "direction": "LONG",
            "conviction": 1.5,  # out-of-range
            "entry_price": 2199.0,
            "stop_loss": 2160.0,
            "take_profit": 2260.0,
            "time_horizon_hours": 4.0,
            "reasoning": "overclaiming conviction",
        }
        snap = _snap(mark=2200.0)
        store = self._store_with_atr()

        result = validate_claude_response(raw, snap, store)
        assert result is not None
        assert result["conviction"] == 1.0

    def test_conviction_clamped_below_zero(self) -> None:
        """conviction=-0.3 is clamped to 0.0."""
        raw = {
            "instrument": TEST_INSTRUMENT,
            "direction": "SHORT",
            "conviction": -0.3,
            "entry_price": 2200.0,
            "stop_loss": 2250.0,
            "take_profit": 2150.0,
            "time_horizon_hours": 2.0,
            "reasoning": "negative conviction",
        }
        snap = _snap(mark=2200.0)
        store = self._store_with_atr()

        result = validate_claude_response(raw, snap, store)
        assert result is not None
        assert result["conviction"] == 0.0

    def test_invalid_direction_returns_none(self) -> None:
        """Unrecognised direction string → returns None."""
        raw = {
            "instrument": TEST_INSTRUMENT,
            "direction": "SIDEWAYS",
            "conviction": 0.7,
            "entry_price": 2200.0,
            "stop_loss": None,
            "take_profit": None,
            "time_horizon_hours": 4.0,
            "reasoning": "bad direction",
        }
        snap = _snap()
        store = self._store_with_atr()
        assert validate_claude_response(raw, snap, store) is None


# ---------------------------------------------------------------------------
# 3. Context assembly — build_market_context()
# ---------------------------------------------------------------------------


class TestBuildMarketContext:
    """Tests for build_market_context() output structure."""

    def test_context_contains_expected_sections(self) -> None:
        """Context string includes price stats, funding, OI, and regime."""
        prices = [2200.0 + i * 0.25 for i in range(30)]
        store = _build_store_with_prices(prices)
        snap = _snap(mark=2207.5, funding=0.00015, oi=90_000.0, vol_1h=0.18)
        regime = MarketRegime.TRENDING_UP

        ctx = build_market_context(TEST_INSTRUMENT, store, snap, regime)

        assert "Market Context: ETH-PERP" in ctx
        assert "Regime:" in ctx
        assert "trending_up" in ctx
        assert "Price (last 24 samples)" in ctx or "Intraday Price" in ctx
        assert "Funding Rate Trend" in ctx
        assert "Open Interest Trend" in ctx
        assert "Volatility & Orderbook" in ctx
        assert "Current Snapshot" in ctx
        assert "mark_price=" in ctx
        assert "funding_rate=" in ctx

    def test_context_with_minimal_store(self) -> None:
        """Context assembles without error when store has very few samples."""
        store = _build_store_with_prices([2200.0])
        snap = _snap()
        ctx = build_market_context(TEST_INSTRUMENT, store, snap, MarketRegime.RANGING)
        assert "ETH-PERP" in ctx
        assert "mark_price=2200.00" in ctx

    def test_build_system_prompt_returns_string(self) -> None:
        """System prompt is a non-empty string with key instructions."""
        prompt = build_system_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 100
        assert "submit_market_analysis" in prompt

    def test_market_analysis_tool_schema(self) -> None:
        """MARKET_ANALYSIS_TOOL has required fields in schema."""
        schema = MARKET_ANALYSIS_TOOL["input_schema"]
        required = schema["required"]
        for field in ("instrument", "direction", "conviction", "reasoning"):
            assert field in required


# ---------------------------------------------------------------------------
# 4. Async client — call_claude_analysis()
# ---------------------------------------------------------------------------


class TestCallClaudeAnalysis:
    """Tests for call_claude_analysis() with mocked AsyncAnthropic."""

    def _store(self) -> FeatureStore:
        prices = [2200.0 + i * 0.5 for i in range(20)]
        return _build_store_with_prices(prices)

    @pytest.mark.asyncio
    async def test_valid_long_response_returns_signal_dict(self) -> None:
        """Mock returning LONG/0.75 → validated dict with correct fields."""
        raw_response = {
            "instrument": TEST_INSTRUMENT,
            "direction": "LONG",
            "conviction": 0.75,
            "entry_price": 2201.0,
            "stop_loss": 2175.0,
            "take_profit": 2245.0,
            "time_horizon_hours": 4.0,
            "reasoning": "Strong upward momentum with positive funding.",
        }
        mock_response = _make_claude_response(raw_response)

        with (
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
            patch(
                "agents.signals.claude_market_client.anthropic.AsyncAnthropic",
            ) as mock_client,
        ):
            mock_instance = MagicMock()
            mock_instance.messages.create = AsyncMock(return_value=mock_response)
            mock_client.return_value = mock_instance

            result = await call_claude_analysis(
                instrument_id=TEST_INSTRUMENT,
                store=self._store(),
                snapshot=_snap(mark=2200.0),
                regime=MarketRegime.TRENDING_UP,
            )

        assert result is not None
        assert result["direction"] == PositionSide.LONG
        assert result["conviction"] == 0.75
        assert result["entry_price"] is not None

    @pytest.mark.asyncio
    async def test_no_signal_direction_returns_no_signal_dict(self) -> None:
        """direction=NO_SIGNAL from Claude → call returns tagged dict with reasoning."""
        raw_response = {
            "instrument": TEST_INSTRUMENT,
            "direction": "NO_SIGNAL",
            "conviction": 0.30,
            "entry_price": None,
            "stop_loss": None,
            "take_profit": None,
            "time_horizon_hours": 4.0,
            "reasoning": "No clear opportunity.",
        }
        mock_response = _make_claude_response(raw_response)

        with (
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
            patch(
                "agents.signals.claude_market_client.anthropic.AsyncAnthropic",
            ) as mock_client,
        ):
            mock_instance = MagicMock()
            mock_instance.messages.create = AsyncMock(return_value=mock_response)
            mock_client.return_value = mock_instance

            result = await call_claude_analysis(
                instrument_id=TEST_INSTRUMENT,
                store=self._store(),
                snapshot=_snap(),
                regime=MarketRegime.RANGING,
            )

        assert result is not None
        assert result["direction"] == "NO_SIGNAL"
        assert result["reasoning"] == "No clear opportunity."

    @pytest.mark.asyncio
    async def test_missing_api_key_returns_none(self) -> None:
        """No ANTHROPIC_API_KEY env var → returns None without calling API."""
        with patch.dict("os.environ", {}, clear=True):
            # Ensure ANTHROPIC_API_KEY is absent
            import os
            os.environ.pop("ANTHROPIC_API_KEY", None)

            result = await call_claude_analysis(
                instrument_id=TEST_INSTRUMENT,
                store=self._store(),
                snapshot=_snap(),
                regime=MarketRegime.RANGING,
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_api_error_returns_none_and_does_not_raise(self) -> None:
        """anthropic.APIError → call_claude_analysis returns None (logged, continues)."""
        fake_request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        api_error = anthropic.APIError(
            "Internal Server Error", fake_request, body=None
        )

        with (
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
            patch(
                "agents.signals.claude_market_client.anthropic.AsyncAnthropic",
            ) as mock_client,
        ):
            mock_instance = MagicMock()
            mock_instance.messages.create = AsyncMock(side_effect=api_error)
            mock_client.return_value = mock_instance

            # Must NOT raise; must return None
            result = await call_claude_analysis(
                instrument_id=TEST_INSTRUMENT,
                store=self._store(),
                snapshot=_snap(),
                regime=MarketRegime.HIGH_VOLATILITY,
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_missing_tool_use_block_returns_none(self) -> None:
        """Response with no tool_use block → returns None."""
        # Response with a text block only (no tool_use)
        text_block = SimpleNamespace()
        text_block.type = "text"
        text_block.text = "I cannot call the tool."

        mock_response = SimpleNamespace()
        mock_response.content = [text_block]

        with (
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
            patch(
                "agents.signals.claude_market_client.anthropic.AsyncAnthropic",
            ) as mock_client,
        ):
            mock_instance = MagicMock()
            mock_instance.messages.create = AsyncMock(return_value=mock_response)
            mock_client.return_value = mock_instance

            result = await call_claude_analysis(
                instrument_id=TEST_INSTRUMENT,
                store=self._store(),
                snapshot=_snap(),
                regime=MarketRegime.RANGING,
            )

        assert result is None


# ---------------------------------------------------------------------------
# 5. Scheduler — _process_instrument()
# ---------------------------------------------------------------------------


class TestProcessInstrument:
    """Tests for _process_instrument() — trigger logic, cooldown, enqueueing."""

    def _make_queue(self) -> asyncio.Queue[StandardSignal]:
        return asyncio.Queue(maxsize=50)

    def _default_params(self) -> ClaudeMarketAnalysisParams:
        return ClaudeMarketAnalysisParams(
            enabled=True,
            analysis_interval_seconds=240,   # 4 minutes
            min_conviction=0.50,
            route_a_min_conviction=0.75,
        )

    def _params_fn(self, params: ClaudeMarketAnalysisParams):  # noqa: ANN202
        def load(_instrument_id: str) -> ClaudeMarketAnalysisParams:
            return params
        return load

    @pytest.mark.asyncio
    async def test_cooldown_prevents_second_call(self) -> None:
        """If elapsed < min_interval, Claude is NOT called."""
        store = _build_store_with_prices([2200.0] * 5)
        queue = self._make_queue()
        snap = _snap()
        latest: dict[str, object] = {TEST_INSTRUMENT: snap}
        regime_detector = _make_regime_detector(MarketRegime.RANGING)

        # Record a call at t=0; now elapsed=30s < 180s min_interval
        last_call_time = {TEST_INSTRUMENT: 1_000_000_000.0}
        now = 1_000_000_030.0  # 30 seconds later

        mock_validated = {
            "instrument": TEST_INSTRUMENT,
            "direction": PositionSide.LONG,
            "conviction": 0.72,
            "entry_price": Decimal("2200.00"),
            "stop_loss": Decimal("2180.00"),
            "take_profit": Decimal("2240.00"),
            "time_horizon_hours": 4.0,
            "reasoning": "test",
        }

        with patch(
            "agents.signals.claude_scheduler.call_claude_analysis",
            new=AsyncMock(return_value=mock_validated),
        ) as mock_claude:
            await _process_instrument(
                instrument_id=TEST_INSTRUMENT,
                now=now,
                slow_stores={TEST_INSTRUMENT: store},
                claude_queues={TEST_INSTRUMENT: queue},
                regime_detector=regime_detector,
                last_call_time=last_call_time,
                prev_oi={},
                prev_regime={},
                prev_vol={},
                min_interval_seconds=180.0,  # 3 minutes
                vol_spike_threshold=0.20,
                oi_shift_threshold_pct=5.0,
                load_params_fn=self._params_fn(self._default_params()),
                latest_snapshots=latest,
            )

        # Claude should not have been called (cooldown)
        mock_claude.assert_not_called()
        assert queue.empty()

    @pytest.mark.asyncio
    async def test_base_interval_triggers_claude_call(self) -> None:
        """Elapsed > base_interval triggers a Claude call and enqueues signal."""
        store = _build_store_with_prices([2200.0 + i for i in range(20)])
        queue = self._make_queue()
        snap = _snap(mark=2219.0, vol_1h=0.15)
        latest: dict[str, object] = {TEST_INSTRUMENT: snap}
        regime_detector = _make_regime_detector(MarketRegime.TRENDING_UP)

        # Last call was 300s ago, base_interval=240s → should trigger
        last_call_time = {TEST_INSTRUMENT: 1_000_000_000.0}
        now = 1_000_000_300.0  # 300s later > 240s base_interval, > 30s cooldown

        mock_validated = {
            "instrument": TEST_INSTRUMENT,
            "direction": PositionSide.LONG,
            "conviction": 0.72,
            "entry_price": Decimal("2219.00"),
            "stop_loss": Decimal("2200.00"),
            "take_profit": Decimal("2250.00"),
            "time_horizon_hours": 4.0,
            "reasoning": "uptrend confirmed",
        }

        with patch(
            "agents.signals.claude_scheduler.call_claude_analysis",
            new=AsyncMock(return_value=mock_validated),
        ):
            await _process_instrument(
                instrument_id=TEST_INSTRUMENT,
                now=now,
                slow_stores={TEST_INSTRUMENT: store},
                claude_queues={TEST_INSTRUMENT: queue},
                regime_detector=regime_detector,
                last_call_time=last_call_time,
                prev_oi={},
                prev_regime={},
                prev_vol={},
                min_interval_seconds=30.0,
                vol_spike_threshold=0.20,
                oi_shift_threshold_pct=5.0,
                load_params_fn=self._params_fn(self._default_params()),
                latest_snapshots=latest,
            )

        assert not queue.empty()
        signal = queue.get_nowait()
        assert signal.source == SignalSource.CLAUDE_MARKET_ANALYSIS
        assert signal.direction == PositionSide.LONG
        assert signal.conviction == 0.72

    @pytest.mark.asyncio
    async def test_volatility_spike_triggers_early(self) -> None:
        """Volatility spike (>20% change) triggers Claude before base_interval."""
        store = _build_store_with_prices([2200.0] * 10)
        queue = self._make_queue()
        # High vol_1h snapshot — current 0.30 vs previous 0.15 → 100% spike
        snap = _snap(mark=2200.0, vol_1h=0.30)
        latest: dict[str, object] = {TEST_INSTRUMENT: snap}
        regime_detector = _make_regime_detector(MarketRegime.HIGH_VOLATILITY)

        # 60s elapsed > 30s cooldown but < 240s base_interval
        last_call_time = {TEST_INSTRUMENT: 1_000_000_000.0}
        now = 1_000_000_060.0

        mock_validated = {
            "instrument": TEST_INSTRUMENT,
            "direction": PositionSide.SHORT,
            "conviction": 0.68,
            "entry_price": Decimal("2200.00"),
            "stop_loss": Decimal("2230.00"),
            "take_profit": Decimal("2160.00"),
            "time_horizon_hours": 2.0,
            "reasoning": "vol spike breakdown",
        }

        with patch(
            "agents.signals.claude_scheduler.call_claude_analysis",
            new=AsyncMock(return_value=mock_validated),
        ):
            await _process_instrument(
                instrument_id=TEST_INSTRUMENT,
                now=now,
                slow_stores={TEST_INSTRUMENT: store},
                claude_queues={TEST_INSTRUMENT: queue},
                regime_detector=regime_detector,
                last_call_time=last_call_time,
                prev_oi={},
                prev_regime={},
                prev_vol={TEST_INSTRUMENT: 0.15},  # previous vol=0.15
                min_interval_seconds=30.0,
                vol_spike_threshold=0.20,
                oi_shift_threshold_pct=5.0,
                load_params_fn=self._params_fn(self._default_params()),
                latest_snapshots=latest,
            )

        # Signal enqueued due to vol spike trigger
        assert not queue.empty()
        signal = queue.get_nowait()
        assert signal.metadata.get("trigger") == "vol_spike"

    @pytest.mark.asyncio
    async def test_conviction_below_min_not_enqueued(self) -> None:
        """Signal with conviction < min_conviction is NOT enqueued."""
        store = _build_store_with_prices([2200.0] * 5)
        queue = self._make_queue()
        snap = _snap()
        latest: dict[str, object] = {TEST_INSTRUMENT: snap}
        regime_detector = _make_regime_detector(MarketRegime.RANGING)

        last_call_time: dict[str, float] = {}
        now = 1_000_000_400.0  # way past both cooldown and base_interval

        mock_validated = {
            "instrument": TEST_INSTRUMENT,
            "direction": PositionSide.LONG,
            "conviction": 0.30,  # below min_conviction=0.50
            "entry_price": Decimal("2200.00"),
            "stop_loss": Decimal("2180.00"),
            "take_profit": Decimal("2240.00"),
            "time_horizon_hours": 4.0,
            "reasoning": "weak signal",
        }

        params = ClaudeMarketAnalysisParams(
            analysis_interval_seconds=240,
            min_conviction=0.50,
            route_a_min_conviction=0.75,
        )

        with patch(
            "agents.signals.claude_scheduler.call_claude_analysis",
            new=AsyncMock(return_value=mock_validated),
        ):
            await _process_instrument(
                instrument_id=TEST_INSTRUMENT,
                now=now,
                slow_stores={TEST_INSTRUMENT: store},
                claude_queues={TEST_INSTRUMENT: queue},
                regime_detector=regime_detector,
                last_call_time=last_call_time,
                prev_oi={},
                prev_regime={},
                prev_vol={},
                min_interval_seconds=30.0,
                vol_spike_threshold=0.20,
                oi_shift_threshold_pct=5.0,
                load_params_fn=self._params_fn(params),
                latest_snapshots=latest,
            )

        assert queue.empty()

    @pytest.mark.asyncio
    async def test_api_error_logs_and_continues(self) -> None:
        """anthropic.APIError during scheduler call is logged, does not raise."""
        store = _build_store_with_prices([2200.0] * 5)
        queue = self._make_queue()
        snap = _snap()
        latest: dict[str, object] = {TEST_INSTRUMENT: snap}
        regime_detector = _make_regime_detector(MarketRegime.RANGING)

        last_call_time: dict[str, float] = {}
        now = 1_000_000_400.0

        # Simulate API error (we don't need to raise anthropic.APIError here;
        # call_claude_analysis() itself catches it and returns None, so the
        # scheduler receives None and logs "no_signal")
        with patch(
            "agents.signals.claude_scheduler.call_claude_analysis",
            new=AsyncMock(return_value=None),  # API error path returns None
        ):
            # This must not raise
            await _process_instrument(
                instrument_id=TEST_INSTRUMENT,
                now=now,
                slow_stores={TEST_INSTRUMENT: store},
                claude_queues={TEST_INSTRUMENT: queue},
                regime_detector=regime_detector,
                last_call_time=last_call_time,
                prev_oi={},
                prev_regime={},
                prev_vol={},
                min_interval_seconds=30.0,
                vol_spike_threshold=0.20,
                oi_shift_threshold_pct=5.0,
                load_params_fn=self._params_fn(self._default_params()),
                latest_snapshots=latest,
            )

        # Queue stays empty — no signal on error path
        assert queue.empty()

    @pytest.mark.asyncio
    async def test_no_snapshot_skips_instrument(self) -> None:
        """Missing snapshot in latest_snapshots → instrument silently skipped."""
        store = _build_store_with_prices([2200.0] * 5)
        queue = self._make_queue()
        latest: dict[str, object] = {}  # No snapshot for this instrument

        with patch(
            "agents.signals.claude_scheduler.call_claude_analysis",
            new=AsyncMock(),
        ) as mock_claude:
            await _process_instrument(
                instrument_id=TEST_INSTRUMENT,
                now=1_000_000_400.0,
                slow_stores={TEST_INSTRUMENT: store},
                claude_queues={TEST_INSTRUMENT: queue},
                regime_detector=_make_regime_detector(),
                last_call_time={},
                prev_oi={},
                prev_regime={},
                prev_vol={},
                min_interval_seconds=30.0,
                vol_spike_threshold=0.20,
                oi_shift_threshold_pct=5.0,
                load_params_fn=self._params_fn(self._default_params()),
                latest_snapshots=latest,
            )

        mock_claude.assert_not_called()

    @pytest.mark.asyncio
    async def test_high_conviction_routes_to_route_a(self) -> None:
        """conviction >= route_a_min_conviction → suggested_route=A."""
        store = _build_store_with_prices([2200.0 + i for i in range(10)])
        queue = self._make_queue()
        snap = _snap(mark=2209.0)
        latest: dict[str, object] = {TEST_INSTRUMENT: snap}
        regime_detector = _make_regime_detector(MarketRegime.TRENDING_UP)

        last_call_time: dict[str, float] = {}
        now = 1_000_000_400.0

        mock_validated = {
            "instrument": TEST_INSTRUMENT,
            "direction": PositionSide.LONG,
            "conviction": 0.80,  # >= route_a_min_conviction=0.75
            "entry_price": Decimal("2209.00"),
            "stop_loss": Decimal("2190.00"),
            "take_profit": Decimal("2250.00"),
            "time_horizon_hours": 4.0,
            "reasoning": "strong trend",
        }

        params = ClaudeMarketAnalysisParams(
            analysis_interval_seconds=240,
            min_conviction=0.50,
            route_a_min_conviction=0.75,
        )

        with patch(
            "agents.signals.claude_scheduler.call_claude_analysis",
            new=AsyncMock(return_value=mock_validated),
        ):
            await _process_instrument(
                instrument_id=TEST_INSTRUMENT,
                now=now,
                slow_stores={TEST_INSTRUMENT: store},
                claude_queues={TEST_INSTRUMENT: queue},
                regime_detector=regime_detector,
                last_call_time=last_call_time,
                prev_oi={},
                prev_regime={},
                prev_vol={},
                min_interval_seconds=30.0,
                vol_spike_threshold=0.20,
                oi_shift_threshold_pct=5.0,
                load_params_fn=self._params_fn(params),
                latest_snapshots=latest,
            )

        signal = queue.get_nowait()
        assert signal.suggested_route == Route.A

    @pytest.mark.asyncio
    async def test_low_conviction_routes_to_route_b(self) -> None:
        """conviction < route_a_min_conviction → suggested_route=None (routed to B)."""
        store = _build_store_with_prices([2200.0] * 5)
        queue = self._make_queue()
        snap = _snap()
        latest: dict[str, object] = {TEST_INSTRUMENT: snap}
        regime_detector = _make_regime_detector(MarketRegime.RANGING)

        last_call_time: dict[str, float] = {}
        now = 1_000_000_400.0

        mock_validated = {
            "instrument": TEST_INSTRUMENT,
            "direction": PositionSide.LONG,
            "conviction": 0.60,  # < route_a_min_conviction=0.75
            "entry_price": Decimal("2200.00"),
            "stop_loss": Decimal("2180.00"),
            "take_profit": Decimal("2240.00"),
            "time_horizon_hours": 4.0,
            "reasoning": "moderate signal",
        }

        params = ClaudeMarketAnalysisParams(
            analysis_interval_seconds=240,
            min_conviction=0.50,
            route_a_min_conviction=0.75,
        )

        with patch(
            "agents.signals.claude_scheduler.call_claude_analysis",
            new=AsyncMock(return_value=mock_validated),
        ):
            await _process_instrument(
                instrument_id=TEST_INSTRUMENT,
                now=now,
                slow_stores={TEST_INSTRUMENT: store},
                claude_queues={TEST_INSTRUMENT: queue},
                regime_detector=regime_detector,
                last_call_time=last_call_time,
                prev_oi={},
                prev_regime={},
                prev_vol={},
                min_interval_seconds=30.0,
                vol_spike_threshold=0.20,
                oi_shift_threshold_pct=5.0,
                load_params_fn=self._params_fn(params),
                latest_snapshots=latest,
            )

        signal = queue.get_nowait()
        assert signal.suggested_route is None  # router decides (typically Portfolio B)

    @pytest.mark.asyncio
    async def test_empty_store_skips_instrument(self) -> None:
        """Empty FeatureStore (no samples) → instrument is skipped, Claude not called."""
        store = FeatureStore(sample_interval=timedelta(seconds=0))  # empty
        queue = self._make_queue()
        snap = _snap()
        latest: dict[str, object] = {TEST_INSTRUMENT: snap}

        with patch(
            "agents.signals.claude_scheduler.call_claude_analysis",
            new=AsyncMock(),
        ) as mock_claude:
            await _process_instrument(
                instrument_id=TEST_INSTRUMENT,
                now=1_000_000_400.0,
                slow_stores={TEST_INSTRUMENT: store},
                claude_queues={TEST_INSTRUMENT: queue},
                regime_detector=_make_regime_detector(),
                last_call_time={},
                prev_oi={},
                prev_regime={},
                prev_vol={},
                min_interval_seconds=30.0,
                vol_spike_threshold=0.20,
                oi_shift_threshold_pct=5.0,
                load_params_fn=self._params_fn(self._default_params()),
                latest_snapshots=latest,
            )

        mock_claude.assert_not_called()

    @pytest.mark.asyncio
    async def test_regime_change_triggers_early(self) -> None:
        """Regime change from RANGING to TRENDING_UP triggers Claude before base_interval."""
        store = _build_store_with_prices([2200.0] * 5)
        queue = self._make_queue()
        snap = _snap()
        latest: dict[str, object] = {TEST_INSTRUMENT: snap}

        # Regime just changed: previous=RANGING, current=TRENDING_UP
        regime_detector = _make_regime_detector(MarketRegime.TRENDING_UP)

        # 60s elapsed < 240s base_interval; but regime changed → trigger
        last_call_time = {TEST_INSTRUMENT: 1_000_000_000.0}
        now = 1_000_000_060.0

        mock_validated = {
            "instrument": TEST_INSTRUMENT,
            "direction": PositionSide.LONG,
            "conviction": 0.65,
            "entry_price": Decimal("2200.00"),
            "stop_loss": Decimal("2180.00"),
            "take_profit": Decimal("2240.00"),
            "time_horizon_hours": 4.0,
            "reasoning": "regime flip",
        }

        with patch(
            "agents.signals.claude_scheduler.call_claude_analysis",
            new=AsyncMock(return_value=mock_validated),
        ):
            await _process_instrument(
                instrument_id=TEST_INSTRUMENT,
                now=now,
                slow_stores={TEST_INSTRUMENT: store},
                claude_queues={TEST_INSTRUMENT: queue},
                regime_detector=regime_detector,
                last_call_time=last_call_time,
                prev_oi={},
                prev_regime={TEST_INSTRUMENT: MarketRegime.RANGING},  # previous regime
                prev_vol={},
                min_interval_seconds=30.0,
                vol_spike_threshold=0.20,
                oi_shift_threshold_pct=5.0,
                load_params_fn=self._params_fn(self._default_params()),
                latest_snapshots=latest,
            )

        assert not queue.empty()
        signal = queue.get_nowait()
        assert signal.metadata.get("trigger") == "regime_change"
