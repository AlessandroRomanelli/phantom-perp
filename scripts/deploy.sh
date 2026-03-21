#!/usr/bin/env bash
set -euo pipefail

# ── Config ──────────────────────────────────────────────────────────────
REMOTE_USER="opc"
REMOTE_HOST="140.238.222.244"
SSH_KEY="$HOME/.ssh/id_rsa"
REMOTE_DIR="~/phantom-perp"
COMPOSE_FILE="docker-compose.prod.yml"
PLATFORM="linux/amd64"
TAG="amd64"
TARBALL="/tmp/phantom-perp-all.tar.gz"

AGENTS=(ingestion signals alpha risk confirmation execution reconciliation monitoring)

# ── Helpers ─────────────────────────────────────────────────────────────
ssh_cmd() { ssh -i "$SSH_KEY" -o ConnectTimeout=10 "$REMOTE_USER@$REMOTE_HOST" "$@"; }
scp_cmd() { scp -i "$SSH_KEY" "$@"; }

log()  { printf "\033[1;34m==>\033[0m %s\n" "$*"; }
ok()   { printf "\033[1;32m OK\033[0m %s\n" "$*"; }
fail() { printf "\033[1;31mERR\033[0m %s\n" "$*" >&2; exit 1; }

usage() {
    cat <<EOF
Usage: $(basename "$0") [options] [agent ...]

Deploy phantom-perp to Oracle Cloud.

Options:
  -h, --help       Show this help
  -b, --build-only Build images locally, don't deploy
  -d, --deploy-only Transfer and restart (skip build, use existing tarball)
  -s, --status     Show remote container status
  -l, --logs AGENT Tail logs for an agent on the remote
  --restart        Restart containers without rebuilding

If agent names are given, only those agents are built/deployed.
Otherwise all agents are included.

Examples:
  ./scripts/deploy.sh                    # Full build + deploy
  ./scripts/deploy.sh risk execution     # Rebuild and deploy only risk + execution
  ./scripts/deploy.sh --status           # Check what's running
  ./scripts/deploy.sh --logs risk        # Tail risk agent logs
  ./scripts/deploy.sh --restart          # Restart without rebuilding
EOF
    exit 0
}

# ── Parse args ──────────────────────────────────────────────────────────
BUILD=true
DEPLOY=true
STATUS_ONLY=false
RESTART_ONLY=false
LOG_AGENT=""
SELECTED_AGENTS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help)       usage ;;
        -b|--build-only) DEPLOY=false; shift ;;
        -d|--deploy-only) BUILD=false; shift ;;
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
    log "Remote container status"
    ssh_cmd "docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'"
    exit 0
fi

# ── Logs ────────────────────────────────────────────────────────────────
if [[ -n "$LOG_AGENT" ]]; then
    ssh_cmd "docker logs --tail 50 -f phantom-perp-${LOG_AGENT}-1"
    exit 0
fi

# ── Restart only ────────────────────────────────────────────────────────
if $RESTART_ONLY; then
    log "Restarting containers on remote"
    ssh_cmd "cd $REMOTE_DIR && docker compose -f $COMPOSE_FILE restart"
    ok "Containers restarted"
    ssh_cmd "docker ps --format 'table {{.Names}}\t{{.Status}}'"
    exit 0
fi

# ── Connectivity check ─────────────────────────────────────────────────
if $DEPLOY; then
    log "Checking SSH connectivity"
    ssh_cmd "hostname" >/dev/null 2>&1 || fail "Cannot connect to $REMOTE_USER@$REMOTE_HOST"
    ok "Connected to $(ssh_cmd hostname)"
fi

# ── Build ───────────────────────────────────────────────────────────────
if $BUILD; then
    log "Building ${#AGENTS[@]} images for $PLATFORM"

    PIDS=()
    for agent in "${AGENTS[@]}"; do
        image="phantom-perp-${agent}:${TAG}"
        log "  Building $image"
        docker buildx build \
            --platform "$PLATFORM" \
            -t "$image" \
            -f "agents/${agent}/Dockerfile" \
            . --load \
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

    log "Saving images to $TARBALL"
    IMAGE_LIST=()
    for agent in "${AGENTS[@]}"; do
        IMAGE_LIST+=("phantom-perp-${agent}:${TAG}")
    done
    docker save "${IMAGE_LIST[@]}" | gzip > "$TARBALL"
    SIZE=$(du -h "$TARBALL" | cut -f1)
    ok "Tarball: $TARBALL ($SIZE)"
fi

# ── Deploy ──────────────────────────────────────────────────────────────
if $DEPLOY; then
    log "Transferring $TARBALL to $REMOTE_HOST"
    scp_cmd "$TARBALL" "$REMOTE_USER@$REMOTE_HOST:~/phantom-perp-all.tar.gz"
    ok "Transfer complete"

    log "Loading images on remote"
    ssh_cmd "docker load < ~/phantom-perp-all.tar.gz"
    ok "Images loaded"

    log "Restarting containers"
    ssh_cmd "cd $REMOTE_DIR && docker compose -f $COMPOSE_FILE up -d"
    ok "Containers updated"

    log "Cleaning up remote tarball"
    ssh_cmd "rm -f ~/phantom-perp-all.tar.gz"

    echo ""
    log "Deployment complete. Container status:"
    ssh_cmd "docker ps --format 'table {{.Names}}\t{{.Status}}'"
fi
