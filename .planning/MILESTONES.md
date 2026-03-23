# Milestones

## v1.1 Multi-Instrument Ingestion (Shipped: 2026-03-23)

**Phases completed:** 5 phases, 10 plans, 25 tasks

**Key accomplishments:**

- InstrumentConfig registry loading 5 perp contracts from YAML with per-instrument tick_size/min_order_size, wired into startup, hardcoded defaults removed
- Migrated all ~30 caller files from hardcoded instrument constants to config-driven InstrumentConfig registry with per-instrument tick_size, min_order_size, and ws_product_id lookups
- Multi-instrument WS dispatch routing all 5 products to per-instrument states with readiness gating, 100ms throttle, and reconnect staleness detection
- Per-instrument candle and funding rate REST pollers for all 5 instruments with staggered starts, error isolation, consecutive failure tracking, and REST staleness detection
- Runtime instrument ID assertions in ingestion pipeline with 5 E2E integration tests proving all instruments produce correct MarketSnapshots
- 4 integration tests verify per-instrument FeatureStore routing for all 5 contracts, plus dashboard per-instrument snapshot table and FeatureStore status sections
- ES256 JWT auth replacing HMAC-SHA256, COINBASE_ADV_ env prefix, api.coinbase.com base URL, PyJWT + cryptography deps
- All 10 REST endpoints rewritten to Advanced Trade API paths with Amount-based response models and portfolio UUID injection
- Complete test coverage for Coinbase Advanced Trade client (auth, REST, models) plus InstrumentConfig dynamic product ID resolution for Plan 04
- Dynamic product ID resolution at startup via Advanced Trade API, with passphrase cleanup across all agents

---

## v1.0 Strategy Enhancement (Shipped: 2026-03-22)

**Phases completed:** 5 phases, 14 plans, 25 tasks

**Key accomplishments:**

- scipy/bottleneck deps, VWAP/VOLUME_PROFILE enum entries, FeatureStore timestamps/bar_volumes properties, and per-instrument cooldown isolation verification
- YAML config schema validation halting on unknown base keys plus diff logging showing per-instrument parameter overrides at startup
- Strategy matrix with per-instrument parameter overrides for 4 active strategies across 5 instruments, lowering thresholds for increased signal frequency
- Momentum strategy enhanced with volume confirmation, 3-component adaptive conviction (ADX+RSI+volatility), swing-point stop-loss placement, and Portfolio A routing at conviction >= 0.75
- Multi-factor trend rejection, adaptive BB width via ATR percentile, extended TP with partial targets, volume conviction boost, and Portfolio A routing at conviction >= 0.65
- Graduated 3-tier cascade response (T1/T2/T3) with tier-specific stop/TP widths and volume surge confirmation gate
- Multi-window basis analysis with 3-lookback consensus, funding rate confirmation for 2/3 agreement, and Portfolio A routing at 0.70 conviction
- Volatility-adaptive ADX/ATR thresholds via percentileofscore with trailing stop metadata and tighter initial stops
- Shared funding rate z-score confirmation utility with settlement decay, integrated into correlation (refactored), momentum, and mean reversion strategies
- OBI strategy with time-weighted bid/ask imbalance, spread-based depth gate, and 3-component conviction model for short-horizon directional signals
- Session-aware VWAP deviation mean reversion strategy with feasibility-validated clamped volume weighting, configurable session resets (crypto 00:00 UTC, equity 14:00 UTC), and time-of-session conviction scaling
- Four shared utility modules (adaptive conviction, swing points, session classifier, conviction normalizer) with frozen dataclass results and 32 tests
- Session config (7 strategies x 2 sessions), conviction normalization with unified Portfolio A routing, and shared utility integration replacing inline scipy/swing implementations
- Per-instrument YAML overrides for momentum, mean reversion, and correlation strategies covering all Phase 2-4 params with asset-characteristic-derived values

---
