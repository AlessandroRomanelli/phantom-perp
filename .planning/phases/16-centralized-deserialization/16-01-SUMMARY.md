---
phase: 16-centralized-deserialization
plan: "01"
subsystem: libs/common
tags: [serialization, deserialization, refactor, tdd, bug-fix]
dependency_graph:
  requires: []
  provides: [libs.common.serialization]
  affects:
    - agents/execution/main.py
    - agents/confirmation/main.py
    - agents/reconciliation/main.py
    - agents/reconciliation/paper_simulator.py
    - agents/monitoring/main.py
    - agents/risk/main.py
    - agents/alpha/main.py
    - agents/signals/main.py
    - agents/ingestion/normalizer.py
tech_stack:
  added: []
  patterns:
    - "Centralized serialization module in libs/common with _parse_bool helper"
    - "TDD RED/GREEN/REFACTOR cycle for shared library creation"
key_files:
  created:
    - libs/common/serialization.py
    - libs/common/tests/test_serialization.py
  modified: []
decisions:
  - "_parse_bool raises ValueError on unrecognised input (not silent fallback) — explicit and catchable, aligns with CLAUDE.md error handling"
  - "signal_to_dict uses signal.metadata directly (no _json_safe) — numpy is not a libs dependency; signals agent applies _json_safe locally"
  - "alert_to_dict/deserialize_alert excluded — Alert model is agent-local in monitoring/alerting.py; moving serializers to libs would violate unidirectional dependency rule"
  - "deserialize_portfolio_snapshot always returns empty positions list — positions not serialized in the stream format"
metrics:
  duration_seconds: 174
  completed_date: "2026-04-08"
  tasks_completed: 1
  files_created: 2
  files_modified: 0
requirements:
  - BUG-04
---

# Phase 16 Plan 01: Centralized Serialization Module Summary

**One-liner:** Created `libs/common/serialization.py` with 16 Redis stream ser/deser functions and `_parse_bool` helper, replacing fragile inline boolean ternaries copied across 5 agents.

## What Was Built

`libs/common/serialization.py` is the single source of truth for all inter-agent Redis stream serialization. It provides 16 public functions (8 model pairs, each with `to_dict` and `deserialize_*`) plus a private `_parse_bool` helper that correctly handles all boolean string representations from Redis.

The module replaces the fragile inline boolean ternary `payload["reduce_only"] == "True" if isinstance(..., str) else bool(...)` with `_parse_bool()` calls that correctly handle `"True"`, `"False"`, `"true"`, `"false"`, `"1"`, `"0"`, Python `bool`, and `int` variants — raising `ValueError` on unrecognised input.

## Functions Implemented

| Group | to_dict | deserialize_* |
|-------|---------|---------------|
| MarketSnapshot | `snapshot_to_dict` | `deserialize_snapshot` |
| StandardSignal | `signal_to_dict` | `deserialize_signal` |
| RankedTradeIdea | `idea_to_dict` | `deserialize_idea` |
| ProposedOrder | `order_to_dict` | `deserialize_proposed_order` |
| ApprovedOrder | `approved_order_to_dict` | `deserialize_approved_order` |
| Fill | `fill_to_dict` | `deserialize_fill` |
| PortfolioSnapshot | `portfolio_snapshot_to_dict` | `deserialize_portfolio_snapshot` |
| FundingPayment | `funding_payment_to_dict` | `deserialize_funding_payment` |

## Test Coverage

`libs/common/tests/test_serialization.py` — 36 tests across 8 test classes:

- `TestParseBool`: 12 cases — True/False bool, "True"/"False" string, "true"/"false" lowercase, "1"/"0" string, 1/0 int, and ValueError for invalid/empty input
- `TestDeserializeProposedOrder`: 5 cases — round-trip, reduce_only="True", reduce_only="true", optional fields empty->None, optional fields with values
- `TestDeserializeApprovedOrder`: 3 cases — round-trip, reduce_only="True", reduce_only=False
- `TestDeserializeFill`: 4 cases — round-trip, is_maker="True", is_maker="False", is_maker="true"
- `TestDeserializeIdea`: 2 cases — round-trip, optional None fields
- `TestDeserializeSnapshot`: 3 cases — round-trip, volatility_1h=0.0, volatility_1h=""
- `TestDeserializeSignal`: 3 cases — round-trip, suggested_route=None, optional price fields None
- `TestDeserializePortfolioSnapshot`: 2 cases — round-trip, Route.B
- `TestDeserializeFundingPayment`: 2 cases — round-trip, SHORT position side

## Deviations from Plan

None — plan executed exactly as written. All functions sourced verbatim from agent files as instructed, with inline boolean ternaries replaced by `_parse_bool()` calls.

## Threat Flags

None. This is a pure code-consolidation refactor with no new trust boundaries, endpoints, or data format changes.

## Self-Check: PASSED

Files exist:
- `libs/common/serialization.py` — FOUND
- `libs/common/tests/test_serialization.py` — FOUND

Commits exist:
- `0f5d928` (test RED phase) — FOUND
- `849d342` (feat GREEN phase) — FOUND

Test results:
- `python3 -m pytest libs/common/tests/test_serialization.py -v` — 36 passed
- `python3 -m pytest agents/ libs/ -q --ignore=...` — 1240 passed, 5 skipped
- All exports verified: `from libs.common.serialization import <all 16>` — OK
