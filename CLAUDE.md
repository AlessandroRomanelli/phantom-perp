<!-- GSD:project-start source:PROJECT.md -->
## Project

**Phantom Perp — Strategy Enhancement**

A trading strategy improvement project for the Phantom Perp system — an event-driven, multi-agent perpetual futures trading bot on Coinbase INTX. The goal is to make the existing 5 strategies smarter and more active, add proven new strategies that fill signal gaps, and tune everything per-instrument across 5 perpetual contracts (ETH, BTC, SOL, QQQ, SPY).

**Core Value:** Better signal quality and broader market coverage — the bot should trade smarter when it fires and fire more often by capturing opportunities the current strategies miss (low-vol periods, funding rate dislocations, orderbook flow, volume-based entries).

### Constraints

- **Architecture**: Must use existing `SignalStrategy` base class and `StandardSignal` contract — no changes to the signal interface
- **Data**: Limited to data already in MarketSnapshot and FeatureStore — no new data sources or API integrations
- **Risk**: Strategy changes must not weaken existing risk guardrails — risk agent and limits are untouched
- **Config**: All new parameters must be configurable via YAML in `configs/strategies/` — no hardcoded magic numbers
- **Routing**: Portfolio A routing requires `suggested_target=PortfolioTarget.A` with appropriate conviction thresholds per strategy
- **Instruments**: Per-instrument configs go in existing `configs/strategies/<strategy>.yaml` under `instruments:` key
<!-- GSD:project-end -->

<!-- GSD:stack-start source:codebase/STACK.md -->
## Technology Stack

## Languages
- Python 3.12+ - Full application codebase (agents, libraries, scripts)
## Runtime
- Python 3.13-slim base image (Docker)
- `asyncio` for concurrent event handling (24/7 operation)
- `uvloop` (0.19+) on non-Windows platforms for higher throughput
- `pip` via Hatchling build system
- Lockfile: Virtual environment via Docker isolation
- Production: Pre-built Docker images for `linux/amd64`
## Frameworks
- `python-telegram-bot` (21+) - Telegram bot integration for Portfolio B confirmations
- `httpx` (0.27+) - Async REST client for Coinbase INTX API
- `websockets` (13+) - Async WebSocket client for Coinbase real-time feeds
- `polars` (1.0+) - Primary for speed-critical 24/7 data paths
- `pandas` - Compatibility/fallback (used via polars where possible)
- `pydantic` (2.6+) - Data validation for all models
- `pydantic-settings` (2.2+) - Environment variable and config file loading
- `PyYAML` (6+) - Strategy and instrument YAML config parsing
- `SQLAlchemy[asyncio]` (2.0+) - Async ORM for PostgreSQL
- `asyncpg` (0.29+) - Native async PostgreSQL driver
- `redis` (5.0+) - Async client for Redis Streams and caching
- `orjson` (3.9+) - Fast JSON encoding/decoding for message payloads and WebSocket
- `ta-lib` (0.4+) - TA-Lib C library compiled in Docker
- `numpy` (1.26+) - Numerical computation for indicators
- `scikit-learn` (1.4+) - ML algorithms for strategy features
- `xgboost` (2+) - Gradient boosting for ensemble models
- `pytest` (8+) - Test runner with async support
- `pytest-asyncio` (0.23+) - Async test fixture support
- `pytest-cov` (5+) - Code coverage reporting
- `respx` (0.21+) - Mock `httpx` requests for unit tests
- `fakeredis` (2.21+) - In-memory Redis mock for integration tests
- `freezegun` (1.4+) - Time freezing for deterministic tests
- `ruff` (0.3+) - Fast linter/formatter (configured in `pyproject.toml`)
- `mypy` (1.9+) - Static type checker (strict mode)
- `hatchling` - Build backend
- Docker & Docker Compose - Local dev and production orchestration
- `Dockerfile` per agent with layer caching optimization
## Key Dependencies
- `httpx` + `websockets` - Exchange connectivity (Coinbase INTX)
- `redis` - Message broker (Redis Streams)
- `SQLAlchemy` + `asyncpg` - Persistent state (PostgreSQL + TimescaleDB)
- `python-telegram-bot` - Portfolio B confirmations (user interaction)
- `structlog` (24.1+) - Structured JSON logging across all agents
- `orjson` - Serialization for Redis Streams payloads
- `uvloop` - High-performance event loop (non-Windows)
- `polars` - DataFrame operations for signal generation
- `ta-lib` - Technical indicators (pre-compiled C binary in Docker)
- `numpy` - Numerical arrays
- `scikit-learn` + `xgboost` - ML models for strategy prediction
## Configuration
- Configuration via `.env` file (environment variables)
- Runtime overrides via `configs/default.yaml` (YAML)
- Per-strategy configs in `configs/strategies/` (strategy-specific parameters)
- `COINBASE_INTX_API_KEY_A`, `API_SECRET_A`, `PASSPHRASE_A` - Portfolio A credentials
- `COINBASE_INTX_API_KEY_B`, `API_SECRET_B`, `PASSPHRASE_B` - Portfolio B credentials
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` - Telegram bot setup
- `REDIS_URL` - Redis connection (default: `redis://localhost:6379`)
- `DATABASE_URL` - PostgreSQL connection (default: `postgresql://phantom:phantom_dev@localhost:5432/phantom_perp`)
- `ENVIRONMENT` - Operating mode (`paper` or `live`)
- `LOG_LEVEL` - Logging verbosity (INFO, DEBUG, etc.)
- `pyproject.toml` - Python project metadata and dependencies
- `docker-compose.yml` - Local development orchestration (build from source)
- `docker-compose.prod.yml` - Production orchestration (pre-built images, memory-constrained: 64MB Redis, 128MB Postgres effective cache)
## Platform Requirements
- Python 3.12+
- Docker & Docker Compose
- ~500MB RAM minimum (single container)
- Internet connectivity for Coinbase INTX API
- Linux `x86_64` (amd64) — images pre-compiled for this architecture
- Docker runtime
- Redis 7-alpine (in-memory, 64MB max with LRU eviction)
- PostgreSQL 16 with TimescaleDB extension (in-container, 128MB effective cache)
- ~4GB total swap space (for 8 agents + 2 services on Oracle Always Free instance)
- 24/7 uptime requirement (no market hours)
- Oracle Cloud Always Free AMD instance (140.238.222.244)
- Images cross-compiled on Apple Silicon for Linux target via Docker buildx
- Transfer via SCP to production host at `opc@140.238.222.244`
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->
## Conventions

## Naming Patterns
- Lowercase with underscores: `rest_client.py`, `state_manager.py`, `pnl_calculator.py`
- Test files: `test_*.py` (always prefixed with `test_`)
- One class per file in most cases; related utilities may share a file
- Agent entry points: `main.py` within each agent directory
- snake_case: `poll_portfolio()`, `compute_realized_pnl()`, `record_payment()`
- Private functions: prefixed with underscore `_prune_buffer()`, `_request()`
- Async functions named normally (async keyword indicates awaitable nature): `async def poll_portfolio()`
- Test methods: `test_<description>` with descriptive suffixes: `test_simple_profit()`, `test_old_payments_pruned()`
- snake_case universally: `api_key`, `position_size`, `mark_price`, `cumulative_24h_usdc`
- Monetary amounts include currency suffix: `margin_required_usdc`, `fee_usdc`, `funding_pnl_usdc`
- Decimal amounts never use float names: use `Decimal("0.01")` not `0.01`
- Boolean flags: `is_maker`, `net_positive`, `enabled`, `reduce_only`
- Collection iterators: standard `i`, `j` for indices; descriptive names for iteration: `for order in orders`, `for fill in fills`
- Time constants: `POLL_INTERVAL = 30` (seconds), `STALE_DATA_HALT_SECONDS = 30`
- Temporary variables in factories/builders: `_payment()`, `_make_candles()` (underscore prefix for test-only helpers)
- PascalCase for all classes: `StandardSignal`, `MarketSnapshot`, `PerpPosition`, `CoinbaseAuth`
- Enum members: SCREAMING_SNAKE_CASE: `OrderSide.BUY`, `PositionSide.LONG`, `PortfolioTarget.A`
- Exception classes: PascalCase ending in `Error`: `PhantomPerpError`, `PortfolioMismatchError`, `RiskLimitBreachedError`
- Dataclass field types: Always fully typed with type hints
- Aliases: rarely used; when needed, PascalCase: `T0 = datetime(...)` (used in tests for timestamp constants)
## Code Style
- Ruff formatter and linter configured in `pyproject.toml`
- Line length: 100 characters (enforced, with `E501` (line too long) ignored in rules)
- Imports sorted by: standard library → third-party → first-party (`libs`, `agents`, `orchestrator`)
- `[tool.ruff.lint.isort]` defines first-party modules: `["libs", "agents", "orchestrator"]`
- Ruff with strict ruleset: `["E", "F", "I", "N", "W", "UP", "B", "A", "SIM", "TCH"]`
- MyPy enabled with strict mode: `disallow_untyped_defs = true`
- Every function must have type hints on arguments and return value
- Type hints required on all class attributes and parameters
- No untyped `def` statements
- f-strings for interpolation: `f"Error on {endpoint}: {message}"`
- Raw strings for regex patterns: `r"^\d{4}-\d{2}-\d{2}"`
- Multi-line strings: triple quotes for docstrings and JSON examples
- Standard library imports first
- Third-party (httpx, pydantic, structlog) second
- First-party (libs, agents, orchestrator) third
- Blank line between each group
- No wildcard imports (`from module import *`)
- Explicit imports preferred: `from datetime import datetime, timedelta, UTC`
- Conditional imports for optional dependencies (e.g., `uvloop` on non-Windows): `import uvloop; sys_platform != 'win32'`
## Error Handling
- Catch specific exceptions first, then generic `Exception`: see `agents/reconciliation/main.py` lines 158-165
- Never silently swallow exceptions — always log with full context before re-raising or handling
- Custom exception hierarchy in `libs/common/exceptions.py` with descriptive `__init__` methods
- Portfolio-mismatch errors treated as critical: `PortfolioMismatchError` includes expected/actual portfolio IDs
- Rate limit errors distinguished: `RateLimitExceededError` subclass of `CoinbaseAPIError` with `retry_after` field
- Validate inputs in constructors and `__post_init__` (see `StandardSignal.__post_init__` lines 31-35)
- Raise with descriptive messages that include context: `f"Portfolio ID mismatch: target={expected_target}..."`
## Logging
- Every agent calls `setup_logging()` in its main.py
- Logger is bound with `agent_name` and automatically includes `timestamp`, `log_level`, `logger_name`
- Info level for state transitions: `logger.info("portfolio_poller_started", portfolio=label, interval=POLL_INTERVAL)`
- Warning for recoverable issues: `logger.warning("portfolio_fetch_failed", portfolio=target.value, error=str(e))`
- Error for failures that need attention: `logger.error("reconciliation_task_failed", error=str(exc), exc_type=type(exc).__name__)`
- All log calls include relevant context: portfolio, order_id, instrument, error reason
- Avoid logging secrets, passwords, or private keys (enforced by forbidden_files list)
- Every log includes: `agent_name` (from logger binding), `timestamp` (UTC ISO format), `log_level`
- Add contextual fields: `portfolio="A"`, `target=target.value`, `order_id=order_id`, `error=str(e)`
- Event names are snake_case descriptors: `"positions_fetch_failed"`, `"order_placed_successfully"`
## Comments
- Complex algorithms that aren't obvious from variable names (e.g., volatility calculation logic)
- Non-obvious business logic: e.g., "margin ratio < 1.0 is safe" in `PerpPosition`
- Magic numbers or thresholds: `# 30 seconds is the stale data threshold`
- Workarounds or known issues: marked with `# TODO:` or `# FIXME:` (grepped for in concerns audit)
- Do NOT comment obvious code: `x = x + 1  # increment x` is noise
- Required on all public functions and classes
- Format: Description → Args → Returns → Raises
- Example from `libs/coinbase/auth.py` line 37-54:
- Private functions (prefixed `_`) may have shorter docstrings or none if truly trivial
- One-liner docstrings acceptable for simple properties: `"""Human-readable strategy name."""`
## Function Design
- Aim for < 30 lines of code per function
- Helper functions preferred over deeply nested conditionals
- If a function needs explaining with a lengthy docstring, consider breaking it into smaller functions
- Maximum 5 positional parameters; use dataclass if more needed
- Required parameters before optional ones
- Use type hints on every parameter: `def fetch(client: CoinbaseRESTClient, target: PortfolioTarget) -> PortfolioResponse:`
- Avoid `*args` and `**kwargs` in favor of explicit parameters
- Always declare return type: `-> str`, `-> list[Fill]`, `-> dict[str, Any]`
- Return `None` explicitly if no value: `-> None` (not implicit)
- Return tuples for multiple values: `-> tuple[Decimal, Decimal, Decimal]` (e.g., `compute_fees_from_fills()`)
- Return empty collections, not None: `return []` not `return None` (for optionality use `list[X] | None`)
- All I/O operations are async: `async def fetch_positions(...)`, `await client.get_positions()`
- Use `asyncio.TaskGroup()` for parallel tasks: `async with asyncio.TaskGroup() as tg: ...`
- Never block the event loop with `time.sleep()`; use `await asyncio.sleep()`
## Module Design
- Modules export their primary class/function without re-exporting internals
- No barrel files (`__init__.py` re-exporting all contents) except in `libs/` core modules
- `__init__.py` files are sparse: only import top-level classes if truly public API
- Private modules prefixed with underscore in very few cases; prefer directory structure instead
- `libs/common/models/__init__.py` may re-export common models for convenience
- `libs/portfolio/__init__.py` re-exports `PortfolioTarget` enum for convenience
- Agent `__init__.py` files are empty or minimal
- Agents depend on `libs` (unidirectional)
- `libs` does not depend on any `agents`
- `orchestrator` orchestrates agents but does not import agent logic (spawns as subprocesses)
- Circular imports prevented by clear module hierarchy
## Decimal Usage
- Prices: `Decimal("2230.50")`
- Amounts: `Decimal("1.5")` for 1.5 ETH
- Fees, P&L, margin: always `Decimal`
- Never mix `float` and `Decimal` in calculations
- Convert from strings: `Decimal(str(value))` or `Decimal("123.45")`
- Initialize constants: `FEE_MAKER = Decimal("0.000125")`
## Dataclasses
- All models in `libs/common/models/` are dataclasses or Pydantic models
- `frozen=True` for immutable contracts: `@dataclass(frozen=True, slots=True)`
- `slots=True` for memory efficiency (Python 3.10+)
- Include `field(default_factory=dict)` for mutable defaults, never `= {}` or `= []`
- Example: `StandardSignal` (frozen, slots) in `libs/common/models/signal.py`
- Coinbase API responses: Pydantic models in `libs/coinbase/models.py`
- Validation and type coercion built-in
- Strict mode enforced where needed
## Type Hints
- Union types: `str | None` (Python 3.10+ syntax, not `Optional[str]`)
- Generics: `list[Fill]`, `dict[str, Any]`, `tuple[Decimal, Decimal, Decimal]`
- Callable types: `Callable[[MarketSnapshot, FeatureStore], list[StandardSignal]]` for strategy evaluate
- Self references (if needed in future): use `from __future__ import annotations` at top
- Forward references: `from __future__ import annotations` enables `-> MarketSnapshot` before the class is defined
- Use `dict[str, Any]` only when true dynamic dispatch needed (e.g., deserializing JSON)
- Prefer explicit types where possible
## Portfolio-Aware Code
- Never use portfolio UUID strings; always use `PortfolioTarget.A` or `PortfolioTarget.B`
- Client selection: `client = CoinbaseClientPool.get_client(target)` (target is `PortfolioTarget`)
- Data models include `portfolio_target: PortfolioTarget` field for all portfolio-specific data
- Logging includes `portfolio=target.value` (converts enum to string for logs)
- Each portfolio's state is fetched, stored, and processed independently
- Risk limits are checked per-portfolio: `limits = self.portfolio_a_limits` or `limits = self.portfolio_b_limits`
- Reconciliation queries both portfolios separately and publishes to separate streams
- No cross-portfolio transfers allowed: the code path for `POST /api/v1/transfers/portfolios` does not exist in the codebase
## Safety Guardrails
- `MAX_LEVERAGE_GLOBAL = Decimal("5.0")` — hardcoded constant in `libs/common/constants.py`
- Stale data check: 30 seconds (`STALE_DATA_HALT_SECONDS = 30`)
- Funding rate circuit breaker: 0.05% (`FUNDING_RATE_CIRCUIT_BREAKER_PCT = Decimal("0.0005")`)
- `PORTFOLIO_A_MAX_POSITION_PCT_EQUITY = Decimal("40.0")` — max % of equity in single trade
- `PORTFOLIO_A_DAILY_LOSS_KILL_PCT = Decimal("10.0")` — halt if daily loss exceeds 10%
- `PORTFOLIO_A_MAX_DRAWDOWN_PCT = Decimal("25.0")` — halt if equity drops 25% from peak
- `PORTFOLIO_A_MIN_LIQUIDATION_DISTANCE_PCT = Decimal("8.0")` — minimum distance to liq price
- `PORTFOLIO_B_MAX_DAILY_LOSS_PCT = Decimal("5.0")` — stricter than A
- `PORTFOLIO_B_MAX_DRAWDOWN_PCT = Decimal("15.0")` — stricter than A
- `PORTFOLIO_B_MIN_LIQUIDATION_DISTANCE_PCT = Decimal("15.0")` — stricter than A
- `PORTFOLIO_B_AUTO_APPROVE_MAX_NOTIONAL_USDC = Decimal("2000")` — cap auto-approve size
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->
## Architecture

## Pattern Overview
- Multi-stage asynchronous pipeline: each stage is an independent agent communicating via Redis Streams
- Dual-portfolio architecture with physically isolated margin and API key scoping
- Portfolio-aware routing: signals flow through risk limits specific to each portfolio
- 24/7 operational model with no market-close downtime
- Safety-critical architecture with non-negotiable guardrails (hardcoded, not configurable)
## Layers
- Purpose: Ingest real-time market data (orderbook, trades, candles, funding rates) from Coinbase INTX and external enrichment sources
- Location: `agents/ingestion/`
- Contains: WebSocket and REST polling modules that consume Coinbase APIs and external data providers, normalize to unified `MarketSnapshot` model
- Depends on: Coinbase REST/WS clients (`libs/coinbase/`), Redis for publishing
- Used by: Signal generation strategies (consume `MarketSnapshot` from Redis stream)
- Key files: `agents/ingestion/main.py` orchestrates concurrent sources; `agents/ingestion/sources/` contains individual pollers (candles, funding_rate, ws_market_data); `agents/ingestion/normalizer.py` builds `MarketSnapshot`
- Purpose: Run all trading strategies in parallel, emit `StandardSignal` objects
- Location: `agents/signals/`
- Contains: Strategy implementations (`agents/signals/strategies/`), feature store for indicator computation, strategy orchestrator
- Depends on: Market snapshots from ingestion, strategy configs, technical indicator library (`libs/indicators/`)
- Used by: Alpha combiner consumes `StandardSignal` from Redis stream
- Key files: `agents/signals/main.py` runs all strategies; `agents/signals/strategies/` contains base class and implementations (momentum, mean_reversion, funding_arb, orderbook_imbalance, liquidation_cascade, correlation, regime_trend); `agents/signals/feature_store.py` maintains per-instrument rolling window buffers
- Purpose: Combine multiple signals, resolve conflicts, route to portfolios, rank by conviction
- Location: `agents/alpha/`
- Contains: Alpha combiner (weighted aggregation), regime detector, strategy scorecard (rolling accuracy tracking), conflict resolver, portfolio router
- Depends on: Signals and market snapshots from Redis, configuration for weighting rules
- Used by: Risk agent consumes `RankedTradeIdea` from portfolio-scoped streams
- Key files: `agents/alpha/main.py` orchestrates flow; `agents/alpha/combiner.py` performs aggregation; `agents/alpha/regime_detector.py` classifies market regime; `agents/alpha/scorecard.py` tracks per-strategy accuracy; `libs/portfolio/router.py` routes by time horizon, conviction, and strategy type
- Purpose: Validate trade ideas against portfolio-specific risk limits, compute position sizing, margin requirements, liquidation prices
- Location: `agents/risk/`
- Contains: Risk engine (deterministic evaluation), margin calculator, liquidation guard, position sizer, fee/funding cost estimators, limits registry
- Depends on: Ranked ideas from alpha, live portfolio state from reconciliation, market data
- Used by: Execution and confirmation agents consume approved orders from portfolio-scoped streams
- Key files: `agents/risk/main.py` orchestrates validation; `agents/risk/limits.py` defines separate limit sets for Portfolio A (aggressive) and B (conservative); `agents/risk/margin_calculator.py` computes initial/maintenance margin and liquidation distance; `agents/risk/position_sizer.py` sizes based on equity and risk budget
- Purpose: Present orders to user via Telegram for approval before execution
- Location: `agents/confirmation/`
- Contains: Telegram bot setup, message composition, callback handler, state machine for order lifecycle, timeout manager
- Depends on: Approved orders from risk agent (Portfolio B stream), Telegram API
- Used by: Execution agent consumes confirmed orders from unified stream
- Key files: `agents/confirmation/main.py` runs Telegram bot; `agents/confirmation/state_machine.py` manages order states (pending_confirmation → confirmed → sent_to_exchange); `agents/confirmation/message_composer.py` formats rich trade notifications with inline keyboards
- Purpose: Place orders on Coinbase via the correct portfolio's API client, monitor fills, manage stop-loss orders
- Location: `agents/execution/`
- Contains: Order placer (routes via `CoinbaseClientPool`), algo selector, fill monitor, retry handler, circuit breaker, stop-loss manager
- Depends on: Approved (Portfolio A) and confirmed (Portfolio B) orders from portfolio-scoped/unified streams, Coinbase REST client pool
- Used by: Reconciliation consumes fills from portfolio-scoped streams
- Key files: `agents/execution/main.py` subscribes to approved_orders:a and confirmed_orders; `agents/execution/order_placer.py` calls `CoinbaseClientPool.get_client(target)` to route via correct API key; `agents/execution/circuit_breaker.py` pauses on adverse conditions; `agents/execution/stop_loss_manager.py` places protective orders
- Purpose: Query Coinbase for portfolio state (equity, margin, positions), track hourly funding payments, detect discrepancies with internal state
- Location: `agents/reconciliation/`
- Contains: State manager, Coinbase reconciler, funding tracker, P&L calculator
- Depends on: Fills from execution, Coinbase REST API (queried via both portfolio clients independently)
- Used by: Risk agent consumes portfolio state to validate new trades; monitoring consumes state and funding for reporting
- Key files: `agents/reconciliation/main.py` polls each portfolio independently and publishes to portfolio-scoped streams; `agents/reconciliation/state_manager.py` builds `PortfolioSnapshot` from Coinbase response; `agents/reconciliation/funding_tracker.py` tracks hourly USDC settlement payments
- Purpose: Track performance (P&L, Sharpe, drawdown), report funding/fees, generate alerts, enforce kill switches
- Location: `agents/monitoring/`
- Contains: Health checker, performance tracker (separate per portfolio), funding reporter, fee reporter, alerting engine
- Depends on: Portfolio state, funding payments, fills from reconciliation
- Used by: Alerts published to unified stream for all agents to consume
- Key files: `agents/monitoring/main.py` orchestrates; `agents/monitoring/performance_tracker.py` computes portfolio-specific metrics; `agents/monitoring/alerting.py` enforces daily loss and drawdown kill switches; `agents/monitoring/funding_report.py` aggregates hourly funding events
- Purpose: Shared libraries, messaging, portfolio routing, storage abstractions
- Location: `libs/`
- Contains: Common models (`libs/common/models/`), Coinbase client abstraction (`libs/coinbase/`), Redis Streams messaging (`libs/messaging/`), technical indicators (`libs/indicators/`), portfolio routing logic (`libs/portfolio/`)
- Key files: `libs/common/models/` defines core contracts (StandardSignal, ProposedOrder, PerpPosition, PortfolioSnapshot); `libs/coinbase/client_pool.py` routes API calls by portfolio; `libs/messaging/channels.py` defines Redis stream names; `libs/portfolio/router.py` applies routing rules
- Purpose: Manage agent lifecycle, startup/shutdown order, circuit breakers, watchdog health checks
- Location: `orchestrator/`
- Contains: Pipeline DAG definition, circuit breakers (per-portfolio and global), watchdog for agent crashes
- Key files: `orchestrator/dag.py` defines agent dependencies and startup order; `orchestrator/circuit_breakers.py` implements kill switches for daily loss and max drawdown; `orchestrator/main.py` manages container lifecycle
## Data Flow
- **Signal Source** → Alpha Combiner: `RankedTradeIdea` is routed to Portfolio A or B based on signal properties (time horizon, conviction, source type)
- **Order Execution** → Execution Agent: `CoinbaseClientPool.get_client(portfolio_target)` selects the correct REST client authenticated with that portfolio's API key
- **Portfolio State** → Risk & Reconciliation: Each agent independently queries or consumes data for Portfolio A and Portfolio B; no cross-portfolio operations
- **Market State**: Held in Redis Streams (immutable, append-only); Signals agent maintains per-instrument rolling buffer in memory (FeatureStore)
- **Portfolio State**: Queried from Coinbase API by reconciliation agent, published to Redis Streams; Risk agent reads from streams to validate new trades
- **Order State**: Published to Redis Streams at each stage (risk_approved → pending_confirmation → confirmed → sent_to_exchange → open → filled); deserialized by downstream agents
- **Performance State**: Computed in-memory by monitoring agent from portfolio snapshots and fills
## Key Abstractions
- Purpose: Universal signal contract emitted by all strategies
- Examples: `agents/signals/strategies/momentum.py`, `agents/signals/strategies/funding_arb.py`
- Pattern: Frozen dataclass with signal_id, timestamp, direction (LONG/SHORT), conviction (0.0-1.0), source (SignalSource enum), time_horizon (timedelta), reasoning (human-readable)
- Location: `libs/common/models/signal.py`
- Purpose: Signal after alpha combination and portfolio routing
- Pattern: Contains conviction_weighted score, portfolio target assignment, suggested entry/stop/TP from multiple signals
- Location: `libs/common/models/trade_idea.py`
- Used by: Risk agent to evaluate and size trades
- Purpose: Order lifecycle contract
- Pattern: ProposedOrder (sized, risk-checked, awaiting execution/confirmation); ApprovedOrder (user-confirmed for Portfolio B)
- Location: `libs/common/models/order.py`
- Key fields: `portfolio_target` (enum, not UUID), `size`, `leverage`, `stop_loss`, `take_profit`, `estimated_margin_required_usdc`, `estimated_liquidation_price`
- Purpose: Complete portfolio state at a point in time
- Pattern: Queries Coinbase API separately per portfolio; includes equity, margin, positions, unrealized/realized P&L, funding, fees
- Location: `libs/common/models/portfolio.py`
- Sourced by: Reconciliation agent publishes per-portfolio snapshots
- Purpose: Route API calls to the correct Coinbase API key
- Pattern: `PortfolioTarget.A` = "autonomous" (no confirmation), `PortfolioTarget.B` = "user_confirmed"
- Location: `libs/common/models/enums.py`
- Critical: No portfolio UUIDs are stored in internal models; routing is via enum → `CoinbaseClientPool.get_client(target)`
- Purpose: Hourly USDC settlement record
- Pattern: Tracks rate, payment amount, position size at settlement, cumulative 24h total
- Location: `libs/common/models/funding.py`
- Sourced by: Reconciliation agent publishes one per portfolio per hour
- Purpose: Route API calls to portfolio-scoped clients
- Pattern: Holds two `CoinbaseRESTClient` instances (one per portfolio API key); `get_client(target)` returns the correct instance
- Location: `libs/coinbase/client_pool.py`
- Critical: All Coinbase API calls in execution and reconciliation agents route via this pool — no portfolio UUID parameters are passed to REST methods
- Purpose: Apply configurable rules to route signals to Portfolio A or B
- Pattern: Rules evaluated in order (time horizon < 2h → A; high-frequency sources → A; default → B)
- Location: `libs/portfolio/router.py`
- Used by: Alpha combiner after signal aggregation
- Purpose: Encapsulate portfolio-specific risk guardrails
- Pattern: Separate limit objects for Portfolio A (aggressive) and B (conservative); read from YAML config
- Location: `agents/risk/limits.py`
- Example: Portfolio A max_leverage=5.0, max_daily_loss_pct=10.0; Portfolio B max_leverage=3.0, max_daily_loss_pct=5.0
- Purpose: Centralized Redis Streams channel name management
- Pattern: Unified channels (stream:signals) and portfolio-scoped channels (stream:approved_orders:a, stream:approved_orders:b); class methods accept PortfolioTarget enum
- Location: `libs/messaging/channels.py`
- Usage: `Channel.approved_orders(PortfolioTarget.A)` → "stream:approved_orders:a"
## Entry Points
- Location: `agents/ingestion/main.py`
- Triggers: System startup; runs 24/7
- Responsibilities:
- Location: `agents/signals/main.py`
- Triggers: System startup; reactive to `stream:market_snapshots`
- Responsibilities:
- Location: `agents/alpha/main.py`
- Triggers: System startup; reactive to `stream:signals` and `stream:market_snapshots`
- Responsibilities:
- Location: `agents/risk/main.py`
- Triggers: System startup; reactive to both `stream:ranked_ideas:a` and `stream:ranked_ideas:b`
- Responsibilities:
- Location: `agents/confirmation/main.py`
- Triggers: System startup; reactive to `stream:approved_orders:b` (Portfolio B only)
- Responsibilities:
- Location: `agents/execution/main.py`
- Triggers: System startup; reactive to `stream:approved_orders:a` and `stream:confirmed_orders`
- Responsibilities:
- Location: `agents/reconciliation/main.py`
- Triggers: System startup; runs on ~30-second poll interval
- Responsibilities:
- Location: `agents/monitoring/main.py`
- Triggers: System startup; runs 24/7, reacts to portfolio state, funding, and fills
- Responsibilities:
## Error Handling
- **Portfolio Mismatch** (critical): If an order tagged `PortfolioTarget.A` ends up querying Portfolio B's state (e.g., due to misconfigured env var), `PortfolioMismatchError` is raised → system halts. Checked in reconciliation agent before querying Coinbase.
- **Rate Limit** (recoverable): `RateLimitExceededError` is caught in REST client, retried with exponential backoff (max 2 retries). Alert is published.
- **Insufficient Margin** (recoverable): Order rejected by Coinbase with "insufficient margin" — caught as `InsufficientMarginError`, rejected by risk engine, not retried. Logged as risk failure.
- **Stale Data** (halting): If `MarketSnapshot` is > 30 seconds old, `StaleDataError` is raised, trading pauses, alert fires. Risk agent checks `(now - snapshot.timestamp) > STALE_DATA_HALT_SECONDS`.
- **Funding Rate Circuit Breaker** (halting): If absolute hourly funding rate > 0.05%, `FundingRateCircuitBreakerError` halts new position-opening. Checked in risk agent.
- **WebSocket Disconnect** (recoverable): Ingestion and execution agents auto-reconnect with exponential backoff (max 30s delay). Trading pauses during disconnect; resumes when connection re-established.
- **Confirmation Timeout** (auto-handling): Portfolio B order not confirmed within TTL → auto-approved (if config allows) or expired and cancelled. Logged as override event.
## Cross-Cutting Concerns
- Framework: `structlog` with JSON output (production) or human-readable (development)
- Pattern: `logger = setup_logging("agent_name", json_output=False)` in each agent entrypoint
- Every log line includes: agent_name, timestamp (UTC), trace_id (for request tracing), instrument (if applicable), portfolio_target (if applicable)
- Example: `logger.info("order_placed", order_id="...", portfolio_target="autonomous", size=2.5, entry_price=2232.00)`
- Framework: `pydantic` for external data models (Coinbase API responses); `dataclasses` for internal models
- Pattern: Models define schema and validation rules; deserialization in agent entrypoints calls `Snapshot.model_validate(payload)` or custom `from_dict()` helpers
- Risk validation: `RiskEngine.evaluate()` performs all checks before returning approval
- Pattern: Each agent loads API credentials from environment variables at startup
- Coinbase: Two sets of credentials (API_KEY_A, SECRET_A, PASSPHRASE_A for Portfolio A; same for B)
- Telegram: Single bot token from env var
- Credentials never logged; only auth successes logged ("coinbase_auth_ready")
- Framework: YAML files in `configs/` + `pydantic-settings` for validation
- Pattern: Base config (`default.yaml`) defines instrument specs, fee tier, portfolio limits, routing rules; environment overrides (`paper.yaml`, `live.yaml`) set mode-specific settings
- Loading: `get_settings()` returns `Settings` pydantic model; `load_yaml_config()` merges YAML into settings
- Strategy configs: Per-strategy YAML in `configs/strategies/` (e.g., momentum.yaml) with per-instrument parameter overrides
- No configuration is changeable at runtime; all changes require agent restart
- Pattern: Every downstream agent (risk, execution, reconciliation, monitoring) explicitly branches by `portfolio_target`
- Streams: Named with portfolio suffix (stream:approved_orders:a, stream:approved_orders:b)
- API routing: Via `CoinbaseClientPool.get_client(target)`, which returns the client authenticated with that portfolio's key
- Margin isolation: Each portfolio's margin is tracked independently in Coinbase; liquidation in A cannot affect B (enforced by exchange)
- No transfers: System has zero code path for inter-portfolio fund transfers (no call to `/api/v1/transfers/portfolios` exists in any agent)
- Async I/O: All network calls (REST, WebSocket, Redis) are async via `asyncio` + `uvloop` (production)
- Concurrency: Signal strategies run in parallel via `asyncio.TaskGroup`; ingestion pollers are concurrent tasks
- Caching: Redis used for message broker (streams) and caching (portfolio state, market data)
- Indicators: Computed efficiently via `polars` (fast) for large rolling windows; `numpy` for intermediate calculations
- Rate limiting: Per-portfolio rate limiter in `CoinbaseClientPool` respects Coinbase headers (RateLimit-Remaining, Retry-After)
- Circuit breaker pattern: Execution agent pauses on adverse conditions (e.g., repeated rejections)
- Watchdog: Orchestrator monitors all agents; if an agent crashes, others are restarted in dependency order
- Graceful degradation: If Telegram bot is unreachable, Portfolio B trades queue (buffered in Redis) — Portfolio A continues operating
- Auto-reconnect: WebSocket clients reconnect on drop with exponential backoff
- Idempotency: Agents use signal_id and order_id for deduplication; processing same message twice is safe
<!-- GSD:architecture-end -->

<!-- GSD:workflow-start source:GSD defaults -->
## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:
- `/gsd:quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd:debug` for investigation and bug fixing
- `/gsd:execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->



<!-- GSD:profile-start -->
## Developer Profile

> Profile not yet configured. Run `/gsd:profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->
