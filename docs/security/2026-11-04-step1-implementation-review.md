# Step 1 implementation verification

Date: November 4, 2026. Reviewed the supplied `faithful_48h_replay.py` and its frozen inputs/results
under `/Users/jithinjacob/.gemini/antigravity/brain/04d32a04-b148-49b0-8804-0d5c73b1a80d/scratch`.

## Verdict

**Needs changes before accepting Step 1 as a faithful historical production replay.** It is a
substantial, reproducible improvement over the earlier gate-only experiment. It now invokes the
real production pipeline, but does so on a selected subset with synthetic tradeability, neutral
macro and missing derivatives fields replaced by numeric defaults. Call-path fidelity is not
historical-input or full-universe fidelity.

This verification changed no supplied implementation or source input, core screener logic,
dependencies, services, Git history or deployment. The earlier implementation task was paused
when the user supplied Step 1 for review; no duplicate replay implementation was started.

## Verified improvements

- Inspected all 957 lines before execution. Called only its offline function with original output
  redirected locally; blocked socket creation/DNS/connection helpers after initializing the event
  loop. No fetch/freeze or live runtime was invoked.
- **Entire saved results JSON reproduced exactly**, using the current main-checkout source.
- All **72 symbol-times** have the claimed available 240 closed hourly / 480 closed four-hour bars,
  adequate weekly history, and bars ending no later than the candle cutoff.
- Independently recomputed all four OI horizons at every symbol-time from exact timestamp pairs;
  1h/4h/24h/168h changes match. The earlier shortened-7d issue is fixed in this supplied dataset.
- Fetched independent primary Binance funding history for HOT/ICNT/BMT: **66 matching funding
  records**, including the historically extreme HOT funding. Final-anchor funding is no longer
  blindly substituted for every earlier scan.
- The code executes `CryptoOpportunityScreener.screen(..., enforce_quality=True)` and separately
  records direct enriched evaluation/publication eligibility. It filters weekly bars at each cutoff
  and lets the production evaluator compute its actual kinetic/alpha/scoring features.
- Fixed 24h close-to-close endpoint returns are separated from the shared-anchor returns of the
  prior experiment. They remain price diagnostics, not executable entry/fee/slippage returns.

## Findings, ordered by importance

### P1 — Synthetic historical tradeability and neutral macro invalidate exact-production claims

Locations in the supplied script: 268–270, 298–338, 407–420, 711–714.

`quotes()` invents a one-tick bid/ask around the candle close. The production pipeline explicitly
filters on spread and ranks liquidity; this input guarantees an assumed narrow spread instead of
testing the historical market. Macro is replaced with `MacroSentimentContext()` and thus neutral
scoring; historical basis/depth become `0` in the derivatives snapshot while healthy telemetry is
reported. A zero observation and an unknown observation have different meanings.

This may be a valid controlled diagnostic **if explicitly labelled and sensitivity-tested**, but
it is not an exact historical production baseline. Some substituted fields may not affect a
particular long path; that does not establish the fidelity of the overall adapter or all outputs.

Required correction: strict mode must require the relevant historical inputs/provenance or return
unavailable. A diagnostic mode may retain explicit assumptions, with per-field limitations and no
claim of faithful historical tradeability or actual production publication.

### P1 — Seven supplied instruments cannot reproduce full-universe admission

Locations: 61–62, 278–287, 749–754.

The market contains BTC plus six hand-selected tokens. The real code runs, but universe selection,
liquidity ranks, shortlist competition and the 15-probe admission budget operate on that subset.
`max_symbols` and production scan request/configuration are not frozen. Consequently the HOT
publication proves what happens in this subset simulation, not that the historical production
scanner would admit it against all other contracts. There is no historical full-universe gain rank.

Required correction: replay the historical complete universe/quote selection context and frozen
request, or label the result a seven-instrument subset diagnostic and keep full-market publication
and discovery-performance claims unverified. Do not fill the missing universe with only winners.

### P1 — Missing/stale input contract is not enforced

Locations: 289–296, 340–349, 359–420.

Adversarial probes reproduced all of the following without modifying the source files:

| Input alteration / condition | Adapter behavior |
|---|---|
| Unknown historical basis and book depth | Returns zeros and `degraded_telemetry=False` |
| Keep only the oldest HOT funding record, almost 708 hours old | Still `degraded_telemetry=False` |
| Remove the last 12 hours of HOT candles | Stale taker data still reported healthy |
| Supply only ten 4h bars and request 480 | Returns ten bars with no warmup-unavailable failure |

The supplied intact dataset has good bar coverage, but the claimed general fail-closed contract
does not exist. There is no preflight enforcing continuity/latest-period freshness and all claimed
warmups before classifying observations. A missing symbol/history can be a silent partial feed,
exception or fewer candidates instead of a separately recorded unavailable observation.

Required correction: validate each interval/window, funding settlement freshness using its actual
interval, required field availability and types before scanning. Preserve unknowns; prevent a
failed observation from entering the comparable denominator as a failed prediction. Add automated
regressions for these malformed/stale cases, not just a successful reproduction run.

### P2 — Decision time and replay identity are not frozen

Locations: 291, 319–338, 359–365, 711–714, 737–747.

Quote `observed_at` defaults to the current wall clock, rather than the historical observation.
Funding permits the next 1,000ms and OI permits the next boundary/minute, while evaluation time is
the earlier candle close. **68/72** selected funding records are after that declared cutoff by
small boundary offsets. This need not be an operational leak for a scan run after settlement, but
the script needs a distinct decision/availability time and a consistently anchored outcome clock.
Publication arrivals are not captured, so event-time retrieval is not proof of live availability.

`load_settings()` reads ambient configuration; there is no frozen screening configuration,
source/dependency identity or input-integrity manifest checked before replay. Current instrument
metadata is used for historical dates. Reproducing today does not pin the baseline for tomorrow.

Required correction: freeze allowed settings/request/source/package hashes and dataset hashes;
set explicit historical observation timestamps; separate signal-bar cutoff from decision time and
validate all input availability against it. Document any unverified historical metadata/arrival.

### P2 — Challenger stops at object creation, not publication eligibility

Locations: 649–671; publication is checked later at 834–840 and by the full pipeline.

If the first hypothesis creates an opportunity, the wrapper immediately returns it. It also stops
at the first alternative that creates an opportunity. A candidate can subsequently fail RR/EV,
grade/conviction or another publication rule, while a later hypothesis might be publishable.
Therefore it does not evaluate every hypothesis through all claimed downstream publication checks.

Required correction before relying on Step 2: define whether this is intentionally first-opportunity
fallback or publication-aware arbitration. Freeze the selection rule and test a first opportunity
that fails publication followed by an alternative that passes. Keep this separate from baseline
replay correction and do not choose arbitration using future returns.

## Findings and report claims cross-checked

**HOT:** The subset simulation does publish HOT at T-32h with historical negative funding under
the unchanged control. This supersedes any claim from the earlier leaky inputs that the same
diagnostic would necessarily miss HOT. It does not establish an actual historical production alert.

**ICNT:** The enriched challenger at T-8h passes all gates and publication eligibility, but neither
full-pipeline model publishes it. The control fails L3 before absorption; the challenger alternative
then reaches Stage 1 absorption=33 and is rejected. The report's parenthetical “L3 (vcr<=0.85)” is
incorrect: inspected L3 checks base range/ceiling distance with archetype-specific bounds, not VCR.

**Stage 1 bottleneck:** Independently traced original production function return events and retained
all 24 control/challenger pipeline journals. MEGA T-20/T-4 reject at absorption=33; TRUST T-12/T-4
reject at 15/30; ICNT's challenger alternative rejects at 33. Quote funding is populated, but the
evaluator's local funding is `None` in the un-enriched call. This supports the stated bottleneck
under the supplied inputs. It does not justify granting full OI points before OI exists or simply
removing the threshold; any admission fix needs budget/fairness and false-positive evaluation.

**BMT attribution is overstated:** The previous continuous model's three accepted observations
were T-8/T-4/T-0. The old input tape already has 192/196/200 hourly OI samples at those observations;
each has a full 168-hour baseline. Thus shortened OI windows did not cause those three signals.
Furthermore this new challenger is multi-hypothesis production gating, not the old continuous
factor model. A change from 3 signals to 0 across different models cannot prove 100% causation by
funding leakage. Isolate funding/OI corrections while holding the model fixed to make that claim.

**No full-pipeline uplift:** Both models publish only HOT T-32h and MEGA T-12h in this supplied
experiment. ICNT's additional direct Stage 2 eligibility does not become an additional live-path
publication. Any claim of improved full-path discovery from this result would be unsupported.

**Report count correction:** TRUST gate passes are **7/12** in both saved JSON and independent replay,
not 6/12 as the narrative summary says. Opportunity creation is 6/12, which is a different stage.

## Acceptance decision and next work

Keep this as a useful **subset production-code diagnostic**. Step 1's data/time/coverage acceptance
gate is not yet complete, and Step 2 has no demonstrated full-path uplift here. Correct the
strict-versus-assumption-based replay contract, stale-data regressions, frozen identity and
reported findings before changing production strategy or enrolling another cohort.

Where historical spread, macro or full-universe snapshots were never captured, code cannot recover
them honestly. Preserve those observations as unavailable for strict comparisons. A fresh
preregistered study can supply the missing evidence once collection capacity is verified.

## Retained verification evidence

- `docs/security/evidence/2026-11-04-step1-main-replay.json` and `.log`: network-blocked replay.
- `2026-11-04-step1-window-checks.json`: all 72 window/OI calculations and funding-boundary checks.
- `2026-11-04-step1-independent-funding.json`: three primary funding endpoint responses, 66 matches.
- `2026-11-04-step1-adversarial-probes.json`: missing/stale-input probe outcomes.
- `2026-11-04-step1-source-trace.json`: Stage 1 return traces plus all 24 pipeline journals.

No new full application-suite, production deployment, capacity test or live-source-version result
is implied by these research verification checks. The original implementation remains untouched.
