---
phase: 06
slug: config-state-foundation
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-22
---

# Phase 06 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x with pytest-asyncio |
| **Config file** | pyproject.toml |
| **Quick run command** | `python -m pytest agents/ingestion/tests/ -x -q` |
| **Full suite command** | `python -m pytest -x -q` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest agents/ingestion/tests/ -x -q`
- **After every plan wave:** Run `python -m pytest -x -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 06-01-01 | 01 | 1 | MCFG-01 | unit | `python -m pytest agents/ingestion/tests/test_instruments_config.py -x -q` | ❌ W0 | ⬜ pending |
| 06-01-02 | 01 | 1 | MCFG-02 | unit | `python -m pytest agents/ingestion/tests/ libs/common/tests/ -x -q` | ✅ | ⬜ pending |
| 06-02-01 | 02 | 2 | MSTA-01 | unit | `python -m pytest agents/ingestion/tests/test_main.py -x -q` | ❌ W0 | ⬜ pending |
| 06-02-02 | 02 | 2 | MSTA-02 | unit | `python -m pytest agents/ingestion/tests/test_normalizer.py -x -q` | ✅ | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `agents/ingestion/tests/test_instruments_config.py` — stubs for MCFG-01 (YAML parsing, 5 instruments loaded)
- [ ] `agents/ingestion/tests/test_main.py` — extend for MSTA-01 (Dict[str, IngestionState] creation)

*Existing test infrastructure covers MCFG-02 (constants removal verified by import errors) and MSTA-02 (normalizer tests exist).*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Coinbase product IDs valid | MCFG-01 | Requires live API | Verify `{id}-INTX` convention against Coinbase docs for BTC, SOL, QQQ, SPY |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
