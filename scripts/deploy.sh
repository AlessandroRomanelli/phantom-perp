#!/usr/bin/env bash
set -euo pipefail

# ── Config ──────────────────────────────────────────────────────────────
COMPOSE_FILE="docker-compose.yml"
AGENTS=(ingestion signals alpha risk confirmation execution reconciliation monitoring tuner scheduler dashboard)

# ── Helpers ─────────────────────────────────────────────────────────────
log()  { printf "\033[1;34m==>\033[0m %s\n" "$*"; }
ok()   { printf "\033[1;32m OK\033[0m %s\n" "$*"; }
fail() { printf "\033[1;31mERR\033[0m %s\n" "$*" >&2; exit 1; }

usage() {
    cat <<EOF
Usage: $(basename "$0") [options] [agent ...]

Build and deploy phantom-perp locally via Docker Compose.

Options:
  -h, --help       Show this help
  -b, --build-only Build images without starting containers
  -s, --status     Show container status
  -l, --logs AGENT Tail logs for an agent
  --restart        Restart containers without rebuilding

If agent names are given, only those agents are rebuilt.
Otherwise all agents are rebuilt.

Examples:
  ./scripts/deploy.sh                    # Full rebuild + up
  ./scripts/deploy.sh signals risk       # Rebuild only signals + risk, then up
  ./scripts/deploy.sh --status           # Check what's running
  ./scripts/deploy.sh --logs signals     # Tail signals logs
  ./scripts/deploy.sh --restart          # Restart without rebuilding
EOF
    exit 0
}

# ── Parse args ──────────────────────────────────────────────────────────
BUILD=true
START=true
STATUS_ONLY=false
RESTART_ONLY=false
LOG_AGENT=""
SELECTED_AGENTS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help)       usage ;;
        -b|--build-only) START=false; shift ;;
        -s|--status)     STATUS_ONLY=true; shift ;;
        -l|--logs)       LOG_AGENT="$2"; shift 2 ;;
        --restart)       RESTART_ONLY=true; BUILD=false; shift ;;
        -*)              fail "Unknown option: $1" ;;
        *)               SELECTED_AGENTS+=("$1"); shift ;;
    esac
done

if [[ ${#SELECTED_AGENTS[@]} -gt 0 ]]; then
    AGENTS=("${SELECTED_AGENTS[@]}")
fi

# ── Status ──────────────────────────────────────────────────────────────
if $STATUS_ONLY; then
    docker compose -f "$COMPOSE_FILE" ps
    exit 0
fi

# ── Logs ────────────────────────────────────────────────────────────────
if [[ -n "$LOG_AGENT" ]]; then
    docker compose -f "$COMPOSE_FILE" logs --tail 50 -f "$LOG_AGENT"
    exit 0
fi

# ── Restart only ────────────────────────────────────────────────────────
if $RESTART_ONLY; then
    log "Restarting containers"
    docker compose -f "$COMPOSE_FILE" restart
    ok "Containers restarted"
    docker compose -f "$COMPOSE_FILE" ps
    exit 0
fi

# ── Build ────────────────────────────────────────────────────────────────
if $BUILD; then
    log "Building ${#AGENTS[@]} image(s)"

    PIDS=()
    for agent in "${AGENTS[@]}"; do
        log "  Building $agent"
        docker compose -f "$COMPOSE_FILE" build "$agent" \
            > "/tmp/build-${agent}.log" 2>&1 &
        PIDS+=($!)
    done

    FAILED=0
    for i in "${!PIDS[@]}"; do
        if wait "${PIDS[$i]}"; then
            ok "  ${AGENTS[$i]}"
        else
            printf "\033[1;31mFAIL\033[0m  %s (see /tmp/build-%s.log)\n" "${AGENTS[$i]}" "${AGENTS[$i]}"
            FAILED=1
        fi
    done
    [[ $FAILED -eq 1 ]] && fail "Some builds failed"
fi

# ── Start ────────────────────────────────────────────────────────────────
if $START; then
    log "Starting containers"
    docker compose -f "$COMPOSE_FILE" up -d
    ok "Containers up"
    echo ""
    docker compose -f "$COMPOSE_FILE" ps
fi
