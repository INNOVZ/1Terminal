# Early-momentum v7 preregistration

Date registered: 2026-08-17

Status: rejected for promotion; retained as a reproducible research control. No performance result
had been observed when the contract below was written.

## Problem statement

`layered_tsmom_v6` waits for a daily EMA200 regime, agreement across 30d/90d/180d returns, and a
30-bar 4h breakout. That is coherent trend following, but it can identify a mature move rather than
an emerging one. Its doubled-cost BTC and SOL results were negative, with most trades ultimately
stopping out.

No indicator can reliably know in advance that a token will rise or fall. V7 instead tests whether
an observable sequence—short-horizon momentum, abnormal participation, breakout, held retest, and
resumption—has positive forward expectancy after costs.

## Primary hypothesis

An hourly trade is eligible only when all of the following are known from closed bars:

1. The 4h close is on the directional side of EMA50. This is one medium-term regime measurement,
   not an EMA20/EMA50/EMA200 conjunction.
2. At least two of 6h, 24h, and 7d returns agree with the proposed direction.
3. During the previous eight completed hours, price closed beyond the preceding 20h channel on at
   least 1.25x its preceding 20h average volume.
4. A later closed bar retested the breakout level within 0.50 event ATR without any retest close
   failing through the level by more than 0.25 event ATR.
5. The current closed bar resumes in the trade direction through both the breakout level and the
   previous bar's extreme.
6. ATR is positive, the executable quote remains inside the existing anti-chase boundary, and any
   known opposing structural level leaves at least the existing 1.5R minimum room.

Long and short formulas are exact mirrors. Spot remains long-only. Futures shorts require separate
explicit authority and default to disabled.

## Ranking—not vetoes

The following may change priority but cannot create or veto a v7 setup:

- 1h/4h EMA20/50 and EMA200 observations;
- MACD histogram side/cross;
- RSI regime, exhaustion, and confirmed divergence;
- ADX level/direction;
- current relative volume and volatility percentile;
- confirmed market structure;
- funding, basis, open interest, taker imbalance, and book depth.

Derivatives features remain observational because the current candle replay cannot reconstruct
historical point-in-time derivatives data. Using them in runtime eligibility before equivalent
historical replay would make the live and backtest strategies different.

## Fixed defaults

- Breakout channel: 20 closed 1h bars.
- Maximum breakout age: 8 closed 1h bars.
- Breakout relative volume: 1.25x.
- Retest touch buffer: 0.50 breakout-event ATR.
- Retest hold buffer: 0.25 breakout-event ATR.
- Momentum horizons: 6h, 24h, 168h; two must align.
- Entry cadence: hourly discovery with the existing rolling execution cooldown.
- Exit, anti-chase, position sizing, ≥1.5R bracket rule, costs, leverage, drawdown, correlation,
  reconciliation, and kill-switch policies: unchanged for the entry-isolation experiment.

These values will not be tuned after viewing the first registered replay. Any later change must use
a new strategy version and untouched time period.

## Promotion test

The initial diagnostic will report BTC, ETH, SOL, DOGE, and AVAX separately with both Futures
directions enabled, ordinary and doubled costs, and at least two chronological one-year windows.
Promotion requires positive expectancy in R and profit factor above one in every declared holdout,
acceptable drawdown, no single-symbol dependence, and a portfolio replay with actual ranking and
correlation limits. A high win rate is not a substitute for positive expectancy.

## Research basis and caveats

- Cryptocurrency returns have documented momentum factors, but the evidence depends on universe,
  horizon, liquidity, and implementation: https://www.nber.org/papers/w25882
- Large/liquid cryptocurrencies show different momentum/reversal behavior from small illiquid
  coins: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4378429
- Trading volume can contain information about momentum persistence, but also about eventual
  overreaction: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=92589
- Recent crypto research finds volume-weighted time-series momentum, which motivates testing volume
  as part of the event rather than as another oscillator:
  https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4825389
- A realistic-assumptions study warns that many statistically attractive crypto momentum portfolios
  still lose money after jumps, liquidation, skew, and trading frictions:
  https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4675565

These papers motivate a test; none guarantees that this exact implementation has an edge.

## Registered result — 2026-08-17

The implementation was tested without changing the registered thresholds. Replays used USD-M,
both directions, hourly discovery, an eight-hour successful-entry cooldown, closed candles,
next-bar limit entry, and the existing managed exit. Each symbol used an independent $10,000
diagnostic account, so returns must not be added as if they were one portfolio.

Latest one-year window, doubled execution costs:

| Symbol | Trades | Win rate | Profit factor | Expectancy (R) | Return |
|---|---:|---:|---:|---:|---:|
| BTCUSDT | 23 | 8.7% | 0.093 | -1.110 | -5.23% |
| ETHUSDT | 39 | 25.6% | 0.382 | -0.553 | -4.84% |
| SOLUSDT | 52 | 32.7% | 0.449 | -0.443 | -5.18% |
| DOGEUSDT | 53 | 43.4% | 0.881 | -0.086 | -0.99% |
| AVAXUSDT | 44 | 47.7% | 0.819 | -0.117 | -1.14% |

Earlier one-year window, doubled execution costs:

| Symbol | Trades | Win rate | Profit factor | Expectancy (R) | Return |
|---|---:|---:|---:|---:|---:|
| BTCUSDT | 65 | 40.0% | 0.538 | -0.355 | -4.90% |
| ETHUSDT | 53 | 50.9% | 1.110 | +0.064 | +0.76% |
| SOLUSDT | 61 | 42.6% | 0.640 | -0.235 | -3.36% |
| DOGEUSDT | 46 | 47.8% | 1.160 | +0.100 | +1.04% |
| AVAXUSDT | 50 | 62.0% | 1.866 | +0.382 | +4.41% |

Ordinary-cost entry/exit attribution did not rescue the hypothesis. In the latest window BTC,
ETH, and SOL remained negative with managed exits. Replaying the same entry policy with a static
bracket left those three negative; only DOGE was clearly positive (+0.129R), while AVAX was
approximately flat (-0.001R). BTC's managed replay had 44 entries, of which 23 never reached
+0.5R, confirming that poor entry selection—not execution costs or the trailing policy alone—is
the primary defect.

The result fails the registered rule: zero of five latest-window symbols passed doubled costs, and
the apparent earlier effect was asset- and regime-dependent. V7 must not become the deployment
default, must not be enabled for live capital, and must not be repaired by tuning its thresholds on
these observed windows. A subsequent hypothesis needs a new version and new data.
