# Phantom Perp — Pre-v1.5 Performance Report

**Period:** 2026-03-31 14:47 UTC → 2026-04-01 07:09 UTC (~16.4 hours)  
**Architecture:** Dual-portfolio (PortfolioTarget A/B), paper mode  
**Instruments:** BTC-PERP, ETH-PERP, SOL-PERP  
**Generated:** 2026-04-01

---

## Executive Summary

Over ~16 hours of paper trading, the system generated **808 signals** from 6 active strategies, converted them into **453 orders**, and recorded **1,262 fills** across 3 instruments. Total notional volume was **$1,342,725** with **$193.92 in simulated fees**.

Key findings:
- **OI Divergence** was the most active signal producer (438 signals, 54%) but had the lowest signal-to-order conversion rate (46%) and accumulated large unhedged positions
- **Orderbook Imbalance** was the highest-fee strategy ($67.14, 35% of all fees) — it traded exclusively BTC-PERP with the largest notional per fill and was 100% Route A (autonomous)
- **Correlation** was the most broadly active across instruments and produced the second-highest fee load ($23.52)
- **Claude Market Analysis** had perfect signal-to-order conversion (100%) but very low volume (6 signals, 12 fills)
- **Momentum, Regime Trend, Funding Arb, Liquidation Cascade** generated **zero signals** in this period

---

## Signal Activity

| Strategy | Signals | Avg Conviction | Long | Short | Instruments | Signal→Order Rate |
|---|---:|---:|---:|---:|---:|---:|
| oi_divergence | 438 | 0.500 | 284 | 154 | 3 | 46.1% |
| correlation | 191 | 0.410 | 4 | 187 | 3 | 65.3% |
| orderbook_imbalance | 115 | 0.770 | 79 | 36 | 1 (BTC only) | 77.4% |
| mean_reversion | 55 | 0.522 | 10 | 45 | 1 (BTC only) | 50.9% |
| claude_market_analysis | 6 | 0.607 | 4 | 2 | 2 (ETH, SOL) | 100.0% |
| vwap | 3 | 0.590 | 0 | 3 | 3 | 66.7% |
| **momentum** | **0** | — | — | — | — | — |
| **regime_trend** | **0** | — | — | — | — | — |
| **contrarian_funding** | **0** | — | — | — | — | — |
| **liquidation_cascade** | **0** | — | — | — | — | — |

**Observations:**
- OI Divergence dominates signal volume but with mediocre conviction (0.50 avg) — many are low-confidence signals that barely clear the threshold
- Orderbook Imbalance has the highest avg conviction (0.77) and best conversion rate among high-volume strategies — it fires with confidence
- Correlation is heavily SHORT-biased (187 short vs 4 long) — likely reflecting a persistent cross-asset correlation regime
- 4 strategies produced zero signals over 16 hours — these are either misconfigured for current market conditions or have thresholds too tight

---

## Order Routing

| Route | Orders | Fills | Notional ($) | Fees ($) |
|---|---:|---:|---:|---:|
| Route A (Autonomous) | 121 | 242 | $702,462 | $87.82 |
| Route B (User-Confirmed) | 333 | 666 | $242,707 | $30.36 |

Route A handled 74% of notional volume despite fewer orders — driven by Orderbook Imbalance's large BTC-PERP positions routed exclusively to Route A. Route B received the bulk of OI Divergence and Correlation orders.

---

## Fee Breakdown by Strategy

| Strategy | Fills | Fees ($) | Notional ($) | Avg Fee/Fill | Maker % |
|---|---:|---:|---:|---:|---:|
| orderbook_imbalance | 178 | **$67.14** | $537,145 | $0.377 | 100% |
| correlation | 250 | $23.52 | $188,985 | $0.094 | 100% |
| oi_divergence | 406 | $18.80 | $149,078 | $0.046 | 100% |
| mean_reversion | 56 | $6.84 | $54,890 | $0.122 | 100% |
| claude_market_analysis | 12 | $1.42 | $11,474 | $0.118 | 100% |
| vwap | 4 | $0.44 | $3,489 | $0.110 | 100% |

All fills were maker (100%) — the paper simulator uses limit orders exclusively.

**Orphan fills** (stop-loss/take-profit without order_signal record): 357 fills, $75.81 in fees. These are protective orders placed by `stop_loss_manager` that don't join back to the original signal.

**Total fees:** $193.92 ($118.16 attributed to strategies + $75.81 from protective orders)

---

## Fee Breakdown by Strategy × Instrument

| Strategy | BTC-PERP | ETH-PERP | SOL-PERP |
|---|---:|---:|---:|
| orderbook_imbalance | **$67.14** | — | — |
| correlation | $11.94 | $10.48 | $1.10 |
| oi_divergence | $6.20 | $11.60 | $1.00 |
| mean_reversion | $6.84 | — | — |
| claude_market_analysis | — | $1.12 | $0.30 |
| vwap | $0.36 | — | $0.08 |

BTC-PERP generated **79% of all attributed fees** ($92.48 of $118.16), driven entirely by Orderbook Imbalance.

---

## Instrument Summary

| Instrument | Fills | Total Size | Notional ($) | Fees ($) | Avg Price | Buys | Sells |
|---|---:|---:|---:|---:|---:|---:|---:|
| BTC-PERP | 482 | 15.64 | $1,063,904 | $153.40 | $68,140 | 239 | 243 |
| ETH-PERP | 587 | 118.70 | $250,637 | $36.39 | $2,108 | 274 | 313 |
| SOL-PERP | 193 | 338.94 | $28,184 | $4.13 | $83.15 | 63 | 130 |

---

## Net Position Exposure (Open Risk)

| Strategy | Net Position (contracts) | Direction |
|---|---:|---|
| correlation | -145.58 | Heavily SHORT |
| oi_divergence | -75.13 | SHORT |
| vwap | -7.62 | SHORT |
| mean_reversion | -0.40 | ~Flat |
| orderbook_imbalance | +3.20 | Slightly LONG |
| claude_market_analysis | +10.60 | LONG |

**System is net short** across all strategies combined — most positions were opened but not closed within the measurement window. This is expected for a 16-hour sample where many trades haven't hit their stop-loss or take-profit levels yet.

---

## Strategy Effectiveness Ranking

### Tier 1 — Active & Effective
1. **Orderbook Imbalance** — Highest conviction (0.77), best conversion rate among volume strategies (77%), largest notional throughput. BTC-PERP only. Also the highest fee generator ($67.14).
2. **Correlation** — Broad instrument coverage, good conversion rate (65%), consistent signal production across hours. Heavily SHORT-biased — may need directional diversification.

### Tier 2 — Active but Noisy
3. **OI Divergence** — Most prolific signal producer but low conviction (0.50 avg) and lowest conversion rate (46%). Generates volume but signal quality is questionable.
4. **Mean Reversion** — Moderate activity, decent conviction (0.52), BTC-only. Small fee footprint.

### Tier 3 — Low Volume
5. **Claude Market Analysis** — Perfect conversion rate but fires very rarely (6 signals in 16 hours). By design — it's on a 4-hour cycle. Small but precise.
6. **VWAP** — Barely active (3 signals). May need threshold tuning or broader instrument enablement.

### Tier 4 — Silent
7. **Momentum** — Zero signals. Either thresholds too tight or market conditions didn't trigger.
8. **Regime Trend** — Zero signals.
9. **Contrarian Funding** — Zero signals. Funding rates likely within normal bounds.
10. **Liquidation Cascade** — Zero signals.

---

## Key Takeaways

1. **Fee concentration risk:** Orderbook Imbalance alone accounts for 35% of attributed fees and 40% of total notional. A single strategy driving that much volume on one instrument is a concentration concern.

2. **Silent strategies:** 4 of 10+ strategies produced zero signals. Before resetting, these should be reviewed — either thresholds need loosening or they need different market conditions to activate.

3. **Directional bias:** The system is heavily net short across almost all strategies. Correlation's 187-short-to-4-long ratio suggests it may be responding to a persistent regime rather than balanced signals.

4. **Protective order fees:** $75.81 in orphan fill fees (39% of total) from stop-loss/take-profit orders is significant. These can't be attributed to strategies because `stop_loss_manager` creates new orders without linking back to the original signal.

5. **Paper mode fidelity:** 100% maker fills across all strategies. Real execution would see a mix of maker/taker depending on order type and fill speed — actual fees would be higher.

---

*Report generated from PostgreSQL tables: signals (808 rows), order_signals (453 rows), fills (1,262 rows). Redis: 24 keys across 14 streams and 10 cache entries.*
