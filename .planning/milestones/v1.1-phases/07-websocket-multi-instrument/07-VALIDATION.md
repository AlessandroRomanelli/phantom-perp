---
phase: 7
slug: websocket-multi-instrument
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-22
---

# Phase 7 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x with pytest-asyncio |
| **Config file** | `pyproject.toml` |
| **Quick run command** | `python -m pytest agents/ingestion/tests/ -x -q` |
| **Full suite command** | `python -m pytest agents/ingestion/tests/ -v --tb=short` |
| **Estimated runtime** | ~5 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest agents/ingestion/tests/ -x -q`
- **After every plan wave:** Run `python -m pytest agents/ingestion/tests/ -v --tb=short`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 5 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 07-01-01 | 01 | 1 | MWS-01 | unit | `python -m pytest agents/ingestion/tests/test_ws_market_data.py -x -q` | ✅ | ⬜ pending |
| 07-01-02 | 01 | 1 | MWS-02 | unit | `python -m pytest agents/ingestion/tests/test_ws_market_data.py -x -q` | ✅ | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

Existing infrastructure covers all phase requirements. Test file `agents/ingestion/tests/test_ws_market_data.py` already exists with WS parsing tests.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| WS reconnect re-subscribes all products | MWS-01 | Requires real WS disconnect | Verify `_subscriptions` list contains all 5 product IDs after simulated reconnect |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 5s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
