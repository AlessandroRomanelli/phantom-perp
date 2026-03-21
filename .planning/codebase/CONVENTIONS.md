# Coding Conventions

**Analysis Date:** 2026-03-21

## Naming Patterns

**Files:**
- Lowercase with underscores: `rest_client.py`, `state_manager.py`, `pnl_calculator.py`
- Test files: `test_*.py` (always prefixed with `test_`)
- One class per file in most cases; related utilities may share a file
- Agent entry points: `main.py` within each agent directory

**Functions:**
- snake_case: `poll_portfolio()`, `compute_realized_pnl()`, `record_payment()`
- Private functions: prefixed with underscore `_prune_buffer()`, `_request()`
- Async functions named normally (async keyword indicates awaitable nature): `async def poll_portfolio()`
- Test methods: `test_<description>` with descriptive suffixes: `test_simple_profit()`, `test_old_payments_pruned()`

**Variables:**
- snake_case universally: `api_key`, `position_size`, `mark_price`, `cumulative_24h_usdc`
- Monetary amounts include currency suffix: `margin_required_usdc`, `fee_usdc`, `funding_pnl_usdc`
- Decimal amounts never use float names: use `Decimal("0.01")` not `0.01`
- Boolean flags: `is_maker`, `net_positive`, `enabled`, `reduce_only`
- Collection iterators: standard `i`, `j` for indices; descriptive names for iteration: `for order in orders`, `for fill in fills`
- Time constants: `POLL_INTERVAL = 30` (seconds), `STALE_DATA_HALT_SECONDS = 30`
- Temporary variables in factories/builders: `_payment()`, `_make_candles()` (underscore prefix for test-only helpers)

**Types:**
- PascalCase for all classes: `StandardSignal`, `MarketSnapshot`, `PerpPosition`, `CoinbaseAuth`
- Enum members: SCREAMING_SNAKE_CASE: `OrderSide.BUY`, `PositionSide.LONG`, `PortfolioTarget.A`
- Exception classes: PascalCase ending in `Error`: `PhantomPerpError`, `PortfolioMismatchError`, `RiskLimitBreachedError`
- Dataclass field types: Always fully typed with type hints
- Aliases: rarely used; when needed, PascalCase: `T0 = datetime(...)` (used in tests for timestamp constants)

## Code Style

**Formatting:**
- Ruff formatter and linter configured in `pyproject.toml`
- Line length: 100 characters (enforced, with `E501` (line too long) ignored in rules)
- Imports sorted by: standard library → third-party → first-party (`libs`, `agents`, `orchestrator`)
- `[tool.ruff.lint.isort]` defines first-party modules: `["libs", "agents", "orchestrator"]`

**Linting:**
- Ruff with strict ruleset: `["E", "F", "I", "N", "W", "UP", "B", "A", "SIM", "TCH"]`
- MyPy enabled with strict mode: `disallow_untyped_defs = true`
- Every function must have type hints on arguments and return value
- Type hints required on all class attributes and parameters
- No untyped `def` statements

**String formatting:**
- f-strings for interpolation: `f"Error on {endpoint}: {message}"`
- Raw strings for regex patterns: `r"^\d{4}-\d{2}-\d{2}"`
- Multi-line strings: triple quotes for docstrings and JSON examples

**Imports:**
- Standard library imports first
- Third-party (httpx, pydantic, structlog) second
- First-party (libs, agents, orchestrator) third
- Blank line between each group
- No wildcard imports (`from module import *`)
- Explicit imports preferred: `from datetime import datetime, timedelta, UTC`
- Conditional imports for optional dependencies (e.g., `uvloop` on non-Windows): `import uvloop; sys_platform != 'win32'`

## Error Handling

**Patterns:**
- Catch specific exceptions first, then generic `Exception`: see `agents/reconciliation/main.py` lines 158-165
- Never silently swallow exceptions — always log with full context before re-raising or handling
- Custom exception hierarchy in `libs/common/exceptions.py` with descriptive `__init__` methods
- Portfolio-mismatch errors treated as critical: `PortfolioMismatchError` includes expected/actual portfolio IDs
- Rate limit errors distinguished: `RateLimitExceededError` subclass of `CoinbaseAPIError` with `retry_after` field

**Common patterns:**
```python
try:
    result = await client.fetch_positions()
except (CoinbaseAPIError, RateLimitExceededError) as e:
    logger.warning("positions_fetch_failed", portfolio=target.value, error=str(e))
    # Return fallback or None
except Exception as e:
    logger.error("positions_fetch_error", portfolio=target.value, error=str(e))
    raise
```

- Validate inputs in constructors and `__post_init__` (see `StandardSignal.__post_init__` lines 31-35)
- Raise with descriptive messages that include context: `f"Portfolio ID mismatch: target={expected_target}..."`

## Logging

**Framework:** structlog with JSON output

**Setup:** `libs/common/logging.py` — `setup_logging(agent_name, level="INFO", json_output=True)`
- Every agent calls `setup_logging()` in its main.py
- Logger is bound with `agent_name` and automatically includes `timestamp`, `log_level`, `logger_name`

**Patterns:**
- Info level for state transitions: `logger.info("portfolio_poller_started", portfolio=label, interval=POLL_INTERVAL)`
- Warning for recoverable issues: `logger.warning("portfolio_fetch_failed", portfolio=target.value, error=str(e))`
- Error for failures that need attention: `logger.error("reconciliation_task_failed", error=str(exc), exc_type=type(exc).__name__)`
- All log calls include relevant context: portfolio, order_id, instrument, error reason
- Avoid logging secrets, passwords, or private keys (enforced by forbidden_files list)

**Structured fields:**
- Every log includes: `agent_name` (from logger binding), `timestamp` (UTC ISO format), `log_level`
- Add contextual fields: `portfolio="A"`, `target=target.value`, `order_id=order_id`, `error=str(e)`
- Event names are snake_case descriptors: `"positions_fetch_failed"`, `"order_placed_successfully"`

## Comments

**When to Comment:**
- Complex algorithms that aren't obvious from variable names (e.g., volatility calculation logic)
- Non-obvious business logic: e.g., "margin ratio < 1.0 is safe" in `PerpPosition`
- Magic numbers or thresholds: `# 30 seconds is the stale data threshold`
- Workarounds or known issues: marked with `# TODO:` or `# FIXME:` (grepped for in concerns audit)
- Do NOT comment obvious code: `x = x + 1  # increment x` is noise

**JSDoc/TSDoc (Google-style docstrings):**
- Required on all public functions and classes
- Format: Description → Args → Returns → Raises
- Example from `libs/coinbase/auth.py` line 37-54:
  ```python
  def sign(
      self,
      method: str,
      path: str,
      body: str = "",
      timestamp: str | None = None,
  ) -> dict[str, str]:
      """Generate authentication headers for a request.

      Args:
          method: HTTP method (GET, POST, DELETE, etc.). Will be uppercased.
          path: Request path including leading slash (e.g., '/api/v1/orders').
          body: Request body as a string. Empty string for GET requests.
          timestamp: Unix timestamp as string. Auto-generated if None.

      Returns:
          Dictionary of authentication headers to merge into the request.
      """
  ```
- Private functions (prefixed `_`) may have shorter docstrings or none if truly trivial
- One-liner docstrings acceptable for simple properties: `"""Human-readable strategy name."""`

## Function Design

**Size:**
- Aim for < 30 lines of code per function
- Helper functions preferred over deeply nested conditionals
- If a function needs explaining with a lengthy docstring, consider breaking it into smaller functions

**Parameters:**
- Maximum 5 positional parameters; use dataclass if more needed
- Required parameters before optional ones
- Use type hints on every parameter: `def fetch(client: CoinbaseRESTClient, target: PortfolioTarget) -> PortfolioResponse:`
- Avoid `*args` and `**kwargs` in favor of explicit parameters

**Return Values:**
- Always declare return type: `-> str`, `-> list[Fill]`, `-> dict[str, Any]`
- Return `None` explicitly if no value: `-> None` (not implicit)
- Return tuples for multiple values: `-> tuple[Decimal, Decimal, Decimal]` (e.g., `compute_fees_from_fills()`)
- Return empty collections, not None: `return []` not `return None` (for optionality use `list[X] | None`)

**Async:**
- All I/O operations are async: `async def fetch_positions(...)`, `await client.get_positions()`
- Use `asyncio.TaskGroup()` for parallel tasks: `async with asyncio.TaskGroup() as tg: ...`
- Never block the event loop with `time.sleep()`; use `await asyncio.sleep()`

## Module Design

**Exports:**
- Modules export their primary class/function without re-exporting internals
- No barrel files (`__init__.py` re-exporting all contents) except in `libs/` core modules
- `__init__.py` files are sparse: only import top-level classes if truly public API
- Private modules prefixed with underscore in very few cases; prefer directory structure instead

**Barrel Files:**
- `libs/common/models/__init__.py` may re-export common models for convenience
- `libs/portfolio/__init__.py` re-exports `PortfolioTarget` enum for convenience
- Agent `__init__.py` files are empty or minimal

**Dependencies:**
- Agents depend on `libs` (unidirectional)
- `libs` does not depend on any `agents`
- `orchestrator` orchestrates agents but does not import agent logic (spawns as subprocesses)
- Circular imports prevented by clear module hierarchy

## Decimal Usage

**All monetary values are `Decimal`:**
- Prices: `Decimal("2230.50")`
- Amounts: `Decimal("1.5")` for 1.5 ETH
- Fees, P&L, margin: always `Decimal`
- Never mix `float` and `Decimal` in calculations
- Convert from strings: `Decimal(str(value))` or `Decimal("123.45")`
- Initialize constants: `FEE_MAKER = Decimal("0.000125")`

**Why:** Floating-point precision errors accumulate in perpetual futures trading. With funding settlements every hour and positions held 24/7, cumulative errors could exceed profit. Decimal guarantees exact representation.

## Dataclasses

**Use `@dataclass` for data models:**
- All models in `libs/common/models/` are dataclasses or Pydantic models
- `frozen=True` for immutable contracts: `@dataclass(frozen=True, slots=True)`
- `slots=True` for memory efficiency (Python 3.10+)
- Include `field(default_factory=dict)` for mutable defaults, never `= {}` or `= []`
- Example: `StandardSignal` (frozen, slots) in `libs/common/models/signal.py`

**Pydantic models for external data:**
- Coinbase API responses: Pydantic models in `libs/coinbase/models.py`
- Validation and type coercion built-in
- Strict mode enforced where needed

## Type Hints

**Coverage:** Every function, class attribute, and parameter is type-hinted.

**Patterns:**
- Union types: `str | None` (Python 3.10+ syntax, not `Optional[str]`)
- Generics: `list[Fill]`, `dict[str, Any]`, `tuple[Decimal, Decimal, Decimal]`
- Callable types: `Callable[[MarketSnapshot, FeatureStore], list[StandardSignal]]` for strategy evaluate
- Self references (if needed in future): use `from __future__ import annotations` at top
- Forward references: `from __future__ import annotations` enables `-> MarketSnapshot` before the class is defined

**No type: Any usage without justification**
- Use `dict[str, Any]` only when true dynamic dispatch needed (e.g., deserializing JSON)
- Prefer explicit types where possible

## Portfolio-Aware Code

**Routing via `PortfolioTarget` enum:**
- Never use portfolio UUID strings; always use `PortfolioTarget.A` or `PortfolioTarget.B`
- Client selection: `client = CoinbaseClientPool.get_client(target)` (target is `PortfolioTarget`)
- Data models include `portfolio_target: PortfolioTarget` field for all portfolio-specific data
- Logging includes `portfolio=target.value` (converts enum to string for logs)

**Isolation patterns:**
- Each portfolio's state is fetched, stored, and processed independently
- Risk limits are checked per-portfolio: `limits = self.portfolio_a_limits` or `limits = self.portfolio_b_limits`
- Reconciliation queries both portfolios separately and publishes to separate streams
- No cross-portfolio transfers allowed: the code path for `POST /api/v1/transfers/portfolios` does not exist in the codebase

## Safety Guardrails

These are enforced at the code level and cannot be disabled:

**Global (both portfolios):**
- `MAX_LEVERAGE_GLOBAL = Decimal("5.0")` — hardcoded constant in `libs/common/constants.py`
- Stale data check: 30 seconds (`STALE_DATA_HALT_SECONDS = 30`)
- Funding rate circuit breaker: 0.05% (`FUNDING_RATE_CIRCUIT_BREAKER_PCT = Decimal("0.0005")`)

**Portfolio A specific:**
- `PORTFOLIO_A_MAX_POSITION_PCT_EQUITY = Decimal("40.0")` — max % of equity in single trade
- `PORTFOLIO_A_DAILY_LOSS_KILL_PCT = Decimal("10.0")` — halt if daily loss exceeds 10%
- `PORTFOLIO_A_MAX_DRAWDOWN_PCT = Decimal("25.0")` — halt if equity drops 25% from peak
- `PORTFOLIO_A_MIN_LIQUIDATION_DISTANCE_PCT = Decimal("8.0")` — minimum distance to liq price

**Portfolio B specific:**
- `PORTFOLIO_B_MAX_DAILY_LOSS_PCT = Decimal("5.0")` — stricter than A
- `PORTFOLIO_B_MAX_DRAWDOWN_PCT = Decimal("15.0")` — stricter than A
- `PORTFOLIO_B_MIN_LIQUIDATION_DISTANCE_PCT = Decimal("15.0")` — stricter than A
- `PORTFOLIO_B_AUTO_APPROVE_MAX_NOTIONAL_USDC = Decimal("2000")` — cap auto-approve size

All constants defined in `libs/common/constants.py` and never duplicated in agent code.

---

*Convention analysis: 2026-03-21*
