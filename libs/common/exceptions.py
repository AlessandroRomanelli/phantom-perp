"""Custom exception hierarchy for phantom-perp."""


class PhantomPerpError(Exception):
    """Base exception for all phantom-perp errors."""


# ── Portfolio Errors ─────────────────────────────────────────────────────


class PortfolioMismatchError(PhantomPerpError):
    """Raised when portfolio_target does not match the resolved portfolio_id.

    This is a critical safety error that should halt the system.
    """

    def __init__(self, expected_target: str, expected_id: str, actual_id: str) -> None:
        self.expected_target = expected_target
        self.expected_id = expected_id
        self.actual_id = actual_id
        super().__init__(
            f"Portfolio ID mismatch: target={expected_target} expects id={expected_id}, "
            f"got id={actual_id}. HALTING — this is a critical safety violation."
        )


class PortfolioNotConfiguredError(PhantomPerpError):
    """Raised when a portfolio ID is not configured (e.g. missing env var)."""


# ── Exchange Errors ──────────────────────────────────────────────────────


class CoinbaseAPIError(PhantomPerpError):
    """Error returned by the Coinbase Advanced API."""

    def __init__(self, status_code: int, message: str, endpoint: str) -> None:
        self.status_code = status_code
        self.endpoint = endpoint
        super().__init__(f"Coinbase API error {status_code} on {endpoint}: {message}")


class RateLimitExceededError(CoinbaseAPIError):
    """Request was rate-limited by Coinbase."""

    def __init__(self, endpoint: str, retry_after: float | None = None) -> None:
        self.retry_after = retry_after
        super().__init__(429, "Rate limit exceeded", endpoint)


class InsufficientMarginError(CoinbaseAPIError):
    """Order rejected due to insufficient margin."""


class OrderRejectedError(CoinbaseAPIError):
    """Order rejected by the exchange for a non-margin reason."""


# ── Risk Errors ──────────────────────────────────────────────────────────


class RiskLimitBreachedError(PhantomPerpError):
    """A proposed trade would breach a risk limit."""

    def __init__(self, limit_name: str, limit_value: str, actual_value: str) -> None:
        self.limit_name = limit_name
        self.limit_value = limit_value
        self.actual_value = actual_value
        super().__init__(
            f"Risk limit breached: {limit_name} limit={limit_value}, actual={actual_value}"
        )


class StaleDataError(PhantomPerpError):
    """Market data is too stale to trade on."""

    def __init__(self, age_seconds: float) -> None:
        self.age_seconds = age_seconds
        super().__init__(f"Market data is {age_seconds:.1f}s old (limit: 30s). Trading halted.")


class FundingRateCircuitBreakerError(PhantomPerpError):
    """Funding rate exceeds the circuit breaker threshold."""


# ── Data Errors ──────────────────────────────────────────────────────────


class WebSocketDisconnectedError(PhantomPerpError):
    """WebSocket connection was lost."""


class MessageDeserializationError(PhantomPerpError):
    """Failed to deserialize a message from Redis Streams."""


# ── Confirmation Errors ──────────────────────────────────────────────────


class ConfirmationTimeoutError(PhantomPerpError):
    """User did not respond within the TTL window."""

    def __init__(self, order_id: str, ttl_seconds: int) -> None:
        self.order_id = order_id
        self.ttl_seconds = ttl_seconds
        super().__init__(f"Order {order_id} expired after {ttl_seconds}s without confirmation")
