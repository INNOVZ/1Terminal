# Radar lifecycle hardening — November 8, 2026

Scope: local pure screener/radar changes in the main checkout. No deployment, trading,
account access, worker activation, research enrollment or modification of prior studies.
The pre-existing modified `data/screener_latest.json` is outside this change.

## Behavior and architecture

- Domain lifecycle decisions are separate from the new application refresh service.
  The latter owns public provider calls, concurrency (8), deadlines, freshness checks
  and unknown-data retention. The API assembles/persists the radar; the UI displays it.
- Fresh radar entries require exactly five displayed checklist items, all true, before
  the 16-new-pick limit. Engine structural gates, scoring and publication thresholds
  remain unchanged. Engine consensus and displayed radar checks are distinct contracts.
- Re-verification distinguishes passed, failed and unknown. Missing data cannot produce
  5/5. Unknown gates count as neither passed nor failed; the UI shows missing-data warnings.
- EMA50/bandwidth use actual closed 4h candles. The 7/14-day contraction health check
  requires 336 closed hourly candles. Stale/degraded derivatives are unavailable.
  Discovery POC/VAL/VAH remain explicitly labeled frozen structural references.
- Health checks are versioned `retention-health-v2`; they do not claim full original
  admission-rule parity. Discovery checklist/core verification are preserved separately.
- Current decisions use public quotes <=60s old (5s clock tolerance), not hourly closes.
  A confirmed current stop is processed even during a candle outage. Candle freshness,
  continuity and coverage since discovery/last check are validated before historical use.
- Observed triggers persist for both directions across rolling candle windows. A trigger
  observation does not assert an executed fill. The UI uses `TRIGGER OBSERVED` and does
  not infer a trade simply from price moving away from entry.
- Pre-discovery/overlapping candles cannot establish post-discovery historical events.
  Closed post-discovery target wicks count. Stop takes conservative priority when both
  stop and target are touched; OHLC does not establish intrabar execution order.
- Untriggered setups expire at age >=72h, including entry shelves. Observed triggers
  can outlive that deadline. Late breakouts do not revive expired, untriggered setups.
  This deliberately removes the previous indefinite shelf exception.
- Requalification of the same symbol/direction/archetype preserves original discovery,
  bracket and trigger history even if a new score/levels differ. Changed archetypes or
  direction create new theses; supersession is recorded. Same-thesis qualification
  cannot bypass confirmed invalidation during that reconciliation.
- Every scan writes a UUID-named operational record under `data/radar_events/`, including
  scan ID, refresh/removal/supersession reasons, decisive quotes and setup snapshots
  without candle arrays. Audit failure is exposed. Results distinguish fresh, retained
  and unavailable counts and expose lifecycle version `radar-v2`.

## Verification

The focused tests cover missing/stale data, frozen metric rejection, actual 4h metric
computation, long/short triggers, event timing, expiry, target wicks, stop priority,
provider outages, current quote stops, requalification and persisted removal records.
Completed check totals are appended below after the final regression run.

## Limits and deployment handoff

- Reconciliation remains scan-time, not continuous monitoring. Events after the last
  verification are not guaranteed to be reflected immediately.
- Legacy or outage records with insufficient evidence are explicitly unavailable;
  triggers/history are not retrospectively invented. Unknown candidates may remain
  visible during prolonged outages.
- Audit files require operator backup/retention/disk review. They are not a checksummed
  research archive and do not enroll a prospective 24h/48h study.
- Strict 5/5 radar admission can show fewer cards than core publication. Same-thesis
  requalification deliberately retains original brackets rather than refreshing them.
- Verify exact production VM/release, audit-directory permissions, provider behavior,
  resource use and UI labels after deployment. Local mocks/builds do not establish this.
- The isolated research worktree remains separate. Do not overwrite its prerequisites
  or waive source/version guards. No live deployment or research activation occurred.

## Completed verification

- Full backend regression: 680 passed, one existing Starlette warning, 113.47s.
- Final focused lifecycle/API/publication/runtime checks: 47 passed, same warning, 0.69s.
  This includes 31 lifecycle tests and the final expiry/target guard added after the
  full-suite run. Final source was rechecked with these focused tests and static checks.
- Scoped mypy: no issues in domain lifecycle and application radar service.
- Ruff checks/format and Git whitespace check passed for changed Python files.
- Frontend TypeScript (`--noEmit --incremental false`) and targeted ESLint passed.
- Next.js 16.4.2 production build passed. No live browser/provider acceptance performed.
- Publication regression fixtures now explicitly supply five displayed gates, preserving
  tests for core 1.55R shorts, core consensus differences and relative publication order;
  separate tests assert rejection of a fresh 4/5 radar candidate and acceptance of 5/5.

No code was committed, pushed or deployed. These checks establish the tested lifecycle
behavior, not predictive performance or absence of all system vulnerabilities.
