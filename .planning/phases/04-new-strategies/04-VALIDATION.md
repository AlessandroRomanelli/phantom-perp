---
phase: 4
slug: new-strategies
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-22
---

# Phase 4 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x with pytest-asyncio |
| **Config file** | `pyproject.toml` (pytest config section) |
| **Quick run command** | `python -m pytest agents/signals/tests/ -x -q` |
| **Full suite command** | `python -m pytest agents/ libs/ -x -q` |
| **Estimated runtime** | ~25 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest agents/signals/tests/ -x -q`
- **After every plan wave:** Run `python -m pytest agents/ libs/ -x -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 25 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 04-01-01 | 01 | 1 | FUND-01..03 | unit | `python -m pytest agents/signals/tests/test_funding_utility.py -x -q` | ❌ W0 | ⬜ pending |
| 04-02-01 | 02 | 1 | OBI-01..04 | unit | `python -m pytest agents/signals/tests/test_orderbook_imbalance.py -x -q` | ❌ W0 | ⬜ pending |
| 04-03-01 | 03 | 2 | VWAP-01 | unit | `python -m pytest agents/signals/tests/test_vwap.py -x -q` | ❌ W0 | ⬜ pending |
| 04-03-02 | 03 | 2 | VWAP-02..04 | unit | `python -m pytest agents/signals/tests/test_vwap.py -x -q` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `agents/signals/tests/test_funding_utility.py` — stubs for FUND-01..03
- [ ] `agents/signals/tests/test_orderbook_imbalance.py` — stubs for OBI-01..04
- [ ] `agents/signals/tests/test_vwap.py` — stubs for VWAP-01..04

*Existing test infrastructure covers framework needs.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| VWAP feasibility with live data | VWAP-01 | Approximation quality depends on real market data | Run against live FeatureStore data and inspect VWAP values vs known price levels |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 25s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
