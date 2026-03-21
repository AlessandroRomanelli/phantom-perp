# External Integrations

**Analysis Date:** 2025-03-21

## APIs & External Services

**Coinbase INTX (Primary):**
- Service: Coinbase International Exchange (perpetual futures)
  - SDK/Client: Custom async REST client (`libs/coinbase/rest_client.py`) + WebSocket client (`libs/coinbase/ws_client.py`)
  - Auth: HMAC-SHA256 request signing via `libs/coinbase/auth.py`
    - Environment vars: `COINBASE_INTX_API_KEY_A/B`, `COINBASE_INTX_API_SECRET_A/B`, `COINBASE_INTX_PASSPHRASE_A/B`
  - Portfolio Routing: API keys are **portfolio-scoped** on Coinbase. Each key can only access one portfolio.
    - Portfolio A client authenticated with `KEY_A/SECRET_A/PASSPHRASE_A` (via `COINBASE_INTX_*_A` env vars)
    - Portfolio B client authenticated with `KEY_B/SECRET_B/PASSPHRASE_B` (via `COINBASE_INTX_*_B` env vars)
  - Client Pool: `CoinbaseClientPool.get_client(target)` selects the correct REST client per `PortfolioTarget` enum
  - REST API Base URL: `https://api.international.coinbase.com` (overridable via `COINBASE_INTX_REST_URL`)
  - Endpoints used:
    - `GET /api/v1/instruments` - List all tradable instruments
    - `GET /api/v1/instruments/{product_id}/book` - Fetch L2 order book for ETH-PERP-INTX, etc.
    - `GET /api/v1/instruments/{product_id}/candles` - OHLCV data (1m, 5m, 15m, 1h, 6h granularities)
    - `GET /api/v1/instruments/{product_id}/funding` - Hourly funding rate history
    - `POST /api/v1/orders` - Place new orders (portfolio-scoped)
    - `DELETE /api/v1/orders/{order_id}` - Cancel orders (portfolio-scoped)
    - `GET /api/v1/orders` - List open orders (portfolio-scoped)
    - `GET /api/v1/positions` - Query current positions (portfolio-scoped)
    - `GET /api/v1/portfolios/{portfolio_id}` - Account equity and margin (portfolio-scoped)
    - `GET /api/v1/fills` - Order execution history (portfolio-scoped)

**Coinbase INTX WebSocket Feeds:**
- Market Data URL: `wss://ws-md.international.coinbase.com` (public)
  - Channels subscribed:
    - `MARKET_DATA` - Tick, L2 book updates, trade events for all active instruments
    - `INSTRUMENTS` - Funding rate updates, instrument status changes
- User Data URL: `wss://ws.international.coinbase.com` (authenticated via API key)
  - Channels subscribed (portfolio-scoped — WebSocket filter by portfolio ID on receipt):
    - `RISK` - Position updates, margin changes, liquidation notifications
    - `ORDERS` - Order status changes, fills, rejections
  - Auth: Not yet implemented (reserved for future JWT-based auth)
  - WebSocket client implementation: `libs/coinbase/ws_client.py`
    - Auto-reconnect with exponential backoff (max 30 seconds)
    - Ping/pong keepalive (ping every 20s, 10s timeout)

## Data Storage

**Databases:**

**PostgreSQL 16 + TimescaleDB:**
- Provider: TimescaleDB extension on PostgreSQL
- Docker image: `timescale/timescaledb:latest-pg16`
- Connection: `postgresql+asyncpg://phantom:phantom_dev@localhost:5432/phantom_perp`
  - Async driver: `asyncpg` (0.29+)
  - ORM: SQLAlchemy 2.0+ with async session factory
- Storage classes:
  - `RelationalStore` (`libs/storage/relational.py`) - Orders, trades, configuration
  - `TimeseriesStore` (`libs/storage/timeseries.py`) - OHLCV candles, hourly funding rates, P&L snapshots
- Memory limits (production):
  - `shared_buffers=64MB`
  - `work_mem=4MB`
  - `effective_cache_size=128MB`

**File Storage:**
- Local filesystem only (no S3, GCS, or similar)
- Configuration and scripts stored in repository (`/configs/`, `/scripts/`)

**Caching:**
- Redis (no TTL-based cache for production; Streams provide persistence)

## Message Broker

**Redis Streams:**
- Provider: Redis 7-alpine
- Connection: `redis://localhost:6379` (overridable via `REDIS_URL`)
- Client: `redis.asyncio` (aioredis 5.0+)
- Serialization: `orjson` (fast JSON) for payload encoding
- Max memory (production): `64MB` with `maxmemory-policy=allkeys-lru` eviction
- Streams used (see `libs/messaging/channels.py` for registry):
  - `stream:market_snapshots` - Ingestion → Signals, Monitoring
  - `stream:funding_updates` - Ingestion → Risk, Signals (funding_arb strategy)
  - `stream:signals` - Signals → Alpha
  - `stream:ranked_ideas:a` - Alpha (router) → Risk (Portfolio A)
  - `stream:ranked_ideas:b` - Alpha (router) → Risk (Portfolio B)
  - `stream:approved_orders:a` - Risk → Execution (Portfolio A, direct)
  - `stream:approved_orders:b` - Risk → Confirmation (Telegram bot)
  - `stream:confirmed_orders` - Confirmation → Execution (Portfolio B, after user approval)
  - `stream:exchange_events:a` - Execution → Reconciliation (Portfolio A fills)
  - `stream:exchange_events:b` - Execution → Reconciliation (Portfolio B fills)
  - `stream:portfolio_state:a` - Reconciliation → Risk, Monitoring
  - `stream:portfolio_state:b` - Reconciliation → Risk, Monitoring
  - `stream:funding_payments:a` - Reconciliation → Monitoring, Signals (Portfolio A)
  - `stream:funding_payments:b` - Reconciliation → Monitoring, Signals (Portfolio B)
  - `stream:alerts` - Any agent → Monitoring, Telegram bot
  - `stream:user_overrides` - Confirmation → Monitoring (feedback loop)

## Authentication & Identity

**Coinbase INTX API Key Auth:**
- Type: HMAC-SHA256 request signing (API key + secret + passphrase)
- Implementation: `libs/coinbase/auth.py`
  - Signature = HMAC-SHA256(timestamp + HTTP method + path + body, api_secret)
  - Headers: `CB-ACCESS-KEY`, `CB-ACCESS-SIGN`, `CB-ACCESS-TIMESTAMP`, `CB-ACCESS-PASSPHRASE`
- **Portfolio Routing:** API keys are portfolio-scoped at Coinbase level. No portfolio ID parameter in API calls — the key itself determines which portfolio is accessed.

**Telegram Bot Auth:**
- Type: Bot token (long-lived authentication)
- Environment var: `TELEGRAM_BOT_TOKEN`
- Client library: `python-telegram-bot` (21+)
- Webhook mode: Receives updates via HTTP POST to `TELEGRAM_WEBHOOK_URL`
- Chat ID discovery:
  - Pre-configured via `TELEGRAM_CHAT_ID` environment variable, OR
  - Learned at runtime when user sends `/start` command
- Implementation: `agents/confirmation/bot.py`

## Monitoring & Observability

**Error Tracking:**
- Not external — errors logged locally via `structlog` JSON output

**Logs:**
- Framework: `structlog` (24.1+) with JSON output
- Setup: `libs/common/logging.py`
- Output: stdout (captured by Docker, piped to host via logging driver)
- Fields: `timestamp`, `agent_name`, `portfolio_target`, `trace_id`, `instrument`, `event`
- Aggregation (production): Prometheus + Loki via Docker logging driver (configured in `infra/monitoring/`)

**Metrics (Future):**
- Prometheus scrape targets (defined in `infra/monitoring/prometheus/`)
- Dashboards: Grafana (in `infra/monitoring/grafana/`)
- Metrics exposed: Not yet implemented (agents currently output only logs)

## CI/CD & Deployment

**Hosting:**
- Oracle Cloud Always Free AMD instance (public IP `140.238.222.244`)
- `opc` user account, Ubuntu-based
- Docker containers orchestrated via `docker-compose.prod.yml`

**CI Pipeline:**
- Not automated — manual build and deploy via `scripts/deploy.sh`
  - Builds all agent images locally (Apple Silicon)
  - Cross-compiles to `linux/amd64` via Docker buildx
  - Transfers tarballs via SCP to production host
  - Restarts containers via SSH

**Deployment Process:**
```bash
./scripts/deploy.sh                    # Full rebuild + deploy all agents
./scripts/deploy.sh risk execution     # Selective rebuild
./scripts/status.sh                    # Check deployment status
```

## Environment Configuration

**Required Environment Variables:**

**Coinbase INTX (Portfolio-Scoped):**
- `COINBASE_INTX_API_KEY_A` - Portfolio A API key
- `COINBASE_INTX_API_SECRET_A` - Portfolio A API secret
- `COINBASE_INTX_PASSPHRASE_A` - Portfolio A passphrase
- `COINBASE_INTX_API_KEY_B` - Portfolio B API key
- `COINBASE_INTX_API_SECRET_B` - Portfolio B API secret
- `COINBASE_INTX_PASSPHRASE_B` - Portfolio B passphrase
- `COINBASE_INTX_REST_URL` - REST base URL (default: `https://api.international.coinbase.com`)
- `COINBASE_INTX_WS_MARKET_URL` - Market data WebSocket URL (default: `wss://ws-md.international.coinbase.com`)
- `COINBASE_INTX_WS_USER_URL` - User data WebSocket URL (default: `wss://ws.international.coinbase.com`)

**Portfolio IDs (for reference only — not used by system):**
- `COINBASE_PORTFOLIO_A_ID` - Portfolio A UUID (informational in config)
- `COINBASE_PORTFOLIO_B_ID` - Portfolio B UUID (informational in config)

**Telegram:**
- `TELEGRAM_BOT_TOKEN` - Bot authentication token
- `TELEGRAM_CHAT_ID` - Target chat ID (pre-configured or learned at runtime)
- `TELEGRAM_WEBHOOK_URL` - Webhook URL for receiving updates (if webhook mode enabled)

**Optional Data Providers (Not Yet Integrated):**
- `COINGLASS_API_KEY` - Open interest and liquidation data (placeholder)
- `GLASSNODE_API_KEY` - On-chain metrics (placeholder)
- `CRYPTOQUANT_API_KEY` - Exchange + on-chain data (placeholder)
- `NEWS_API_KEY` - News aggregation (placeholder)
- `REDDIT_CLIENT_ID` - Reddit sentiment (placeholder)
- `REDDIT_CLIENT_SECRET` - Reddit sentiment (placeholder)

**Infrastructure:**
- `REDIS_URL` - Redis connection string (default: `redis://localhost:6379`)
- `DATABASE_URL` - PostgreSQL/TimescaleDB connection string (default: `postgresql://phantom:phantom_dev@localhost:5432/phantom_perp`)
- `LOG_LEVEL` - Logging verbosity (default: `INFO`)
- `ENVIRONMENT` - Operating mode (`paper` for simulated, `live` for real)
- `POSTGRES_PASSWORD` - Postgres superuser password (Docker only)

**Secrets Location:**
- `.env` file (git-ignored)
- Docker Compose reads `.env` and injects into containers via `env_file: .env`
- Never commit `.env` to git — only `.env.example` with placeholders

## Webhooks & Callbacks

**Incoming (Not Implemented Yet):**
- Telegram webhook endpoint (placeholder URL in `TELEGRAM_WEBHOOK_URL`)
  - Expected POST endpoint for receiving Telegram bot updates

**Outgoing (None):**
- System does not call external APIs for notifications or callbacks
- Trade notifications go to Telegram via `python-telegram-bot` (bot-initiated, not webhook)

## Data Provider Integrations

**Currently Not Integrated (Configured But Unused):**
- CoinGlass - Open interest and liquidation cascade tracking (API key placeholder in env)
- Glassnode - On-chain metrics (ETH staking, whale moves) (API key placeholder in env)
- CryptoQuant - Exchange data and on-chain signals (API key placeholder in env)
- News APIs - News sentiment and narrative analysis (API key placeholder in env)
- Reddit - Community sentiment and discussion monitoring (OAuth placeholder in env)
- Twitter/CT - Crypto Twitter sentiment (not configured — would require API key)

These integrations are designed to be plugged into the **ingestion agent** (`agents/ingestion/sources/`) as optional enrichment sources. Current implementation relies solely on Coinbase INTX market data and internal on-chain-derived signals.

---

*Integration audit: 2025-03-21*
