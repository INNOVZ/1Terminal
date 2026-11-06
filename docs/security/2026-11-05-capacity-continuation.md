# Capacity-test continuation pointer

CURRENT VERIFIED STATUS: local collection checks and corrected report retests PASSED.
Full suite740passed/1existingwarning. Corrected24h528.616655s,48h763.435117s, bothunder900s
at256MiB/.5CPU;512complete outcomes/577ranks; manifests/payloads identical tobaseline; noOOM.
All own test containers removed after evidence retention. Nothing is running for this local probe.
No GCP/deploy/commit/push. Code remains in the existing isolated worktree, uncommitted.
Read that worktree docs/security/2026-11-05-capacity-test.md and evidence/2026-11-05-capacity
for exact segmented evidence and remaining gates. Below is chronological resumption history.

Active work remains in `/Users/jithinjacob/.codex/worktrees/dual-track-shadow/trdindicator`.
Read its `docs/security/2026-11-05-capacity-test.md` before continuing. No GCP deployment occurred.

Synthetic root: `/private/tmp/atlas-capacity-full-20261105` (contains run-state.json/capacity.json).
Frozen source and hashes: `/private/tmp/atlas-capacity-source-20261105`.
Own local test containers:
- atlas-capacity-full-ranks-20261105
- atlas-capacity-full-outcomes-20261105

Shared start epoch1793850185.636224, planned supervisor stop epoch1793851145.636224.
Each container:256MiB/.5CPU, non-root, no network, read-only source/root, synthetic archive writable.
Do not restart a finished run on the same session and claim a fresh cadence result. Read outputs/logs
and inspect exit/OOM status first. The full report-finalization probe is a separate phase after
the two-worker run, using role=finalize and the same resource limits/source/root.

The4-member supervisor/transport smoke passed; first two full outcome acquisition passes each
used30 requests and zero full report assemblies. Full results are not yet accepted from partial logs.
No real study archive, worker, source threshold, Git commit, push or deployment was changed.

Resumed verification: both collection roles exited0, noOOM. Independent collection checks passed:
4rank slots, maxarrivalgap306.956162s, all49 missing24h paths completed,4passes within90s.
Provisional details: /private/tmp/atlas-capacity-evidence-20261105/provisional-report.json.
Full finalizer started04:03:25UTC in atlas-capacity-full-finalize-20261105;24h report completed
in669.502456s (freeze319.386343s),512complete paths/577ranks, independently reopened successfully.
48h stage is running from approximately04:14:35UTC;900s deadline criterion perhorizon unchanged.
Do not declare full capacity passed before48h result and final independent verification.

Final result: COLLECTION PASS, OVERALL CAPACITY FAIL. Both24h/48h saved reports independently
reopened with512complete outcomes and577ranks; noOOM/cleanexit. 24h669.502456s passed;
48h1015.966903s failed the fixed900s gate. All three exited test containers removed after
retaining evidence. Final machine report and runtime hashes are in the isolated worktree under
docs/security/evidence/2026-11-05-capacity. No GCP/strategy/deployment changes.
Native-host frozen48h reconstruction profiler is running (PTY session59838); scratch driver:
/private/tmp/atlas-capacity-evidence-20261105/profile-report.py. It writes report-profile.txt,
report-profile.pstats and profile-result.json on completion and asserts identical frozen
manifest/payload. Treat host profiling as diagnosis, never a resource-limit capacity pass.

Profiler completed276.586624s; exact manifest/payload unchanged. Canonical serialization consumed
~76.6% cumulative CPU time. An exact built-in primitive fast path (6lines) now avoids unnecessary
checks while retaining enum/subclass tagging. Two compatibility regressions include literal wire
bytes and a fixed pre-change nested-record SHA256. Full suite:739passed,1existing Starlette warning,
133.68s; changed-module mypy passed using scratchcache; Ruff/static checks passed.

Optimized BOTH-horizon report retest now running from~2026-11-05T08:47UTC in own container
atlas-capacity-optimized-finalize-20261105, same256MiB/.5CPU and unchanged900s perhorizon criterion.
Source: /private/tmp/atlas-capacity-source-optimized-20261105 (87fingerprints; only serialization
module differs). Output: /private/tmp/atlas-capacity-finalization-optimized-20261105.
Original full fixture mounted READONLY at /baseline; new reports/results go into new output root.
Do not overwrite baseline evidence or claim optimized pass until BOTH stages and checks finish.

REVIEW CORRECTION: the first optimized retest was deliberately stopped and removed, unaccepted.
A red regression showed tuple type-membership invokes custom metaclass equality. Corrected the
fast path to use identity-only checks; enum/subclass semantics and unsupported-object errors now
retain the baseline path. Full suite rerun is active(session77880); Ruff/mypy passed after correction.
Do not treat740tests as passed until final exit output is seen.
Latest candidate snapshot: /private/tmp/atlas-capacity-source-optimized-v2-20261105 (87hashes).
Latest pending output root: /private/tmp/atlas-capacity-finalization-optimized-v2-20261105.
These are fresh; no v2 capacity container has started yet. Original baseline fixture remains intact.

Corrected candidate full suite confirmed:740passed,1existing Starlette warning,128.81s.
Now running atlas-capacity-optimized-v2-finalize-20261105 from~08:52:30UTC.
Both24h/48h stages;256MiB/.5CPU; original fixtureREADONLY;900s perstage unchanged.
Latest source/output paths are the v2paths immediately above. Independent retest verifier:
/private/tmp/atlas-capacity-optimized-evidence-20261105/verify.py (run with /baseline readonly,
/frozen-source=v2source readonly, /app/data/research=v2output readonly, /results=optimized-evidence
writable). It compares BOTH saved report manifests/payloads to baseline and enforces time/coverage.

Corrected v2 first horizon PASSED:24h528.616655s (freeze252.178213s), versus baseline669.502456s
(~21%faster),512complete outcomes/577ranks, zeroOOM. Report474540dc-d21b-4185-85a0-a9f99c9cf25d.
48h stage running from~09:01:39UTC; do not accept overall pass until its timing and bytewise
baseline comparison succeed. Keep this SAME container/root/source, no restart/backfill.


CLOSED: corrected v2 finalizer and independent verifier both exited0. Verified48h763.435117s
(~24.86%faster),24h528.616655s(~21.04%faster), unchanged900s threshold. Both saved report manifests
and payloads exactly match baseline;512complete outcomes/577ranks each. All87 frozen hashes
validated and current worktree encoder matches the measured source. Finalizer cleanexit/noOOM;
all own containers removed. Original fixture/source snapshots retained in scratch for reproduction.

Durable evidence is in isolated-worktree docs/security/evidence/2026-11-05-capacity, including
optimized-report.json/runtime-v2.json and baseline failure/profile proof. No current-source concurrent
soak, realVM/live-network qualification or immutable image release is implied. Next: terminal-idle
health semantics, integration/package review, then authorized-VM qualification with collection disabled.
