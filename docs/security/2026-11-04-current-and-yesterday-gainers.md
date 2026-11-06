# Binance gainers versus Atlas candidate evidence — November 4, 2026

Read-only research. No screening rules, services, deployments, sessions or collection anchors changed.
The engineering-research skill was used to retain primary data, calculations and uncertainty.

## Answer

The six long candidates in the retained local November 4 08:01 UTC scan do not overlap either
the current observed top ten or November 3 calendar-day top ten. This is a same-snapshot
comparison, **not evidence that yesterday's scanner missed them**. We have no retained November 3
published candidate history in the inspected local files. Early detection and alert lead time
therefore remain unverified.

The public production API also has a publication/freshness discrepancy: its status endpoint reports
a November 4 08:01 run, while its live candidate endpoint returns an October 24 timestamp and zero
opportunities. A second read with a unique query and `Cache-Control: no-cache` returned the same
old payload; the server responded with `cache-control: no-store`. This verifies the discrepancy,
not its cause. A scan-start timestamp alone does not prove scan success or fresh publication.
No production VM SSH, rescan, refresh, restart or repair was performed.

## Markets and clocks

Universe: 522 contracts in the returned Binance exchange metadata with status TRADING,
quote asset USDT and contract type PERPETUAL. This is a current-universe retrospective
comparison, not every historical Binance listing, spot market, USDC contract or TradFi perpetual.
Atlas's local scan evaluated only 320 symbols. Its retained output does not identify all evaluated
symbols, so we cannot separate universe exclusion from gate rejection for these winners.

Current ticker snapshot: November 4 approximately **08:37 UTC / 09:37 Rome**. Returns are Binance's
rolling 24-hour price changes. One contract, BANANAS31USDT, had a ticker roughly 74 seconds older
than the newest row and is treated as unknown under a 60-second freshness rule. The table gives
observed ranks among the other 521; true rank bounds are r to r+1. Thus the first nine names have
certified top-ten membership under this bound; ZEC's tenth-place boundary remains uncertain.

Yesterday: November 3 **00:00–24:00 Europe/Rome**, equivalent to November 2 23:00–November 3
23:00 UTC. Every one of the 522 contracts supplied exactly 24 consecutive closed hourly candles.
Return = (last close / first open - 1) × 100. This is a reconstructed calendar-day ranking,
**not yesterday's archived rolling-24h leaderboard** or its first-entry times.

| Observed rank | Current rolling-24h winner | Change | Yesterday's calendar-day winner | Change |
|---|---|---:|---|---:|
| 1 | CLANKER | +35.97% | AGT | +33.69% |
| 2 | BIRB | +25.21% | HOT | +29.42% |
| 3 | BMT | +24.38% | CLANKER | +25.35% |
| 4 | IDOL | +23.91% | BMT | +23.31% |
| 5 | HOT | +23.49% | IDOL | +20.20% |
| 6 | AIO | +22.72% | ICNT | +19.23% |
| 7 | FLUID | +18.96% | AIO | +15.65% |
| 8 | USELESS | +17.54% | RUNE | +15.32% |
| 9 | VVV | +17.47% | FLUID | +14.53% |
| 10 | ZEC | +14.76% | KMNO | +12.73% |

All symbols above denote USDT perpetual contracts. Six names overlap the two lists; this is
descriptive persistence, not a measured predictive success rate.

## What the candidate evidence establishes

Local scan: November 4 08:01:18 UTC, 320 evaluated, eight published items: six longs and two shorts.

| Local long | Approximate current observed gain rank | Price change from scan price to market snapshot |
|---|---:|---:|
| MEGA | 133 | +0.55% |
| ZRO | 14 | +0.48% |
| STX | 124 | +0.39% |
| HOME | 197 | +0.26% |
| TAG | 514 | −1.00% |
| EDEN | 311 | −0.78% |

Ranks have up to one place of uncertainty from the stale ticker. Shorts TA and AIOT are not long
discovery successes. AIO and AIOT are different contracts and must not be matched by substring.
The roughly 36-minute price changes are descriptive marks from the recorded scan price,
not executed trades, actionable entry returns, or 24h/48h outcomes.

Local `data/screener_runs.jsonl` contains 19 entries dated October 13–19. The attached research
worktree's local data also does not supply November 3 scan history. The previously verified
research cohort had fixed endpoints October 31 / November 1 and cannot establish November 3/4
discovery. A current recommendation cannot retrospectively prove an earlier alert.

The current local portal implementation persists its latest payload but does not append
`screener_runs.jsonl`; the separate CLI cron does append that file. Portal scans call the runtime
recording path, but whether an immutable archive was enabled and successfully retained for a
specific historical production scan still needs its actual receipt and archive. This source
observation does not prove which revision production runs.

## Behavior before the strongest hour

Event definition: choose the largest open-to-close positive hourly return **within each study
window**, then inspect the preceding six closed hours. This is retrospective event selection,
not a deployable prediction rule or the time a token first reached top ten.
Quote-volume ratio = mean hourly quote volume in those six hours / mean in the preceding 18.
OI change uses `sumOpenInterest` units at the event-open timestamp versus six hours earlier,
not dollar OI inflated by price appreciation. All 20 event histories have 24 preceding contiguous
closed bars and exact OI endpoint timestamps. Historical timestamps do not establish real-time
API arrival latency. No causal catalyst or informed buyer identity is inferred.

Yesterday's ten winners:

| Token | Strongest hour starts UTC Nov 3 | Hour return | Prior 6h price | Prior volume ratio | Prior 6h OI |
|---|---|---:|---:|---:|---:|
| AGT | 12:00 | +43.93% | +14.95% | 1.63× | +2.85% |
| HOT | 11:00 | +16.70% | +8.26% | 0.79× | +14.78% |
| CLANKER | 19:00 | +10.80% | +0.09% | 0.77× | −1.64% |
| BMT | 12:00 | +19.21% | −3.53% | 3.71× | +9.97% |
| IDOL | 00:00 | +8.16% | +29.46% | 0.68× | −8.35% |
| ICNT | 11:00 | +7.37% | +11.18% | 7.27× | +40.15% |
| AIO | 15:00 | +12.42% | +7.19% | 1.20× | +1.46% |
| RUNE | 14:00 | +5.71% | +1.35% | 1.76× | −1.73% |
| FLUID | 13:00 | +2.39% | +5.14% | 0.98× | +4.98% |
| KMNO | 14:00 | +4.38% | +3.34% | 1.29× | +0.88% |

Additional current-list names, using the closed-hour proxy window November 3 08:00–November 4
08:00 UTC (37 minutes earlier than the live rolling window):

| Token | Strongest hour starts UTC | Hour return | Prior 6h price | Prior volume ratio | Prior 6h OI |
|---|---|---:|---:|---:|---:|
| BIRB | Nov 4 05:00 | +3.17% | +5.54% | 4.66× | +14.72% |
| USELESS | Nov 4 04:00 | +7.72% | −3.25% | 1.38× | +1.23% |
| VVV | Nov 3 11:00 | +3.85% | −4.09% | 1.91× | −0.52% |
| ZEC | Nov 4 04:00 | +2.58% | −3.43% | 1.11× | +0.58% |

The machine-readable analysis contains both complete ten-token event sets. For example IDOL's
strongest hour differs between windows; do not mix the two measurements.

## Implications and next work

1. **Recover reliable candidate history and resolve publication freshness first.** Retain scan IDs,
   source versions, universe selection/evaluation receipts, rejection stages, published recommendations
   and publication timestamps. Without those, a missed-opportunity explanation would be invented.
2. **BMT is a useful divergence hypothesis:** volume and OI expanded while six-hour price declined.
   This merits testing a research watch state before an archetype-qualified long. ICNT and BIRB
   show volume/OI expansion too, but already had meaningful price advances. A watch is not an entry.
3. **Do not require a universal volume-plus-OI template:** CLANKER had neither increasing in this
   selected pre-event window; IDOL was already extended. Sudden moves and continuation setups
   have different observables. These examples do not establish that CLANKER was predictable.
4. **Maintain the earlier timeframe-contract audit priority** before changing thresholds. Previously
   verified hourly-derived values labelled 4h in the traced path need deliberate resolution;
   this new study does not prove those discrepancies caused any specific omission.
5. Test frozen research hypotheses across the full universe with non-winners, explicit false-positive
   rates, alert lead time, drawdown and liquidity, then use a new prospective holdout. Do not tune
   gates on these winners or promise top-gainer capture. Four-hour scan cadence can also miss
   shorter-lived transitions; faster shadow monitoring is a hypothesis, not an approved deployment.

## Evidence, reproduction and verification

Raw API results and candidate responses are under `docs/security/evidence/2026-11-0[34]-*.json`.
`2026-11-04-market-input.json` retains the ticker and exchange-info responses.
`2026-11-03-rome-hourly.json` retains all 522 historical requests, not only winners.
The two `pre-move-data` files retain 15 symbols' hourly candles and OI histories.
The local scan is copied separately; production responses and HTTP headers are retained separately.

Reproduce offline: `python3 scripts/research_daily_gainers_review.py`.
Output: `docs/security/evidence/2026-11-04-gainers-analysis.json`, including input SHA256 hashes.
Syntax compilation passed. Independent Decimal calculation reproduced all ten calendar-day
rankings and returns; all 522 OHLC/time sequences, all 20 pre-event histories/OI endpoints and
the retained input hashes were checked. This is research-data verification, not a full application
test suite, deployment verification or security audit.

Official endpoint semantics: [Binance USD-M market-data documentation](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data).
Public deployment reads: [live candidates](http://34.141.95.32:8000/api/screener/live),
[cron status](http://34.141.95.32:8000/api/screener/cron/status).
