---
phase: 1
slug: foundation-tuning
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-21
---

# Phase 1 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x with pytest-asyncio |
| **Config file** | `pyproject.toml` (pytest config section) |
| **Quick run command** | `python -m pytest agents/signals/tests/ agents/risk/tests/ -x -q` |
| **Full suite command** | `python -m pytest agents/ libs/ -x -q` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest agents/signals/tests/ agents/risk/tests/ -x -q`
- **After every plan wave:** Run `python -m pytest agents/ libs/ -x -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 01-01-01 | 01 | 1 | INFRA-05 | unit | `grep VWAP libs/common/models/enums.py` | ✅ | ⬜ pending |
| 01-01-02 | 01 | 1 | INFRA-04 | unit | `grep scipy pyproject.toml` | ✅ | ⬜ pending |
| 01-01-03 | 01 | 1 | INFRA-06 | unit | `python -m pytest agents/signals/tests/ -k timestamps -x -q` | ❌ W0 | ⬜ pending |
| 01-01-04 | 01 | 1 | INFRA-07 | unit | `python -m pytest agents/signals/tests/ -k bar_volume -x -q` | ❌ W0 | ⬜ pending |
| 01-01-05 | 01 | 1 | INFRA-01 | unit | `python -m pytest agents/signals/tests/ -k cooldown -x -q` | ❌ W0 | ⬜ pending |
| 01-02-01 | 02 | 1 | INFRA-02 | unit | `python -m pytest libs/ -k config_validation -x -q` | ❌ W0 | ⬜ pending |
| 01-02-02 | 02 | 1 | INFRA-03 | unit | `python -m pytest libs/ -k config_diff -x -q` | ❌ W0 | ⬜ pending |
| 01-02-03 | 02 | 2 | TUNE-01..05 | integration | `python -m pytest agents/signals/tests/ -k instrument_config -x -q` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `agents/signals/tests/test_cooldown_per_instrument.py` — stubs for INFRA-01
- [ ] `agents/signals/tests/test_feature_store_extensions.py` — stubs for INFRA-06, INFRA-07
- [ ] `libs/common/tests/test_config_validation.py` — stubs for INFRA-02, INFRA-03
- [ ] `agents/signals/tests/test_instrument_configs.py` — stubs for TUNE-01..05

*Existing test infrastructure (pytest, conftest) covers framework needs.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Config diff logging at startup | INFRA-03 | Log output requires running agent | Start signal agent, check structlog output for parameter diff lines |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
