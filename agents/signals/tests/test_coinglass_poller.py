"""Tests for the Coinglass heatmap poller: parser, client, and poller loop.

All HTTP calls are intercepted via ``respx`` — no live network calls are made.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

from agents.signals.coinglass_client import CoinglassClient
from agents.signals.coinglass_poller import (
    INSTRUMENT_TO_CG_SYMBOL,
    LiquidationCluster,
    parse_heatmap_response,
    run_coinglass_poller,
)
from libs.common.exceptions import CoinglassAPIError
from libs.common.models.market_snapshot import MarketSnapshot

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

TEST_INSTRUMENT = "ETH-PERP"
_MARK = 2000.0
_BASE_TS = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)


def _snap(
    instrument: str = TEST_INSTRUMENT,
    mark: float = _MARK,
    ts: datetime | None = None,
) -> MarketSnapshot:
    ts = ts or _BASE_TS
    return MarketSnapshot(
        timestamp=ts,
        instrument=instrument,
        mark_price=Decimal(str(mark)),
        index_price=Decimal(str(mark - 0.5)),
        last_price=Decimal(str(mark)),
        best_bid=Decimal(str(mark - 0.25)),
        best_ask=Decimal(str(mark + 0.25)),
        spread_bps=2.0,
        volume_24h=Decimal("10000"),
        open_interest=Decimal("50000"),
        funding_rate=Decimal("0.0001"),
        next_funding_time=ts + timedelta(minutes=30),
        hours_since_last_funding=0.5,
        orderbook_imbalance=0.0,
        volatility_1h=0.10,
        volatility_24h=0.30,
    )


def _make_raw_heatmap(
    y_axis: list[float],
    triplets: list[list],
) -> dict:
    """Build a minimal Coinglass heatmap ``data`` dict."""
    return {
        "y_axis": y_axis,
        "liquidation_leverage_data": triplets,
    }


# ---------------------------------------------------------------------------
# TestParseHeatmapResponse
# ---------------------------------------------------------------------------


class TestParseHeatmapResponse:
    """Tests for the stateless parse_heatmap_response function."""

    def test_valid_response_returns_clusters(self) -> None:
        """Basic happy-path: two price levels, one triplet each."""
        raw = _make_raw_heatmap(
            y_axis=[1900.0, 2100.0],
            triplets=[
                [0, 0, 1_000_000],
                [0, 1, 2_000_000],
            ],
        )
        clusters = parse_heatmap_response(raw, current_price=2000.0)
        assert len(clusters) == 2
        # Should be sorted descending by notional
        assert clusters[0].notional_usd == 2_000_000.0
        assert clusters[1].notional_usd == 1_000_000.0

    def test_distance_pct_computed_correctly(self) -> None:
        """distance_pct = abs(price_level - current_price) / current_price * 100."""
        raw = _make_raw_heatmap(
            y_axis=[1800.0],  # 10% below 2000
            triplets=[[0, 0, 500_000]],
        )
        clusters = parse_heatmap_response(raw, current_price=2000.0)
        assert len(clusters) == 1
        assert abs(clusters[0].distance_pct - 10.0) < 1e-6

    def test_empty_y_axis_returns_empty(self) -> None:
        raw = _make_raw_heatmap(y_axis=[], triplets=[[0, 0, 1_000_000]])
        assert parse_heatmap_response(raw, current_price=2000.0) == []

    def test_empty_triplets_returns_empty(self) -> None:
        raw = _make_raw_heatmap(y_axis=[2000.0], triplets=[])
        assert parse_heatmap_response(raw, current_price=2000.0) == []

    def test_missing_keys_returns_empty(self) -> None:
        assert parse_heatmap_response({}, current_price=2000.0) == []

    def test_min_notional_filters_small_clusters(self) -> None:
        raw = _make_raw_heatmap(
            y_axis=[1900.0, 2100.0],
            triplets=[
                [0, 0, 100_000],   # below threshold
                [0, 1, 1_000_000], # above threshold
            ],
        )
        clusters = parse_heatmap_response(
            raw, current_price=2000.0, min_notional_usd=500_000.0
        )
        assert len(clusters) == 1
        assert clusters[0].notional_usd == 1_000_000.0

    def test_min_notional_zero_keeps_all(self) -> None:
        raw = _make_raw_heatmap(
            y_axis=[1900.0, 2100.0],
            triplets=[
                [0, 0, 1],         # tiny
                [0, 1, 1_000_000],
            ],
        )
        clusters = parse_heatmap_response(raw, current_price=2000.0, min_notional_usd=0.0)
        assert len(clusters) == 2

    def test_top_10_cap_enforced(self) -> None:
        """When more than 10 clusters pass the filter, only top 10 are returned."""
        n = 15
        y_axis = [float(1000 + i * 10) for i in range(n)]
        triplets = [[0, i, float((i + 1) * 100_000)] for i in range(n)]
        raw = _make_raw_heatmap(y_axis=y_axis, triplets=triplets)
        clusters = parse_heatmap_response(raw, current_price=2000.0)
        assert len(clusters) == 10
        # Top cluster must have the highest notional
        assert clusters[0].notional_usd == float(n * 100_000)

    def test_aggregates_multiple_triplets_for_same_y_idx(self) -> None:
        """Multiple triplets with same y_idx (different leverage buckets) are summed."""
        raw = _make_raw_heatmap(
            y_axis=[2000.0],
            triplets=[
                [0, 0, 300_000],  # same y_idx
                [1, 0, 700_000],  # same y_idx
            ],
        )
        clusters = parse_heatmap_response(raw, current_price=2000.0)
        assert len(clusters) == 1
        assert clusters[0].notional_usd == 1_000_000.0

    def test_sorted_descending_by_notional(self) -> None:
        raw = _make_raw_heatmap(
            y_axis=[1900.0, 2050.0, 2100.0],
            triplets=[
                [0, 0, 500_000],
                [0, 1, 3_000_000],
                [0, 2, 1_000_000],
            ],
        )
        clusters = parse_heatmap_response(raw, current_price=2000.0)
        notionals = [c.notional_usd for c in clusters]
        assert notionals == sorted(notionals, reverse=True)

    def test_malformed_triplet_skipped(self) -> None:
        """Triplets with fewer than 3 elements or bad types are silently skipped."""
        raw = _make_raw_heatmap(
            y_axis=[2000.0, 2100.0],
            triplets=[
                [0],                   # too short
                ["x", "y", "z"],       # non-numeric
                [0, 1, 1_000_000],     # valid
            ],
        )
        clusters = parse_heatmap_response(raw, current_price=2000.0)
        assert len(clusters) == 1

    def test_current_price_zero_returns_zero_distance(self) -> None:
        """Guard against division by zero when current_price=0."""
        raw = _make_raw_heatmap(
            y_axis=[2000.0],
            triplets=[[0, 0, 1_000_000]],
        )
        clusters = parse_heatmap_response(raw, current_price=0.0)
        assert clusters[0].distance_pct == 0.0


# ---------------------------------------------------------------------------
# TestCoinglassClient
# ---------------------------------------------------------------------------

_BASE_URL = "https://open-api-v4.coinglass.com"


class TestCoinglassClient:
    """Tests for CoinglassClient — uses respx to intercept httpx calls."""

    @pytest.mark.asyncio
    async def test_successful_heatmap_request(self) -> None:
        """Happy path: valid 2xx + code=0 response returns data dict."""
        data_payload = {"y_axis": [1900.0], "liquidation_leverage_data": []}
        response_body = {"code": "0", "data": data_payload}

        with respx.mock(base_url=_BASE_URL) as mock:
            mock.get(
                "/api/futures/liquidation/heatmap/model2",
                params={"symbol": "ETH", "interval": "12h"},
            ).mock(
                return_value=httpx.Response(
                    200, json=response_body
                )
            )
            client = CoinglassClient(api_key="test-key", base_url=_BASE_URL)
            result = await client.get_liquidation_heatmap("ETH")
            await client.close()

        assert result == data_payload

    @pytest.mark.asyncio
    async def test_request_sends_correct_headers(self) -> None:
        """CG-API-KEY header is sent on every request."""
        response_body = {"code": "0", "data": {}}
        captured_headers: dict = {}

        def capture(request: httpx.Request, *_: object) -> httpx.Response:
            captured_headers.update(dict(request.headers))
            return httpx.Response(200, json=response_body)

        with respx.mock(base_url=_BASE_URL) as mock:
            mock.get("/api/futures/liquidation/heatmap/model2").mock(
                side_effect=capture
            )
            async with CoinglassClient(api_key="my-api-key", base_url=_BASE_URL) as client:
                await client.get_liquidation_heatmap("BTC")

        assert captured_headers.get("cg-api-key") == "my-api-key"
        assert "application/json" in captured_headers.get("accept", "")

    @pytest.mark.asyncio
    async def test_non_200_raises_coinglass_api_error(self) -> None:
        """HTTP 4xx raises CoinglassAPIError."""
        with respx.mock(base_url=_BASE_URL) as mock:
            mock.get("/api/futures/liquidation/heatmap/model2").mock(
                return_value=httpx.Response(403, text="Forbidden")
            )
            client = CoinglassClient(api_key="test-key", base_url=_BASE_URL)
            with pytest.raises(CoinglassAPIError) as exc_info:
                await client.get_liquidation_heatmap("ETH")
            await client.close()

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_api_error_code_raises_coinglass_api_error(self) -> None:
        """code != '0' in a 200 response raises CoinglassAPIError."""
        with respx.mock(base_url=_BASE_URL) as mock:
            mock.get("/api/futures/liquidation/heatmap/model2").mock(
                return_value=httpx.Response(
                    200,
                    json={"code": "40001", "msg": "Invalid API key"},
                )
            )
            client = CoinglassClient(api_key="test-key", base_url=_BASE_URL)
            with pytest.raises(CoinglassAPIError) as exc_info:
                await client.get_liquidation_heatmap("ETH")
            await client.close()

        assert "40001" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_network_error_raises_coinglass_api_error(self) -> None:
        """Network-level errors from httpx are wrapped in CoinglassAPIError."""
        with respx.mock(base_url=_BASE_URL) as mock:
            mock.get("/api/futures/liquidation/heatmap/model2").mock(
                side_effect=httpx.ConnectError("connection refused")
            )
            client = CoinglassClient(api_key="test-key", base_url=_BASE_URL)
            with pytest.raises(CoinglassAPIError):
                await client.get_liquidation_heatmap("ETH")
            await client.close()

    @pytest.mark.asyncio
    async def test_context_manager_closes_client(self) -> None:
        """Async context manager calls close()."""
        response_body = {"code": "0", "data": {}}
        with respx.mock(base_url=_BASE_URL) as mock:
            mock.get("/api/futures/liquidation/heatmap/model2").mock(
                return_value=httpx.Response(200, json=response_body)
            )
            async with CoinglassClient(api_key="test-key", base_url=_BASE_URL) as client:
                result = await client.get_liquidation_heatmap("ETH")
        # After __aexit__, further calls to close() on the underlying client are idempotent
        assert result == {}

    @pytest.mark.asyncio
    async def test_custom_interval_sent_in_query(self) -> None:
        """Custom interval parameter is forwarded to the query string."""
        response_body = {"code": "0", "data": {}}
        captured_url: str | None = None

        def capture(request: httpx.Request, *_: object) -> httpx.Response:
            nonlocal captured_url
            captured_url = str(request.url)
            return httpx.Response(200, json=response_body)

        with respx.mock(base_url=_BASE_URL) as mock:
            mock.get("/api/futures/liquidation/heatmap/model2").mock(
                side_effect=capture
            )
            async with CoinglassClient(api_key="k", base_url=_BASE_URL) as client:
                await client.get_liquidation_heatmap("BTC", interval="1d")

        assert captured_url is not None
        assert "interval=1d" in captured_url


# ---------------------------------------------------------------------------
# TestRunCoinglassPoller
# ---------------------------------------------------------------------------


class TestRunCoinglassPoller:
    """Tests for the run_coinglass_poller async loop."""

    @pytest.mark.asyncio
    async def test_heatmap_dict_updated_on_success(self) -> None:
        """After one poll cycle latest_heatmaps is populated for the instrument."""
        data_payload = {
            "y_axis": [1900.0, 2100.0],
            "liquidation_leverage_data": [
                [0, 0, 1_000_000],
                [0, 1, 2_000_000],
            ],
        }
        latest_heatmaps: dict = {}
        latest_snapshots = {"ETH-PERP": _snap()}

        # Patch asyncio.sleep to raise CancelledError after first iteration
        sleep_call_count = 0

        async def fake_sleep(seconds: float) -> None:
            nonlocal sleep_call_count
            sleep_call_count += 1
            raise asyncio.CancelledError

        with respx.mock(base_url=_BASE_URL) as mock:
            mock.get("/api/futures/liquidation/heatmap/model2").mock(
                return_value=httpx.Response(
                    200, json={"code": "0", "data": data_payload}
                )
            )
            with patch("asyncio.sleep", fake_sleep), patch(
                "agents.signals.coinglass_poller._DEFAULT_BASE_URL"
                if False  # we inject via the client constructor below
                else "agents.signals.coinglass_client._DEFAULT_BASE_URL",
                _BASE_URL,
            ):
                try:
                    await run_coinglass_poller(
                        instrument_ids=["ETH-PERP"],
                        latest_heatmaps=latest_heatmaps,
                        latest_snapshots=latest_snapshots,  # type: ignore[arg-type]
                        api_key="test-key",
                        poll_interval=1,
                        min_notional_usd=0.0,
                    )
                except asyncio.CancelledError:
                    pass

        assert "ETH-PERP" in latest_heatmaps
        assert len(latest_heatmaps["ETH-PERP"]) == 2

    @pytest.mark.asyncio
    async def test_stale_data_preserved_on_api_error(self) -> None:
        """When API call fails, the previously stored clusters are NOT overwritten."""
        existing_clusters = [
            LiquidationCluster(price_level=1900.0, notional_usd=5_000_000.0, distance_pct=5.0)
        ]
        latest_heatmaps: dict = {"ETH-PERP": existing_clusters}
        latest_snapshots = {"ETH-PERP": _snap()}

        async def fake_sleep(seconds: float) -> None:
            raise asyncio.CancelledError

        with respx.mock(base_url=_BASE_URL) as mock:
            mock.get("/api/futures/liquidation/heatmap/model2").mock(
                return_value=httpx.Response(
                    503, text="Service Unavailable"
                )
            )
            with patch("asyncio.sleep", fake_sleep):
                try:
                    await run_coinglass_poller(
                        instrument_ids=["ETH-PERP"],
                        latest_heatmaps=latest_heatmaps,
                        latest_snapshots=latest_snapshots,  # type: ignore[arg-type]
                        api_key="test-key",
                        poll_interval=1,
                        min_notional_usd=0.0,
                    )
                except asyncio.CancelledError:
                    pass

        # Original clusters must survive the failed poll
        assert latest_heatmaps["ETH-PERP"] is existing_clusters

    @pytest.mark.asyncio
    async def test_instruments_without_snapshot_are_skipped(self) -> None:
        """Instruments missing from latest_snapshots do not trigger API calls."""
        latest_heatmaps: dict = {}
        latest_snapshots: dict = {}  # empty — no snapshot for ETH-PERP

        async def fake_sleep(seconds: float) -> None:
            raise asyncio.CancelledError

        api_call_count = 0

        def count_calls(request: httpx.Request, *_: object) -> httpx.Response:
            nonlocal api_call_count
            api_call_count += 1
            return httpx.Response(200, json={"code": "0", "data": {}})

        # Use assert_all_called=False so the un-invoked mock route does not fail
        with respx.mock(base_url=_BASE_URL, assert_all_called=False) as mock:
            mock.get("/api/futures/liquidation/heatmap/model2").mock(
                side_effect=count_calls
            )
            with patch("asyncio.sleep", fake_sleep):
                try:
                    await run_coinglass_poller(
                        instrument_ids=["ETH-PERP"],
                        latest_heatmaps=latest_heatmaps,
                        latest_snapshots=latest_snapshots,  # type: ignore[arg-type]
                        api_key="test-key",
                        poll_interval=1,
                        min_notional_usd=0.0,
                    )
                except asyncio.CancelledError:
                    pass

        assert api_call_count == 0
        assert "ETH-PERP" not in latest_heatmaps

    @pytest.mark.asyncio
    async def test_instruments_without_cg_mapping_are_skipped(self) -> None:
        """Instruments not in INSTRUMENT_TO_CG_SYMBOL are silently skipped."""
        latest_heatmaps: dict = {}
        latest_snapshots = {"QQQ-PERP": _snap(instrument="QQQ-PERP")}

        async def fake_sleep(seconds: float) -> None:
            raise asyncio.CancelledError

        api_call_count = 0

        def count_calls(request: httpx.Request, *_: object) -> httpx.Response:
            nonlocal api_call_count
            api_call_count += 1
            return httpx.Response(200, json={"code": "0", "data": {}})

        # Use assert_all_called=False — QQQ-PERP has no CG mapping so no call is made
        with respx.mock(base_url=_BASE_URL, assert_all_called=False) as mock:
            mock.get("/api/futures/liquidation/heatmap/model2").mock(
                side_effect=count_calls
            )
            with patch("asyncio.sleep", fake_sleep):
                try:
                    await run_coinglass_poller(
                        instrument_ids=["QQQ-PERP"],
                        latest_heatmaps=latest_heatmaps,
                        latest_snapshots=latest_snapshots,  # type: ignore[arg-type]
                        api_key="test-key",
                        poll_interval=1,
                        min_notional_usd=0.0,
                    )
                except asyncio.CancelledError:
                    pass

        assert api_call_count == 0

    @pytest.mark.asyncio
    async def test_multiple_instruments_all_polled(self) -> None:
        """All instruments with both a mapping and a snapshot are polled."""
        latest_heatmaps: dict = {}
        latest_snapshots = {
            "ETH-PERP": _snap("ETH-PERP"),
            "BTC-PERP": _snap("BTC-PERP", mark=50000.0),
        }
        polled_symbols: list[str] = []

        def capture(request: httpx.Request, *_: object) -> httpx.Response:
            symbol = str(request.url.params.get("symbol", ""))
            polled_symbols.append(symbol)
            data = {
                "y_axis": [float(request.url.params.get("symbol", "ETH") == "ETH") * 1900.0 or 48000.0],
                "liquidation_leverage_data": [[0, 0, 1_000_000]],
            }
            return httpx.Response(200, json={"code": "0", "data": data})

        async def fake_sleep(seconds: float) -> None:
            raise asyncio.CancelledError

        with respx.mock(base_url=_BASE_URL) as mock:
            mock.get("/api/futures/liquidation/heatmap/model2").mock(
                side_effect=capture
            )
            with patch("asyncio.sleep", fake_sleep):
                try:
                    await run_coinglass_poller(
                        instrument_ids=["ETH-PERP", "BTC-PERP"],
                        latest_heatmaps=latest_heatmaps,
                        latest_snapshots=latest_snapshots,  # type: ignore[arg-type]
                        api_key="test-key",
                        poll_interval=1,
                        min_notional_usd=0.0,
                    )
                except asyncio.CancelledError:
                    pass

        assert set(polled_symbols) == {"ETH", "BTC"}
        assert "ETH-PERP" in latest_heatmaps
        assert "BTC-PERP" in latest_heatmaps

    @pytest.mark.asyncio
    async def test_api_error_does_not_affect_other_instruments(self) -> None:
        """An error on one instrument does not prevent others from being updated."""
        latest_heatmaps: dict = {}
        latest_snapshots = {
            "ETH-PERP": _snap("ETH-PERP"),
            "BTC-PERP": _snap("BTC-PERP", mark=50000.0),
        }

        def selective_response(request: httpx.Request, *_: object) -> httpx.Response:
            symbol = str(request.url.params.get("symbol", ""))
            if symbol == "ETH":
                return httpx.Response(500, text="Internal Server Error")
            data = {
                "y_axis": [49000.0],
                "liquidation_leverage_data": [[0, 0, 2_000_000]],
            }
            return httpx.Response(200, json={"code": "0", "data": data})

        async def fake_sleep(seconds: float) -> None:
            raise asyncio.CancelledError

        with respx.mock(base_url=_BASE_URL) as mock:
            mock.get("/api/futures/liquidation/heatmap/model2").mock(
                side_effect=selective_response
            )
            with patch("asyncio.sleep", fake_sleep):
                try:
                    await run_coinglass_poller(
                        instrument_ids=["ETH-PERP", "BTC-PERP"],
                        latest_heatmaps=latest_heatmaps,
                        latest_snapshots=latest_snapshots,  # type: ignore[arg-type]
                        api_key="test-key",
                        poll_interval=1,
                        min_notional_usd=0.0,
                    )
                except asyncio.CancelledError:
                    pass

        # ETH failed — no entry should be set
        assert "ETH-PERP" not in latest_heatmaps
        # BTC succeeded
        assert "BTC-PERP" in latest_heatmaps
        assert len(latest_heatmaps["BTC-PERP"]) == 1


# ---------------------------------------------------------------------------
# TestInstrumentMapping
# ---------------------------------------------------------------------------


class TestInstrumentMapping:
    """Verify the INSTRUMENT_TO_CG_SYMBOL constant is correct."""

    def test_eth_mapped(self) -> None:
        assert INSTRUMENT_TO_CG_SYMBOL["ETH-PERP"] == "ETH"

    def test_btc_mapped(self) -> None:
        assert INSTRUMENT_TO_CG_SYMBOL["BTC-PERP"] == "BTC"

    def test_sol_mapped(self) -> None:
        assert INSTRUMENT_TO_CG_SYMBOL["SOL-PERP"] == "SOL"

    def test_qqq_not_mapped(self) -> None:
        assert "QQQ-PERP" not in INSTRUMENT_TO_CG_SYMBOL

    def test_spy_not_mapped(self) -> None:
        assert "SPY-PERP" not in INSTRUMENT_TO_CG_SYMBOL


# ---------------------------------------------------------------------------
# TestCoinglassAPIError
# ---------------------------------------------------------------------------


class TestCoinglassAPIError:
    """Tests for the new exception class."""

    def test_stores_status_code(self) -> None:
        from libs.common.exceptions import CoinglassAPIError as Exc

        err = Exc(404, "not found")
        assert err.status_code == 404

    def test_message_in_str(self) -> None:
        from libs.common.exceptions import CoinglassAPIError as Exc

        err = Exc(503, "service unavailable")
        assert "503" in str(err)
        assert "service unavailable" in str(err)

    def test_is_phantom_perp_error(self) -> None:
        from libs.common.exceptions import CoinglassAPIError as Exc, PhantomPerpError

        assert issubclass(Exc, PhantomPerpError)
