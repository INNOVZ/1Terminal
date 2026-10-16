# Research baseline

This file records reproducible diagnostics, not a claim that Atlas has a validated edge.

## 2026-08-12 — layered long/short momentum architecture, not promoted

`layered_tsmom_v6` is a new versioned experiment; `empirical_tsmom_v5` remains unchanged for
reproducible comparison. V6 keeps only one slow alpha regime and one configured entry setup as hard
eligibility. EMA20/50, MACD, RSI, ADX/DMI, relative volume, token-relative volatility, and market
structure are bounded ranking evidence rather than independent vetoes. Tradability, closed-history,
anti-chase, bracket geometry, BTC exposure, portfolio loss, leverage, and execution interlocks stay
hard because they control whether a trade can be executed safely rather than whether it has alpha.

The profile adds a bounded `directional_momentum_score` over 30d, 90d, and 180d returns. Longs use
the returns directly; shorts negate them before the same normalization. Unit tests prove that an
equal rising/long and falling/short vector receives the same score and that rising momentum is
negative for a proposed short. A falling-market integration fixture produces an eligible Futures
short and rejects the long. This proves directional code symmetry, not predictive edge.

The score is persisted for every accepted and rejected hourly opportunity and pre-registered in
`atlas research-report`. The report measures its Spearman rank information coefficient and
top-minus-bottom quintile forward return separately on chronological training and purged untouched
holdout partitions. The short sleeve remains disabled by default, and configuration refuses v6 in
live mode. Promotion still requires positive cost-aware portfolio expectancy under doubled costs
and untouched holdouts; no win-rate target is encoded.

Initial one-year, single-symbol USD-M diagnostics enabled both sleeves and charged 2x spread, fees,
stop slippage, and funding. These are robustness checks, not a portfolio holdout:

| Symbol | Trades (L/S) | Win rate | Profit factor | Expectancy | Return |
|---|---:|---:|---:|---:|---:|
| BTCUSDT | 33 (9/24) | 39.4% | 0.558 | -0.312R | -2.31% |
| SOLUSDT | 28 (6/22) | 42.9% | 0.727 | -0.172R | -1.15% |

Both runs find long and short directional momentum, but both fail the promotion gate. The result is
evidence that loosening correlated indicator gates increases usable observations without creating
an edge by itself. V6 must remain paper/testnet research, and further work should analyze the
persisted forward information coefficients and entry/exit attribution rather than tune thresholds
against these same two samples.

## 2026-08-17 — rejected early-momentum impulse/retest hypothesis

`early_momentum_v7` was preregistered before replay as a closed-bar event sequence: 4h EMA50
regime, two-of-three 6h/24h/7d directional returns, a 20h channel breakout on at least 1.25x
preceding volume, a held retest, and resumption beyond the breakout and prior-bar extreme. The
existing anti-chase rule, known-level 1.5R room, bracket construction, sizing and risk interlocks
were unchanged. EMA, MACD, RSI, ADX/DMI and confirmed structure were bounded ranking evidence,
not additional hard gates.

The registered five-symbol, two-window diagnostic rejected the hypothesis. In the latest one-year
window all five symbols had negative expectancy under doubled execution costs. In the earlier
window ETH, DOGE and AVAX were positive, while BTC and SOL remained negative. The direction and
symbol effects therefore did not survive the regime change. Latest ordinary-cost managed results
also remained negative for BTC, ETH and SOL; static exits did not repair them. Full per-symbol
results and the original hypothesis contract are in `docs/early-momentum-v7-preregistration.md`.

This experiment also exposed and fixed a generic scoring defect: soft market-quality adjustments
could reduce an otherwise eligible profile below `min_candidate_score`, silently converting a
ranking warning into a hard rejection. Eligible soft-profile candidates are now floored at the
configured ranking baseline. The correction has direction-symmetric regression coverage and does
not change the failed v7 conclusion.

The profile is retained for reproducibility, remains disabled for shorts by default, and is blocked
by the live-mode configuration interlock. Its thresholds must not be optimized on the observed
windows. The next evidence milestone is point-in-time, multi-symbol portfolio replay of relative
and BTC-residual momentum plus the already-collected funding/OI/order-flow features—not another
single-symbol indicator conjunction.

## 2026-08-12 — legacy directional-MACD ranking correction

`legacy_momentum_v1` intentionally permits a three-of-four price/MACD/RSI/ADX momentum
consensus. A ranking defect nevertheless used the absolute MACD-histogram change, so a MACD move
against the candidate direction could add up to six priority points after the consensus tolerated
that conflict. The operator rationale also called every eligible observation "MACD net
acceleration," including disagreements.

Ranking now uses signed directional change: `current - reference` for longs and `reference -
current` for shorts. A conflicting or unchanged MACD contribution is zero and the rationale labels
it as a conflict tolerated by the momentum consensus. Aligned acceleration retains the same bounded
six-point maximum. Long, short, and evidence regressions cover the correction.

This is a correctness fix to cross-sectional priority, not evidence of improved expectancy. A
single-symbol replay normally has no competing candidate to re-rank, so the historical baseline is
not relabeled. The next valid performance comparison requires a chronological multi-symbol
portfolio replay using the runtime ranker.

## 2026-08-11 — slow time-series momentum and entry/exit isolation

`slow_tsmom_v4` tests a materially different horizon instead of another MACD/RSI combination.
It makes decisions on fully closed 4h bars, requires the daily EMA200 regime plus agreement from at
least two of 30d/90d/180d returns, and records—but does not require—a 30-bar 4h breakout.

AVAXUSDT was replayed over the untouched year ending 2026-08-11 with 2x spread, fee, slippage and
funding assumptions. Screening spread remained the observed 10 bps; stressed execution spread was
20 bps. This separation fixed a replay defect where cost stress had previously removed every bar
at the liquidity gate.

| Exit policy | Trades | Win rate | Profit factor | Expectancy (R) | Return |
|---|---:|---:|---:|---:|---:|
| Managed | 82 | 40.2% | 0.611 | -0.280 | -5.03% |
| Static bracket | 110 | 32.7% | 0.758 | -0.193 | -4.73% |

Both variants fail the promotion gate. Managed exits improve hit rate but worsen expectancy, while
static exits preserve a larger payoff ratio. The entry hypothesis is therefore still negative and
neither exit policy is approved. BTC produced no eligible trades in the same one-year slow-profile
window, which is a valid sparsity result rather than a zero-loss claim.

The runtime now records every hourly opportunity, including rejected directions, and captures
actual USD-M funding, basis, open interest, taker imbalance, depth plus BTC beta/correlation.
`atlas research-report` labels matured rows from later observations and reports each feature's
univariate rank IC and quintile spread on chronological partitions. The holdout starts on a unique
timestamp, and rows whose forward label overlaps the boundary are purged. Storage and report
lookback are bounded by `ATLAS_RESEARCH_RETENTION_DAYS` and
`ATLAS_RESEARCH_REPORT_LOOKBACK_DAYS`. Human holdout attestation can nominate a run for governed
review but cannot approve promotion without an immutable experiment registry.
These fields have not accumulated enough live history yet to support an alpha decision.

## 2026-08-11 — empirical trend architecture, not promoted

`empirical_tsmom_v5` replaces the large indicator conjunction with separately observable layers:

- primary regime: daily EMA200 and two-of-three 30d/90d/180d time-series momentum;
- one pre-registered setup per run: 30-bar 4h breakout or 1h EMA20 pullback continuation;
- bounded EMA/MACD/RSI/ADX-DMI quality rank, never an eligibility veto;
- hard liquidity, execution, BTC-exposure, bracket and portfolio-risk controls;
- long-only by default; the short sleeve requires separate explicit authority.

Latest one-year USD-M diagnostics on BTC, ETH, SOL, DOGE and AVAX used the current managed exits
and modeled costs. Both setup hypotheses were declared before replaying the group:

| Setup | Trades | Winners | Win rate | Profit factor | Expectancy | Summed P&L |
|---|---:|---:|---:|---:|---:|---:|
| 4h breakout | 29 | 14 | 48.3% | 0.928 | -0.050R | -$26.24 |
| 1h pullback | 60 | 28 | 46.7% | 0.989 | -0.002R | -$8.42 |

The pullback comparison with static exits produced 63 trades, 21 winners, profit factor 0.884,
expectancy -0.074R and -$121.26. This comparison holds entry rules constant, but not identical
entry events: exit duration changes when the next position is permitted, so results are path
dependent. Because neither managed setup is positive at ordinary costs, doubled-cost and promotion
gates fail without needing another parameter search. The profile remains research-only and the
default deployment profile is unchanged.

## 2026-08-11 — rejected indicator-quality consensus

`trend_quality_v3` tests whether familiar technical indicators improve the v2 breakout hypothesis
without pretending they are independent data. EMA, MACD, RSI and ADX/DMI form one auditable
2-of-4 confirmation layer. A fresh MACD histogram zero-line cross is timing evidence rather than a
required event. RSI divergence is calculated only between confirmed closed-bar pivots and is a
bounded warning penalty, not a reversal trigger. The original v2 profile is unchanged so the
comparison remains reproducible.

The long-only USD-M replay used BTC, ETH, SOL, BNB, XRP and DOGE, hourly discovery, an eight-hour
successful-entry cooldown, managed exits, and the same modeled costs as the earlier baseline:

| Window | Trades | Winners | Win rate | Summed diagnostic P&L |
|---|---:|---:|---:|---:|
| 2025-08-11 to 2026-08-11 | 50 | 22 | 44.0% | +$2.17 |
| 2024-08-11 to 2025-08-11 | 88 | 38 | 43.2% | -$63.80 |
| Combined | 138 | 60 | 43.5% | -$61.63 |

The unchanged v2 comparison was 140 trades, 62 winners (44.3%), and approximately -$59.68. The
indicator consensus therefore did not improve aggregate win rate or expectancy. Its latest-window
P&L also fell from v2's +$90.79 to about +$2.17. `trend_quality_v3` remains available behind
`ATLAS_STRATEGY_PROFILE=trend_quality_v3` for diagnostics, but the default configuration does not
select it and it is rejected for automated or live-capital promotion. This is direct local evidence
against assuming that additional confirmation automatically increases signal quality.

## 2026-08-11 — rejected regime-breakout profile

`regime_breakout_v2` was implemented as a separate pure alpha policy. It replaces the legacy
three-hour price/MACD/RSI/ADX eligibility conjunction with aligned 7-day and 30-day momentum, a
confirmed 40-hour channel breakout, token-relative volatility percentiles, volume/spread quality,
and runtime cross-sectional ranking. MACD, RSI, and ADX contribute only bounded ranking points.

The first latest-window replay allowed symmetric longs and shorts. Across BTC, ETH, SOL, BNB, XRP,
and DOGE it produced 184 trades, 73 winners (39.7%), and approximately -$605.09 summed diagnostic
P&L. Shorts contributed approximately -$697.54, so the short sleeve was disabled rather than letting
long evidence authorize a mirrored short policy.

The independently measured long sleeve produced:

| Window | Trades | Winners | Win rate | Summed diagnostic P&L |
|---|---:|---:|---:|---:|
| 2025-08-11 to 2026-08-11 | 49 | 23 | 46.9% | +$90.79 |
| 2024-08-11 to 2025-08-11 | 91 | 39 | 42.9% | -$150.47 |
| Combined | 140 | 62 | 44.3% | -$59.68 |

The latest window improved, but the chronological holdout rejected promotion. The implementation
remains available behind `ATLAS_STRATEGY_PROFILE=regime_breakout_v2` for controlled research;
the default and example configuration do not select it, and automation remains off by default.
This result also confirms that MACD/RSI removal alone does not manufacture edge. Cross-sectional
portfolio replay and genuinely orthogonal funding/order-flow data remain the next research step.

## 2026-08-10 — managed-runner, multi-symbol diagnostics

These runs use hourly discovery, an eight-hour successful-entry cooldown, confirmed-bar management,
the post-partial stop-only runner, and the cost assumptions below. Each symbol is replayed against a
separate $10,000 account, so summed P&L is a comparison statistic—not a portfolio equity curve.

Latest window, 2025-08-10 through 2026-08-10:

| Symbol | Trades | Win rate | Profit factor | Expectancy (R) | Return |
|---|---:|---:|---:|---:|---:|
| BTCUSDT | 16 | 31.3% | 0.796 | -0.171 | -0.53% |
| ETHUSDT | 17 | 47.1% | 1.089 | +0.048 | +0.20% |
| SOLUSDT | 16 | 31.3% | 0.334 | -0.515 | -1.79% |
| BNBUSDT | 15 | 60.0% | 2.353 | +0.597 | +2.01% |
| XRPUSDT | 23 | 43.5% | 0.786 | -0.132 | -0.68% |
| DOGEUSDT | 32 | 50.0% | 1.091 | +0.052 | +0.36% |

Aggregate diagnostic: 119 trades, 44.5% weighted win rate, and -$43.88 summed P&L across six
independent accounts. Dispersion is much larger than the aggregate: one profitable symbol masks a
large SOL loss.

Chronological preceding window, 2024-08-10 through 2025-08-10, generated with
`--end-days-ago 365`:

| Symbol | Trades | Win rate | Profit factor | Expectancy (R) | Return |
|---|---:|---:|---:|---:|---:|
| BTCUSDT | 9 | 44.4% | 1.194 | +0.101 | +0.23% |
| ETHUSDT | 18 | 33.3% | 0.437 | -0.421 | -1.65% |
| SOLUSDT | 17 | 47.1% | 1.055 | +0.059 | +0.12% |
| BNBUSDT | 14 | 42.9% | 0.470 | -0.329 | -1.04% |
| XRPUSDT | 26 | 57.7% | 1.576 | +0.288 | +1.59% |
| DOGEUSDT | 18 | 22.2% | 0.238 | -0.635 | -2.61% |

Aggregate diagnostic: 102 trades, 42.2% weighted win rate, and -$336.40 summed P&L. The reversal
between BNB and XRP across the two windows rejects a static symbol whitelist and shows why one-year
single-symbol optimization is unsafe.

### Rejected hypothesis: strict combined continuation profile

The latest window was repeated with 4h ADX >= 20, aligned 4h DI, and entry no more than 1.5 ATR in
the trend direction from the 1h EMA20:

```bash
ATLAS_MIN_4H_ADX=20 \
ATLAS_REQUIRE_4H_DI_ALIGNMENT=true \
ATLAS_MAX_ENTRY_EXTENSION_FROM_EMA20_ATR=1.5 \
atlas backtest --symbol BTCUSDT --venue futures --intervals 8 --history-days 365
```

Across the same six symbols it produced 96 trades, a 41.7% weighted win rate, and -$86.34 summed
P&L. That is worse than baseline, so these controls remain permissive by default. The result is kept
as an audit record to prevent retesting and selectively reporting the same failed idea.

### Rejected hypothesis: EMA20 pullback continuation gate

Atlas now exposes an optional setup-to-trigger gate shared by runtime screening and replay. It looks
for a recent 1h EMA20-zone touch, requires the pullback to hold EMA50, and requires the signal to close
back on the trend side of EMA20. The existing three-bar price/MACD-histogram/RSI/ADX composite remains
the momentum confirmation.

The latest six-symbol window was replayed with only this additional setting:

```bash
ATLAS_REQUIRE_PULLBACK_CONFIRMATION=true \
atlas backtest --symbol BTCUSDT --venue futures --intervals 8 --history-days 365
```

The same command was repeated for ETH, SOL, BNB, XRP, and DOGE. Aggregate result: **84 trades, 37
winners (44.0%), and -$99.95 summed P&L** across six independent accounts. This is worse than the
unchanged baseline of 119 trades, 44.5%, and -$43.88. ETH improved materially in isolation, but SOL
lost $141.46 and the cross-asset aggregate deteriorated; a single-symbol improvement is not a valid
promotion case.

A stricter variant also required the signal close to break the previous candle in the trade
direction:

```bash
ATLAS_REQUIRE_PULLBACK_CONFIRMATION=true \
ATLAS_PULLBACK_REQUIRE_PREVIOUS_BAR_BREAK=true \
atlas backtest --symbol BTCUSDT --venue futures --intervals 8 --history-days 365
```

It produced 48 trades, 18 winners (37.5%), and -$209.36 in the latest window. In the preceding
non-overlapping year it produced 42 trades, 16 winners (38.1%), and -$248.16. The bar-break condition
double-counted confirmation already supplied by the three-bar composite and made entries sparser and
later. Both variants remain off by default; their code and diagnostics are retained as explicit
research controls, not claimed improvements to edge.

### Rejected hypothesis: eight-bar no-progress exit

Replay instrumentation now records maximum favorable excursion (MFE) and maximum adverse excursion
in initial-risk units. In the unchanged latest-window baseline, 50 of 119 trades (42.0%) never
reached +0.5R and 58 of 119 reached +1R. Weighted across the six symbols, average MFE was
approximately +1.72R and average adverse excursion was approximately 0.74R. This justified testing
one explicit hypothesis rather than guessing from holding time alone:

```bash
atlas backtest --symbol BTCUSDT --venue futures --intervals 8 --history-days 365 \
  --stagnation-exit-bars 8 --stagnation-min-favorable-r 0.5
```

The trade was closed at the eighth confirmed hourly close if it was still in the initial management
stage and had never reached +0.5R. Across the latest six-symbol window, earlier exits freed capacity
and produced 125 trades, but only 48 winners (38.4%) and approximately -$198.32 summed P&L. This is
materially worse than the unchanged baseline. The option remains available for reproducible research
but is intentionally absent from live management.

### Interpretation

- The current signal does not yet demonstrate a robust positive edge; do not optimize for win rate
  or deploy live capital on these results.
- Long/short and symbol contribution reverse across regimes. Direction bans and asset whitelists are
  not supported by these two windows.
- The next research milestone is a chronological multi-symbol portfolio replay that performs the
  same cross-sectional ranking and correlation/exposure constraints as runtime, with delisted-symbol
  data and mark-to-market drawdown. This is higher value than adding more hard indicator gates.
- Current maximum drawdown is based on closed-trade equity and excludes intratrade excursions; the
  JSON output labels this limitation explicitly.

## 2026-08-04 — current architecture smoke replay

The production-path replay completed successfully for BTCUSDT USD-M over the 30 days ending
2026-08-04, using hourly discovery and an eight-hour rolling entry cooldown. It produced zero
eligible trades under the current strict gates. This validates the replay path and confirms that
the timing change does not manufacture entries; it is too short and too sparse to evaluate edge.

```bash
uv run atlas backtest --symbol BTCUSDT --venue futures --intervals 8 --history-days 30
```

## 2026-08-04 — legacy fixed-slot baseline (superseded)

> These numbers were produced before hourly discovery plus rolling cooldowns were implemented.
> They remain here as an audit record, but they are not evidence for the current runtime timing
> model. Regenerate this table before using it in a strategy decision.

Command:

```bash
uv run atlas backtest --symbol BTCUSDT --venue futures --intervals 1,4,8 --history-days 365
```

Assumptions: $10,000 starting equity, 10 bps spread, 5 bps fees per side, 5 bps adverse stop
slippage, 1 bp Futures funding cost per eight hours, closed candles only, next-bar entry, and
stop-first resolution when stop and target share a candle.

| Legacy fixed slot | Trades | Trades/24h | Win rate | Profit factor | Return | Max drawdown | Daily Sharpe |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1h | 93 | 0.255 | 33.3% | 0.774 | -4.02% | 5.07% | -1.30 |
| 4h | 45 | 0.123 | 42.2% | 1.129 | +0.99% | 2.00% | 0.44 |
| 8h | 35 | 0.096 | 40.0% | 1.050 | +0.30% | 2.25% | 0.16 |

Historical interpretation only: the 1h fixed-slot schedule overtraded this sample; 4h was stronger
than 8h, but neither positive result was robust enough to justify a production policy change. The
current implementation discovers hourly and treats the comparison value as a rolling entry
cooldown, so a fresh multi-symbol walk-forward run is required.
