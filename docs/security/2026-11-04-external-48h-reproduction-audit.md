# Independent reproduction of the supplied November 3 experiment

Verified November 4, 2026. This updates the earlier narrative-only review after the actual files
were provided. No production rules, source inputs, deployments or studies were changed.

## Updated finding

**The supplied saved reproduction is reproducible, and the historical HOT label/gate conflict is
confirmed. Its extra signals do not yet establish better future top-gainer prediction.**

Anchor: November 3 11:59:59.999 UTC. Baseline/patch experiment: T-44h to T-0h; challenger:
T-36h to T-0h. The results are fixed-anchor diagnostic signal counts, not prospective success rates.

After inspecting the script, ran its offline reproduction with socket creation, DNS resolution
and connection helpers blocked, redirecting its output to our audit directory. Did not execute
`--fetch-and-freeze`, invoke its main fallback-to-fetch path, build a runtime, or overwrite original
results. Both the main checkout and attached research checkout reproduced the supplied saved
results JSON exactly after JSON serialization. Python in-memory tuple/list differences are not
result mismatches.

## Equal-window comparison

Recomputed baseline/patch counts on the SAME ten T-36h...T-0h observations used by the challenger:

| Token | Baseline accepted scans /10 | Three-patch accepted scans /10 | Challenger accepted scans /10 |
|---|---:|---:|---:|
| HOT | 0 | 1 | 5 |
| ICNT | 1 | 2 | 3 |
| BMT | 0 | 0 | 3 |
| MEGA | 3 | 3 | 6 |

Removing the two earlier baseline observations does not change these numerators. This resolves
the original denominator mismatch for this diagnostic comparison. The challenger additionally
accepts TRUST 3/10 and BEAT 0/10, but baseline/patch outputs for those two are absent.

Additional acceptance is verified. It is not equivalent to more independent successful forecasts,
top-gainer precision/recall, or calibrated probabilities. No historical full-universe ranking or
fixed future 24h/48h outcome comparison is calculated by this script.

## Independent data checks and provenance

- All 3,582 serialized candle rows across six symbols and three timeframes passed continuity,
  finite positive OHLC ordering, duration, closed status and volume/taker-quote constraints.
- All six 200-point hourly OI series passed unique hourly continuity and positive finite quantity checks.
- **78 overlapping hourly candles** (13 each across six symbols) matched our independently retained
  Binance responses in all eight checked OHLC/volume/taker-volume fields, exactly with Decimal.
- **255 overlapping OI samples** (85 each for HOT/ICNT/BMT) matched independently retained OI units.
- These cross-checks validate those overlaps, not all historical values or raw API arrival times.
- Source script, frozen inputs, saved results and original logs have SHA256 hashes retained;
  their contents were unchanged by the audit. The freeze files were materialized November 4,
  whereas the logs describe November 3 executions. Do not describe the reconstruction as a
  contemporaneously captured, fully immutable November 3 request tape.

The raw histories provide adequate bar counts for EMA200/weekly RSI warmup; the earlier question
about whether warmup data existed is now resolved. Temporal filtering still has defects below.

### Saved reproduction versus original task logs

All 48 baseline/patch observation lines and four summary lines match task-279 exactly.
Task-286's six token signal counts, accepted times, prices and setup labels match. However, seven
scores differ from its original stdout: five HOT scores by 0.7–0.8 points and two TRUST scores by
0.1 point. One MEGA T-8h endpoint-return display changes from -1.0% to -1.1%.
For example HOT T-36h was 61.6 in original stdout and is 60.8 in the supplied reproduction.
The saved November 4 reproduction JSON exactly matches our runs, but original task-286 numeric
stdout is not reproduced byte-for-byte. The cause is not established by this audit.

## HOT T-32h: genuine classification arbitration issue

The exact supplied snapshot reproduces:

- close 0.0003921; claimed 7d OI change about +108.82%; ret7d about +15.73%; 4h BBW 0.21928.
- Main OI branches do not qualify: their capped OI/return or tighter stealth conditions are unmet.
- BREAKOUT_LONG matches and returns before the **later structural OI fallback**, which also matches.
- With the returned breakout label, only L1 fails; all other gates pass.
- With identical numeric gate inputs and the OI accumulation label, all six gates pass.

Thus the report's broad arbitration concern is supported. My earlier review was incomplete:
although the main OI branches precede breakout, the relevant competing route here is the later
fallback. This is a historical feature-level counterfactual, not proof that the full production
pipeline would publish HOT if relabelled or that an accumulation-labelled long is economically safe.
Those missing downstream checks still matter.

## Material experiment defects

1. **Future funding and basis reused across historical scans.** `q_info` contains a single anchor
   funding/basis per token, including BMT's extreme negative values. The script uses those values
   for earlier observations, in some cases up to 44 hours earlier. Historical funding/basis are
   not supplied. This contaminates the temporal interpretation of the baseline and challenger;
   point-in-time performance cannot be inferred from these results.
2. **Baseline is not an end-to-end production replay.** It calls classifier plus six gates and
   counts acceptance. It omits universe selection, enrichment admission/telemetry guards, several
   evaluator vetoes, real continuous scoring, conviction, brackets, publication and ranking limits.
   It substitutes raw token ret24h for BTC-residual alpha; sets all four kinetic trigger flags false;
   and uses 4h squeeze alone where the real evaluator combines 4h/1h squeeze. These choices can
   change eligibility. Consequently “the current screener missed all scans” is too broad.
3. **Unequal RSI checks.** Baseline calculates daily and weekly RSI. The challenger is always called
   with daily RSI `None`, disabling its own daily-overbought check, and has no weekly check. This is
   not equivalent hygiene. Baseline also applies unfiltered latest weekly candles to earlier
   observations: T-44h and T-40h precede the latest weekly close, affecting eight actual baseline
   observations. The common ten-observation comparison excludes those two times, so this weekly
   issue does not explain that table's differences.
4. **Shortened windows labelled 7d OI.** Only 200 hourly OI observations are retained to the anchor.
   The fallback `max(0, len(oi_valid)-169)` silently uses 155, 159, 163 and 167 hours at T-44/-40/-36/-32,
   rather than requiring 168 hours. T-36/-32 are in the common comparison. Unknown 7d history should
   remain unknown or be explicitly labelled by its actual duration.
5. **Missing telemetry becomes a neutral value.** The challenger converts missing OI to zero and
   missing taker ratios to 1.0; missing OI can bypass the drain veto. No freshness, spread, liquidity
   or degraded-Stage2 checks are implemented here. It does not implement the proposed complete
   “survival hygiene” specification.
6. **The implemented factor model differs from the description.** It is a hand-weighted point sum,
   not five empirically orthogonal normalized vectors. Flow uses taker ratios/returns/wicks, not
   the described CVD z-score/BTC-residual terms. `ema200_4h`, `exc_72h` and other arguments are unused.
   Max factor budgets sum to 105 points with no final 100 cap. A score>=52 plus another Boolean
   factor-strength condition determines acceptance. Structure has step bonuses; passing a label
   through argmax does not eliminate thresholds or guarantee stable state.
7. **Floor semantics differ.** The implementation rejects close below 96% of the minimum support,
   but imposes no corresponding upper proximity bound. It does not require price to remain within
   4% of that support as the narrative says.
8. **No common outcome horizon or independent sample.** Returns are `anchor_quote / historical_close - 1`,
   with separate task-279/task-286 endpoint prices. At T-0 there is no 24h/48h future observation in
   the experiment. Repeated watches are counted repeatedly and the universe was selected after
   observing moves. Claims of “caught all bases” or model superiority exceed this evidence.

The latest OI period-end is allowed up to 60 seconds beyond the candle close. This can be legitimate
for a scan running after that boundary, but exact decision time and sample arrival must be recorded;
the candle close alone is not proof of availability. This audit does not equate the next boundary's
one-millisecond offset with a proven operational leak; the anchor funding/basis reuse is the clear
historical-time violation.

## The accepted signals were not uniformly profitable even at the shared endpoint

BMT challenger signals have displayed endpoint changes +3.1%, -2.6% and +0.1%.
MEGA's six display approximately -0.6%, -3.4%, +0.1%, -2.8%, -1.1% and -5.0% in the saved replay.
Those are not matched-horizon trade results, but they show why persistent acceptance alone cannot
be labelled forecast success. No claim about later 24h/48h performance follows from them.

## Decision

Accept the research as **reproducible diagnostic evidence for testing classification decoupling**,
not validation of a replacement trading/screening architecture. Keep the core unchanged as control.
Before a strategy decision, use point-in-time funding/basis, exact closed-bar and OI durations,
consistent hygiene, actual full-pipeline replay and the same symbol-time/alert budget. Evaluate
deduplicated forecasts against full-universe fixed 24h/48h outcomes and top-K membership, then use
untouched periods and prospective cohorts. The collector capacity and feature-contract work remain
necessary; no study or deployment was started by this verification.

## Reproduce this audit

From main checkout, with its matching source/dependencies:

```sh
PYTHONPATH=src .venv/bin/python scripts/research_audit_external_48h.py \
  /Users/jithinjacob/.gemini/antigravity/brain/04d32a04-b148-49b0-8804-0d5c73b1a80d/scratch \
  docs/security/evidence /private/tmp/atlas-external-audit-new
```

Output directory must not already exist. The original source is inspected before execution; the
script explicitly calls its offline function rather than a main routine that may fetch data.
Artifact: `docs/security/evidence/2026-11-04-external-48h-independent-audit/audit.json`.
HOT counterfactual: `docs/security/evidence/2026-11-04-external-48h-trace.json`.
Separate main/worktree replay JSONs are retained. Ruff checks/formatting passed for our audit script;
all data/reproduction assertions passed. No fresh full application test suite is claimed here.

Original script/data/results and two logs are bundled in
`docs/security/evidence/2026-11-04-external-48h-source-bundle.tar.gz`.
SHA256: `7f1cc98db36724f7b11b53a3988bc6683b711301e99e79a3a82dd511178cfb7a`.
Every bundled file was checked against the audited original hash without extracting it.
Repository-wide diff checking reports existing trailing whitespace in the unrelated
`scripts/generate_architecture_pdf.py`; that file was not changed by this audit.
