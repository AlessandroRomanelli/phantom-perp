---
phase: 5
slug: cross-cutting-quality
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-22
---

# Phase 5 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x with pytest-asyncio |
| **Config file** | `pyproject.toml` (pytest config section) |
| **Quick run command** | `python -m pytest agents/signals/tests/ -x -q` |
| **Full suite command** | `python -m pytest agents/ libs/ -x -q` |
| **Estimated runtime** | ~30 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest agents/signals/tests/ -x -q`
- **After every plan wave:** Run `python -m pytest agents/ libs/ -x -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 05-01-01 | 01 | 1 | XQ-01, XQ-05 | unit | `python -m pytest agents/signals/tests/test_shared_utilities.py -x -q` | ❌ W0 | ⬜ pending |
| 05-02-01 | 02 | 1 | XQ-02, XQ-03 | unit | `python -m pytest agents/signals/tests/test_session_classifier.py -x -q` | ❌ W0 | ⬜ pending |
| 05-02-02 | 02 | 2 | XQ-04 | unit | `python -m pytest agents/signals/tests/test_conviction_normalizer.py -x -q` | ❌ W0 | ⬜ pending |
| 05-03-01 | 03 | 2 | - | integration | `python -m pytest agents/signals/tests/ -x -q` | ✅ | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `agents/signals/tests/test_shared_utilities.py` — stubs for XQ-01, XQ-05
- [ ] `agents/signals/tests/test_session_classifier.py` — stubs for XQ-02, XQ-03
- [ ] `agents/signals/tests/test_conviction_normalizer.py` — stubs for XQ-04

*Existing test infrastructure covers framework needs.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Session classification at market boundaries | XQ-02 | Time-dependent boundary testing requires specific timestamps | Verify session transitions at 09:30 ET and 16:00 ET for equity, midnight UTC for crypto |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
