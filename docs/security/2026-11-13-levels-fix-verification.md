# Verification of the level-calculation fixes

Reviewed commit `82c2d5a` against the preceding audit of `27c360d`. Checks cover the local source and deterministic synthetic inputs. Deployed runtime identity and live market prices were outside this verification.

## Confirmed corrections

- Both long and short scanners calculate ATR from closed four-hour candles and pass it into the four-hour archetype, gate and bracket parameters. Genuine hourly inputs retain their hourly values. A normal-entry-point test with deliberately different hourly/four-hour ranges would strengthen regression coverage.
- Mixed flat/ranged candles now conserve the original example's volume. The reproduced 900-unit flat candle plus 100-unit ranged candle allocates 1,000 units.
- The tiny-price example now returns positive, tick-normalized levels. With tick 0.0000000001, VAL is 0.0000000016, POC is 0.0000000020 and VAH is 0.0000000025.
- The above-POC long bracket and the short bracket retain their original structural levels for the original fine-tick test cases.
- Retest stops retain the specified clearance below VAL and the swing low in the previous deep-structure example.
- A `WAIT_FOR_RETEST_ONLY` setup ignores the previously reproduced breakout-only touch. `CEILING_BREAKOUT` also suppresses the alternate retest trigger path.
- Within one complete candle-history evaluation, the first matching event keeps its selected basis when a later candle retests.

## Findings from the reviewed commit

The following findings describe commit `82c2d5a`. The local correction recorded below resolves these four implementation defects.

### P1 — Trigger basis is not persisted through the production lifecycle

`domain/radar_lifecycle.py:441–443` reads `radar_trigger_basis`, but `RadarLifecycleDecision` has no corresponding output field. Its active decision around lines 532–544 returns only the trigger timestamp. `application/radar_service.py:266–267` persists only that timestamp. The new lock therefore depends on reconstructing the basis from whatever candles are present on each evaluation.

A two-call reproduction still changes the return basis:

1. A dual-path setup has retest entry 100 and breakout trigger 104. At 02:30, current price 105 activates it through the live-price breakout path. Observed return is +1.0%.
2. Persist `radar_triggered_at` exactly as the service does. The next closed candle spans 99–106 and closes at 103. It qualifies as a retest and the reconstructed basis becomes 100.
3. The original 02:30 trigger timestamp remains, while displayed return becomes +3.0% after price declines to 103.

Return and persist trigger type, reference entry and associated stop alongside the timestamp. Exercise the actual reconciliation/save/reload path. Older episodes without a recoverable basis need explicit unknown semantics so later candles cannot manufacture an earlier entry.

### P1 — The below-POC path retains the artificial trigger cap

`domain/execution_brackets.py:83` still calculates `min(local_compression_high_4h, close * 1.08)` when current price is below POC. The recent correction removed the cap from the other long branches.

Current price 100, POC 110 and actual compression high 125 produce a trigger of 108.00. This is below both the structural compression high and POC. If this is intended as an early recovery signal, it needs a separate, explicit contract. Its current shared breakout field does not establish a break of that compression boundary.

### P2 — Nearest-tick rounding can erase the structural breakout buffer

The trigger normalization at `domain/execution_brackets.py:107` uses nearest-tick rounding. The short builder uses the same normalization for its breakdown trigger. Guards compare the resulting trigger with current price, rather than with the structural boundary.

Using current price 100, ATR 2 and tick size 1:

- Compression high 125 produces a long trigger of 125, exactly on resistance.
- Consolidation low 90 produces a short trigger of 90, exactly on support.

Lifecycle touch comparisons use `>=` and `<=`, so the intended buffer can disappear. Use directional tick rounding and assert the chosen strict structural relationship after rounding. Scale-equivalent compressed, low-price instruments should be covered too.

### P2 — Uncapped structural stops need positive-price admission

The new retest-stop calculation can produce a negative stop. With current price 100, entry 99, swing low 1 and four-hour ATR 2, the bracket returns stop −0.60. These are direct positive-input helper cases. They do not establish that this particular setup passes every upstream market gate.

The shared publication validator also accepts an otherwise qualifying opportunity carrying this negative stop because it checks quality and reward/risk without validating the price hierarchy. A direct validator call reproduced an empty rejection list.

Preserve the structural reference and reject impossible brackets through the normal publication path. For deep but positive structural stops, retain the actual stop distance and apply an explicit risk-budget policy where required. Raising projected targets to a chosen R multiple cannot independently validate the resulting opportunity.

## Verification

- 76 focused tests passed across execution brackets/absorption, volume indicators, lifecycle and screener data contracts.
- Ruff passed for `src`. Mypy passed for 91 source files.
- Frontend TypeScript checking passed.
- All 830 backend tests passed with RuntimeWarnings treated as errors. One existing Starlette/httpx deprecation warning remains.
- Direct calls confirmed the repaired original cases and reproduced the remaining cases above.

This review changes documentation only. The correction materially improves several calculations, while lifecycle persistence and the remaining structural-price paths still need work before full sign-off. The estimated profile methodology and its predictive quality remain separate validation questions from implementation correctness.

## Local implementation follow-up

The working-tree correction adds the following behavior.

- Lifecycle decisions and reconciliation persist the observed trigger type, entry and associated stop through JSON save/reload. Subsequent candles and refreshed suggested levels preserve these references. Already-triggered legacy episodes with incomplete references expose an unknown basis and omit the entry-relative return.
- Stop checks use the saved mode-specific stop and entry, including the existing short squeeze cap. A newly detected trigger enforces its own stop during the same evaluation. Historical checks against that stop use fully post-trigger candles because an overlapping candle cannot establish event ordering. Current prices enforce the stop immediately.
- Below-POC long breakout triggers clear the maximum of POC, the compression high and current price, plus 0.05 four-hour ATR. Retest targets retain their independent POC/VAH logic.
- Long triggers round upward and short triggers round downward to the tick increment, preserving structural clearance.
- Bracket builders reject impossible selected price plans with a typed error. Shared publication validation requires finite positive prices, the direction-appropriate stop/entry relationship and strictly ordered targets. The scanner applies this safety check even with quality filtering disabled.
- Candidate DTOs expose optional per-mode stops. Short candidates carry the bracket builder's chosen execution mode.
- Publication versions advance to `radar-v4` and `atlas-v2.7-structural-brackets-2`, forcing the existing cache-refresh path and distinguishing the changed policy.

The changes are local and uncommitted. Deployment requires a newly built release image. Existing enrolled studies retain their frozen source and measurement versions. Predictive performance still requires measured outcome evidence.

### Follow-up verification results

- All 852 backend tests passed in 152.41 seconds with RuntimeWarnings treated as errors. One existing Starlette/httpx deprecation warning remains.
- The required lifecycle, research-discovery and live-cache group passed all 72 tests.
- Ruff passed for all source files and the changed tests. Mypy passed for all 91 source files.
- Frontend TypeScript checking passed with incremental output disabled.
- `git diff --check` passed.
- Regressions cover real reconciliation and JSON reload, frozen trigger references, legacy unknown returns, quote-only stop enforcement during candle-provider failure, first-evaluation mode-specific stops for both directions, structural trigger rounding and invalid-price rejection through `screen()` with quality filtering both enabled and disabled.
- The obsolete below-POC 8% cap test now requires a trigger above both POC and the compression pivot. Its independent retest-target and reward/risk assertions remain.
