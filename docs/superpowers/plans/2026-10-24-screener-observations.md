# Screener Observations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve reproducible screening decisions and measure their subsequent 24h/48h price paths.

**Architecture:** Local immutable scan/outcome directories, per-scan recording/replay ports, a stage
decision journal and an immutable runtime result envelope. A separate bounded CLI worker measures
outcomes using public market data; observation never feeds back into candidate selection.

**Tech Stack:** Existing Python >=3.12, stdlib JSON/hashlib/pathlib/dataclasses, pytest/pytest-asyncio,
existing Binance public-data transport. No dependency changes.

**Spec:** `docs/superpowers/specs/2026-10-24-screener-observation-design.md`

## Global Constraints

- Keep scores, thresholds, ranking, desk caps, the four-hour schedule and published brackets unchanged.
- No account endpoints, trading credentials, simulated fills, automatic deployment or Git publication.
- Decimal values use exact strings; aware timestamps use UTC.
- Existing identical scan IDs are idempotent; different content under the same ID raises a conflict.
- Primary horizons are 24 and 48 hours; no interpolation or conversion of missing data to zero returns.
- Preserve existing dirty files, particularly `data/screener_latest.json`; tests use temporary paths.
- Do not mark the whole roadmap milestone complete before replay and cohort outcomes are verified.

## Review Focus

- Two simultaneous scans must not exchange receipts, overwrite files or corrupt one another's calls.
- Failed/interrupted writes must never appear as complete archives; checksum failure blocks replay.
- Eligible-but-unselected and later-delisted symbols must remain in the measurement denominator.
- Repeated calls, errors and missing macro/OI fields must replay without current data or fake defaults.
- Delayed/gapped outcomes and same-bar target/stop touches must not become invented successful trades.

## Current checkpoint

Design and plan approved; native execution selected. All six tasks are implemented locally and
verified. Earlier data-contract fixes remain separate dirty changes; nothing was committed or deployed.

Final evidence (2026-10-24): 372 backend tests pass with one inherited Starlette deprecation warning;
mypy passes for 64 source files; source/touched-test Ruff and diff checks pass. A fresh independent
read-only reviewer found four Important issues (no Critical or Minor findings); all four were fixed in
one test-first pass: published canonical-pair coverage, recording-failure isolation, faithful cancellation
replay and correct exhausted-fetch classification. The full suite passed after that pass.

Final bounded live smoke: five selected/evaluated contracts, zero fetch failures, three Stage 2 probes,
zero published candidates. The archive retained the 512-contract canonical eligible cohort, 22 input
calls and 1,491 decision facts; checksummed storage and exact offline replay passed with HTTP-client
construction disabled. This is a plumbing/replay check, not predictive evidence or a full-cohort load run.
Temporary archive: `/var/folders/hk/cwhrf3xs4cs_xtxzzqw2gxgc0000gn/T/atlas-observations-final-lgbgeu8z`;
scan ID `346e1f9d-3ab2-428b-b085-a4f37ce372c1`. Retain matching code/dependencies for replay.

Resume point: observation implementation is complete; do not rebuild Tasks 1–6 after interruption.
Next work is controlled staging/production rollout acceptance, persistent backup/restore and measured
cohort collection cost, then prospective outcomes and benchmark/calibration. These require their own
operational authorization; no automatic outcome scheduling, threshold tuning or deployment is implied.

---

### Task 1: Lossless records and immutable local store

**Files:** Create `src/algotrader/domain/scan_observations.py`,
`src/algotrader/infrastructure/scan_archive.py`, `tests/test_scan_archive.py`; modify `.gitignore`.

**Interfaces:** `ScanRecord` holds schema/ID/timestamps/identity/request/input/decision/result/status;
`ScanReceipt` holds ID/archive status/error. `LocalScanArchive(root: Path).write_scan(record) -> ScanReceipt`
and `.read_scan(scan_id: str) -> ScanRecord` verify canonical JSON bytes and SHA-256 manifests.
Use explicit typed serializers/deserializers for known dataclasses/enums/Decimals, not dynamic imports
or pickle. UUID/path validation rejects traversal. Writer-internal failure injection stays in tests.

- [x] Write tests asserting exact Decimal/datetime/optional-macro round trips and stable checksums;
  duplicate identical ID is unchanged, changed content raises conflict, traversal is rejected.
- [x] Run `.venv/bin/pytest tests/test_scan_archive.py -q`; observe expected missing-feature failures.
- [x] Implement unique staging, flushed/fsynced payload/manifest, atomic non-overwriting publication,
  checksum-verified reads and ignored generated research data.
- [x] Add/run real temporary-filesystem interruption, tampering and multiprocess same-ID/different-ID
  contention tests. Require no partial complete directory and no silent replacement.
- [x] Review against the storage section; retain uncommitted work for the user, do not commit.

### Task 2: Record input calls and replay the production screener offline

**Files:** Create `src/algotrader/application/scan_recording.py`,
`src/algotrader/application/scan_replay.py`, `tests/test_scan_replay.py`.

**Interfaces:** `RecordingMarketData` decorates `MarketDataPort`/`DerivativesDataPort` per scan;
`RecordingMacroProvider.get_context()` records the exact typed context. `RecordedCall` contains key,
per-key invocation index, parameters, observed result or sanitized failure. `replay_scan(record) ->
ReplayReport` uses recorded ports and the existing `CryptoOpportunityScreener.screen`.

- [x] Write failing fixture tests comparing actual recorded/replayed ordered opportunity dictionaries
  and counts, including repeated derivative calls and missing optional readings.
- [x] Run `.venv/bin/pytest tests/test_scan_replay.py -q`; verify expected failures, then implement
  wrappers, typed call recording and source/config mismatch checks using Task 1's codecs.
- [x] Add failed-call, cancelled/timed-out-call, empty-scan and absent-recorded-call tests. Replay must
  raise explicitly for a missing call and never reach a transport; input counts/results must match.
- [x] Run the focused tests plus existing data-contract and Stage 2 admission tests; review parity.

### Task 3: Stage journal and shared runtime archive integration

**Files:** Modify `src/algotrader/application/crypto_screener.py`,
`src/algotrader/infrastructure/runtime.py`, `src/algotrader/config.py`,
`src/algotrader/api/portal.py`, `src/algotrader/cli.py`, `src/algotrader/jobs/screener_cron.py`;
create `tests/test_scan_recording_integration.py`; extend `tests/test_screener_publication_policy.py`.

**Interfaces:** Optional `ScanDecisionJournal` stores stage facts without altering branch conditions.
`ScreeningRun` contains opportunities, counts and receipt. `Runtime.run_crypto_scan(...) -> ScreeningRun`
has the same screening arguments as `crypto_screen`; the old `crypto_screen` returns the new result's
opportunity list for compatibility. `Settings.research_archive_dir` defaults to `data/research`.

- [x] Write/run failing real-screener fixtures for eligible/not-selected, publication rejection,
  canonical-pair deduplication and post-policy desk limits. Assert identical outputs with/without journal.
- [x] Implement stage observations at existing branches, never duplicate policy formulas. Record a
  generic no-setup reason where evaluator internals do not expose a more specific reason.
- [x] Write/run failing API/CLI/cron integration tests for scan ID/archive status and concurrent receipt
  ownership. Keep display candle enrichment outside decision inputs and retain current output order.
- [x] Implement run envelopes, safe identity whitelist/source hashes, thread-offloaded archive writes
  and all three entry points using their own envelopes rather than mutable last-scan globals.
- [x] Add write-permission/disk failure and failed-scan tests: explicit unarchived receipt/log, unchanged
  screening output; no falsely complete archive. Cache reads must not create scans.
- [x] Run recording/replay/publication/runtime tests and the full backend suite; review stage coverage.

### Task 4: Pure 24h/48h outcome evaluator

**Files:** Create `src/algotrader/domain/forward_outcomes.py`, `tests/test_forward_outcomes.py`.

**Interfaces:** `outcome_anchor(completed_at: datetime) -> datetime` rounds up to the next-or-current
UTC minute. `evaluate_forward_path(*, anchor: datetime, horizon_hours: int, candles: Sequence[Candle],
now: datetime, direction: Direction | None = None) -> ForwardObservation` reports explicit status,
reference/open, end/close, raw/directional return and high/low excursions. Keep touch diagnostics
separate from returns. `rank_cohort(observations) -> CohortSummary` reports observed ranks and coverage.

- [x] Write/run failing exact-window tests: 24h needs 1,440 closed one-minute bars; 48h needs 2,880.
  A completion timestamp at 12:00:00 anchors 12:00; 12:00:00.001 anchors 12:01. No pre-anchor bars count.
- [x] Implement deterministic Decimal calculations; observed end close 110 / open 100 gives raw +10%,
  and short directional return -10%. These are not execution prices or realized P&L.
- [x] Add/run gaps, duplicates, nonfinite/impossible OHLC, future/partial bars, not-due, zero baseline,
  same-bar touch ambiguity, ties and partial/delisted cohort tests. Missing data never becomes zero.
- [x] Review evaluator arithmetic independently of worker/storage code and run the full suite.

### Task 5: Resumable public-data outcome collection and replay commands

**Files:** Create `src/algotrader/application/forward_observer.py`,
`src/algotrader/jobs/outcome_observer.py`, `tests/test_forward_observer.py`;
extend `src/algotrader/infrastructure/scan_archive.py` and `src/algotrader/cli.py`.

**Interfaces:** `ForwardObserver.observe(scan_id: str, horizon_hours: int, max_symbols: int)` consumes
Task 1 records and Task 4 evaluator. Add `atlas replay-scan --scan-id UUID` (offline) and
`atlas observe-scan --scan-id UUID --horizon-hours {24,48} --max-symbols N` (bounded public data).
Add a bounded time-range kline method on the public adapter if needed; do not require live instrument
listing for historical symbols. Outcome attempt records use the same immutable publication primitive.

- [x] Write/run failing fake-transport tests for exact time-range pagination, original eligible cohort,
  unavailable delisted symbol, bounded run refusal and interruption/resume without duplicate successes.
- [x] Implement paced collection, immutable per-symbol attempts, checksummed evidence and aggregate
  coverage/ranks. Not-due/fetch failure stays explicit and retryable; original scan bytes never change.
- [x] Add tests proving the transport receives only allowed public market-data GETs, supported horizon
  validation, safe IDs, offline replay and no observer scheduling in the screening runtime.
- [x] Run all focused tests then full pytest, mypy and touched-file Ruff. No live full-cohort run is
  required to unit-test the worker; measure its live request cost before enabling a schedule.

### Task 6: Final verification, operator documentation and handoff

**Files:** Update `docs/screener-research-roadmap.md`, `README.md`, `docs/gcp-deployment.md`
and this plan's checkboxes/evidence section.

- [x] Document archive location, schema/identity, receipts, replay and outcome commands, missing-data
  semantics, manual scheduling, request limits, disk/backups and current deployment status.
- [x] Run `.venv/bin/pytest -o addopts='' -q --tb=short`, `.venv/bin/mypy src`, Ruff on source/new and
  touched tests, and `git diff --check`; separately name inherited warnings or failures.
- [x] Review the fixed observation diff against the spec, including disabled network replay,
  same/different-scan concurrency, secrets, failure paths and unchanged screening decisions.
- [x] Provide exact verified results and remaining release work. Do not push, deploy, change thresholds
  or claim predictive edge. Prefer a fresh read-only reviewer if the selected execution method permits.

## Plan self-review

Each spec acceptance item maps to Tasks 1–6. Recording uses port inputs, not display payloads; failed
scans and incomplete outcome coverage remain explicit. Task 3 preserves list-return compatibility but
moves API/CLI/cron to per-call receipts. Tasks 4–5 measure outcomes without fills or selection changes.
Deployment, retention deletion, automatic worker scheduling and empirical calibration remain excluded.
