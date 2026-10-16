# 1Terminal — Atlas Institutional AlgoTrader & Crypto Screener

1Terminal (Atlas) is a testnet-first, multi-venue algorithmic trading and screening platform for 1-hour entries with
4-hour trend confirmation. It currently implements Binance Spot and Binance USD-M Futures,
plus a broker-neutral paper execution adapter. The same ports are intended for an IBKR Paper
adapter for equities, ETFs, and index futures.

> This repository is engineering infrastructure, not a promise of profitability or investment
> advice. `production-ready` means operationally controlled and testable; no strategy is ready
> for real capital until it passes the validation gates in this document.

## Safety defaults

- The default execution mode is `paper`.
- Live trading requires production environment, a boolean enable flag, and an exact acknowledgement.
- The operator API is bearer-token protected and binds to localhost by default.
- API startup fails unless the operator token contains at least 32 characters.
- Signals are discovered every confirmed hour, while successful entries have an eight-hour
  rolling cooldown by default.
- At most two same-direction crypto positions may be open across Binance venues.
- Duplicate order intents are durably rejected.
- Unknown order state activates the persistent kill switch.
- Entries use price-capped IOC limit orders; an unfilled entry is skipped rather than chased.
- Spot short entries are impossible; short exposure routes to USD-M Futures.
- Every filled entry must receive stop-loss and take-profit protection.
- Each intent, fill, stop, target, and tracked position transition is durably journaled.
- Local/exchange position divergence in either direction fails closed.

## Implemented architecture

```text
Binance/IBKR market data
        │
        ▼
liquidity filter → 1h/4h screener → deterministic strategy
                                           │
                                           ▼
                                  portfolio risk manager
                                           │
                                           ▼
                              idempotent execution coordinator
                                           │
                    ┌──────────────────────┴──────────────────────┐
                    ▼                                             ▼
             venue adapter                                durable audit state
                    │                                             │
                    ▼                                             ▼
          entry + protection                              kill switch/events
```

See [docs/architecture.md](docs/architecture.md) for the boundaries and invariants, and
[docs/pine-audit.md](docs/pine-audit.md) for the TradingView strategy review. Measured replay
results and their limitations are recorded in [docs/research-baseline.md](docs/research-baseline.md).

## Quick start

Requirements: Python 3.12+ and `uv`.

```bash
cp .env.example .env
# Put the output in ATLAS_OPERATOR_TOKEN; do not commit it.
openssl rand -hex 32
uv sync --extra dev
uv run atlas doctor
uv run atlas screen
```

For Binance Testnet, set `ATLAS_EXECUTION_MODE=testnet` and add separate Spot and USD-M
Futures testnet credentials to `.env`. Do not use production keys during development. Before the
first Spot Test Network execution, explicitly capture Binance's seeded inventory:

```bash
uv run atlas init-testnet-spot-baseline
```

Atlas persists those initial virtual balances and counts only later positive balance deltas as
managed Spot positions. After Binance resets the Spot Test Network account, first confirm there are
no Atlas-managed Spot positions or open Spot orders, then run
`uv run atlas reset-testnet-spot-baseline`. The reset command refuses to run while open Spot orders
exist and also clears the local portfolio risk baseline.

Start the private operations API:

```bash
uv run atlas serve
```

Automated execution is deliberately opt-in. Set `ATLAS_AUTOMATION_ENABLED=true`, then run either
`atlas worker` or the API process. By default, discovery runs after every confirmed hourly close
plus the configured grace period. Eligible observations are persisted for two bars and may be
seen repeatedly, but only one successful entry is allowed per rolling eight-hour cooldown. Before
an order is created, the selected instrument is screened again from fresh closed candles and the
current quote. An expired or invalidated observation cannot execute.

Use `Authorization: Bearer <ATLAS_OPERATOR_TOKEN>` for every `/v1/*` route. `/healthz` and
`/readyz` are public probe endpoints. Liveness does not call Binance; readiness verifies durable
state access and, when automation is enabled, that the scheduler task is running. If the API is
exposed beyond localhost, place it behind a trusted TLS reverse proxy with request limits; never
send the bearer token over plain HTTP.

If a stop, target, or manual exchange action removes a tracked testnet position, the next cycle
halts with a reconciliation reason instead of guessing the exit fill. After verifying Binance's
actual fill price and fees, use the authenticated
`POST /v1/trades/{signal_id}/reconcile-close` endpoint with `exit_price`, `fees`, and an
`exit_reason` of `stop`, `target`, `manual`, or `liquidation`. The endpoint refuses reconciliation
while the exchange position still exists and never clears the kill switch. Review the recorded PnL,
then re-enable the worker separately through `PUT /v1/kill-switch`. This supervised endpoint is
disabled in live mode. Clearing the kill switch is refused while an unresolved intent, incomplete
execution lifecycle, or position mismatch remains.

## Commands

- `atlas doctor`: report configuration presence without printing secrets.
- `atlas screen`: show one explicit operator report containing the configured strategy's
  executable Spot/Futures candidates, its rejection diagnostics, and the separate
  observation-only USD-M alt-momentum leaderboard. Only `execution_screen.candidates` can
  reach order execution.
- `atlas alt-momentum`: show the observation-only USD-M altcoin leaderboard. It ranks liquid alts
  by BTC-residual 6h/24h/7d return, six-hour acceleration and volume participation, while showing
  EMA/RSI/MACD/ADX context. It cannot create or execute an order.
- `atlas backtest --symbol BTCUSDT --venue futures --intervals 1,4,8`: run the
  single-symbol replay diagnostic and compare rolling entry cooldowns with hourly discovery.
- Add `--strategy-profile trend_quality_v3` to compare a research profile without changing the
  deployment's configured execution profile.
- Use `--strategy-profile slow_tsmom_v4` for the 4h decision/daily 30d-90d-180d time-series
  momentum experiment. It remains research-only.
- Use `--strategy-profile empirical_tsmom_v5` for the research-only parsimonious profile. It
  selects one daily/4h regime and the configured `ATLAS_EMPIRICAL_SETUP`; shorts require separate
  authority and the profile is rejected in live mode.
- Use `--strategy-profile layered_tsmom_v6` for the new hard/soft-gate experiment. It records a
  direction-normalized 30d/90d/180d momentum score, uses `ATLAS_LAYERED_SETUP`, and keeps shorts
  behind `ATLAS_LAYERED_SHORTS_ENABLED=true`. Configuration rejects this profile in live mode.
- Use `--strategy-profile early_momentum_v7` for the preregistered hourly volume-breakout,
  held-retest, and resumption experiment. Futures shorts require
  `ATLAS_EARLY_MOMENTUM_SHORTS_ENABLED=true`. Its two-window test failed promotion; configuration
  also rejects the profile in live mode.
- Use `--exit-policy managed|static` to compare the same entry rules under different exits. The
  resulting entry samples remain path-dependent because an exit changes when the next position can
  open; this is not a paired-trade estimator. Add
  `--cost-multiplier 2 --untouched-holdout` to nominate a result for governed review. A human
  attestation alone cannot make the CLI approve promotion.
- `atlas research-report` segments profiles and measures every stored feature independently using rank IC and
  top-minus-bottom quintile forward return. The holdout begins on a unique timestamp, with the
  forward-label horizon purged from the training boundary.
- Add `--end-days-ago 365` to replay the preceding historical year instead of the latest year;
  this supports chronological holdout checks without discarding indicator warm-up history.
- Replay output includes favorable/adverse excursion in R, the count that never reached +0.5R,
  and the count that reached +1R. `--stagnation-exit-bars 8` optionally tests a research-only
  no-progress exit; it is not part of live position management.
- `atlas init-testnet-spot-baseline`: capture initial seeded Spot Test Network inventory once.
- `atlas reset-testnet-spot-baseline`: replace the baseline after an exchange account reset.
- `atlas run-once`: run one screen/risk/execution cycle.
- `atlas worker`: run aligned automated cycles (requires the automation flag).
- `atlas serve`: start the localhost operations API.

## Screener model

The screener first removes inactive, illiquid, and wide-spread USDT instruments. Atlas exposes seven
explicit, auditable strategy profiles; a candidate produced by one profile cannot be executed when
another profile is configured. Hard eligibility
gates are evaluated separately from ranking. Eligible candidates receive a variable 70–100 rank
using trend separation, ADX strength, MACD acceleration, room to opposing structure, spread, and
relative volume.

Momentum discovery is deliberately separate from this entry funnel. `atlas alt-momentum` needs 169
closed hourly candles rather than 480 closed 4h candles, excludes BTC from the ranked set, estimates
each alt's seven-day hourly BTC beta, and ranks the residual return left after subtracting that BTC
component. It reports both long and short relative ranks plus `emerging`, `confirmed`, or `mixed`
phase labels. These are research observations, not candidates: anti-chase, 1.5R structural room,
entry construction and risk approval still belong to the execution strategy. See
[the v8 preregistration](docs/alt-momentum-v8-preregistration.md).

`legacy_momentum_v1` preserves the original baseline below. `regime_breakout_v2` is an opt-in
research profile using aligned 7-day/30-day 4h returns, a confirmed 40-hour channel breakout,
token-relative volatility percentiles, volume, spread, and cross-sectional opportunity ranking.
In the breakout profile, MACD, RSI, and ADX are observable low-weight ranking inputs only; they are
not mandatory confirmations. The short breakout sleeve is disabled independently because it failed
the six-asset diagnostic.

`trend_quality_v3` preserves the v2 momentum/breakout hypothesis and keeps the familiar technical
indicators in one bounded quality layer. EMA, MACD, RSI, and ADX/DMI each contribute at most one
confirmation; two of four are required. EMA verifies the 1h/4h fast/slow stack and EMA200 regime;
MACD verifies the histogram side on both timeframes and records a fresh 1h zero-line cross; RSI
verifies the directional 50-line regime without exhaustion; ADX/DMI verifies strength and
direction. Confirmed-pivot RSI divergence never repaints, but is delayed by construction, so it is
only a score penalty and operator warning—not a reversal signal or automatic exit. The v3 two-year
diagnostic was also negative and it therefore remains research-only. See `docs/research-baseline.md`
before selecting any profile.

`slow_tsmom_v4` makes decisions only on fully closed 4h bars inside an actual daily EMA200 regime.
At least two of 30d, 90d, and 180d returns must agree with direction; a 4h channel breakout is
ranking/timing evidence rather than a second momentum gate. Its first AVAX doubled-cost holdout was
negative under both managed and static exits, so it is not eligible for automated execution.

`empirical_tsmom_v5` is the deliberately parsimonious successor experiment. Eligibility uses one
primary regime—daily EMA200 plus two-of-three 30d/90d/180d time-series momentum—and exactly one
configured setup: a 4h channel breakout or a 1h EMA20 pullback continuation. EMA, MACD, RSI and
ADX/DMI form a bounded -4 to +8 ranking feature and cannot veto the regime/setup. The default short
sleeve is disabled and independently authorized. Every layer is persisted as a separate research
feature. The first five-asset replay remained below a profit factor of one, so v5 is not promoted.

`layered_tsmom_v6` makes the hard/soft boundary explicit. Its only alpha hard gates are the daily
EMA200 regime, agreement from at least two of 30d/90d/180d direction-adjusted returns, one selected
breakout or pullback setup, positive ATR, an anti-chase boundary, and a fully closed 4h decision
bar. Liquidity, spread, history, bracket geometry, BTC exposure, and portfolio limits remain safety
gates. EMA20/50, MACD, RSI, ADX/DMI, relative volume, volatility percentile, and market structure
only change ranking. Opposing structure can reduce priority but cannot veto an otherwise valid v6
setup. Support/resistance still protects the stop; the initial target must still clear 1.5R. The
new `directional_momentum_score` is positive when momentum agrees with the proposed side and uses
exactly mirrored math for long and short. `atlas research-report` tests that feature against later
direction-adjusted returns in chronological training and untouched-holdout partitions. This is an
instrumented hypothesis, not proof of positive expectancy or permission for live capital.

`early_momentum_v7` tests an earlier and more explicit event sequence. The 4h close must be on the
directional side of EMA50 and two of 6h/24h/7d returns must align. A closed 1h bar must then break
the preceding 20h channel on at least 1.25x prior average volume; a later bar must retest and hold
that level; and the current bar must close beyond both the breakout event and previous bar. This
prevents the current bar from proving its own setup. EMA20/50, EMA200, MACD, RSI, ADX/DMI,
volatility, current volume, and structure rank valid events but cannot veto them. Known opposing
structure must still leave at least 1.5R, and all existing execution and portfolio interlocks remain
unchanged. See [the preregistration](docs/early-momentum-v7-preregistration.md) before interpreting
any result. The registered replay rejected v7: all five latest-window symbols had negative
expectancy at doubled costs, so this profile is a reproducible research control—not a deployment
recommendation.

The following conjunction applies to the preserved `legacy_momentum_v1` profile:

- 1h and 4h EMA20/EMA50 trend alignment
- price relative to EMA200 on both timeframes
- ADX and directional movement
- three-bar price, MACD, RSI, and ADX momentum state
- RSI regime
- relative volume
- ATR-normalized tradable volatility

Support and resistance are calculated from the preceding 48 closed 1h candles. Long entries are
blocked within 0.5 ATR of resistance, and short entries are blocked within 0.5 ATR of support.
Both the CLI and operator API include these levels and the current/reference momentum values in
screening results. A second anti-chase gate rejects an entry quote more than 0.5 ATR beyond the
signal candle close. The final IOC limit is capped by both the quote tolerance and that exact ATR
boundary, so a price move between screening and submission cannot bypass the rule.

Every accepted signal receives a directionally symmetric protective bracket. The stop is at least
1.5 ATR from entry. When the relevant support (long) or resistance (short) can be invalidated within
3 ATR, the stop is moved beyond that level with a 0.25 ATR buffer; farther structural levels are
ignored so an old range extreme cannot create an unreasonable stop. The initial profit target is
2R. If opposing structure is closer, the target is placed 0.25 ATR before it, but the trade is
rejected unless at least 1.5R remains after tick-size normalization. Longs use
`support → stop` and `resistance → target`; shorts mirror this as
`resistance → stop` and `support → target`.

In `legacy_momentum_v1`, momentum is a hard entry gate, not merely ranking points. With the default three-bar lookback, Atlas
compares the latest closed bar with the bar three hours earlier; it does not require every intervening
bar to be monotonic. Longs require at least three of price, MACD histogram, RSI, and ADX to improve in
the long direction; shorts mirror the price, MACD, and RSI tests while also treating stronger ADX as
confirmation. Both directions additionally require
aligned 1h/4h EMA20/50 trends and EMA200 macro direction, ADX of at least 20, aligned DI, credible
volume, and sufficient volatility.
Candidates with mixed or losing momentum cannot produce an order, and momentum is checked again by
the strategy and risk manager before execution.

An optional research gate separates the trend setup from entry timing. With
`ATLAS_REQUIRE_PULLBACK_CONFIRMATION=true`, a candidate must have touched the 1h EMA20 zone within the
configured lookback, kept its closes on the trend side of EMA50, and closed back on the trend side of
EMA20. The existing three-bar composite supplies the continuation confirmation. A previous-bar break
can additionally be required with `ATLAS_PULLBACK_REQUIRE_PREVIOUS_BAR_BREAK=true`. These controls are
off by default because the recorded walk-forward diagnostics did not improve aggregate expectancy.

The replay reports conservative maximum favorable/adverse excursion for every trade. Entry-candle
excursion uses only the close because OHLC data cannot establish whether the high or low occurred
before the fill. Later candles use their full range; stop/target candles follow the replay's
stop-first convention rather than crediting an unknowable pre-exit excursion.

It ranks candidates; it does not label any trade as certain or “best”. A separate risk manager
decides whether a ranked candidate is allowed to become an order.

Futures use up to 3× leverage. Each Futures entry is sized from the stop-loss risk first, then
capped to one-third of Futures equity as estimated margin. Aggregate Futures notional cannot exceed
3× Futures equity. For a $100 Futures account, that means no more than about $33.33 margin per
position, about $100 notional per position at 3×, and $300 combined notional. The amount is derived
from current Futures equity; `$33.33` is not hardcoded. Because the crypto correlation gate permits
only two positions in the same direction, a third simultaneous position must be in the opposite
direction unless the policy is explicitly changed.

Position sizing is fixed-fractional rather than fixed-notional or Kelly-based: Atlas risks at most
0.25% of current venue equity at the confirmed stop using the worst price allowed by the capped IOC
order—not the earlier signal quote—then applies quantity precision, balance,
margin, gross-exposure, correlation, and portfolio-loss caps. A wider structure-aware stop therefore
produces a smaller position automatically. Kelly sizing is intentionally excluded until a stable,
out-of-sample estimate of win probability and payoff distribution exists.

The entire filled quantity is protected immediately by an exchange-side stop and take-profit order.
At +1R Atlas records a durable reduce-only intent, confirms the venue fill, realizes half by default,
and only then advances management state. The remaining runner receives stop-only protection at
cost-adjusted breakeven, so the original 2R target cannot cap it; from +2R onward the stop ratchets
behind confirmed structure. A rejected zero-fill partial restores the full original bracket. An
ambiguous fill or failed replacement activates the kill switch instead of assuming inventory is
safe. Manual, stop, target, and liquidation exits are reconciled into the durable ledger, including
already-realized partial P&L.

## Research replay

`atlas backtest` downloads public closed Binance candles and runs the same screener, strategy, risk
manager, anti-chase rule, and entry-price cap used by execution. A simulated IOC fills only when the
next candle's spread-adjusted OHLC range reaches its limit; otherwise the trade is skipped. It evaluates every confirmed 1h
bar, then compares 1h, 4h, and 8h rolling entry cooldowns. Its default one-year diagnostic reports
trade count, trades per 24 hours, win rate, payoff ratio, expectancy, expectancy in R, long/short
contribution, exit reasons, holding time, profit factor, closed-trade drawdown, and daily Sharpe. It
assumes 10 bps spread, 5 bps fees per side,
5 bps adverse stop slippage, Futures funding of 1 bp per eight hours, and stop-first execution when
both stop and target occur inside one candle.

This is a single-symbol replay of currently listed instruments, not a portfolio backtest and not
strategy validation. Runtime observations now capture actual funding, basis, open interest,
taker imbalance, order-book depth and BTC beta/correlation. Candle replay reconstructs aligned
historical BTC beta/correlation and fails closed when it is unavailable, but does not reconstruct
historical derivatives/order-flow fields. It also does not model delisted-symbol survivorship or
walk-forward parameter selection. Keep the configured
8h execution cooldown until multi-symbol out-of-sample evidence supports changing it.

The 4h ADX/DI, EMA20-extension, and volatility-ceiling controls are configurable but permissive by
default. They are research hypotheses, not claimed edge: the August 2026 six-symbol test of a
stricter combined profile reduced both aggregate P&L and weighted win rate. See
`docs/research-baseline.md` before changing production defaults.

No profile has passed the live-capital validation gates. Automation is disabled by default; the
presence of the v2, v3, and `slow_tsmom_v4` profiles in code records reproducible experiments,
not approval to trade.

## Validation gates before live funds

1. Unit, integration, and property tests pass.
2. Backtest includes fees, funding, spread, slippage, and delisted symbols.
3. Walk-forward and untouched out-of-sample results remain acceptable.
4. Binance Spot and Futures Testnet run without reconciliation errors for at least 30 days.
5. Live-data shadow mode runs for at least several weeks.
6. Disaster-recovery and kill-switch drills pass.
7. A tiny-capital canary is reviewed manually before any scaling.

## Current scope boundary

Binance trades crypto products, not ordinary listed equities. Cash indexes are benchmarks rather
than directly orderable assets. Equity and index exposure therefore requires a separate broker
through stocks, ETFs, futures, or options. The domain ports support that separation; the first
traditional-asset implementation should use IBKR Paper after an account and API choice are confirmed.
