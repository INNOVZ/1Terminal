# Dual-track shadow discovery, without changing the published core

Status: approved by Jithin on 2026-10-24 ("please proceed as per implementation plan").
This document fixes the integration, budget and measurement boundaries for implementation.
The six-task observation plan is complete and must not be restarted.

Implementation checkpoint (2026-10-25): implemented and verified locally in the isolated worktree;
independent review found seven Important gaps, addressed in one regression-tested fix pass. No
deployment, Git publication or predictive-performance validation. See the implementation plan/ledger.

## Goal

Preserve the current live screener exactly while discovering and recording additional long setups
through a bounded, independently admitted research arm. Separate structural coils, positioning-led
setups and already-moving continuations. Make differences inspectable before changing any production
threshold, grade or 16-slot allocation. Finding future gainers is the research objective, not an
acceptance claim supported by implementation tests.

## Current architecture and constraints

The modular monolith already separates public-data adapters, domain calculations, application
screening, runtime orchestration and immutable observation storage. Preserve these boundaries.
`Runtime.run_crypto_scan` is the publication boundary; its compatibility wrapper returns a list.
API/CLI/cron consume per-run receipts. ScanRecord v1 and offline replay preserve normalized inputs,
stage facts and the eligible canonical cohort. The latest local baseline is 372 passing tests.

Current discovery selection is not the proposed independent Track B. The additional Stage 2 probes
only inspect already-selected instruments. The `ALL` evaluator does not enter active BREAKOUT mode.
The latter can evaluate active expansion but only after respecting its existing accumulation priority.
These are baseline behaviours to preserve, not silently repair inside this experiment.

No product code, dependency, existing archive, deployment, schedule or user scan file changes during
design. No trading keys, account endpoints, execution, grade rewrite or profitability claims later.

## Approaches and recommendation

1. **Separate opt-in shadow runner (recommended):** consume a fresh archived core scan, reuse its
   public inputs, and acquire a strictly bounded set of additional observations. Store a separate
   child research record. Core publication does not invoke or wait for the runner.
2. **Offline-only replay:** safest first comparison and useful for tests, but cannot discover tokens
   outside recorded deep-data coverage; an absent derivative input is not a negative signal.
3. **Change ALL/publication in place:** rejected for this milestone. It would change live eligibility,
   latency and desk competition before comparative evidence exists.

The recommendation applies the already agreed shadow-only direction. Exact budgets below are
engineering proposals, not historically calibrated trading parameters.

## Core preservation and vocabulary

Keep two independent dimensions rather than forcing every existing setup into one exclusive track:

- **Arm:** `core_baseline` versus `discovery_shadow`. Every current published opportunity, including
  shorts and funding-squeeze setups, remains in the baseline arm.
- **Setup family:** structural accumulation, positioning/squeeze, active expansion/continuation,
  distribution/reversal short, or explicitly classified legacy compatibility setup.

Explicitly inventory every emitted archetype and every SetupLabel, distinguishing aliases and unused
compatibility labels. Structural labels include COIL_SPRING_LONG, VCP_COMPRESSION_LONG and
RANGE_BOTTOM_ACCUMULATION_LONG. Positioning includes OI_ACCUMULATION_LONG,
SHORT_SQUEEZE_ABSORPTION_LONG and FUNDING_SQUEEZE_SPRING_LONG (WYCKOFF_SQUEEZE_SPRING is an alias).
Expansion includes BREAKOUT_LONG and MOMENTUM_EMERGING_LONG, plus the existing legacy expansion labels.
Preserve all four current classifier shorts: UTAD_DIVERGENCE_SHORT, EXHAUSTION_REVERSAL_SHORT,
DISTRIBUTION_SHORT and VCP_DISTRIBUTION_SHORT. Classify COIL_BREAKDOWN_SHORT and other enum-only legacy
labels explicitly; do not claim they are active simply because they exist in the enum.

Family membership is diagnostic metadata. It never changes a gate, score, grade, priority, short
eligibility or direction. A positioning setup is not necessarily early, and a structural setup is
not necessarily a future winner. Keep the current published result and hypothetical levels unchanged.

## Opt-in admission outside the current deep shortlist

Proposed command: `atlas shadow-scan --scan-id CORE_UUID --archive-dir PATH`.
No API refresh, background scheduler or automatic child scan is added. Require a complete, supported,
source-compatible production-quality ALL parent scan; reject stale/incomplete or diagnostic parents.
Parent completion must be no more than 300 seconds old at admission, with start/end/individual input
times retained so this is not mistaken for proof every input is fresh. Reapply existing adapter/bar
freshness and explicit input-availability checks on reused observations; missing inputs remain unknown.

Use the parent's recorded eligible canonical cohort and quotes to build the cheap admission pool.
Choose the same canonical liquid pair as the core. Never broaden core eligibility or query private
data. Shadow deep-data hygiene is quote volume >= $1.5m and spread <= 25bps, matching current probes.
Do not use any subsequent return, outcome or updated universe to admit a token retrospectively.

Initial maximum is **15 unique additional tokens**, partitioned into up to:

- Five quote-anomaly candidates outside the parent's selected deep universe. Rank negative funding
  and positive basis evidence using a versioned, recorded admission rule; missing evidence earns none.
- Five positive price-momentum controls across the eligible pool, excluding core-published tokens.
  Rank observed 24h price change, with deterministic volume/symbol tie-breaks. These controls are not
  called pre-pump discoveries merely because they pass a numerical momentum rank.
- Five deterministic coverage probes outside the parent's selected universe. Rank by a stable hash
  of parent scan ID and symbol; log `coverage_probe`, not invented OI evidence. These permit observation
  of OI-led tokens with no quote anomaly. They are exploratory samples, not a full-universe OI scan.

Deduplicate across admission buckets deterministically. Do not backfill a bucket by weakening its
conditions or changing baseline allocation. A limit below 15 scales bucket admission deterministically;
freeze that rule and included/excluded IDs in the experiment configuration. Never exceed the run cap.

## Data, qualification and request boundary

Fetch missing closed candles and explicit derivatives for admitted tokens **before** applying the
existing per-symbol evaluator. This avoids requiring an OI-dependent setup to survive without OI.
Reuse recorded candle series only when their interval/count/cutoff and freshness are suitable.
Use recorded BTC/macro context only with explicit provenance and availability; do not synthesize defaults.

Review refinements: a macro context requires at least one observed component, a context collection age
<=300 seconds and all supplied component observations <=48 hours old; future skew is limited to five
seconds. Missing optional components stay absent. These versioned conservative research defaults are
not market-calibrated thresholds. A stale/empty macro context or degraded derivative telemetry marks
the child partial rather than becoming a complete negative observation. Derivative observations must
be <=60 seconds old, with the same five-second future tolerance. Source observations are archived.
Successful empty/short weekly history retains the existing four-hour-to-weekly resampling fallback;
provider failures remain failures. Record that weekly evidence is derived, not directly observed.

Evaluate with the existing per-symbol BREAKOUT path and LONG direction, preserving its existing
accumulation-first behaviour, then use the shared publication-quality helper to distinguish qualified
shadow setups from diagnostic/rejected results. No new 18% return ceiling, 0.28 bandwidth gate,
anti-zombie rule, assumed probability, score weight or 7/5/4 allocation is adopted in v1.
This is a bounded admission/measurement experiment using existing scoring, not the original prompt
implemented verbatim. Any later alternative thresholds require a separately versioned experiment.

The runner owns separate public-only clients, tape and journal. Proposed safety caps: 15 tokens,
150 actual HTTP attempts (including retries), and a 90-second child-run deadline. Enforce admission
before fetching and attempt accounting before each transport attempt; a nominal endpoint-call count
that excludes retries is insufficient. Respect exchange weight/backoff headers and provider limits;
the attempt cap is not a claim of a particular request weight or zero failures. Budget exhaustion,
timeouts and partial coverage have explicit terminal states. No extra core requests are permitted.
No concurrent automatic production rollout is implied; measure load before any scheduling decision.

## Timing, labels and fair comparison

Keep parent decision time, child admission time, provider observation times and child evaluation time
separate. A child uses newer information and may start after the parent; label this comparison
`staggered_shadow`, record the lag, and never describe it as a simultaneous paired outperformance test.
Reuse the original eligible cohort for denominator/ranking, but anchor child forward observations at
the first full minute at/after its own completion. Parent outcomes retain their own original anchor.
Do not compare unequal horizons without displaying those timing differences.

Retain logical snapshot request/arrival/finish times and available provider collection start/finish
times, including failed acquisition. A failed acquisition has no invented arrival timestamp. Preserve
the outer terminal cause and phase separately from the cancelled operation, so deadline expiry and
external cancellation reproduce faithfully offline, including interruption during evaluation.

Store two independent labels on each candidate:

- `price_phase`: pre-trigger, at/above trigger, or unknown, using the hypothetical direction/trigger.
- `decision_gainer_status`: already within the eligible cohort's observed 24h top-10 (including ties),
  outside that group, or unknown when ranking coverage is incomplete. This is not Binance UI membership.

Parent quotes are admission evidence only, not child decision-time rank evidence. After deep-data
acquisition, collect one fresh public `GET /fapi/v1/ticker/24hr` whole-market snapshot and retain its
raw `lastPrice`, `priceChangePercent` and `closeTime`, together with request/arrival timestamps.
Use this same snapshot for both price-versus-trigger classification and original-cohort ranking;
never combine an old parent rank with a newer child price. Binance documents this endpoint as rolling
24h statistics, with all symbols returned when symbol is omitted; the bulk request has weight 40
([official market-data reference](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data)).
Its transport attempts, retries and exchange weight belong to the child budget, not extra free calls.

Proposed classification freshness defaults: each cohort observation must be no more than 60 seconds
old, no more than five seconds ahead of the host classification clock, and cohort close times must
span no more than 60 seconds. These are conservative experiment settings, not provider guarantees.
Require valid finite returns and positive prices for every original-cohort member before assigning
a known rank. Missing symbols, malformed values, stale data, failed requests or exhausted budgets
make gainer status unknown; never substitute zero returns, parent ranks or a reduced denominator.
Keep independently valid price-phase evidence, but without a known contemporaneous rank it cannot
earn an early label. Record the snapshot cutoff, coverage and classification time for replay.

`early_candidate` is a hypothesis label only when pre-trigger and observed outside that decision-time
top group. Already-moving candidates remain continuation/expansion observations. Unknown data cannot
earn an early label. Preserve the raw rank, coverage, values and versioned classification rules.

The existing fixed 24h/48h observer can measure endpoint cohort ranks and price paths; it cannot prove
when a token first entered a rolling gainers leaderboard. v1 reports qualified-only counts, overlap,
additional discoveries, missed/rejected reasons, observed coverage and explicitly staggered outcomes.
No 1–12h lead-time claim or promotion recommendation is emitted. Prospective gainer-rank snapshots
and a matched-cutoff comparative benchmark remain a subsequent measurement milestone.

## Storage and failure isolation

Use a separate versioned ShadowRunRecord under `data/research/shadow/SHADOW_UUID/`, containing the
parent ID/checksum, policy/config/source identity, admission pool/decisions, reused-input references,
new normalized calls, limits/consumption, timing/coverage, all evaluated setups and rejection reasons.
Keep core ScanRecord v1 and existing archives immutable and readable; do not rewrite them or mix
shadow candidates into core `opportunities`, scan counts, latest-results JSON, watchlist or desk slots.

Offline shadow replay requires recorded inputs and matching identity; no network fallback. Provider,
budget, cancellation, serialization and archive failures are research-run errors, not reasons to
change the core result. A failed child cannot make its parent incomplete. Store incomplete child
evidence explicitly; do not score an incomplete child as a complete 15-token experiment.

Extend observation tooling through an explicit shadow-record entry point, not by pretending a shadow
record is a production ScanRecord. It uses the same pure price-path evaluator, original cohort and
missing-data semantics; cached parent outcomes must not be reused at a different child anchor.

## Verification and completion boundary

1. Freeze representative input tapes and verify identical ordered serialized core decisions/counts/
   brackets, with the shadow module available, disabled, failing and budget-exhausted. The normal
   runtime/API/cron must never invoke it. Cover every active archetype, including quiet coils,
   funding squeeze and all four short classifiers; inventory enum-only aliases separately.
2. Admission tests prove whole eligible-pool coverage, outside-selected probes, deterministic buckets,
   no duplicate assets, no future-outcome inputs, safe IDs, strict parent age and explicit missingness.
3. Mock-transport tests prove caps include retries, deadlines terminate, rejected/budgeted-out tokens
   are accounted for, and no account endpoint, automatic scheduler or whole-core rescan is used.
4. Archived/replayed shadow fixtures reproduce admissions, labels, rejections and qualified results
   offline; checksums prove parent bytes unchanged after successful and failed child runs.
5. Counterexamples prove a +18% already-ranked token is not labelled pre-pump, missing returns do not
   become zero, and incomplete rank coverage does not become a known outside-top-10 classification.
   A token entering the top group between parent and child must not inherit an obsolete outside rank;
   stale/missing final snapshots, excessive provider-clock skew and budget exhaustion remain unknown.
6. Outcome tests prove separate anchors, no parent-cache leakage, preserved original cohort coverage,
   short-baseline retention and ambiguity handling. No leaderboard lead time or trading P&L invented.
7. Run full pytest, strict source typing, touched-file lint and diff checks, followed by a fresh
   read-only implementation review. Retain a task ledger and exact remaining work across interruptions.

Production scheduling, dashboard integration, threshold promotion, calibrated probabilities,
full-universe depth expansion, stronger ILI/COI/VPVR semantics and deployment are not completed by
this experiment. They remain visible follow-on milestones, not silently folded into core changes.
