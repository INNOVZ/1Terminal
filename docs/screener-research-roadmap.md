# Atlas screener: evidence, decisions and delivery order

Research date: 2026-10-22. Scope: the repository and primary exchange, methodology and infrastructure
sources. This is an engineering/research assessment, not an empirical validation of the screener.
No historical replay or live exchange experiment was completed as part of this migration.

## Decision

Build a public-data decision-support screener first. It should identify reproducible setups, explain
why they qualify, expose uncertainty and record what happened afterwards. Algorithmic execution is
deferred to a separate future product. Removing orders reduces operational risk; it does not establish
predictive accuracy. “Institutional grade” is a target requiring measurable acceptance criteria,
not a property conferred by a large number of indicators or a Grade A label.

The migration removes trading/account network capabilities, the testnet trader, paper broker,
execution/reconciliation coordinators, trading routes and CLI commands. It retains useful offline
research calculations. It also labels heuristic outputs, replaces the hardcoded scan count, adds
cleanup and readiness, and requires verification before deployment.

## What the evidence supports

### Public market data is sufficient for this product

Binance distinguishes public NONE endpoints from authenticated account/trading endpoints. Public
screening does not require a trading API key. We enforce that distinction in the transport with an
exact public GET allowlist and no signing implementation. This is stronger than a configuration flag
that could accidentally be changed. [Binance request security](https://developers.binance.com/en/docs/products/spot/rest-api)

The Spot adapter is retained for public research; the current main screener uses USD-M perpetual
contracts. A perpetual short scenario is analytical output, not permission to borrow, size or trade.

### Correct timestamps and units come before additional signals

Binance's OI statistics API exposes 1h and 4h periods, timestamps, contract OI and OI notional as distinct
fields. It retains approximately one month and may omit periods without trades. Thus array offsets
alone cannot guarantee a one-hour or seven-day change. The current adapter still assigns its four-hour
change to the one-hour field and uses the first of 42 four-hour samples as seven-day history; that span
is only 164 hours when complete. These are known defects, not accepted model choices.
[Binance USD-M market data](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data)

Next implementation: join observations by exchange timestamp; require the declared horizon and
freshness; return missing when the endpoint cannot support it. Specify whether each feature measures
contract quantity or USD notional. USD OI can rise merely because price rises. The existing strategy
must explicitly choose its intended interpretation before changing formulas.

Aggregate taker buy/sell volumes over the exact requested window and divide their sums. Do not
substitute a one-hour ratio for four-hour or 24-hour flow, or average ratios with different denominators.
Preserve missing values instead of inventing neutral evidence. Timestamp semantics and partial bars
need fixture tests from documented exchange payloads.

### A candle-derived volume profile is an approximation

TradingView documents using lower-timeframe data for volume profiles. Atlas distributes candle
volume across price bins and currently uses 180 four-hour bars/40 bins, while its older specification
describes 720 hourly bars/50 bins. Neither a candle interpolation nor a matching time window proves
observed trade volume at each price. Label the profile method, interval, lookback and bin count.
Compare against lower-timeframe or trade-derived profiles on representative liquid and thin markets
before using narrow POC/VAL thresholds. [TradingView volume-profile methodology](https://www.tradingview.com/support/solutions/43000502040-volume-profile-indicators-basic-concepts/)

Binance supplies downloadable public market archives with checksums. They can support reproducible
candle/trade datasets, but historical files do not automatically provide every contemporaneous input
used by this screener. Verify timestamp units and coverage per dataset; retain source checksums and
download versions. Begin collecting OI, macro and universe snapshots prospectively.
[Binance public-data repository](https://github.com/binance/binance-public-data)

### Market research motivates hypotheses, not these exact rules

Liu and Tsyvinski report cryptocurrency momentum and investor-attention effects in their historical
sample. Liu, Tsyvinski and Wu identify market, size and momentum factors. This supports testing simple
momentum and market-adjusted baselines. It does not validate Atlas's current Wyckoff labels, gate
thresholds, short-horizon altcoin universe, transaction costs or hardcoded probabilities.
[Risks and Returns of Cryptocurrency](https://www.nber.org/papers/w24877),
[Common Risk Factors in Cryptocurrency](https://www.nber.org/papers/w25882)

| Hypothesis family | Current interpretation | Necessary comparison |
|---|---|---|
| Trend/momentum | EMA, RSI, MACD, ADX and breakout state | Simple momentum alone; measure incremental value of correlated indicators |
| Range/accumulation | Compression and price/profile geometry | Same-volatility liquid controls; measure breakout incidence and false breaks |
| Distribution/reversal | Exhaustion, divergence and rejected highs | Separate short-side outcomes across market regimes |
| Derivatives positioning | OI, funding, basis and taker flow | Price-only baseline; correct windows, missingness and units first |
| Macro overlay | Sentiment, dominance and liquidity conditions | Compare with/without overlay using data actually available at the decision time |

### Scores and probabilities need different evidence

A calibrated probability must match observed event frequency. Calibration curves, Brier score and log
loss help evaluate probability forecasts, but must use data independent of model fitting. Brier score
also reflects discrimination and outcome uncertainty, so it is not sufficient alone. Atlas currently
has assumed base win rates and conditional target probabilities; these are not calibrated forecasts.
[Scikit-learn probability-calibration guide](https://scikit-learn.org/stable/modules/calibration.html)

Keep a transparent ranking score initially. Preserve old API field names only for compatibility, with
explicit uncalibrated metadata. The UI now shows “Uncalibrated” instead of a claimed win percentage and
labels model EV as assumption-based. Calibration should follow a declared event definition: for
example, a specified target reached before invalidation within 24 hours. A probability without a
time horizon, entry condition and handling of ambiguous bars has no reproducible meaning.

### Multiple experiments require an experiment ledger

Bailey et al. show how selecting strategies from many backtests can overfit and propose a framework
to assess that risk. Record every attempted specification and rejected variant. A final holdout loses
its role if inspected repeatedly while tuning. Walk-forward splits, overlap purging and block-aware
uncertainty are proposed controls for our timestamped, overlapping observations; they are not a
guarantee of future performance. [The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf)

## Recommended architecture and research contract

Keep the modular monolith. Separate three concerns inside it: acquiring validated public data,
evaluating a versioned screening policy, and publishing/observing results. A shared application service
should serve the API, CLI, cron and replay; admission rules must not be independently reimplemented.

Each scan should persist: scan ID, code/strategy version, configuration hash, decision timestamp,
data cutoff, instrument universe as known then, provider timestamps, input availability, selected and
rejected candidates, gate reasons, raw/normalized features, ranking and output levels. Persisting only
successful symbols cannot diagnose selection bias or reconstruct why a candidate was missing.

For ranking quality, measure top-k lift against the eligible universe, forward returns relative to BTC,
rank correlation, coverage and turnover. For setup quality, measure trigger incidence, target-before-
invalidation frequency, maximum favourable/adverse excursion and time to resolution. Report 4h/24h/72h
horizons separately and group long/short, setup family, liquidity tier and regime. Bootstrap by time
blocks and asset clusters; correlated hourly observations are not independent trials.

Freeze hypothetical entry/stop/target levels when published. Define no-fill, expiry, overlapping signals,
missing prices and delistings before scoring results. When both target and stop fall within one candle,
use finer data or report conservative bounds. Compare executable-price assumptions with fees, spread,
slippage and funding for any profitability claim. A screener can be useful without an automated P&L
claim; paper execution is unnecessary to collect forward observations.

## Ordered delivery plan

### 0 — Pure-screener boundary and deployment migration: implemented locally

Removed exchange execution, credentials, trading entry points and runtime coupling. Added negative
capability tests, client lifecycle cleanup, honest model labels, observed scan counts and CI checks.
The deployment workflow uses the verified commit, builds before replacement and waits for health,
following [Compose's documented health wait](https://docs.docker.com/reference/cli/docker/compose/up/).

Release acceptance still requires VM rollout, legacy-process inspection, key revocation, watchlist
backup/restore verification and a controlled real public-data scan. No source change can cancel an
existing exchange order or stop a separate worker. See gcp-deployment.md. Do not mark this milestone
deployed merely because local tests pass.

### 1 — Data correctness and versioned strategy contract

Priority fixes: OI/taker horizons; closed/fresh bars; missing-data rejection; exact universe eligibility;
ILI inputs rather than volume-imputed pseudo-factors; VPVR method parity; COI propagation; true R:R from
tick-normalized levels. The breakout scorer currently floors reported R:R at 2.5 and must instead
measure the bracket and reject insufficient reward. Consolidate API/cron/CLI acceptance policies.

Acceptance: fixture tests for timestamps/gaps/units; one policy version used by all entry points;
formula/threshold contract tests; no missing data silently promoted to confirming evidence;
documented counts for universe, selected, complete, partial, rejected and published candidates.
Keep the four-hour schedule during migration; choose a faster loop only after load and usefulness
measurements. Do not implement the old 15-minute/8-hour specification by assumption.

### 2 — Snapshot archive and forward-outcome recorder

Start collection immediately after data semantics are stable. Use append-only observations with
idempotent scan/event IDs, immutable decision-time features and a separate outcome job. Retain
delisted/ineligible instruments in history. Store raw snapshots outside the small operational SQLite
database as volume grows, with manifests and checksums; preserve watchlist state separately.

Acceptance: replay an archived scan into identical decisions; observe every published setup through
expiry; quantify missing outcomes; prove the job never reaches an account endpoint. Determine sample
requirements from desired confidence intervals and effective independent observations, not an arbitrary
number of calendar days or trades.

### 3 — Benchmark and calibrate

Test simple liquid-universe momentum/range baselines and ablate each feature family. Preregister
chronological splits and primary metrics, purge observations whose outcome windows overlap validation,
and reserve an untouched final period. Evaluate the same production policy with point-in-time inputs.
The existing institutional backtester is exploratory and currently lacks this parity and dataset.

Acceptance: report all trials, uncertainty, regime-specific results, cost sensitivity and failure
conditions. Only display win probabilities after out-of-sample calibration shows useful reliability
and sample sizes per reported group. Promote no strategy solely because its in-sample score improves.

### 4 — Production quality, in parallel with observation

Add user authentication/ownership for watchlist writes, rate limits and bounded scan parameters,
coalesced scheduled/on-demand scans, TLS/restricted ingress, stale-data labels, scan duration/error
metrics and tested backups. The current public mutation endpoints and concurrent scan paths remain
operational debt. Readiness alone is not a data-quality monitor.

Acceptance: a fresh install and upgrade preserve state; provider outage and overload tests produce
explicit degraded status; no fabricated macro values; restore drill succeeds; approved scan-load and
freshness budgets are measured. Start with service objectives derived from the four-hour cadence and
the VM's measured capacity rather than claiming arbitrary “institutional” latency.

### 5 — Future execution product: deferred

Revisit only with a stable signal contract and credible evidence. Build a separately deployed,
authenticated executor with independent credentials, account risk, idempotency, fill/protection state
machines and reconciliation. It consumes versioned screening events; the screener never receives
trading keys. Git history preserves prior implementation ideas, but restoration is a new review and
validation task, not an environment toggle.

## Limits of this research

Primary sources establish API semantics and evaluation methods, not Atlas's effectiveness. Existing
repository experiments concern multiple older strategies and cannot establish the current screener's
edge. Historical macro/OI availability, delisted coverage, current production load and user-perceived
utility need direct measurement. This document distinguishes implemented migration work from the
remaining validation programme so future releases cannot accidentally claim evidence we do not have.
