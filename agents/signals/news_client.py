"""Async HTTP client for CryptoPanic and Finnhub economic calendar APIs.

Provides two public async functions:

- ``fetch_crypto_headlines`` — fetches recent crypto news headlines from
  the CryptoPanic developer API.
- ``fetch_economic_events`` — fetches upcoming high-impact US economic
  events from the Finnhub economic calendar API.

Both functions use ``httpx.AsyncClient`` as a short-lived async context
manager, return an empty list on any error, and emit structured log
warnings so the signals agent can degrade gracefully when either API is
unavailable.

Usage::

    headlines = await fetch_crypto_headlines(api_key="...", currencies=["BTC"])
    events = await fetch_economic_events(api_key="...")
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog

_logger = structlog.get_logger(__name__)

_CRYPTOPANIC_DEFAULT_BASE_URL: str = "https://cryptopanic.com"
_FINNHUB_DEFAULT_BASE_URL: str = "https://finnhub.io"
_HTTP_TIMEOUT: float = 10.0


@dataclass(frozen=True, slots=True)
class CryptoHeadline:
    """A single crypto news headline from CryptoPanic.

    Attributes:
        title: Headline title text.
        published_at: UTC datetime when the article was published.
        source: Publication domain or name.
        currencies: List of currency symbols mentioned (e.g. ``["BTC", "ETH"]``).
    """

    title: str
    published_at: datetime
    source: str
    currencies: list[str]


@dataclass(frozen=True, slots=True)
class EconomicEvent:
    """A high-impact US economic calendar event from Finnhub.

    Attributes:
        event: Human-readable event name (e.g. "CPI m/m").
        event_time: UTC date the event is scheduled.
        impact: Impact level string (always ``"high"`` after filtering).
        country: Country code (always ``"US"`` after filtering).
        estimate: Analyst consensus estimate, if available.
        previous: Previous reading, if available.
    """

    event: str
    event_time: datetime
    impact: str
    country: str
    estimate: str | None
    previous: str | None


def _parse_iso(value: str) -> datetime:
    """Parse an ISO 8601 string to a UTC-aware datetime.

    Args:
        value: ISO 8601 timestamp string (with or without timezone).

    Returns:
        UTC-aware datetime.
    """
    # Replace trailing Z with +00:00 for fromisoformat compatibility
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _parse_date(value: str) -> datetime:
    """Parse a ``YYYY-MM-DD`` date string to a UTC-aware datetime.

    Args:
        value: Date string in ``YYYY-MM-DD`` format.

    Returns:
        UTC midnight datetime for the given date.
    """
    return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)


async def fetch_crypto_headlines(
    api_key: str,
    currencies: list[str] | None = None,
    max_items: int = 10,
    base_url: str = _CRYPTOPANIC_DEFAULT_BASE_URL,
) -> list[CryptoHeadline]:
    """Fetch recent crypto news headlines from the CryptoPanic developer API.

    Args:
        api_key: CryptoPanic developer API token.
        currencies: Currency symbols to filter by (default: ``["BTC", "ETH", "SOL"]``).
        max_items: Maximum number of headlines to return.
        base_url: Base URL for the CryptoPanic API.

    Returns:
        List of ``CryptoHeadline`` objects, empty on any error.
    """
    if currencies is None:
        currencies = ["BTC", "ETH", "SOL"]

    if not api_key:
        _logger.warning("cryptopanic_api_key_missing")
        return []

    joined_currencies = ",".join(currencies)
    url = (
        f"{base_url.rstrip('/')}/api/developer/v2/posts/"
        f"?auth_token={api_key}&currencies={joined_currencies}&kind=news&public=true"
    )

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            response = await client.get(url)
            response.raise_for_status()
            body: dict[str, Any] = response.json()

        results: list[dict[str, Any]] = body.get("results", [])
        headlines: list[CryptoHeadline] = []
        for item in results[:max_items]:
            source_info: dict[str, Any] = item.get("source") or {}
            source_title: str = str(source_info.get("title") or source_info.get("domain") or "")
            currency_entries: list[dict[str, Any]] = item.get("currencies") or []
            currency_codes: list[str] = [
                str(c.get("code", "")) for c in currency_entries if c.get("code")
            ]
            headlines.append(
                CryptoHeadline(
                    title=str(item.get("title") or ""),
                    published_at=_parse_iso(str(item["published_at"])),
                    source=source_title,
                    currencies=currency_codes,
                )
            )
        return headlines

    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "crypto_headlines_fetch_failed",
            error=str(exc),
            exc_type=type(exc).__name__,
        )
        return []


async def fetch_economic_events(
    api_key: str,
    hours_ahead: int = 48,
    base_url: str = _FINNHUB_DEFAULT_BASE_URL,
) -> list[EconomicEvent]:
    """Fetch upcoming high-impact US economic events from Finnhub.

    Args:
        api_key: Finnhub API token.
        hours_ahead: Number of hours into the future to query (default: 48).
        base_url: Base URL for the Finnhub API.

    Returns:
        List of ``EconomicEvent`` objects filtered to US + high impact,
        empty on any error.
    """
    if not api_key:
        _logger.warning("finnhub_api_key_missing")
        return []

    now = datetime.now(tz=UTC)
    from_date = now.strftime("%Y-%m-%d")
    to_date = (now + timedelta(hours=hours_ahead)).strftime("%Y-%m-%d")
    url = (
        f"{base_url.rstrip('/')}/api/v1/calendar/economic"
        f"?from={from_date}&to={to_date}&token={api_key}"
    )

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            response = await client.get(url)
            response.raise_for_status()
            body: dict[str, Any] = response.json()

        entries: list[dict[str, Any]] = body.get("economicCalendar") or []
        events: list[EconomicEvent] = []
        for item in entries:
            if item.get("country") != "US":
                continue
            if item.get("impact") != "high":
                continue
            events.append(
                EconomicEvent(
                    event=str(item.get("event") or ""),
                    event_time=_parse_date(str(item["time"])),
                    impact=str(item.get("impact") or ""),
                    country=str(item.get("country") or ""),
                    estimate=str(item["estimate"]) if item.get("estimate") is not None else None,
                    previous=str(item["prev"]) if item.get("prev") is not None else None,
                )
            )
        return events

    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "economic_events_fetch_failed",
            error=str(exc),
            exc_type=type(exc).__name__,
        )
        return []
