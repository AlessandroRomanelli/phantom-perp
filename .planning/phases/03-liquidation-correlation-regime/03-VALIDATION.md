---
phase: 3
slug: liquidation-correlation-regime
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-22
---

# Phase 3 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x with pytest-asyncio |
| **Config file** | `pyproject.toml` (pytest config section) |
| **Quick run command** | `python -m pytest agents/signals/tests/ -x -q` |
| **Full suite command** | `python -m pytest agents/ libs/ -x -q` |
| **Estimated runtime** | ~20 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest agents/signals/tests/ -x -q`
- **After every plan wave:** Run `python -m pytest agents/ libs/ -x -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 20 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 03-01-01 | 01 | 1 | LIQ-01, LIQ-02 | unit | `python -m pytest agents/signals/tests/test_liquidation_cascade.py -x -q` | ❌ W0 | ⬜ pending |
| 03-02-01 | 02 | 1 | CORR-01, CORR-02, CORR-03 | unit | `python -m pytest agents/signals/tests/test_correlation.py -x -q` | ❌ W0 | ⬜ pending |
| 03-03-01 | 03 | 1 | RT-01, RT-02 | unit | `python -m pytest agents/signals/tests/test_regime_trend.py -x -q` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `agents/signals/tests/test_liquidation_cascade.py` — stubs for LIQ-01, LIQ-02
- [ ] `agents/signals/tests/test_correlation.py` — stubs for CORR-01, CORR-02, CORR-03
- [ ] `agents/signals/tests/test_regime_trend.py` — stubs for RT-01, RT-02

*Existing test infrastructure (pytest, conftest) covers framework needs.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Trailing stop metadata in execution layer | RT-02 | No consumer exists yet (ADV-02 is v2) | Verify metadata dict contains `trail_atr_mult` and `trail_activation_pct` keys |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 20s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
