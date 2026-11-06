# Local screener archives and forward observations

Status: approach and implementation plan approved by Jithin on 2026-10-24; native execution in progress.

## Intent and boundaries

Determine whether Atlas identifies opportunities before their move, and where future gainers leave
the funnel. Preserve point-in-time evidence rather than infer success from current charts.
Keep scores, thresholds, ranking, desk caps, the four-hour schedule and published brackets unchanged.
No account endpoints, trading credentials, simulated fills, profitability claims, new dependencies,
external infrastructure, automatic deployment or Git publication.

## Approaches

- Local immutable files with checksums: selected by Jithin. Fits the existing mounted data directory
  and roadmap; inspectable and replayable without a new service. Requires disk-space monitoring and
  backups. Keep the research store separate from operational watchlist SQLite.
- Managed database/object storage: deferred. Useful at greater volume, but introduces credentials,
  infrastructure and deployment changes before archive volume is measured.

The following detailed measurement and storage decisions are the implementation proposal, not
existing capabilities. These are engineering defaults for the approved approach.

## Observation contract

Every actual runtime scan receives a UUID scan ID at invocation, UTC start/end timestamps and a
terminal success/failure state. A retry of archive persistence reuses that ID; a genuinely new scan
gets a new ID even when it produces identical candidates. Cache reads are not new scans.

Archive each scan's normalized, typed market-data port responses: instruments, quotes, all requested
candle series, derivatives and macro context, including missing values, failures and call parameters.
Record repeated calls separately, preserving invocation order per request key. Decimal values use
exact strings; aware timestamps use UTC. This is a normalized-input archive, not raw HTTP bodies.
Never record headers, tokens, environment files or arbitrary settings dumps. The macro serialization
must preserve real optional readings, not use its display-oriented `as_dict()` defaults.

Store policy version, a whitelist of effective screening configuration/request arguments, Python and
package versions, source-file content hashes and Git revision if available. Dirty checkouts must not
be identified by commit SHA alone. A settings/code hash is an identifier, not a copy of executable code.

Keep all returned instruments and eligibility decisions, including excluded, delisted and unselected
contracts, rather than archive only the curated 16 candidates. Instrument metadata is preserved even
when later exchange listings no longer contain that symbol. Quote-less observations remain missing.
Canonical cohort selection prefers the scanner's liquid pair for each base; retain an eligible
fallback only when no liquid pair exists. This guarantees a published USDC pair is not lost merely
because an ineligible higher-volume USDT pair existed before liquidity filtering.

The decision journal distinguishes universe exclusion, missing quote, liquidity rejection, pair
deduplication, selection limit, Stage 1 no setup/fetch failure, Stage 2 non-admission/failed enrichment,
publication rejection reasons, final deduplication/desk caps and publication. Generic `no_setup` means
only that the evaluator produced no setup: do not invent an inner gate diagnosis. Record candidates
by symbol, direction and setup label, before and after curation. Detailed inner-factor attribution
can be extended later without changing this schema's stage-level meaning.

## Immutable storage and availability

Use `data/research/scans/<scan-id>/` for payload plus manifest; ignore generated research data in Git.
Write into a unique staging directory on the same filesystem, flush/fsync files, then publish the
complete directory atomically. Readers ignore staging directories. A manifest records schema version,
payload SHA-256 and byte size; reads verify both before parsing/replay. Existing identical scan IDs
are idempotent; different content under the same ID raises an explicit conflict, never overwrites.
Concurrent threads/processes must not replace another writer's complete archive. No mutable global
manifest is necessary: discover only complete scan directories.

Archiving is an observation side effect, not a selection rule. An archive write failure must not
manufacture an empty successful scan or change candidates. Return an explicit archive status/error
with that scan's receipt and log the failure; do not claim durable recording. Partial input captures
from failed scans are labeled failed/incomplete and are not valid complete replay samples.

Introduce an immutable result envelope so API, CLI and cron receive the receipt belonging to their
own invocation, not a race-prone `last_scan_*` global. Keep the existing list-returning runtime method
as a compatibility wrapper. Existing latest-results/watchlist persistence stays independent.

## Replay contract

Replay the same production screener with recorded market/macro ports and exact request/configuration.
No network access is permitted; missing recorded calls fail explicitly rather than fetch current data.
Compare ordered serialized published decisions, pre-curation decisions and funnel counts. Reject
unsupported schema or mismatched source/configuration identity rather than call a drifted result an
identical historical replay. An empty successful scan is a valid reproducible result.

## Separate forward observations

Write outcomes under `data/research/outcomes/<scan-id>/<horizon-hours>/`, independently of input files.
Use the original eligible, canonical base-asset universe as the comparison cohort; keep excluded and
noncanonical contract records for audit but do not double-count them in gainers rankings. Publish
candidate-only measurements early only if clearly labeled insufficient for missed-gainer recall.

Primary horizons are 24 and 48 hours. Baseline is the open of the first UTC one-minute interval whose
start is at or after scan completion, not an earlier quote or a hypothetical fill. Use complete public
one-minute OHLCV data over the subsequent exact horizon, with end price the final minute's close.
Record anchor time and baseline price. Compute raw price return, direction-adjusted candidate return,
and high/low excursion from that baseline. These are signal observations, not realized R or P&L.
Measure suggested-entry/stop/target touches separately; a same-bar stop/target or pre-entry ordering
is ambiguous, never an assumed winning fill. Do not turn touches into trading returns.

Not-yet-due, missing/invalid baseline, gaps, delisting/unavailable history and provider failure have
explicit statuses, not zero returns or losses. Reject duplicate/conflicting/nonfinite/impossible OHLC
bars and exclude partial/pre-anchor bars. No interpolation. A horizon is complete only with all its
expected minutes. Rank observed raw returns within the original eligible canonical cohort and report
coverage/missing counts; incomplete coverage cannot claim exact full-cohort top-gainer recall.
Do not equate fixed 24h/48h cohort return with Binance's changing rolling top-gainers leaderboard.

Outcome jobs are manual CLI commands initially: one scan and horizon per invocation, a bounded symbol
limit and request pacing. If the requested cohort exceeds the run limit, refuse explicitly rather than
silently truncate it. Persist per-symbol evidence/results idempotently so interrupted jobs can resume.
Retry missing observations append-only with attempt identity; earlier evidence is never rewritten.
Declare any aggregate's attempt selection and coverage. Automatic scheduling is a later operational
decision after request volume is measured; do not add another production scanner implicitly.

## Acceptance and delivery

1. Real filesystem tests cover round-trip fidelity, checksums, idempotency/conflicts, corruption,
   interrupted writes and concurrent processes; no user runtime data is touched.
2. Recorded/replayed public screener fixtures produce identical decisions and counts without network;
   include empty scans, provider failures and an eligible token omitted by desk allocation.
3. Integration tests prove API/CLI/cron publish the same scan ID/status for their own result and that
   archive failures do not change candidates. Selection before/after observation must be identical.
4. Pure outcome tests cover exact 24h/48h timing, gaps, incomplete bars, shorts, precision, ambiguity,
   cohort ties/coverage and retries. Input archive checksums remain unchanged after outcome writes.
5. Worker tests exercise a public-data-only fake transport and resume behavior, never live accounts.
6. Run the full backend suite, mypy, touched-file Ruff and diff checks. Report inherited warnings and
   any gaps. No predictive-performance or production-readiness claim follows from passing tests.

Retention, disk capacity, measured full-cohort request cost, deployment/mount permissions and backup
restore remain release acceptance work. No deletion policy or deployment change is implied here.
