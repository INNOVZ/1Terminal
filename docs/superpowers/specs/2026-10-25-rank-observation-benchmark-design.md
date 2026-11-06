# Prospective rank observations and honest matched-anchor benchmarks

Status: approved, implemented and locally integrated uncommitted on 2026-10-25; verification passes.
Jithin selected rank snapshots/benchmarking before dashboard integration. The observation and shadow
milestones are already integrated locally and remain uncommitted; do not rebuild them.
Fresh builder check completed: nothing important left to clarify before implementation planning.
Approved engineering defaults include manual-only collection, three attempts/15s,
five-minute sampling target/ten-minute gap threshold, and matched forward anchors with staggered
decision times disclosed. Six implementation tasks and the fresh independent review/fix pass are
complete: 514 tests pass, strict source typing and touched lint/format pass. No deployment or claim
of predictive advantage. The implementation plan/ledger records local integration status.

## Intent and boundary

Answer the next research question: were qualified candidates observed outside the cohort's rolling
top-rank group before they were subsequently observed inside it? Also compare their subsequent price
paths with core longs and a simple price-momentum reference at one shared forward anchor.

The current fixed 24h/48h observer cannot establish rolling leaderboard membership between endpoints.
The existing child decision snapshot supplies initial rolling-rank evidence, but the parent Quote
codec drops provider closeTime and can contain legacy zero defaults. Do not invent parent-time ranks.
Core and child decisions use different information times; a shared forward anchor does not fix that.

Keep live publication, all long/short families, scores, grades, allocation, core cadence, data schemas
and current API/cron unchanged. No executor, credentials, probability calibration or promotion.
This milestone produces measurement plumbing and reproducible descriptive reports, not market edge.

## Approaches

1. Manual append-only rank capture plus offline reporting: recommended first slice, following current
   research CLI/storage boundaries. Collection costs and sampling gaps are visible before scheduling.
2. Historical candle reconstruction alone: useful for forward prices, but not an exact replacement
   for point-in-time exchange rolling statistics or an originally eligible universe.
3. Automatic collector/dashboard first: deferred because it adds service/schedule/load commitments
   without yet establishing this measurement contract.

Jithin chose measurement first. Keeping collection manual is the engineering recommendation for this
slice; automatic scheduling remains a later, separately reviewed operational change.

## Two explicit commands

- `atlas snapshot-ranks --scan-id PARENT_UUID --archive-dir PATH`: one bounded public snapshot.
- `atlas benchmark-shadow --shadow-id CHILD_UUID --horizon-hours 24|48 --archive-dir PATH`: fully
  offline report, using saved rank samples and existing child-anchored outcome attempts only.

The collector reads a supported complete parent archive and its original canonical eligible cohort.
It MUST allow old parents: observation is intentionally prospective over their following 24/48 hours.
Do not apply shadow admission's 300-second parent-age rule or require current scoring-source identity;
no old scoring code is executed. Verify the parent checksum, supported schema and canonical cohort.
Optional child evidence used by a report must reference exactly that parent checksum/cohort.
No universe refresh, new eligible symbols, core rescan, per-symbol OI/depth calls or account requests.

## Public-data acquisition and limits

Use the public USD-M whole-market `GET /fapi/v1/ticker/24hr` response, retaining raw rows and
request/arrival/finish times, provider openTime/closeTime, lastPrice, priceChangePercent and quoteVolume.
Binance describes this as rolling 24h data; omitting symbol returns all symbols and has weight40
([official API reference](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data)).
This is cohort ranking over USD-M pairs, not Binance Spot or UI leaderboard membership.

Engineering caps: cohort <=1000, at most three actual HTTP attempts including retries, and
15 seconds per command including backoff. Reuse the proven guarded transport without changing normal
production-client defaults. Record actual attempt consumption and exchange-weight headers; do not
equate attempt count with weight or promise that multiple processes share a global limiter.

Reuse the existing pure classification rules where appropriate: finite returns, positive prices,
no duplicate canonical rows, provider age <=60 seconds, future skew <=5 seconds and cross-cohort
provider timestamp span <=60 seconds. Ranking is complete only when every original cohort member is
valid. Missing/delisted members, malformed/nonfinite fields, timeouts and budget failures remain
explicit unknown/partial observations, never a reduced denominator or zero return.

The ranking target is rank <=10, including ties, using 1 + number of strictly larger returns. This
group may include negative returns during a broad decline: report sign separately and do not call
all group members profitable gainers. Pair-denominated percentage changes remain pair-denominated.

## Immutable storage and reproducibility

Introduce a separate versioned RankSnapshotRecord, not a ScanRecord or ShadowRunRecord rewrite.
Store checksummed immutable directories under `ranks/PARENT_UUID/SNAPSHOT_UUID/`. Retain parent ID/
checksum, exact cohort and cohort hash, collector source/policy/config versions, raw evidence,
normalized ranks, coverage/reasons, aware UTC times, request accounting and terminal state.

Each invocation has its own UUID; identical ID/content is idempotent, conflicting content fails.
Failed/partial attempts remain inspectable and cannot become successful samples. No writes to latest
scan JSON, existing scan/child records, watchlists, core counts or outcome caches. Existing archives
remain readable. Offline recalculation of normalized ranks must match saved evidence without network.

Store report manifests under `benchmarks/PARENT_UUID/REPORT_UUID/`, referencing exact child/parent,
rank-snapshot and outcome-attempt IDs/checksums plus report policy/source version. Repeating with
the same frozen input set must reproduce the same report content apart from report identity/time.
Malformed/unsupported input records fail explicitly; missing measurements produce unavailable sections.

## Sampling, first-observed membership and missingness

Research sampling target is five minutes with a ten-minute maximum acceptable gap. These
are engineering defaults, not evidence of a suitable prediction horizon or a sample-size threshold.
v1 does not create a repeating process: each command captures once. A future authorized scheduler
may invoke it. Report actual sample counts, spacing, unknown attempts and boundary coverage.

Sort samples by observed arrival time and preserve provider cutoffs. Exclude future arrivals beyond
the report's as-of cutoff; report out-of-order/nonadvancing provider cutoffs instead of silently using
them as new independent samples. Duplicate equivalent observations add no sampling coverage.
An incomplete sample is unknown, even if the candidate's own row exists.

For each qualified child long, recalculate initial membership from its saved final decision snapshot
using the supported rank policy, never from old parent quotes or a stamped early label alone. Retain
the initial provider/arrival/classification timestamps and child completion separately.
Initial state is outside, inside/already-moving, or unknown. Only known-outside candidates can be
described as subsequently observed entering the group. Unknown initial state cannot become early.

Report first observed inside-group sample after the child's forward anchor, its timestamp/cutoffs,
lag from child completion and the previous complete observed-outside sample if one exists. Name this
`first_observed_membership`, not actual first entry, breakout time or guaranteed lead time. The token
may have entered and left between samples, including before the previous outside observation.
Even ideal five-minute sampling cannot establish continuous absence or exact entry time.

Use separate states for observed membership, not observed in sufficient samples, insufficient
sampling, not due, unavailable initial evidence and already inside. Sufficient sampling requires a
complete sample within ten minutes after the anchor and within ten minutes before horizon end, and
no gap >ten minutes across those boundaries. These rules only certify the declared sampled target.
Do not label an unobserved/insufficient case a failed prediction. Always show original denominators,
initial-state partitions and follow-up missingness; do not silently report only successful tokens.

## Matched-forward-anchor comparison, not matched information

Use the child's existing outcome anchor (first full minute at/after child completion) for EVERY
reported price-path arm. Read compatible existing child outcome attempts for the whole original
cohort; do not reuse parent-anchored outcomes or fetch missing data in an offline report. Missing/
incompatible paths are unavailable, and partial coverage stays visible. 24h and48h are separate reports.

Freeze three descriptive sets without modifying the live desk:

- Core published longs, in their archived order. Retain all original shorts in the record but report
  them separately; upside leaderboard membership is not a valid short success criterion.
- All qualified child longs, in archived order. Rejected and unselected symbols remain inspectable.
- A simple five-symbol momentum reference derived from the child's saved contemporaneous whole-cohort
  ticker response: outside the initial top10, positive 24h return, quoteVolume >=$1.5m; descending return,
  then descending volume, then symbol. Missing/malformed volume means this reference is unavailable,
  not zero. This is volume-filtered momentum, not the core's full spread/quality policy.

Report exact set sizes/overlap, original-cohort coverage, raw forward returns, observed membership
counts and explicit unknown counts. A fixed top-five diagnostic is allowed only where an arm has at
least five frozen members; otherwise mark insufficient candidates rather than changing k retrospectively.
No inferential significance, calibrated probabilities, threshold optimization or promotion verdict.

Label comparisons `matched_forward_anchor_staggered_decisions`, show parent/child lag, and state that
core decisions used older information. The momentum reference uses the child observation cutoff.
This is a descriptive control, not a causal proof that the child improves the core. Fixed-forward
cohort ranks and rolling-24h membership are distinct metrics and must never be substituted for one another.

## Verification and completion boundary

- Core parity/source checks: no changes to scoring, strategy families, shared publication or runtime/
  API/cron invocation; existing 443 tests remain green before new acceptance tests.
- Collector: actual retries/deadline/account denial, all-cohort coverage, stale/future/duplicate rows,
  old-parent acceptance, immutable parent bytes, failed-attempt storage and no latest-results writes.
- Storage/recalculation: schema checks, safe IDs, Decimal/aware-time preservation, conflicting writes,
  checksums and exact normalized rank reconstruction offline.
- Timeline: already-inside, unknown initial state, ties, missing/delisted members, intermittent gaps,
  duplicate/nonadvancing samples, later arrivals, horizon boundaries and possible unobserved crossings.
- Benchmark: same child anchor, incompatible parent caches rejected, missing paths retained, short-side
  separation, frozen momentum ties/missing volume, unequal set sizes and distinct rolling/forward ranks.
- End-to-end fixture with a real qualified child and a rejection, repeated prospective rank samples,
  immutable archives and offline report reproduction. No network construction during reporting.
- Full suite, strict typing, touched lint/diff checks and fresh independent implementation review.

Still out of scope: automatic collection, dashboard, deployment/load acceptance, backup/restore,
ILI/COI/VPVR improvements, full historical model training, probability calibration and execution.
These remain roadmap items; building measurement does not mark the whole system complete.
