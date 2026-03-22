---
phase: 2
slug: momentum-mean-reversion
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-22
---

# Phase 2 — Validation Strategy

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
| 02-01-01 | 01 | 1 | MOM-01 | unit | `python -m pytest agents/signals/tests/test_momentum.py -x -q` | ❌ W0 | ⬜ pending |
| 02-01-02 | 01 | 1 | MOM-02 | unit | `python -m pytest agents/signals/tests/test_momentum.py -x -q` | ❌ W0 | ⬜ pending |
| 02-01-03 | 01 | 1 | MOM-03 | unit | `python -m pytest agents/signals/tests/test_momentum.py -x -q` | ❌ W0 | ⬜ pending |
| 02-01-04 | 01 | 1 | MOM-04 | unit | `python -m pytest agents/signals/tests/test_momentum.py -x -q` | ❌ W0 | ⬜ pending |
| 02-02-01 | 02 | 1 | MR-01 | unit | `python -m pytest agents/signals/tests/test_mean_reversion.py -x -q` | ❌ W0 | ⬜ pending |
| 02-02-02 | 02 | 1 | MR-02 | unit | `python -m pytest agents/signals/tests/test_mean_reversion.py -x -q` | ❌ W0 | ⬜ pending |
| 02-02-03 | 02 | 1 | MR-03, MR-04 | unit | `python -m pytest agents/signals/tests/test_mean_reversion.py -x -q` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `agents/signals/tests/test_momentum.py` — stubs for MOM-01, MOM-02, MOM-03, MOM-04
- [ ] `agents/signals/tests/test_mean_reversion.py` — stubs for MR-01, MR-02, MR-03, MR-04

*Existing test infrastructure (pytest, conftest) covers framework needs.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Portfolio A routing in alpha combiner | MOM-04, MR-04 | Requires full pipeline with alpha combiner | Verify `suggested_target=PortfolioTarget.A` in signal metadata when conviction exceeds threshold |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 20s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
