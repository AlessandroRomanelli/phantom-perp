# Codebase Concerns

**Analysis Date:** 2026-03-21

## Tech Debt

### 1. Rate Limiter Accuracy and Blocking Behavior

**Issue:** The `RateLimiter` in `libs/coinbase/rate_limiter.py` (lines 14-86) uses a soft token-bucket approach with `asyncio.sleep()` in a busy-wait loop (lines 59-63). This has several problems:

- **Thread safety**: The `_refill()` method is not atomic; multiple concurrent requests can read stale `_tokens` values before acquiring the lock
- **Busy-wait inefficiency**: When tokens are depleted, the code sleeps in a tight loop within the lock, blocking all other acquire attempts
- **Header-based updates not idempotent**: Calling `update_from_headers()` with the same remaining count multiple times overwrites the internal state, potentially losing tokens

**Files:** `libs/coinbase/rate_limiter.py`

**Impact:**
- Risk agent or execution agent may be temporarily blocked waiting for rate limit tokens
- During high-concurrency scenarios (multiple agents acquiring tokens), fairness is not guaranteed
- If Coinbase rate limit headers are delayed or incorrect, token count can become inconsistent

**Fix approach:**
- Refactor to use `asyncio.Event` or `asyncio.Condition` for signaling instead of polling
- Make `update_from_headers()` use the maximum of current and header values to prevent loss
- Add tests for concurrent token acquisition and proper fairness

### 2. String-Based Type Serialization in Redis Streams

**Issue:** The execution, reconciliation, and monitoring agents serialize boolean and enum values as strings, then deserialize by comparing with string literals:

- `libs/execution/main.py` lines 90, 112, 128, 148
- `libs/reconciliation/main.py` lines 71, 119-120
- `libs/monitoring/main.py` lines 119-120

Example (execution/main.py:90):
```python
reduce_only=payload["reduce_only"] == "True" if isinstance(payload["reduce_only"], str) else bool(payload["reduce_only"]),
```

**Files:**
- `agents/execution/main.py`
- `agents/reconciliation/main.py`
- `agents/monitoring/main.py`

**Impact:**
- Brittle deserialization: if serialization format ever changes, deserialization silently produces wrong values
- No validation: malformed values like `"true"` (lowercase) will silently deserialize to `False`
- High cognitive load: scattered string conversion logic instead of centralized serialization layer

**Fix approach:**
- Create a `SerializationRegistry` class that handles all type conversions consistently
- Use JSON-native types (booleans → `true`/`false`, enums → string values)
- Validate deserialized values and log warnings on type mismatches
- Apply consistently across all agent serialization functions

### 3. Portfolio State Snapshot Is Incomplete (Missing Open Positions)

**Issue:** The `PortfolioSnapshot` model includes a `positions: list[PerpPosition]` field, but the reconciliation agent (`agents/reconciliation/main.py:77-91`) serializes only a `position_count` and does not serialize the full position data:

```python
def portfolio_snapshot_to_dict(snap: PortfolioSnapshot) -> dict[str, Any]:
    return {
        ...
        "position_count": len(snap.open_positions),
    }
```

When deserialized (line 103), the positions list is always empty: `positions=[]`

**Files:** `agents/reconciliation/main.py` (lines 77-108)

**Impact:**
- Monitoring, alpha, and risk agents cannot access position details from the stream
- Any downstream analysis that needs position leverage, unrealized P&L, or liquidation prices must query Coinbase directly again
- This creates redundant API calls and race conditions (position state may change between snapshot and query)

**Fix approach:**
- Add `serialize_positions()` helper to convert `list[PerpPosition]` to a JSON-serializable format (omit large nested objects)
- Include position list in `portfolio_snapshot_to_dict()`
- Update deserialization to reconstruct position objects (or at least include key fields as JSON)
- Store full position snapshots in TimescaleDB for historical analysis

### 4. Confirmation Agent State Machine Has No Persistence

**Issue:** The `OrderStateMachine` in `agents/confirmation/state_machine.py` (195 lines) holds all order state in memory with no persistence. If the confirmation bot restarts:

- Pending orders are lost
- User cannot resume approvals/rejections
- No audit trail of state transitions

**Files:** `agents/confirmation/state_machine.py`

**Impact:**
- Live trading risk: if bot crashes with pending Portfolio B orders, they orphan in the state machine and may not be cleaned up properly
- User friction: user must re-approve or re-send the order after a restart

**Fix approach:**
- Store order state transitions in PostgreSQL (`agents/confirmation/schema.py` + `RelationalStore`)
- Add a `load_pending_orders()` method that queries the DB on bot startup
- Log all state transitions to the DB before updating in-memory state (write-ahead pattern)
- Add a recovery mechanism to resume stale orders (with price refresh) after restart

### 5. Ingestion Agent Doesn't Validate Data Freshness Before Building Snapshots

**Issue:** In `agents/ingestion/normalizer.py`, the `build_snapshot()` function constructs a `MarketSnapshot` from the `IngestionState` without checking if the underlying data fields (mark_price, spread, funding_rate, etc.) are stale:

- No timestamp validation against data age
- If one data source is temporarily unavailable, the snapshot includes stale data without flagging it
- Market snapshot could have mark_price from 10 minutes ago and funding_rate from 5 minutes ago mixed together

**Files:** `agents/ingestion/normalizer.py` (139 lines)

**Impact:**
- Risk agent may use inconsistent data for position sizing and liquidation calculations
- Execution agent may place orders at stale prices
- Strategies may respond to outdated signals

**Fix approach:**
- Add max-age checks for each data field (mark_price, spread, funding_rate, candles)
- If any field exceeds its max-age, either omit it or flag the snapshot as "degraded quality"
- Log warning when data quality is degraded
- Risk agent should reject trades when using degraded market data

## Known Bugs

### 1. Paper Simulator Doesn't Account for Funding Rate in Margin Calculations

**Issue:** In `agents/reconciliation/paper_simulator.py` (529 lines), the paper trading simulator fills orders and updates equity, but does not simulate hourly funding rate settlements:

- Position size changes and margin is updated
- But no funding payment is deducted/added each hour
- After 24 hours of trading, funding impact is completely missing from paper performance

**Files:** `agents/reconciliation/paper_simulator.py`

**Symptoms:**
- Paper trading results show higher returns than they should (funding costs are ignored)
- Strategy performance estimates are optimistic

**Trigger:**
- Run paper trading for > 4 hours (multiple funding cycles) and compare to live results

**Workaround:**
- Manually deduct estimated funding from paper P&L; use monitoring agent's `funding_report.py` to compute expected cost
- For short horizon trades (< 2h), funding impact is negligible

**Fix approach:**
- Add a `_simulate_funding_settlement()` method that runs every hour
- Calculate funding payment based on position size and current rate
- Deduct from paper equity and log as funding event

### 2. Execution Agent Does Not Handle Partial Fills Correctly in Paper Mode

**Issue:** In `agents/execution/main.py` (777 lines), the paper simulator immediately fills orders at market price. But if a large order size is split into multiple fills on Coinbase:

- Paper mode simulates a single fill at the market price at order time
- Live mode may get multiple fills over time at different prices (e.g., 0.5 ETH at $2230, 0.5 ETH at $2231)
- The average execution price differs between paper and live

**Files:** `agents/execution/main.py`

**Symptoms:**
- Paper backtest shows fill price of $2230.50, but live execution has average price of $2231.25
- Fee calculation is optimistic in paper (doesn't account for slippage on large orders)

**Trigger:**
- Open large positions (> 2 ETH) and check actual fills against paper fills

**Workaround:**
- Reduce maximum order size to sizes that typically fill as a single order
- Manually compare paper vs live fill prices in monitoring dashboards

**Fix approach:**
- Modify paper simulator to split large orders into smaller fills (e.g., 0.1 ETH chunks)
- Use realistic order book depth from ingestion data to estimate fill prices
- Add slippage modeling to paper fills

### 3. WebSocket Reconnection May Drop Messages During Reconnect Window

**Issue:** In `libs/coinbase/ws_client.py` (193 lines), the `connect()` method re-subscribes to channels but does not handle messages that arrive during the reconnect:

```python
async def connect(self) -> None:
    self._ws = await websockets.asyncio.client.connect(...)
    for sub in self._subscriptions:
        await self._send(sub)
```

Between the old connection closing and the new one receiving the re-subscription ack, market data updates are missed.

**Files:** `libs/coinbase/ws_client.py`

**Symptoms:**
- During a reconnect event, the ingestion agent may miss 1-5 seconds of market data
- Gap in order book snapshots, causing missed signals

**Trigger:**
- Force a network disconnect or let the connection timeout naturally
- Monitor ingestion logs for timestamp gaps in market snapshots

**Workaround:**
- Use REST API to fetch the latest market state immediately after reconnect
- Risk agent already enforces a stale-data halt (30s), so 5s gaps are tolerated

**Fix approach:**
- Fetch the latest orderbook and trades via REST immediately after resubscribing to WS
- Fill the gap with REST data before resuming normal WS-only operation
- Log gap-fill events for monitoring

## Security Considerations

### 1. API Key Exposure in Configuration

**Risk:** API keys are configured via environment variables (`COINBASE_INTX_API_KEY_A`, `COINBASE_INTX_API_SECRET_A`, etc.). If environment variables are logged, dumped, or exposed:

- Full portfolio A and B API keys are compromised
- Attacker can trade, transfer funds, and liquidate positions
- No API key rotation mechanism (requires system restart)

**Files:**
- `libs/common/config.py` (lines 24-29)
- `libs/coinbase/auth.py`

**Current mitigation:**
- Environment variables are never logged (good)
- API keys are not stored in code or config files (good)
- But no env var masking in error messages if an exception includes the key

**Recommendations:**
- Add explicit checks to strip API keys from exception messages and logs (use regex to detect patterns)
- Implement API key rotation mechanism: support reading from a rotating set and graceful handoff
- Use temporary credentials (Coinbase session tokens) if available
- Add logging of all API calls with timestamp, endpoint, and portfolio target (for audit)

### 2. No Authentication Between Internal Agents

**Risk:** Redis Streams messages between agents have no signature or authentication. A compromised agent or external process with Redis access can:

- Inject fake trade signals (signal agent)
- Approve orders without user confirmation (confirmation agent)
- Publish false position updates (reconciliation agent)

**Files:** `libs/messaging/redis_streams.py`

**Current mitigation:**
- Redis connection string includes host/port but no authentication mechanism
- System is designed for internal use only (not exposed to external networks)

**Recommendations:**
- Add HMAC signatures to all critical message types (StandardSignal, ProposedOrder, ApprovedOrder)
- Use Redis AUTH if running Redis server with a password
- Encrypt sensitive fields (order prices, sizes) in Redis payloads
- Rotate the shared signing key periodically

### 3. No Rate Limiting on /start Command in Telegram Bot

**Risk:** The Telegram bot's `/start` handler (`agents/confirmation/bot.py:~150-170`) registers the chat ID on first message. An attacker with the bot token can send `/start` multiple times, potentially registering multiple chat IDs or triggering rate-limit issues.

**Files:** `agents/confirmation/bot.py`

**Current mitigation:**
- Chat ID is only set once (after that, messages are sent to the registered chat)
- But no logging of the `/start` event for audit

**Recommendations:**
- Log all `/start` attempts with timestamp and chat ID
- Add a simple rate limiter: only allow chat ID registration once per minute
- Alert on suspicious `/start` patterns (multiple attempts in short succession)

## Performance Bottlenecks

### 1. Ingestion Agent Publishes Market Snapshots on Every WebSocket Update

**Issue:** In `agents/ingestion/main.py:97-117`, the `on_ws_update()` callback publishes a new `MarketSnapshot` to Redis Streams on **every single market data event**:

```python
async def on_ws_update(instrument_id: str) -> None:
    state = states[instrument_id]
    snapshot = build_snapshot(state, instrument_id)
    await publisher.publish(Channel.MARKET_SNAPSHOTS, payload)
    snapshot_counts[instrument_id] += 1
```

Coinbase's WebSocket can send 50+ updates per second per instrument. This means:
- 50+ entries per second in `stream:market_snapshots`
- 50+ deserialization operations in the signals agent
- Redis memory usage explodes (default max stream length is 100,000 entries)

**Files:** `agents/ingestion/main.py` (lines 97-117)

**Impact:**
- Redis memory usage grows quickly during volatile market periods
- Signals agent falls behind consuming messages
- Latency from snapshot publication to signal generation increases
- StreamMaxLen trimming (`approximate=True`) may drop recent messages unexpectedly

**Cause:** The intent was to have near-realtime market data for signal generation. But publishing every tick is wasteful.

**Improvement path:**
- Add a debounce: only publish a snapshot if:
  - Mark price changed by > 0.1% (or configurable threshold)
  - Spread widened/narrowed by > 5 bps
  - Volume profile changed significantly
  - > 1 second has elapsed since last snapshot
- Keep high-frequency data (all ticks) in the `IngestionState` for local signal strategies
- Publish sampled snapshots for multi-agent consumption

### 2. Portfolio State Polling Has No Adaptive Interval

**Issue:** In `agents/reconciliation/main.py:52`, the reconciliation agent polls each Coinbase portfolio every 30 seconds unconditionally:

```python
POLL_INTERVAL = 30
```

During low-activity periods (e.g., funding rate is 0.001%, no open positions), polling every 30 seconds wastes API quota and CPU.

**Files:** `agents/reconciliation/main.py` (line 52)

**Impact:**
- Each portfolio poll makes 2-3 REST calls (positions, portfolio details, possibly fills)
- 2 portfolios × 2 calls × 2880 polls/day = ~11,500 API calls just for polling
- With 30 calls/sec rate limit, this uses ~6% of rate limit budget permanently

**Improvement path:**
- Implement adaptive polling:
  - Fast poll (5-10s) when positions are open or recent fills occurred
  - Slow poll (60-120s) when idle
  - Use WebSocket user-data feed for position updates (if available) instead of REST polling
- Track API call efficiency and log "cost per snapshot"

### 3. Risk Agent Queries Coinbase for Fresh Equity on Every Trade

**Issue:** The `PortfolioStateFetcher` in `agents/risk/portfolio_state_fetcher.py` queries Coinbase for current equity and margin every time a risk check runs. For high-frequency trading:

- 1 trade signal per second → 60 Coinbase API calls per minute just for equity checks
- Adds 100-200ms latency per risk check (network round-trip)

**Files:** `agents/risk/portfolio_state_fetcher.py`

**Impact:**
- Risk agent is the bottleneck during high-frequency periods
- Round-trip latency delays order placement
- Risk checks may reject orders that would have passed 200ms earlier

**Improvement path:**
- Use cached portfolio state from `stream:portfolio_state:a/b` instead of direct Coinbase queries
- Reconciliation agent publishes portfolio state every 30s (high frequency)
- Risk agent reads the latest snapshot from Redis (nanosecond access)
- Fall back to live Coinbase query only if cached state is > 30s old

## Scaling Limits

### 1. Single Redis Instance Is Not Highly Available

**Risk:** The system has a single Redis instance (local Docker or remote single-node). If it crashes:

- All message queues are lost (market snapshots, signals, approved orders)
- All trading halts until Redis restarts
- No persistence of in-flight orders from confirmation agent

**Files:** `libs/messaging/redis_streams.py`

**Current capacity:**
- Default Redis memory: 512MB - 2GB (depends on deployment)
- Stream entries: 100,000 max per stream (configurable)
- Throughput: ~100,000 ops/sec on a single Redis instance

**Limit:** At 50 market snapshots/second × 10 instruments = 500 entries/sec, the system can run for 200 seconds before hitting the stream length limit.

**Scaling path:**
- Migrate to Redis Cluster (3+ nodes) for redundancy
- Implement Redis Sentinel for automatic failover
- Use a more durable message broker (Kafka, RabbitMQ) if trading volume increases

### 2. PostgreSQL Relational Store Has No Sharding

**Risk:** The relational store (`libs/storage/relational.py`) uses a single PostgreSQL instance. As trading volume increases:

- Order history grows rapidly (100+ trades/day × 365 = 36,500 orders/year)
- Fill history also grows (avg 2-3 fills per order)
- Query performance degrades (full table scans on large history)

**Files:** `libs/storage/relational.py`

**Current capacity:**
- Default PostgreSQL RAM: 256MB - 1GB
- Can hold ~1M rows comfortably
- After 1 year of trading: 36K orders + 100K fills = manageable

**Limit:** With aggressive trading (1,000+ orders/day), the database fills up in ~1 month. Backups and recovery take longer.

**Scaling path:**
- Partition order and fill tables by date (monthly partitions)
- Archive old data (> 6 months) to cold storage
- Use TimescaleDB for time-series optimization (already used for candles, funding)

### 3. Single Ingestion Agent Cannot Scale to Many Instruments

**Risk:** The ingestion agent runs candle pollers, funding rate pollers, and WS subscriptions for all configured instruments. If expanding to 10+ instruments:

- Each instrument has 5 timeframes (1m, 5m, 15m, 1h, 6h) → 50 REST pollers
- Plus funding rate pollers → 60 concurrent tasks
- WebSocket connection overhead increases

**Files:** `agents/ingestion/main.py`

**Current capacity:** 2-3 instruments comfortably. Beyond that, API rate limits and task scheduling overhead become noticeable.

**Scaling path:**
- Split ingestion into multiple agent instances, each handling a subset of instruments
- Use a shared Redis cache for cross-instrument data (e.g., ETH/BTC correlation)
- Or migrate to a data warehouse that provides normalized market data (e.g., Parquet files from a data provider)

## Fragile Areas

### 1. Portfolio Routing Logic Is Brittle

**Issue:** The portfolio router in `libs/portfolio/router.py` makes routing decisions based on hardcoded rules and config:

```yaml
portfolio:
  routing:
    rules:
      - condition: "time_horizon < 2h"
        target: "A"
      - condition: "source in [FUNDING_ARB, ORDERBOOK]"
        target: "A"
      - condition: "default"
        target: "B"
```

If a strategy is added or a condition needs adjustment, the config must be updated and the system redeployed. The rules are:
- Not versioned
- Not A/B testable
- Cannot be updated without downtime

**Files:** `libs/portfolio/router.py`

**Safe modification:**
- Add a `Router.evaluate()` method that logs the decision path before committing
- Store routing decisions in a database table (for audit)
- Add A/B testing capability: route some signals via the alternate portfolio to test hypothesis
- Version the routing rules and allow hot-reloading from Redis

### 2. Risk Limit Enforcement Has No Audit Trail

**Issue:** The risk agent approves or rejects orders, but rejections are not logged persistently. If a risk rejection is suspected to be wrong, there is no way to:

- Query why an order was rejected
- Review the equity/margin state at the time of rejection
- Replay the risk check with different parameters

**Files:** `agents/risk/main.py`

**Safe modification:**
- Store all risk evaluations (not just rejections) in PostgreSQL
- Include: order ID, idea, portfolio state, all risk check results, approval/rejection decision
- Add a query tool to investigate specific rejections

### 3. Confirmation State Machine Has No Timeout Recovery

**Issue:** If a user approves an order but the execution agent never receives it (network glitch), the order remains in "CONFIRMED" state forever. The system doesn't retry or escalate.

**Files:** `agents/confirmation/state_machine.py`

**Safe modification:**
- Add a timeout monitor: if order is in "CONFIRMED" state for > 5 minutes, publish an alert
- Implement automatic retry: re-send the confirmed order to execution agent
- Allow user to manually retry via Telegram command `/retry <order_id>`

## Test Coverage Gaps

### 1. No Integration Tests for Portfolio Isolation

**Issue:** The critical safety property — orders for Portfolio A must route through Portfolio A's API client and orders for Portfolio B through Portfolio B's client — is not explicitly tested in integration tests.

**Files:**
- Tests exist at `tests/unit/test_portfolio_registry.py` (enum tests only)
- Missing: `tests/integration/test_portfolio_isolation.py`

**What's not tested:**
- Risk agent produces orders with `portfolio_target=A` or `portfolio_target=B`
- Execution agent routes these to the correct `CoinbaseClientPool.get_client(target)`
- No cross-contamination (A order sent via B client)

**Risk:** A future refactor could silently break portfolio isolation without test failure.

**Priority:** HIGH - this is a safety-critical test

**Fix approach:**
- Create `tests/integration/test_portfolio_isolation.py`
- Mock both Coinbase clients
- Verify that all A orders call the Portfolio A client and B orders call the Portfolio B client
- Add to pre-deployment CI/CD checks

### 2. No Tests for Confirmation Agent Timeout

**Issue:** The confirmation bot has a TTL mechanism for pending orders, but no test validates that orders expire correctly.

**Files:** `agents/confirmation/main.py` (no timeout test)

**What's not tested:**
- Order stays in PENDING_CONFIRMATION state for N seconds
- After TTL, order auto-expires and is moved to EXPIRED status
- User receives an expiry notification

**Risk:** Timeout logic could break silently; stale orders remain pending indefinitely.

**Fix approach:**
- Create `tests/unit/test_confirmation_timeout.py`
- Use `freezegun` to mock time
- Verify expiry behavior and notification

### 3. No Tests for No-Transfer Constraint

**Issue:** CLAUDE.md explicitly states: "The system never transfers funds between Portfolio A and Portfolio B." This is a critical architectural constraint, but there is no test that validates the code path does not exist.

**Files:**
- `libs/coinbase/rest_client.py` (must NOT call the `/api/v1/transfers/portfolios` endpoint)
- `agents/reconciliation/main.py`
- `agents/risk/main.py`

**What's not tested:**
- Static: grep codebase for "transfers/portfolios" → should find zero matches
- Dynamic: mock Coinbase API and assert that the transfer endpoint is never called during a full pipeline run

**Risk:** A future PR could accidentally add a fund-sweep feature that violates the architecture.

**Priority:** HIGH - architectural constraint

**Fix approach:**
- Create `tests/integration/test_no_cross_transfer.py`
- Part 1: Static analysis — grep all source files for transfer endpoint patterns
- Part 2: Dynamic — run a full paper trading cycle and assert the transfer endpoint is never mocked-called

---

*Concerns audit: 2026-03-21*
