#!/usr/bin/env bash
set -euo pipefail

# ── Config ──────────────────────────────────────────────────────────────
REMOTE_USER="opc"
REMOTE_HOST="140.238.222.244"
SSH_KEY="$HOME/.ssh/id_rsa"

AGENTS=(ingestion signals alpha risk confirmation execution reconciliation monitoring)

UNIFIED_STREAMS=(
    stream:market_snapshots
    stream:funding_updates
    stream:signals
    stream:alerts
)
PORTFOLIO_STREAMS=(
    stream:ranked_ideas:a     stream:ranked_ideas:b
    stream:approved_orders:a  stream:approved_orders:b
    stream:confirmed_orders
    stream:exchange_events:a  stream:exchange_events:b
    stream:portfolio_state:a  stream:portfolio_state:b
    stream:funding_payments:a stream:funding_payments:b
)

# ── Helpers ─────────────────────────────────────────────────────────────
ssh_cmd() { ssh -i "$SSH_KEY" -o ConnectTimeout=10 "$REMOTE_USER@$REMOTE_HOST" "$@" 2>/dev/null; }

BOLD="\033[1m"
DIM="\033[2m"
RESET="\033[0m"
GREEN="\033[32m"
RED="\033[31m"
YELLOW="\033[33m"
CYAN="\033[36m"
BLUE="\033[34m"

section() { printf "\n${BOLD}${BLUE}── %s ──${RESET}\n\n" "$*"; }

usage() {
    cat <<EOF
Usage: $(basename "$0") [options]

Show status report of phantom-perp deployed on Oracle Cloud.

Options:
  -h, --help       Show this help
  -s, --short      Compact summary (containers + resource usage only)
  -l, --logs       Include recent log snippets per agent
  -e, --errors     Show only agents with errors/restarts
  --json           Output key metrics as JSON (for programmatic use)
EOF
    exit 0
}

SHORT=false
SHOW_LOGS=false
ERRORS_ONLY=false
JSON_OUT=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help)    usage ;;
        -s|--short)   SHORT=true; shift ;;
        -l|--logs)    SHOW_LOGS=true; shift ;;
        -e|--errors)  ERRORS_ONLY=true; shift ;;
        --json)       JSON_OUT=true; shift ;;
        *)            echo "Unknown option: $1"; exit 1 ;;
    esac
done

# ── Connectivity ────────────────────────────────────────────────────────
if ! ssh_cmd "true" 2>/dev/null; then
    printf "${RED}Cannot connect to %s@%s${RESET}\n" "$REMOTE_USER" "$REMOTE_HOST"
    exit 1
fi

# ── JSON output mode ────────────────────────────────────────────────────
if $JSON_OUT; then
    ssh_cmd 'bash -s' <<'REMOTE_SCRIPT'
        echo "{"

        # Containers
        echo '  "containers": ['
        first=true
        while IFS='|' read -r name status; do
            $first || echo ","
            first=false
            printf '    {"name": "%s", "status": "%s"}' "$name" "$status"
        done < <(docker ps -a --format '{{.Names}}|{{.Status}}' --filter 'name=phantom-perp')
        echo ""
        echo "  ],"

        # Resources
        echo '  "resources": ['
        first=true
        while IFS='|' read -r name cpu mem; do
            $first || echo ","
            first=false
            printf '    {"name": "%s", "cpu": "%s", "mem": "%s"}' "$name" "$cpu" "$mem"
        done < <(docker stats --no-stream --format '{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}' 2>/dev/null)
        echo ""
        echo "  ],"

        # Host
        mem_total=$(free -m | awk '/Mem:/{print $2}')
        mem_used=$(free -m | awk '/Mem:/{print $3}')
        swap_total=$(free -m | awk '/Swap:/{print $2}')
        swap_used=$(free -m | awk '/Swap:/{print $3}')
        disk_pct=$(df / | awk 'NR==2{print $5}')
        load=$(cat /proc/loadavg | cut -d' ' -f1-3)
        uptime_s=$(awk '{print int($1)}' /proc/uptime)

        printf '  "host": {"mem_total_mb": %d, "mem_used_mb": %d, "swap_total_mb": %d, "swap_used_mb": %d, "disk_used": "%s", "load": "%s", "uptime_seconds": %d}\n' \
            "$mem_total" "$mem_used" "$swap_total" "$swap_used" "$disk_pct" "$load" "$uptime_s"

        echo "}"
REMOTE_SCRIPT
    exit 0
fi

# ── Header ──────────────────────────────────────────────────────────────
printf "${BOLD}phantom-perp Status Report${RESET}\n"
printf "${DIM}%s | %s@%s${RESET}\n" "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$REMOTE_USER" "$REMOTE_HOST"

# ── Host Resources ──────────────────────────────────────────────────────
section "Host Resources"

ssh_cmd 'bash -s' <<'REMOTE_SCRIPT'
    uptime_s=$(awk '{print int($1)}' /proc/uptime)
    days=$((uptime_s / 86400))
    hours=$(( (uptime_s % 86400) / 3600 ))
    printf "  Uptime:   %dd %dh\n" "$days" "$hours"

    load=$(cat /proc/loadavg | cut -d' ' -f1-3)
    cores=$(nproc)
    printf "  Load:     %s  (%d cores)\n" "$load" "$cores"

    mem_total=$(free -m | awk '/Mem:/{print $2}')
    mem_used=$(free -m | awk '/Mem:/{print $3}')
    mem_pct=$((mem_used * 100 / mem_total))
    printf "  Memory:   %d / %d MB (%d%%)\n" "$mem_used" "$mem_total" "$mem_pct"

    swap_total=$(free -m | awk '/Swap:/{print $2}')
    swap_used=$(free -m | awk '/Swap:/{print $3}')
    if [[ $swap_total -gt 0 ]]; then
        swap_pct=$((swap_used * 100 / swap_total))
        printf "  Swap:     %d / %d MB (%d%%)\n" "$swap_used" "$swap_total" "$swap_pct"
    fi

    disk_used=$(df / | awk 'NR==2{print $3/1024/1024}')
    disk_total=$(df / | awk 'NR==2{print $2/1024/1024}')
    disk_pct=$(df / | awk 'NR==2{print $5}')
    printf "  Disk:     %.1f / %.1f GB (%s)\n" "$disk_used" "$disk_total" "$disk_pct"
REMOTE_SCRIPT

# ── Containers ──────────────────────────────────────────────────────────
section "Containers"

CONTAINER_DATA=$(ssh_cmd "docker ps -a --format '{{.Names}}|{{.Status}}|{{.RunningFor}}' --filter 'name=phantom-perp' | sort")
RESTART_DATA=$(ssh_cmd "docker inspect --format '{{.Name}}|{{.RestartCount}}' \$(docker ps -aq --filter 'name=phantom-perp') 2>/dev/null | sort")

printf "  ${BOLD}%-35s %-12s %-20s %s${RESET}\n" "CONTAINER" "STATE" "UPTIME" "RESTARTS"
while IFS='|' read -r name status running_for; do
    [[ -z "$name" ]] && continue

    if [[ "$status" == Up* ]]; then
        state="${GREEN}running${RESET}"
    else
        state="${RED}stopped${RESET}"
    fi

    # Extract restart count
    restarts=0
    while IFS='|' read -r rname rcount; do
        if [[ "$rname" == "/$name" ]]; then
            restarts=$rcount
            break
        fi
    done <<< "$RESTART_DATA"

    restart_str="$restarts"
    if [[ $restarts -gt 0 ]]; then
        restart_str="${YELLOW}${restarts}${RESET}"
    fi

    if $ERRORS_ONLY && [[ "$status" == Up* ]] && [[ $restarts -eq 0 ]]; then
        continue
    fi

    printf "  %-35s %-22b %-20s %b\n" "$name" "$state" "$running_for" "$restart_str"
done <<< "$CONTAINER_DATA"

if $SHORT; then
    section "Resource Usage"
    printf "  ${BOLD}%-35s %8s %18s${RESET}\n" "CONTAINER" "CPU" "MEMORY"
    ssh_cmd "docker stats --no-stream --format '{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}'" | sort | while IFS='|' read -r name cpu mem; do
        printf "  %-35s %8s %18s\n" "$name" "$cpu" "$mem"
    done
    TOTAL_MEM=$(ssh_cmd "docker stats --no-stream --format '{{.MemUsage}}'" | awk -F/ '{gsub(/[A-Za-z ]/,"",$1); sum+=$1} END{printf "%.1f", sum}')
    printf "\n  ${BOLD}Total container memory: %s MiB${RESET}\n" "$TOTAL_MEM"
    exit 0
fi

# ── Resource Usage ──────────────────────────────────────────────────────
section "Resource Usage"

printf "  ${BOLD}%-35s %8s %18s %10s${RESET}\n" "CONTAINER" "CPU" "MEMORY" "NET I/O"
ssh_cmd "docker stats --no-stream --format '{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}|{{.NetIO}}'" | sort | while IFS='|' read -r name cpu mem net; do
    printf "  %-35s %8s %18s %10s\n" "$name" "$cpu" "$mem" "$net"
done

TOTAL_MEM=$(ssh_cmd "docker stats --no-stream --format '{{.MemUsage}}'" | awk -F/ '{gsub(/[A-Za-z ]/,"",$1); sum+=$1} END{printf "%.1f", sum}')
printf "\n  ${BOLD}Total container memory: %s MiB${RESET}\n" "$TOTAL_MEM"

# ── Redis Streams ───────────────────────────────────────────────────────
section "Redis Streams"

ALL_STREAMS=("${UNIFIED_STREAMS[@]}" "${PORTFOLIO_STREAMS[@]}")

printf "  ${BOLD}%-35s %10s %10s %s${RESET}\n" "STREAM" "LENGTH" "GROUPS" "LAST ENTRY"

for stream in "${ALL_STREAMS[@]}"; do
    INFO=$(ssh_cmd "docker exec phantom-perp-redis-1 redis-cli XINFO STREAM $stream 2>/dev/null" 2>/dev/null || echo "")
    if [[ -z "$INFO" ]]; then
        printf "  ${DIM}%-35s %10s${RESET}\n" "$stream" "(empty)"
        continue
    fi

    len=$(echo "$INFO" | awk '/^length$/{getline; print}')
    groups=$(echo "$INFO" | awk '/^groups$/{getline; print}')

    # Extract timestamp from last entry ID
    last_id=$(echo "$INFO" | awk '/^last-generated-id$/{getline; print}')
    if [[ -n "$last_id" && "$last_id" != "0-0" ]]; then
        ts_ms=${last_id%%-*}
        last_time=$(ssh_cmd "date -d @$((ts_ms / 1000)) '+%H:%M:%S' 2>/dev/null" || echo "")
    else
        last_time="-"
    fi

    printf "  %-35s %10s %10s %s\n" "$stream" "${len:-0}" "${groups:-0}" "$last_time"
done

# ── Pipeline Flow ───────────────────────────────────────────────────────
section "Pipeline Flow (messages in last 60s)"

ssh_cmd 'bash -s' <<'REMOTE_SCRIPT'
    now_ms=$(($(date +%s) * 1000))
    start_ms=$((now_ms - 60000))

    streams=(
        "stream:market_snapshots|Ingestion -> Signals"
        "stream:signals|Signals -> Alpha"
        "stream:ranked_ideas:a|Alpha -> Risk (A)"
        "stream:ranked_ideas:b|Alpha -> Risk (B)"
        "stream:approved_orders:a|Risk -> Execution (A)"
        "stream:approved_orders:b|Risk -> Confirmation"
        "stream:confirmed_orders|Confirm -> Execution (B)"
        "stream:portfolio_state:a|Recon -> Monitoring (A)"
        "stream:portfolio_state:b|Recon -> Monitoring (B)"
    )

    for entry in "${streams[@]}"; do
        IFS='|' read -r stream label <<< "$entry"
        count=$(docker exec phantom-perp-redis-1 redis-cli XRANGE "$stream" "$start_ms" + 2>/dev/null | wc -l)
        # Each entry has a key line + field lines; rough estimate: entries = lines / 3
        msgs=$((count / 3))
        if [[ $msgs -gt 0 ]]; then
            printf "  %-35s %4d msg/min\n" "$label" "$msgs"
        else
            printf "  \033[2m%-35s %4d msg/min\033[0m\n" "$label" "0"
        fi
    done
REMOTE_SCRIPT

# ── Latest Market Data & Portfolio State ─────────────────────────────────
section "Latest Market Data"

ssh_cmd 'bash -s' <<'REMOTE_SCRIPT'
docker exec phantom-perp-redis-1 redis-cli XREVRANGE stream:market_snapshots + - COUNT 1 2>/dev/null | grep '^{' | head -1 | python3 -c "
import sys, json
line = sys.stdin.readline().strip()
if not line:
    print('  (no data)')
    sys.exit(0)
d = json.loads(line)
print(f'  Instrument:     {d.get(\"instrument\", \"N/A\")}')
print(f'  Mark Price:     \${float(d.get(\"mark_price\", 0)):,.2f}')
print(f'  Index Price:    \${float(d.get(\"index_price\", 0)):,.2f}')
print(f'  Last Price:     \${float(d.get(\"last_price\", 0)):,.2f}')
print(f'  Spread:         {float(d.get(\"spread_bps\", 0)):.2f} bps')
print(f'  Funding Rate:   {float(d.get(\"funding_rate\", 0)) * 100:.4f}%')
print(f'  Vol (1h/24h):   {float(d.get(\"volatility_1h\", 0)):.4f} / {float(d.get(\"volatility_24h\", 0)):.4f}')
print(f'  OB Imbalance:   {float(d.get(\"orderbook_imbalance\", 0)):+.4f}')
print(f'  Timestamp:      {d.get(\"timestamp\", \"N/A\")}')
" 2>/dev/null || echo "  (unable to parse)"
REMOTE_SCRIPT

section "Portfolio State"

ssh_cmd 'bash -s' <<'REMOTE_SCRIPT'
for suffix in a b; do
    if [ "$suffix" = "a" ]; then
        label="Portfolio A (Autonomous)"
    else
        label="Portfolio B (User-Confirmed)"
    fi

    docker exec phantom-perp-redis-1 redis-cli XREVRANGE "stream:portfolio_state:$suffix" + - COUNT 1 2>/dev/null | grep '^{' | head -1 | python3 -c "
import sys, json
label = '$label'
line = sys.stdin.readline().strip()
if not line:
    print(f'  \033[2m{label}: no data\033[0m')
    print()
    sys.exit(0)
d = json.loads(line)
print(f'  {label}')
print(f'    Equity:          \${float(d.get(\"equity_usdc\", 0)):,.2f} USDC')
print(f'    Margin Used:     \${float(d.get(\"used_margin_usdc\", 0)):,.2f} USDC ({float(d.get(\"margin_utilization_pct\", 0)):.1f}%)')
print(f'    Unrealized P&L:  \${float(d.get(\"unrealized_pnl_usdc\", 0)):+,.2f} USDC')
print(f'    Realized Today:  \${float(d.get(\"realized_pnl_today_usdc\", 0)):+,.2f} USDC')
print(f'    Funding Today:   \${float(d.get(\"funding_pnl_today_usdc\", 0)):+,.2f} USDC')
print(f'    Net P&L Today:   \${float(d.get(\"net_pnl_today_usdc\", 0)):+,.2f} USDC')
positions = d.get('positions', [])
if positions:
    for p in positions:
        side = p.get('side', '?')
        size = float(p.get('size', 0))
        entry = float(p.get('entry_price', 0))
        mark = float(p.get('mark_price', 0))
        pnl = float(p.get('unrealized_pnl_usdc', 0))
        lev = float(p.get('leverage', 0))
        print(f'    Position:        {side} {size} ETH @ \${entry:,.2f} (mark \${mark:,.2f}, P&L \${pnl:+,.2f}, {lev:.1f}x)')
else:
    print(f'    Positions:       FLAT')
print()
" 2>/dev/null || echo "  $label: (unable to parse)"
done
REMOTE_SCRIPT

# ── Agent Logs ──────────────────────────────────────────────────────────
if $SHOW_LOGS; then
    section "Recent Logs (last 10 lines per agent)"

    for agent in "${AGENTS[@]}"; do
        container="phantom-perp-${agent}-1"
        printf "  ${BOLD}%s${RESET}\n" "$container"
        ssh_cmd "docker logs --tail 10 $container 2>&1" | sed 's/^/    /'
        echo ""
    done
fi

# ── Error Check ─────────────────────────────────────────────────────────
section "Error Check (last 100 lines per agent)"

FOUND_ERRORS=false
for agent in "${AGENTS[@]}"; do
    container="phantom-perp-${agent}-1"
    ERRORS=$(ssh_cmd "docker logs --tail 100 $container 2>&1 | grep -iE 'error|exception|traceback|critical|HALT' | tail -5")
    if [[ -n "$ERRORS" ]]; then
        FOUND_ERRORS=true
        printf "  ${RED}${BOLD}%s${RESET}\n" "$container"
        echo "$ERRORS" | sed 's/^/    /'
        echo ""
    fi
done

if ! $FOUND_ERRORS; then
    printf "  ${GREEN}No errors found in recent logs${RESET}\n"
fi

echo ""
