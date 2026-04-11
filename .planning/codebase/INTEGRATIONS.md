# External Integrations

**Analysis Date:** 2026-04-08

## APIs & External Services

**Coinbase Advanced Trade API:**
- Spot/perpetual order placement, portfolio state queries, WebSocket market data
- SDK/Client: Custom `CoinbaseRESTClient` in `libs/coinbase/rest_client.py`
- WebSocket: Custom `CoinbaseWSClient` in `libs/coinbase/ws_client.py`
- Auth: ES256 JWT via `libs/coinbase/auth.py` (Cloud API keys, not legacy API keys)
- Endpoints: `https://api.coinbase.com`, `wss://advanced-trade-ws.coinbase.com`, `wss://advanced-trade-ws-user.coinbase.com`
- Used by: Ingestion (market data, candles, funding rates), Execution (order placement), Reconciliation (portfolio state)
- Rate limiting: Implemented in `libs/coinbase/rate_limiter.py` with configurable buffer (20% by default)

**Claude CLI (Market Analysis):**
- LLM inference for market analysis signals (trend, reversal, volatility assessment)
- Client: `claude -p` via `asyncio.create_subprocess_exec()` in `agents/signals/claude_market_client.py`
- Auth: Local Claude CLI installation (no API key required)
- Used by: Signals agent (`ClaudeMarketAnalysisStrategy`) for continuous market regime analysis
- Response format: JSON via tool_use (submit_market_analysis tool with LONG/SHORT/NO_SIGNAL)
- Rate limiting: API-side; no client-side limiting implemented
- Cost optimization: Per-request cost minimized via structured JSON output clamping

**Finnhub News API:**
- Crypto news headlines for context-aware signal generation
- Endpoint: `https://finnhub.io/api/v1/news` (category=crypto)
- Auth: `FINNHUB_API_KEY` environment variable
- Used by: Signals agent via `agents/signals/news_client.py::fetch_crypto_headlines()`
- Returns: List of `CryptoHeadline` objects with title, published_at, source, currencies
- Degrades gracefully: Returns empty list on timeout or API error; agent continues without news context

**ForexFactory Economic Calendar:**
- High-impact USD economic events (CPI, NFP, etc.) for macroeconomic context
- Endpoint: `https://nfs.faireconomy.media/ff_calendar_thisweek.json` and `ff_calendar_nextweek.json`
- Auth: None required (public JSON feed)
- Used by: Signals agent via `agents/signals/news_client.py::fetch_economic_events()`
- Returns: List of `EconomicEvent` objects (event name, time, impact level, estimate, previous)
- Degrades gracefully: Returns empty list on timeout or parse error

**Optional Data Providers (Configured but Not Required):**
- Coinglass API (`COINGLASS_API_KEY`) - Liquidation data, long/short ratios
- Glassnode API (`GLASSNODE_API_KEY`) - On-chain metrics
- CryptoQuant API (`CRYPTOQUANT_API_KEY`) - Exchange flow data
- Reddit API (`REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`) - Sentiment analysis

All optional providers: Referenced in config template but no active pollers implemented in current codebase.

## Data Storage

**Databases:**
- **PostgreSQL 16 + TimescaleDB extension**
  - Primary store for orders, fills, funding payments, performance snapshots
  - Connection: `DATABASE_URL` env var (e.g., `postgresql://phantom:phantom_dev@localhost:5432/phantom_perp`)
  - Client: `SQLAlchemy[asyncio]` + `asyncpg` (native async driver)
  - ORM: SQLAlchemy 2.0 with async context manager (`RelationalStore.session()` in `libs/storage/relational.py`)
  - Schema: `libs/storage/models.py` defines `FillRecord`, `OrderSignalRecord`, and other tables
  - Used by: Execution (order/fill storage), Reconciliation (state queries), Tuner (training data)
  - Initialization: `init_db()` in `agents/*/main.py` creates all tables on startup (idempotent)

**File Storage:**
- Local filesystem only
- Strategy config YAMLs mounted as Docker volume: `strategy_configs:/app/configs/strategies` (read-only in most containers)
- Tuner container has read-write access to update strategies post-optimization

**Caching:**
- **Redis (In-Memory)**
  - Message broker via Redis Streams (append-only event log)
  - Cache: Peak equity persistence (Redis hash: `phantom:perf:peak_equity`)
  - Connection: `REDIS_URL` env var (default: `redis://localhost:6379`)
  - Client: `redis.asyncio` (async client)
  - Configuration: 7-alpine image, 64MB max memory, LRU eviction policy
  - Stream names: Centrally managed in `libs/messaging/channels.py` (Channel class methods)

## Authentication & Identity

**Auth Provider:**
- Custom implementation (no third-party OAuth/OIDC)

**Coinbase Advanced API:**
- Method: ES256 JWT with ephemeral 2-minute lifetime
- Implementation: `CoinbaseAuth` class in `libs/coinbase/auth.py`
- Key format: Cloud API key UUID + PEM-encoded EC private key
- Signature: JWT.encode() with PyJWT library, algorithm="ES256"
- Portfolio isolation: Two separate API key pairs (Route A and Route B) for credential segregation
- No caching: JWT generated fresh per request (~<1ms cost)

**Telegram Bot Token:**
- Method: Bearer token in Telegram HTTP API calls
- Implementation: `python-telegram-bot` library handles token setup
- Env var: `TELEGRAM_BOT_TOKEN`
- Used by: `agents/confirmation/bot.py` for Route B order confirmations
- Webhook support: Optional `TELEGRAM_WEBHOOK_URL` for incoming updates (unused in current code; polling mode used)

## Monitoring & Observability

**Error Tracking:**
- Not configured (no Sentry, Rollbar, or third-party error tracking)
- Errors logged to stdout via `structlog` with full context

**Logs:**
- Framework: `structlog` with JSON serialization
- Output: Stdout (Docker container logs)
- Pattern: Each agent calls `setup_logging()` in `main.py`
- Fields: agent_name (binding), timestamp, log_level, event_name, contextual fields (instrument, route, error)
- Examples: `agents/ingestion/main.py` line 48, `agents/signals/main.py` line 80

**Metrics:**
- Not exposed via Prometheus or other monitoring service
- Performance metrics computed in-memory by `agents/monitoring/main.py`:
  - `DualPerformanceTracker`: Per-portfolio P&L, Sharpe ratio, drawdown
  - `DualFundingReporter`: Hourly funding payment aggregation
  - `DualFeeTracker`: Cumulative fee tracking

## CI/CD & Deployment

**Hosting:**
- Docker Compose for local development
- Docker images pre-built for `linux/amd64` (cross-compiled on Apple Silicon via buildx)
- SCP transfer to production host (manual deployment, no orchestration)

**CI Pipeline:**
- Not detected (no GitHub Actions, GitLab CI, or Jenkins configuration in codebase)
- Development: Pre-commit hooks via `ruff` and `mypy` (configured in `pyproject.toml`)

**Build System:**
- Hatchling (Python build backend in `pyproject.toml`)
- Docker: Individual Dockerfile per agent (`agents/*/Dockerfile`)
- Base image: `python:3.13-slim`
- Dependencies: `pip install .` from `pyproject.toml`
- System deps: TA-Lib C library compiled in Docker layer 1 (wget, build-essential, configure, make)

## Environment Configuration

**Required Environment Variables:**
- `COINBASE_ADV_API_KEY_A` - Cloud API Key UUID for Route A
- `COINBASE_ADV_API_SECRET_A` - PEM-encoded EC private key for Route A
- `COINBASE_ADV_REST_URL` - Coinbase REST endpoint (default: `https://api.coinbase.com`)
- `COINBASE_ADV_WS_MARKET_URL` - Coinbase market WebSocket endpoint
- `COINBASE_ADV_WS_USER_URL` - Coinbase user WebSocket endpoint
- `COINBASE_PORTFOLIO_ID` - Portfolio UUID (primary, Route A)
- `REDIS_URL` - Redis connection string
- `DATABASE_URL` - PostgreSQL connection string

**Optional Environment Variables:**
- `COINBASE_ADV_API_KEY_B`, `COINBASE_ADV_API_SECRET_B` - Route B credentials (dual-portfolio)
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` - Telegram confirmations
- `TELEGRAM_WEBHOOK_URL` - Telegram webhook URL (unused)
- `COINGLASS_API_KEY`, `GLASSNODE_API_KEY`, `CRYPTOQUANT_API_KEY`, `FINNHUB_API_KEY`, `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET` - Optional data providers
- `ENVIRONMENT` - Operating mode (paper or live, default: paper)
- `LOG_LEVEL` - Logging verbosity (default: INFO)
- `POSTGRES_PASSWORD` - PostgreSQL password (default: phantom_dev)
- `TUNER_CRON` - Scheduler cron expression (default: `0 0 * * *` — daily at midnight)

**Secrets Location:**
- Environment variables in `.env` file (not committed to git)
- Template: `.env.example` (no actual secrets)
- Docker Compose: Secrets passed via `env_file: .env`

## Webhooks & Callbacks

**Incoming:**
- Telegram webhook URL optional but not implemented (polling mode used instead)
- Coinbase does not support incoming webhooks in Advanced Trade API (all data fetched via polling/streaming)

**Outgoing:**
- None detected (no callbacks to external systems)

## Rate Limiting & API Quotas

**Coinbase Advanced:**
- Implementation: `RateLimiter` class in `libs/coinbase/rate_limiter.py`
- Strategy: Respects Coinbase's RateLimit-Remaining header with configurable buffer (20% by default)
- Backoff: Exponential retry on 429 (rate limit) with max 2 retries
- Scope: Per-client (route-scoped via `CoinbaseClientPool`)

**Claude CLI (Market Analysis):**
- Rate limiting: API-side (no client-side limiting)
- Timeout: Subprocess timeout configured per call site

**Finnhub & ForexFactory:**
- Rate limiting: API-side
- Timeout: 10 seconds (hardcoded in `agents/signals/news_client.py`)
- Graceful degradation: Returns empty list on timeout

## Dependencies Chain

**Core Flow:**
1. **Ingestion** fetches market data from Coinbase WebSocket/REST → publishes to Redis Streams
2. **Signals** consumes market snapshots → runs strategies (Claude API, technical indicators) → publishes signals to Redis
3. **Alpha** combines signals → routes via portfolio rules → publishes ranked ideas to Redis
4. **Risk** validates ideas against portfolio state (from Reconciliation) → publishes approved orders
5. **Confirmation** (Route B only) presents via Telegram → publishes confirmed orders
6. **Execution** places orders on Coinbase → publishes fills
7. **Reconciliation** queries Coinbase portfolio state → publishes snapshots + funding
8. **Monitoring** tracks performance, funding, alerts

**External Dependencies:**
- Coinbase API: Critical (no trading without it)
- Redis: Critical (message broker)
- PostgreSQL: Critical (state persistence)
- Claude CLI: Optional (market analysis feature, degrades gracefully)
- Telegram Bot: Required only for Route B (Route A trades autonomously)
- Finnhub/ForexFactory: Optional (news context, degrades gracefully)

---

*Integration audit: 2026-04-08*
