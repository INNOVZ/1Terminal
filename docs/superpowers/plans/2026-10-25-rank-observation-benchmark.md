# Prospective Rank Observations and Benchmarks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure subsequently observed rolling-rank membership and compare frozen candidate sets at the same forward-price anchor, without changing the live screener.

**Architecture:** One manual collector saves immutable whole-cohort public ticker evidence. Pure normalization and timeline functions feed an offline benchmark service using existing child-anchored outcome archives. Separate rank/report namespaces preserve the completed observation and shadow implementation.

**Tech Stack:** Existing Python 3.12+, dataclasses, Decimal, asyncio, httpx, pytest, mypy, Ruff and checksummed local archives; no new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-25-rank-observation-benchmark-design.md`

## Global Constraints

- Keep live publication, all long/short families, scores, grades, allocation, core cadence, data schemas and current API/cron unchanged.
- No executor, credentials, probability calibration or promotion. No automatic collection, dashboard or deployment.
- Commands: `atlas snapshot-ranks --scan-id PARENT_UUID --archive-dir PATH` and `atlas benchmark-shadow --shadow-id CHILD_UUID --horizon-hours 24|48 --archive-dir PATH`.
- Original canonical cohort only, <=1000 members; complete supported parents may be old. No shadow parent-age or current scoring-source admission gate.
- At most three actual HTTP attempts including retries and 15 seconds including backoff; public bulk USD-M ticker request only.
- Snapshot age <=60 seconds, future skew <=5 seconds, cohort provider timestamp span <=60 seconds.
- Competition rank = 1 + number of strictly larger returns; rank <=10 includes ties. Missing evidence never becomes zero or a smaller denominator.
- Five-minute sampling target; maximum acceptable gap ten minutes. These are engineering defaults, not predictive evidence.
- All price-path arms use `outcome_anchor(child.completed_at)`; 24h and 48h reports remain separate.
- Momentum reference: five symbols, outside initial top10, positive return, quoteVolume >=1500000; descending return, descending volume, then symbol.
- Label comparisons `matched_forward_anchor_staggered_decisions`; do not imply matched information times, causal benefit or exact breakout/entry time.
- Preserve uncommitted prerequisites, unrelated frontend changes and latest scan data. No commit, push or deployment.

## Review Focus

- A delisted, duplicate or malformed cohort row must prevent complete ranking, even when the candidate row is valid (Tasks 1, 4).
- Retries/backoff/deadline expiry must not bypass actual-attempt limits or erase failed evidence; concurrent invocations must have independent budgets (Task 3).
- Equivalent/nonadvancing samples and samples arriving after the horizon/as-of must not manufacture sampling coverage or early discovery (Task 4).
- Wrong-parent/anchor/source outcomes and missing momentum volume must remain unavailable, not silently select favorable alternatives (Task 5).
- Corrupted archives, conflicting IDs and report input drift must fail closed while identical frozen evidence reproduces the report without HTTP construction (Tasks 2, 5, 6).

## Execution checkpoint

Design and implementation plan approved on 2026-10-25. All six tasks and the independent review/fix pass are complete locally.
Native execution is retained: implement task-by-task, then fresh independent implementation review.
Reuse the existing isolated worktree after reconciling its prerequisites with the integrated checkout; do not create a clean checkout that omits uncommitted observation/shadow code. Follow the worktree skill before writes there.
Keep each red/green command, changed-file inventory and review result in this plan. User preference overrides commit steps: leave changes uncommitted and integrate locally only after verification.
Previous checkpoint: 443 tests passed; this is historical evidence, not a fresh run for this milestone.

## File and interface map

All source paths below are under `src/algotrader/`; test paths are under `tests/`.

- `domain/rank_observations.py`: frozen `RankPolicy`, `RankSnapshotRecord`, `BenchmarkRecord` and pure normalization. `RankPolicy` owns the limits above plus schema/policy version; no core schema edits.
- `infrastructure/rank_archive.py`: `LocalRankArchive(root: Path)` composes existing atomic checksummed `LocalScanArchive` storage. Use versioned dictionary envelopes, as `LocalShadowArchive` does, rather than adding dataclasses to the core codec registry.
- `application/rank_collector.py`: bounded one-shot acquisition with injected market factory, UTC clock and monotonic clock; no scoring/admission execution.
- `domain/rank_timeline.py`: pure child-initial-state and follow-up sampling interpretation.
- `domain/shadow_benchmark.py`: pure frozen arms, descriptive aggregation and separate rolling/forward metrics.
- `application/shadow_benchmark.py`: offline identity/compatibility selection, manifest construction and immutable report writing.
- `cli.py`: two manual command parsers and dispatches only; `README.md`, roadmap and this plan: usage, limitations and verification checkpoint.
- New tests: `test_rank_observations.py`, `test_rank_archive.py`, `test_rank_collector.py`, `test_rank_timeline.py`, `test_shadow_benchmark.py`, `test_rank_acceptance.py`; extend `test_shadow_cli.py` for the manual commands.

Shared types: `Rows = tuple[dict[str, Any], ...]`; cohort is `tuple[Instrument, ...]`. Structured dictionaries follow the existing research-envelope convention; schema validation rejects malformed loaded records. All stored times are aware UTC; raw numeric evidence is retained and normalized arithmetic uses Decimal.

### Task 1: Pure normalization and research envelopes

**Files:** Create `domain/rank_observations.py`; test `tests/test_rank_observations.py`.

**Interfaces:**
- Consume `classify_shadow(cohort, candidates, rows, observed_at, policy) -> ClassificationReport` with one synthetic candidate per cohort member and explicit compatible freshness settings. Do not change existing classification behavior.
- Produce `normalize_ranks(cohort: tuple[Instrument, ...], rows: Rows, observed_at: datetime, policy: RankPolicy) -> dict[str, Any]` with `complete`, `coverage`, `members` and explicit reasons. Each member retains symbol, Decimal return/price, provider cutoffs, nullable rank and return sign; incomplete coverage gives no known cohort ranks.
- `RankSnapshotRecord`: snapshot/parent IDs, parent checksum, cohort/hash, policy/source identity, raw rows, normalized result, request/arrival/completion times, accounting, status/error, schema version 1.
- `BenchmarkRecord`: report/parent/child IDs, as-of/creation time, horizon, policy/source identity, exact input manifest and report payload, schema version 1.

- [x] Write `test_competition_rank_and_whole_cohort_unknown`: equal returns share rank; the tenth-place tie is included; `missing_member.complete is False` and every member rank is unknown, including a valid candidate. Add duplicate, NaN/Infinity/bool, nonpositive price, missing/delisted, negative-return top10 and noncanonical cohort cases.
- [x] Run `uv run pytest tests/test_rank_observations.py -q`; expect failure because the new module does not exist.
- [x] Implement the interfaces and validated policy caps. Pin age/future/span boundaries with assertions: 60/5/60 seconds accepted, one millisecond beyond rejected; preserve raw openTime/closeTime without inventing missing values. Normalized completeness uses rank-required fields; volume validity is separately explicit for the reference arm.
- [x] Run the new tests plus `tests/test_shadow_discovery.py`; require all pass and demonstrate existing classifier parity.
- [x] Record red/green evidence and review the diff; leave uncommitted.

### Task 2: Immutable rank/report storage and offline reconstruction

**Files:** Create `infrastructure/rank_archive.py`; test `tests/test_rank_archive.py`.

**Interfaces:**
- Consume Task 1 envelopes, `normalize_ranks`, `canonical_bytes`, `validated_id` and existing atomic archive primitives.
- Produce `LocalRankArchive.write_snapshot(record: RankSnapshotRecord) -> ScanReceipt`, `read_snapshot(parent_id: str, snapshot_id: str) -> RankSnapshotRecord`, `snapshots(parent_id: str) -> tuple[RankSnapshotRecord, ...]`, `write_report(record: BenchmarkRecord) -> ScanReceipt`, `read_report(parent_id: str, report_id: str) -> BenchmarkRecord`.
- Namespaces: `ranks/PARENT_UUID/SNAPSHOT_UUID/` and `benchmarks/PARENT_UUID/REPORT_UUID/`. Checksums in report manifests identify the canonical archived payload, not a mutable summary.

- [x] Write `test_rank_archive_conflicts_and_offline_reconstruction`: same ID/content is idempotent, changed content raises `ArchiveConflict`, altered bytes raise `ArchiveIntegrityError`; recomputing normalized evidence equals the saved value with network construction forbidden.
- [x] Run `uv run pytest tests/test_rank_archive.py -q`; expect new archive imports to fail.
- [x] Implement versioned envelopes and strict read validation, including canonical UUIDs/path safety, cohort hash, schema, aware times and normalized/raw consistency. Unknown versions and internally inconsistent records fail explicitly. Failed/partial records round-trip as failed/partial, never as complete samples.
- [x] Run the new tests and `tests/test_scan_archive.py tests/test_shadow_archive.py`; assert Decimal/time preservation, interrupted atomic writes leave no readable complete record, concurrent distinct IDs survive, and old archives remain readable.
- [x] Record evidence and review storage boundaries; leave uncommitted.

### Task 3: Bounded manual rank collector

**Files:** Create `application/rank_collector.py`; modify `cli.py`; test `tests/test_rank_collector.py`, `tests/test_shadow_cli.py`.

**Interfaces:**
- Consume complete `ScanRecord` from `LocalScanArchive.read_scan`, Task 1 normalization and Task 2 storage.
- Produce `RankCollector.collect(scan_id: str) -> RankSnapshotRecord` (async). Constructor takes archive root, `RankPolicy`, injectable UTC/monotonic clocks and a market factory creating an owned `ShadowMarketData`-compatible reader with `decision_tickers()`/`close()`.
- Internally reuse `ShadowBudget(ShadowPolicy(max_attempts=3, deadline_seconds=15))` and guarded `ShadowMarketData` transport only; never run shadow admission/evaluation. Persist rank policy separately, not a misleading shadow run policy.

- [x] Write `test_old_parent_collects_once_without_core_writes`: a complete two-day-old parent with historical scoring identity succeeds; parent/latest bytes stay identical and no universe/OI/depth/account methods execute. Add invalid parent/cohort rejection before network creation.
- [x] Run `uv run pytest tests/test_rank_collector.py tests/test_shadow_cli.py -q`; expect new collector/command assertions to fail.
- [x] Implement one bulk request with per-invocation UUID/budget. Preserve request/arrival/finish provenance and actual attempts/observed weights; apply the 15-second outer timeout including retry backoff. Archive partial/failed attempts after valid parent resolution, close owned resources, and persist then propagate external cancellation rather than claiming completion.
- [x] Add `snapshot-ranks` CLI dispatch. Test actual guarded transport: attempt four blocked after three failures, slow response/backoff cannot exceed deadline, account request denied, separate concurrent budgets, malformed body archived as failed and stale/incomplete rows as partial. CLI prints archive identity/status and uses a nonzero exit for failed collection.
- [x] Run collector, CLI and existing `tests/test_shadow_transport.py` tests; record evidence and untouched production invocation/source checks. Leave uncommitted.

### Task 4: Honest sampled-membership timeline

**Files:** Create `domain/rank_timeline.py`; test `tests/test_rank_timeline.py`.

**Interfaces:**
- Consume Task 1 normalization/envelopes and `ShadowRunRecord` final recorded `decision_tickers` call with its provider/arrival/classification provenance.
- Produce `initial_rank_state(child: ShadowRunRecord, policy: RankPolicy) -> dict[str, Any]` and `membership_timeline(child: ShadowRunRecord, initial: dict[str, Any], snapshots: tuple[RankSnapshotRecord, ...], horizon_hours: int, as_of: datetime, policy: RankPolicy) -> dict[str, Any]`.
- Return original denominators, initial partitions, per-symbol status, sample quality/spacing and nullable `first_observed_membership` with previous complete outside observation; retain shorts separately from long-success metrics.

- [x] Write `test_inside_unknown_and_observed_are_distinct`: initial-inside stays `already_inside`; unknown never becomes early; known-outside plus a later complete rank <=10 becomes observed membership, even if sparse, with sampling insufficiency separately visible.
- [x] Run `uv run pytest tests/test_rank_timeline.py -q`; expect new module import failure.
- [x] Implement initial reconstruction from the saved final ticker evidence, not parent quotes or stamped labels. Sort follow-ups by arrival and restrict to anchor <=arrival<=min(as_of, horizon end); reject nonadvancing provider-cutoff observations as coverage contributors. Require every cohort cutoff to advance relative to the last accepted complete sample; retain rejected samples/reasons in diagnostics.
- [x] Test complete boundary samples at anchor+10min and end-10min pass; +1ms gap fails; not-yet-due absent membership is `not_due`, and due absence is `not_observed_in_sufficient_samples` only with boundary/gap coverage. Test duplicates, out-of-order cutoffs, future arrivals, partial/delisted samples, horizon equality and 24/48 isolation. Prior outside observation is evidence of an observation only, never proof of no intervening crossing.
- [x] Run timeline and normalization tests; review denominators/status precedence and record evidence. Leave uncommitted.

### Task 5: Offline matched-anchor benchmark and reproducible manifests

**Files:** Create `domain/shadow_benchmark.py`, `application/shadow_benchmark.py`; modify `cli.py`; test `tests/test_shadow_benchmark.py`, `tests/test_shadow_cli.py`.

**Interfaces:**
- Pure producer: `freeze_benchmark_arms(parent: ScanRecord, child: ShadowRunRecord, initial: dict[str, Any], policy: RankPolicy) -> dict[str, Any]`; `summarize_benchmark(arms: dict[str, Any], timeline: dict[str, Any], outcomes: dict[str, Any]) -> dict[str, Any]`.
- Service producer: `ShadowBenchmark(root: Path, policy: RankPolicy).build(shadow_id: str, horizon_hours: int, *, as_of: datetime) -> BenchmarkRecord`; synchronous and filesystem-only. Consume Tasks 2/4 and existing `LocalShadowArchive.outcome_attempts`/`outcome_anchor`.
- Reuse existing internal `_measurement_identity(parent_checksum, shadow_id, completed_at)` from `application/shadow_observer.py` to test current outcome compatibility without editing that source-hashed observer or invalidating existing outcomes merely to expose a helper.

- [x] Write `test_benchmark_never_constructs_network_and_uses_child_anchor`: forbid HTTP/market construction; all arms reference exactly the child anchor, wrong parent/anchor/horizon is rejected, incompatible identities become unavailable, missing paths retain cohort denominator and shorts are separate.
- [x] Run `uv run pytest tests/test_shadow_benchmark.py tests/test_shadow_cli.py -q`; expect new benchmark/command failures.
- [x] Implement exact parent checksum/cohort validation. Freeze archived core longs and qualified child longs in order, retain rejections/admission exclusions, and build momentum from the final whole-cohort ticker evidence. Initial incomplete ranks or missing/nonfinite/negative cohort quote volume makes the reference unavailable; zero volume is valid but fails the volume filter. Sort eligible symbols by return/volume/symbol and retain at most five; disclose fewer than five rather than backfill.
- [x] Select latest compatible complete child outcome attempt observed by as-of, tie-break by attempt ID; inspect all compatible identity fields before selection. Retain partial/missing diagnostics and exact selected attempt IDs/checksums. Missing measurements are unavailable; malformed supported inputs raise integrity errors. Never use parent caches or fetch/recompute missing market history.
- [x] Build an immutable report manifest with all selected rank/outcome IDs/checksums, parent/child checksums, source/policy, as-of and provenance. Output raw forward returns, overlap/set sizes, missingness and rolling membership separately from fixed-forward cohort ranks. Top-five diagnostics use archived first five only when arm size >=5; missing outcomes remain unknown, not filtered/replaced. No winner/promotion verdict.
- [x] Add offline `benchmark-shadow` dispatch. Test momentum thresholds/ties/missing volume, unequal arm sizes, negative-return top10, source drift, late outcomes and identical frozen-input reruns whose payloads/manifests match except report ID/creation time. Run new benchmark/CLI plus existing shadow observer tests; record evidence and leave uncommitted.

### Task 6: End-to-end acceptance, core parity and review

**Files:** Create `tests/test_rank_acceptance.py`; modify `README.md`, `docs/screener-research-roadmap.md`, this plan.

**Interfaces:** Consume all prior interfaces; no new production entry point beyond the two manual CLI commands.

- [x] Write `test_real_child_rank_collection_to_offline_report`: extend existing real-evaluator acceptance fixture with provider cutoffs/openTime/quoteVolume, a qualified outside-shortlist long and a rejected symbol; collect multiple prospective immutable samples and consume compatible whole-cohort child outcomes. Assert original denominator, early-state evidence, same-anchor arms and deterministic report reproduction with all network constructors forbidden during reporting.
- [x] Run `uv run pytest tests/test_rank_acceptance.py -q`; demonstrate failing integration before completing acceptance wiring.
- [x] Complete fixture/integration wiring only; pin parent/child/latest byte preservation, no API/runtime/cron invocation of the new collector, nonempty core replay parity and existing quiet-coil/positioning/four-short parity tests. Do not claim all short families are full-bar production fixtures when coverage is classifier-level.
- [x] Document manual capture cadence, explicit gaps, old-parent eligibility, both commands, namespace layout and the distinction between rolling ranks and forward outcomes. State no scheduling, exact first-entry proof, edge guarantee or deployment readiness.
- [x] Run `uv run pytest`, `uv run mypy src`, Ruff check/format-check on changed Python paths and `git diff --check`. Require all tests including the previous 443 to pass; disclose inherited warnings. Verify CLI help/doctor and unchanged core/API/cron source plus latest-results hash against the execution-start inventory.
- [x] Request fresh independent implementation review against this plan/spec and the integrated diff; address actionable findings with red/green regressions, then rerun affected/full checks. Record final evidence, remaining roadmap items and integration status; no commit/push/deploy.

## Plan self-review

Coverage: normalization/initial evidence (Tasks 1/4), immutable provenance (2), bounded manual acquisition (3), sampled-time semantics (4), frozen matched-anchor controls and offline reproducibility (5), parity/acceptance/docs/fresh review (6). Each Review Focus case has explicit owning-task tests. Interfaces share the same envelopes, cohort and policy types; no changes to core schemas or the existing source-hashed outcome implementation are required. This milestone measures outcomes; automatic sampling, dashboard and strategy calibration remain future work.

## Verified implementation checkpoint

2026-10-25: all six tasks implemented using native TDD in the retained isolated worktree. Fresh independent review found five Important issues (no Critical/Minor); all five were addressed in one RED/GREEN regression pass. Full suite 514 pass, one inherited Starlette warning; strict mypy79 and 14-file touched lint/format checks pass. See the retained `.superpowers/sdd/2026-10-25-rank-observation-benchmark/progress.md` for commands, findings and rulings. No automatic collection, live strategy changes, commits, pushes or deployment.

Local integration completed into `/Users/jithinjacob/Desktop/trdindicator`, kept uncommitted as requested. Fresh combined-checkout verification: 514 passed, one inherited warning in 31.05s; strict mypy79, all 14 touched-file lint/format checks, CLI help/doctor and diff checks pass. Latest-results SHA256 before/after integration: `891cd0cddf9dd2fe03ce99f1a3743ab5e908d491e3a4da679bea99c663251adb`; the file refreshed independently during the earlier interruption, not during integration. HEAD remains `5e84bf01ed9306d3d8483852394955aa2d5175ab`, index empty. Native worktree and recovery ledger retained. No deferred minors. This completes this measurement-tooling milestone, not live empirical validation, scheduling, dashboard or deployment readiness.
