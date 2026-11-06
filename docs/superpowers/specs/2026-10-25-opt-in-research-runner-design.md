# Opt-in prospective research runner and controlled delivery

Status: Tasks 1–7 implemented, independently reviewed, integrated locally and verified.
Publication/deployment remain gated; no collection activation. See the execution ledger for checkpoints.
Fresh independent builder check completed: nothing important left to clarify before implementation
planning. Jithin approved the numerical defaults and one-session v1 boundary as part of this design;
they are not an already-enabled deployment or validated production-capacity claim.
The observation, shadow and rank/benchmark milestones are already locally integrated. Their last
verified checkpoint is 514 passing tests, strict source typing and touched-file lint/format checks.
Do not rebuild them or silently turn this into a strategy/scoring change.

## Intent and agreed direction

Jithin wants the next research milestone implemented, verified and published through GitHub so the
existing pipeline can deploy the backend to GCP; the frontend is already connected to Vercel.
A sleeping laptop cannot sustain prospective sampling. The selected direction is a separate opt-in
research worker using the existing Python/container stack and persistent research archive, disabled
by default. Deploying its code does not authorize automatic collection or change live publication.

This first slice runs one explicitly enrolled frozen child/parent cohort for its original 24h/48h
windows. It records subsequently observed rolling top10 membership and produces the existing honest
matched-anchor reports. It does not automatically produce new candidate decisions every few minutes.
The operator first chooses a complete archived shadow child; creating one still uses the existing
fresh-parent, same-source admission workflow. No automatic rescans, universe refresh, OI/depth scans,
candidate promotion, trading/account endpoints or exchange credentials are introduced.

## Approaches

1. Separate opt-in Compose research services: selected direction, matching the existing GCP Compose
   deployment and shared data mount. Independent rank and outcome processes prevent slow historical
   price collection from delaying rank snapshots. This adds worker/session lifecycle verification.
2. Put scheduling in the API/cron process: rejected because API restarts, requests and slow outcomes
   would share lifecycle/resources with research timing and increase coupling to live publication.
3. Laptop or ad-hoc VM shell loops: rejected as the operational target because sleep/disconnects,
   duplicate loops and lack of durable session state make missingness/restart behavior unreliable.

Docker profiles gate optional services, and Docker restart policies provide process recovery, not
application checkpoints or guaranteed sampling. See the official [profile documentation](https://docs.docker.com/compose/how-tos/profiles/)
and [restart-policy documentation](https://docs.docker.com/engine/containers/start-containers-automatically/).

## Proposed operator contract

The commands/numerical defaults below are engineering proposals, not already implemented or evidence
that a sample count or horizon establishes predictive advantage.

- `atlas research-start --shadow-id UUID --archive-dir PATH`: validate and enroll an existing complete
  child and its exact checksummed parent/cohort; return a session UUID, frozen anchors/endpoints and
  status without making exchange requests. One nonterminal session per archive in v1; reject a second
  rather than replace the active session or change its cohort. The original cohort must be <=1000.
- `atlas research-status --session-id UUID --archive-dir PATH`: offline current state, heartbeat,
  actual sampling gaps, due/partial/complete horizon jobs, request accounting and report IDs.
- `atlas research-stop --session-id UUID --archive-dir PATH`: durable stop request, no deletion.
- `atlas research-worker --role ranks|outcomes --session-id UUID --archive-dir PATH`: explicit opt-in
  process. Each role owns a separate exclusive OS advisory lock under the archive root; locks release
  when the process dies. No worker invocation from API/runtime/core cron, no public mutation API.

Session enrollment does not reset the clock to enrollment time: rank observation ends at the child's
existing 48h endpoint, and 24h/48h paths use the child's existing next-full-minute anchor. Late
enrollment/resume leaves earlier rank slots unknown and visibly reduces coverage; it never backfills
historical rolling ranks or starts a new 48h window for an old child. Refuse enrollment after the
48h observation window. Existing session commands are idempotent by session/child identity; duplicate
enrollment of the same child returns the existing session rather than making a second experiment.

## Durable state and restart semantics

Use a separate versioned session namespace under `data/research/sessions/SESSION_UUID/`, not SQLite
watchlists or rewrites of ScanRecord/ShadowRunRecord/RankSnapshotRecord/BenchmarkRecord schemas.
Keep an immutable checksummed session definition (parent/child IDs and checksums, original cohort,
policy, measurement/source identities, original anchor, both endpoints, enrollment timestamp).
Append immutable event/attempt records; derive status from them. A replaceable atomic checkpoint may
accelerate reads but is not the authoritative record. Corruption/unsupported versions fail closed.
Persist UTC timestamps and monotonic elapsed-time accounting; reject naive times.

Each due rank slot is reserved durably before acquisition. Completion links the exact snapshot ID
and checksum. After a crash, a reserved slot without completion is interrupted/unknown, never assumed
successful or retried as though it were an on-time observation. If a snapshot committed before its
completion event, reconcile by its preallocated ID/checksum; do not issue a duplicate HTTP request.
Expose missed/reserved/interrupted/partial/failed slots and the last successful arrival separately
from a process heartbeat. Never create a success heartbeat merely because the daemon is alive.

The current collector allocates its own UUID: implementation may add an optional caller-supplied
canonical snapshot ID solely for this idempotency boundary, with existing manual behavior unchanged.
This must be regression-tested for conflicts, crash recovery and cancellation. No archive overwrites.

Session source identity pins the supported rank interpretation policy and the existing outcome
measurement identity. A release must not silently reinterpret an active session under changed
semantics: report `source_mismatch` and stop acquisition for that session until an operator selects
the compatible release. Keep all evidence readable; do not weaken existing replay identity guards.

## Rank process: cadence and missingness

Use 300-second slots anchored to `outcome_anchor(child.completed_at)`, through the inclusive 48h
endpoint. Do not sleep relative to the previous request's finish or accumulate timing drift.
Acquire at most once per slot, no concurrent captures and no catch-up burst. On restart, mark elapsed
unclaimed slots missed, then wait for the next due slot. A launch during a current slot may capture
once if its lateness is <=30 seconds; otherwise skip it explicitly. Record scheduled and actual times.
Backward/forward wall-clock jumps cannot repeat a slot; log clock discontinuity and calculate gaps
from actual arrivals. The process uses interruptible waits and responds to SIGTERM/durable stop.

Reuse the original whole-cohort rank collector: <=3 actual HTTP attempts, <=15 seconds for acquisition
and cleanup, raw provider evidence and explicit incomplete coverage. Durable disk publication can
outlast cancellation; report that boundary rather than promise a hard filesystem wall-clock deadline.
Keep recorded exchange weight distinct from attempt counts. A five-minute target and ten-minute
coverage threshold are existing engineering defaults, not proof of exact entry time or absence.

## Outcome process and report finalization

Historical price collection must not run on the rank process's event loop. The outcome role checks
the frozen 24h and 48h due endpoints, with a 60-second publication grace before its first attempt.
Read existing compatible complete per-symbol paths before fetching any missing data. Use the existing
ShadowObserver and its child anchor/measurement identity; do not edit its source-hashed measurement
code merely to add scheduling. Preserve original whole-cohort denominators, shorts and missing paths.

2026-10-26 approved review amendment: Jithin explicitly permits an optional exact whole-cohort
traversal permutation on ShadowObserver to prevent failed-prefix starvation in bounded passes.
The default manual order, cohort, anchors, formulas and source guards are preserved. The runner
durably reserves each symbol visit before fetching and prioritizes least-visited symbols across
restarts; reservations are not fabricated outcome attempts. This approved observer source change
changes the measurement fingerprint: incompatible old active sessions must remain blocked and
archived evidence stays readable, never silently relabelled or rewritten.

Review hardening also requires request-window checks immediately before rank market construction,
late committed snapshots retained as interrupted rather than poisoning recovery, outcome HTTP
budgets capped by the original retry expiry, and streaming report assembly without retaining full
raw/normalized snapshots for the whole horizon. The 256MiB cap and supported cohort remain intact.

Proposed per-pass cap: 30 actual public HTTP attempts including retries, 90 seconds including network
backoff and cleanup, sequential requests and existing >=0.2-second per-symbol pacing. A guarded
transport and an outer deadline bound actual I/O and iteration. Never use the current unbounded
manual outcome job unchanged as a background task. A timed-out pass retains already committed
per-symbol attempts; later passes resume via compatible successful caches. Do not invent an outcome
for the unvisited remainder. One outcome pass at a time, at most one per five-minute slot; 24h takes
precedence if both are due. No outcome requests before the relevant horizon is due.

The maximum retry window is 12 hours after each horizon endpoint. Freeze a report when the whole
cohort's compatible paths are complete, or at retry-window expiry with missing paths explicitly
unavailable. Failed passes do not reset the retry window. At 24h, rank collection continues for 48h.
For the final descriptive report, use a saved as-of cutoff and exact archived input manifest; repeat
finalization after a crash must link the same report ID rather than silently generate new trials.
Reserve each horizon's report UUID/as-of durably before finalization; add an optional caller-supplied
canonical report UUID to the existing offline builder if needed, preserving its manual defaults.
Freeze the exact report input IDs/checksums with that reservation, not just as-of: a delayed archive
commit can carry an earlier observation timestamp and must not silently enter a restarted finalization.
On recovery read/verify an already committed reserved report before attempting another build.
Missing rank samples or initial evidence remain unavailable even if price paths later become complete.

After both horizons have final reports, mark the session complete; a stopped session never resumes
automatically. An enabled daemon may remain idle with a terminal status so `unless-stopped` does not
create an exit/restart loop. No automatic enrollment of subsequent scans in v1.

## Resource and exchange safety

One session, one rank process and one outcome process per archive root; locks also protect against
accidental duplicate manual workers. Proposed worker container caps: 256MiB memory, 0.5 CPU each;
failure under the cap is explicit, not permission to change the universe. Persistent data is shared
with the existing archive but workers do not mount the watchlist state volume or expose ports.
Use UID10001, read-only root filesystems, tmpfs, dropped capabilities and no-new-privileges as the API
already does. Explicit archive mounts only; no Docker socket, SSH keys or exchange credentials.

Before every pass, require >=2GiB free space and total research-archive bytes below a proposed 5GiB
limit, including sibling rank/outcome blobs rather than counting only the session event directory. At a limit,
pause with `storage_blocked`, retain evidence and request operator attention; no retention deletion.
Bound status/event volume by slots/passes and do not spin on errors. Disk thresholds are safeguards,
not a guarantee that a 1000-symbol session fits. Measure actual growth and VM capacity before enabling.
Observe account-wide exchange weight and Retry-After responses; pause later research work rather than
retry aggressively. Existing clients do not implement a shared cross-process/IP rate limiter, so do
not claim a global quota guarantee. Load acceptance must show no unacceptable core scan degradation.

## Deployment and explicit activation

Extend Compose with optional research-role services sharing the verified application image and the
same persistent `/app/data/research` archive. Keep normal `docker compose up` API-only via a research
profile. The API remains one worker and retains its existing data/state mounts and port mapping.
The frontend needs no changes for this milestone; do not alter Vercel backend routing.

2026-10-26 scope amendment from Jithin: deployment is now to the dedicated research-only VM
`instance-20261025-174348`, project `alfa2-512717`, zone `europe-west10-c`. No other VM/project,
project IAM or existing live API deployment may be changed. Use a separate `atlas-research`
Compose project and explicitly named research services; never start the API on this VM.
The existing API deployment job is retained unchanged. Its main-branch trigger is a publication
gate: do not merge/push main and inadvertently deploy another VM under the newer scope.
Verification-only CI additions do not authorize that deployment. The archive remains persistent
and control-file selection remains outside SHA releases as described below.

Persist the explicit research enable/session selection outside SHA release worktrees in a small
operator-owned control file at `/home/deploy/alfa-terminal/research-control.env`. Missing file means
disabled. Only `ATLAS_RESEARCH_ENABLED=true|false` and one canonical `ATLAS_RESEARCH_SESSION_ID` are
accepted; unknown/malformed keys fail deployment preflight, never source the file as shell code.
This file is not committed and is not mounted writable into the application containers.

Deployment reads that control before constructing Compose arguments. Build, up, health checks and
rollback use the same profile/session selection so later normal deployments with `--remove-orphans`
cannot accidentally abandon an explicitly enabled worker. Disabling explicitly stops the two named
research services, not the API, and preserves sessions/archives. A source-incompatible release reports
the blocked session rather than silently restarting it against a different policy.

Deploy code disabled first. Validate persistent UID ownership, locks, clock, disk headroom, restart
behavior and bounded public request load before explicit collection activation. Do not equate an API
health check, successful Git push or Vercel build with a healthy collecting worker. Research status
must distinguish disabled, starting, running, degraded, storage/source blocked, stopped and complete.

## Publishing scope and verification

Jithin now authorizes eventual GitHub publication/deployment, superseding the previous keep-uncommitted
delivery preference for the verified release only. Do not push until design/plan approval,
implementation, independent review and the exact release's checks are complete. Confirm Git remote,
branch/PR state and CI/deployment triggers before external writes; never force-push or bypass checks.
Use a reviewed feature branch/PR and the repository's normal protected-main route where applicable.
Publishing a feature branch alone is not a main deployment; report which state actually occurred.

Release needs the existing pure-screener/research prerequisites as well as the new runner. Review
their full staged diff and CI compatibility, not only new worker files. Never blanket-stage the dirty
checkout: exclude runtime JSON, generated PDFs, unrelated PDF-script edits, secrets and other unrelated
changes. Include frontend changes only if they are reviewed prerequisites, not because Vercel is linked.
Keep a recoverable isolated worktree/ledger through verification and publication.

Tests must cover: duplicate enrollment/roles, checksum and policy drift, preallocated-ID crash windows,
no catch-up/repeated slots on clock jumps/restarts, exact 24h/48h boundaries, graceful shutdown and
cancellation-safe durability, whole-cohort missingness, actual HTTP retry/deadline enforcement,
outcome resume/grace/expiry, deterministic report finalization, disk/resource guards, persistent
profile/session activation across a SHA deployment and disabled-default zero-network behavior.
Use real classifier/observer/report code with transport-level fixtures, including a qualified child
and a rejection. Keep the previous 514 tests green; run strict typing, touched lint/format/diff checks,
full deployment CI checks and network-disabled container smoke tests, then fresh independent review.

After publishing, verify GitHub checks and exact deployed SHA, GCP direct API health/readiness and
the Vercel proxy, then worker status/persistent evidence if explicitly activated. Do not claim a full
24h/48h empirical result before its horizon elapses. Missing access or failed CI is a reported blocker,
not permission to declare deployment complete.

## Still outside this milestone

Automatic recurring candidate enrollment, dashboard presentation, rank-source expansion to Binance
Spot/global UI, signal-score changes, threshold calibration, probabilities, promotion, execution,
unrelated frontend redesign, backup/restore implementation and claiming predictive advantage.
