"""Tests for agents/signals/news_client.py.

All HTTP calls are intercepted via ``respx`` — no live network calls are made.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from agents.signals.news_client import (
    CryptoHeadline,
    EconomicEvent,
    fetch_crypto_headlines,
    fetch_economic_events,
)

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

_CRYPTO_BASE_URL = "https://cryptopanic.com"
_FINNHUB_BASE_URL = "https://finnhub.io"
_FAKE_CP_KEY = "test-cp-key"
_FAKE_FH_KEY = "test-fh-key"


def _make_cp_response(n: int = 3) -> dict:
    """Build a minimal CryptoPanic /posts/ response with ``n`` items."""
    return {
        "count": n,
        "next": None,
        "previous": None,
        "results": [
            {
                "id": i,
                "title": f"Headline {i}",
                "published_at": f"2025-06-15T12:0{i}:00Z",
                "source": {"title": f"Source{i}", "domain": f"source{i}.com"},
                "currencies": [{"code": "BTC"}, {"code": "ETH"}],
            }
            for i in range(n)
        ],
    }


def _make_fh_response(items: list[dict] | None = None) -> dict:
    """Build a minimal Finnhub economic calendar response."""
    if items is None:
        items = [
            {
                "event": "CPI m/m",
                "time": "2025-06-16",
                "impact": "high",
                "country": "US",
                "estimate": "0.3",
                "prev": "0.2",
            },
            {
                "event": "Fed Funds Rate",
                "time": "2025-06-17",
                "impact": "high",
                "country": "US",
                "estimate": None,
                "prev": "5.25",
            },
        ]
    return {"economicCalendar": items}


# ---------------------------------------------------------------------------
# TestFetchCryptoHeadlines
# ---------------------------------------------------------------------------


class TestFetchCryptoHeadlines:
    """Tests for ``fetch_crypto_headlines``."""

    @pytest.mark.asyncio
    async def test_returns_parsed_headlines(self) -> None:
        """Happy path: 3-item response is fully parsed into CryptoHeadline objects."""
        with respx.mock(base_url=_CRYPTO_BASE_URL) as mock:
            mock.get("/api/developer/v2/posts/").mock(
                return_value=httpx.Response(200, json=_make_cp_response(3))
            )
            result = await fetch_crypto_headlines(api_key=_FAKE_CP_KEY, base_url=_CRYPTO_BASE_URL)

        assert len(result) == 3
        for headline in result:
            assert isinstance(headline, CryptoHeadline)

        # Spot-check first item
        h0 = result[0]
        assert h0.title == "Headline 0"
        assert isinstance(h0.published_at, datetime)
        assert h0.published_at.tzinfo is not None  # must be timezone-aware
        assert h0.source == "Source0"
        assert "BTC" in h0.currencies
        assert "ETH" in h0.currencies

    @pytest.mark.asyncio
    async def test_returns_empty_on_http_error(self) -> None:
        """HTTP 429 → empty list (no exception raised)."""
        with respx.mock(base_url=_CRYPTO_BASE_URL) as mock:
            mock.get("/api/developer/v2/posts/").mock(
                return_value=httpx.Response(429, text="Too Many Requests")
            )
            result = await fetch_crypto_headlines(api_key=_FAKE_CP_KEY, base_url=_CRYPTO_BASE_URL)

        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_missing_api_key(self) -> None:
        """Empty ``api_key`` → empty list with zero HTTP calls."""
        with respx.mock(base_url=_CRYPTO_BASE_URL, assert_all_called=False) as mock:
            route = mock.get("/api/developer/v2/posts/").mock(
                return_value=httpx.Response(200, json=_make_cp_response(1))
            )
            result = await fetch_crypto_headlines(api_key="", base_url=_CRYPTO_BASE_URL)

        assert result == []
        assert route.call_count == 0

    @pytest.mark.asyncio
    async def test_respects_max_items(self) -> None:
        """max_items=5 returns at most 5 headlines even if the API returns 20."""
        with respx.mock(base_url=_CRYPTO_BASE_URL) as mock:
            mock.get("/api/developer/v2/posts/").mock(
                return_value=httpx.Response(200, json=_make_cp_response(20))
            )
            result = await fetch_crypto_headlines(
                api_key=_FAKE_CP_KEY, max_items=5, base_url=_CRYPTO_BASE_URL
            )

        assert len(result) == 5

    @pytest.mark.asyncio
    async def test_filters_by_currencies_in_url(self) -> None:
        """currencies param is forwarded as a comma-joined query string."""
        captured_url: str | None = None

        def capture(request: httpx.Request, *_: object) -> httpx.Response:
            nonlocal captured_url
            captured_url = str(request.url)
            return httpx.Response(200, json=_make_cp_response(1))

        with respx.mock(base_url=_CRYPTO_BASE_URL) as mock:
            mock.get("/api/developer/v2/posts/").mock(side_effect=capture)
            await fetch_crypto_headlines(
                api_key=_FAKE_CP_KEY,
                currencies=["BTC", "ETH", "SOL"],
                base_url=_CRYPTO_BASE_URL,
            )

        assert captured_url is not None
        assert "currencies=BTC%2CETH%2CSOL" in captured_url or "currencies=BTC,ETH,SOL" in captured_url

    @pytest.mark.asyncio
    async def test_default_currencies_applied(self) -> None:
        """When currencies=None, defaults to BTC/ETH/SOL in the request URL."""
        captured_url: str | None = None

        def capture(request: httpx.Request, *_: object) -> httpx.Response:
            nonlocal captured_url
            captured_url = str(request.url)
            return httpx.Response(200, json=_make_cp_response(0))

        with respx.mock(base_url=_CRYPTO_BASE_URL) as mock:
            mock.get("/api/developer/v2/posts/").mock(side_effect=capture)
            await fetch_crypto_headlines(api_key=_FAKE_CP_KEY, base_url=_CRYPTO_BASE_URL)

        assert captured_url is not None
        assert "BTC" in captured_url
        assert "ETH" in captured_url
        assert "SOL" in captured_url

    @pytest.mark.asyncio
    async def test_source_falls_back_to_domain(self) -> None:
        """When source has no ``title``, ``domain`` is used as the source string."""
        body = {
            "results": [
                {
                    "id": 99,
                    "title": "Domain source test",
                    "published_at": "2025-06-15T10:00:00Z",
                    "source": {"domain": "example.com"},  # no 'title' key
                    "currencies": [],
                }
            ]
        }
        with respx.mock(base_url=_CRYPTO_BASE_URL) as mock:
            mock.get("/api/developer/v2/posts/").mock(
                return_value=httpx.Response(200, json=body)
            )
            result = await fetch_crypto_headlines(api_key=_FAKE_CP_KEY, base_url=_CRYPTO_BASE_URL)

        assert len(result) == 1
        assert result[0].source == "example.com"

    @pytest.mark.asyncio
    async def test_published_at_is_utc_aware(self) -> None:
        """published_at field must be a UTC-aware datetime."""
        with respx.mock(base_url=_CRYPTO_BASE_URL) as mock:
            mock.get("/api/developer/v2/posts/").mock(
                return_value=httpx.Response(200, json=_make_cp_response(1))
            )
            result = await fetch_crypto_headlines(api_key=_FAKE_CP_KEY, base_url=_CRYPTO_BASE_URL)

        assert result[0].published_at.tzinfo is not None


# ---------------------------------------------------------------------------
# TestFetchEconomicEvents
# ---------------------------------------------------------------------------


class TestFetchEconomicEvents:
    """Tests for ``fetch_economic_events``."""

    @pytest.mark.asyncio
    async def test_returns_parsed_events(self) -> None:
        """Happy path: 2 high-impact US events are fully parsed."""
        with respx.mock(base_url=_FINNHUB_BASE_URL) as mock:
            mock.get("/api/v1/calendar/economic").mock(
                return_value=httpx.Response(200, json=_make_fh_response())
            )
            result = await fetch_economic_events(api_key=_FAKE_FH_KEY, base_url=_FINNHUB_BASE_URL)

        assert len(result) == 2
        for ev in result:
            assert isinstance(ev, EconomicEvent)
            assert ev.impact == "high"
            assert ev.country == "US"
            assert isinstance(ev.event_time, datetime)
            assert ev.event_time.tzinfo is not None

        assert result[0].event == "CPI m/m"
        assert result[0].estimate == "0.3"
        assert result[0].previous == "0.2"
        # Second event has no estimate
        assert result[1].estimate is None

    @pytest.mark.asyncio
    async def test_filters_high_impact_us_only(self) -> None:
        """Non-US and non-high-impact entries are filtered out."""
        mixed_items = [
            {"event": "UK CPI", "time": "2025-06-16", "impact": "high", "country": "GB", "estimate": None, "prev": None},
            {"event": "US Low Impact", "time": "2025-06-16", "impact": "low", "country": "US", "estimate": None, "prev": None},
            {"event": "US Medium", "time": "2025-06-16", "impact": "medium", "country": "US", "estimate": None, "prev": None},
            {"event": "US High", "time": "2025-06-16", "impact": "high", "country": "US", "estimate": "0.5", "prev": "0.4"},
        ]
        with respx.mock(base_url=_FINNHUB_BASE_URL) as mock:
            mock.get("/api/v1/calendar/economic").mock(
                return_value=httpx.Response(200, json=_make_fh_response(mixed_items))
            )
            result = await fetch_economic_events(api_key=_FAKE_FH_KEY, base_url=_FINNHUB_BASE_URL)

        assert len(result) == 1
        assert result[0].event == "US High"
        assert result[0].country == "US"
        assert result[0].impact == "high"

    @pytest.mark.asyncio
    async def test_returns_empty_on_http_error(self) -> None:
        """HTTP 500 → empty list (no exception raised)."""
        with respx.mock(base_url=_FINNHUB_BASE_URL) as mock:
            mock.get("/api/v1/calendar/economic").mock(
                return_value=httpx.Response(500, text="Internal Server Error")
            )
            result = await fetch_economic_events(api_key=_FAKE_FH_KEY, base_url=_FINNHUB_BASE_URL)

        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_missing_api_key(self) -> None:
        """Empty ``api_key`` → empty list with zero HTTP calls."""
        with respx.mock(base_url=_FINNHUB_BASE_URL, assert_all_called=False) as mock:
            route = mock.get("/api/v1/calendar/economic").mock(
                return_value=httpx.Response(200, json=_make_fh_response())
            )
            result = await fetch_economic_events(api_key="", base_url=_FINNHUB_BASE_URL)

        assert result == []
        assert route.call_count == 0

    @pytest.mark.asyncio
    async def test_lookforward_window_in_url(self) -> None:
        """``from`` and ``to`` query params span approximately ``hours_ahead`` hours."""
        captured_url: str | None = None

        def capture(request: httpx.Request, *_: object) -> httpx.Response:
            nonlocal captured_url
            captured_url = str(request.url)
            return httpx.Response(200, json=_make_fh_response([]))

        hours_ahead = 72
        before = datetime.now(tz=UTC)

        with respx.mock(base_url=_FINNHUB_BASE_URL) as mock:
            mock.get("/api/v1/calendar/economic").mock(side_effect=capture)
            await fetch_economic_events(
                api_key=_FAKE_FH_KEY,
                hours_ahead=hours_ahead,
                base_url=_FINNHUB_BASE_URL,
            )

        after = datetime.now(tz=UTC)
        assert captured_url is not None

        # Extract the 'to' date from the URL
        from urllib.parse import parse_qs, urlparse

        qs = parse_qs(urlparse(captured_url).query)
        from_date_str = qs["from"][0]
        to_date_str = qs["to"][0]

        from_dt = datetime.strptime(from_date_str, "%Y-%m-%d").replace(tzinfo=UTC)
        to_dt = datetime.strptime(to_date_str, "%Y-%m-%d").replace(tzinfo=UTC)

        # from_date should be today (within ±1 day tolerance for DST/midnight edge cases)
        assert abs((from_dt - before).days) <= 1
        # to_date should be ~hours_ahead hours from now (allowing day-boundary rounding)
        expected_to = before + timedelta(hours=hours_ahead)
        assert abs((to_dt - expected_to).days) <= 1

    @pytest.mark.asyncio
    async def test_estimate_none_when_missing(self) -> None:
        """estimate field is None when the API item has no estimate."""
        items = [
            {
                "event": "NFP",
                "time": "2025-06-16",
                "impact": "high",
                "country": "US",
                "estimate": None,
                "prev": "150",
            }
        ]
        with respx.mock(base_url=_FINNHUB_BASE_URL) as mock:
            mock.get("/api/v1/calendar/economic").mock(
                return_value=httpx.Response(200, json=_make_fh_response(items))
            )
            result = await fetch_economic_events(api_key=_FAKE_FH_KEY, base_url=_FINNHUB_BASE_URL)

        assert len(result) == 1
        assert result[0].estimate is None
        assert result[0].previous == "150"

    @pytest.mark.asyncio
    async def test_empty_calendar_returns_empty_list(self) -> None:
        """An empty economicCalendar array → empty result."""
        with respx.mock(base_url=_FINNHUB_BASE_URL) as mock:
            mock.get("/api/v1/calendar/economic").mock(
                return_value=httpx.Response(200, json={"economicCalendar": []})
            )
            result = await fetch_economic_events(api_key=_FAKE_FH_KEY, base_url=_FINNHUB_BASE_URL)

        assert result == []
