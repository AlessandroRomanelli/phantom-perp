#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILE="docker-compose.yml"
AGENTS=(ingestion signals alpha risk confirmation execution reconciliation monitoring tuner scheduler dashboard)

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

Show status of the local phantom-perp deployment.

Options:
  -h, --help    Show this help
  -s, --short   Compact summary (containers + resource usage only)
  -l, --logs    Include recent log snippets per agent
  -e, --errors  Show only agents with errors/restarts
  --json        Output key metrics as JSON
EOF
    exit 0
}

SHORT=false
SHOW_LOGS=false
ERRORS_ONLY=false
JSON_OUT=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help)   usage ;;
        -s|--short)  SHORT=true; shift ;;
        -l|--logs)   SHOW_LOGS=true; shift ;;
        -e|--errors) ERRORS_ONLY=true; shift ;;
        --json)      JSON_OUT=true; shift ;;
        *)           echo "Unknown option: $1"; exit 1 ;;
    esac
done

redis_cmd() { docker compose -f "$COMPOSE_FILE" exec -T redis redis-cli "$@" 2>/dev/null; }

# ── JSON output mode ────────────────────────────────────────────────────
if $JSON_OUT; then
    echo "{"
    echo '  "containers": ['
    first=true
    while IFS='|' read -r name status; do
        $first || echo ","
        first=false
        printf '    {"name": "%s", "status": "%s"}' "$name" "$status"
    done < <(docker compose -f "$COMPOSE_FILE" ps --format '{{.Name}}|{{.Status}}')
    echo ""
    echo "  ]"
    echo "}"
    exit 0
fi

# ── Header ──────────────────────────────────────────────────────────────
printf "${BOLD}phantom-perp Status Report${RESET}\n"
printf "${DIM}%s${RESET}\n" "$(date '+%Y-%m-%d %H:%M:%S %Z')"

# ── Host Resources ──────────────────────────────────────────────────────
section "Host Resources"

uptime_s=$(awk '{print int($1)}' /proc/uptime 2>/dev/null || sysctl -n kern.boottime 2>/dev/null | awk '{print int($NF)}' || echo 0)
if [[ $uptime_s -gt 0 ]]; then
    days=$((uptime_s / 86400)); hours=$(( (uptime_s % 86400) / 3600 ))
    printf "  Uptime:   %dd %dh\n" "$days" "$hours"
fi
printf "  Load:     %s\n" "$(uptime | awk -F'load average' '{print $2}' | tr -d ': ')"

if command -v free &>/dev/null; then
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
fi

disk_pct=$(df . | awk 'NR==2{print $5}')
disk_used=$(df . | awk 'NR==2{printf "%.1f", $3/1024/1024}')
disk_total=$(df . | awk 'NR==2{printf "%.1f", $2/1024/1024}')
printf "  Disk:     %s GB / %s GB (%s)\n" "$disk_used" "$disk_total" "$disk_pct"

# ── Containers ──────────────────────────────────────────────────────────
section "Containers"

printf "  ${BOLD}%-40s %-12s %-20s %s${RESET}\n" "CONTAINER" "STATE" "STATUS" "RESTARTS"
while IFS='|' read -r name status; do
    [[ -z "$name" ]] && continue

    if [[ "$status" == *"Up"* ]] || [[ "$status" == *"running"* ]]; then
        state="${GREEN}running${RESET}"
    else
        state="${RED}stopped${RESET}"
    fi

    restarts=$(docker inspect --format '{{.RestartCount}}' "$name" 2>/dev/null || echo 0)
    restart_str="$restarts"
    [[ $restarts -gt 0 ]] && restart_str="${YELLOW}${restarts}${RESET}"

    if $ERRORS_ONLY && [[ "$status" == *"Up"* ]] && [[ $restarts -eq 0 ]]; then
        continue
    fi

    printf "  %-40s %-22b %-20s %b\n" "$name" "$state" "$status" "$restart_str"
done < <(docker compose -f "$COMPOSE_FILE" ps --format '{{.Name}}|{{.Status}}')

if $SHORT; then
    section "Resource Usage"
    printf "  ${BOLD}%-40s %8s %18s${RESET}\n" "CONTAINER" "CPU" "MEMORY"
    docker stats --no-stream --format '{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}' \
        $(docker compose -f "$COMPOSE_FILE" ps -q) 2>/dev/null | sort | \
        while IFS='|' read -r name cpu mem; do
            printf "  %-40s %8s %18s\n" "$name" "$cpu" "$mem"
        done
    exit 0
fi

# ── Resource Usage ──────────────────────────────────────────────────────
section "Resource Usage"

printf "  ${BOLD}%-40s %8s %18s %10s${RESET}\n" "CONTAINER" "CPU" "MEMORY" "NET I/O"
docker stats --no-stream --format '{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}|{{.NetIO}}' \
    $(docker compose -f "$COMPOSE_FILE" ps -q) 2>/dev/null | sort | \
    while IFS='|' read -r name cpu mem net; do
        printf "  %-40s %8s %18s %10s\n" "$name" "$cpu" "$mem" "$net"
    done

# ── Redis Streams ───────────────────────────────────────────────────────
section "Redis Streams"

ALL_STREAMS=("${UNIFIED_STREAMS[@]}" "${PORTFOLIO_STREAMS[@]}")
printf "  ${BOLD}%-35s %10s %10s %s${RESET}\n" "STREAM" "LENGTH" "GROUPS" "LAST ENTRY"

for stream in "${ALL_STREAMS[@]}"; do
    INFO=$(redis_cmd XINFO STREAM "$stream" 2>/dev/null || echo "")
    if [[ -z "$INFO" ]]; then
        printf "  ${DIM}%-35s %10s${RESET}\n" "$stream" "(empty)"
        continue
    fi
    len=$(echo "$INFO" | awk '/^length$/{getline; print}')
    groups=$(echo "$INFO" | awk '/^groups$/{getline; print}')
    last_id=$(echo "$INFO" | awk '/^last-generated-id$/{getline; print}')
    if [[ -n "$last_id" && "$last_id" != "0-0" ]]; then
        ts_ms=${last_id%%-*}
        last_time=$(date -d "@$((ts_ms / 1000))" '+%H:%M:%S' 2>/dev/null || date -r "$((ts_ms / 1000))" '+%H:%M:%S' 2>/dev/null || echo "?")
    else
        last_time="-"
    fi
    printf "  %-35s %10s %10s %s\n" "$stream" "${len:-0}" "${groups:-0}" "$last_time"
done

# ── Pipeline Flow ───────────────────────────────────────────────────────
section "Pipeline Flow (messages in last 60s)"

now_ms=$(( $(date +%s) * 1000 ))
start_ms=$(( now_ms - 60000 ))

declare -A FLOW_LABELS=(
    ["stream:market_snapshots"]="Ingestion -> Signals"
    ["stream:signals"]="Signals -> Alpha"
    ["stream:ranked_ideas:a"]="Alpha -> Risk (A)"
    ["stream:ranked_ideas:b"]="Alpha -> Risk (B)"
    ["stream:approved_orders:a"]="Risk -> Execution (A)"
    ["stream:approved_orders:b"]="Risk -> Confirmation"
    ["stream:confirmed_orders"]="Confirm -> Execution (B)"
    ["stream:portfolio_state:a"]="Recon -> Monitoring (A)"
    ["stream:portfolio_state:b"]="Recon -> Monitoring (B)"
)

for stream in stream:market_snapshots stream:signals stream:ranked_ideas:a stream:ranked_ideas:b \
              stream:approved_orders:a stream:approved_orders:b stream:confirmed_orders \
              stream:portfolio_state:a stream:portfolio_state:b; do
    count=$(redis_cmd XRANGE "$stream" "$start_ms" + 2>/dev/null | wc -l || echo 0)
    msgs=$(( count / 3 ))
    label="${FLOW_LABELS[$stream]}"
    if [[ $msgs -gt 0 ]]; then
        printf "  %-35s %4d msg/min\n" "$label" "$msgs"
    else
        printf "  ${DIM}%-35s %4d msg/min${RESET}\n" "$label" "0"
    fi
done

# ── Latest Market Data ───────────────────────────────────────────────────
section "Latest Market Data"

redis_cmd XREVRANGE stream:market_snapshots + - COUNT 1 2>/dev/null | grep '^{' | head -1 | python3 -c "
import sys, json
line = sys.stdin.readline().strip()
if not line:
    print('  (no data)')
    sys.exit(0)
d = json.loads(line)
print(f'  Instrument:    {d.get(\"instrument\", \"N/A\")}')
print(f'  Mark Price:    \${float(d.get(\"mark_price\", 0)):,.2f}')
print(f'  Funding Rate:  {float(d.get(\"funding_rate\", 0)) * 100:.4f}%')
print(f'  Vol (1h/24h):  {float(d.get(\"volatility_1h\", 0)):.4f} / {float(d.get(\"volatility_24h\", 0)):.4f}')
print(f'  OB Imbalance:  {float(d.get(\"orderbook_imbalance\", 0)):+.4f}')
print(f'  Timestamp:     {d.get(\"timestamp\", \"N/A\")}')
" 2>/dev/null || echo "  (unable to parse)"

# ── Portfolio State ──────────────────────────────────────────────────────
section "Portfolio State"

for suffix in a b; do
    label="Portfolio $(echo $suffix | tr '[:lower:]' '[:upper:]')"
    redis_cmd XREVRANGE "stream:portfolio_state:$suffix" + - COUNT 1 2>/dev/null | grep '^{' | head -1 | python3 -c "
import sys, json
label = '$label'
line = sys.stdin.readline().strip()
if not line:
    print(f'  {label}: no data')
    print()
    sys.exit(0)
d = json.loads(line)
print(f'  {label}')
print(f'    Equity:         \${float(d.get(\"equity_usdc\", 0)):,.2f} USDC')
print(f'    Margin Used:    \${float(d.get(\"used_margin_usdc\", 0)):,.2f} USDC ({float(d.get(\"margin_utilization_pct\", 0)):.1f}%)')
print(f'    Unrealized P&L: \${float(d.get(\"unrealized_pnl_usdc\", 0)):+,.2f} USDC')
print(f'    Net P&L Today:  \${float(d.get(\"net_pnl_today_usdc\", 0)):+,.2f} USDC')
for p in d.get('positions', []):
    side = p.get('side', '?'); size = float(p.get('size', 0))
    entry = float(p.get('entry_price', 0)); mark = float(p.get('mark_price', 0))
    pnl = float(p.get('unrealized_pnl_usdc', 0)); lev = float(p.get('leverage', 0))
    print(f'    Position:       {side} {size} @ \${entry:,.2f} (mark \${mark:,.2f}, P&L \${pnl:+,.2f}, {lev:.1f}x)')
if not d.get('positions'):
    print(f'    Positions:      FLAT')
print()
" 2>/dev/null || echo "  $label: (unable to parse)"
done

# ── Agent Logs ──────────────────────────────────────────────────────────
if $SHOW_LOGS; then
    section "Recent Logs (last 10 lines per agent)"
    for agent in "${AGENTS[@]}"; do
        printf "  ${BOLD}%s${RESET}\n" "$agent"
        docker compose -f "$COMPOSE_FILE" logs --tail 10 "$agent" 2>&1 | sed 's/^/    /'
        echo ""
    done
fi

# ── Error Check ─────────────────────────────────────────────────────────
section "Error Check (last 100 lines per agent)"

FOUND_ERRORS=false
for agent in "${AGENTS[@]}"; do
    ERRORS=$(docker compose -f "$COMPOSE_FILE" logs --tail 100 "$agent" 2>&1 | \
        grep -iE 'error|exception|traceback|critical|HALT' | tail -5 || true)
    if [[ -n "$ERRORS" ]]; then
        FOUND_ERRORS=true
        printf "  ${RED}${BOLD}%s${RESET}\n" "$agent"
        echo "$ERRORS" | sed 's/^/    /'
        echo ""
    fi
done

$FOUND_ERRORS || printf "  ${GREEN}No errors found in recent logs${RESET}\n"
echo ""
