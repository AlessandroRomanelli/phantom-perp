"""System-wide constants. Safety guardrails and instrument config."""

from decimal import Decimal

# ── Instrument ───────────────────────────────────────────────────────────

INSTRUMENT_ID = "ETH-PERP"
BASE_CURRENCY = "ETH"
QUOTE_CURRENCY = "USDC"
TICK_SIZE = Decimal("0.01")
MIN_ORDER_SIZE = Decimal("0.0001")

ACTIVE_INSTRUMENT_IDS: list[str] = [
    "ETH-PERP",
    "BTC-PERP",
    "SOL-PERP",
    "QQQ-PERP",
    "SPY-PERP",
]

# ── Fee Schedule (VIP 1) ────────────────────────────────────────────────

FEE_MAKER = Decimal("0.000125")  # 0.0125%
FEE_TAKER = Decimal("0.000250")  # 0.0250%

# ── Safety Guardrails (non-negotiable, code-level constants) ────────────

MAX_LEVERAGE_GLOBAL = Decimal("5.0")
MAX_LEVERAGE_PORTFOLIO_B = Decimal("3.0")

STALE_DATA_HALT_SECONDS = 30

FUNDING_RATE_CIRCUIT_BREAKER_PCT = Decimal("0.0005")  # 0.05% absolute hourly rate

# Portfolio A limits
PORTFOLIO_A_MAX_POSITION_PCT_EQUITY = Decimal("40.0")
PORTFOLIO_A_DAILY_LOSS_KILL_PCT = Decimal("10.0")
PORTFOLIO_A_MAX_DRAWDOWN_PCT = Decimal("25.0")
PORTFOLIO_A_MIN_LIQUIDATION_DISTANCE_PCT = Decimal("8.0")

# Portfolio B limits
PORTFOLIO_B_MAX_DAILY_LOSS_PCT = Decimal("5.0")
PORTFOLIO_B_MAX_DRAWDOWN_PCT = Decimal("15.0")
PORTFOLIO_B_MIN_LIQUIDATION_DISTANCE_PCT = Decimal("15.0")
PORTFOLIO_B_AUTO_APPROVE_MAX_NOTIONAL_USDC = Decimal("2000")

# ── Funding ──────────────────────────────────────────────────────────────

FUNDING_SETTLEMENTS_PER_DAY = 24
FUNDING_SETTLEMENT_INTERVAL_HOURS = 1

# ── Coinbase INTX URLs (defaults, overridable via env) ──────────────────

DEFAULT_REST_BASE_URL = "https://api.international.coinbase.com"
DEFAULT_WS_MARKET_URL = "wss://ws-md.international.coinbase.com"
DEFAULT_WS_USER_URL = "wss://ws.international.coinbase.com"
