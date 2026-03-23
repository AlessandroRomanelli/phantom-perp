---
gsd_state_version: 1.0
milestone: v1.1
milestone_name: Multi-Instrument Ingestion
status: unknown
stopped_at: Completed 09.1-04-PLAN.md (Phase 09.1 complete)
last_updated: "2026-03-23T14:40:32.068Z"
progress:
  total_phases: 5
  completed_phases: 5
  total_plans: 10
  completed_plans: 10
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-03-22)

**Core value:** Better signal quality and broader market coverage across all instruments and conditions
**Current focus:** Phase 09.1 — coinbase-advanced-api-migration

## Current Position

Phase: 09.1
Plan: Not started

## Performance Metrics

**Velocity:**

- Total plans completed: 14 (from v1.0)
- Average duration: 4 min
- Total execution time: ~1 hour

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| Phase 01 | 3 | 7min | 2.3min |
| Phase 02 | 2 | 9min | 4.5min |
| Phase 03 | 3 | 12min | 4min |
| Phase 04 | 3 | 16min | 5.3min |
| Phase 05 | 3 | 12min | 4min |
| Phase 06 | 1 | 4min | 4min |
| Phase 07 | 1 | 3min | 3min |

**Recent Trend:**

- Last 5 plans: 8min, 4min, 3min, 6min, 3min
- Trend: Stable

*Updated after each plan completion*
| Phase 06 P02 | 22min | 3 tasks | 44 files |
| Phase 07 P01 | 3min | 2 tasks | 6 files |
| Phase 08 P01 | 4min | 3 tasks | 8 files |
| Phase 09 P01 | 3min | 2 tasks | 4 files |
| Phase 09 P02 | 3min | 2 tasks | 3 files |
| Phase 09.1 P01 | 2min | 2 tasks | 6 files |
| Phase 09.1 P02 | 4min | 2 tasks | 3 files |
| Phase 09.1 P03 | 6min | 3 tasks | 12 files |
| Phase 09.1 P04 | 3min | 3 tasks | 5 files |

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- [Roadmap v1.1]: Phases 7 and 8 are independent (WS and REST polling can execute in either order after Phase 6)
- [Roadmap v1.1]: 4 phases for 10 requirements — config/state foundation, then WS and REST as parallel tracks, then E2E verification
- [06-01]: Load instruments from default.yaml directly since env-specific configs don't duplicate instruments list
- [06-01]: Convert YAML floats to Decimal via Decimal(str(value)) to avoid precision loss
- [Phase 06]: Strategy tick_size lookup via get_instrument(snapshot.instrument).tick_size at evaluate() entry point
- [07-01]: Periodic staleness check every 30s in WS listen loop rather than event-driven after reconnect
- [07-01]: Readiness flags set directly in candles.py/funding_rate.py source files rather than wrapper tasks in main.py
- [Phase 08]: Per-instrument REST clients with shared RateLimiter rather than one shared client
- [Phase 08]: Error isolation via _run_rest_poller_isolated wrapper prevents one instrument crash from tearing down TaskGroup
- [Phase 09]: Optional instrument_id param with default None preserves backward compatibility in build_snapshot()
- [Phase 09]: _snap helper gets optional instrument param with backward-compatible default
- [Phase 09.1]: PEM newline normalization via replace handles env var escaping
- [Phase 09.1]: Kept get_instruments() as alias to get_products() for backward compat
- [Phase 09.1]: Generate fresh EC keys in conftest fixtures rather than hardcoded test keys
- [Phase 09.1 P04]: Discovery client reuses same auth and rate_limiter as per-instrument REST clients
- [Phase 09.1 P04]: Added portfolio_uuid_a/b to CoinbaseClientPool in risk agent for portfolio-scoped endpoints

### Pending Todos

None yet.

### Roadmap Evolution

- Phase 09.1 inserted after Phase 09: Coinbase Advanced API Migration (URGENT)

### Blockers/Concerns

- Coinbase Advanced API product IDs for perp contracts need verification (likely BTC-PERP-INTX, SOL-PERP-INTX, etc. but could differ for Advanced vs INTX)
- Rate limiting across 5 instruments — candle/funding pollers will make 5x more API calls

## Session Continuity

Last session: 2026-03-23T14:30:55Z
Stopped at: Completed 09.1-04-PLAN.md (Phase 09.1 complete)
Resume file: None
