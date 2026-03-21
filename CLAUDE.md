# CLAUDE.md — Autonomous ETH Perpetual Futures Trading System

## Project Identity

- **Name:** `phantom-perp`
- **Purpose:** A modular, event-driven AI agentic system for trading **ETH-PERP-INTX** (Ethereum perpetual futures) on **Coinbase International Exchange (INTX)**, operating across two physically separate Coinbase portfolios — one fully autonomous, one user-confirmed via Telegram.
- **Market:** `ETH-PERP-INTX` — USDC-margined Ethereum perpetual futures contract on Coinbase INTX.
- **Philosophy:** Separate opinion from action from oversight. No single agent both decides and executes without independent review.

---

## Market Context — ETH-PERP-INTX

Understanding the instrument is critical for every agent in the system:

- **Perpetual futures** have no expiry date. Positions can be held indefinitely.
- **Funding rate** is computed and **settled every hour** in **USDC**. When funding is positive, longs pay shorts; when negative, shorts pay longs. With 24 funding events per day, cumulative funding impact is significant and must be tracked granularly.
- **Leverage** is available. Coinbase INTX supports up to a maximum leverage on ETH-PERP (check current limits via API). Our system will enforce its own lower caps per portfolio.
- **Margin** is **USDC-denominated**. Positions require initial margin to open and maintenance margin to hold. If equity falls below maintenance margin, liquidation occurs.
- **Mark price** (not last traded price) determines unrealized P&L and liquidation. The mark price is derived from the index price (spot) plus a decaying funding basis.
- **24/7 market.** There are no market hours. The system must be designed to run continuously.
- **Fee tier: VIP 1** — `0.0125% maker` | `0.0250% taker`. Execution strategy should strongly favor limit orders (maker) to cut fees in half.

---

## Dual-Portfolio Architecture

The system operates across **two physically separate Coinbase INTX portfolios**. This is not a logical partition — these are real, independent portfolios with their own margin, positions, and equity at the exchange level.

### Portfolio A — Autonomous
- **API Key:** Dedicated Coinbase API key scoped to Portfolio A. Configured via `COINBASE_INTX_API_KEY_A` / `SECRET_A` / `PASSPHRASE_A`.
- **Autonomy:** Fully autonomous. Trades execute **without user confirmation**. The confirmation agent is bypassed entirely.
- **Objective:** Maximize profitability. Higher risk tolerance. Aggressive strategies are encouraged.
- **Constraints:** No all-in trades. Position sizing limits apply relative to Portfolio A's own equity. All safety guardrails (stop-loss, liquidation distance, drawdown kill switch) enforce with Portfolio A-specific thresholds.
- **Use cases:** High-frequency signals, short-lived funding arb, orderbook imbalance scalps, liquidation cascade fades — anything where the confirmation delay would kill the opportunity.

### Portfolio B — User-Confirmed
- **API Key:** Dedicated Coinbase API key scoped to Portfolio B. Configured via `COINBASE_INTX_API_KEY_B` / `SECRET_B` / `PASSPHRASE_B`.
- **Autonomy:** Every trade requires user confirmation via Telegram before execution.
- **Objective:** Steady, risk-managed returns. Capital preservation is a priority alongside growth.
- **Constraints:** Conservative position sizing. Stricter safety guardrails than Portfolio A.
- **Use cases:** Higher-conviction, longer-horizon trades: momentum, trend-following, fundamental/sentiment-based positions.

### Physical Separation — Implications

Because these are separate Coinbase portfolios:

1. **Margin is fully isolated.** Each portfolio has its own margin pool. A liquidation event in Portfolio A cannot affect Portfolio B's positions or equity. This is the strongest possible risk isolation — enforced by the exchange, not by our code.
2. **Positions are independent.** Each portfolio holds its own positions. Portfolio A can be long ETH while Portfolio B is short ETH — these are separate positions with separate margin, separate liquidation prices, and separate funding payments.
3. **API calls are routed via the correct client.** Since each API key is portfolio-scoped, routing is handled by selecting the correct `CoinbaseRESTClient` instance via `CoinbaseClientPool.get_client(target)`. No portfolio UUIDs are passed in API calls.
4. **No automatic transfers.** There are **no automatic fund transfers** between portfolios. No profit sweeps, no rebalancing, no top-ups. All transfers between Portfolio A and Portfolio B are **user-initiated only**, performed manually through Coinbase or via Coinbase's own transfer interface. The system will never move funds between portfolios on its own under any circumstance.
5. **WebSocket feeds** may deliver events for both portfolios on the same connection. The ingestion and reconciliation agents must filter events by portfolio ID.

### Routing Logic

When a signal passes through the alpha combiner and risk agent, it is tagged with a target portfolio based on:

| Criteria                           | → Portfolio A (Autonomous) | → Portfolio B (User-Confirmed) |
| ---------------------------------- | -------------------------- | ------------------------------ |
| Time horizon < 2 hours             | ✅                          |                                |
| Time horizon ≥ 2 hours             |                            | ✅                              |
| Strategy: funding_arb, orderbook   | ✅                          |                                |
| Strategy: momentum, sentiment      |                            | ✅                              |
| Strategy: liquidation_cascade      | ✅                          |                                |
| Conviction ≥ 0.85 + short horizon  | ✅                          |                                |
| Estimated notional > A's max size  |                            | ✅                              |
| User override (Telegram command)   | Configurable               | Configurable                   |

This routing is configurable in `configs/default.yaml` under `portfolio.routing`.

---

## Coinbase INTX API

### Authentication
Coinbase INTX uses **API key + secret + passphrase** authentication with HMAC-SHA256 request signing. Every request must include:
- `CB-ACCESS-KEY` — API key
- `CB-ACCESS-SIGN` — HMAC-SHA256 signature of (timestamp + method + path + body)
- `CB-ACCESS-TIMESTAMP` — Unix timestamp
- `CB-ACCESS-PASSPHRASE` — Passphrase set during key creation

**API keys are portfolio-scoped.** Each key is created under a specific Coinbase portfolio and can only operate on that portfolio. The system requires **two separate API keys** — one for Portfolio A, one for Portfolio B. This is handled by `CoinbaseClientPool` in `libs/coinbase/client_pool.py`, which holds a separate `CoinbaseRESTClient` (with its own auth and rate limiter) per portfolio.

### Portfolio Routing via API Key Scoping
On Coinbase Advanced, each API key is scoped to a single portfolio. Portfolio routing is handled entirely by which `CoinbaseRESTClient` instance is used — no `portfolio_id` parameter is passed to API calls. The `CoinbaseClientPool` holds one client per portfolio, each authenticated with its own API key:

- `CoinbaseClientPool.get_client(PortfolioTarget.A)` — returns the client for Portfolio A
- `CoinbaseClientPool.get_client(PortfolioTarget.B)` — returns the client for Portfolio B
- `CoinbaseClientPool.market_client` — for non-portfolio-scoped endpoints (market data)

Internal data models use `portfolio_target: PortfolioTarget` (an enum with values `A="autonomous"`, `B="user_confirmed"`) for routing decisions. Portfolio UUIDs are not stored or passed internally.

### Key Endpoints

| Purpose                    | Method | Endpoint                                      | Portfolio-Scoped | Used By         |
| -------------------------- | ------ | --------------------------------------------- | ---------------- | --------------- |
| List instruments           | GET    | `/api/v1/instruments`                          | No               | Ingestion       |
| Get ETH-PERP orderbook     | GET    | `/api/v1/instruments/ETH-PERP-INTX/book`      | No               | Ingestion       |
| Get candles (OHLCV)        | GET    | `/api/v1/instruments/ETH-PERP-INTX/candles`   | No               | Ingestion       |
| Get funding rate           | GET    | `/api/v1/instruments/ETH-PERP-INTX/funding`   | No               | Ingestion, Risk |
| Create order               | POST   | `/api/v1/orders`                               | **Yes**          | Execution       |
| Cancel order               | DELETE | `/api/v1/orders/{order_id}`                    | **Yes**          | Execution       |
| List open orders           | GET    | `/api/v1/orders`                               | **Yes**          | Reconciliation  |
| Get positions              | GET    | `/api/v1/positions`                            | **Yes**          | Reconciliation  |
| Get portfolio/account      | GET    | `/api/v1/portfolios/{portfolio_id}`            | **Yes**          | Reconciliation  |
| Get fills                  | GET    | `/api/v1/fills`                                | **Yes**          | Reconciliation  |
| Transfer between portfolios| POST   | `/api/v1/transfers/portfolios`                 | **Yes**          | **NEVER used by the system** |

### WebSocket Feeds
Coinbase INTX provides real-time WebSocket feeds at `wss://ws-md.international.coinbase.com` (market data) and `wss://ws.international.coinbase.com` (user data):

| Channel        | Data                                      | Portfolio-Scoped | Used By              |
| -------------- | ----------------------------------------- | ---------------- | -------------------- |
| `MARKET_DATA`  | L2 order book updates, trades, ticker     | No               | Ingestion            |
| `INSTRUMENTS`  | Instrument status, funding rate updates   | No               | Ingestion, Risk      |
| `RISK`         | Position updates, margin, liquidation     | **Yes — filter** | Reconciliation, Risk |
| `ORDERS`       | Order status changes, fills in real-time  | **Yes — filter** | Execution, Recon     |

WebSocket user-data events must be **filtered by portfolio ID** on receipt. The `ws_client.py` should tag each event with the portfolio it belongs to before publishing to Redis Streams.

### Rate Limits
- REST API: Rate limits apply per API key. Monitor `RateLimit-Remaining` headers.
- WebSocket: Connection limits apply. Use a single connection with multiple channel subscriptions.
- The ingestion agent must implement backoff and respect rate limits. Never retry aggressively.
- Each portfolio's API key has its own rate limit budget. The `CoinbaseClientPool` creates a separate `RateLimiter` per portfolio.

### Fee Schedule (VIP 1)
```
Maker: 0.0125%
Taker: 0.0250%
```
These are hardcoded in `libs/common/constants.py` and used by:
- **Risk agent** — to compute net P&L impact of proposed trades.
- **Execution agent** — to decide order type. Limit (maker) saves 0.0125% per side.
- **Monitoring agent** — to track total fees paid and fee-adjusted returns per portfolio.
- If the fee tier changes, update `constants.py` and `configs/default.yaml`.

### SDK / Library
- Use `coinbase-advanced-py` or build a thin async client with `httpx` over the REST API + `websockets` for WS feeds.
- Alternatively, `ccxt` supports Coinbase INTX under the `coinbaseinternational` exchange ID, but verify it supports portfolio-scoped operations.

---

## Architecture Overview

```
                                         ┌─────────────────────────┐
                                         │     Signal Generation    │
                                         │  (all strategies, shared │
                                         │   market data)           │
                                         └────────────┬────────────┘
                                                      │
                                                      ▼
┌──────────────┐                          ┌───────────────────────┐
│    Data      │─────────────────────────▶│   Alpha Combination   │
│  Ingestion   │                          │  + Portfolio Routing   │
└──────────────┘                          └───────────┬───────────┘
                                                      │
                                         ┌────────────┴────────────┐
                                         ▼                         ▼
                                 ┌──────────────┐         ┌──────────────┐
                                 │   Risk Mgmt  │         │   Risk Mgmt  │
                                 │ (Portfolio A) │         │ (Portfolio B) │
                                 │ own equity,  │         │ own equity,  │
                                 │ own margin   │         │ own margin   │
                                 └──────┬───────┘         └──────┬───────┘
                                        │                        │
                                        ▼                        ▼
                                 ┌──────────────┐         ┌──────────────┐
                                 │  Execution   │         │ Confirmation │
                                 │  (immediate) │         │  (Telegram)  │
                                 │  client: A   │         └──────┬───────┘
                                 │              │                │
                                 └──────┬───────┘                ▼
                                        │                 ┌──────────────┐
                                        │                 │  Execution   │
                                        │                 │ (on confirm) │
                                        │                 │  client: B   │
                                        │                 │              │
                                        │                 └──────┬───────┘
                                        ▼                        ▼
                                 ┌────────────────────────────────┐
                                 │     Portfolio Reconciliation    │
                                 │  (queries both Coinbase         │
                                 │   portfolios independently)     │
                                 └────────────────┬───────────────┘
                                                  │
                                                  ▼
                                 ┌────────────────────────────────┐
                                 │    Monitoring & Learning        │
                                 │  (per-portfolio performance)    │
                                 └────────────────────────────────┘
```

All agents communicate via Redis Streams. The pipeline runs **24/7** with no market-close downtime.

---

## Repository Structure

```
phantom-perp/
├── CLAUDE.md                          # This file — project brain
├── README.md                          # Public-facing docs
├── docker-compose.yml                 # Local orchestration
├── docker-compose.prod.yml            # Production orchestration
├── .env.example                       # Environment variable template
├── pyproject.toml                     # Root Python project config (monorepo)
├── Makefile                           # Common commands (lint, test, run, deploy)
│
├── libs/                              # Shared libraries used across agents
│   ├── common/                        # Shared models, enums, constants
│   │   ├── models/
│   │   │   ├── signal.py              # StandardSignal dataclass
│   │   │   ├── order.py               # ProposedOrder, ApprovedOrder, Fill
│   │   │   ├── position.py            # PerpPosition (entry price, leverage, liq price, margin)
│   │   │   ├── portfolio.py           # PortfolioSnapshot (per Coinbase portfolio)
│   │   │   ├── market_snapshot.py     # Unified market data model (incl. funding rate)
│   │   │   ├── funding.py             # FundingRate, FundingPayment models (hourly USDC)
│   │   │   └── enums.py              # OrderSide, OrderType, SignalSource, PositionSide, PortfolioTarget
│   │   ├── config.py                  # Centralized config loading (env + YAML)
│   │   ├── logging.py                 # Structured logging (JSON format)
│   │   ├── exceptions.py             # Custom exception hierarchy
│   │   ├── constants.py              # Instrument ID, tick size, lot size, FEE_MAKER, FEE_TAKER, safety guardrails
│   │   └── utils.py                  # Shared helpers (timestamps, rounding to tick, etc.)
│   ├── coinbase/                      # Coinbase INTX API client
│   │   ├── auth.py                    # HMAC-SHA256 request signing
│   │   ├── rest_client.py             # Async REST client — portfolio routing via API key scoping, no portfolio_id param
│   │   ├── ws_client.py              # WebSocket client — tags events with portfolio_id on receipt
│   │   ├── models.py                  # Coinbase API response models (Pydantic)
│   │   └── rate_limiter.py           # Token bucket rate limiter (shared budget across both portfolios)
│   ├── messaging/                     # Message broker abstraction layer
│   │   ├── base.py                    # Abstract Publisher / Consumer interfaces
│   │   ├── redis_streams.py           # Redis Streams implementation
│   │   └── channels.py               # Channel name constants and topic registry
│   ├── storage/                       # Persistence abstraction
│   │   ├── timeseries.py             # TimescaleDB adapter (candles, hourly funding, P&L)
│   │   ├── relational.py             # PostgreSQL via SQLAlchemy (orders, trades, config)
│   │   └── cache.py                  # Redis cache helpers
│   ├── portfolio/                     # Portfolio routing logic
│   │   ├── router.py                 # Route signals → Portfolio A or B based on configurable rules
│   │   └── registry.py              # Re-exports PortfolioTarget enum (portfolio routing via API key scoping)
│   └── indicators/                    # Shared technical indicator library
│       ├── moving_averages.py         # SMA, EMA, VWMA
│       ├── oscillators.py            # RSI, MACD, Stochastic
│       ├── volatility.py             # ATR, Bollinger Bands, realized vol
│       ├── volume.py                 # OBV, VWAP, volume profile
│       └── funding.py                # Funding rate analytics (hourly cumulative, z-score, predicted)
│
├── agents/                            # One directory per pipeline agent
│   ├── ingestion/                     # Phase 1: Data Ingestion & Enrichment
│   │   ├── Dockerfile
│   │   ├── main.py                    # Agent entrypoint
│   │   ├── sources/
│   │   │   ├── ws_market_data.py      # WebSocket: L2 book, trades, ticker for ETH-PERP-INTX
│   │   │   ├── ws_user_data.py        # WebSocket: orders, fills, risk — filtered by portfolio_id
│   │   │   ├── candles.py             # REST polling for OHLCV candles (multiple timeframes)
│   │   │   ├── funding_rate.py        # Hourly funding rate history + current/predicted rate
│   │   │   ├── liquidations.py        # Large liquidation detection (from trades/external)
│   │   │   ├── onchain.py             # On-chain metrics: ETH gas, staking rate, whale moves
│   │   │   ├── sentiment.py           # Crypto sentiment: CT (Crypto Twitter), Reddit, Fear&Greed
│   │   │   ├── macro.py               # BTC correlation, DXY, rates, risk-on/off indicators
│   │   │   └── open_interest.py       # Aggregate open interest (Coinbase + external APIs)
│   │   ├── enrichment.py             # Align all sources on unified timeline, compute derived fields
│   │   ├── normalizer.py             # Schema normalization to MarketSnapshot
│   │   └── tests/
│   │
│   ├── signals/                       # Phase 2: Signal Generation
│   │   ├── Dockerfile
│   │   ├── main.py                    # Agent entrypoint — runs all strategies in parallel
│   │   ├── strategies/
│   │   │   ├── base.py                # Abstract SignalStrategy interface
│   │   │   ├── momentum.py            # Trend-following across multiple timeframes
│   │   │   ├── mean_reversion.py      # Deviation from VWAP, Bollinger mean-reversion
│   │   │   ├── funding_arb.py         # Funding rate arbitrage (hourly — more opportunities than 8h)
│   │   │   ├── orderbook_imbalance.py # L2 book imbalance → short-term directional signal
│   │   │   ├── liquidation_cascade.py # Detect liquidation clusters → fade or follow
│   │   │   ├── sentiment.py           # NLP-driven crypto sentiment signals
│   │   │   ├── correlation.py         # ETH/BTC ratio, ETH vs macro factor divergences
│   │   │   └── onchain.py            # On-chain activity → mid-term directional bias
│   │   ├── feature_store.py           # Precomputed features for strategies
│   │   └── tests/
│   │
│   ├── alpha/                         # Phase 3: Alpha Combination, Ranking & Routing
│   │   ├── Dockerfile
│   │   ├── main.py
│   │   ├── combiner.py               # Weighted signal aggregation
│   │   ├── regime_detector.py         # Market regime: trending, ranging, high-vol, low-vol, squeeze
│   │   ├── scorecard.py              # Rolling accuracy tracker per strategy (separate for A and B)
│   │   ├── conflict_resolver.py       # Resolve opposing signals with regime-aware weighting
│   │   ├── portfolio_router.py        # Assign each ranked idea to Portfolio A or Portfolio B
│   │   └── tests/
│   │
│   ├── risk/                          # Phase 4: Risk Management & Pre-Trade Compliance
│   │   ├── Dockerfile
│   │   ├── main.py
│   │   ├── limits.py                  # Dual limit sets: Portfolio A (aggressive) and Portfolio B (conservative)
│   │   ├── margin_calculator.py       # Compute initial/maintenance margin, distance to liquidation
│   │   ├── funding_cost_estimator.py  # Project hourly funding cost over expected holding period
│   │   ├── fee_calculator.py          # Compute maker/taker fees at VIP 1 rates
│   │   ├── position_sizer.py          # Size based on target portfolio's Coinbase equity, risk budget, ATR
│   │   ├── liquidation_guard.py       # Ensure liquidation price is always beyond stop-loss
│   │   ├── portfolio_state_fetcher.py # Query Coinbase for each portfolio's current equity and margin
│   │   ├── simulator.py              # What-if: margin impact, new liquidation price, funding drag
│   │   └── tests/
│   │
│   ├── confirmation/                  # Phase 4.5: User Confirmation (Telegram) — Portfolio B ONLY
│   │   ├── Dockerfile
│   │   ├── main.py                    # Telegram bot entrypoint
│   │   ├── bot.py                     # Telegram bot setup (webhook mode)
│   │   ├── message_composer.py        # Format trade proposals into rich messages
│   │   ├── callback_handler.py        # Process user responses (approve/reject/delay/modify)
│   │   ├── state_machine.py           # Order confirmation state transitions
│   │   ├── timeout_manager.py         # TTL enforcement, stale price guards
│   │   ├── batching.py               # Group multiple signals into single messages
│   │   ├── portfolio_commands.py      # Telegram commands: /status, /pause, /resume, /kill
│   │   ├── config.py                  # User prefs: auto-approve thresholds, quiet hours
│   │   └── tests/
│   │
│   ├── execution/                     # Phase 5: Execution
│   │   ├── Dockerfile
│   │   ├── main.py
│   │   ├── order_placer.py            # Place orders via the correct CoinbaseRESTClient per portfolio
│   │   ├── algo_selector.py           # Execution strategy: limit-only (maker), IOC, scaled, TWAP
│   │   ├── fill_monitor.py            # Monitor via WS user feed; filter fills by portfolio target
│   │   ├── retry_handler.py           # Handle order rejections, insufficient margin, re-quote
│   │   ├── stop_loss_manager.py       # Place and manage stop-loss / take-profit — portfolio-scoped
│   │   ├── circuit_breaker.py         # Pause execution on adverse conditions (per-portfolio)
│   │   └── tests/
│   │
│   ├── reconciliation/                # Phase 6: Portfolio Reconciliation
│   │   ├── Dockerfile
│   │   ├── main.py
│   │   ├── state_manager.py           # Source of truth: queries BOTH Coinbase portfolios independently
│   │   ├── coinbase_reconciler.py     # Cross-check internal state vs Coinbase — per portfolio target
│   │   ├── funding_tracker.py         # Track hourly USDC funding payments — per portfolio
│   │   ├── pnl_calculator.py          # Realized + unrealized P&L, funding-adjusted, fee-adjusted, per portfolio
│   │   ├── analytics.py              # Exposure, effective leverage, margin utilization — per portfolio
│   │   └── tests/
│   │
│   └── monitoring/                    # Phase 7: Monitoring, Feedback & Learning
│       ├── Dockerfile
│       ├── main.py
│       ├── health_checker.py          # Latency, WS connection status, data freshness
│       ├── performance_tracker.py     # Sharpe, drawdown, win rate — tracked SEPARATELY per portfolio
│       ├── funding_report.py          # Hourly/daily/weekly funding income/cost breakdown per portfolio
│       ├── fee_report.py              # Total fees paid, maker vs taker ratio, fee savings
│       ├── retrainer.py              # Model re-tuning on recent data
│       ├── shadow_mode.py             # Run new models in parallel before promoting
│       ├── alerting.py               # Telegram alerts for system issues
│       └── tests/
│
├── orchestrator/                      # Pipeline coordinator
│   ├── main.py                        # Manages agent lifecycle, retries, timeouts
│   ├── circuit_breakers.py            # Global kill switches (per-portfolio and global)
│   ├── watchdog.py                    # Ensure all agents are alive (24/7 critical)
│   └── dag.py                         # Pipeline DAG definition and execution order
│
├── infra/                             # Infrastructure-as-code
│   ├── terraform/
│   ├── k8s/
│   └── monitoring/
│       ├── grafana/                   # Dashboards: P&L per portfolio, funding, margin, signals
│       ├── prometheus/                # Scrape configs, alert rules
│       └── loki/                      # Log aggregation config
│
├── scripts/
│   ├── deploy.sh                      # Build, transfer, and deploy Docker images to Oracle Cloud
│   ├── status.sh                      # Generate status report of deployed system
│   ├── dashboard.py                   # Live terminal dashboard (polls Redis Streams)
│   ├── seed_data.py                   # Load historical ETH-PERP candles + hourly funding rates
│   ├── backtest.py                    # Run strategies against historical ETH-PERP data
│   ├── paper_trade.py                 # Launch full pipeline with paper/simulated execution
│   ├── funding_analysis.py            # Analyze hourly funding rate patterns and cumulative impact
│   └── generate_config.py            # Interactive config generator
│
├── configs/                           # Runtime configuration (YAML)
│   ├── default.yaml                   # Base configuration
│   ├── paper.yaml                     # Paper trading overrides
│   ├── live.yaml                      # Live trading overrides
│   └── strategies/
│       ├── momentum.yaml
│       ├── mean_reversion.yaml
│       ├── funding_arb.yaml
│       ├── orderbook_imbalance.yaml
│       ├── liquidation_cascade.yaml
│       └── sentiment.yaml
│
└── tests/
    ├── integration/
    │   ├── test_pipeline_flow.py
    │   ├── test_risk_rejection.py
    │   ├── test_confirmation_timeout.py
    │   ├── test_margin_calculation.py
    │   ├── test_funding_tracking.py
    │   ├── test_portfolio_routing.py
    │   ├── test_portfolio_isolation.py  # Verify A orders route through A's client, B through B's
    │   └── test_no_cross_transfer.py   # Verify no code path exists for automatic transfers
    └── e2e/
        └── test_paper_trade_cycle.py
```

---

## Tech Stack

| Layer              | Technology                             | Rationale                                          |
| ------------------ | -------------------------------------- | -------------------------------------------------- |
| Language           | Python 3.12+                           | Ecosystem depth for ML, data, crypto               |
| Async runtime      | `asyncio` + `uvloop`                   | High-throughput, 24/7 event processing             |
| Message broker     | Redis Streams                          | Low latency, lightweight, built-in persistence     |
| Database           | PostgreSQL + TimescaleDB extension     | Relational + time-series in one engine             |
| Cache              | Redis                                  | Shared state, rate limiting, pub/sub               |
| Exchange API       | Coinbase INTX REST + WebSocket         | Direct integration, no intermediaries              |
| HTTP client        | `httpx` (async)                        | Async REST calls with connection pooling           |
| WebSocket client   | `websockets`                           | Native async WS with auto-reconnect               |
| Telegram bot       | `python-telegram-bot` v20+ (async)     | Native async, webhook support, inline keyboards    |
| ML/modeling        | `scikit-learn`, `xgboost`, `pytorch`   | Strategy-dependent                                 |
| NLP / sentiment    | `transformers` (crypto-fine-tuned)     | Crypto-specific sentiment classification           |
| Data processing    | `polars` (primary), `pandas` (compat)  | Polars for speed-critical 24/7 paths               |
| Technical analysis | `ta-lib` or custom (in `libs/indicators/`) | Standard indicator computation                 |
| Config             | `pydantic-settings` + YAML             | Typed, validated configuration                     |
| Testing            | `pytest` + `pytest-asyncio`            | Async-native test support                          |
| Containerization   | Docker + Docker Compose                | Reproducible local dev and deployment              |
| Observability      | Prometheus + Grafana + Loki            | Metrics, dashboards, centralized logging           |

---

## Data Models (Core Contracts)

### PortfolioTarget Enum
```python
class PortfolioTarget(str, Enum):
    A = "autonomous"       # No user confirmation, higher risk
    B = "user_confirmed"   # Requires Telegram approval
```

Portfolio routing is handled by `CoinbaseClientPool.get_client(target)`, which returns the `CoinbaseRESTClient` instance authenticated with the correct API key. No portfolio UUIDs are stored or passed in internal models — the API key on each client determines which Coinbase portfolio is accessed.

### MarketSnapshot (ETH-PERP-INTX Specific)
```python
@dataclass
class MarketSnapshot:
    timestamp: datetime
    instrument: str                     # "ETH-PERP-INTX"
    mark_price: Decimal                 # Used for P&L and liquidation
    index_price: Decimal                # Spot index (underlying)
    last_price: Decimal                 # Last traded price
    best_bid: Decimal
    best_ask: Decimal
    spread_bps: float                   # Spread in basis points
    volume_24h: Decimal                 # 24h volume in contracts
    open_interest: Decimal              # Current open interest
    funding_rate: Decimal               # Current hourly funding rate
    next_funding_time: datetime         # Next hourly settlement
    hours_since_last_funding: float     # Fraction of hour since last settlement
    orderbook_imbalance: float          # Bid vs ask depth ratio
    volatility_1h: float               # Realized vol last 1h
    volatility_24h: float              # Realized vol last 24h
```

### FundingPayment (Hourly USDC)
```python
@dataclass
class FundingPayment:
    timestamp: datetime                 # Settlement time
    instrument: str                     # "ETH-PERP-INTX"
    portfolio_target: PortfolioTarget   # A or B
    rate: Decimal                       # Hourly funding rate applied
    payment_usdc: Decimal               # Amount paid (negative) or received (positive)
    position_size: Decimal              # Position size at time of settlement
    position_side: PositionSide         # LONG or SHORT
    cumulative_24h_usdc: Decimal        # Rolling 24h funding total for this portfolio
```

### StandardSignal
```python
@dataclass
class StandardSignal:
    signal_id: str                      # UUID
    timestamp: datetime
    instrument: str                     # "ETH-PERP-INTX"
    direction: PositionSide             # LONG or SHORT
    conviction: float                   # 0.0 to 1.0
    source: SignalSource                # MOMENTUM, FUNDING_ARB, ORDERBOOK, etc.
    time_horizon: timedelta             # Expected holding period
    suggested_target: PortfolioTarget | None  # Strategy's suggestion (router may override)
    entry_price: Decimal | None         # Suggested entry (None = market)
    stop_loss: Decimal | None           # Suggested stop
    take_profit: Decimal | None         # Suggested TP
    reasoning: str                      # Human-readable explanation
    metadata: dict                      # Strategy-specific extras
```

### PerpPosition
```python
@dataclass
class PerpPosition:
    instrument: str                     # "ETH-PERP-INTX"
    portfolio_target: PortfolioTarget   # A or B
    side: PositionSide                  # LONG, SHORT, or FLAT
    size: Decimal                       # Number of contracts (ETH)
    entry_price: Decimal                # Average entry price
    mark_price: Decimal                 # Current mark price
    unrealized_pnl_usdc: Decimal        # Based on mark price
    realized_pnl_usdc: Decimal          # From closed portions
    leverage: Decimal                   # Effective leverage
    initial_margin_usdc: Decimal        # Margin locked
    maintenance_margin_usdc: Decimal    # Minimum margin before liquidation
    liquidation_price: Decimal          # Estimated liquidation price
    margin_ratio: float                 # maintenance_margin / equity (< 1.0 = safe)
    cumulative_funding_usdc: Decimal    # Total hourly funding paid/received
    total_fees_usdc: Decimal            # Total maker+taker fees paid
```

### PortfolioSnapshot (per Coinbase portfolio)
```python
@dataclass
class PortfolioSnapshot:
    timestamp: datetime
    portfolio_target: PortfolioTarget   # A or B
    equity_usdc: Decimal                # Total equity in this portfolio (from Coinbase)
    used_margin_usdc: Decimal
    available_margin_usdc: Decimal
    margin_utilization_pct: float
    positions: list[PerpPosition]
    unrealized_pnl_usdc: Decimal
    realized_pnl_today_usdc: Decimal
    funding_pnl_today_usdc: Decimal     # Net funding received/paid today (up to 24 settlements)
    fees_paid_today_usdc: Decimal
    net_pnl_today_usdc: Decimal         # realized + unrealized + funding - fees

@dataclass
class SystemSnapshot:
    """Combined view of both portfolios — used by monitoring only."""
    timestamp: datetime
    portfolio_a: PortfolioSnapshot
    portfolio_b: PortfolioSnapshot
    combined_equity_usdc: Decimal
```

### Order Lifecycle
```python
class OrderStatus(Enum):
    RISK_APPROVED = "risk_approved"
    PENDING_CONFIRMATION = "pending_confirmation"  # Portfolio B only
    CONFIRMED = "confirmed"
    REJECTED_BY_USER = "rejected_by_user"
    REJECTED_BY_RISK = "rejected_by_risk"
    EXPIRED = "expired"
    SENT_TO_EXCHANGE = "sent_to_exchange"
    OPEN = "open"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED_BY_EXCHANGE = "rejected_by_exchange"

@dataclass
class ProposedOrder:
    order_id: str
    signal_id: str
    instrument: str                     # "ETH-PERP-INTX"
    portfolio_target: PortfolioTarget   # A or B — determines which API client to use
    side: OrderSide                     # BUY or SELL
    size: Decimal                       # In ETH
    order_type: OrderType               # MARKET, LIMIT, STOP_LIMIT
    limit_price: Decimal | None
    stop_loss: Decimal | None
    take_profit: Decimal | None
    leverage: Decimal
    reduce_only: bool
    conviction: float
    sources: list[SignalSource]
    estimated_margin_required_usdc: Decimal
    estimated_liquidation_price: Decimal
    estimated_fee_usdc: Decimal
    estimated_funding_cost_1h_usdc: Decimal
    proposed_at: datetime
```

---

## Message Channels (Redis Streams)

| Channel                         | Publisher          | Consumer(s)                  | Payload               |
| ------------------------------- | ------------------ | ---------------------------- | --------------------- |
| `stream:market_snapshots`       | Ingestion          | Signals, Monitoring          | MarketSnapshot        |
| `stream:funding_updates`        | Ingestion          | Risk, Signals (funding_arb)  | FundingRate (hourly)  |
| `stream:signals`                | Signals            | Alpha                        | StandardSignal        |
| `stream:ranked_ideas:a`         | Alpha (router)     | Risk (Portfolio A limits)    | RankedTradeIdea       |
| `stream:ranked_ideas:b`         | Alpha (router)     | Risk (Portfolio B limits)    | RankedTradeIdea       |
| `stream:approved_orders:a`      | Risk               | Execution (direct, target=A) | ProposedOrder         |
| `stream:approved_orders:b`      | Risk               | Confirmation (Telegram)      | ProposedOrder         |
| `stream:confirmed_orders`       | Confirmation       | Execution (target=B)         | ProposedOrder         |
| `stream:exchange_events:a`      | Execution          | Reconciliation (A)           | OrderEvent / Fill     |
| `stream:exchange_events:b`      | Execution          | Reconciliation (B)           | OrderEvent / Fill     |
| `stream:portfolio_state:a`      | Reconciliation     | Risk (A), Monitoring         | PortfolioSnapshot     |
| `stream:portfolio_state:b`      | Reconciliation     | Risk (B), Monitoring         | PortfolioSnapshot     |
| `stream:funding_payments:a`     | Reconciliation     | Monitoring, Signals          | FundingPayment        |
| `stream:funding_payments:b`     | Reconciliation     | Monitoring, Signals          | FundingPayment        |
| `stream:alerts`                 | Any agent          | Monitoring, Telegram bot     | Alert                 |
| `stream:user_overrides`         | Confirmation       | Monitoring (feedback loop)   | UserOverrideEvent     |

Streams are split by portfolio (`*:a`, `*:b`) wherever data is portfolio-specific. Market data and signals (pre-routing) remain unified.

---

## Confirmation Agent — Telegram Interface

### Trade Confirmation Message (Portfolio B only)
```
🔔 Trade Request #0472 [Portfolio B]

📊 ETH-PERP-INTX — LONG
Size: 2.5 ETH (~$5,580 USDC)
Entry: ~$2,232.00 (limit, maker 0.0125%)
Stop-loss: $2,188 (-1.97%)
Take-profit: $2,320 (+3.94%)
Leverage: 3.2x

📈 Signal: Momentum (0.78) + Sentiment (0.71)
Catalyst: Breakout above 4h EMA, positive CT sentiment surge

⚠️ Risk Summary:
  Margin required: 1,743 USDC
  Liquidation price: $1,891 (15.3% away)
  Est. fees: 0.70 USDC (maker)
  Funding cost (est. next hour): -0.08 USDC (you receive)
  Portfolio B equity: 45,230 USDC | Margin: 38%

✅ Approve · ❌ Reject · ⏸ Delay 30m · ✏️ Modify
```

### Autonomous Trade Notification (Portfolio A — informational only)
```
⚡ Auto-Trade Executed [Portfolio A]

📊 ETH-PERP-INTX — SHORT
Size: 0.3 ETH (~$670 USDC)
Entry: $2,233.50 (limit filled)
Stop-loss: $2,262 (+1.28%)
Strategy: Orderbook Imbalance (0.87)

Portfolio A equity: 4,847 USDC | Margin: 24%
```

Portfolio A trades are sent as **informational notifications** — not for approval. These can be batched (configurable: real-time, hourly, or daily digest).

### Telegram Commands
```
/status           — Show both portfolio snapshots (equity, positions, P&L, margin)
/status a         — Portfolio A detail (queries Coinbase portfolio 75b5eaf1...)
/status b         — Portfolio B detail (queries Coinbase portfolio B)
/funding          — Today's hourly funding breakdown per portfolio
/fees             — Fee summary (maker/taker split, total paid)
/pause            — Pause all trading (both portfolios)
/pause a          — Pause Portfolio A only
/pause b          — Pause Portfolio B only
/resume           — Resume trading
/resume a         — Resume Portfolio A only
/kill             — Emergency: close all positions in both portfolios, halt system
/kill a           — Emergency: close all Portfolio A positions, halt A only
```

Note: There is **no** `/rebalance`, `/transfer`, or `/sweep` command. All fund movements between portfolios are performed by the user directly through the Coinbase interface. The system has no code path for inter-portfolio transfers.

### State Machine (Portfolio B orders)
```
RISK_APPROVED → PENDING_CONFIRMATION
  → CONFIRMED → SENT_TO_EXCHANGE
  → REJECTED → CANCELLED (logged as user override)
  → DELAYED → re-enter PENDING_CONFIRMATION after delay (with price refresh)
  → MODIFIED → re-sent to Risk agent for re-validation
  → EXPIRED (TTL hit) → CANCELLED or AUTO_APPROVED (based on config)
```

---

## Configuration — `configs/default.yaml`

```yaml
instrument:
  id: "ETH-PERP-INTX"
  base_currency: "ETH"
  quote_currency: "USDC"
  tick_size: 0.01
  min_order_size: 0.01

coinbase:
  rest_base_url: "https://api.international.coinbase.com/api/v1"
  ws_market_data_url: "wss://ws-md.international.coinbase.com"
  ws_user_data_url: "wss://ws.international.coinbase.com"
  rate_limit_buffer_pct: 20

fees:
  tier: "VIP1"
  maker_rate: 0.000125               # 0.0125%
  taker_rate: 0.000250               # 0.0250%

funding:
  settlement_interval: "hourly"
  settlement_currency: "USDC"
  settlements_per_day: 24

# ── Portfolio Configuration ─────────────────────────────────────────────

portfolio:
  a:
    name: "Autonomous"
    # API key from COINBASE_INTX_API_KEY_A (portfolio-scoped)
    requires_confirmation: false
  b:
    name: "User-Confirmed"
    # API key from COINBASE_INTX_API_KEY_B (portfolio-scoped)
    requires_confirmation: true

  transfers:
    automatic: false                  # NEVER automatically transfer funds between portfolios
    system_initiated: false           # System has NO authority to move funds between portfolios
    # All transfers are user-initiated via Coinbase UI or Coinbase API directly

  routing:
    # Rules evaluated in order. First match wins.
    rules:
      - condition: "time_horizon < 2h"
        target: "A"
      - condition: "source in [FUNDING_ARB, ORDERBOOK_IMBALANCE, LIQUIDATION_CASCADE]"
        target: "A"
      - condition: "conviction >= 0.85 and time_horizon < 4h"
        target: "A"
      - condition: "default"
        target: "B"

# ── Risk Limits (per portfolio) ─────────────────────────────────────────

risk:
  portfolio_a:                        # Autonomous — aggressive but not reckless
    max_leverage: 5.0
    max_position_size_eth: 3.0
    max_position_pct_equity: 40.0     # Relative to Portfolio A's Coinbase-reported equity
    max_margin_utilization_pct: 70.0
    min_liquidation_distance_pct: 8.0
    max_daily_loss_pct: 10.0          # Kill switch for A only
    max_drawdown_pct: 25.0
    stop_loss_required: true
    max_concurrent_positions: 3
    max_funding_cost_per_day_usdc: 20

  portfolio_b:                        # User-confirmed — conservative
    max_leverage: 3.0
    max_position_size_eth: 8.0
    max_position_pct_equity: 25.0     # Relative to Portfolio B's Coinbase-reported equity
    max_margin_utilization_pct: 50.0
    min_liquidation_distance_pct: 15.0
    max_daily_loss_pct: 5.0
    max_drawdown_pct: 15.0
    stop_loss_required: true
    max_concurrent_positions: 3
    max_funding_cost_per_day_usdc: 100

  global:
    stale_data_halt_seconds: 30

execution:
  default_order_type: "limit"
  limit_offset_bps: 5
  order_ttl_seconds: 120
  max_slippage_bps: 20
  retry_on_rejection: true
  max_retries: 2
  prefer_maker: true                  # Always try limit first: 0.0125% vs 0.0250%

confirmation:                         # Applies to Portfolio B only
  default_ttl_seconds: 300
  stale_price_threshold_pct: 1.0
  auto_approve:
    enabled: false
    max_notional_usdc: 2000
    min_conviction: 0.9
    only_reduce: false
  quiet_hours:
    enabled: true
    start: "23:00"
    end: "07:00"
    timezone: "Europe/Zurich"
    behavior: "queue"
  batching:
    enabled: true
    window_seconds: 30
    max_batch_size: 5
  daily_budget:
    enabled: false
    max_daily_notional_usdc: 20000

notification:                          # Portfolio A trade notifications (informational only)
  autonomous_trades:
    mode: "realtime"                   # realtime | hourly_digest | daily_digest
    include_stop_loss_triggers: true
    include_funding_settlements: false  # Too noisy at 24/day; use /funding command

monitoring:
  funding_alert_threshold_pct: 0.03   # Alert if hourly funding rate exceeds ±0.03%
  margin_alert_threshold_pct: 50.0
  liquidation_distance_alert_pct: 15.0
  ws_reconnect_max_delay_seconds: 30
  heartbeat_interval_seconds: 60
  performance_report:
    frequency: "daily"
    include_portfolio_breakdown: true
    include_funding_attribution: true
    include_fee_breakdown: true
```

---

## Agent Design Principles

1. **Single responsibility.** Each agent owns one phase. No agent both generates signals and executes trades.
2. **Portfolio-awareness.** Every agent downstream of the alpha combiner must know which portfolio it is targeting via `portfolio_target: PortfolioTarget`. Exchange API calls are routed by selecting the correct `CoinbaseRESTClient` from `CoinbaseClientPool.get_client(target)` — the API key on each client determines the portfolio.
3. **No cross-portfolio transfers.** The system **never** moves funds between Portfolio A and Portfolio B. This is a hard architectural constraint — no configuration toggle, no admin override, no Telegram command. The code path does not exist. All fund movements between portfolios are user-initiated via Coinbase directly.
4. **Statelessness.** Agents read state from Redis Streams and PostgreSQL, not local memory. Any instance can restart without data loss.
5. **Idempotency.** Processing the same message twice produces the same result. Use signal/order IDs for deduplication.
6. **24/7 resilience.** Every agent must handle WebSocket disconnects, API timeouts, and rate limits gracefully. Auto-reconnect with exponential backoff is mandatory.
7. **Graceful degradation.** If the Telegram bot is unreachable, Portfolio B trades queue (not drop). Portfolio A continues operating independently. If the Coinbase WS drops, all trading pauses until reconnected.
8. **Funding-awareness.** With hourly settlements, funding impact accumulates faster than on 8h exchanges. Every position-opening decision must project hourly funding costs over the holding period.
9. **Fee-consciousness.** At VIP 1 rates, the maker/taker spread is 0.0125%. Default to limit (maker) orders. Track maker/taker ratio as a performance metric.
10. **Configuration over code.** Strategy parameters, risk limits, and confirmation thresholds live in YAML — not hardcoded. The exceptions are safety guardrails and the no-transfer rule.

---

## Safety & Risk Rules (Non-Negotiable)

These are hardcoded guardrails that **cannot** be overridden by config or by any agent at runtime:

### Global (both portfolios)
1. **Maximum leverage cap: 5x.** Even if Coinbase allows higher, the system never exceeds 5x on any position. Code-level constant.
2. **Mandatory stop-loss.** Every new position in both portfolios must have a stop-loss order placed on the exchange. No exceptions.
3. **Stale data halt.** If mark price data is older than 30 seconds, all new order placement is paused and an alert fires.
4. **No cross-portfolio transfers.** The system never initiates fund transfers between Portfolio A and Portfolio B. This code path does not exist. The `/api/v1/transfers/portfolios` endpoint is never called by any agent. All transfers are user-initiated via Coinbase directly.
5. **No agent self-modification.** No agent can alter risk limits, kill-switch thresholds, portfolio IDs, or confirmation bypass rules at runtime.
6. **Funding rate circuit breaker.** If the absolute hourly funding rate exceeds 0.05%, the system alerts and pauses new position-opening trades (both portfolios) until the rate normalizes or the user manually approves via Telegram.
7. **Portfolio routing integrity.** Orders tagged with `PortfolioTarget.A` must always be sent via the Portfolio A client (authenticated with `COINBASE_INTX_API_KEY_A`). Orders tagged with `PortfolioTarget.B` must always be sent via the Portfolio B client. Routing is enforced by `CoinbaseClientPool.get_client(target)`.

### Portfolio A specific
8. **No all-in trades.** No single Portfolio A trade can use more than 40% of Portfolio A's equity as reported by Coinbase.
9. **Daily loss kill switch (A): 10%.** If Portfolio A's daily loss exceeds 10% of its equity at day start, Portfolio A halts. Portfolio B continues normally.
10. **Max drawdown kill switch (A): 25%.** If Portfolio A's equity drops 25% from its peak, Portfolio A halts until manually re-enabled via Telegram `/resume a`.

### Portfolio B specific
11. **Confirmation required in live mode.** The Telegram confirmation phase cannot be disabled for Portfolio B when `ENVIRONMENT=live`. Auto-approve is capped at 2000 USDC notional.
12. **Daily loss kill switch (B): 5%.** Stricter than A.
13. **Max drawdown kill switch (B): 15%.** Stricter than A.
14. **Max leverage (B): 3x.** Lower than the global 5x cap.

---

## Perp-Specific Concerns for All Agents

### Hourly Funding Rate (USDC)
- Funding settles **every hour**, not every 8 hours. 24 funding events per day.
- Each settlement is small individually but accumulates quickly. A position held for 24 hours experiences 24 separate funding charges/payments.
- The `funding_arb` strategy can exploit intra-day funding rate oscillations.
- The risk agent must project **cumulative funding cost** over the expected holding period.
- The reconciliation agent tracks every hourly payment per Coinbase portfolio — funding for Portfolio A positions posts to Portfolio A's balance, and likewise for B. This is handled by the exchange automatically since they are separate portfolios.
- Monitoring reports funding as hourly, daily, and weekly aggregates per portfolio.

### Liquidation
- Liquidation is based on **mark price**, not last traded price.
- Risk agent enforces minimum liquidation distance (8% for A, 15% for B).
- Since portfolios have **physically isolated margin** at the exchange level, a liquidation in Portfolio A cannot cascade to Portfolio B and vice versa. This is the strongest possible isolation guarantee.

### Leverage Drift
- Effective leverage changes as mark price moves. A 3x long becomes higher leverage as price drops.
- Reconciliation agent continuously recomputes effective leverage per position and alerts if it drifts above portfolio-specific limits.

### 24/7 Operation
- No market hours. The system must handle the user being asleep or unreachable.
- Portfolio A operates fully autonomously — no user dependency.
- Portfolio B's quiet hours config queues or rejects trades when the user is likely asleep.
- The orchestrator watchdog must ensure no agent has silently crashed at any hour.

### Opposing Positions Across Portfolios
- It is valid for Portfolio A to be SHORT while Portfolio B is LONG (or vice versa). These are independent Coinbase portfolios with independent margin. The system should not treat this as an error or conflict — each portfolio operates with its own thesis and risk management.
- The monitoring agent should log when opposing positions exist and include it in the daily report, but this is informational, not a warning.

---

## Environment Variables

```env
# Coinbase INTX (per-portfolio API keys — keys are portfolio-scoped)
COINBASE_INTX_API_KEY_A=
COINBASE_INTX_API_SECRET_A=
COINBASE_INTX_PASSPHRASE_A=
COINBASE_INTX_API_KEY_B=
COINBASE_INTX_API_SECRET_B=
COINBASE_INTX_PASSPHRASE_B=
COINBASE_INTX_REST_URL=https://api.international.coinbase.com
COINBASE_INTX_WS_MARKET_URL=wss://ws-md.international.coinbase.com
COINBASE_INTX_WS_USER_URL=wss://ws.international.coinbase.com

# Telegram
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
TELEGRAM_WEBHOOK_URL=

# Data Providers (optional, for enrichment)
COINGLASS_API_KEY=                     # Open interest, liquidation data
GLASSNODE_API_KEY=                     # On-chain metrics
CRYPTOQUANT_API_KEY=                   # On-chain + exchange data
NEWS_API_KEY=
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=

# Infrastructure
REDIS_URL=redis://localhost:6379
DATABASE_URL=postgresql://user:pass@localhost:5432/phantom_perp
LOG_LEVEL=INFO
ENVIRONMENT=paper                      # paper | live
```

---

## Development Workflow

### First-Time Setup
```bash
git clone <repo-url> && cd phantom-perp
cp .env.example .env                   # Fill in Coinbase API keys (one per portfolio), Telegram token
make setup                             # Install deps, spin up Docker (Redis, Postgres, TimescaleDB)
make seed                              # Load historical ETH-PERP candles + hourly funding rates
```

### Running Locally
```bash
make paper                             # Full pipeline (both portfolios) with simulated execution
make paper-a                           # Portfolio A only (autonomous)
make paper-b                           # Portfolio B only (with Telegram confirmation)
make agent AGENT=signals               # Run a single agent in isolation
make test                              # All tests
make test-integration                  # Integration tests
make logs AGENT=confirmation           # Tail logs for confirmation agent
make funding-report                    # Analyze hourly funding rate patterns
make portfolio-report                  # Show A vs B performance comparison
```

### Deployment (Oracle Cloud)
The system is deployed to an Oracle Cloud Always Free AMD instance (`140.238.222.244`, user `opc`). Images are cross-compiled for `linux/amd64` locally (Apple Silicon) and transferred via SCP.

```bash
./scripts/deploy.sh                    # Full build + deploy all agents
./scripts/deploy.sh risk execution     # Rebuild specific agents only
./scripts/deploy.sh --status           # Check remote container status
./scripts/deploy.sh --logs risk        # Tail remote agent logs
./scripts/status.sh                    # Full status report (host, containers, streams, market data)
./scripts/status.sh --short            # Compact resource summary
```

The server runs 10 containers (8 agents + Redis + PostgreSQL/TimescaleDB) on 503 MB RAM + 4 GB swap. See `docker-compose.prod.yml` for production compose config.

### Adding a New Strategy
1. Create `agents/signals/strategies/your_strategy.py`
2. Implement the `SignalStrategy` interface (see `base.py`)
3. Add config at `configs/strategies/your_strategy.yaml`
4. Register in `agents/signals/main.py`
5. Decide default routing: add a rule in `configs/default.yaml` under `portfolio.routing.rules`
6. Write tests in `agents/signals/tests/`
7. Deploy — alpha combiner picks it up with weight 0.0 (shadow mode)

---

## Testing Strategy

| Level       | Scope                                        | Tooling                        |
| ----------- | -------------------------------------------- | ------------------------------ |
| Unit        | Individual functions and strategy logic       | `pytest`                       |
| Integration | Agent-to-agent communication via Redis        | `pytest` + Docker Compose      |
| Integration | Portfolio routing isolation (A ≠ B client)     | `pytest` + Docker Compose     |
| Integration | No transfer code path exists                  | `pytest` (static + runtime)   |
| E2E         | Full pipeline paper-trade (both portfolios)   | `pytest` + paper broker mock   |
| Backtest    | Strategy performance on historical data       | `scripts/backtest.py`          |
| Shadow      | New models running alongside live (no trades) | `agents/monitoring/shadow_mode.py` |

**Critical tests:**
- **`test_portfolio_isolation.py`** — Verifies orders for Portfolio A are routed through the Portfolio A client and orders for Portfolio B through the Portfolio B client. A cross-portfolio routing is a critical failure.
- **`test_no_cross_transfer.py`** — Verifies that no code path in any agent calls the `/api/v1/transfers/portfolios` endpoint. This is both a static analysis test (grep the codebase) and a runtime test (mock the endpoint and assert it's never called during a full pipeline run).
- **`test_portfolio_registry.py`** — Verifies the `PortfolioTarget` enum values and that the registry re-exports them correctly.

Every PR must pass unit + integration tests. E2E runs nightly. Backtests run on demand.

---

## Key Conventions

- **Naming:** snake_case for files/variables, PascalCase for classes, UPPER_SNAKE for constants.
- **Decimals:** Use `Decimal` (not `float`) for all prices, sizes, and monetary values.
- **Currency:** All monetary values are in USDC unless explicitly stated. Variable names should include `_usdc` suffix (e.g., `margin_required_usdc`).
- **Portfolio routing:** Use `PortfolioTarget` enum (not portfolio UUID strings) throughout internal code. Route API calls via `CoinbaseClientPool.get_client(target)` — never pass portfolio IDs to REST client methods.
- **Type hints:** Required everywhere. `pydantic` for external data; `dataclasses` for internal.
- **Async:** All I/O must be async. Use `asyncio.TaskGroup` for parallel work.
- **Error handling:** Never silently swallow. Log with full context, then retry or escalate.
- **Logging:** Structured JSON via `structlog`. Every line includes `agent_name`, `portfolio_target`, `trace_id`, `timestamp`, and `instrument`.
- **Timestamps:** Always UTC. Use `datetime.now(UTC)`. Never naive datetimes.
- **Docstrings:** Required for all public functions/classes. Google-style.
- **Commits:** Conventional Commits (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`).
- **Branch strategy:** `main` (always deployable) → `develop` (integration) → `feature/*`.
