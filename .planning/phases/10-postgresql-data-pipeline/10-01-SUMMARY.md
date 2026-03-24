---
phase: 10-postgresql-data-pipeline
plan: "01"
subsystem: storage
tags: [orm, postgresql, repository, data-pipeline, tuner]
dependency_graph:
  requires: []
  provides: [libs.storage.models, libs.storage.repository, libs.storage.relational.init_db]
  affects: [agents/risk, agents/execution, agents/signals, agents/tuner]
tech_stack:
  added: []
  patterns: [SQLAlchemy 2.0 DeclarativeBase, asynccontextmanager session, INNER JOIN attribution]
key_files:
  created:
    - libs/storage/models.py
    - libs/storage/repository.py
    - tests/unit/test_storage_models.py
    - tests/unit/test_repository.py
  modified:
    - libs/storage/relational.py
    - libs/storage/__init__.py
decisions:
  - "Store enum .value strings in DB columns (PortfolioTarget.A.value='autonomous', SignalSource.MOMENTUM.value='momentum') to match existing serializer convention"
  - "Use INNER JOIN in get_fills_by_strategy() — only attributed fills are relevant for per-strategy performance; orphan fills excluded by design"
  - "Mock-based unit testing with sync SQLite + _AsyncSessionWrapper — no aiosqlite dependency needed; tests the query logic without a real DB"
  - "Keep get_session() for backward compatibility while adding session() as the preferred context manager"
  - "init_db() uses engine.begin() not engine.connect() to auto-commit DDL — required per PostgreSQL semantics"
metrics:
  duration_seconds: 425
  tasks_completed: 2
  files_created: 4
  files_modified: 2
  tests_added: 27
  completed_date: "2026-03-24"
---

# Phase 10 Plan 01: PostgreSQL Data Pipeline Foundation Summary

SQLAlchemy 2.0 ORM models (fills, order_signals, signals), RelationalStore session context manager, `init_db()` schema bootstrap, and TunerRepository with typed attribution JOIN query methods.

## What Was Built

### libs/storage/models.py (NEW)

Three ORM tables using SQLAlchemy 2.0 `DeclarativeBase` + `mapped_column`:

- **FillRecord** (`fills` table): mirrors the `Fill` dataclass with nullable `order_id` for signal-less fills. Composite indexes on `(portfolio_target, filled_at)` and `(instrument, filled_at)`.
- **OrderSignalRecord** (`order_signals` table): written at risk-approval time. Stores `primary_source` (highest-conviction `SignalSource.value`) and `all_sources` (comma-separated). Indexes on `(primary_source, proposed_at)` and `(portfolio_target, proposed_at)`.
- **SignalRecord** (`signals` table): written at signal emit time. Stores `time_horizon_seconds` (from `timedelta.total_seconds()`). Indexes on `(source, timestamp)` and `(instrument, timestamp)`.

All `Decimal` fields use `NUMERIC(20, 8)` to avoid float rounding errors.

### libs/storage/relational.py (UPDATED)

- Added `@asynccontextmanager session()` — preferred context manager for all new code; auto-closes session.
- Added `engine` property returning `AsyncEngine` (needed by `init_db`).
- Preserved `get_session()` for backward compatibility.
- Added module-level `init_db(engine: AsyncEngine)` — calls `Base.metadata.create_all` via `engine.begin()` for auto-committing DDL. Safe to call at every agent startup (idempotent).

### libs/storage/repository.py (NEW)

- **AttributedFill**: frozen dataclass carrying fill fields plus `primary_source` and `conviction` from the `order_signals` JOIN.
- **TunerRepository**: query class taking `RelationalStore` in constructor.
  - `get_fills_by_strategy()`: INNER JOIN fills → order_signals, filter by `portfolio_target` + rolling time window. Satisfies DATA-01, DATA-04.
  - `get_fills_by_instrument()`: same JOIN, ordered by `(instrument, filled_at)`. Satisfies DATA-03.
  - `get_order_signals()`: raw `OrderSignalRecord` rows for order lifecycle analysis.
  - `get_signals()`: raw `SignalRecord` rows with optional instrument filter.
  - `write_fill()`, `write_order_signal()`, `write_signal()`: persist ORM records using `session()` context manager.

### libs/storage/__init__.py (UPDATED)

Exports: `Base`, `FillRecord`, `OrderSignalRecord`, `SignalRecord`, `init_db`, `TunerRepository`, `AttributedFill`.

## Test Coverage

27 unit tests across two files:

- `tests/unit/test_storage_models.py` (12 tests): ORM instantiation, nullable fields, `Base.metadata.tables` contents, composite column checks, `RelationalStore.session()` context manager check, `init_db` callable check.
- `tests/unit/test_repository.py` (15 tests): Uses sync SQLite + `_AsyncSessionWrapper` mock (no aiosqlite required). Covers DATA-01 through DATA-04: portfolio filter, time window filter, multi-instrument grouping, attribution JOIN, INNER JOIN exclusion, conviction population, instrument ordering, write methods.

## Deviations from Plan

None — plan executed exactly as written.

The only minor deviation: Task 2 required an `_AsyncSessionWrapper` test helper class to bridge sync SQLite with the repository's `await session.execute()` calls. This is test infrastructure, not production code, and directly follows the plan's recommendation: "mock `RelationalStore.session()` to return a session backed by SQLite."

## Known Stubs

None. All methods are fully implemented and tested.

## Self-Check: PASSED
