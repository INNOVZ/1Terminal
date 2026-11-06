# Atlas observation review — 2026-11-02

Needs revision for a complete discovery study. Review completeness: Partial.
The retained records were verified; missing observations and predictive edge remain unverified.
No VM data, reports, services, scoring rules, Git history, production deployment, or study anchors were changed.
Only local audit notes/scripts were added. This review used the data-validation skill to separate data integrity, collection completeness, and strategy conclusions.

## Scope and provenance

Read-only verification started 2026-11-02 04:04:44 UTC, against only project alfa2-512717,
zone europe-west10-c, instance instance-20261025-174348 (ID 6237690318220919058).
Research release: 3f9b36f5e5f34c892e886b6aca86bfb8fc860d57.
Session: ec5b83a8-b892-473e-a0e6-69425fb35c5e.
Parent: 79c0a603-20ca-4099-820d-132c8905ec14.
Child: bfac3bda-dc08-4832-8e7c-b741d90fae46.
Archive root: /home/deploy/alfa-terminal/data/research.
Frozen cohort: 512 Binance USD-M contracts, not the earlier 513-member v2 study.
Anchor: 2026-10-30 04:30 UTC; endpoints Oct31 04:30 and Nov1 04:30 UTC.
Both reports are finalized. More elapsed time will not fill their missing values.

## 24h results

Report d27ba1e6-bcab-4629-b2e1-ad110197a53b.
SHA256 f9e51a15e670f3b9ebe25b58a799bf72985753840a229a3319bc1b4513413c0f.
As of 2026-10-31T16:35:50.496383+00:00; created 17:13:39.990751 UTC.

- Complete forward-price outcomes: 172/512 (33.59375%); unavailable: 340.
- Accepted rank snapshots within the horizon: 287; 199 bounded and 88 complete.
- Maximum sample gap: 305.087327 seconds, Oct30 23:25:04.414994–23:30:09.502321 UTC.
- Cadence meets the frozen 600-second rule. That does NOT mean every ticker value was known.
- SOON: no observed top-10 membership in sufficient sampled observations, but no stored forward-price outcome.
- Frozen momentum reference: four of five observed in the cohort's rolling-24h top10.
- Complete whole-cohort fixed-forward gain ranking is unavailable.

## 48h results

Report adb97b65-b7ca-4ea2-948e-4ef8b1d4ef1b.
SHA256 4a3f704d91b8f287e4fb8eac65136ed1777f948951b5fd16b8de1d0814841d00.
As of 2026-11-01T16:42:37.643495+00:00; created 17:31:27.582617 UTC.

- Complete forward-price outcomes: 87/512 (16.9921875%); unavailable: 425.
- Accepted rank snapshots: 506 cumulatively over 48h, not 506 additional samples; 413 bounded and 93 complete.
- Maximum gap: 3282.336532 seconds (54m42s), Oct31 16:20:25.807758–17:15:08.144290 UTC.
- Sampling is insufficient under the frozen 600-second rule.
- SOON: no observed membership, but gaps prevent claiming it never entered top10.
- All five momentum reference contracts have at least one certified observed membership.
- Complete whole-cohort fixed-forward gain ranking is unavailable.

## Independent integrity and calculation verification

Read-only standalone script: 2026-11-02-verify-archived-study.py.
Executed with a 240-second limit on the host, not through the application's calculation helpers.

- All 506 archived rank payloads: manifest SHA256 and byte length checked.
- Independently reconstructed valid ticker population using unique symbols, finite positive prices,
  finite returns, ticker timestamps within -5/+60 seconds of arrival, and maximum 60-second span.
- Recomputed all 512 members' competition-rank bounds and inside/outside/unknown flags in every snapshot.
  Unknown members remain in the 512-member denominator.
- All 259 retained forward outcomes (172 + 87): checksum, unique continuous 1440/2880 minute opens,
  closed flags and close times before observation checked.
- Recomputed first-open baseline, last-close endpoint, raw return, high excursion and low excursion
  using Decimal arithmetic; tolerance 1e-20 percentage points.
- Both report manifests' rank references and selected outcome references matched actual checksums.
  Selected observations matched the reports.
- Recomputed accepted-sample gaps and first observed memberships independently.
- Result: zero mismatches in these checks.

Limits: this is not a full security audit, independent parent/child discovery replay, or a CPU profile.
Independent checks validate retained evidence, not missing data or continuous-time membership.
No current broad test-suite, browser, backup restore, or production API verification was claimed.

## Candidate results

There was one frozen core long: SOONUSDT (score 91.5, conviction 80, VCP_COMPRESSION_LONG).
There were zero qualifying child/shadow long candidates. An empty child arm is not a successful prediction.

The momentum reference was frozen before the outcomes:
IDOLUSDT, MERLUSDT, UAIUSDT, BEAMXUSDT, LSKUSDT.
At initial classification these ranked 11, 12, 13, 14, 15 respectively, all outside top10.
SOON initially ranked 36. Thus the reference was already near the top10; its performance does not
establish hidden pre-pump discovery, and the arms differ in size and selection mechanism.

First certified rolling-top10 observations (UTC):

| Token | First observation | Rank or bounds |
|---|---|---|
| IDOLUSDT | Oct30 04:35:03.728430 | 10 |
| BEAMXUSDT | Oct30 05:55:03.655409 | 6 |
| MERLUSDT | Oct30 07:50:03.510200 | 10 |
| LSKUSDT | Oct30 10:10:04.387550 | 10 |
| UAIUSDT | Oct31 13:55:25.539240 | [4,7], exact rank unknown |
| SOONUSDT | None observed | 48h absence is not established because of gaps |

### Retrospective supplemental prices — NOT frozen prospective outcomes

The worker never reserved SOON at either horizon. To answer its price performance, a separate
bounded public-data check fetched Binance USD-M /fapi/v1/klines at 1-minute interval:
18 GET requests, six preregistered symbols, three pages each. Retrieved Nov2 04:06:45.501524 UTC.
No archive writes or replacement of finalized reports. Script: 2026-11-02-retrospective-price-check.py.
Each symbol had 2880 unique continuous minutes; 24h uses the first 1440.
OHLC values were checked finite, positive, and internally ordered.
These public-history responses were used in memory; full response bytes were not retained by this audit.

Formula: (last minute close / first minute open - 1) * 100.
Anchor Oct30 04:30 UTC; 24h ends Oct31 04:30 UTC, 48h ends Nov1 04:30 UTC.
These are raw fixed-window price changes, NOT strategy-entry returns, filled trades, or net P&L.

| Token | Baseline | 24h endpoint | 24h return | 48h endpoint | 48h return |
|---|---:|---:|---:|---:|---:|
| SOONUSDT | 0.3172000 | 0.3024000 | -4.665826% | 0.2805000 | -11.569987% |
| IDOLUSDT | 0.0148540 | 0.0238010 | +60.232934% | 0.0186220 | +25.366905% |
| MERLUSDT | 0.0269800 | 0.0288600 | +6.968125% | 0.0303700 | +12.564863% |
| UAIUSDT | 0.2634000 | 0.2739000 | +3.986333% | 0.3223000 | +22.361427% |
| BEAMXUSDT | 0.0035550 | 0.0050700 | +42.616034% | 0.0052260 | +47.004219% |
| LSKUSDT | 0.174540 | 0.221120 | +26.687292% | 0.239490 | +37.212100% |

BEAMX 24h agrees with the frozen archived outcome (+42.61603375527426160337552740%).
SOON's highest minute high over the entire 48h was only +1.040353% above the anchor baseline;
its lowest low was -11.979823%.
This cohort is unfavorable evidence for the core selection relative to this reference, not
proof of repeatable underperformance. No win-rate, causal effect, or general predictive-edge claim is supported.
Never treat the largest returns among the observed subset as the true full-cohort forward top gainers.

## Collection performance and source trace

Rank journal: 574 reservations, 506 completions, 68 interrupted reservations
(reason reserved_without_committed_snapshot), and three missed-window events.
These are not evidence of 68 provider outages; window expiration can leave the same interrupted record.

Outcome journal: 39 passes (78 reserved/terminal events), 298 symbol reservations.
Distinct symbols reserved: 195 at24h, 103 at48h; SOON reserved zero times.
All stored attempts were complete. Missing report values are therefore not equivalent to hundreds of recorded
exchange failures; many symbols were never reached or were interrupted before an attempt committed.

Passes commonly exhausted the 90-second budget after approximately 13–21 HTTP requests
(limit30); terminal reasons included pass_deadline and cleanup:TimeoutError.
The first24h pass started Oct31 04:36:32.683236 and ended04:38:02.685750,
21 requests, latest response200. The first48h pass started Nov1 04:50:30.977881 and ended
04:52:09.673468,18 requests, latest response200.
Observed pass spacing was tens of minutes, not the intended five-minute cadence.
Journal inspection found no exchange cooldown events; inspected last responses were200 with modest weights.
This does not exclude every possible transient network failure.

Source trace: ResearchOutcomeWorker.tick calls benchmark.freeze_inputs before a collection pass
and again afterward. ShadowBenchmark.freeze_inputs constructs the full benchmark, rereading and
checking growing rank/outcome archives and building whole-cohort timelines.
The as_of-to-created final-report delays were approximately38min (24h) and49min (48h).
Repeated full assembly on the resource-limited VM is a likely dominant bottleneck.
This is a code-and-journal inference, not a completed CPU/memory profile or exclusive causal attribution.
The earlier initial/synthetic capacity tests did not establish sustained aged-archive capacity.

Both workers remained running on the verified release with restart count0 and OOMKilledfalse.
Both health checks were unhealthy because heartbeat timestamps stopped at terminal slot720
(60h); reports were already finalized. This terminal-liveness issue is separate from collection completeness.
No restart, stop, new cohort, retrospective rank reconstruction, or live fix was performed.

## Prioritized problems and proposed fixes

1. P1 — Incomplete forward collection at both horizons; the core candidate itself was never reached.
   Fix (Proposed): use a lightweight checksum-backed completion index instead of rebuilding the whole report
   around each pass; prioritize frozen core/shadow/reference outcomes, followed by fair whole-cohort traversal.
   Keep all512 members, immutable anchors, source guards, original evidence, and explicit unknowns.

2. P1 — 48h rank sampling has material gaps.
   Fix (Proposed): profile growing-archive preparation, bound pre-capture work, and verify independent rank
   acquisition cadence during concurrent outcome collection and finalization on the actual resource limits.
   Do not relax freshness/rank policies merely to make the study pass.

3. P1 — Complete strategy comparison is unavailable and no repeatable edge is demonstrated.
   Fix (Proposed): preserve these reports as partial evidence; after runner repair, run sustained
   48h-sized archive/capture-window capacity tests, then a fresh preregistered multi-cohort study.
   Analyze missed winners and shadow rejection reasons before proposing changes to core thresholds.
   A separate supplemental whole-cohort historical-price reconstruction could recover price comparisons,
   but cannot recover missed rolling-ticker snapshots.

4. P2 — Finalized workers present stale-heartbeat health alarms.
   Fix (Proposed): explicit terminal-idle/stop semantics, separate from acquisition health.
   No automatic stop under this read-only review.

## Review scorecards

Counts below are scoped analytical items, not record counts or proof of complete verification.
Generated from 2026-11-02-observation-coverage.json using summarize-coverage.mjs.
No repairs to the system were made; all collection defects remain open.

### Study usefulness and quality

| Category | Observed defects | Assessment |
|---|---|---|
| Study usefulness and completeness | 2 / 2 | Neither horizon supplies the intended complete cohort price comparison. |
| Analytical clarity | 0 / 2 | Prospective reports and retrospective supplemental prices are separated. |
| Visual and interaction consistency | N/A | No UI in the audited artifact. |

### Analytical correctness and robustness

| Category | Observed defects | Assessment |
|---|---|---|
| Source authority and confidence | 0 / 2 | Archived manifests verified; public Binance prices used for the labeled supplement. |
| SQL/value accuracy | 0 / 2 | Independent recomputation matched both reports' retained evidence; no SQL involved. |
| Within-chart agreement | N/A | No charts. |
| Complete source details | N/A | No interactive source-detail surfaces; lineage is documented above. |
| Cross-artifact consistency | 0 / 2 | All report rank/outcome references reconciled. |
| Data-quality controls | 2 / 2 | Incomplete forward coverage at both horizons, plus insufficient48h cadence. |
| Conclusion support | 0 / 2 | Descriptive findings supported; repeatable predictive edge remains unverified. |

Security cleanup: this audit's temporary OS Login key was matched to its local public key,
revoked, and both temporary key files deleted. A subsequent OS Login listing confirmed the temporary
fingerprint absent and the original user key preserved. No account/trading credentials were used.

