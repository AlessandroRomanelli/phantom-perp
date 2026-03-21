# Codebase Structure

**Analysis Date:** 2026-03-21

## Directory Layout

```
phantom-perp/
├── CLAUDE.md                          # Project requirements and architecture guide
├── README.md                          # Public documentation
├── pyproject.toml                     # Root Python project config (monorepo)
├── Makefile                           # Common commands (lint, test, run, deploy)
├── docker-compose.yml                 # Local development orchestration
├── docker-compose.prod.yml            # Production orchestration
├── .env                               # Environment variables (gitignored)
├── .env.example                       # Environment variable template
│
├── libs/                              # Shared libraries used across all agents
│   ├── common/                        # Shared models, enums, constants
│   │   ├── models/
│   │   │   ├── __init__.py
│   │   │   ├── enums.py              # PortfolioTarget, PositionSide, OrderSide, OrderType, OrderStatus, SignalSource, MarketRegime
│   │   │   ├── signal.py             # StandardSignal (universal signal contract)
│   │   │   ├── trade_idea.py         # RankedTradeIdea (signal after alpha combination)
│   │   │   ├── order.py              # ProposedOrder, ApprovedOrder, Fill (order lifecycle)
│   │   │   ├── position.py           # PerpPosition (entry price, leverage, liquidation price, margin)
│   │   │   ├── portfolio.py          # PortfolioSnapshot (per-portfolio equity, positions, P&L)
│   │   │   ├── market_snapshot.py    # MarketSnapshot (unified market data with orderbook imbalance, volatility)
│   │   │   └── funding.py            # FundingPayment (hourly USDC settlement record)
│   │   ├── config.py                 # Centralized config loading (YAML + env vars)
│   │   ├── constants.py              # Instrument specs, fee rates (VIP 1: 0.0125% maker, 0.025% taker)
│   │   ├── exceptions.py             # Custom exception hierarchy (PortfolioMismatchError, RateLimitExceededError, etc.)
│   │   ├── logging.py                # Structured logging setup (JSON format)
│   │   ├── utils.py                  # Shared helpers (generate_id, round_to_tick, utc_now)
│   │   └── __init__.py
│   ├── coinbase/                     # Coinbase INTX API client (portfolio-scoped)
│   │   ├── auth.py                   # HMAC-SHA256 request signing
│   │   ├── rest_client.py            # Async REST client (public and portfolio-scoped endpoints)
│   │   ├── ws_client.py              # WebSocket client (market data and user data feeds)
│   │   ├── models.py                 # Pydantic models for Coinbase API responses
│   │   ├── client_pool.py            # CoinbaseClientPool (routes API calls to Portfolio A or B client)
│   │   ├── rate_limiter.py           # Token bucket rate limiter (per-portfolio)
│   │   └── __init__.py
│   ├── messaging/                    # Message broker abstraction (Redis Streams)
│   │   ├── base.py                   # Abstract Publisher/Consumer interfaces
│   │   ├── redis_streams.py          # Redis Streams implementation (RedisPublisher, RedisConsumer)
│   │   ├── channels.py               # Channel name constants and registry (stream:signals, stream:approved_orders:a, etc.)
│   │   └── __init__.py
│   ├── storage/                      # Persistence abstraction (PostgreSQL + TimescaleDB)
│   │   ├── timeseries.py             # TimescaleDB adapter for candles, funding, P&L
│   │   ├── relational.py             # SQLAlchemy models for orders, trades, config
│   │   ├── cache.py                  # Redis cache helpers
│   │   └── __init__.py
│   ├── portfolio/                    # Portfolio routing logic
│   │   ├── router.py                 # PortfolioRouter (routes signals to A or B by time horizon, conviction, source)
│   │   ├── registry.py               # Re-exports PortfolioTarget enum
│   │   └── __init__.py
│   ├── indicators/                   # Technical indicator library (shared across all strategies)
│   │   ├── moving_averages.py        # SMA, EMA, VWMA
│   │   ├── oscillators.py            # RSI, MACD, Stochastic, ADX
│   │   ├── volatility.py             # ATR, Bollinger Bands, realized vol
│   │   ├── volume.py                 # OBV, VWAP, volume profile
│   │   ├── funding.py                # Funding rate analytics (hourly cumulative, z-score, predicted)
│   │   └── __init__.py
│   └── __init__.py
│
├── agents/                           # One directory per pipeline agent
│   ├── ingestion/                    # Phase 1: Data Ingestion & Enrichment
│   │   ├── Dockerfile
│   │   ├── main.py                   # Agent entrypoint (orchestrates all data sources)
│   │   ├── state.py                  # IngestionState (per-instrument rolling market state)
│   │   ├── normalizer.py             # build_snapshot() (constructs MarketSnapshot from IngestionState)
│   │   ├── sources/
│   │   │   ├── ws_market_data.py     # WebSocket market data listener (L2, trades, ticker)
│   │   │   ├── ws_user_data.py       # WebSocket user data listener (orders, fills) — not used by ingestion, defined but skipped
│   │   │   ├── candles.py            # REST candle pollers (1m, 5m, 15m, 1h, 6h timeframes)
│   │   │   ├── funding_rate.py       # Hourly funding rate poller (every 5 minutes)
│   │   │   ├── liquidations.py       # Liquidation detection from trades/external APIs
│   │   │   ├── onchain.py            # On-chain metrics (gas, staking rate, whale moves)
│   │   │   ├── sentiment.py          # Crypto sentiment (CT, Reddit, Fear&Greed)
│   │   │   ├── macro.py              # BTC correlation, DXY, rates, risk-on/off
│   │   │   ├── open_interest.py      # Aggregate open interest (Coinbase + external)
│   │   │   └── __init__.py
│   │   ├── tests/
│   │   │   ├── test_main.py
│   │   │   ├── test_normalizer.py
│   │   │   └── __init__.py
│   │   └── __init__.py
│   │
│   ├── signals/                      # Phase 2: Signal Generation
│   │   ├── Dockerfile
│   │   ├── main.py                   # Agent entrypoint (loads strategies, runs in parallel)
│   │   ├── feature_store.py          # FeatureStore (per-instrument rolling price buffer + indicators)
│   │   ├── strategies/
│   │   │   ├── base.py               # SignalStrategy abstract base class
│   │   │   ├── momentum.py           # Momentum: multi-timeframe EMA crossover + ADX + RSI
│   │   │   ├── mean_reversion.py     # Mean reversion: Bollinger Bands + VWAP deviation
│   │   │   ├── funding_arb.py        # Funding arb: exploit intra-hour funding rate swings
│   │   │   ├── orderbook_imbalance.py # Orderbook imbalance: L2 bid/ask depth ratio
│   │   │   ├── liquidation_cascade.py # Liquidation cascade: detect and fade large liquidation clusters
│   │   │   ├── correlation.py        # Correlation: ETH/BTC ratio, macro divergences
│   │   │   ├── regime_trend.py       # Regime-aware trend: adjusts conviction by market regime
│   │   │   ├── sentiment.py          # Sentiment: NLP-driven sentiment signals (not yet implemented)
│   │   │   └── __init__.py
│   │   ├── tests/
│   │   │   ├── test_main.py
│   │   │   ├── test_feature_store.py
│   │   │   └── __init__.py
│   │   └── __init__.py
│   │
│   ├── alpha/                        # Phase 3: Alpha Combination & Routing
│   │   ├── Dockerfile
│   │   ├── main.py                   # Agent entrypoint
│   │   ├── combiner.py               # AlphaCombiner (weighted signal aggregation by source)
│   │   ├── regime_detector.py        # RegimeDetector (classify market: trending, ranging, high-vol, squeeze, etc.)
│   │   ├── scorecard.py              # StrategyScorecard (rolling accuracy tracking per strategy, per portfolio)
│   │   ├── conflict_resolver.py      # ConflictResolver (resolve opposing signals via regime weighting)
│   │   ├── portfolio_router.py       # Portfolio routing (wrapper around libs/portfolio/router.py)
│   │   ├── tests/
│   │   │   ├── test_main.py
│   │   │   ├── test_combiner.py
│   │   │   └── __init__.py
│   │   └── __init__.py
│   │
│   ├── risk/                         # Phase 4: Risk Management & Pre-Trade Compliance
│   │   ├── Dockerfile
│   │   ├── main.py                   # Agent entrypoint (consumes ranked ideas, validates vs limits)
│   │   ├── limits.py                 # RiskLimits (Portfolio A: aggressive; Portfolio B: conservative)
│   │   ├── margin_calculator.py      # Compute initial/maintenance margin, liquidation price, distance
│   │   ├── liquidation_guard.py      # Ensure stop-loss is always before liquidation
│   │   ├── funding_cost_estimator.py # Project hourly funding cost over holding period
│   │   ├── fee_calculator.py         # Compute maker/taker fees at VIP 1 rates
│   │   ├── position_sizer.py         # Size based on equity, risk budget, ATR
│   │   ├── portfolio_state_fetcher.py # Query Coinbase for live equity/margin (async, cached)
│   │   ├── simulator.py              # What-if: simulate margin impact, new liquidation price
│   │   ├── tests/
│   │   │   ├── test_main.py
│   │   │   ├── test_limits.py
│   │   │   ├── test_margin_calculator.py
│   │   │   └── __init__.py
│   │   └── __init__.py
│   │
│   ├── confirmation/                 # Phase 4.5: User Confirmation via Telegram (Portfolio B only)
│   │   ├── Dockerfile
│   │   ├── main.py                   # Agent entrypoint (Telegram bot, webhook mode)
│   │   ├── bot.py                    # TelegramBot setup and webhook handler
│   │   ├── message_composer.py       # Format trade proposals into rich messages
│   │   ├── callback_handler.py       # Process button responses (approve/reject/delay/modify)
│   │   ├── state_machine.py          # OrderStateMachine (pending_confirmation → confirmed → sent_to_exchange)
│   │   ├── timeout_manager.py        # TTL enforcement, stale price guards, auto-approval
│   │   ├── batching.py               # Batch multiple orders into single messages
│   │   ├── portfolio_commands.py     # Telegram commands: /status, /pause, /resume, /kill
│   │   ├── config.py                 # ConfirmationConfig (user prefs, auto-approve thresholds, quiet hours)
│   │   ├── tests/
│   │   │   ├── test_main.py
│   │   │   ├── test_state_machine.py
│   │   │   └── __init__.py
│   │   └── __init__.py
│   │
│   ├── execution/                    # Phase 5: Order Execution
│   │   ├── Dockerfile
│   │   ├── main.py                   # Agent entrypoint (places orders, monitors fills)
│   │   ├── order_placer.py           # Place orders via CoinbaseClientPool (routes by portfolio_target)
│   │   ├── algo_selector.py          # Select execution strategy (limit-only, IOC, scaled, TWAP)
│   │   ├── fill_monitor.py           # Monitor fills via WebSocket user data feed
│   │   ├── retry_handler.py          # Handle order rejections, insufficient margin, re-quote
│   │   ├── stop_loss_manager.py      # Place and manage stop-loss / take-profit orders (portfolio-scoped)
│   │   ├── circuit_breaker.py        # Pause execution on adverse conditions (per-portfolio)
│   │   ├── tests/
│   │   │   ├── test_main.py
│   │   │   ├── test_order_placer.py
│   │   │   └── __init__.py
│   │   └── __init__.py
│   │
│   ├── reconciliation/               # Phase 6: Portfolio Reconciliation
│   │   ├── Dockerfile
│   │   ├── main.py                   # Agent entrypoint (polls Coinbase, publishes state)
│   │   ├── state_manager.py          # Build PortfolioSnapshot from Coinbase API response
│   │   ├── coinbase_reconciler.py    # Cross-check internal state vs Coinbase (per portfolio)
│   │   ├── funding_tracker.py        # Track hourly USDC funding payments (per portfolio)
│   │   ├── pnl_calculator.py         # Compute realized + unrealized P&L, funding-adjusted, fee-adjusted
│   │   ├── paper_simulator.py        # Simulate fills for paper trading mode
│   │   ├── analytics.py              # Compute exposure, effective leverage, margin utilization
│   │   ├── tests/
│   │   │   ├── test_main.py
│   │   │   ├── test_state_manager.py
│   │   │   ├── test_funding_tracker.py
│   │   │   └── __init__.py
│   │   └── __init__.py
│   │
│   ├── monitoring/                   # Phase 7: Monitoring, Feedback & Learning
│   │   ├── Dockerfile
│   │   ├── main.py                   # Agent entrypoint (tracks performance, enforces kill switches)
│   │   ├── health_checker.py         # Latency, WS connection status, data freshness
│   │   ├── performance_tracker.py    # Sharpe, drawdown, win rate (tracked separately per portfolio)
│   │   ├── funding_report.py         # Hourly/daily/weekly funding income/cost breakdown (per portfolio)
│   │   ├── fee_report.py             # Total fees paid, maker vs taker ratio, fee savings
│   │   ├── alerting.py               # Alert types and conditions (daily loss, drawdown, margin, funding rate)
│   │   ├── retrainer.py              # Model re-tuning on recent data (if ML models used)
│   │   ├── shadow_mode.py            # Run new models in parallel before promoting to live
│   │   ├── tests/
│   │   │   ├── test_main.py
│   │   │   ├── test_performance_tracker.py
│   │   │   └── __init__.py
│   │   └── __init__.py
│   │
│   └── __init__.py
│
├── orchestrator/                     # Pipeline coordinator
│   ├── main.py                       # Manages agent lifecycle, retries, timeouts
│   ├── dag.py                        # PipelineDAG (agent dependency graph, startup/shutdown order)
│   ├── circuit_breakers.py           # Global and per-portfolio kill switches (daily loss, drawdown)
│   ├── watchdog.py                   # Ensure all agents are alive (24/7 health checks)
│   ├── tests/
│   │   ├── test_dag.py
│   │   ├── test_circuit_breakers.py
│   │   └── __init__.py
│   └── __init__.py
│
├── configs/                          # Runtime configuration (YAML)
│   ├── default.yaml                  # Base configuration (instrument specs, risk limits, routing rules)
│   ├── paper.yaml                    # Paper trading overrides (auto-confirm, simulated execution)
│   ├── live.yaml                     # Live trading overrides (real execution, Telegram confirmation for B)
│   └── strategies/
│       ├── momentum.yaml             # Momentum strategy parameters and per-instrument overrides
│       ├── mean_reversion.yaml
│       ├── funding_arb.yaml
│       ├── orderbook_imbalance.yaml
│       ├── liquidation_cascade.yaml
│       ├── correlation.yaml
│       ├── regime_trend.yaml
│       └── sentiment.yaml            # Placeholder; sentiment strategy not yet implemented
│
├── scripts/                          # Utility scripts
│   ├── deploy.sh                     # Build and deploy all agents to Oracle Cloud
│   ├── status.sh                     # Generate status report of deployed system
│   ├── dashboard.py                  # Live terminal dashboard (polls Redis Streams)
│   ├── seed_data.py                  # Load historical ETH-PERP candles + hourly funding rates
│   ├── backtest.py                   # Run strategies against historical data
│   ├── paper_trade.py                # Launch full pipeline with paper/simulated execution
│   ├── funding_analysis.py           # Analyze hourly funding rate patterns and cumulative impact
│   └── generate_config.py            # Interactive config generator
│
├── infra/                            # Infrastructure-as-code
│   ├── terraform/                    # Terraform configs for Oracle Cloud
│   ├── k8s/                          # Kubernetes manifests (if applicable)
│   └── monitoring/
│       ├── grafana/                  # Dashboards: P&L per portfolio, funding, margin, signals
│       ├── prometheus/               # Scrape configs, alert rules
│       └── loki/                     # Log aggregation config
│
├── tests/                            # Test suite
│   ├── unit/                         # Unit tests (individual functions)
│   │   ├── test_coinbase_auth.py
│   │   ├── test_portfolio_registry.py
│   │   ├── test_channels.py          # Channel registry and naming
│   │   ├── test_indicators.py
│   │   └── __init__.py
│   ├── integration/                  # Integration tests (agent-to-agent via Redis, portfolio routing)
│   │   ├── test_pipeline_flow.py     # Full pipeline signal → order → execution → reconciliation
│   │   ├── test_risk_rejection.py    # Risk agent rejects trades over limit
│   │   ├── test_confirmation_timeout.py # Portfolio B confirmation TTL
│   │   ├── test_margin_calculation.py
│   │   ├── test_funding_tracking.py
│   │   ├── test_portfolio_routing.py # Verify signals route to correct portfolio
│   │   ├── test_portfolio_isolation.py # Verify Portfolio A orders use A client, B uses B client
│   │   ├── test_no_cross_transfer.py # Verify no inter-portfolio transfer code path
│   │   └── __init__.py
│   ├── e2e/
│   │   ├── test_paper_trade_cycle.py # Full cycle in paper mode
│   │   └── __init__.py
│   └── __init__.py
│
└── .planning/                        # GSD planning documents
    └── codebase/
        ├── ARCHITECTURE.md           # This file — pattern, layers, data flow, abstractions
        ├── STRUCTURE.md              # Directory layout, naming conventions, where to add code
        ├── STACK.md                  # Technology stack and external integrations
        ├── CONVENTIONS.md            # Naming, style, import organization, error handling
        ├── TESTING.md                # Test patterns, fixtures, test organization
        └── CONCERNS.md               # Known issues, tech debt, security, performance
```

## Directory Purposes

**libs/common/models/**
- Purpose: Core data models used across all agents
- Contains: Frozen dataclasses and Pydantic models for signals, orders, positions, portfolios, market snapshots
- Key files: `signal.py` (StandardSignal), `order.py` (ProposedOrder/ApprovedOrder/Fill), `enums.py` (PortfolioTarget, OrderStatus, SignalSource)

**libs/coinbase/**
- Purpose: Coinbase INTX REST and WebSocket client abstraction
- Contains: Authentication (HMAC-SHA256 signing), async HTTP client, WebSocket client, API response models
- Key feature: `client_pool.py` routes API calls by portfolio target (no portfolio UUID passed to methods)
- Critical: Each client holds its own rate limiter and auth (per-portfolio API key)

**libs/messaging/**
- Purpose: Event bus abstraction (Redis Streams)
- Contains: Publisher/Consumer interfaces, Redis Streams implementation, channel registry
- Key file: `channels.py` centralizes stream names (stream:signals, stream:approved_orders:a, stream:approved_orders:b, etc.)

**libs/portfolio/**
- Purpose: Portfolio routing logic
- Key file: `router.py` applies rules to assign signals to Portfolio A (autonomous) or B (user-confirmed)

**libs/indicators/**
- Purpose: Technical indicator library shared by all strategies
- Contains: Moving averages (SMA, EMA), oscillators (RSI, MACD, ADX), volatility (ATR, Bollinger), funding rate analytics

**agents/ingestion/**
- Purpose: Real-time market data ingestion
- Contains: WebSocket and REST polling modules that consume Coinbase and external APIs
- Output: Publishes `MarketSnapshot` to stream:market_snapshots and `FundingRate` to stream:funding_updates

**agents/signals/**
- Purpose: Generate trading signals
- Contains: Strategy implementations (base class + momentum, mean_reversion, funding_arb, orderbook_imbalance, liquidation_cascade, correlation, regime_trend)
- Feature: Each strategy is a class implementing `SignalStrategy` interface
- Output: Publishes `StandardSignal` to stream:signals

**agents/alpha/**
- Purpose: Combine signals, detect regime, route to portfolios
- Contains: AlphaCombiner (weighted aggregation), RegimeDetector, StrategyScorecard (accuracy tracking), PortfolioRouter
- Output: Publishes `RankedTradeIdea` to stream:ranked_ideas:a or stream:ranked_ideas:b

**agents/risk/**
- Purpose: Validate trades against portfolio-specific limits
- Contains: RiskEngine (deterministic evaluation), margin/liquidation calculations, position sizer, fee/funding estimators
- Output: Publishes `ProposedOrder` to stream:approved_orders:a (→ execution) or stream:approved_orders:b (→ confirmation)

**agents/confirmation/**
- Purpose: Telegram-based user confirmation (Portfolio B only)
- Contains: Telegram bot (webhook mode), message composer, state machine, timeout manager
- Input: Consumes ProposedOrder from stream:approved_orders:b
- Output: Publishes ApprovedOrder to stream:confirmed_orders on user approval

**agents/execution/**
- Purpose: Place orders on Coinbase, monitor fills, manage stop-loss
- Contains: Order placer (routes via CoinbaseClientPool), algo selector, fill monitor, retry handler, circuit breaker
- Dual input path:
  - Portfolio A: Consumes ProposedOrder from stream:approved_orders:a (direct from risk)
  - Portfolio B: Consumes ApprovedOrder from stream:confirmed_orders (user-confirmed)
- Output: Publishes Fill to stream:exchange_events:a or stream:exchange_events:b

**agents/reconciliation/**
- Purpose: Query Coinbase state, track funding, detect discrepancies
- Contains: State manager, Coinbase reconciler, funding tracker, P&L calculator
- Operation: Queries each portfolio independently via CoinbaseClientPool
- Output: Publishes PortfolioSnapshot to stream:portfolio_state:a/b and FundingPayment to stream:funding_payments:a/b

**agents/monitoring/**
- Purpose: Performance tracking, fee/funding reporting, kill switches
- Contains: Health checker, performance tracker (separate per portfolio), funding/fee reporters, alerting engine
- Enforces: Daily loss kill switch (A: 10%, B: 5%) and drawdown kill switch (A: 25%, B: 15%)
- Output: Publishes Alert to stream:alerts

**orchestrator/**
- Purpose: Manage agent lifecycle, dependencies, health
- Contains: PipelineDAG (startup/shutdown order), circuit breakers, watchdog

**configs/**
- Purpose: Runtime configuration files
- Base: `default.yaml` — instrument specs, fee tiers, portfolio limits, routing rules
- Overrides: `paper.yaml` (auto-confirm, simulated execution), `live.yaml` (real execution, Telegram)
- Per-strategy: `strategies/*.yaml` — parameters and per-instrument overrides

## Key File Locations

**Entry Points:**
- `agents/ingestion/main.py`: Starts ingestion pipeline (WebSocket + REST pollers)
- `agents/signals/main.py`: Runs all strategies in parallel
- `agents/alpha/main.py`: Combines signals and routes to portfolios
- `agents/risk/main.py`: Validates trades against limits
- `agents/confirmation/main.py`: Telegram bot for Portfolio B confirmation
- `agents/execution/main.py`: Places orders on Coinbase
- `agents/reconciliation/main.py`: Queries Coinbase state
- `agents/monitoring/main.py`: Tracks performance and enforces kill switches

**Configuration:**
- `libs/common/config.py`: Central config loading (YAML + env)
- `configs/default.yaml`: Base instrument specs, fees, portfolio limits, routing rules
- `agents/risk/limits.py`: Portfolio-specific risk limits (read from YAML)
- `agents/execution/config.py`: Execution-specific config (order TTL, slippage, retry limits)
- `agents/confirmation/config.py`: Telegram confirmation config (TTL, auto-approve thresholds, quiet hours)

**Core Logic:**
- `libs/common/models/`: Core data models (StandardSignal, ProposedOrder, PortfolioSnapshot, etc.)
- `libs/common/enums.py`: PortfolioTarget, OrderStatus, SignalSource (universal enums)
- `libs/coinbase/client_pool.py`: Routes API calls to Portfolio A or B client
- `libs/messaging/channels.py`: Redis Streams channel registry
- `libs/portfolio/router.py`: Routes signals by time horizon, conviction, source
- `agents/signals/strategies/base.py`: SignalStrategy abstract base
- `agents/risk/limits.py`: RiskLimits (Portfolio A aggressive, B conservative)
- `agents/alpha/combiner.py`: AlphaCombiner (weighted signal aggregation)

**Testing:**
- `tests/unit/test_*.py`: Unit tests for individual modules
- `tests/integration/test_*.py`: Integration tests (agent-to-agent via Redis, portfolio isolation)
- `tests/integration/test_portfolio_isolation.py`: Verify Portfolio A uses A client, B uses B client
- `tests/integration/test_no_cross_transfer.py`: Verify no inter-portfolio transfer code path exists

## Naming Conventions

**Files:**
- Agent files: lowercase_with_underscores, e.g., `order_placer.py`, `liquidation_guard.py`
- Test files: `test_<module>.py`, e.g., `test_margin_calculator.py`
- Config files: lowercase with hyphens, e.g., `funding_arb.yaml`, `default.yaml`

**Directories:**
- Agent directories: lowercase (agents/ingestion, agents/signals)
- Library directories: lowercase (libs/common, libs/coinbase)
- Strategy directories: lowercase/plural (agents/signals/strategies)
- Config directories: lowercase/plural (configs/strategies)

**Classes:**
- PascalCase, e.g., `StandardSignal`, `ProposedOrder`, `CoinbaseRESTClient`, `PortfolioRouter`
- Exception classes: PascalCase + "Error", e.g., `PortfolioMismatchError`, `RiskLimitBreachedError`

**Functions & Methods:**
- snake_case, e.g., `build_snapshot()`, `compute_position_size()`, `round_to_tick()`
- Private methods: prefixed with underscore, e.g., `_validate_stop_loss()`

**Constants:**
- UPPER_SNAKE_CASE, e.g., `STALE_DATA_HALT_SECONDS`, `FUNDING_RATE_CIRCUIT_BREAKER_PCT`, `MIN_ORDER_SIZE`
- Grouped at module top (e.g., constants.py)

**Enums:**
- Class name: PascalCase, e.g., `PortfolioTarget`, `OrderStatus`, `SignalSource`
- Values: lowercase with underscores or UPPER_SNAKE (depends on type), e.g., `PortfolioTarget.A = "autonomous"`, `OrderStatus.RISK_APPROVED = "risk_approved"`

**Variables:**
- snake_case for local variables, e.g., `portfolio_equity_usdc`, `liquidation_distance_pct`
- Suffix with unit where applicable: `_usdc` for USDC amounts, `_pct` for percentages, `_bps` for basis points, `_seconds` for time durations

## Where to Add New Code

**New Trading Strategy:**
1. Create `agents/signals/strategies/your_strategy.py`
2. Implement `class YourStrategy(SignalStrategy)` with `evaluate()` method
3. Register in `agents/signals/main.py` under `STRATEGY_CLASSES` dict
4. Add config file at `configs/strategies/your_strategy.yaml` with parameters and per-instrument overrides
5. Add default routing rule in `configs/default.yaml` under `portfolio.routing.rules` (to which portfolio)
6. Write tests in `agents/signals/tests/test_your_strategy.py`
7. Deploy — alpha combiner will load it automatically with weight 0.0 (shadow mode)

**New Risk Limit or Guardrail:**
1. Add to `agents/risk/limits.py` (RiskLimits dataclass) with separate Portfolio A and B values
2. Read from `configs/default.yaml` under `risk.portfolio_a` and `risk.portfolio_b`
3. Implement check in `agents/risk/main.py` or in `RiskEngine.evaluate()`
4. Write test in `tests/integration/test_risk_rejection.py`
5. If critical/non-negotiable: hardcode in `libs/common/constants.py` as fallback

**New External Data Source (Enrichment):**
1. Create `agents/ingestion/sources/your_source.py`
2. Implement poller that fetches data asynchronously
3. Call `on_source_update()` callback to notify ingestion agent
4. Update `agents/ingestion/main.py` to spawn your poller
5. Update `agents/ingestion/normalizer.py` to incorporate new data into `MarketSnapshot` if needed

**New Telegram Command (Portfolio B):**
1. Add handler in `agents/confirmation/portfolio_commands.py`
2. Register in `agents/confirmation/bot.py` setup
3. Write test in `agents/confirmation/tests/test_portfolio_commands.py`

**New Configuration Parameter:**
1. Add to `configs/default.yaml` under appropriate section (e.g., `risk.portfolio_a.new_param`)
2. Load via `get_settings()` in `libs/common/config.py` (if using pydantic-settings)
3. Pass through agent's config object
4. Write unit test for config loading

**New Database Model (PostgreSQL):**
1. Define SQLAlchemy model in `libs/storage/relational.py`
2. Create migration script in `infra/db/migrations/`
3. Update reconciliation agent to persist records if needed

**New Alert Type:**
1. Add to `AlertType` enum in `agents/monitoring/alerting.py`
2. Implement check function in same module
3. Call from `agents/monitoring/main.py` in the monitoring loop
4. Write test in `agents/monitoring/tests/`

## Special Directories

**`.planning/codebase/`**
- Purpose: GSD planning documents (ARCHITECTURE.md, STRUCTURE.md, STACK.md, CONVENTIONS.md, TESTING.md, CONCERNS.md)
- Generated: No (written by humans or GSD mappers)
- Committed: Yes

**`.claude/`**
- Purpose: Internal context and analysis (GSD thread state)
- Generated: Yes (by GSD system)
- Committed: No (gitignored)

**`infra/`**
- Purpose: Infrastructure-as-code (Terraform, Kubernetes, monitoring)
- Generated: No
- Committed: Yes

**`configs/`**
- Purpose: Runtime YAML configuration
- Generated: No (manually edited by operators)
- Committed: Yes (no secrets)

**`scripts/`**
- Purpose: Deployment and utility scripts
- Generated: No
- Committed: Yes

**`tests/`**
- Purpose: Full test suite (unit, integration, E2E)
- Generated: No
- Committed: Yes

**`__pycache__/`, `.pytest_cache/`**
- Purpose: Python and pytest caches
- Generated: Yes
- Committed: No (gitignored)
