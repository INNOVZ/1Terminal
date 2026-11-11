# Evidence-led discovery implementation

## Implemented locally

The November 11 historical investigation led to an opt-in research challenger and
collection reliability changes. The production entry point continues to instantiate
`CryptoOpportunityScreener`. Research composition explicitly selects `DiscoveryScreener`.

### Collection and reporting

- The sampler performs acquisition only. A separate `reports` CLI worker owns
  `.report.lock`. Report computation never owns `.worker.lock`.
- Both processes share immutable snapshots. Each report is published atomically and
  finalized idempotently. The reporting container has networking disabled.
- Health reads a small atomic progress record and enrollment metadata. The sampler
  validates definition/source/runtime at startup and checks definition/enrollment file
  signatures while running. Individual ticks retain full validation by default.
- Reports distinguish observed, missed, failed and absent slots. The legacy
  `recorded_slots` and `missing_slots` fields retain their file-count meanings.
  `unobserved_slots` counts every unsuccessful or absent acquisition. Any such gap
  gives the new report a degraded collection status.
- New policies and reports are versioned. Existing completed studies retain their
  original files and require their original image for strict replay.

### Decision evidence

The shared engine now records selection allocation, requested budget, turnover, spread,
and discovery eligibility. Accumulation diagnostics include archetype inputs, failed
funding/OI/support guards, and L1–L6 inputs/results. Decimal values are serialized as
strings so the public CLI reports remain JSON compatible.

The default feature formulas and publication thresholds retain the control behavior.
Additional journal evidence changes source fingerprints, which prevents mixing an old
capture with newly compiled engine code.

Full diagnostics remain in the checksummed comparison companion. The hot definition
contains compact decisions, decisive values and hashes binding each full event. Its
top-level comparison digest binds the companion. Explicit companion reads allow up to
128 MiB while definition/snapshot reads retain their 16 MiB limit. Actual full-budget
definition payloads were checked after compaction.

### Versioned discovery challenger

`discovery-v2-full336-rvol4h-watch20` adds the following research behavior.

1. Request 360 hourly bars. Compute contraction from a complete contiguous 336-hour
   window, with recent 168-hour mean volume divided by 336-hour mean volume.
2. Compute 4H RVOL from the latest closed four-hour bar and an inclusive 20-bar mean
   on that timeframe. The longer hourly history also changes warm-up for other hourly
   indicators. This is explicitly a versioned feature-contract comparison.
3. Use up to 20 spare slots inside the total 320 ceiling for canonical instruments with
   $500k–$1.5m turnover, positive prices and 0–25 bps spread. Selection uses a deterministic
   hash of policy version, frozen four-hour UTC bucket and symbol. It rotates across
   captures and preserves the existing selection prefix. Full allocations have no spare
   watch slots.
4. Record structural readiness when existing L1–L5 pass. Record enriched L6 activation
   separately. Subsequent rejection evidence supersedes earlier readiness evidence.
5. Record six-hour BTC-relative returns and turnover acceleration using aligned closed
   hourly data. The exploratory emerging observation requires positive relative strength,
   acceleration above 1x and 24h return at most 9%. It carries `actionable=false`.
6. Valid recorded histories that lack the new feature warm-up are explicitly unavailable
   per symbol. Unrecorded requests, failed acquisition, invalid provenance and malformed
   histories still block the affected comparison. Reports expose unavailable symbols.

The watch selection, readiness and emerging views receive separate prospective outcome
summaries. Published engine/radar results continue through the existing scoring, risk,
curation and five-check radar admission paths. Watch observations do not provide orders,
executed fills or an automatic production promotion.

### Changes between consecutive captures

`prepare --previous-study PATH` can bind an earlier issued definition from the same
source/runtime/policy to the new study. Context must precede the new capture and be at
most eight hours old. Only definition/feature evidence is read. Prior outcome reports
have no role in admission.

The resulting views measure a known false-to-true emerging transition and readiness
followed by enriched activation. Missing prior observations stay unknown. The collector
still follows one enrolled cohort. Additional candidate captures require separate
issuance, preparation and enrollment.

## Public input and deployment boundaries

Acquisition uses `atlas-public-evidence-v4`. The CLI requires the explicit 320-symbol
ceiling for discovery, with up to 420 supplemental provider calls, including at most
60 derivatives calls. Wire budgets remain bounded at 3,000 attempts, 600 seconds and
128 MiB for discovery. Throttling stops acquisition. Supplementary histories must overlap
the control's closed candles exactly and cannot introduce bars after its capture start.

Every supplemental provider value is re-derived from retained public HTTP receipts.
Control replay remains mandatory. New watch inputs must belong to the frozen canonical
cohort. No trading/account credentials, order entry points or published network ports
were introduced.

The deployment composition specifies 512 MiB/0.5 CPU for sampling and 256 MiB/0.25 CPU
for reporting. A one-off capture needs separate capacity acceptance. Local synthetic
checks cannot establish available memory or scheduling behavior on the actual GCP VM.

## Verification scope

Focused tests cover lock isolation during a blocked report, crash recovery for progress,
lightweight health reads, coverage accounting, watch bounds/rotation, feature semantics,
chronology, missing evidence, short warm-up availability, and capture/replay/preparation/
report entry points. Full-suite and capacity results are retained alongside this document.

An initial synthetic capacity attempt used inconsistent simulated quote clocks. The
strict receipt verifier rejected it. The fixture clock was corrected and the attempt log
was preserved. The live freshness/receipt checks retained their requirements.

Full-budget serialization also exposed a 27 MiB definition that exceeded the 16 MiB
read guard. Compaction reduced the measured 320-symbol definition payload to about
7.9 MiB before cohort metadata. A dedicated regression covers oversized diagnostics.

## Remaining acceptance

User deployment, fresh real-market input verification, successful enrollment and a valid
first snapshot remain deployment steps. Predictive benefit requires prospective comparisons
across multiple periods. This implementation supplies the challenger, evidence and reports
for those comparisons. It does not establish a calibrated top-gainer success rate.
