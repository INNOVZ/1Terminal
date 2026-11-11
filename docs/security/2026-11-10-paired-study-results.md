# Verified prospective study results — November 10, 2026

Study: `71346822-f686-4bb1-99ec-0f56ef8f0ea1`.
Approved VM: `alfa2-512717/europe-west10-c/instance-20261025-174348`.
Window: November8 13:50UTC to November10 13:50UTC (14:50 Rome time).
Read-only verification; no services, controls, timestamps or data were changed. Temporary
OS Login key was removed and local key files deleted after verification.

## What was independently verified

On-VM checks validated the checksum of the definition, enrollment, comparison, every
snapshot and the24h report. Every observed snapshot's schedule/arrival bounds, whole-cohort
membership and price/rank bounds were independently reconstructed from its raw ticker rows.
The24h report's symbol returns and first observed gainer times matched reconstruction.
48h figures below were independently computed from the verified snapshots. At the last VM
check the automatic48h report file was still pending; it is not claimed finalized here.

Running image ID is `sha256:9c3476cb1d21cb6efc655691a56e2408f2e534d864ef63d0c149a95afd988339`,
which differs from the exported release ID. The study's recorded backend source hash,
runner/rank-contract hashes and tracked package versions match the prepared release.
This is source/runtime fingerprint evidence, not proof that both container images are
byte-identical or a new vulnerability audit.

## Collection quality and actual scan scope

- Frozen cohort:512 canonical eligible USD-M symbols.
- Deep scan: **48 selected/evaluated**, not the planned full320 capacity. Both arms used
  this same48-symbol selection. Counts:547 eligible instruments,462 liquid,0 evaluation
  failures; control2 Stage1 survivors/1 publication; challenger4 survivors/3 publications.
-577 scheduled files: **576 observed**, **1 missed/late**, no absent files.
- Anchor observed13:50:04.895UTC Nov8:498 known member values,14 unknown.
-24h endpoint observed13:50:03.698UTC Nov9:511 known,1 unknown.
-48h endpoint observed13:50:08.912UTC Nov10:512 known,0 unknown.
- Across observed samples, known values averaged505.40/512, ranging466–512. Unknown
  competitors also widen valid members' rank bounds; unknown ranks are not outside ranks.

The one missed slot was289, scheduled November9 13:55UTC, attempted13:59:23.528UTC,
with reason `slot_late`. The24h report's as-of was13:50:48.381UTC and its file publication
mtime was13:59:14.296UTC: about506 seconds later. The runner awaits report reconstruction
before its next tick; this source path and chronology establish report blocking as an
operational problem on the actual VM. Local measured capacity did not establish real
VM scheduling margin. Its health checks also exceeded15s; Docker reported no OOM.

## Frozen candidate outcomes

Returns are sampled prices relative to the common observation anchor. They are not
returns from suggested entry, executed trades or stop/target fills. High/low figures are
five-minute sampled extrema, not candle-wick extremes.

| Candidate | Arm |24h return |48h return |48h sampled maximum |48h sampled minimum |Observed top10 |
|---|---|---:|---:|---:|---:|---|
|ZECUSDT |Control + challenger |-3.99% |-7.76% |+0.66% |-10.37% |No |
|1000PEPEUSDT |Challenger |+1.48% |+6.27% |+6.53% |-3.52% |No |
|INJUSDT |Challenger |+0.57% |-0.43% |+2.12% |-3.41% |No |

The control had0/1 observed top10 hits; challenger0/3. All three were provably outside
at the anchor, and their membership was known/outside in the observed snapshots. The
single missed slot and between-sample events remain unknown.1000PEPE's best observed
rank was12; INJ's lower bound34 and ZEC's lower bound38.

On the preregistered nonpositive-endpoint-return criterion, the48h control had1/1
nonpositive candidates and challenger2/3. Funding carry expanded publications from1 to3,
but did not establish improvement on the primary top-gainer objective in this cohort.
This sample cannot establish calibrated win rates or general predictive advantage.

The momentum reference contained10 symbols selected from capture-time rolling returns.
At24h,9/10 were observed in top10 at some sample, but all10 had unknown initial rank
membership under the anchor's missing-data bounds. This is not evidence of9 early
predictions. The report recorded0 eligible early detections for that reference.

## Missed opportunities: established selection versus downstream rejection

|Symbol |48h anchor return |Last established control funnel evidence |
|---|---:|---|
|INITUSDT |+82.66% |Eligible; excluded by selection limit |
|PIEVERSEUSDT |+41.13% |Eligible; excluded by selection limit; initial rank unknown |
|ARIAUSDT |+25.98% |Eligible; excluded by selection limit |
|EDGEUSDT |+19.35% |Eligible; excluded by selection limit |
|AIOTUSDT |+18.92% |Selected; Stage1 no_setup; Stage2 not_admitted |
|BRUSDT |+13.98% |Eligible; excluded by selection limit |
|B2USDT |+13.65% |Eligible; excluded by selection limit |
|SOPHUSDT |+13.58% |Eligible; excluded by selection limit |
|CYBERUSDT |+12.56% |Selected; Stage2 admitted; enrichment_unavailable_or_no_setup |
|CAPUSDT |+11.52% |Eligible; excluded by selection limit |
|MERLUSDT |+10.61% |Eligible; excluded by selection limit |
|AIOUSDT |+9.02% |Eligible; excluded by selection limit |

These are12 largest matched-anchor48h returns among members with known baseline and
endpoint prices, not a Binance spot leaderboard. All were observed within the cohort's
rolling24h top10 at some sample, but exact first entry times are not certified when rank
bounds were unknown.23 initially-outside symbols were observed top10 within the first24h
and were missed by both arms. Full bounded summaries retain their identities.

A selection-limit exclusion does not establish that a320-symbol scan would publish that
token; it would only give it an opportunity to undergo deeper evaluation. Stage1 no_setup
and the combined enrichment/no_setup reason do not identify an exact failed sub-gate.
Further input-level replay is needed before changing strategy rules.

## Recommended order

1. Separate reporting from the time-critical sampler and make health checks lightweight.
   Verify actual VM scheduling margin; keep the completed data/anchors immutable.
2. Diagnose why this deployment captured48 rather than320 and restore the intended
   preregistered scan budget for a future qualified trial.
3. Use this existing evidence to trace selected-but-rejected winners and selection misses;
   distinguish scan-budget effects from strategy-gate effects before new rule changes.
4. Only then run fresh full-budget, preregistered cohorts across additional periods. This
   cohort supplied useful operational/strategy evidence but did not demonstrate early
   top-gainer discovery by the published candidates.

## Evidence and access limitation

`docs/security/evidence/2026-11-10-paired-verification.json`,
`2026-11-10-paired-verified-summary.txt` and `2026-11-10-paired-publication-check.txt` retain
bounded results. Original study stays on the VM at
`/home/deploy/atlas-paired-study-20261108/study`.

Automatic approval review rejected exporting the full private study archive as broader
than results verification. A safer on-VM checksum/rank/outcome validation with bounded
summaries completed instead. No raw archive or full snapshots were transferred locally.

## Publication follow-up

A further read-only check at2026-11-10T14:07:53UTC found the automatic48h report
still pending. This does not change the independently validated48h snapshot outcomes.
No collection or report rebuilding was triggered; temporary access credentials were removed.

## Finalized analysis available

The48h report was subsequently published at2026-11-10T14:10:49UTC, and its checksum passed.
See [finalized 24h/48h data analysis](2026-11-10-paired-data-analysis.md) for both-horizon
metrics, exact frozen-input rejection diagnostics and the selection-only budget comparison.
Earlier pending-publication notes above are preserved as historical checks.
