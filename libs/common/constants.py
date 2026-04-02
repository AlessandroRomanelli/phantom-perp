"""System-wide constants. Safety guardrails and instrument config."""

from decimal import Decimal

# ── Fee Schedule (VIP 1) ────────────────────────────────────────────────

FEE_MAKER = Decimal("0.000125")  # 0.0125%
FEE_TAKER = Decimal("0.000250")  # 0.0250%

# ── Safety Guardrails (non-negotiable, code-level constants) ────────────

MAX_LEVERAGE_GLOBAL = Decimal("10.0")
MAX_LEVERAGE_ROUTE_B = Decimal("5.0")

STALE_DATA_HALT_SECONDS = 30
REST_CANDLE_STALE_SECONDS = 600    # 10 min -- candle pollers poll every 60-1800s
REST_FUNDING_STALE_SECONDS = 900   # 15 min -- funding poller polls every 300s
REST_POLLER_STAGGER_SECONDS = 2.0  # Delay between instrument starts to avoid burst

FUNDING_RATE_CIRCUIT_BREAKER_PCT = Decimal("0.0005")  # 0.05% absolute hourly rate

# Route A limits
ROUTE_A_MAX_POSITION_PCT_EQUITY = Decimal("40.0")
ROUTE_A_DAILY_LOSS_KILL_PCT = Decimal("10.0")
ROUTE_A_MAX_DRAWDOWN_PCT = Decimal("25.0")
ROUTE_A_MIN_LIQUIDATION_DISTANCE_PCT = Decimal("8.0")

# Route B limits
ROUTE_B_MAX_DAILY_LOSS_PCT = Decimal("5.0")
ROUTE_B_MAX_DRAWDOWN_PCT = Decimal("15.0")
ROUTE_B_MIN_LIQUIDATION_DISTANCE_PCT = Decimal("15.0")
ROUTE_B_AUTO_APPROVE_MAX_NOTIONAL_USDC = Decimal("2000")

# ── Funding ──────────────────────────────────────────────────────────────

FUNDING_SETTLEMENTS_PER_DAY = 24
FUNDING_SETTLEMENT_INTERVAL_HOURS = 1

# ── Coinbase Advanced Trade URLs (defaults, overridable via env) ─────────

DEFAULT_REST_BASE_URL = "https://api.coinbase.com"
