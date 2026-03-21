# Scripts

Operational scripts for deploying, monitoring, and inspecting the phantom-perp system.

## deploy.sh

Build, transfer, and deploy Docker images to the Oracle Cloud production server.

Images are cross-compiled for `linux/amd64` on the local machine (Apple Silicon), saved to a tarball, transferred via SCP, and loaded on the remote. Containers are then recreated via `docker-compose.prod.yml`.

```bash
# Full build + deploy (all 8 agents)
./scripts/deploy.sh

# Rebuild and deploy only specific agents
./scripts/deploy.sh risk execution

# Build images locally without deploying
./scripts/deploy.sh --build-only

# Deploy an existing tarball (skip build)
./scripts/deploy.sh --deploy-only

# Restart containers without rebuilding
./scripts/deploy.sh --restart

# Check remote container status
./scripts/deploy.sh --status

# Tail remote logs for a specific agent
./scripts/deploy.sh --logs risk
```

## status.sh

Generate a status report of the deployed system on Oracle Cloud. Connects via SSH and collects host metrics, container health, Redis stream state, market data, and portfolio snapshots.

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
- **Containers** — status, uptime, restart count for all 10 containers
- **Resource Usage** — per-container CPU, memory, network I/O
- **Redis Streams** — message count, consumer groups, last entry time for all 15 streams
- **Pipeline Flow** — messages per minute through each stage of the pipeline
- **Latest Market Data** — ETH-PERP mark/index/last price, spread, funding rate, volatility
- **Portfolio State** — equity, margin, P&L, and positions for Portfolio A and B
- **Error Check** — scans recent logs for errors, exceptions, and critical alerts

## dashboard.py

Live terminal dashboard that polls Redis Streams and displays real-time pipeline state. Requires a direct Redis connection (run locally with port-forwarding, or on the server).

```bash
# Default: connects to redis://localhost:6379
python scripts/dashboard.py

# Custom Redis URL
python scripts/dashboard.py --redis redis://redis:6379

# Custom refresh interval (seconds)
python scripts/dashboard.py --refresh 3
```
