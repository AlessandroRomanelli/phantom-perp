# Technology Stack

**Analysis Date:** 2025-03-21

## Languages

**Primary:**
- Python 3.12+ - Full application codebase (agents, libraries, scripts)
  - Type hints required via `mypy` strict mode
  - Async-first with `asyncio` and `uvloop`

## Runtime

**Environment:**
- Python 3.13-slim base image (Docker)
- `asyncio` for concurrent event handling (24/7 operation)
- `uvloop` (0.19+) on non-Windows platforms for higher throughput

**Package Manager:**
- `pip` via Hatchling build system
- Lockfile: Virtual environment via Docker isolation
- Production: Pre-built Docker images for `linux/amd64`

## Frameworks

**Core:**
- `python-telegram-bot` (21+) - Telegram bot integration for Portfolio B confirmations
  - Webhook-based async Application with inline keyboard handlers
  - Command handlers for `/status`, `/pause`, `/resume`, `/kill`

**Async HTTP:**
- `httpx` (0.27+) - Async REST client for Coinbase INTX API
  - Connection pooling, timeout management (10s default, 5s connect)
  - Rate limiting via shared `RateLimiter` per portfolio

**WebSocket:**
- `websockets` (13+) - Async WebSocket client for Coinbase real-time feeds
  - Market data and user-data channels
  - Auto-reconnect with exponential backoff (up to 30s)

**Data Processing:**
- `polars` (1.0+) - Primary for speed-critical 24/7 data paths
- `pandas` - Compatibility/fallback (used via polars where possible)

**Configuration:**
- `pydantic` (2.6+) - Data validation for all models
- `pydantic-settings` (2.2+) - Environment variable and config file loading
- `PyYAML` (6+) - Strategy and instrument YAML config parsing

**Database & Storage:**
- `SQLAlchemy[asyncio]` (2.0+) - Async ORM for PostgreSQL
- `asyncpg` (0.29+) - Native async PostgreSQL driver
- `redis` (5.0+) - Async client for Redis Streams and caching

**Serialization:**
- `orjson` (3.9+) - Fast JSON encoding/decoding for message payloads and WebSocket

**Technical Analysis:**
- `ta-lib` (0.4+) - TA-Lib C library compiled in Docker
  - Indicators: moving averages, oscillators, volatility, volume
- `numpy` (1.26+) - Numerical computation for indicators

**Machine Learning & Modeling:**
- `scikit-learn` (1.4+) - ML algorithms for strategy features
- `xgboost` (2+) - Gradient boosting for ensemble models

**Testing:**
- `pytest` (8+) - Test runner with async support
- `pytest-asyncio` (0.23+) - Async test fixture support
- `pytest-cov` (5+) - Code coverage reporting
- `respx` (0.21+) - Mock `httpx` requests for unit tests
- `fakeredis` (2.21+) - In-memory Redis mock for integration tests
- `freezegun` (1.4+) - Time freezing for deterministic tests

**Code Quality:**
- `ruff` (0.3+) - Fast linter/formatter (configured in `pyproject.toml`)
  - Line length: 100 characters
  - Selected rules: E, F, I, N, W, UP, B, A, SIM, TCH
- `mypy` (1.9+) - Static type checker (strict mode)
  - `disallow_untyped_defs: true` enforced

**Build & Orchestration:**
- `hatchling` - Build backend
- Docker & Docker Compose - Local dev and production orchestration
- `Dockerfile` per agent with layer caching optimization

## Key Dependencies

**Critical (Core Trading):**
- `httpx` + `websockets` - Exchange connectivity (Coinbase INTX)
- `redis` - Message broker (Redis Streams)
- `SQLAlchemy` + `asyncpg` - Persistent state (PostgreSQL + TimescaleDB)
- `python-telegram-bot` - Portfolio B confirmations (user interaction)

**Infrastructure:**
- `structlog` (24.1+) - Structured JSON logging across all agents
- `orjson` - Serialization for Redis Streams payloads
- `uvloop` - High-performance event loop (non-Windows)

**Data & Compute:**
- `polars` - DataFrame operations for signal generation
- `ta-lib` - Technical indicators (pre-compiled C binary in Docker)
- `numpy` - Numerical arrays
- `scikit-learn` + `xgboost` - ML models for strategy prediction

## Configuration

**Environment:**
- Configuration via `.env` file (environment variables)
- Runtime overrides via `configs/default.yaml` (YAML)
- Per-strategy configs in `configs/strategies/` (strategy-specific parameters)

**Key Environment Variables:**
- `COINBASE_INTX_API_KEY_A`, `API_SECRET_A`, `PASSPHRASE_A` - Portfolio A credentials
- `COINBASE_INTX_API_KEY_B`, `API_SECRET_B`, `PASSPHRASE_B` - Portfolio B credentials
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` - Telegram bot setup
- `REDIS_URL` - Redis connection (default: `redis://localhost:6379`)
- `DATABASE_URL` - PostgreSQL connection (default: `postgresql://phantom:phantom_dev@localhost:5432/phantom_perp`)
- `ENVIRONMENT` - Operating mode (`paper` or `live`)
- `LOG_LEVEL` - Logging verbosity (INFO, DEBUG, etc.)

**Build Configuration:**
- `pyproject.toml` - Python project metadata and dependencies
- `docker-compose.yml` - Local development orchestration (build from source)
- `docker-compose.prod.yml` - Production orchestration (pre-built images, memory-constrained: 64MB Redis, 128MB Postgres effective cache)

## Platform Requirements

**Development:**
- Python 3.12+
- Docker & Docker Compose
- ~500MB RAM minimum (single container)
- Internet connectivity for Coinbase INTX API

**Production:**
- Linux `x86_64` (amd64) — images pre-compiled for this architecture
- Docker runtime
- Redis 7-alpine (in-memory, 64MB max with LRU eviction)
- PostgreSQL 16 with TimescaleDB extension (in-container, 128MB effective cache)
- ~4GB total swap space (for 8 agents + 2 services on Oracle Always Free instance)
- 24/7 uptime requirement (no market hours)

**Deployment Target:**
- Oracle Cloud Always Free AMD instance (140.238.222.244)
- Images cross-compiled on Apple Silicon for Linux target via Docker buildx
- Transfer via SCP to production host at `opc@140.238.222.244`

---

*Stack analysis: 2025-03-21*
