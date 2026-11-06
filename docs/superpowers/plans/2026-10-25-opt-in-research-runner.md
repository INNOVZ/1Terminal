# Opt-in Research Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reliably observe one explicitly enrolled frozen candidate cohort through its original 24h/48h windows on GCP without changing live screening or enabling research by default.

**Architecture:** Immutable session definitions and role journals drive two independently locked worker processes: rank sampling and bounded historical outcome/report collection. Existing collector, observer and benchmark code remain the measurement authorities. Optional Compose services and a persistent operator control file separate shipping code from enabling collection.

**Tech Stack:** Existing Python 3.13 deployment runtime (project floor 3.12), asyncio, Decimal, dataclasses, httpx, POSIX flock, local checksummed archives, pytest/mypy/Ruff, Docker Compose, GitHub Actions and existing Next.js/Vercel frontend. No new runtime dependency.

**Spec:** `docs/superpowers/specs/2026-10-25-opt-in-research-runner-design.md`

## Global Constraints

- One nonterminal session per archive in v1; original canonical whole cohort <=1000, no truncation.
- No automatic rescans, universe refresh, OI/depth scans, candidate promotion, trading/account endpoints or exchange credentials are introduced.
- Preserve core strategies, scores, grades, allocation, API/runtime/core cron and existing archive schemas.
- Original child next-full-minute anchor; original 24h/48h endpoints. Never reset windows at enrollment/restart or backfill rolling ranks.
- 300-second rank slots through the inclusive 48h endpoint; <=30-second capture lateness, no catch-up burst or repeated slot.
- Rank acquisition/cleanup <=3 actual HTTP attempts and <=15 seconds; durable disk publication is a separate cancellation boundary.
- Outcome pass <=30 actual public HTTP attempts including retries, <=90 seconds including backoff/cleanup, sequential requests, >=0.2-second per-symbol pacing.
- Outcome first attempt at endpoint+60 seconds, at most one pass per five-minute slot, 24h precedence when both due, fixed retry expiry endpoint+12h.
- Before every pass: >=2GiB free and research archive <5GiB; pause rather than delete evidence or shrink the cohort.
- Two optional worker containers: 256MiB and 0.5 CPU each, UID10001, read-only root, tmpfs, no ports/state-volume/socket/keys.
- Missing operator control file means disabled. Only ATLAS_RESEARCH_ENABLED=true|false and a canonical ATLAS_RESEARCH_SESSION_ID accepted; never shell-source it.
- No activation, push or deployment before the applicable gates. No force-push, blanket staging, secret/runtime-data commits or unrelated PDF/frontend changes.
- Retain native execution, existing isolated worktree and durable ledger. Keep intermediate work uncommitted; create the reviewed release commit only in the publishing phase.

## Review Focus

2026-10-26 implementation review amendment: Jithin approved the optional exact-permutation
observer traversal extension needed for durable fair acquisition. See the spec amendment and
ledger: no measurement formulas, anchors, denominators or source-version checks may be weakened.
Collector request windows and streaming timeline assembly are reviewed hardening extensions;
manual collector/observer defaults stay unchanged. All four Important findings require regression
tests and a green full suite before local integration or publication.

- Crash after reservation, snapshot publication or report publication must recover without duplicate acquisition/trial or fabricated success (Tasks 2–4).
- Outcome budget exhaustion swallowed by the existing observer must not generate fabricated attempts for unvisited symbols or starve rank sampling (Task 4).
- SIGTERM, backward clocks and restart near exact slot/horizon boundaries must not repeat slots, extend windows or erase durable evidence (Tasks 1, 3, 5).
- Worker enablement must survive SHA-based deployments; disabled/invalid controls must never accidentally start research or alter API settings (Task 6).
- Malformed control/journal data, source drift, disk pressure and large/invalid provider cooldowns must fail closed without blocking the live core (Tasks 2–6).

## Checkpoint and execution

Design and implementation plan approved by Jithin; native execution is in progress. The fresh starting baseline was 514 tests. Use `executing-plans` natively: no implementer agents; one fresh independent implementation reviewer after Task 7. Use a separate plan ledger under `.superpowers/sdd/2026-10-25-opt-in-research-runner/`; do not overwrite completed rank/shadow ledgers.
Reuse the attached isolated worktree after reconciling approved documents and prerequisites. Preserve main-checkout runtime JSON, generated PDFs and unrelated PDF-generator edits. Record each RED/GREEN command, ruling and integration/publication result. Run the full suite before each task is marked complete.
Common verification commands: `uv run --extra dev python -m pytest` for the full suite and `uv run --extra dev python -m mypy src` for strict source typing. Run `uv run --extra dev python -m ruff check <explicit touched Python paths>` and `uv run --extra dev python -m ruff format --check <same paths>`; record actual paths/results, not placeholders in the ledger. Use an accessible task-specific UV cache if needed. A scoped clean check does not excuse unrelated required CI failures; report them without silently modifying unrelated user work.

## File responsibilities and shared contracts

All module paths below are relative to `src/algotrader/`; tests are under `tests/`.

- `domain/research_sessions.py`: immutable `ResearchPolicy`, `ResearchSession`, `ResearchEvent`, pure slot/horizon decisions and journal reducer. No I/O.
- `infrastructure/research_sessions.py`: immutable envelopes, schema/identity validation, ordered per-role journals, enrollment/role advisory locks and durable reservations.
- `application/research_sessions.py`: offline start/status/stop, identity validation and disk guard; no market client construction.
- `application/research_rank_worker.py`: one rank tick plus restart reconciliation using preallocated snapshot IDs.
- `adapters/binance/research.py`: observed public-response headers and bounded outcome-minute port, using existing allowlist/guarded transport.
- `application/research_outcome_worker.py`: due-pass lifecycle, compatible-cache reuse, fixed cutoff/report reservation/finalization.
- `jobs/research_worker.py`: process ownership, clocks, signals, interruptible waiting and role heartbeat/health.
- Existing `application/rank_collector.py`, `application/shadow_benchmark.py`: optional caller-provided canonical IDs only; existing manual defaults unchanged.
- `cli.py`: four research commands and offline worker-health subcommand. No public research HTTP routes.
- `scripts/research_compose_control.py`: strict control parsing and argv-based Compose wrapper used consistently by deployment and operator commands; packaged/available in the release, never run by importing the API.
- `compose.yaml`, `.github/workflows/deploy.yml`, `docs/gcp-deployment.md`, `README.md`, roadmap: optional services, CI, deployment/activation/recovery instructions. No new frontend feature.

Definitions: `Role = Literal["ranks", "outcomes", "control"]`. Session definition contains IDs/checksums, canonical `tuple[Instrument, ...]`, UTC enrollment/anchor/endpoints, frozen runner/rank policies and semantic identities. Event contains canonical event/session IDs, role, per-role integer sequence, aware UTC timestamp, kind and `dict[str, Any]` details. Store these via explicit dictionary envelopes without extending the core archive type registry.

### Task 1: Pure session policy, scheduling and status reduction

**Files:** Create `domain/research_sessions.py`; test `tests/test_research_session_policy.py`.

**Interfaces:** Produce `rank_slot(session: ResearchSession, now: datetime, consumed: frozenset[int]) -> dict[str, Any]`, `due_horizon(session: ResearchSession, now: datetime, completed: frozenset[int]) -> int | None`, `reduce_session(session: ResearchSession, events: tuple[ResearchEvent, ...], now: datetime) -> dict[str, Any]`, plus the records/policy above. Consumers use these functions rather than reimplement clocks.

- [ ] Write `test_rank_slots_do_not_drift_repeat_or_backfill`: at anchor+300s slot1 may run; +330s still allowed; +330s+1us is missed. Consumed slot1 never re-runs after rollback/restart. Test inclusive slot576 at +48h, no acquisition later, naive clocks rejected and old enrollment cannot change endpoints.
- [ ] Run `uv run --extra dev python -m pytest tests/test_research_session_policy.py -q`; expected missing new module.
- [ ] Implement validated frozen policies and pure decisions. Missed slots retain their scheduled timestamps; unknown/interrupted is not a negative prediction. Return next wait timestamp, eligible slot, missed slots and terminal reason explicitly.
- [ ] Write/run horizon/reducer tests: no outcome before +24h+60s; exact grace accepted; 24h precedes 48h; attempts do not extend +12h expiry; durable stop dominates later starts. Separate disabled/starting/running/degraded/storage_blocked/source_mismatch/stopped/complete from last successful observation. Expected all pass.
- [ ] Record passing focused/full suite and review the pure boundary diff; no commit.

### Task 2: Durable sessions, locks and offline operator operations

**Files:** Create `infrastructure/research_sessions.py`, `application/research_sessions.py`; test `tests/test_research_session_archive.py`, `tests/test_research_sessions.py`.

**Interfaces:** `LocalResearchSessions(root: Path).read(session_id: str) -> ResearchSession`, `.events(session_id: str) -> tuple[ResearchEvent, ...]`, `.append(session_id: str, role: Role, kind: str, details: dict[str, Any], at: datetime) -> ResearchEvent`, `.role_lock(role: Role) -> AbstractContextManager[None]`, `.reserve_rank(session_id: str, slot: int, at: datetime) -> dict[str, Any]`, `.reserve_report(session_id: str, horizon_hours: int, as_of: datetime, inputs: dict[str, Any]) -> dict[str, Any]`. `ResearchSessions(root: Path)` exposes `.root: Path`, `.store: LocalResearchSessions`, `.start(shadow_id: str, *, now: datetime) -> ResearchSession`, `.status(session_id: str, *, now: datetime) -> dict[str, Any]`, `.stop(session_id: str, *, now: datetime) -> None`, `.guard(session_id: str) -> dict[str, Any]` returning `ok`, `reason` and nullable `next_allowed_at`.

- [ ] Write `test_enrollment_and_role_locks_are_cross_process_exclusive`: duplicate same-child enrollment returns existing UUID; distinct child rejected while active; a second process cannot acquire the same role lock; ranks/outcomes roles coexist and killed process releases its lock. Offline operations forbid HTTP constructors.
- [ ] Run `uv run --extra dev python -m pytest tests/test_research_session_archive.py tests/test_research_sessions.py -q`; expected missing new interfaces.
- [ ] Implement `sessions/UUID/definition/`, `events/ROLE/SEQUENCE-UUID/`, `reservations/ranks/SLOT/`, `reservations/reports/24|48/` via existing atomic checksummed primitives. Serialize enrollment under a separate archive-level lock. A lifetime worker lock and short journal locks must be distinct so status/stop do not deadlock a running worker. Reducer order is per-role sequence with explicit event meaning, not wall-time last-write-wins.
- [ ] Implement exact parent/child checksum/cohort/completeness and supported semantic identity checks. Pin rank/classifier/report interpretation and existing outcome measurement identity, not merely Git SHA. Stop acquisition on mismatch while allowing offline inspection. Reject late (>48h), naive, malformed, unsupported or corrupt input. Re-enrollment never restarts stopped/completed same-child trials.
- [ ] Test disk guard with injected disk-usage results at 2GiB-1/2GiB and archive size 5GiB-1/5GiB, including sibling ranks/outcomes. Block without deletion; bound repeated blocked events to once per role/slot or state transition. Test interrupted atomic writes, conflicting reservations, journal gaps/duplicates and malformed records. Run focused/full suite; ledger results, no commit.

### Task 3: Idempotent rank ticks and crash recovery

**Files:** Create `application/research_rank_worker.py`; modify `application/rank_collector.py`; test `tests/test_research_rank_worker.py`, extend `tests/test_rank_collector.py`.

**Interfaces:** Extend `RankCollector.collect(scan_id: str, *, snapshot_id: str | None = None) -> RankSnapshotRecord` (async). Produce `ResearchRankWorker(sessions: ResearchSessions, collector: RankCollector).tick(session_id: str, *, now: datetime) -> dict[str, Any]` (async). Task 5 owns its lifetime role lock; tick owns durable reservation/completion reconciliation.

- [ ] Write `test_reserved_slot_crash_never_reissues_snapshot`: reserve deterministic snapshot ID, simulate death before/after snapshot commit and before completion event; recovery must respectively record interrupted/unknown or link the verified existing snapshot without HTTP. Validate parent/checksum/cohort and scheduled/actual times; unrelated ID/content conflicts fail.
- [ ] Run `uv run --extra dev python -m pytest tests/test_research_rank_worker.py tests/test_rank_collector.py -q`; expect absent optional-ID/tick behavior to fail.
- [ ] Implement optional-ID validation before network creation, existing-ID idempotent read/validation, reservation before acquisition, elapsed missed-slot recording and once-only completion event. Do not retry an orphan reservation even if it is still within the 30s window. Keep collector's three-attempt/15s and cancellation-safe durability behavior.
- [ ] Test restart at +29s/+31s, forward/backward wall jumps, failed/partial snapshots, source/storage blocks and stop during acquisition. Verify no catch-up HTTP burst, original 48h endpoint fixed, actual arrivals drive gaps, manual collector still defaults to unique IDs. Run focused/full suite and ledger result; no commit.

### Task 4: Bounded outcome passes and exactly-once report linkage

**Files:** Create `adapters/binance/research.py`, `application/research_outcome_worker.py`; modify `application/shadow_benchmark.py`; test `tests/test_research_outcome_worker.py`, `tests/test_research_transport.py`; extend benchmark tests.

**Interfaces:** `ResearchOutcomeMarketPort` extends existing `MinuteHistoryPort` with `async close() -> None`. `ResearchOutcomeWorker(sessions: ResearchSessions, market_factory: Callable[[ShadowBudget, dict[str, Any]], ResearchOutcomeMarketPort], benchmark: ShadowBenchmark).tick(session_id: str, *, now: datetime) -> dict[str, Any]` (async). Factory's dictionary is per-pass observed response/cooldown metadata. Add `ShadowBenchmark.freeze_inputs(shadow_id: str, horizon_hours: int, *, as_of: datetime) -> dict[str, Any]` (offline, no writes); extend `.build(shadow_id: str, horizon_hours: int, *, as_of: datetime, report_id: str | None = None, inputs: dict[str, Any] | None = None) -> BenchmarkRecord`. Frozen inputs identify exact parent/child/rank/outcome records and checksums, source/policy and as-of; when supplied, build validates and uses only those inputs. Manual defaults remain unchanged.

- [ ] Write `test_outcome_pass_budget_does_not_touch_unvisited_symbols`: actual transport retries count toward 30, request31 never reaches I/O, outer 90s includes backoff/cleanup, and exhaustion stops the observer rather than letting its `except Exception` write unvisited-cohort unavailable attempts. Compatible successful paths remain cached; cancelled pass leaves only already committed paths.
- [ ] Run `uv run --extra dev python -m pytest tests/test_research_transport.py tests/test_research_outcome_worker.py tests/test_shadow_benchmark.py -q`; expect failure before implementation.
- [ ] Implement an owned guarded public market per pass using existing budget/transport primitives with limits30/90. Wrap minute calls with a private `OutcomePassBudgetReached(asyncio.CancelledError)` signal on guard exhaustion so the unchanged source-hashed observer cannot swallow it; catch that signal as bounded-pass termination, never as a user stop. Separately propagate external cancellation after journaling/cleanup. Regression-test this distinction; do not edit `shadow_observer.py` merely for scheduling.
- [ ] Capture observed weight and 418/429 Retry-After at transport level before client retry logic. Persist shared cooldown events read by both roles; honor an explicit finite positive Retry-After without shortening it, default to the next five-minute slot when absent/malformed. Unrepresentable cooldown timestamps block with operator-visible reason rather than overflow/spin. This is cooperative research throttling, not an account-wide quota guarantee.
- [ ] Implement grace/due/expiry/cache selection from Tasks 1/2. At most one pass per slot, 24h precedence, no paths fetched before due, no invented records for unvisited remainder. On all-compatible-paths complete or fixed expiry, freeze inputs then reserve report UUID/as-of/input IDs/checksums together, then build offline; recover an already committed reserved report by verification instead of rebuilding against later inputs. Freeze exactly one final report per horizon; continue ranks after24h and mark complete only after both reports.
- [ ] Test full-cohort missing paths, delisting, repeated failures, partial commit/restart, source mismatch, +12h expiry, report-commit/link crash and manual benchmark defaults. Insert a late-written rank/outcome record with observation time <=reserved as-of after reservation; assert restarted finalization excludes it and preserves the frozen input set. Verify no duplicate trial, current report manifest/checksum validation unchanged and shorts remain separate. Run focused/full suite; ledger results, no commit.

### Task 5: Explicit CLI and supervised process lifecycle

**Files:** Create `jobs/research_worker.py`; modify `cli.py`; test `tests/test_research_worker.py`, `tests/test_research_cli.py`.

**Interfaces:** `run_research_worker(root: Path, session_id: str, role: Literal["ranks", "outcomes"], *, clock: Callable[[], datetime] = utc_now, monotonic: Callable[[], float] = time.monotonic, wait: Callable[[float, asyncio.Event], Awaitable[None]] = interruptible_wait, stop: asyncio.Event | None = None) -> int` (async). Production defaults and injected clocks/wait share the same scheduling path; no test-only production command. CLI commands are exactly the four in the spec. Add `research-health --session-id UUID --role ranks|outcomes --archive-dir PATH` for offline container health checks.

- [ ] Write `test_workers_never_build_markets_when_stopped_blocked_or_not_due` and CLI tests: start/status/stop are offline; worker requires explicit role/session; naive/unsafe IDs fail before clients; SIGTERM and durable stop release locks after persistence. Run `uv run --extra dev python -m pytest tests/test_research_worker.py tests/test_research_cli.py -q`; expected missing commands/process functions.
- [ ] Implement lifetime lock ownership, interruptible aligned waits and role-specific bounded events. A heartbeat is process liveness, not collection success. Source/storage blocks prevent acquisitions, are operator-visible and are periodically rechecked without tight loops. Terminal sessions remain network-idle so `unless-stopped` does not create a restart loop. No new session enrollment from an idle worker.
- [ ] Implement offline health distinguishing stale/missing heartbeat from collecting, cooldown, blocked or complete status; a blocked/stale worker must not be reported as healthy collecting. Persist observed cooldown, clock discontinuity and shutdown state; don't fake missed rank timestamps on resume.
- [ ] Test duplicate roles, signals during HTTP/commit/wait, stopped-session restart, independent rank/outcome clocks, slow outcome pass concurrent with scheduled rank tick, and exact endpoints. Run focused/full suite and CLI help/doctor; ledger results, no commit.

### Task 6: Optional Compose services and durable deployment controls

**Files:** Modify `compose.yaml`, `.github/workflows/deploy.yml`, `Dockerfile` if packaging needed, `docs/gcp-deployment.md`; create `scripts/research_compose_control.py`; test `tests/test_research_deployment.py`.

**Interfaces:** `parse_research_control(path: Path) -> dict[str, str]`, `compose_argv(release: Path, control: dict[str, str], action: list[str]) -> list[str]`, and script `main() -> int`. Parse only `ATLAS_RESEARCH_ENABLED` and `ATLAS_RESEARCH_SESSION_ID`, invoke Docker via argv, never shell evaluation. Fixed control path `/home/deploy/alfa-terminal/research-control.env`; missing means disabled. Workflow build/up/health/rollback use the same wrapper/profile selection and existing SHA image/project/data paths.

- [ ] Write `test_deployment_control_is_persistent_strict_and_disabled_by_default`: execute the wrapper with a fake Docker executable recording argv; missing/false control never starts research; true+canonical session selects both workers consistently across two SHA releases; duplicates/unknown keys/invalid booleans or IDs fail before deployment commands. No sourcing, interpolation execution, secrets or arbitrary Docker arguments.
- [ ] Run `uv run --extra dev python -m pytest tests/test_research_deployment.py -q`; expect absent wrapper/services to fail.
- [ ] Add profile `research` services `research-ranks`/`research-outcomes`: same built image, separate role commands, archive-only bind mount, no host ports/watchlist state, UID10001/security settings,256MiB/0.5CPU, graceful stop period, `unless-stopped` and offline health checks. Missing session while disabled must still permit Compose configuration/API startup; enabled invalid session fails preflight.
- [ ] Implement safe control wrapper and update deployment to preserve explicit enabled/session selection when replacing SHA releases and using `--remove-orphans`. Explicit disable stops only the two named research services without touching the API or deleting data. Preserve existing API/env/state and Vercel routing; no new frontend config.
- [ ] Test parsed Compose configurations, defaults, security/resource/mount bounds, enabled/disabled transitions and incompatible-source status. Run network-disabled container smoke tests for API plus idle/stopped worker, and existing deployment CI checks. Document separate activation steps and worker evidence checks; ledger any missing Docker/environment blockers instead of claiming deployment readiness. No commit yet.

### Task 7: Acceptance, full release verification and independent review

**Files:** Create `tests/test_research_runner_acceptance.py`; modify README, roadmap and this plan/ledger.

**Interfaces:** All previous producers; no new live-core integration.

- [ ] Write `test_real_child_session_restart_to_two_frozen_reports`: use real qualified/rejected child fixture, controlled UTC advancement, transport-level tickers/minutes and immutable archive. Inject process death at reservation/snapshot/report boundaries; finish both original horizons, preserve unknown gaps and reuse successes without HTTP duplication. Expect complete whole-cohort identities, separate missingness and deterministic final report linkage. Run `uv run --extra dev python -m pytest tests/test_research_runner_acceptance.py -q` RED before completing acceptance wiring.
- [ ] Complete acceptance wiring only. Verify no market construction when disabled, no core/API/cron invocation of workers, existing nonempty core replay and quiet-coil/positioning/four-short parity remain green. Preserve runtime-data hashes and unrelated dirty files; don't claim full-bar short coverage from classifier-only fixtures.
- [ ] Run full pytest (previous514 plus new tests), strict mypy, touched lint/format/diff checks, all backend/frontend/container workflow commands for the proposed release. Record inherited failures by name; no publication while required checks are failing or unavailable.
- [ ] Request the one fresh independent review against spec/plan, scoped full release prerequisites and Review Focus. Fix accepted Critical/Important findings in one RED/GREEN pass and rerun full verification; ledger declined-to-judge rulings/minors. No strategy/source-identity guard weakening to make tests pass.
- [ ] Integrate locally only after verification, preserve unrelated changes, rerun combined-checkout checks and save the exact release manifest. This task completes implementation, not publication or live empirical validation.

### Task 8: Controlled GitHub publication and deployment verification

**Files:** Only explicitly reviewed release files from Task 7; exclude runtime JSON, generated PDFs, unrelated PDF-generator edits/secrets and unrelated frontend work.

**Interfaces:** Existing GitHub Actions main deployment and existing Vercel connection. Use the publishing skill for live branch/remote/PR probes and explicit-path staging; this is a gated operational step, not permission to bypass protected main.

- [ ] Inspect active release instructions, remote/default branch/PR state and exact staged diff; verify all necessary pure-screener/research prerequisites are included and exclusions untouched. Confirm production preflight evidence from `docs/gcp-deployment.md` (state/backup, permissions, no legacy trading service/credentials) before triggering a live rollout; missing access/evidence blocks deployment, not a reason to guess.
- [ ] Create scoped local release commit(s) and publish on the reviewed feature branch/PR path; attach any created PR to this chat. Reverify branch/remote/PR state immediately before publishing. No direct force-push or check bypass; a feature-branch push is not a completed GCP deployment.
- [ ] Follow required PR/CI review handoff. Use the normal approved main-merge/release path only after checks and required protections permit it; identify the exact published/deployed SHA. Deploy code with research disabled first; do not write a live enable/session control file in this milestone without explicit activation approval.
- [ ] Verify GitHub workflow result, GCP direct health/readiness/mode/execution-disabled and Vercel proxy consistency for that release, plus persistent mount/ownership and disabled-worker state. If explicitly activated later, verify heartbeat AND actual snapshot checksums/cadence/request/disk evidence; never infer collection from API health.
- [ ] Record publication/deployment/activation states separately and report blockers truthfully. Keep the recoverable worktree/ledger until successful handoff; no destructive cleanup of uncommitted material. Do not claim 24h/48h empirical results before those original windows elapse.

## Self-review

Verified checkpoint (2026-10-26): Tasks 1–7 complete and integrated locally, uncommitted.
Combined checkout: 650 tests, strict typing/source lint, clean frontend audit/build and offline
CLI checks passed. Synthetic Linux amd64 report capacity: 1,000 symbols/577 snapshots under the
256 MiB cap, reported peak RSS 63,320 KiB. Exact optimized-image packaging smoke passed.
Four independent-review Important findings were fixed with failing-then-passing regressions.
Task 8 is gated by release routing and operational preflight: existing main deploys another API
VM; only the dedicated research VM is authorized. No push, deployment or activation occurred.

Spec coverage: policy/operator boundaries (1/2/5), immutable crash recovery and identities (2/3/4), precise cadence and stop/clock handling (1/3/5), isolated bounded outcome/resume/report finalization (4), resource/cooldown guards (2/4/6), persistent opt-in deployment (6), full acceptance/review (7), scoped verified publication and live checks (8). Each Review Focus case has an owning-task regression. Slot/session/role/UUID types and existing optional-ID extensions agree across producers/consumers. Task 8 remains conditional on access and live preflight evidence; enabling collection is explicitly separate from publishing code.
