# Research missing-data policy v3 — approved implementation scope

## Problem observed on the research VM

The frozen 513-member v2 study now includes `VELODROMEUSDT`, absent from later
Binance ticker responses. At 14:34 UTC on October29, the repaired worker captured
512 valid fresh rows within7.4 seconds of the scheduled slot. V2 correctly labels
that capture partial because it accepts only stale missing values up to1% and
does not accept omitted rows. This makes all later rank evidence unusable under
v2 even though many individual memberships can be bounded mathematically.

## Proposed change for a new cohort only

Introduce explicit `bounded-shadow-v3`, `prospective-ranks-v3` and a matching
report policy. Keep v1/v2 parsing, replay, freshness checks and outputs unchanged.
Never relabel existing v2 archives with v3 outcomes or bypass source identity.

Retain every member of the frozen original cohort. Treat an omitted, stale,
future, duplicate or malformed canonical ticker as unknown for that member.
Do not reuse its stale price, assign a zero return, remove it from the denominator,
or substitute another contract. Valid rows continue to require positive finite
prices, finite returns, an unambiguous canonical symbol, age<=60s, future skew<=5s,
and a cross-valid-row cutoff span<=60s.

For a valid member s, with U unknown cohort members:

- lower rank = 1 + number of valid members with a strictly greater return;
- upper rank = lower rank + U;
- certify inside top10 only when upper<=10;
- certify outside top10 only when lower>10;
- crossing intervals and members without a valid row remain unknown.

Keep competition-rank tie semantics. Assert exact rank only when U=0. Mark an
otherwise valid mixed snapshot bounded when at least one row is valid; captures
with no valid information or an invalid valid-row time span remain partial.
This removes a global stale-only/1% rejection rule, not mathematical uncertainty.
When U>=10, no valid member can be certified inside top10 solely from these
conservative bounds. Reports must expose U and the valid fraction explicitly.

## Interpretation and safeguards

Sampling arrival and data completeness are separate. Bounded arrivals cannot be
presented as whole-cohort complete observations. Reports retain per-token unknown
sample counts, initial unknown partitions, gaps, and insufficient-sampling states.
No transition or absence is inferred through an uncertain rank sample. The
momentum reference remains unavailable without its required complete initial
rank. A study with too much missing data must remain inconclusive.

The screener's core selection, scores, grades, gates and published candidates do
not change. This is public-data research only. Scheduling windows, request limits,
cohort size caps, storage safeguards and execution-disabled boundaries remain.

## Verification and activation

1. Prove bounds against exhaustive assignments of small unknown cohorts,
   including ties, missing leaders, duplicate/malformed rows and the rank10 edge.
2. Reconstruct bounds from raw checksummed evidence at archive enrollment,
   observation and report boundaries; reject falsified coverage/status fields.
3. Verify v1/v2 fixtures retain their original behavior, reports do not mix policy
   versions, and incompatible old sessions fail their source identity checks.
4. Verify snapshot-to-timeline-to-frozen-report behavior with an omitted original
   member, uncertainty intervals and a complete fixed-forward outcome cohort.
5. Pass the full suite, typing, lint, constrained container checks and the real
   archive timing gate before the research VM rollout.
6. Preserve and stop the degraded v2 study only when the new release and capture
   procedure are ready. Capture exactly one fresh current-universe parent/child,
   verify its checksums, enroll it with original child anchor, then verify several
   five-minute arrivals and bounded whole-cohort outcome throughput. No recurring
   scans or historical prospective backfill.

Jithin approved the versioned v3 policy in the October29 approval reply.
Implementation resumed October30 after interruption. Existing evidence remains
under its frozen policy. Verified release3f9b36f is deployed research-only;
one fresh512-member cohort/session is active with source anchorOctober30 04:30UTC.
See `docs/security/2026-10-29-research-recovery.md` for current verification,
immutable IDs, observation endpoints and remaining operational closeout checks.
