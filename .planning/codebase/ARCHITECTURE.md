# Architecture

**Analysis Date:** 2026-03-21

## Pattern Overview

**Overall:** Event-driven, agent-based pipeline with explicit separation of concerns and portfolio-scoped routing.

**Key Characteristics:**
- Multi-stage asynchronous pipeline: each stage is an independent agent communicating via Redis Streams
- Dual-portfolio architecture with physically isolated margin and API key scoping
- Portfolio-aware routing: signals flow through risk limits specific to each portfolio
- 24/7 operational model with no market-close downtime
- Safety-critical architecture with non-negotiable guardrails (hardcoded, not configurable)

## Layers

**Data Ingestion Layer:**
- Purpose: Ingest real-time market data (orderbook, trades, candles, funding rates) from Coinbase INTX and external enrichment sources
- Location: `agents/ingestion/`
- Contains: WebSocket and REST polling modules that consume Coinbase APIs and external data providers, normalize to unified `MarketSnapshot` model
- Depends on: Coinbase REST/WS clients (`libs/coinbase/`), Redis for publishing
- Used by: Signal generation strategies (consume `MarketSnapshot` from Redis stream)
- Key files: `agents/ingestion/main.py` orchestrates concurrent sources; `agents/ingestion/sources/` contains individual pollers (candles, funding_rate, ws_market_data); `agents/ingestion/normalizer.py` builds `MarketSnapshot`

**Signal Generation Layer:**
- Purpose: Run all trading strategies in parallel, emit `StandardSignal` objects
- Location: `agents/signals/`
- Contains: Strategy implementations (`agents/signals/strategies/`), feature store for indicator computation, strategy orchestrator
- Depends on: Market snapshots from ingestion, strategy configs, technical indicator library (`libs/indicators/`)
- Used by: Alpha combiner consumes `StandardSignal` from Redis stream
- Key files: `agents/signals/main.py` runs all strategies; `agents/signals/strategies/` contains base class and implementations (momentum, mean_reversion, funding_arb, orderbook_imbalance, liquidation_cascade, correlation, regime_trend); `agents/signals/feature_store.py` maintains per-instrument rolling window buffers

**Alpha Combination Layer:**
- Purpose: Combine multiple signals, resolve conflicts, route to portfolios, rank by conviction
- Location: `agents/alpha/`
- Contains: Alpha combiner (weighted aggregation), regime detector, strategy scorecard (rolling accuracy tracking), conflict resolver, portfolio router
- Depends on: Signals and market snapshots from Redis, configuration for weighting rules
- Used by: Risk agent consumes `RankedTradeIdea` from portfolio-scoped streams
- Key files: `agents/alpha/main.py` orchestrates flow; `agents/alpha/combiner.py` performs aggregation; `agents/alpha/regime_detector.py` classifies market regime; `agents/alpha/scorecard.py` tracks per-strategy accuracy; `libs/portfolio/router.py` routes by time horizon, conviction, and strategy type

**Risk Management Layer:**
- Purpose: Validate trade ideas against portfolio-specific risk limits, compute position sizing, margin requirements, liquidation prices
- Location: `agents/risk/`
- Contains: Risk engine (deterministic evaluation), margin calculator, liquidation guard, position sizer, fee/funding cost estimators, limits registry
- Depends on: Ranked ideas from alpha, live portfolio state from reconciliation, market data
- Used by: Execution and confirmation agents consume approved orders from portfolio-scoped streams
- Key files: `agents/risk/main.py` orchestrates validation; `agents/risk/limits.py` defines separate limit sets for Portfolio A (aggressive) and B (conservative); `agents/risk/margin_calculator.py` computes initial/maintenance margin and liquidation distance; `agents/risk/position_sizer.py` sizes based on equity and risk budget

**Confirmation Layer (Portfolio B Only):**
- Purpose: Present orders to user via Telegram for approval before execution
- Location: `agents/confirmation/`
- Contains: Telegram bot setup, message composition, callback handler, state machine for order lifecycle, timeout manager
- Depends on: Approved orders from risk agent (Portfolio B stream), Telegram API
- Used by: Execution agent consumes confirmed orders from unified stream
- Key files: `agents/confirmation/main.py` runs Telegram bot; `agents/confirmation/state_machine.py` manages order states (pending_confirmation → confirmed → sent_to_exchange); `agents/confirmation/message_composer.py` formats rich trade notifications with inline keyboards

**Execution Layer:**
- Purpose: Place orders on Coinbase via the correct portfolio's API client, monitor fills, manage stop-loss orders
- Location: `agents/execution/`
- Contains: Order placer (routes via `CoinbaseClientPool`), algo selector, fill monitor, retry handler, circuit breaker, stop-loss manager
- Depends on: Approved (Portfolio A) and confirmed (Portfolio B) orders from portfolio-scoped/unified streams, Coinbase REST client pool
- Used by: Reconciliation consumes fills from portfolio-scoped streams
- Key files: `agents/execution/main.py` subscribes to approved_orders:a and confirmed_orders; `agents/execution/order_placer.py` calls `CoinbaseClientPool.get_client(target)` to route via correct API key; `agents/execution/circuit_breaker.py` pauses on adverse conditions; `agents/execution/stop_loss_manager.py` places protective orders

**Reconciliation Layer:**
- Purpose: Query Coinbase for portfolio state (equity, margin, positions), track hourly funding payments, detect discrepancies with internal state
- Location: `agents/reconciliation/`
- Contains: State manager, Coinbase reconciler, funding tracker, P&L calculator
- Depends on: Fills from execution, Coinbase REST API (queried via both portfolio clients independently)
- Used by: Risk agent consumes portfolio state to validate new trades; monitoring consumes state and funding for reporting
- Key files: `agents/reconciliation/main.py` polls each portfolio independently and publishes to portfolio-scoped streams; `agents/reconciliation/state_manager.py` builds `PortfolioSnapshot` from Coinbase response; `agents/reconciliation/funding_tracker.py` tracks hourly USDC settlement payments

**Monitoring & Learning Layer:**
- Purpose: Track performance (P&L, Sharpe, drawdown), report funding/fees, generate alerts, enforce kill switches
- Location: `agents/monitoring/`
- Contains: Health checker, performance tracker (separate per portfolio), funding reporter, fee reporter, alerting engine
- Depends on: Portfolio state, funding payments, fills from reconciliation
- Used by: Alerts published to unified stream for all agents to consume
- Key files: `agents/monitoring/main.py` orchestrates; `agents/monitoring/performance_tracker.py` computes portfolio-specific metrics; `agents/monitoring/alerting.py` enforces daily loss and drawdown kill switches; `agents/monitoring/funding_report.py` aggregates hourly funding events

**Infrastructure & Configuration:**
- Purpose: Shared libraries, messaging, portfolio routing, storage abstractions
- Location: `libs/`
- Contains: Common models (`libs/common/models/`), Coinbase client abstraction (`libs/coinbase/`), Redis Streams messaging (`libs/messaging/`), technical indicators (`libs/indicators/`), portfolio routing logic (`libs/portfolio/`)
- Key files: `libs/common/models/` defines core contracts (StandardSignal, ProposedOrder, PerpPosition, PortfolioSnapshot); `libs/coinbase/client_pool.py` routes API calls by portfolio; `libs/messaging/channels.py` defines Redis stream names; `libs/portfolio/router.py` applies routing rules

**Orchestration & Coordination:**
- Purpose: Manage agent lifecycle, startup/shutdown order, circuit breakers, watchdog health checks
- Location: `orchestrator/`
- Contains: Pipeline DAG definition, circuit breakers (per-portfolio and global), watchdog for agent crashes
- Key files: `orchestrator/dag.py` defines agent dependencies and startup order; `orchestrator/circuit_breakers.py` implements kill switches for daily loss and max drawdown; `orchestrator/main.py` manages container lifecycle

## Data Flow

**Signal Generation → Execution → Reconciliation Cycle:**

1. **Ingestion** (continuous): Pulls market data from Coinbase and external sources, publishes `MarketSnapshot` to `stream:market_snapshots` every tick
2. **Signals** (reactive): Consumes `MarketSnapshot`, runs all strategies in parallel, publishes `StandardSignal` to `stream:signals`
3. **Alpha** (reactive): Consumes `StandardSignal` and `MarketSnapshot`, combines signals, detects regime, routes by portfolio, publishes `RankedTradeIdea` to `stream:ranked_ideas:a` or `stream:ranked_ideas:b`
4. **Risk** (reactive): Consumes `RankedTradeIdea` from both portfolio streams, queries live portfolio state, evaluates against portfolio-specific limits, publishes `ProposedOrder` to `stream:approved_orders:a` (→ execution) or `stream:approved_orders:b` (→ confirmation)
5. **Confirmation** (reactive, Portfolio B only): Consumes `ProposedOrder` from `stream:approved_orders:b`, sends Telegram message, waits for user response, publishes `ApprovedOrder` to `stream:confirmed_orders`
6. **Execution** (reactive, dual path):
   - Portfolio A: Consumes `ProposedOrder` from `stream:approved_orders:a`, places order via `CoinbaseClientPool.get_client(PortfolioTarget.A)`
   - Portfolio B: Consumes `ApprovedOrder` from `stream:confirmed_orders`, places order via `CoinbaseClientPool.get_client(PortfolioTarget.B)`
   - Both: Publish fills to `stream:exchange_events:a` or `stream:exchange_events:b`
7. **Reconciliation** (periodic): Polls Coinbase for each portfolio's state independently, publishes `PortfolioSnapshot` to `stream:portfolio_state:a` and `stream:portfolio_state:b`; tracks hourly funding to `stream:funding_payments:a` and `stream:funding_payments:b`
8. **Monitoring** (continuous): Consumes all state and funding events, enforces kill switches, reports performance, publishes alerts to `stream:alerts`

**Portfolio Routing Decision Points:**

- **Signal Source** → Alpha Combiner: `RankedTradeIdea` is routed to Portfolio A or B based on signal properties (time horizon, conviction, source type)
- **Order Execution** → Execution Agent: `CoinbaseClientPool.get_client(portfolio_target)` selects the correct REST client authenticated with that portfolio's API key
- **Portfolio State** → Risk & Reconciliation: Each agent independently queries or consumes data for Portfolio A and Portfolio B; no cross-portfolio operations

**State Management:**

- **Market State**: Held in Redis Streams (immutable, append-only); Signals agent maintains per-instrument rolling buffer in memory (FeatureStore)
- **Portfolio State**: Queried from Coinbase API by reconciliation agent, published to Redis Streams; Risk agent reads from streams to validate new trades
- **Order State**: Published to Redis Streams at each stage (risk_approved → pending_confirmation → confirmed → sent_to_exchange → open → filled); deserialized by downstream agents
- **Performance State**: Computed in-memory by monitoring agent from portfolio snapshots and fills

## Key Abstractions

**StandardSignal:**
- Purpose: Universal signal contract emitted by all strategies
- Examples: `agents/signals/strategies/momentum.py`, `agents/signals/strategies/funding_arb.py`
- Pattern: Frozen dataclass with signal_id, timestamp, direction (LONG/SHORT), conviction (0.0-1.0), source (SignalSource enum), time_horizon (timedelta), reasoning (human-readable)
- Location: `libs/common/models/signal.py`

**RankedTradeIdea:**
- Purpose: Signal after alpha combination and portfolio routing
- Pattern: Contains conviction_weighted score, portfolio target assignment, suggested entry/stop/TP from multiple signals
- Location: `libs/common/models/trade_idea.py`
- Used by: Risk agent to evaluate and size trades

**ProposedOrder & ApprovedOrder:**
- Purpose: Order lifecycle contract
- Pattern: ProposedOrder (sized, risk-checked, awaiting execution/confirmation); ApprovedOrder (user-confirmed for Portfolio B)
- Location: `libs/common/models/order.py`
- Key fields: `portfolio_target` (enum, not UUID), `size`, `leverage`, `stop_loss`, `take_profit`, `estimated_margin_required_usdc`, `estimated_liquidation_price`

**PortfolioSnapshot:**
- Purpose: Complete portfolio state at a point in time
- Pattern: Queries Coinbase API separately per portfolio; includes equity, margin, positions, unrealized/realized P&L, funding, fees
- Location: `libs/common/models/portfolio.py`
- Sourced by: Reconciliation agent publishes per-portfolio snapshots

**PortfolioTarget Enum:**
- Purpose: Route API calls to the correct Coinbase API key
- Pattern: `PortfolioTarget.A` = "autonomous" (no confirmation), `PortfolioTarget.B` = "user_confirmed"
- Location: `libs/common/models/enums.py`
- Critical: No portfolio UUIDs are stored in internal models; routing is via enum → `CoinbaseClientPool.get_client(target)`

**FundingPayment:**
- Purpose: Hourly USDC settlement record
- Pattern: Tracks rate, payment amount, position size at settlement, cumulative 24h total
- Location: `libs/common/models/funding.py`
- Sourced by: Reconciliation agent publishes one per portfolio per hour

**CoinbaseClientPool:**
- Purpose: Route API calls to portfolio-scoped clients
- Pattern: Holds two `CoinbaseRESTClient` instances (one per portfolio API key); `get_client(target)` returns the correct instance
- Location: `libs/coinbase/client_pool.py`
- Critical: All Coinbase API calls in execution and reconciliation agents route via this pool — no portfolio UUID parameters are passed to REST methods

**PortfolioRouter:**
- Purpose: Apply configurable rules to route signals to Portfolio A or B
- Pattern: Rules evaluated in order (time horizon < 2h → A; high-frequency sources → A; default → B)
- Location: `libs/portfolio/router.py`
- Used by: Alpha combiner after signal aggregation

**RiskLimits:**
- Purpose: Encapsulate portfolio-specific risk guardrails
- Pattern: Separate limit objects for Portfolio A (aggressive) and B (conservative); read from YAML config
- Location: `agents/risk/limits.py`
- Example: Portfolio A max_leverage=5.0, max_daily_loss_pct=10.0; Portfolio B max_leverage=3.0, max_daily_loss_pct=5.0

**Channel Registry:**
- Purpose: Centralized Redis Streams channel name management
- Pattern: Unified channels (stream:signals) and portfolio-scoped channels (stream:approved_orders:a, stream:approved_orders:b); class methods accept PortfolioTarget enum
- Location: `libs/messaging/channels.py`
- Usage: `Channel.approved_orders(PortfolioTarget.A)` → "stream:approved_orders:a"

## Entry Points

**Ingestion Agent:**
- Location: `agents/ingestion/main.py`
- Triggers: System startup; runs 24/7
- Responsibilities:
  1. Initialize WebSocket client to Coinbase market data feed (public, not portfolio-scoped)
  2. Spawn concurrent pollers for candles (multiple timeframes per instrument), funding rates, liquidation detection
  3. On each WS tick, rebuild `IngestionState` with latest orderbook/trade/ticker data
  4. Call `build_snapshot()` to construct unified `MarketSnapshot`
  5. Publish to `stream:market_snapshots` and `stream:funding_updates`

**Signals Agent:**
- Location: `agents/signals/main.py`
- Triggers: System startup; reactive to `stream:market_snapshots`
- Responsibilities:
  1. Load all strategy instances from `agents/signals/strategies/`
  2. Maintain per-instrument `FeatureStore` for rolling price buffers and indicators
  3. On every `MarketSnapshot`, call `strategy.evaluate(snapshot, store)` for each enabled strategy
  4. Deduplicate signals (same strategy, same direction, within cooldown), assign signal_id
  5. Publish `StandardSignal` to `stream:signals`

**Alpha Agent:**
- Location: `agents/alpha/main.py`
- Triggers: System startup; reactive to `stream:signals` and `stream:market_snapshots`
- Responsibilities:
  1. Consume `StandardSignal`, buffer by instrument
  2. On signal arrival, query latest `MarketSnapshot` for context
  3. Run `AlphaCombiner` to aggregate signals by direction, compute conviction-weighted score
  4. Detect market regime via `RegimeDetector`
  5. Resolve conflicting signals (long vs short on same instrument) via regime-aware weighting
  6. Route via `PortfolioRouter` to determine Portfolio A or B
  7. Publish `RankedTradeIdea` to `stream:ranked_ideas:a` or `stream:ranked_ideas:b`

**Risk Agent:**
- Location: `agents/risk/main.py`
- Triggers: System startup; reactive to both `stream:ranked_ideas:a` and `stream:ranked_ideas:b`
- Responsibilities:
  1. Subscribe to both portfolio streams (one consumer per stream)
  2. On `RankedTradeIdea` arrival, query `stream:portfolio_state:*` for live equity/margin of that portfolio
  3. Create `RiskEngine` with portfolio-specific limits (from `limits_for_portfolio(target)`)
  4. Call `engine.evaluate(idea, portfolio_state, market_price, ...)` → `RiskCheckResult`
  5. If approved: compute position size, estimate fees/funding, construct `ProposedOrder`, publish to `stream:approved_orders:a` or `stream:approved_orders:b`
  6. If rejected: log rejection reason, do not publish

**Confirmation Agent:**
- Location: `agents/confirmation/main.py`
- Triggers: System startup; reactive to `stream:approved_orders:b` (Portfolio B only)
- Responsibilities:
  1. Subscribe to `stream:approved_orders:b`
  2. In paper mode: auto-approve all, pass to execution immediately
  3. In live mode: send Telegram message with trade details and inline keyboard (Approve/Reject/Delay/Modify)
  4. Wait for user response (with TTL), timeout enforces auto-approval or expiry based on config
  5. On user approval: publish `ApprovedOrder` to `stream:confirmed_orders`
  6. On user rejection: log as override event, publish to `stream:user_overrides`

**Execution Agent:**
- Location: `agents/execution/main.py`
- Triggers: System startup; reactive to `stream:approved_orders:a` and `stream:confirmed_orders`
- Responsibilities:
  1. Subscribe to both streams (Portfolio A orders go direct from risk; Portfolio B orders come via confirmation)
  2. On order receipt:
     - Deserialize to `ProposedOrder` or `ApprovedOrder`
     - Select execution algo (limit-only for maker fee, or market if needed)
     - Get correct client: `CoinbaseClientPool.get_client(order.portfolio_target)`
     - Place order via REST API (routing handled by client's auth key)
  3. Monitor fills via WebSocket user data feed (Portfolio A and B filtered by portfolio_id in event)
  4. On partial/full fill: construct `Fill` object, publish to `stream:exchange_events:a` or `stream:exchange_events:b`
  5. Manage stop-loss and take-profit orders (separate orders on the exchange)

**Reconciliation Agent:**
- Location: `agents/reconciliation/main.py`
- Triggers: System startup; runs on ~30-second poll interval
- Responsibilities:
  1. Query Coinbase API for Portfolio A state using `CoinbaseClientPool.get_client(PortfolioTarget.A)`
  2. Query Coinbase API for Portfolio B state using `CoinbaseClientPool.get_client(PortfolioTarget.B)`
  3. Build `PortfolioSnapshot` from each response (equity, margin, positions, P&L)
  4. Publish to `stream:portfolio_state:a` and `stream:portfolio_state:b` respectively
  5. Consume `stream:exchange_events:a` and `stream:exchange_events:b` to track fills
  6. Compute funding impact from fills + hourly funding rates
  7. Publish `FundingPayment` events to `stream:funding_payments:a` and `stream:funding_payments:b`

**Monitoring Agent:**
- Location: `agents/monitoring/main.py`
- Triggers: System startup; runs 24/7, reacts to portfolio state, funding, and fills
- Responsibilities:
  1. Consume `stream:portfolio_state:a` and `stream:portfolio_state:b`
  2. Consume `stream:funding_payments:a` and `stream:funding_payments:b`
  3. Consume `stream:exchange_events:a` and `stream:exchange_events:b` for fee tracking
  4. Maintain `DualPerformanceTracker` (separate P&L/Sharpe/drawdown per portfolio)
  5. Maintain `DualFundingReporter` (hourly/daily/weekly funding breakdown per portfolio)
  6. Maintain `DualFeeTracker` (maker/taker split per portfolio)
  7. Check daily loss kill switch: if portfolio A loss > 10% today, halt A; if B loss > 5%, halt B
  8. Check drawdown kill switch: if portfolio A drawdown > 25%, halt A; if B > 15%, halt B
  9. Publish alerts to `stream:alerts` on breaches

## Error Handling

**Strategy:** Errors are categorized by severity and operation type. Critical errors halt the system; recoverable errors are logged and retried.

**Patterns:**

- **Portfolio Mismatch** (critical): If an order tagged `PortfolioTarget.A` ends up querying Portfolio B's state (e.g., due to misconfigured env var), `PortfolioMismatchError` is raised → system halts. Checked in reconciliation agent before querying Coinbase.
- **Rate Limit** (recoverable): `RateLimitExceededError` is caught in REST client, retried with exponential backoff (max 2 retries). Alert is published.
- **Insufficient Margin** (recoverable): Order rejected by Coinbase with "insufficient margin" — caught as `InsufficientMarginError`, rejected by risk engine, not retried. Logged as risk failure.
- **Stale Data** (halting): If `MarketSnapshot` is > 30 seconds old, `StaleDataError` is raised, trading pauses, alert fires. Risk agent checks `(now - snapshot.timestamp) > STALE_DATA_HALT_SECONDS`.
- **Funding Rate Circuit Breaker** (halting): If absolute hourly funding rate > 0.05%, `FundingRateCircuitBreakerError` halts new position-opening. Checked in risk agent.
- **WebSocket Disconnect** (recoverable): Ingestion and execution agents auto-reconnect with exponential backoff (max 30s delay). Trading pauses during disconnect; resumes when connection re-established.
- **Confirmation Timeout** (auto-handling): Portfolio B order not confirmed within TTL → auto-approved (if config allows) or expired and cancelled. Logged as override event.

**Logging Convention:** All errors logged as structured JSON via `structlog` with fields: `agent_name`, `timestamp`, `trace_id`, `portfolio_target`, `severity`, `error_type`, `message`.

## Cross-Cutting Concerns

**Logging:**
- Framework: `structlog` with JSON output (production) or human-readable (development)
- Pattern: `logger = setup_logging("agent_name", json_output=False)` in each agent entrypoint
- Every log line includes: agent_name, timestamp (UTC), trace_id (for request tracing), instrument (if applicable), portfolio_target (if applicable)
- Example: `logger.info("order_placed", order_id="...", portfolio_target="autonomous", size=2.5, entry_price=2232.00)`

**Validation:**
- Framework: `pydantic` for external data models (Coinbase API responses); `dataclasses` for internal models
- Pattern: Models define schema and validation rules; deserialization in agent entrypoints calls `Snapshot.model_validate(payload)` or custom `from_dict()` helpers
- Risk validation: `RiskEngine.evaluate()` performs all checks before returning approval

**Authentication:**
- Pattern: Each agent loads API credentials from environment variables at startup
- Coinbase: Two sets of credentials (API_KEY_A, SECRET_A, PASSPHRASE_A for Portfolio A; same for B)
- Telegram: Single bot token from env var
- Credentials never logged; only auth successes logged ("coinbase_auth_ready")

**Configuration:**
- Framework: YAML files in `configs/` + `pydantic-settings` for validation
- Pattern: Base config (`default.yaml`) defines instrument specs, fee tier, portfolio limits, routing rules; environment overrides (`paper.yaml`, `live.yaml`) set mode-specific settings
- Loading: `get_settings()` returns `Settings` pydantic model; `load_yaml_config()` merges YAML into settings
- Strategy configs: Per-strategy YAML in `configs/strategies/` (e.g., momentum.yaml) with per-instrument parameter overrides
- No configuration is changeable at runtime; all changes require agent restart

**Portfolio Isolation:**
- Pattern: Every downstream agent (risk, execution, reconciliation, monitoring) explicitly branches by `portfolio_target`
- Streams: Named with portfolio suffix (stream:approved_orders:a, stream:approved_orders:b)
- API routing: Via `CoinbaseClientPool.get_client(target)`, which returns the client authenticated with that portfolio's key
- Margin isolation: Each portfolio's margin is tracked independently in Coinbase; liquidation in A cannot affect B (enforced by exchange)
- No transfers: System has zero code path for inter-portfolio fund transfers (no call to `/api/v1/transfers/portfolios` exists in any agent)

**Performance & Scalability:**
- Async I/O: All network calls (REST, WebSocket, Redis) are async via `asyncio` + `uvloop` (production)
- Concurrency: Signal strategies run in parallel via `asyncio.TaskGroup`; ingestion pollers are concurrent tasks
- Caching: Redis used for message broker (streams) and caching (portfolio state, market data)
- Indicators: Computed efficiently via `polars` (fast) for large rolling windows; `numpy` for intermediate calculations
- Rate limiting: Per-portfolio rate limiter in `CoinbaseClientPool` respects Coinbase headers (RateLimit-Remaining, Retry-After)

**Resilience:**
- Circuit breaker pattern: Execution agent pauses on adverse conditions (e.g., repeated rejections)
- Watchdog: Orchestrator monitors all agents; if an agent crashes, others are restarted in dependency order
- Graceful degradation: If Telegram bot is unreachable, Portfolio B trades queue (buffered in Redis) — Portfolio A continues operating
- Auto-reconnect: WebSocket clients reconnect on drop with exponential backoff
- Idempotency: Agents use signal_id and order_id for deduplication; processing same message twice is safe
