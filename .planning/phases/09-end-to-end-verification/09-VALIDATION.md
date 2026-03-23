---
phase: 9
slug: end-to-end-verification
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-23
---

# Phase 9 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x |
| **Config file** | `pyproject.toml` |
| **Quick run command** | `python -m pytest agents/ingestion/tests/ agents/signals/tests/ -x -q` |
| **Full suite command** | `python -m pytest agents/ingestion/tests/ agents/signals/tests/ -v` |
| **Estimated runtime** | ~10 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest agents/ingestion/tests/ agents/signals/tests/ -x -q`
- **After every plan wave:** Run `python -m pytest agents/ingestion/tests/ agents/signals/tests/ -v`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 10 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| TBD | 01 | 1 | ME2E-01 | integration | `python -m pytest agents/ingestion/tests/test_main_wiring.py -v` | ✅ | ⬜ pending |
| TBD | 01 | 1 | ME2E-02 | integration | `python -m pytest agents/signals/tests/test_main.py -v` | ✅ | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

*Existing infrastructure covers all phase requirements.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Dashboard per-instrument display | ME2E-01 | Visual layout verification | Run `python scripts/dashboard.py`, verify all 5 instruments shown |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 10s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
