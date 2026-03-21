# Testing Patterns

**Analysis Date:** 2026-03-21

## Test Framework

**Runner:**
- `pytest` version 8.x + `pytest-asyncio` version 0.23.x
- Config: `pyproject.toml` lines 43-49
- Async mode: `asyncio_mode = "auto"` (no manual `@pytest.mark.asyncio` needed)

**Test paths:** `tests/`, `libs/*/tests/`, `agents/*/tests/` (pytest discovers all)

**Assertion Library:**
- pytest built-in assertions: `assert x == y`
- Approximate comparisons: `pytest.approx()` for floats: `assert imb == pytest.approx(0.8)`
- Dataclass/object equality: direct assertion (dataclasses support `==` by default)

**Run Commands:**
```bash
make test                           # All tests
make test-integration              # Only integration tests (marked with @pytest.mark.integration)
pytest tests/unit/test_coinbase_auth.py  # Single file
pytest -v                          # Verbose output
pytest --cov=libs --cov-report=html  # Coverage report
```

**Test markers:** (from `pyproject.toml`)
- `@pytest.mark.integration` — requires Docker services (Redis, Postgres, TimescaleDB)
- `@pytest.mark.e2e` — full pipeline tests (paper trade cycle)
- Unmarked tests are unit tests (no external services)

## Test File Organization

**Location:**
- Co-located with source code
- Unit tests: `agents/reconciliation/tests/test_pnl_calculator.py` (next to `pnl_calculator.py`)
- Central tests: `tests/unit/`, `tests/integration/`, `tests/e2e/`
- Each module has corresponding `test_*.py` file

**Naming:**
- `test_*.py` — test file prefix (required)
- `Test*` — test class prefix (optional but recommended for grouping)
- `test_*` — test function prefix (required)
- Descriptive names: `test_simple_profit()`, `test_old_payments_pruned()`, `test_api_key_matches()`

**Example structure:**
```
agents/reconciliation/tests/
├── __init__.py
├── test_pnl_calculator.py     # Tests for pnl_calculator.py
├── test_state_manager.py      # Tests for state_manager.py
├── test_funding_tracker.py    # Tests for funding_tracker.py
└── test_main.py               # Integration tests for main.py
```

## Test Structure

**Test class organization:**
Classes group related tests. Each test method is independent.

```python
class TestComputeFeesFromFills:
    def test_all_maker(self) -> None:
        fills = [_fill(fee=Decimal("0.50"), is_maker=True), ...]
        total, maker, taker = compute_fees_from_fills(fills)
        assert total == Decimal("0.80")
        assert maker == Decimal("0.80")
        assert taker == Decimal("0")

    def test_all_taker(self) -> None:
        # Independent test
        ...
```

**Setup patterns:**
- Test data factories: `_fill()`, `_payment()`, `_make_candles()` (prefixed with underscore)
- Factory defaults: all optional parameters with sensible defaults
- Constants for test timestamps: `T0 = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)`
- Example from `tests/unit/test_coinbase_auth.py`:

```python
@pytest.fixture
def auth() -> CoinbaseAuth:
    """Create a CoinbaseAuth with known test credentials."""
    raw_secret = b"test-secret-key-1234567890"
    b64_secret = base64.b64encode(raw_secret).decode()
    return CoinbaseAuth(
        api_key="test-api-key",
        api_secret=b64_secret,
        passphrase="test-passphrase",
    )

class TestCoinbaseAuth:
    def test_sign_returns_all_required_headers(self, auth: CoinbaseAuth) -> None:
        headers = auth.sign("GET", "/api/v1/orders", "", "1700000000")
        assert "CB-ACCESS-KEY" in headers
        ...
```

**Teardown:** Not needed (no stateful resources in unit tests; integration tests use pytest fixtures with scope)

**Assertion patterns:**
- Direct equality: `assert total == Decimal("0.80")`
- Approximate for floats: `assert imb == pytest.approx(0.0)`
- Collections: `assert len(items) == 5`, `assert item in items`
- Boolean state: `assert tracker.net_positive is True`
- Exception testing: `pytest.raises(ValueError)`

## Mocking

**Framework:** `unittest.mock` (pytest built-in)

**Mocking patterns:**

**External API mocking:**
- REST calls: `respx` library (in dev dependencies)
- Redis: `fakeredis` library (in-memory Redis mock)
- Database: `SQLAlchemy` with in-memory SQLite for integration tests

**Example — mocking Coinbase REST client (not yet in codebase but pattern is clear):**
```python
from unittest.mock import AsyncMock, patch

class TestOrderPlacement:
    async def test_order_placed_successfully(self) -> None:
        client_mock = AsyncMock(spec=CoinbaseRESTClient)
        client_mock.create_order.return_value = OrderResponse(...)

        # Now test code that uses client
        order = await place_order(client_mock, signal)
        assert order.status == OrderStatus.SENT_TO_EXCHANGE
        client_mock.create_order.assert_called_once()
```

**What to Mock:**
- External API clients (Coinbase, external data providers)
- Time-dependent functions: use `freezegun` for datetime control (in dev dependencies)
- Redis streams: use `fakeredis` for unit tests; real Redis for integration tests
- Database: in-memory SQLite for unit tests; real Postgres for integration tests

**What NOT to Mock:**
- Core business logic functions (they should be fast and deterministic)
- Dataclass constructors and validation
- Enum values
- Internal utility functions
- Actual calculation logic (P&L, margin, liquidation)

**Why:** Mocks that shadow the real code can hide bugs. Test the actual business logic with test data, not mocked behavior.

## Fixtures and Factories

**Test Data Factories:**
Location: Inside test files, prefixed with `_`

Example from `agents/reconciliation/tests/test_pnl_calculator.py`:
```python
T0 = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)

def _fill(
    side: OrderSide = OrderSide.BUY,
    size: Decimal = Decimal("1.0"),
    price: Decimal = Decimal("2200"),
    fee: Decimal = Decimal("0.55"),
    is_maker: bool = True,
    filled_at: datetime = T0,
    order_id: str = "ord-1",
) -> Fill:
    return Fill(
        fill_id=f"fill-{order_id}",
        order_id=order_id,
        portfolio_target=PortfolioTarget.A,
        instrument="ETH-PERP",
        side=side,
        size=size,
        price=price,
        fee_usdc=fee,
        is_maker=is_maker,
        filled_at=filled_at,
        trade_id=f"trade-{order_id}",
    )
```

**Pytest Fixtures:**
Location: In test files or shared `conftest.py`

Example from `tests/unit/test_coinbase_auth.py`:
```python
@pytest.fixture
def auth() -> CoinbaseAuth:
    """Create a CoinbaseAuth with known test credentials."""
    # ... setup ...
    return CoinbaseAuth(...)

class TestCoinbaseAuth:
    def test_sign_returns_all_required_headers(self, auth: CoinbaseAuth) -> None:
        # Fixture injected automatically
        headers = auth.sign("GET", "/api/v1/orders", "", "1700000000")
```

**Fixture scopes:**
- `function` (default): Fresh instance for each test method
- `class`: Reused within a test class
- `module`: Reused across all tests in a module (rarely used)
- `session`: Reused across entire test run (only for expensive resources like DB containers)

## Coverage

**Requirements:** No explicit coverage floor enforced in config, but aim for > 80% on critical paths (risk, reconciliation, execution)

**View Coverage:**
```bash
pytest --cov=libs --cov=agents --cov-report=html
# Opens htmlcov/index.html in browser
```

**What to cover:**
- All public functions
- Critical error paths
- Branch coverage for conditional logic
- Portfolio-specific code paths (separate tests for A and B where relevant)

**What's acceptable to skip:**
- Debug-only code (`if __debug__:`)
- Unreachable code branches
- External library integration stubs

## Test Types

**Unit Tests:**
- Scope: Single function or class in isolation
- Dependencies: Mocked or injected
- Speed: Should run in < 100ms each
- Location: Co-located with source or in `tests/unit/`
- Example: `test_simple_profit()` in `tests/unit/test_pnl_calculator.py` — tests `compute_realized_pnl()` with specific fills, no external calls

**Integration Tests:**
- Scope: Multiple components interacting (e.g., signal → alpha combiner → router)
- Dependencies: Real Redis Streams, possibly real database
- Marked: `@pytest.mark.integration`
- Speed: 1-10 seconds
- Location: `tests/integration/` or agent-specific `tests/`
- Example: `test_portfolio_isolation.py` — verifies orders for Portfolio A route through A's client, not B's

**E2E Tests:**
- Scope: Full pipeline (ingestion → signals → alpha → risk → execution → reconciliation)
- Dependencies: Real or simulated Coinbase (paper trading mode)
- Marked: `@pytest.mark.e2e`
- Speed: 30+ seconds (full market data ingestion cycle)
- Location: `tests/e2e/test_paper_trade_cycle.py`
- Example: Launch full pipeline, emit a signal, verify trade executes and reconciliation confirms

**Critical integration tests (non-negotiable):**
- `test_portfolio_isolation.py`: Orders tagged with `PortfolioTarget.A` must route through Portfolio A's client
- `test_no_cross_transfer.py`: Verify no code path calls `/api/v1/transfers/portfolios`
- `test_portfolio_registry.py`: Verify `PortfolioTarget` enum exists and values are correct

## Common Patterns

**Async Testing:**
Async tests are automatically detected and run by pytest-asyncio. No explicit `@pytest.mark.asyncio` needed.

```python
async def test_fetch_positions_returns_list(self) -> None:
    client = create_mock_client()
    positions = await fetch_positions(client)
    assert isinstance(positions, list)
    assert len(positions) > 0
```

**Error Testing:**
```python
class TestStandardSignal:
    def test_conviction_out_of_range_raises(self) -> None:
        with pytest.raises(ValueError, match="Conviction must be in"):
            StandardSignal(
                signal_id="sig-1",
                timestamp=utc_now(),
                instrument="ETH-PERP",
                direction=PositionSide.LONG,
                conviction=1.5,  # Invalid: > 1.0
                source=SignalSource.MOMENTUM,
                time_horizon=timedelta(hours=1),
                reasoning="test",
            )

    def test_flat_direction_raises(self) -> None:
        with pytest.raises(ValueError, match="cannot be FLAT"):
            StandardSignal(..., direction=PositionSide.FLAT, ...)
```

**Decimal Testing:**
Always compare `Decimal` values directly; never convert to float for comparison.

```python
def test_fees_exact(self) -> None:
    fills = [_fill(fee=Decimal("0.55")), _fill(fee=Decimal("1.15"))]
    total, _, _ = compute_fees_from_fills(fills)
    assert total == Decimal("1.70")  # Exact, not approximate
```

**Time-based Testing:**
Use constant timestamps for reproducibility.

```python
T0 = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)

def test_payment_timestamp(self) -> None:
    tracker = FundingTracker(portfolio_target=PortfolioTarget.A)
    payment = tracker.compute_payment(
        rate=Decimal("0.0001"),
        position_size=Decimal("2.5"),
        position_side=PositionSide.LONG,
        mark_price=Decimal("2000"),
        instrument="ETH-PERP",
        timestamp=T0,  # Predictable
    )
    assert payment.timestamp == T0
```

**Portfolio-specific Testing:**
Separate test methods for Portfolio A and B where behavior differs.

```python
class TestPortfolioLimits:
    def test_portfolio_a_allows_40_percent_equity(self) -> None:
        limits = PORTFOLIO_A_LIMITS
        assert limits.max_position_pct_equity == Decimal("40.0")

    def test_portfolio_b_allows_25_percent_equity(self) -> None:
        limits = PORTFOLIO_B_LIMITS
        assert limits.max_position_pct_equity == Decimal("25.0")
```

**Testing private functions:**
OK to test private functions (prefixed `_`) when they contain non-trivial logic. Import them directly:

```python
from agents.alpha.combiner import _BufferedSignal  # Direct import of private class for testing
```

## Running Tests Locally

**Full suite:**
```bash
make test              # All unit + integration
make test-integration  # Only integration
pytest -xvs            # Stop on first failure, verbose, no capture
```

**Single test:**
```bash
pytest agents/reconciliation/tests/test_pnl_calculator.py::TestComputeRealizedPnl::test_simple_profit -v
```

**Watch mode:** (not built-in, use external tool)
```bash
pytest-watch          # Requires pytest-watch package
```

**With coverage:**
```bash
pytest --cov=libs --cov=agents --cov-report=html --cov-fail-under=75
```

## Test Organization Summary

| Test Type | Framework | Location | Speed | External Deps |
|-----------|-----------|----------|-------|---------------|
| Unit | pytest | `agents/*/tests/test_*.py` | < 100ms | None (mocked) |
| Integration | pytest + Docker | `agents/*/tests/` or `tests/integration/` | 1-10s | Redis, Postgres |
| E2E | pytest + Docker | `tests/e2e/` | 30s+ | Full pipeline |

---

*Testing analysis: 2026-03-21*
