---
phase: 14-docker-infrastructure
plan: 02
subsystem: docker-compose-integration
tags: [docker, compose, scheduler, cron, volumes, tuner]
dependency_graph:
  requires: [agents/tuner/Dockerfile, agents/tuner/entrypoint.py]
  provides: [agents/scheduler/Dockerfile, agents/scheduler/entrypoint.sh, agents/scheduler/crontab, docker-compose.yml (tuner service), docker-compose.prod.yml (tuner + scheduler + strategy_configs)]
  affects: [docker-compose.yml, docker-compose.prod.yml]
tech_stack:
  added: [alpine:3.19, docker-cli, docker-cli-compose]
  patterns: [named Docker volume for config sharing, scheduler container with Docker socket, crond with TUNER_CRON env override]
key_files:
  created:
    - agents/scheduler/Dockerfile
    - agents/scheduler/entrypoint.sh
    - agents/scheduler/crontab
  modified:
    - docker-compose.yml
    - docker-compose.prod.yml
decisions:
  - alpine:3.19 with docker-cli-compose for scheduler -- minimal ~10MB image, compose v2 plugin for docker compose command
  - strategy_configs named volume scope is configs/strategies/ only -- minimal surface area per D-02
  - scheduler in prod compose only, not dev -- per D-18, dev users run tuner manually
  - TUNER_CRON env var with sed substitution -- configurable daily default 00:00 UTC per D-05
metrics:
  duration_seconds: 240
  completed_at: "2026-03-25T18:20:08Z"
  tasks_completed: 2
  files_changed: 5
---

# Phase 14 Plan 02: Docker Compose Integration for Tuner and Scheduler Summary

**One-liner:** Alpine scheduler container with crond + Docker socket, strategy_configs named volume wiring tuner (rw) to signals (ro), tuner in both compose files, scheduler prod-only.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Scheduler container Dockerfile, entrypoint, and crontab | 575a331 | agents/scheduler/Dockerfile, agents/scheduler/entrypoint.sh, agents/scheduler/crontab |
| 2 | Docker Compose integration for tuner, scheduler, and shared volume | 9fb5566 | docker-compose.yml, docker-compose.prod.yml |

## What Was Built

**agents/scheduler/Dockerfile** — Minimal `FROM alpine:3.19` with `apk add --no-cache docker-cli docker-cli-compose`. Copies crontab to `/etc/crontabs/root` and `entrypoint.sh` to `/entrypoint.sh`. `ENTRYPOINT ["/entrypoint.sh"]` replaces the shell process with crond.

**agents/scheduler/entrypoint.sh** — Applies `TUNER_CRON` env var override via `sed -i` substitution on the installed crontab, then `exec crond -f -l 2`. Default schedule is `0 0 * * *` (daily 00:00 UTC) per D-05.

**agents/scheduler/crontab** — Daily cron job: `cd /app && docker compose -f docker-compose.prod.yml --project-name phantom-perp run --rm tuner && docker compose -f docker-compose.prod.yml --project-name phantom-perp restart signals >> /proc/1/fd/1 2>&1`. The `&&` ensures signals only restarts if tuner exits 0. `/proc/1/fd/1` routes cron output to container stdout.

**docker-compose.yml (dev)** — Added `tuner:` service with `build:` from source, `strategy_configs:/app/configs/strategies` rw mount, `depends_on postgres`, `restart: "no"`. Added `strategy_configs:/app/configs/strategies:ro` volume to `signals:` service. Added `strategy_configs:` to `volumes:` section. No scheduler in dev per D-18.

**docker-compose.prod.yml (prod)** — Added `tuner:` service with `image: phantom-perp-tuner:amd64`, rw strategy_configs mount, `restart: "no"`. Added `scheduler:` service with `/var/run/docker.sock:/var/run/docker.sock` and `./docker-compose.prod.yml:/app/docker-compose.prod.yml:ro` bind mounts, `restart: unless-stopped`. Added `:ro` strategy_configs mount to `signals:`. Added `strategy_configs:` to volumes.

## Verification Results

- `docker compose -f docker-compose.yml config --services` — tuner listed, 12 services total
- `docker compose -f docker-compose.prod.yml config --services` — tuner and scheduler listed, 12 services total
- `grep 'strategy_configs' docker-compose.yml docker-compose.prod.yml` — volume appears in both files
- `grep ':ro' docker-compose.yml docker-compose.prod.yml` — signals has read-only mount in both
- `grep 'TUNER_CRON' agents/scheduler/entrypoint.sh` — env var configurability present
- `grep 'FROM alpine:3.19' agents/scheduler/Dockerfile` — confirmed
- `grep 'restart signals' agents/scheduler/crontab` — confirmed

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None. All compose service wiring is complete. Scheduler crontab correctly references the prod compose file path as it will exist inside the container.

## Self-Check: PASSED

Files created:
- agents/scheduler/Dockerfile — FOUND
- agents/scheduler/entrypoint.sh — FOUND
- agents/scheduler/crontab — FOUND

Files modified:
- docker-compose.yml — FOUND
- docker-compose.prod.yml — FOUND

Commits:
- 575a331 (feat Task 1) — present in git log
- 9fb5566 (feat Task 2) — present in git log
