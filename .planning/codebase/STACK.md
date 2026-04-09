# Technology Stack

**Analysis Date:** 2026-04-08

## Languages

**Primary:**
- Python 3.12+ (3.13-slim in Docker) - Full application codebase (agents, libraries, scripts)

**Secondary:**
- HTML/JavaScript - Dashboard UI (`agents/dashboard/static/index.html`)
- YAML - Configuration files (`configs/`)

## Runtime

**Environment:**
- Python 3.13-slim (Docker base image)
- `asyncio` for concurrent event handling (24/7 operation)
- `uvloop` (0.19+) on non-Windows platforms for higher throughput event loop
- Hatchling build system for package management
- Lockfile: Docker image layer isolation; development via virtual environment

**Package Manager:**
- `pip` with `pyproject.toml` manifest (Hatchling backend)
- No separate lock file; version pinning via `pyproject.toml` ranges

## Frameworks

**Core Trading/Data:**
- `httpx` (0.27+) - Async REST client for Coinbase Advanced API and external data sources
- `websockets` (13+) - Async WebSocket client for Coinbase real-time market feeds
- `polars` (1.0+) - Primary dataframe library for speed-critical 24/7 data operations
- `pandas` - Compatibility layer where polars not used

**Configuration & Validation:**
- `pydantic` (2.6+) - Data validation for API models and configuration
- `pydantic-settings` (2.2+) - Environment variable and config file loading
- `PyYAML` (6+) - Strategy and instrument YAML config parsing

**Database & Storage:**
- `SQLAlchemy[asyncio]` (2.0+) - Async ORM for relational schema
- `asyncpg` (0.29+) - Native async PostgreSQL driver
- `redis` (5.0+) - Async client for Redis Streams (message broker) and caching

**Messaging & Serialization:**
- `orjson` (3.9+) - Fast JSON encoding/decoding for Redis Streams payloads and WebSocket frames
- `redis` (async client) - Redis Streams for agent-to-agent messaging (append-only event log)

**Machine Learning & Indicators:**
- `ta-lib` (0.4+) - TA-Lib C library compiled in Docker for technical indicators
- `numpy` (1.26+) - Numerical computation for indicator calculations
- `scikit-learn` (1.4+) - ML algorithms for strategy feature engineering
- `xgboost` (2+) - Gradient boosting for ensemble models and regime detection
- `scipy` (1.14+) - Scientific computing for statistical tests

**Integration & Communication:**
- `python-telegram-bot` (21+) - Telegram bot SDK for Route B order confirmations
- `claude` CLI - Local Claude instance for market analysis signals (via subprocess)

**Authentication & Security:**
- `cryptography` (42+) - Elliptic Curve (ES256) private key handling for JWT signing
- `PyJWT` (2.8+) - JWT token generation for Coinbase Advanced API authentication

**Logging & Monitoring:**
- `structlog` (24.1+) - Structured JSON logging across all agents

**Development & Testing:**
- `pytest` (8+) - Test runner with async support
- `pytest-asyncio` (0.23+) - Async test fixture support
- `pytest-cov` (5+) - Code coverage reporting
- `ruff` (0.3+) - Fast linter/formatter (configured in `pyproject.toml`)
- `mypy` (1.9+) - Static type checker (strict mode, Python 3.12)
- `pre-commit` (3+) - Git pre-commit hooks
- `respx` (0.21+) - Mock `httpx` requests for unit tests
- `fakeredis` (2.21+) - In-memory Redis mock for integration tests
- `freezegun` (1.4+) - Time freezing for deterministic unit tests
- `bottleneck` (1.4+) - Fast NumPy array operations for rolling window calculations

## Key Dependencies

**Exchange Connectivity:**
- `httpx` + `websockets` - Coinbase Advanced REST API and WebSocket feeds
- `cryptography` + `PyJWT` - ES256 JWT auth for Coinbase Cloud API

**Message Broker:**
- `redis` (async client) - Redis Streams for agent coordination and message queuing

**Persistent Storage:**
- `SQLAlchemy[asyncio]` + `asyncpg` - PostgreSQL/TimescaleDB for orders, fills, state
- `redis` - Caching and peak equity state persistence

**AI/Insights:**
- `claude` CLI (subprocess) - Local Claude instance for market analysis and strategy orchestration
- `ta-lib`, `numpy`, `scikit-learn`, `xgboost` - Technical indicators and ML models

**User Interaction:**
- `python-telegram-bot` - Telegram bot for Route B (manual) trade confirmations

**Performance:**
- `uvloop` - Higher-throughput async event loop (non-Windows)
- `orjson` - Fast JSON serialization for Redis Streams payloads
- `polars` - Vectorized dataframe operations for rolling indicators
- `bottleneck` - Fast rolling window functions (e.g., moving averages)

## Configuration

**Environment Variables:**
- Loaded from `.env` file at startup (via `pydantic-settings`)
- Required: `COINBASE_ADV_API_KEY_A`, `COINBASE_ADV_API_SECRET_A` (Route A credentials)
- Optional: `COINBASE_ADV_API_KEY_B`, `COINBASE_ADV_API_SECRET_B` (Route B dual-portfolio)
- Optional: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (Route B confirmations)
- Optional: `COINGLASS_API_KEY`, `GLASSNODE_API_KEY`, `CRYPTOQUANT_API_KEY`, `FINNHUB_API_KEY`, `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET` (data providers)
- Required: `REDIS_URL` (default: `redis://localhost:6379`)
- Required: `DATABASE_URL` (default: `postgresql://phantom:phantom_dev@localhost:5432/phantom_perp`)
- Optional: `ENVIRONMENT` (paper or live, default: paper)
- Optional: `LOG_LEVEL` (INFO, DEBUG, etc.)

**Runtime Config Files:**
- `configs/default.yaml` - Base configuration (instruments, fees, risk limits, routing rules)
- `configs/paper.yaml` - Paper trading overrides
- `configs/live.yaml` - Production overrides
- `configs/strategies/*.yaml` - Per-strategy parameters (e.g., `momentum.yaml`, `vwap.yaml`)
- `configs/strategy_matrix.yaml` - Strategy-to-instrument activation matrix

**Project Metadata:**
- `pyproject.toml` - Python project definition, dependencies, tool configuration (ruff, mypy, pytest)
- `.env.example` - Template environment variables (secrets redacted)

## Platform Requirements

**Development:**
- Python 3.12+ (local venv)
- Docker & Docker Compose (local orchestration)
- ~500MB RAM minimum (single container)
- Internet connectivity for Coinbase Advanced API and external data providers

**Production:**
- Docker runtime
- Linux `x86_64` (amd64) architecture (images cross-compiled on Apple Silicon)
- ~4GB total swap space (for 8 agents + 2 services)
- 24/7 uptime requirement (no market hours)

**Service Dependencies (Docker Compose):**
- Redis 7-alpine (in-memory, 64MB max with LRU eviction)
- PostgreSQL 16 with TimescaleDB extension (128MB effective cache)
- All agents run as isolated Docker containers with health checks

---

*Stack analysis: 2026-04-08*
