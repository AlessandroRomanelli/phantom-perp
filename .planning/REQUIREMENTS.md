# Requirements: Phantom Perp v1.2

**Defined:** 2026-03-24
**Core Value:** Better signal quality and broader market coverage — close Portfolio A's P&L gap through AI-powered parameter tuning

## v1.2 Requirements

Requirements for the AI-Powered Parameter Tuner milestone. Each maps to roadmap phases.

### Data Pipeline

- [x] **DATA-01**: Tuner can query Portfolio A fill records with strategy attribution from PostgreSQL
- [x] **DATA-02**: Tuner computes per-strategy performance metrics across all 5 instruments
- [x] **DATA-03**: Tuner computes per-instrument performance metrics across all strategies
- [x] **DATA-04**: Signal source attribution is preserved through the order-to-fill chain in PostgreSQL

### Metrics Engine

- [x] **METR-01**: Tuner computes expectancy (avg_win x win_rate - avg_loss x loss_rate) as primary metric per strategy
- [x] **METR-02**: Tuner computes profit factor per strategy per instrument
- [x] **METR-03**: Tuner computes max drawdown and drawdown duration per strategy
- [x] **METR-04**: Tuner computes fee-adjusted P&L (includes trading fees and funding costs)

### Safety & Bounds

- [x] **SAFE-01**: Every tunable parameter has hard min/max bounds defined in a bounds registry YAML
- [x] **SAFE-02**: YAML config writes use atomic write pattern (write-tmp + os.replace)
- [x] **SAFE-03**: Post-write validation reloads written YAML and compares against intended values
- [x] **SAFE-04**: Tuner logs all parameter changes with before/after values in structured log

### Claude Integration

- [x] **CLAI-01**: Tuner sends performance metrics to Claude via Anthropic SDK with structured output
- [x] **CLAI-02**: Claude returns parameter recommendations as typed JSON with per-parameter reasoning
- [x] **CLAI-03**: Claude prompt includes current parameter values, bounds, and recent performance context
- [x] **CLAI-04**: Code validates all Claude recommendations against bounds before applying

### Infrastructure

- [x] **INFR-01**: Tuner runs as a Docker container (run-to-completion, not daemon)
- [x] **INFR-02**: Shared Docker volume mounts config directory accessible to both tuner and signals containers
- [x] **INFR-03**: Daily cron trigger runs tuner container and restarts signals agent after completion
- [x] **INFR-04**: Tuner Dockerfile follows existing agent patterns (Python 3.13-slim, layer caching)

### Notifications

- [ ] **NOTF-01**: Tuner sends daily Telegram message with parameter changes and Claude's reasoning
- [ ] **NOTF-02**: Telegram message includes before/after values for each changed parameter
- [ ] **NOTF-03**: Tuner sends structured log entries for every tuning run (changes or no changes)

## Future Requirements

Deferred to future milestone. Tracked but not in current roadmap.

### Advanced Metrics

- **METR-05**: Session-aware performance breakdown (per crypto_weekday/weekend, equity_market/off_hours)
- **METR-06**: Conviction-outcome correlation analysis (signal conviction vs actual P&L)

### Advanced Safety

- **SAFE-05**: Cumulative drift caps from baseline anchor (prevent gradual parameter drift)
- **SAFE-06**: Minimum sample size gates before tuning (require N trades before adjusting)
- **SAFE-07**: Dry-run mode for safe testing (full loop without YAML writes)
- **SAFE-08**: Open-position deferred reload (don't change params while positions are open)

### Advanced AI

- **CLAI-05**: Dual-call Claude consistency check (verify recommendations are stable)
- **CLAI-06**: Magnitude gating on large parameter changes (flag outsized adjustments)

### Advanced Infrastructure

- **INFR-05**: Volume initialization procedure for first production deploy
- **INFR-06**: Health check integration with monitoring agent

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| Real-time parameter adjustment | Daily cadence prevents overfitting |
| Portfolio B tuning | Tuner targets Portfolio A only |
| A vs B comparison metrics | User chose A-only data source |
| Strategy enable/disable by AI | Too dangerous — AI adjusts params, not strategy activation |
| Backtesting of parameter changes | Separate project concern |
| Redis-based parameter overrides | YAML rewrite chosen for auditability |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| DATA-01 | Phase 10 | Complete |
| DATA-02 | Phase 10 | Complete |
| DATA-03 | Phase 10 | Complete |
| DATA-04 | Phase 10 | Complete |
| METR-01 | Phase 11 | Complete |
| METR-02 | Phase 11 | Complete |
| METR-03 | Phase 11 | Complete |
| METR-04 | Phase 11 | Complete |
| SAFE-01 | Phase 12 | Complete |
| SAFE-02 | Phase 12 | Complete |
| SAFE-03 | Phase 12 | Complete |
| SAFE-04 | Phase 12 | Complete |
| CLAI-01 | Phase 13 | Complete |
| CLAI-02 | Phase 13 | Complete |
| CLAI-03 | Phase 13 | Complete |
| CLAI-04 | Phase 13 | Complete |
| INFR-01 | Phase 14 | Complete |
| INFR-02 | Phase 14 | Complete |
| INFR-03 | Phase 14 | Complete |
| INFR-04 | Phase 14 | Complete |
| NOTF-01 | Phase 15 | Pending |
| NOTF-02 | Phase 15 | Pending |
| NOTF-03 | Phase 15 | Pending |

**Coverage:**
- v1.2 requirements: 23 total
- Mapped to phases: 23
- Unmapped: 0 ✓

---
*Requirements defined: 2026-03-24*
*Last updated: 2026-03-24 after roadmap creation*
