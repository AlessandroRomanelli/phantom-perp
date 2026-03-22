# Roadmap: Phantom Perp Strategy Enhancement

## Milestones

- ✅ **v1.0 Strategy Enhancement** — Phases 1-5 (shipped 2026-03-22)
- 🚧 **v1.1 Multi-Instrument Ingestion** — Phases 6-9 (in progress)

## Phases

<details>
<summary>✅ v1.0 Strategy Enhancement (Phases 1-5) — SHIPPED 2026-03-22</summary>

- [x] Phase 1: Foundation and Per-Instrument Tuning (3/3 plans) — completed 2026-03-22
- [x] Phase 2: Momentum and Mean Reversion Improvements (2/2 plans) — completed 2026-03-22
- [x] Phase 3: Liquidation, Correlation, and Regime Improvements (3/3 plans) — completed 2026-03-22
- [x] Phase 4: New Strategies (3/3 plans) — completed 2026-03-22
- [x] Phase 5: Cross-Cutting Quality (3/3 plans) — completed 2026-03-22

Full details: `.planning/milestones/v1.0-ROADMAP.md`

</details>

### v1.1 Multi-Instrument Ingestion (In Progress)

**Milestone Goal:** Enable all 5 perpetual contracts (ETH, BTC, SOL, QQQ, SPY) to flow through the ingestion pipeline so v1.0 strategy improvements evaluate across all instruments.

- [ ] **Phase 6: Config and State Foundation** - Multi-instrument config structure and per-instrument state management
- [ ] **Phase 7: WebSocket Multi-Instrument** - Single WS connection subscribing to all products with per-instrument routing
- [ ] **Phase 8: REST Polling Multi-Instrument** - Candle and funding pollers loop over all instruments
- [ ] **Phase 9: End-to-End Verification** - All 5 instruments produce snapshots that reach the signals agent

## Phase Details

### Phase 6: Config and State Foundation
**Goal**: The ingestion layer reads instrument configuration from YAML and manages per-instrument state instead of relying on hardcoded single-instrument constants
**Depends on**: Phase 5 (v1.0 complete)
**Requirements**: MCFG-01, MCFG-02, MSTA-01, MSTA-02
**Success Criteria** (what must be TRUE):
  1. default.yaml contains an instruments list with metadata (tick_size, min_order_size, base_currency) for all 5 perp contracts
  2. No hardcoded INSTRUMENT_ID, BASE_CURRENCY, QUOTE_CURRENCY, TICK_SIZE, or MIN_ORDER_SIZE constants remain in constants.py — all values are config-driven
  3. Ingestion main.py creates a Dict[str, IngestionState] with one entry per active instrument from config
  4. Normalizer accepts an instrument parameter and builds MarketSnapshot with that instrument ID (not a hardcoded constant)
**Plans**: TBD

Plans:
- [ ] 06-01: TBD
- [ ] 06-02: TBD

### Phase 7: WebSocket Multi-Instrument
**Goal**: A single WebSocket connection receives real-time market data for all 5 instruments and routes messages to the correct per-instrument state
**Depends on**: Phase 6
**Requirements**: MWS-01, MWS-02
**Success Criteria** (what must be TRUE):
  1. WebSocket client sends a single subscription message covering all active instrument product IDs (not one connection per instrument)
  2. Incoming WS messages are parsed for product ID and dispatched to the correct per-instrument IngestionState
  3. WebSocket reconnection re-subscribes to all instruments (not just the first)
**Plans**: TBD

Plans:
- [ ] 07-01: TBD

### Phase 8: REST Polling Multi-Instrument
**Goal**: Candle and funding rate pollers fetch data for each active instrument independently, producing per-instrument data in the pipeline
**Depends on**: Phase 6
**Requirements**: MPOL-01, MPOL-02
**Success Criteria** (what must be TRUE):
  1. Candle poller fetches candles for each of the 5 active instruments (concurrent polling, not sequential)
  2. Funding rate poller fetches funding for each of the 5 active instruments (concurrent polling)
  3. Polled data updates the correct per-instrument IngestionState (no cross-contamination between instruments)
**Plans**: TBD

Plans:
- [ ] 08-01: TBD

### Phase 9: End-to-End Verification
**Goal**: All 5 instruments produce MarketSnapshots that flow through the full ingestion pipeline and are consumed by the signals agent
**Depends on**: Phase 7, Phase 8
**Requirements**: ME2E-01, ME2E-02
**Success Criteria** (what must be TRUE):
  1. All 5 active instruments produce MarketSnapshots published to stream:market_snapshots with correct instrument field values
  2. Signals agent FeatureStores show non-zero store_samples for all 5 instruments
  3. No snapshot contains a stale or wrong instrument ID (each snapshot's instrument matches its source data)
**Plans**: TBD

Plans:
- [ ] 09-01: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 6 → 7 → 8 → 9 (note: 7 and 8 are independent, both depend on 6)

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1. Foundation and Per-Instrument Tuning | v1.0 | 3/3 | Complete | 2026-03-22 |
| 2. Momentum and Mean Reversion Improvements | v1.0 | 2/2 | Complete | 2026-03-22 |
| 3. Liquidation, Correlation, and Regime Improvements | v1.0 | 3/3 | Complete | 2026-03-22 |
| 4. New Strategies | v1.0 | 3/3 | Complete | 2026-03-22 |
| 5. Cross-Cutting Quality | v1.0 | 3/3 | Complete | 2026-03-22 |
| 6. Config and State Foundation | v1.1 | 0/0 | Not started | - |
| 7. WebSocket Multi-Instrument | v1.1 | 0/0 | Not started | - |
| 8. REST Polling Multi-Instrument | v1.1 | 0/0 | Not started | - |
| 9. End-to-End Verification | v1.1 | 0/0 | Not started | - |
