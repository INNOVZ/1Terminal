# Atlas screener: evidence, decisions and delivery order

## Shadow milestone checkpoint (2026-10-25)

The independent shadow arm is implemented locally in the isolated `dual-track-shadow` worktree.
Independent review's seven Important findings were addressed with regression tests; the final suite
passes 443 tests, strict source typing and touched-file lint. Manual scan/replay/outcome commands preserve the existing
published core; no production runtime/API/cron integration or threshold promotion is added. The
original observation milestone remains complete. See
`docs/superpowers/plans/2026-10-24-dual-track-shadow.md` for verification and remaining work.
Next research gates remain prospective gainer-rank snapshots, matched-cutoff comparisons and measured
collection costs. Passing tests establish implementation behavior, not discovery advantage.
Local integration approved/completed on 2026-10-25: the original checkout now includes the verified
shadow implementation, with all changes kept uncommitted. Post-integration checks pass 443 tests,
strict source typing and touched-file lint; existing scan data is unchanged. Nothing was pushed or
deployed. At that checkpoint, prospective rank tracking was the next research milestone.
Jithin subsequently approved rank snapshots/benchmarking before dashboard integration. Its design is
`docs/superpowers/specs/2026-10-25-rank-observation-benchmark-design.md`; local implementation and the
review fix pass are complete. No rank collection is scheduled or live; see the checkpoint below.

Research date: 2026-10-22. Implementation checkpoint: 2026-10-24. Scope: the repository and primary
exchange, methodology and infrastructure sources. This is an engineering/research assessment, not an
empirical validation of the screener. Bounded public-data smoke scans were subsequently completed;
historical replay and predictive validation remain outstanding.

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
alone cannot guarantee a one-hour or seven-day change. The original adapter assigned its four-hour
change to the one-hour field and used the first of 42 four-hour samples as seven-day history; that span
is only 164 hours when complete. The current adapter fetches hourly observations and requires an
exchange-timestamp match for each requested horizon. Missing or stale observations do not become a
shorter-window change.
[Binance USD-M market data](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data)

OI-change features now measure contract quantity, not USD notional, to isolate positioning change
from mark-price change; the absolute OI field remains the exchange's current quantity. This is a
strategy-version change, not proof that the new feature improves ranking. Main screener consumers
now keep the 1h, 4h and 24h horizons separate. Missing OI no longer counts as nonnegative OI in the
breakout volume gate or absorption confirmation. Provider timestamps still need to enter the research
archive, and quantity versus notional features still require an out-of-sample comparison.

The adapter now aggregates hourly taker buy/sell volumes over complete 1h, 4h and 24h windows and
divides their sums. It excludes the still-open hour and rejects incomplete windows. Candle fallbacks
require consecutive closed hourly observations with usable tape fields; zero default fields cannot
establish coverage. The API exposes each horizon and the horizon behind the legacy ratio field.
Ratios use buy volume divided by sell volume, with neutral at 1.00; the absorption scorer converts
that ratio to buyer share before using its existing share thresholds. Missing funding and OI do not
earn confirmation points. These semantics have fixture and integration coverage.

Live USD-M 1h, 4h and weekly feeds require a recent closed bar (at most one interval plus five minutes
old). Derivatives snapshots require an exchange clock within five minutes of the local clock, with
one minute of future-clock tolerance. Native open weekly candles are excluded; daily and weekly
resampling emits only complete, consecutive UTC periods. This requires a correctly synchronized
host clock. It is not a full audit of every historical candle or macro provider's freshness.

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
tick-normalized levels; and one API/cron/CLI acceptance policy.

Acceptance: fixture tests for timestamps/gaps/units; one policy version used by all entry points;
formula/threshold contract tests; no missing data silently promoted to confirming evidence;
documented counts for universe, selected, complete, partial, rejected and published candidates.
Keep the four-hour schedule during migration; choose a faster loop only after load and usefulness
measurements. Do not implement the old 15-minute/8-hour specification by assumption.

Progress: timestamp-checked hourly OI, complete taker windows, closed/fresh bars, and a separate bounded
quote-anomaly Stage 2 probe lane are implemented locally with tests. Scan responses carry funnel
counts through the screening return boundary. The breakout scorer measures R:R after tick rounding,
rejects collapsed brackets and insufficient reward, and no longer reports an artificial 2.5R floor.
Core breakout/retest brackets now publish the same entry/stop anchor used for their R multiples.
Published R:R is measured rather than capped, with `risk_reward_target_index` identifying its target;
the existing conservative cap remains an internal scoring input. Target projections are still heuristic.
The dashboard prefers the published suggested entry over the alternate pullback entry, identifies
the R:R target, and no longer caps displayed R:R at 8. Published-contract chart levels retain their
original precision and are not rescaled when live prices move away; a new scan clears adapted levels.

All public entry points use `Runtime.crypto_screen`, which enforces the shared publication policy
before desk allocation. Lowering `min_score` cannot bypass its base quality requirements. Long R:R
must reach 2.00 and short R:R 1.50; the existing tier, checklist, model-EV and regime rules remain.
API and cron no longer apply competing filters or re-rank the result. Their responses and the CLI
identify policy `atlas-v2.7-data-contract-1`. Direct application-level screening can still request
diagnostic candidates with an explicit score threshold; that override is not exposed by the public
runtime. Model EV remains assumption-based and is not a calibrated profitability estimate.

Still open in this milestone: full rejection/input-availability records, archived policy/config/code
identity, truthful ILI inputs, VPVR parity, end-to-end COI propagation and measured full-universe load.
Request-weight logging is an estimate, not an enforced rate limiter. Empirical candidate recall also
remains open; neither more probes nor passing unit tests validates 24–48 hour top-gainer prediction.

Local verification checkpoint (2026-10-24): all 327 backend tests pass, mypy passes for 57 source files,
and the frontend production build passes. Desktop browser fixtures verify a breakout entry distinct
from its pullback, uncapped 9.50R, and unchanged published levels after a large chart-price gap and
timeframe/target-toggle interactions. No browser console errors or warnings were observed. Targeted
frontend lint has no errors but retains six existing warnings. The current mobile stylesheet hides
the chart and verification panel below 768px; mobile candidate-list rendering is checked, not full
mobile analysis functionality. That existing limitation needs a separate responsive-view change.

A bounded public-data smoke scan evaluated 25 selected contracts with zero fetch failures and ten
Stage 2 probes; none qualified at that observation. BTC's distinct OI/taker horizons were available
and non-degraded. This zero-result run does not exercise live candidate brackets; fixture tests cover
their arithmetic. It is not a full-universe load test or evidence of predictive performance.

### 2 — Snapshot archive and forward-outcome recorder

Implemented locally (2026-10-24): immutable normalized-input archives with SHA-256 manifests,
per-invocation scan receipts across API/CLI/cron, stage-level universe/selection/publication records,
source/config/package identities and offline production-path replay. The original eligible canonical
cohort is retained separately from curated candidates, including unselected/quote-less instruments.
Where a liquid canonical pair exists, the cohort uses that exact selection pair, so published USDC
contracts remain observable even when a wider-spread USDT contract had greater quoted volume.
Generic evaluator no-setup records do not claim an inner-factor diagnosis. Archive failures are
explicit without intentionally changing publication rules; failed scans retain partial inputs.
Metadata/serialization failures preserve screening and mark the recording incomplete/unavailable;
exhausted data fetches have explicit journal facts rather than a false strategy-rejection diagnosis.

The manual outcome worker measures exact complete-minute 24h/48h windows, stores per-symbol evidence
and append-only attempts, resumes compatible successful observations, and reports ranks plus missing
coverage. Gaps, invalid candles, provider failure and not-yet-due windows are not losses or zero returns.
Touch diagnostics acknowledge unknown intrabar ordering and are not simulated fills. Run limits refuse
oversized cohorts rather than silently select only successful candidates. No outcome job is scheduled
automatically, and no live full-cohort collection or predictive benchmark is claimed.

Verification: 372 backend tests pass; strict typing covers 64 source files; source and touched-test
lint/diff checks pass. Independent review identified four Important issues, each fixed with regression
tests before the green full-suite run. A final bounded five-contract real-data scan saved 22 normalized
calls and 1,491 stage facts for a 512-contract cohort, then replayed identically with HTTP-client
construction disabled. It published zero candidates; this verifies plumbing, not bracket performance
on live opportunities, top-gainer prediction, or full-universe provider capacity.

Release acceptance remains: controlled deployment, source-preserving backup/restore, capacity/retention
planning and measured full-cohort provider load. Historical data collection and empirical calibration
remain outstanding. See README command examples and the approved observation design/implementation plan.

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

### Prospective-rank implementation checkpoint (2026-10-25)

The approved rank-observation milestone adds manual `snapshot-ranks` and offline `benchmark-shadow`
commands, now integrated locally with changes kept uncommitted. It preserves the original cohort,
stores immutable raw/normalized evidence and exact report manifests, and compares frozen long sets
at the child's forward anchor. Fresh independent review found five Important issues; all five were
addressed with RED/GREEN regressions. Pre-integration verification passed 514 tests, strict source
typing and touched lint/format checks. Final combined-checkout verification is recorded in
`docs/superpowers/plans/2026-10-25-rank-observation-benchmark.md`; implementation is not deployment.
Sampling remains manual. Sparse observations cannot establish continuous absence or exact entry;
unknown initial states, already-inside candidates, missing outcomes and incomplete cohorts remain
separate. No calibrated probabilities, promotion decision or claim of predictive advantage follows.
Scheduling/load acceptance, dashboard presentation, backup/restore and chronological out-of-sample
evaluation/calibration remain subsequent work; the system is not fully validated by this milestone.

Primary sources establish API semantics and evaluation methods, not Atlas's effectiveness. Existing
repository experiments concern multiple older strategies and cannot establish the current screener's
edge. Historical macro/OI availability, delisted coverage, current production load and user-perceived
utility need direct measurement. This document distinguishes implemented migration work from the
remaining validation programme so future releases cannot accidentally claim evidence we do not have.

### Opt-in runner checkpoint (2026-10-26)

Session policy/archive/locks, idempotent rank ticks, bounded outcome passes,
frozen report inputs, explicit CLI supervision and offline health are locally
implemented. Tasks 1–6 passed 633 tests, strict typing and deployment-control checks.
The optional Compose profile and separate research-only deployment control passed
network-disabled synthetic container verification; frontend production build passed
with 11 lint warnings and no errors. This is local evidence, not cloud load acceptance.
The only approved cloud destination is the dedicated VM in project `alfa2-512717`;
the existing live API/Vercel deployment is outside mutation scope. End-to-end acceptance and
one fresh independent review are complete; all four Important findings were addressed with
regression tests. The latest full suite and exact backend CI checks passed 650 tests.
The final synthetic Linux amd64 report-capacity check completed 1,000 symbols and 577 snapshots
within the 256 MiB cap (reported peak RSS about 61.8 MiB); isolated packaging smoke also passed.
The approved observer traversal extension changes source fingerprints without weakening guards.
Local integration is verified: the combined checkout passed 650 tests, strict typing/source lint,
clean frontend install/audit/build and offline CLI checks. Runtime data, PDFs and unrelated edits
were preserved. Scoped publication/deployment, VM operational acceptance and explicit collection
activation remain gates. No predictive result or calibrated advantage is claimed.
