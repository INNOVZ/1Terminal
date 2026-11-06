# Dual-track Shadow Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Observe additional independently admitted long candidates without changing the published core.

**Architecture:** A manual application runner consumes an immutable core archive and owns separate
bounded public-data clients, input tape and research archive. Pure admission/classification functions
feed the existing per-symbol evaluator and publication helper. Offline replay and child-anchored
outcomes remain separate from production publication and parent outcomes.

**Tech Stack:** Existing Python, dataclasses, Decimal, asyncio, httpx, pytest and local checksummed JSON;
no new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-24-dual-track-shadow-design.md`

## Global Constraints

- Preserve live scores, thresholds, ordering, grades, brackets, shorts and 16-slot allocation.
- Manual commands only; no runtime/API/cron invocation, trading credentials or account endpoints.
- Maximum 15 unique tokens, 150 actual HTTP attempts including retries, 90-second child deadline.
- Parent: complete, supported, source-compatible production-quality ALL scan, age 0..300 seconds.
- Admission hygiene: quote volume >= $1.5m and spread <= 25bps; original canonical cohort only.
- Classification: original whole cohort, observed rolling 24h top-10 including ties, never Binance UI membership.
- Snapshot age <=60 seconds, future skew <=5 seconds, cohort timestamp span <=60 seconds.
- Missing/stale/malformed/nonfinite evidence stays unknown; never a fabricated zero or early label.
- Core ScanRecord v1 and parent bytes immutable; no latest-results/watchlist writes.
- No Git commit, push, deployment or automatic collection; preserve all existing dirty changes.
- Keep completed observation Tasks 1–6 complete. This experiment does not prove predictive advantage.

## Review Focus

- Concurrent independent children must not share budgets, tapes, IDs or overwrite archives (Tasks 2–4).
- Clock jumps, cancellation and nested retries must not bypass limits or produce complete records (Tasks 2–3).
- Duplicate/malformed ticker rows must not silently create complete rank coverage (Task 1).
- A removed canonical symbol must remain an unavailable denominator member, not disappear (Tasks 1, 5).
- Source drift or unavailable recorded calls must fail replay without creating a live client (Task 4).

## Checkpoint and execution

Design and task breakdown approved; native execution retained. Jithin requested an isolated worktree:
`/Users/jithinjacob/.codex/worktrees/dual-track-shadow/trdindicator`. Uncommitted prerequisite source,
tests and documentation were copied; the original scan-results data was not copied or modified.
All six tasks are implemented locally. Independent review found seven Important issues, no Critical
or Minor issues; the single regression-tested fix pass addressed all seven. Final verification on
2026-10-25: 443 tests pass, one inherited Starlette warning; strict mypy passes for 73 source files.
Ruff/diff checks pass. Original core source remains byte-identical; CLI alone gains manual commands.
Real-evaluator acceptance covers a qualified outside-shortlist child plus a no-setup rejection,
archive/replay/outcomes, and nonempty production-quality ALL core replay. Representative quiet-coil,
positioning/funding and four-short parity also has a classifier-level matrix; this is not a claim
that all four short archetypes were emitted by full-bar production ALL acceptance fixtures.
No deployment/commit. Preserve the isolated worktree and ledger until Jithin chooses integration.
Local integration subsequently approved and completed (2026-10-25): shadow source/tests/CLI/docs are
now also in `/Users/jithinjacob/Desktop/trdindicator`, uncommitted as Jithin explicitly requested.
Combined-checkout verification: 443 tests pass (one inherited warning), strict mypy73, touched Ruff
and diff checks pass; CLI help/doctor expose the manual shadow commands. The existing latest scan
file's SHA-256 is unchanged across integration. Worktree retained as a recovery/reference copy.
Track each red/green command and review result here as work progresses. No fresh worktree should omit
the currently uncommitted prerequisite implementation: resolve isolation using the worktree skill
before execution and retain all prerequisite bytes.

### Task 1: Pure admission, setup inventory and contemporaneous classification

Status: complete; exact RED/GREEN evidence and completion command retained in this plan's SDD ledger.

**Files:** Create `src/algotrader/domain/shadow_discovery.py`, `tests/test_shadow_discovery.py`.

**Interfaces:** Frozen `ShadowPolicy` holds version and the exact limits above;
`admit_shadow(parent: ScanRecord, policy: ShadowPolicy, now: datetime) -> AdmissionReport`;
`classify_shadow(cohort: Sequence[Instrument], candidates: Sequence[dict[str, Any]],
rows: Sequence[dict[str, Any]], observed_at: datetime, policy: ShadowPolicy) -> ClassificationReport`.
Reports retain all admission decisions, selected instruments/buckets, raw evidence, coverage and labels.
`setup_family(label: str) -> str` inventories active archetypes, SetupLabel values and aliases explicitly.

- [x] Write tests for all cohort members considered; outside-selected anomaly/coverage probes;
  existing published symbols excluded; positive-momentum controls; deterministic ties and no duplicate
  base asset. Assert volume 1499999 rejected, 1500000 accepted; spread 25 accepted, >25 rejected.
- [x] Pin bucket rules: anomaly requires negative funding AND positive basis, sorted ascending funding,
  descending basis then symbol; momentum descending positive return, volume then symbol; coverage
  SHA-256 of parent ID plus symbol. Allocate slots round-robin anomaly/momentum/coverage up to five
  each, skip exhausted buckets, deduplicate before consuming a slot; never relax bucket predicates.
- [x] Add tests for parents older than 300s, future completion, diagnostic requests and failed/incomplete
  status; missing quotes and missing/nonfinite evidence must have explicit reasons, never defaults.
- [x] Add classification counterexamples: changed parent/child rank, top-10 cutoff ties, fewer than ten
  cohort members, missing/delisted members, duplicate rows, malformed/NaN returns, nonpositive prices,
  61s-old or 6s-future rows, timestamp span >60s. Assert unknown cannot earn `early_candidate`.
- [x] Run `.venv/bin/pytest tests/test_shadow_discovery.py -q`, observe missing-feature failure;
  implement the pure interfaces, then rerun to PASS. Rank = 1 + count of strictly larger returns;
  early requires pre-trigger AND known rank >10 using the SAME final snapshot price and return.
- [x] Inventory all active long/short classifier labels, aliases and enum-only compatibility labels;
  test exhaustive coverage and no mutation of supplied opportunities. Review Task 1 against the spec.

### Task 2: Public transport budgets and timestamp-preserving snapshot adapter

**Files:** Create `src/algotrader/adapters/binance/shadow.py`,
`src/algotrader/application/shadow_budget.py`, `tests/test_shadow_transport.py`;
modify `src/algotrader/adapters/binance/client.py` only if needed for an opt-in attempt guard.

**Interfaces:** `ShadowBudget(policy: ShadowPolicy)` provides `before_attempt() -> None` and
`snapshot() -> dict[str, Any]`; `ShadowBudgetExceeded` reports attempts/deadline reason.
`ShadowMarketData` owns the existing public futures adapter and guarded transport;
`decision_tickers() -> Sequence[dict[str, Any]]` retains raw ticker fields and collection timestamps.
Use monotonic deadline accounting, separate aware wall-clock evidence. Any shared-client hook defaults
to no-op and cannot alter production configuration or retry behaviour.

- [x] Mock real transport attempts, including both client retries and futures-wrapper retries;
  assert 150 succeeds and attempt 151 is blocked before I/O, independently for concurrent children.
- [x] Test deadline during backoff/hanging I/O and cancellation; close child clients in finally,
  preserve cancellation, record partial consumption, and never suppress a limit into empty success.
- [x] Test public endpoint allowlisting, absence of signed/account calls, bulk ticker weight accounting,
  raw malformed/missing fields retained for Task 1, and no call at all when budget is exhausted.
- [x] Run `.venv/bin/pytest tests/test_shadow_transport.py -q` red, implement guards at actual transport
  boundary and outer child timeout, rerun green plus existing adapter/client tests. Review no-op parity.

### Task 3: Separate research record, runner and immutable storage

**Files:** Create `src/algotrader/domain/shadow_observations.py`,
`src/algotrader/infrastructure/shadow_archive.py`, `src/algotrader/application/shadow_runner.py`,
`tests/test_shadow_runner.py`, `tests/test_shadow_archive.py`.

**Interfaces:** Versioned `ShadowRunRecord` contains child/parent IDs, parent checksum, source/config/
policy identity, cohort, admission decisions, input references/new RecordedCalls, evaluated results,
qualification reasons, final snapshot, journal, timing, budget and terminal status.
`LocalShadowArchive(root: Path).write_run(record: ShadowRunRecord) -> ScanReceipt`,
`.read_run(shadow_id: str) -> ShadowRunRecord` use `root/shadow/UUID` and explicit safe codecs.
`ShadowRunner(...).run(parent: ScanRecord, policy: ShadowPolicy) -> ShadowRunRecord` uses injected
market/clock ports and owns no production runtime. Keep core codec registry/schema unchanged.

- [x] Write archive tests for exact Decimal/aware-time roundtrip, canonical UUID/path validation,
  checksum tampering, interrupted writes and concurrent same-ID conflicts using temporary roots.
- [x] Write runner fixtures asserting parent source/request validation, checksum unchanged on success,
  failure and cancellation; exact selected/rejected/budgeted-out accounting, no parent or latest-file writes.
- [x] Pin required candles to existing evaluator requests: 1h/240, 4h/480, 1w/40. Reuse only valid closed
  interval/count/cutoff/freshness inputs; fetch missing inputs through Task 2. Obtain derivatives BEFORE
  evaluation; missing BTC/macro context is explicit unavailability, not a neutral invented reading.
- [x] Exercise existing `_evaluate_single` with BREAKOUT/LONG, explicit derivatives and preloaded bars;
  retain accumulation-first semantics and apply `publication_rejection_reasons`, preserving available
  COI context. Store all diagnostics separately from qualified results; no full `screen()` call.
- [x] Acquire final ticker snapshot after deep data, then Task 1 classification; record all distinct
  timestamps and `staggered_shadow`. Deadline/provider failures produce explicit partial/error status.
- [x] Run both focused test files red, implement minimal storage and orchestration, rerun green;
  inspect emitted record and parent checksum. Review task interfaces and failure isolation.

### Task 4: Offline replay and explicit manual CLI

**Files:** Create `src/algotrader/application/shadow_replay.py`,
`src/algotrader/jobs/shadow_scan.py`, `tests/test_shadow_replay.py`, `tests/test_shadow_cli.py`;
modify `src/algotrader/cli.py`.

**Interfaces:** `replay_shadow(record: ShadowRunRecord, parent: ScanRecord) -> dict[str, Any]`
reconstructs Task 3 via recorded ports and recorded clocks, checks matching identities and consumes
every recorded call. `run_shadow_scan(root: Path, scan_id: str) -> ShadowRunRecord` creates child-only
clients. Commands: `atlas shadow-scan --scan-id UUID --archive-dir PATH` and
`atlas replay-shadow --shadow-id UUID --archive-dir PATH`.

- [x] Record/replay fixtures with qualified, rejected, missing-input and budget-ended children;
  assert exact admissions/results/labels/journal equivalence while client construction is prohibited.
- [x] Test repeated calls, cancellation, absent/unused calls, parent-checksum mismatch and source/config
  drift: fail explicitly, never network fallback. Replay bypasses current parent-age validation only
  by reproducing the recorded admission clock; never by silently weakening the rule.
- [x] CLI tests assert valid IDs, JSON-only stdout, nonzero failure status, clear shadow/uncalibrated
  labels, client cleanup and no changes to existing screen/replay/observe commands or API/cron wiring.
- [x] Run the two new files and `tests/test_cli.py` red, implement, rerun green; review offline isolation.

### Task 5: Child-anchored 24h/48h observations

**Files:** Create `src/algotrader/application/shadow_observer.py`,
`tests/test_shadow_observer.py`; modify `src/algotrader/cli.py` for `observe-shadow`.

**Interfaces:** `ShadowObserver(...).observe(shadow_id: str, horizon_hours: int,
max_symbols: int, *, now: datetime | None = None) -> dict[str, Any]` consumes a complete child,
original parent cohort and shared `forward_outcomes` pure evaluator. Store attempts under
`root/shadow-outcomes/CHILD_UUID/HORIZON/SYMBOL/ATTEMPT_UUID`, never parent caches.

- [x] Assert anchor at first full minute at/after child completion; retain parent anchor/lag separately;
  max-symbol cap refuses truncation, absent/delisted cohort members remain explicit unavailable.
- [x] Test exact 24/48h minute coverage, gaps, target/stop ambiguity, idempotent completed attempts,
  incompatible measurement identity and no parent-cache reuse. Baseline shorts remain unchanged.
- [x] Assert summary distinguishes additional qualified discoveries, overlap and staggered outcomes;
  no rolling-leaderboard entry time, calibrated probabilities, P&L or promotion recommendation.
- [x] Run new tests red, implement using existing pure evaluators without fabricating a ScanRecord,
  rerun green with existing observer/outcome tests. Review storage identity and denominator preservation.

### Task 6: Core parity, end-to-end verification and operational handoff

**Files:** Create `tests/test_shadow_core_parity.py`; modify `README.md`,
`docs/screener-research-roadmap.md`, this plan and the approved spec status as appropriate.

**Interfaces:** No additional product interfaces. Freeze representative core tapes using current
fixtures; core runtime must have zero dependency on executing the shadow runner.

- [x] Add core parity tests comparing ordered serialized opportunities, decisions, counts and brackets
  with shadow code available, absent invocation, failed children and exhausted children. Include quiet
  coils, positioning/funding squeeze and all four active shorts; aliases/enum-only labels are inventory,
  not fabricated evidence of active emission. Check no API/cron shadow entry point.
- [x] Run one complete fake-provider parent → child → immutable archive → offline replay → outcome flow;
  assert original parent bytes and user latest scan file unchanged. Failure-case flow remains inspectable.
- [x] Run `.venv/bin/pytest -o addopts='' -q --tb=short`, `.venv/bin/mypy src`, touched-file Ruff checks
  and `git diff --check`; record exact totals/errors, do not confuse focused with full verification.
- [x] Obtain a fresh read-only implementation review before completion; fix actionable findings with
  failing regression tests and rerun affected checks. Review authorization is supplied by the approved
  spec's fresh-review requirement; do not dispatch implementer agents for native execution.
- [x] Document manual commands, caps, timing/coverage caveats, no predictive validation, and remaining
  work: prospective rank snapshots, matched-cutoff benchmark, measured operations, scheduling/UI and
  separate deployment approval. Update this durable checkpoint; retain all changes uncommitted.

## Self-review

Spec coverage: vocabulary/admission/timing in Task 1; limits/data in Tasks 2–3; immutable evidence and
replay in Tasks 3–4; outcomes in Task 5; core parity and completion boundary in Task 6. Five review-focus
conditions have explicit tests in their owning tasks. All cross-task names match the interfaces above.
No production strategy thresholds or admission paths are modified by this plan.

## Final handoff

Do not restart Tasks 1–6. The observation and shadow implementation milestones are complete locally;
the broader roadmap is not. Next research work: prospective rolling-rank snapshots and a matched-cutoff
benchmark before considering promotion. Operations/load checks, backup/restore, scheduling/UI and
deployment remain separately scoped. Raw score/grade fields still do not imply calibrated probability.
