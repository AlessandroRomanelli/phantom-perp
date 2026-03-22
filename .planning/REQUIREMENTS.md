# Requirements: Phantom Perp Multi-Instrument Ingestion

**Defined:** 2026-03-22
**Core Value:** Better signal quality and broader market coverage across all instruments and conditions

## v1.1 Requirements

### Multi-Instrument Config

- [ ] **MCFG-01**: default.yaml supports a list of active instruments with per-instrument metadata (tick_size, min_order_size, base_currency)
- [ ] **MCFG-02**: Remove hardcoded single-instrument constants (INSTRUMENT_ID, BASE_CURRENCY, QUOTE_CURRENCY, TICK_SIZE, MIN_ORDER_SIZE) from constants.py — use config-driven values

### WebSocket Ingestion

- [ ] **MWS-01**: WebSocket client subscribes to all active instruments via single connection with multi-product subscription
- [ ] **MWS-02**: Incoming WS messages are routed to the correct per-instrument IngestionState by product ID

### REST Polling

- [ ] **MPOL-01**: Candle poller fetches candles for each active instrument independently (5 instruments x N timeframes concurrent)
- [ ] **MPOL-02**: Funding rate poller fetches funding for each active instrument independently (5 concurrent pollers)

### State Management

- [ ] **MSTA-01**: IngestionState is managed per-instrument via Dict[str, IngestionState] in main.py
- [ ] **MSTA-02**: Normalizer builds MarketSnapshot with correct instrument ID from parameter (not hardcoded INSTRUMENT_ID constant)

### End-to-End Verification

- [ ] **ME2E-01**: All active instruments produce MarketSnapshots published to stream:market_snapshots with correct instrument field
- [ ] **ME2E-02**: Signals agent FeatureStores receive samples for all active instruments (store_samples shows non-zero for all 5)

## v2 Requirements

### Volume Profile

- **VPRO-01**: Per-bar volume ingestion in FeatureStore (requires data pipeline changes)
- **VPRO-02**: Price-volume histogram with HVN/LVN detection
- **VPRO-03**: Support/resistance signals from volume nodes

### Advanced Features

- **ADV-01**: Multi-timeframe FeatureStore — 60s + 5m + 15m buffers
- **ADV-02**: Trailing stop state management in execution layer
- **ADV-03**: Fill quality tracking for Portfolio A adverse selection monitoring

## Out of Scope

| Feature | Reason |
|---------|--------|
| New instrument onboarding beyond 5 | Current 5 instruments sufficient for this milestone |
| Execution layer changes | Ingestion layer only — execution, risk, alpha combiner untouched |
| Strategy logic changes | v1.0 strategies are complete — this milestone enables their data |
| Per-instrument rate limiting | Use existing rate limiter — monitor for issues before adding complexity |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| MCFG-01 | TBD | Pending |
| MCFG-02 | TBD | Pending |
| MWS-01 | TBD | Pending |
| MWS-02 | TBD | Pending |
| MPOL-01 | TBD | Pending |
| MPOL-02 | TBD | Pending |
| MSTA-01 | TBD | Pending |
| MSTA-02 | TBD | Pending |
| ME2E-01 | TBD | Pending |
| ME2E-02 | TBD | Pending |

**Coverage:**
- v1.1 requirements: 10 total
- Mapped to phases: 0
- Unmapped: 10

---
*Requirements defined: 2026-03-22*
*Last updated: 2026-03-22 after initial definition*
