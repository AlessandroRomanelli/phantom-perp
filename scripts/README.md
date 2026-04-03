# Scripts

Operational scripts for running and monitoring phantom-perp locally.

## deploy.sh

Build all agent images and start the stack via `docker-compose.yml`.

```bash
# Full rebuild + up (all agents in parallel)
./scripts/deploy.sh

# Rebuild and redeploy only specific agents
./scripts/deploy.sh signals risk

# Build images without starting containers
./scripts/deploy.sh --build-only

# Restart containers without rebuilding
./scripts/deploy.sh --restart

# Check container status
./scripts/deploy.sh --status

# Tail logs for a specific agent
./scripts/deploy.sh --logs signals
```

## status.sh

Show a live status report of the running stack — containers, resources, Redis streams, pipeline flow, market data, and portfolio state.

```bash
# Full report
./scripts/status.sh

# Compact summary (containers + resource usage only)
./scripts/status.sh --short

# Include recent log snippets per agent
./scripts/status.sh --logs

# Show only agents with errors or restarts
./scripts/status.sh --errors

# Machine-readable JSON output
./scripts/status.sh --json
```

Report sections:
- **Host Resources** — uptime, CPU load, memory, swap, disk
- **Containers** — status and restart count for all services
- **Resource Usage** — per-container CPU, memory, network I/O
- **Redis Streams** — message count, consumer groups, last entry time for all 15 streams
- **Pipeline Flow** — messages per minute through each pipeline stage
- **Latest Market Data** — mark price, funding rate, volatility, orderbook imbalance
- **Portfolio State** — equity, margin, P&L, and open positions
- **Error Check** — scans recent logs for errors, exceptions, and critical alerts

## dashboard.py

Live terminal dashboard polling Redis Streams directly.

```bash
python scripts/dashboard.py
python scripts/dashboard.py --redis redis://localhost:6379
python scripts/dashboard.py --refresh 3
```

## query_obi_btc.py / query_oi_divergence_btc.py

Post-hoc P&L evidence scripts for monitoring strategy performance.

```bash
# OBI BTC: fills and P&L since tuner's fee-reduction adjustment
python scripts/query_obi_btc.py --since 2025-01-01

# OI Divergence BTC: fills and P&L
python scripts/query_oi_divergence_btc.py --since 2025-01-01
```

Requires `DATABASE_URL` set in environment.
