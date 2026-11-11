# Analysis of finalized 24h/48h prospective observations

Study `71346822-f686-4bb1-99ec-0f56ef8f0ea1`, November 8–10, 2026.
Both horizons share the November 8 13:50 UTC anchor (14:50 Rome).
This analysis supersedes the earlier report-publication-pending notes. The automatic 48h
report was published November 10 at14:10:49 UTC, and its checksum was verified.

## Findings that change the next work

1. The capture explicitly requested `max_symbols=48`; this was not a320-symbol run that
   happened to return48. Only48 of512 cohort members underwent deep evaluation.
2. At48h,55 initially-outside members were subsequently observed in the cohort's rolling
   top10.42 had hit the selection limit,3 were excluded earlier, and10 were selected but
   unpublished. Coverage is the largest measured exclusion category in this trial.
3. Exact offline replay identifies real rejection mechanisms. Most selected missed entrants
   initially had no matching long archetype. NIGHT passed the structural stage but failed
   enriched ignition confirmation. IDOL hit the crowded-positive-funding guard. These are
   distinct mechanisms; a uniform threshold reduction would not be defensible.
4. The collector and reporting are coupled. One scheduled observation was missed while the
   24h report blocked the sampler. Health checks also exceeded their15-second deadline.
5. Feature names need contract review before calibration: the `rvol_4h` input in this core
   is assigned from the1h snapshot, and the nominal14-day contraction baseline is truncated
   by fetching240 hourly candles. These legacy semantics were preserved, not repaired, in
   the control. Replacing them would require a versioned comparison.

## Evidence and scope

Final reports were checksummed on the approved alfa2 VM and bound to the frozen definition.
The earlier verification independently checked all577 snapshot envelopes, observed request/
arrival windows and whole-cohort rank bounds against raw ticker rows. Report24h symbol
returns matched that reconstruction; report48h candidate returns also match the independent
48h endpoint calculation exactly. No study or source files, thresholds, services, anchors
or candidate sets were modified. No market data was fetched for the offline diagnostics.

The control and funding-carry arms were re-executed on the original capture in temporary,
network-disabled, read-only containers. Source/runtime guards and exact original control
replay passed. Instrumentation only observed function returns and branch locals. Both arm
counts/publications reproduced. Selection-only counterfactuals stopped before candle
requests; they are explicitly not replayed forecasts for newly included symbols.

This is **one frozen initial scan**, issued November8 13:45UTC, followed for48h. It does not
contain every subsequent four-hour production scan. A token missed by these frozen arms
could have been flagged by a later production scan; this study cannot establish otherwise.
24h and48h are overlapping horizons of the same cohort, not independent trials. Repeated
rank samples are not hundreds of independent model predictions.

## Candidate results and reference

Returns below use the common anchor's sampled price, not suggested entry or executed fills.
Max/min are five-minute sampled extrema, without stop/target ordering or trading costs.

|Candidate |Arm |24h |48h |48h sampled max |48h sampled min |Observed top10 |
|---|---|---:|---:|---:|---:|---|
|ZECUSDT |Control and challenger |-3.99% |-7.76% |+0.66% |-10.37% |No |
|1000PEPEUSDT |Challenger |+1.48% |+6.27% |+6.53% |-3.52% |No |
|INJUSDT |Challenger |+0.57% |-0.43% |+2.12% |-3.41% |No |

|Arm |Candidates |24h observed top10 |48h observed top10 |24h nonpositive endpoints |48h nonpositive endpoints |
|---|---:|---:|---:|---:|---:|
|Control |1 |0 |0 |1 |1 |
|Funding carry |3 |0 |0 |1 |2 |
|Momentum reference |10 |9 |9 |5 |7 |

Unweighted mean anchor returns were control−3.99%/−7.76%, funding carry−0.65%/−0.64%,
and momentum reference−1.37%/−2.47% at24h/48h respectively. These are descriptive averages
across unequal, overlapping sets—not portfolio returns or evidence of statistically useful
outperformance. The control and challenger share ZEC; only three unique published symbols
are involved.

All ten reference symbols had unknown initial rank membership under the anchor's missing
competitor bounds. Its9/10 observed hits are therefore **not nine early predictions**.
The reference was selected from strong capture-time rolling returns and cannot be presented
as pre-pump discovery. The report correctly records zero eligible early detections for it.

Funding carry added two publications and one useful positive48h endpoint, but did not
establish improvement on the primary top10-discovery objective in this trial.1000PEPE's
best observed rank was12. A positive6.27% move can still be useful even without top10 rank;
this is why opportunity quality and leaderboard membership need separate metrics.

## What was missed, and at which stage

“New observed entrant” here means provably outside top10 at the anchor and subsequently
certified inside at an observed sample. It does not establish exact first entry between
samples, never having been a gainer before, or profitable execution.

|Exclusion category for initially-outside later entrants |24h |48h |
|---|---:|---:|
|Selection limit |18 |42 |
|Earlier liquidity/universe exclusion |0 |3 |
|Selected, not admitted to Stage2 |3 |4 |
|Selected, Stage2 admitted, not published |2 |6 |
|Total new observed entrants missed |23 |55 |

An additional10 members at each horizon were observed top10 but had unknown initial rank
membership; they are kept outside the55/23 initially-outside groups.

At48h,42/55=76.4% were selection-limit exclusions and10/55=18.2% were selected but rejected.
This establishes budget/selection as a major bottleneck **in this48-symbol trial**. It does
not establish that every excluded token satisfied entry, liquidity or risk requirements.

Only37/55 new observed entrants had positive48h anchor returns;18 exceeded+5%. At24h,
18/23 were positive and15 exceeded+5%.29/55 had a sampled peak above+10% within48h.
Rolling top10 membership and lasting positive forward returns are materially different labels.

Examples from the matched-anchor forward-return leaders:

|Symbol |24h |48h |Initial control decision |
|---|---:|---:|---|
|INITUSDT |+1.42% |+82.66% |Selection limit |
|PIEVERSEUSDT |+22.26% |+41.13% |Selection limit; initial rank unknown |
|ARIAUSDT |+5.51% |+25.98% |Selection limit |
|EDGEUSDT |+15.66% |+19.35% |Selection limit |
|AIOTUSDT |+3.42% |+18.92% |Selected; no long archetype; initial rank unknown |
|CYBERUSDT |−4.09% |+12.56% |Selected; no long archetype even after enrichment |
|MERLUSDT |+11.72% |+10.61% |Selection limit |
|IDOLUSDT |+15.37% |+0.77% |Initial archetype mismatch; enriched funding veto |

## Exact replay findings: selected cases

### NIGHT: readiness versus activation

NIGHT returned a RANGE_BOTTOM_ACCUMULATION_LONG opportunity during structural evaluation.
All L1–L6 domain gates passed at that stage. With the archived derivatives provided:

- 7d OI growth:+4.36%;4h OI growth:+1.56%;1h OI growth:+0.89%.
- 1h taker ratio:1.367. The order-flow branch was **confirmed** and there was no acute
  liquidation veto.
- The RVOL field was1.1923. No kinetic, pocket-pivot, micro-expansion or absorption trigger
  was present. Coil maturity was50; volume-vacuum and mature-squeeze bypasses were false.
- The structural path accepts a1.15 RVOL route; the enriched compressed-setup path requires
  actual ignition or qualifying stealth maturity. Consequently `ignition_confirmed=False`
  and L6 failed while L1–L5 remained true.

It subsequently returned+6.39% at48h, with a+10.70% sampled peak and−6.72% sampled minimum.
This supports investigating a **separate early-readiness research label**, not reducing
1.20 to1.19 to fit NIGHT. The Stage1/Stage2 distinction is implemented policy, not an
inverted condition. Its suitability for forecasting untriggered setups remains a hypothesis.

### ARB: compressed, but absorption requirements not met

The classifier's compression, supply-exhaustion, value-support and momentum helpers passed.
Its MACD histogram was positive but falling (0.0004613 versus0.0004917); `is_mom_up` passes
on positivity as well as improvement, so it must not be described as accelerating momentum.

Taker ratio4h was0.8782. The range-bottom branch needs0.95, while the looser0.85 alternative
also requires contraction<=0.90; its actual contraction was0.9212. Its upper-base VCP route
also did not match its range position/distance. No long archetype was assigned, before
mandatory L1–L6 gate evaluation.

ARB was+8.71% at24h but−2.81% at48h, with a+10.30% sampled peak and−4.65% sampled minimum.
This case does not prove the absorption requirements should be weakened.

### IDOL: already expanded, then a crowded-funding veto

Initially no long archetype matched. Recorded bandwidth was0.829, its72h excursion was
about146.7%, and price was well above its EMA50/POC references. During enriched scoring,
raw funding+8.4681bps exceeded the core's raw+2.5bps guard, causing an early return before
L1–L6. No claim is made that this raw-rate guard is already normalized across settlement
intervals.

IDOL was+15.37% at24h, peaked+17.10% in sampled prices and ended+0.77% at48h. This is a
continuation/expansion case, not evidence that a quiet compression label was accidentally lost.

### CYBER and other selected cases

CYBER still had no matching long archetype after derivatives enrichment. At the decision it
was below EMA50 and VAL, bandwidth0.511, RVOL input0.611 and contraction1.367. Later OI
strength did not make those initial structural conditions disappear. It ultimately gained
12.56%, but its sampled minimum was−9.12%; source evidence alone does not show a safe fill.

ICNT and ONE acquired squeeze archetypes after enrichment but failed L2 and L6. ICNT ended
−12.65%, ONE−2.78%. AIN had no initial long archetype, reached a+12.32% sampled peak, but
fell as far as−46.69% in sampled prices and ended−28.35%. These show the danger of labeling
every missed top10 entrant a desirable long trade.

## Recorded-quotes selection counterfactual

The actual engine's initial selection stage was run on the original frozen instruments,
quotes and settings. The48-symbol selection was reproduced exactly. Only the budget was
then changed to320, and execution stopped before fetching/evaluating any candle.

|Budget ceiling |Actually selected |Later new entrants in evaluation pool |
|---|---:|---:|
|48 |48 |10/55 |
|320 |270 |38/55 |

The larger ceiling would admit28 additional later entrants to deeper evaluation, including
INIT, ARIA, EDGE, B2, BEAMX and SOPH. It still would not select17 of the55, including MERL
and TST.320 is a ceiling; the discovery lane does not fill slots without qualifying evidence.

This is **selection coverage**, not38 predicted gainers. Original candles/OI were not
captured for every newly admitted symbol, so their full publication outcomes cannot be
faithfully replayed from this archive. No such outcomes were fabricated.

## Data/measurement problems to fix before strategy calibration

1. Reporting awaited inside the sampler loop blocked the24h successor slot. The report's
   as-of13:50:48 was published13:59:14, and slot289 (13:55) was attempted13:59:23, too late.
   The48h report publication lag was20m49s after its endpoint. Move report computation off
   the sampling critical path and keep health checks independent of large archive reads.
2. `recorded_slots=577` and `missing_slots=0` count record files. One file describes a missed
   observation. Expose observed/missed/failed/absent-file counts separately; zero missing
   files is not perfect acquisition.
3. The core's `rvol_4h = snap_1h.relative_volume` is a1h metric under a4h name. This matters
   directly to the NIGHT ignition interpretation. Freeze current behavior as control,
   define real timeframe semantics and compare a new feature-contract version separately.
4. Volume contraction asks for7/14-day windows while fetching240 hourly bars, often239
   closed bars with a partial tail. The long baseline is thus roughly10 days, not14.
   Full-window acquisition or honest renamed semantics needs a versioned implementation.
5. Current journals collapse important decisions into `no_setup` or
   `enrichment_unavailable_or_no_setup`. Persist typed archetype/gate/funding/data-availability
   rejection reasons and decisive observed values so future audits do not require tracing.

## Recommended sequence

- First repair scheduling/health/coverage accounting and reconcile the intended scan budget.
  These are established operational/measurement findings, not speculative strategy changes.
- Audit and version timeframe/feature contracts while preserving the existing engine as a
  reproducible control. Add structured rejection evidence without changing decisions.
- Preregister a narrowly defined readiness-versus-activation research comparison using the
  existing structural/risk checks. Do not tune thresholds to these named winners or remove
  crowding/anti-chase safeguards solely because a rejected token later appeared in top10.
- Then collect full-budget, repeated point-in-time cohorts at the intended scan cadence,
  with matched baselines and risk/path metrics across additional periods. Treat promising
  differences here as hypotheses, not validated predictive edge.

## Boundaries of these conclusions

This dataset identifies where this initial scan excluded later movers. It does not establish
external market causes such as news, coordinated buying or liquidation-driven demand for
all those moves. Continuous OI/candle features were not captured for every member, especially
unselected ones. Exact pre-breakout paths for all excluded tokens cannot be certified from
bulk ticker samples alone. Suggested entries/stops were not executed, and sampled extrema
lack intrabar event ordering. These limits remain even though the finalized report exists.

Bounded report summaries and exact offline diagnostic outputs/drivers are retained under
`docs/security/evidence/2026-11-10-finalized-horizon-analysis.json`,
`2026-11-10-frozen-replay-diagnostics.json`, `2026-11-10-frozen-branch-diagnostics.json`,
and `2026-11-10-selection-counterfactual.json`. Raw study data stayed on the VM. Both
analysis containers were temporary/read-only/network-disabled with automatic removal.
At credential cleanup, OS Login no longer found the short-lived key; local key files were
removed. No implementation, deployment, study data or existing service was changed.
