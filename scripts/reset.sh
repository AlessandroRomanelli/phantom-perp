#!/usr/bin/env bash
set -euo pipefail

# ── Full system reset: stop all containers, drop Redis + Postgres data,
#    rebuild images, and restart everything from scratch.
#
# Usage:
#   ./scripts/reset.sh              # Local dev (docker-compose.yml)
#   ./scripts/reset.sh --prod       # Production (docker-compose.prod.yml)
#   ./scripts/reset.sh --no-build   # Skip image rebuild (restart only)
#   ./scripts/reset.sh --yes        # Skip confirmation prompt

# ── Helpers ─────────────────────────────────────────────────────────────
RED='\033[1;31m'
GREEN='\033[1;32m'
BLUE='\033[1;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { printf "${BLUE}==>${NC} %s\n" "$*"; }
ok()   { printf "${GREEN} OK${NC} %s\n" "$*"; }
warn() { printf "${YELLOW}WRN${NC} %s\n" "$*"; }
fail() { printf "${RED}ERR${NC} %s\n" "$*" >&2; exit 1; }

# ── Parse flags ─────────────────────────────────────────────────────────
COMPOSE_FILE="docker-compose.yml"
SKIP_BUILD=false
SKIP_CONFIRM=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --prod)       COMPOSE_FILE="docker-compose.prod.yml"; shift ;;
        --no-build)   SKIP_BUILD=true; shift ;;
        --yes|-y)     SKIP_CONFIRM=true; shift ;;
        -h|--help)
            echo "Usage: $(basename "$0") [--prod] [--no-build] [--yes]"
            echo ""
            echo "  --prod       Use docker-compose.prod.yml (pre-built images)"
            echo "  --no-build   Skip image rebuild, just restart"
            echo "  --yes, -y    Skip confirmation prompt"
            exit 0
            ;;
        *) fail "Unknown option: $1" ;;
    esac
done

# ── Resolve project root ───────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

if [[ ! -f "$COMPOSE_FILE" ]]; then
    fail "Compose file not found: $PROJECT_ROOT/$COMPOSE_FILE"
fi

COMPOSE="docker compose -f $COMPOSE_FILE"

# ── Confirmation ────────────────────────────────────────────────────────
if [[ "$SKIP_CONFIRM" != "true" ]]; then
    echo ""
    printf "${RED}╔══════════════════════════════════════════════╗${NC}\n"
    printf "${RED}║  FULL SYSTEM RESET — ALL DATA WILL BE LOST  ║${NC}\n"
    printf "${RED}╠══════════════════════════════════════════════╣${NC}\n"
    printf "${RED}║${NC}  Compose file: %-28s ${RED}║${NC}\n" "$COMPOSE_FILE"
    printf "${RED}║${NC}  Redis:        %-28s ${RED}║${NC}\n" "FLUSHALL + volume removed"
    printf "${RED}║${NC}  Postgres:     %-28s ${RED}║${NC}\n" "Volume removed (full drop)"
    printf "${RED}║${NC}  Images:       %-28s ${RED}║${NC}\n" "$(if $SKIP_BUILD; then echo 'kept (--no-build)'; else echo 'rebuilt from source'; fi)"
    printf "${RED}╚══════════════════════════════════════════════╝${NC}\n"
    echo ""
    read -rp "Type 'reset' to confirm: " CONFIRM
    if [[ "$CONFIRM" != "reset" ]]; then
        echo "Aborted."
        exit 0
    fi
fi

# ── Step 1: Stop all containers ────────────────────────────────────────
log "Stopping all containers..."
$COMPOSE down --timeout 15 2>/dev/null || true
ok "Containers stopped"

# ── Step 2: Remove data volumes ────────────────────────────────────────
log "Removing data volumes..."

# Extract the compose project name — docker compose lowercases the dir name
# and keeps hyphens (but strips other special chars).
PROJECT_NAME=$(docker compose -f "$COMPOSE_FILE" config --format json 2>/dev/null \
    | head -1 | sed 's/.*"name": *"//;s/".*//' 2>/dev/null || true)
if [[ -z "$PROJECT_NAME" || "$PROJECT_NAME" == "{" ]]; then
    # Fallback: replicate docker compose's default naming (lowercase, keep hyphens)
    PROJECT_NAME=$(basename "$PROJECT_ROOT" | tr '[:upper:]' '[:lower:]')
fi

for vol in redis_data pg_data; do
    FULL_VOL="${PROJECT_NAME}_${vol}"
    if docker volume inspect "$FULL_VOL" &>/dev/null; then
        docker volume rm "$FULL_VOL"
        ok "Removed volume: $FULL_VOL"
    else
        warn "Volume not found (already clean): $FULL_VOL"
    fi
done

# strategy_configs volume — remove too for a truly clean state
STRAT_VOL="${PROJECT_NAME}_strategy_configs"
if docker volume inspect "$STRAT_VOL" &>/dev/null; then
    docker volume rm "$STRAT_VOL"
    ok "Removed volume: $STRAT_VOL"
fi

# ── Step 3: Rebuild images (dev only) ──────────────────────────────────
if [[ "$SKIP_BUILD" == "true" ]]; then
    log "Skipping image rebuild (--no-build)"
elif [[ "$COMPOSE_FILE" == "docker-compose.prod.yml" ]]; then
    log "Production mode — skipping build (uses pre-built images)"
else
    log "Rebuilding all images (this may take a few minutes)..."
    $COMPOSE build --parallel
    ok "All images rebuilt"
fi

# ── Step 4: Start infrastructure first ─────────────────────────────────
log "Starting Redis and Postgres..."
$COMPOSE up -d redis postgres

# Wait for health checks
log "Waiting for services to be healthy..."
TIMEOUT=60
ELAPSED=0
while [[ $ELAPSED -lt $TIMEOUT ]]; do
    REDIS_HEALTHY=$($COMPOSE ps redis --format '{{.Health}}' 2>/dev/null || echo "unknown")
    PG_HEALTHY=$($COMPOSE ps postgres --format '{{.Health}}' 2>/dev/null || echo "unknown")

    if [[ "$REDIS_HEALTHY" == "healthy" && "$PG_HEALTHY" == "healthy" ]]; then
        break
    fi
    sleep 2
    ELAPSED=$((ELAPSED + 2))
    printf "."
done
echo ""

if [[ $ELAPSED -ge $TIMEOUT ]]; then
    warn "Timed out waiting for services — checking status..."
    $COMPOSE ps
    fail "Redis or Postgres not healthy after ${TIMEOUT}s"
fi

ok "Redis and Postgres healthy"

# ── Step 5: Start all agents ───────────────────────────────────────────
log "Starting all agents..."
$COMPOSE up -d
ok "All services started"

# ── Step 6: Status summary ─────────────────────────────────────────────
echo ""
log "System status:"
$COMPOSE ps --format "table {{.Name}}\t{{.Status}}\t{{.Health}}"

echo ""
printf "${GREEN}╔══════════════════════════════════════════════╗${NC}\n"
printf "${GREEN}║         RESET COMPLETE — CLEAN STATE         ║${NC}\n"
printf "${GREEN}╚══════════════════════════════════════════════╝${NC}\n"
echo ""
echo "  Logs:   $COMPOSE logs -f"
echo "  Status: $COMPOSE ps"
echo "  Stop:   $COMPOSE down"
echo ""
