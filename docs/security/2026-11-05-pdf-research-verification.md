# Independent review of Atlas Screener Findings, Bottlenecks and Roadmap

Review date: November 5, 2026. Reviewed the seven-page supplied PDF, rendered pages, retained study records, current source, and the external scripts/results named by the PDF. Instructions in the PDF were treated as proposals, not implementation authorization.

## Verdict

The design direction is useful: preserve an identifiable legacy core, distinguish watch states from entry-quality alerts, repair publication/evidence capture, specify feature contracts, and compare challengers under controlled budgets.

The claimed empirical proof is not sufficient for strategy promotion. The arithmetic of the full-population proxy experiment reproduces, but it is not a test of the actual production core or the proposed repaired pipeline. Several factual and methodological corrections are needed. No evidence establishes a general low-drawdown edge, 4.5x full-pipeline uplift, or repeatable top-gainer discovery.

My own retained morning and afternoon research reproduces exactly. It remains descriptive evidence with explicit limits; it does not validate a replacement strategy.

## What I verified

1. Extracted all seven pages, rendered the PDF and visually inspected its tables/funnel and milestone claims. PDF SHA256 is in pdf-metadata.json under the review evidence directory.
2. Located the external scratch scripts and input/result files. Hashed them and retained reviewed source snapshots. Did not execute their main entry points, overwrite their results, run fetch fallbacks or apply their proposed patches.
3. Executed only the inspected, filesystem-only population simulation function with networking disabled, using its original retained data. Its complete population result equals the saved JSON exactly.
4. Independently recomputed every nonempty proxy arm's raw forward return and MFE/MAE means using Decimal. Reported rounded values reproduce. All selected forward windows contain 24 bars in this dataset; the code's weaker minimum-of-20 check did not truncate these actual rows.
5. Re-ran my morning and afternoon scripts to new output paths; both outputs are byte-identical. Verified their 26/28 consumed-input hashes and their script identities.
6. Re-ran the independent float/fsum checks: 1,044 full-population feature/outcome rows, 14 five-minute transition histories and reference rankings. Separately reconstructed all 34 hourly ranking boundaries with exact rational arithmetic: membership matches, no tenth-place boundary ties.
7. Exercised actual production absorption and VCR functions with controlled inputs, and checked an archetype counterexample. No production function was edited.
8. Re-ran the strict replay CLI on the supplied six-token reconstruction: exit3, 72 unavailable, 0 strict-eligible, 0 negative forecasts.
9. Re-ran focused tests in the existing isolated research worktree: 68 passed in54.30s, covering strict replay, the outcome worker and archive behavior. This is not a new full-suite or deployment qualification.
10. Rechecked the public status/live endpoints at14:47:44UTC. Status still reports the12:01 run; live still returns theOctober24 timestamp and zero candidates. No refresh parameter, remote SSH or deployment.

Evidence directory: docs/security/evidence/2026-11-05-pdf-review. Historical740-test and capacity claims were compared with their recorded worktree ledger/results; no new full48h soak or GCP capacity test was run.

## Main claim-by-claim corrections

### Pages 1/3: 168-scan funnel is a subset diagnostic, not actual historical pipeline attribution

The saved metadata explicitly sets diagnostic_only and performance_claims_permitted=false. The 134/168 no-archetype/pre-return count,32 domain-gate failures,2 gate passes and1 direct enriched publication agree with that diagnostic's saved counters.

However:
- Its universe contains BTC plus14 names chosen after they became winners, with synthetic spreads, neutral macro, unavailable/zero historical basis/depth and reconstructed derivatives.
- It calls the private single-symbol evaluator directly for Stage1 and enriched Stage2. It does not recreate actual full-universe admission, shortlist competition or publication history.
- A quote-volume rank greater than320 is only recorded; it does not prevent the subsequent evaluation. The PDF draws this as a sequential exclusion. Eleven of the13 outside-top320 cases also belong to the134 no-archetype cases, so those counts cannot be subtracted as disjoint funnel stages.
- Actual selection is a liquidity-core plus anomaly-discovery funnel, with eligibility, spreads and pair deduplication. A raw top320 volume cutoff is not that selector.
- Empty classification traces conflate returns before classification with no matching archetype. The shown wrappers do not log each exact return line, so granular causal counts need more instrumentation.
- The first-entry loader reads keys absent from the retained study schema. All14 saved first_top10_hour_utc fields are None. The real earlier study stores a nested hourly_rank_entry/first_observed_top10_boundary. The diagnostic does not enforce a before-first-entry restriction.
- Twelve observations per hindsight-selected token are correlated; many occur after a move or leaderboard appearance. They are not168 independent forecast opportunities.

Keep this as conditional mechanism research. Do not present it as proof of why each actual normal scan excluded a token.

### Page 6: the 2-to-9 publication uplift was not executed as a repaired full pipeline

The function named verify_track2_challenger_on_exact_screener does not run a screener. Its actual calls load two previous JSON results and read summary fields.

It maps direct enriched Stage2 publication counts into track2_challenger_published. For the14-token case it additionally maps control_stage1_pass into a field named control_full_pipeline_published. Stage1 survival is not full publication: later gates can reject it, and Stage2 anomaly probes can admit a symbol that failed Stage1.

No Stage1 funding propagation, proportional admission floor, new probe ranking or full publication-aware arbitration is applied by that checker.

Thus2 and9 are reproducible aggregates of different stages, not a measured control-versus-repaired full-pipeline result. The five positive token/cohort cases contain only four unique symbols: HOT, ICNT, MEGA and TRUST; TRUST is counted in both cohorts.

A concrete example matters: the new diagnostic's TRUST T-44h row has Stage1 archetype=None and no opportunity, while enriched Stage2 creates an OI_ACCUMULATION opportunity. Lowering an absorption threshold alone cannot fix a path that never reaches absorption. Actual admission/probe policy must be implemented and evaluated.

The inherited multi-hypothesis helper also stops at the first created opportunity; it does not necessarily continue after that opportunity fails publication. Publication-aware arbitration remains a proposal.

Reviewed source: verify_strategy_outperformance.py lines554–587; faithful_48h_replay.py lines650–673; trace_nov5_gainers_counterfactual.py direct evaluator calls.

### Page 6: population results reproduce, but the control is not the production core

The population function explicitly calls its selector a coiled-core proxy. It uses a handful of hand-written filters/ranking terms instead of screen(), the canonical archetype/gate chain, Stage1/2 admission, absorption, scoring/conviction, brackets and publication policy.

Other material differences:
- The so-called un-enriched control already uses OI4h/OI24h in eligibility and ranking.
- The challenger changes EMA tolerance, bandwidth, return bounds, range-position floor and ranking weights. It is not a repairs-only challenger with frozen gates.
- USDCUSDT is selected by the WindowA core proxy, although Atlas policy excludes it.
- Its1.5M liquidity cutoff is not the universal core500k floor;1.5M is used for the discovery hygiene route in the inspected code.
- Artificial four-hour bars are grouped from the first retained hourly bar, Oct31 09:00UTC, rather than UTC four-hour boundaries.
- WindowA has only87 past hourly bars, forming21 artificial4h bars; its latest such close isNov3 20:59:59.999. WindowB has96 hourly bars and24 artificial4h bars. An EMA50 estimated from21/24 bars is not the production warmed four-hour series; long profile/weekly context is also absent.
- WindowA returns run midnightUTC-to-midnightUTC, but the target ten are from theRome23:00UTC-to23:00UTC day. WindowB outcomes end at09:00, but its label set is the09:30 rolling snapshot, with freshness uncertainty.
- The watch rules coded by the simulation differ from the proposed definitions. For example W1 uses1.5x volume and4% six-hour or8% daily OI, rather than the page5 rule; W2 does not enforce the described higher-low/taker conditions; W3 does not test the described funding range or actual flag duration.
- A maximum12-name cap alone does not establish matched API weight, invested capital, risk or actual alert count: some buckets contain only3,4,5 or9 names.
- Positive24h closes are not executed-trade win rates. Ratios of average MFE to average MAE are not expectancy or proof of low tail risk. Actual fees, spreads, availability and bracket fills are not modeled.

No direct future-price use inside the proxy's feature loop was established by this review. The decisive issues are the wrong baseline, different rules/targets, short/misaligned inputs and lack of out-of-sample evidence.

### Page 6: the omitted WindowA combined result changes the outperformance conclusion

Reproduced values:

| Proxy arm | WindowA mean24h return | WindowA meanMAE | WindowB mean24h return | WindowB meanMAE |
|---|---:|---:|---:|---:|
|Core proxy|+4.01%|-3.05%|+7.65%|-1.20%|
|Challenger proxy|+4.58%|-3.20%|+7.23%|-2.11%|
|Combined proxy|+2.80%|-5.30%|+9.19%|-3.40%|

The PDF omits the combined result in WindowA and highlights its stronger WindowB result. Combined is worse than core on both return and MAE in WindowA. The challenger also has lower return and worse MAE in WindowB. Two overlapping windows cannot establish persistent dominance.

The printed one-hit rows naming FLUID+AIO and JCT+LSK also confuse hit examples: the saved result lists onlyFLUID for WindowA W1 and onlyJCT for WindowB W2.

These numbers are useful exploratory proxy outcomes, not empirical proof of the described production strategy.

### Page 4: VCR is not 7 hours versus 14 hours

volume_contraction_metrics multiplies its day parameters by24. The PDF's7h/14h diagnosis is contradicted by the code.

There is a different, genuine contract issue: normal fetch asks for240 hourly bars, while a full14-day calculation needs336. The helper falls back to all available bars for the longer window, producing approximately7-day versus10-day ADV with240 closed bars, potentially slightly less when an open bar is filtered.

Controlled example using the actual helper:
- Full336 bars: ADV7=72, ADV14=48, ratio1.50.
- Same trailing240 bars: ADV7=72, fallback ADVlong=57.6, ratio1.25.

Add this to the contract audit, without silently changing legacy measurements or thresholds. The existing hourly ATR/RVOL passed into four-hour-labelled fields remains confirmed.

### Pages 4/5: Stage1 starvation mechanism is supported; its proposed threshold is not validated

The actual absorption function reproduces33 with missing OI/funding,48 with known neutral funding alone, and73 with OI plus funding for the same constructed flow/geometry inputs. This confirms the mechanism can exist; it does not establish its prevalence or economics in the full market.

Important implementation caveats:
- Missing data and observed adverse data need separate handling.
- Funding-only propagation must respect source time and actual funding interval.
- With funding available and OI absent, the maximum known-pillar score is75. Strictly proportional35% is26.25/75, not25/75.
- A score33 is already classified HIGH_REJECTION_RISK inside the absorption function. Merely lowering a caller's numeric floor does not remove that status-based rejection.
- Some symbols fail archetype recognition before absorption; threshold changes alone do not address those cases.
- Stage2 probes already provide another enrichment path. A universal Stage1-failure-equals-starvation assumption is incorrect.

Test explicit pending-enrichment semantics and bounded admission separately, preserve known-risk guards, and require full Stage2 entry/publication validation. Do not directly apply25/75 as a proven repair.

### Pages 4/6: not every archetype has the claimed universal structural constraints

The classifier has branches with different requirements. A controlled case below EMA50 and VAL, with wide bandwidth, still classifies as MOMENTUM_EMERGING_LONG. This is not proof of publication; later gates may reject it. It disproves the universal pre-classification claim.

Likewise, the legacy _score_setup fallback is deliberately restricted to explicit BREAKOUT mode in the inspected source. ALL mode still calls the accumulation classifier, which can return BREAKOUT_LONG. The condition's existence does not establish that all breakout discovery is dead or that broadening it is a safe bug fix.

### Funding units need correction

The supplied new14-token history has approximately four-hour funding intervals for13 symbols and eight-hour intervals forBAT. Calling every recorded rate an eight-hour rate is incorrect. The raw0.00025 veto exists, but its economic interpretation depends on the interval and on whether a live mark-rate or last settlement is used.

Binance exposes fundingIntervalHours in fundingInfo:
https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data

Do not normalize thresholds or annualize historical values from an unverified eight-hour assumption.

### Pages 1/7: archive inventory and capacity statements need correction

The cited November2 archive review records sessionec5b83a8-b892-473e-a0e6-69425fb35c5e:
-512 frozen members, anchorOct30 04:30UTC.
-One core longSOON and zero qualifying shadow longs; five separate momentum references.
-506 accepted cumulative48h rank snapshots.
-172/512 complete24h price outcomes and87/512 complete48h outcomes.

This does not match the PDF's510 symbols, six published longs,12:05 anchor and568 snapshots. If a different study was intended, it needs its own session/source identifiers. I did not perform a new remote audit of the original archive in this review.

The528.6s/763.4s finalization timings are supported by the retained local synthetic report test under256MiB/0.5CPU. They are not a new real48h soak or actualGCP qualification.

The3.5h full-cohort figure was a linear projection from49 new paths over four scheduled passes with463 active paths preseeded, not an observed fresh512-member study completed in3.5h. Collection and optimized finalization were measured separately; the concurrent collection run used the baseline serialization source. Recorded740 tests establish regressions at that worktree state, not economic edge or zero vulnerabilities.

## Verification of my own prior conclusions

Confirmed again:
- Morning and afternoon analyses reproduce byte-for-byte, with matching consumed-input and script hashes.
- All34 hourly proxy boundaries match an independent exact-rational reconstruction.
- Retained candidate snapshots show no overlap with the studied lists at those snapshots.
- The supplied production API still has the documented publication discrepancy at the fresh recheck.
- Specific timing markers can occur before the next nominal four-hour scan.
- The proposed simple volume/OI reference does not capture most endpoint winners in the two studied windows.
- Subsequent mark changes, including JCT/ELSA reversals, are reproducible facts.
- Hourly ATR/RVOL mapping concerns and the constrained local report timings are supported.

Boundaries I retain and would emphasize more strongly:
- We cannot infer never-alerted status or actual rejection reasons without complete, source-matched historical journals.
- Timing markers and hourly leaderboard proxies are not exact live entry timestamps, actionable signals or proof that faster monitoring improves risk-adjusted outcomes.
- Reproduction validates calculations, not a predictive strategy. Two overlapping windows and selected winner histories do not establish economic edge or causal institutional flow.
- The legacy core is multi-archetype, not a universally compressed-only selector.
- Local segmented capacity checks do not equal actualVM/live-network qualification.
- My review of the old VM study is a comparison to retained audited records; this turn did not re-fetch all originalVM artifacts.
- The VCR7-versus-truncated10-day finding is an additional confirmed contract issue, replacing the PDF's7h/14h claim.

No need to abandon the useful earlier descriptive findings. Their scope must remain narrower than the PDF's proof claims.

## Roadmap I would accept after revision

1. Establish source/config/run identity and publication freshness using the recording path already in the current source. Diagnose deployed/runtime alignment rather than building duplicate infrastructure. Match scan identity and ordered started/completed/published times, not literal equality of cron-start and payload-completion timestamps.
2. Freeze legacy feature contracts and specify explicit alternatives:1h/4h ATR/RVOL, true VCR lookbacks/missingness, funding intervals, UTC-aligned bars and sufficient warmups.
3. Implement an isolated challenger that actually changes Stage1 admission/missingness and publication-aware arbitration. Test each change independently, then jointly. Keep real risk and final publication guards.
4. Run actual baseline and challenger pipelines on identical full-cohort evidence, with measured API/selection budgets and correctly aligned outcomes. Retain all negative cases, unknowns, repeated-alert accounting and source fingerprints.
5. Treat watch-track rules as frozen hypotheses. Use disjoint development/holdout periods, risk/path metrics and a preregistered prospective comparison only after the challenger is real and reproducible. Freeze eligibility by policy/version, not a permanently hard-coded512.
6. Qualify packaging, terminal health and runtime capacity on the authorized researchVM before activation. Account honestly for every member and provider missingness; do not manufacture complete values to satisfy a100% target.

The immediate next implementation is still evidence/contract correctness plus a controlled challenger, not production threshold relaxation or another unchanged observation presented as improvement.

## Scope of changes in this review

Only review notes, local evidence outputs and source snapshots were added. The supplied PDF, external originals, original research outputs, product source, core rules and archives were not modified. No VM write, cohort activation, account credential, trading action, commit/push or deployment.

