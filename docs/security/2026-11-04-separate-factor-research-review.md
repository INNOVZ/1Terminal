# Independent review of the separate continuous-factor proposal

**Update after the experiment files were supplied:** See
`2026-11-04-external-48h-reproduction-audit.md`. The saved results now reproduce exactly; the
historical HOT conflict is confirmed through the later OI fallback. The narrative-only assessment
below predates those files. Point-in-time leakage and full-pipeline comparison limitations remain.

Date: November 4, 2026. Scope: verify the supplied research, not implement its proposed model.

## Verdict

**The research identifies legitimate design concerns, but its reported historical performance is
not reproducible from the supplied evidence and several explanations are incorrect or overstated.**
Treat the proposed scorer as an experimental challenger. Do not replace the core or describe its
48h results as verified, mathematically proven, or institutional-grade performance.

Input: user attachment
`/Users/jithinjacob/.codex/attachments/e246ce95-5489-42d3-98e5-471dc64f8702/Pasted text.txt`.
It contains a narrative and result table, not an executable experiment, raw data, dated scan outputs,
source revision, scoring formulas/weights, or exact UTC time corresponding to T-0.
Bounded searches of the inspected repository docs/scripts/tests and likely temporary experiment
filenames did not locate the described task-279 experiment. This does not prove it never ran.
The originating script/data/time anchor were requested; conclusions below do not assume them.

Two local sources checked: main checkout HEAD 5e84bf01ed9306d3d8483852394955aa2d5175ab plus
its preserved dirty files, and the attached research worktree HEAD
3f9b36f5e5f34c892e886b6aca86bfb8fc860d57 plus its local collector repair.
Neither source is assumed to be the unknown experiment revision or current production source.

## Code claims checked

| Claim | Finding |
|---|---|
| An archetype must match before long scoring | Confirmed. `crypto_screener.py` classifies and returns on no match, evaluates gates and returns on failure, then calls continuous factor scoring. |
| Archetype labels affect acceptance | Confirmed. L1's base-consolidation path is label-dependent; L3/L4/L5 also depend on labels. |
| HOT matched an earlier BREAKOUT branch that took priority over OI accumulation | Conflicts with both inspected sources: main OI accumulation returns precede the main breakout return. A late OI fallback does exist after breakout, so a snapshot matching only that fallback could be preempted; this requires its actual features. The report's specific historical explanation is not established. |
| The system has no continuous architecture until this proposal | Incorrect characterization. Five-factor continuous scoring already exists, downstream of classification/gates, with some discrete thresholds inside it. The real question is eligibility, feature validity and aggregation, not introducing continuous scores for the first time. |
| The long factor scorer has no 24h OI input | Confirmed: it takes 7d, 4h and 1h OI changes, but no 24h OI parameter. |
| The whole system relies on a single 7d OI point | Incorrect. The scorer uses three horizons; the adapter supplies 24h OI, and archetype/gate logic consumes it too. Omitting 24h from this scorer is a specific modeling limitation, not proof of the claimed BEAT/SOON error. |
| Hourly data is fed into some 4h-labelled features | Reconfirmed in main: ATR begins as `snap_1h.atr14`, and `rvol_4h = snap_1h.relative_volume`. This remains a concrete feature-contract issue before threshold comparisons. |

Read-only synthetic verification ran against **both** checkouts using their own imports:

- Same numeric LongGateInputs: OI accumulation label passes all six gates; breakout label fails
  L1 while L2–L6 remain true. This isolates label-conditioned acceptance in the domain function.
  It is not an end-to-end scan or a historical HOT replay.
- A synthetic snapshot satisfying both the main OI-accumulation conditions and main breakout
  conditions is classified as OI accumulation. This confirms the current branch priority.
- Signature introspection confirms scorer OI inputs are 7d/4h/1h.

The proposal is right to question this coupling. It has not proved the quoted historical signal
counts or that a price increase alone caused the claimed HOT rejection.

## Why the stated hit rates do not establish improvement

1. **Different denominators:** baseline/patches use 12 scans; the challenger uses 10. T-44h to T-0h
   has 12 four-hour-spaced observations. Missing warmup, excluded observations or different windows
   must be disclosed and the same eligible symbol-times compared. Missing is not silently a fail
   or a dropped denominator.
2. **Signal frequency is not hit rate:** 5/10 means five accepted observations if those counts are
   accurate. It does not measure precision, top-gainer recall, or five independent successful forecasts.
   Repeated alerts on one token are correlated. MEGA's stable six-scan signal is not evidence of a
   subsequent top-gainer outcome.
3. **Winner selection and tiny controls:** HOT/ICNT/BMT are selected after their moves; MEGA is
   described as a current base. BEAT is added as one chosen negative example. This cannot estimate
   full-universe false positives or forecast quality. No archived complete future top-20 ranking
   or actual first-entry evidence is supplied.
4. **Common endpoint returns:** quoted returns run from different T-offsets to an apparent common
   current price. That gives earlier signals more elapsed time and is not matched 24h/48h forward
   performance. A signal at T-0 has no future observation in the described window.
5. **No precise model:** the exact transforms, weights, score cutoff, missingness handling, ties,
   cross-sectional population, conviction and downstream publication filters are absent. Scores such
   as 58.1/75.0 and BEAT's claimed zero signals cannot be reproduced from qualitative descriptions.
6. **No point-in-time proof:** every replay needs closed-bar/event time plus arrival availability,
   funding as of that date, historical basis/order flow, and sufficient warmup. EMA200 on 4h bars
   alone needs 800 hours for 200 observations; a 48h scoring window is fine only with earlier warmup
   retained. Weekly RSI and a 30-day profile require still longer/appropriate histories. Current
   derivatives or current 30-day levels reused in old scans would leak future information.
7. **Continuity overclaim:** HOT's listed signal times skip T-20h. Four-hour sampling also cannot
   prove continuous acceptance between samples. The phrase that it caught the entire base is not
   established by those five listed observations.

The arithmetic 0.85^10 = 0.1968744043 is correct. Interpreting it as actual recall requires measured
conditional pass probabilities and suitable independence assumptions. The code contains correlated
AND/OR branches; neither the probability nor independence is established. Counting checks is not
a statistical proof that a specific architecture has 19.7% recall.

Repeatedly selecting models/thresholds on observed winners risks backtest overfitting; that is why
these diagnostics need untouched validation periods, a recorded model-search history and prospective
evaluation, rather than treating the preferred retrospective examples as confirmation.
[Primary research: Bailey et al., The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf).

## Proposal corrections

**Eligibility versus alpha is a useful separation, not an institutional certification.** Data
integrity and tradeability constraints should be explicit. But ret24h<=16%, RSI<70, floor distance
3%, and OI drawdown bounds are strategy/risk choices. They retain threshold cliffs, need evidence,
and differ between the report's experiment section and its proposed final design. Rejecting all
short-horizon OI contraction can exclude a price rise driven by position unwinds; OI contraction
alone does not prove a liquidation cascade. These rules should be versioned and evaluated, not
asserted to prevent ruin or false signals.

**Negative mark/index basis does not prove spot-led buying.** Binance defines mark price using
multiple inputs including index price, funding and contract/order-book information. A negative
relative price is not a causal lead/lag or spot order-flow measurement. Spot-led participation needs
separate synchronized spot/perpetual prices and flows; extreme negative funding/basis is not
automatically positive alpha.
[Binance mark/index methodology](https://www.binance.com/en/support/faq/detail/360033525071).

**The five factors are not established as orthogonal.** OI, funding/basis, flow, momentum and
compression can be correlated. Nested 7d/24h/4h/1h OI changes overlap; adding their scores can count
the same recent movement repeatedly. Positive cumulative changes over unequal horizons do not by
themselves establish acceleration. Normalize time scales or inspect disjoint log-change windows,
measure dependence in training data, and test whether the additional horizon improves holdout
results. No particular decorrelation method is assumed necessary in advance.

**Argmax factor attribution is not a semantic setup classifier.** A high OI contribution does not
establish compression/accumulation or a tradable squeeze. Separate explanatory factor attribution
from multi-label setup predicates. Evaluate all relevant setup hypotheses without letting the
first match automatically disable alternatives. Entry readiness, target/stop construction and
the existing archetype-based valuation assumptions need a separate compatibility decision; moving
labels cannot safely leave those downstream semantics implicit.

**Continuous scores do not ensure stable watch state.** Top-K cutoffs, score thresholds, hygiene
filters and argmax labels can still flicker. Explicit, versioned watch/confirmation/expiry states,
hysteresis or persistence can be tested, with immediate invalidation for genuinely invalid data.
Avoid counting a persistent watch as a new independent success at every scan.

**Changing scores does not fix admission coverage.** A 320-symbol evaluated output is not the full
522-contract market universe used in our November 3 reconstruction. The original study also had
bounded derivatives/shadow admission. Requiring fully enriched telemetry for every ranked token
without accounting for those budgets can preserve the same coverage bottleneck. Record stage-wise
attrition and distinguish missing features from adverse signals before comparing scorer quality.

**Historical measurement remains incomplete.** Existing archive classes enable studies; their
presence does not establish complete labels. Our verified original study had only 172/512 complete
24h outcomes and 87/512 complete 48h outcomes, plus a failed 48h rank-cadence criterion. Those
missing outcomes cannot be treated as negatives or silently removed to calibrate weights.

## What our separate primary-data study supports

Our retained November 3 Rome-calendar reconstruction covers all 522 current-universe contracts,
and is reproducible from complete hourly windows. It confirms strong gains for HOT, ICNT and BMT
in that particular day, **not this attachment's unidentified rolling window or replay signals**.

Before the strongest hourly moves, BMT showed six-hour price -3.53%, quote-volume activity 3.71x
its prior baseline and OI units +9.97%; ICNT showed +11.18%, 7.27x and +40.15%. CLANKER instead
showed +0.09%, 0.77x and -1.64%. These are retrospectively selected event diagnostics, not proven
first top-ten entry times. They support multiple research hypotheses, not one mandatory OI pattern.
See `2026-11-04-current-and-yesterday-gainers.md` for exact clocks, formulas and retained raw data.

## Recommended implementation decision

Keep the core frozen as the comparison. First finish the collector capacity/reliability gate and
define correct as-of feature contracts. Then build one separately versioned research challenger:

1. Fixed full-universe eligibility and recorded acquisition/admission budgets, with missingness explicit.
2. Continuous factor ranking for eligible observations; multi-label setup explanations and a separate
   watch/readiness state. Test label-decoupling and added OI horizons as separate ablations before
   attributing any improvement to a wholesale redesign.
3. Exact formula/weight/cutoff/source versions, and a strict comparable replay over identical
   symbol-time observations with realistic warmup and historical derivatives availability.
4. Deduplicated first-alert precision/recall at the same alert budget; first observed top-K lead time;
   fixed 24h/48h returns and excursions; adverse movement, liquidity and missing-data coverage.
   Report raw price outcomes separately from any hypothetical entry/stop/fee simulation.
5. Training/validation splits that account for overlapping 48h labels, untouched periods, multiple
   market regimes and fresh preregistered cohorts. Do not use this hand-picked group as the holdout.

This addresses the concern about fitting each day's winners without accepting unverified claims
that a particular replacement model already solves it. No architecture migration is implemented here.

## Verification artifacts

`scripts/research_verify_factor_proposal.py`: deterministic, offline, read-only synthetic source checks.
Evidence: `docs/security/evidence/2026-11-04-factor-proposal-source-checks.json` and
`2026-11-04-factor-proposal-worktree-checks.json`, including imported source paths and SHA256 hashes.
Both executions passed their assertions. Main-checkout focused existing tests: 44 passed in 0.04s
(`test_v25_domain.py`, `test_v27_ignition_upgrade.py`, `test_live_audit_6fixes.py`).
These tests verify current behavior, not historical returns or challenger superiority.
No production logic, candidate thresholds, dependencies, Git commits, deployments or studies changed.
