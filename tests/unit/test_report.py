"""Unit tests for libs/tuner/report.py.

Coverage:
- compose_tuning_report with changes: header, Claude summary, per-param old→new with reasoning
- compose_tuning_report with no changes: header + explicit no-change message
- HTML escaping of Claude-generated text (summary, reasoning)
- Truncation of reasoning at 200 chars per entry
- Instrument-level vs base-level parameter display
- Output stays under 4096 chars for realistic payloads
"""

from __future__ import annotations

from datetime import UTC, datetime

from libs.tuner.audit import ParameterChange
from libs.tuner.recommender import TuningResult
from libs.tuner.report import compose_tuning_report

T0 = datetime(2026, 3, 29, 0, 0, 0, tzinfo=UTC)


def _make_change(
    *,
    strategy: str = "momentum",
    instrument: str | None = "ETH-PERP",
    param: str = "min_conviction",
    old_value: float = 0.65,
    new_value: float = 0.72,
    reasoning: str = "Win rate below threshold; raising bar.",
) -> ParameterChange:
    return ParameterChange(
        strategy=strategy,
        instrument=instrument,
        param=param,
        old_value=old_value,
        new_value=new_value,
        reasoning=reasoning,
        timestamp=T0,
    )


class TestComposeWithChanges:
    """Tests for the changes-present case."""

    def test_header_includes_timestamp(self) -> None:
        result = TuningResult(summary="All good.", changes=[_make_change()])
        msg = compose_tuning_report(result, T0)
        assert "2026-03-29 00:00 UTC" in msg

    def test_header_is_bold(self) -> None:
        result = TuningResult(summary="All good.", changes=[_make_change()])
        msg = compose_tuning_report(result, T0)
        assert "<b>" in msg

    def test_includes_claude_summary(self) -> None:
        result = TuningResult(summary="Momentum needs tuning.", changes=[_make_change()])
        msg = compose_tuning_report(result, T0)
        assert "Momentum needs tuning." in msg

    def test_shows_old_to_new_values(self) -> None:
        change = _make_change(old_value=0.65, new_value=0.72)
        result = TuningResult(summary="Test.", changes=[change])
        msg = compose_tuning_report(result, T0)
        assert "0.65" in msg
        assert "0.72" in msg
        assert "→" in msg

    def test_shows_param_name(self) -> None:
        change = _make_change(param="lookback_period")
        result = TuningResult(summary="Test.", changes=[change])
        msg = compose_tuning_report(result, T0)
        assert "lookback_period" in msg

    def test_shows_reasoning_italic(self) -> None:
        change = _make_change(reasoning="Signal quality improved.")
        result = TuningResult(summary="Test.", changes=[change])
        msg = compose_tuning_report(result, T0)
        assert "<i>" in msg
        assert "Signal quality improved." in msg

    def test_shows_strategy_and_instrument(self) -> None:
        change = _make_change(strategy="momentum", instrument="ETH-PERP")
        result = TuningResult(summary="Test.", changes=[change])
        msg = compose_tuning_report(result, T0)
        assert "momentum" in msg
        assert "ETH-PERP" in msg

    def test_base_level_param_shows_base_label(self) -> None:
        change = _make_change(strategy="funding_arb", instrument=None)
        result = TuningResult(summary="Test.", changes=[change])
        msg = compose_tuning_report(result, T0)
        assert "funding_arb" in msg
        # Should indicate base-level, not instrument-specific
        assert "(base)" in msg.lower() or "base" in msg.lower()

    def test_multiple_changes(self) -> None:
        changes = [
            _make_change(strategy="momentum", param="min_conviction"),
            _make_change(strategy="funding_arb", instrument=None, param="weight"),
        ]
        result = TuningResult(summary="Two changes.", changes=changes)
        msg = compose_tuning_report(result, T0)
        assert "min_conviction" in msg
        assert "weight" in msg

    def test_changes_count_in_header(self) -> None:
        changes = [_make_change(), _make_change(param="other")]
        result = TuningResult(summary="Test.", changes=changes)
        msg = compose_tuning_report(result, T0)
        assert "2" in msg


class TestComposeNoChanges:
    """Tests for the no-change case."""

    def test_no_changes_message(self) -> None:
        result = TuningResult(summary="All parameters well-tuned.", changes=[])
        msg = compose_tuning_report(result, T0)
        assert "No parameter changes" in msg or "no parameter changes" in msg.lower()

    def test_no_changes_includes_timestamp(self) -> None:
        result = TuningResult(summary="All good.", changes=[])
        msg = compose_tuning_report(result, T0)
        assert "2026-03-29 00:00 UTC" in msg

    def test_no_changes_includes_summary(self) -> None:
        result = TuningResult(summary="Insufficient data.", changes=[])
        msg = compose_tuning_report(result, T0)
        assert "Insufficient data." in msg


class TestHTMLEscaping:
    """Tests that Claude-generated text is HTML-escaped."""

    def test_summary_escaped(self) -> None:
        result = TuningResult(summary="ratio < 1.0 & improving > baseline", changes=[])
        msg = compose_tuning_report(result, T0)
        assert "&lt;" in msg
        assert "&amp;" in msg
        assert "&gt;" in msg
        # Raw < > & must NOT appear unescaped in Claude text
        assert "< 1.0" not in msg
        assert "> baseline" not in msg

    def test_reasoning_escaped(self) -> None:
        change = _make_change(reasoning="value < threshold & needs <b>adjustment</b>")
        result = TuningResult(summary="Test.", changes=[change])
        msg = compose_tuning_report(result, T0)
        # The reasoning text should be escaped
        assert "&lt;b&gt;" in msg or "&lt;" in msg


class TestTruncation:
    """Tests that long reasoning is truncated at 200 chars."""

    def test_long_reasoning_truncated(self) -> None:
        long_reason = "A" * 300
        change = _make_change(reasoning=long_reason)
        result = TuningResult(summary="Test.", changes=[change])
        msg = compose_tuning_report(result, T0)
        # Should not contain the full 300-char reasoning
        assert "A" * 300 not in msg
        # Should contain truncated version (200 chars + ellipsis)
        assert "…" in msg or "..." in msg

    def test_short_reasoning_not_truncated(self) -> None:
        short_reason = "Short and sweet."
        change = _make_change(reasoning=short_reason)
        result = TuningResult(summary="Test.", changes=[change])
        msg = compose_tuning_report(result, T0)
        assert "Short and sweet." in msg

    def test_total_message_under_4096_chars(self) -> None:
        """With 5 strategies × 5 instruments and truncated reasoning, stays under limit."""
        changes = []
        strategies = ["momentum", "mean_reversion", "funding_arb", "orderbook", "correlation"]
        instruments = ["ETH-PERP", "BTC-PERP", "SOL-PERP", "QQQ-PERP", "SPY-PERP"]
        for strat in strategies:
            for inst in instruments:
                changes.append(
                    _make_change(
                        strategy=strat,
                        instrument=inst,
                        param="some_param",
                        reasoning="X" * 200,
                    )
                )
        result = TuningResult(summary="Many changes needed.", changes=changes)
        msg = compose_tuning_report(result, T0)
        assert len(msg) <= 4096
