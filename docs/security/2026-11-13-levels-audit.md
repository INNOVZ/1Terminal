# POC, trigger, entry and stop audit

Reviewed local HEAD `27c360d` on November 13, 2026, including the recent `6345b43` level-calculation changes. This is a local implementation and methodology review. Deployment identity and current exchange prices were outside the checks. The saved dashboard snapshot is dated November 11 and cannot establish current live levels.

## Assessment

The engine has useful structural components. Long and short profiles use a consistent 180 four-hour-bar window when available. Published prices are normalized to exchange ticks. The main brackets calculate reward/risk from their selected entry and stop, and the chart preserves backend levels for candidates carrying the current risk-anchor metadata. Recent changes add a separate handle-low anchor for long breakout stops.

Material calculation and state-management gaps remain. The present implementation does not establish an optimal strategy or a calibrated institutional-quality level model.

## Findings

### P1 — Four-hour volatility parameters receive one-hour ATR

`application/crypto_screener.py:1078` assigns `atr14 = snap_1h.atr14`. The long bracket call at line 1452 passes this value as `atr14_4h`. The short scorer repeats the assignment at line 1780 and passes the value into its bracket around line 1997. Related gate/archetype inputs also use this value under four-hour names.

The bracket's ATR buffers, stop distances and target spacing therefore use the hourly volatility scale despite their four-hour contract. Separate the timeframe-specific ATR values and preserve genuine one-hour ignition inputs. A regression should give the two timeframes deliberately different ranges and exercise the normal screening entry point.

### P1 — Distance caps can move a trigger inside the structure it claims to break

`domain/execution_brackets.py:91` and line 98 cap the local compression high at 110% of current price before forming the long trigger. The short builder at line 304 floors a breakdown trigger at 97.5% of current price.

Reproduced with valid positive inputs and exchange tick 0.01:

- Current price 100, actual compression high 125, VAH 105 and ATR 2 produce a long breakout trigger of 110.16.
- Current price 100 and actual consolidation low 90 produce a short breakdown trigger of 97.50.

Crossing those generated triggers can leave the original structural boundary intact. A distant structural level needs explicit treatment through setup admission, a separately identified nearer pivot, or a waiting state. Moving its price to fit an acceptable distance changes the signal's meaning.

### P1 — Flat candles lose their volume inside a mixed-range profile

`domain/indicators.py:865–890` allocates volume only when the intersection between a candle and bin has positive width. A candle with equal high and low has zero-width intersections and contributes no bin volume. Its volume remains included in `total_vol` and the target value-area volume.

A flat candle at 100 carrying 900 volume units, combined with a 90–110 candle carrying 100 units, produces total volume 1,000, allocated bin volume 100 and value-area volume 100. The resulting POC and 70% value-area calculation omit 90% of the supplied volume. Allocate flat-bar volume to its valid price bin and assert mass conservation across normal, flat and boundary candles.

### P1 — Radar activation ignores the selected entry mode and can change its return basis later

`domain/radar_lifecycle.py:440–476` accepts either a breakout touch or a retest observation without consulting `execution_mode`. A `WAIT_FOR_RETEST_ONLY` candidate with entry 100, stop 90 and breakout trigger 104 becomes `IN_ACTIVE_TRADE` after a candle spanning 101–106 closes at 105, despite never reaching its retest entry.

The new trigger-basis calculation also re-examines the whole candle sequence. It uses the breakout price only while no retest is present anywhere in that sequence. With a later candle spanning 99–103.5 and closing at 103, the same episode switches its return basis to 100. Displayed return changes from +1.0% at price 105 to +3.0% at price 103. The original breakout event timestamp persists.

Persist the first mode-appropriate observed trigger type, reference entry and associated stop as one episode decision. Subsequent observations need an explicit transition policy. The output remains a setup observation with no assertion of an executed fill.

### P2 — Fixed eight-decimal rounding can destroy exchange-valid price levels

`domain/indicators.py:918–925` rounds profile prices to eight decimals before exchange-tick normalization. A profile between 0.000000001 and 0.000000003 with tick 0.0000000001 produces POC, VAL and VAH equal to zero. The later tick normalization cannot recover the discarded precision. Absolute `1e-8` kernel guards also deserve scale-sensitive testing.

Preserve sufficient precision until tick normalization. Validate strictly positive, ordered levels within the observed profile range and test supported low-price instruments.

### P2 — A risk-capped stop can lose its stated structural clearance

`domain/execution_brackets.py:143–153` first places the long stop beneath the swing low and VAL, then moves it upward with `structural_stop_cap`. With entry 99, VAL 70, swing low 60 and ATR 2, the final stop is 87.12, above VAL. This contradicts the adjacent comment about preserving clearance below VAL.

A risk-budget stop and structural invalidation need explicit semantics. If the selected structure requires excessive risk, use a defined admission or nearer-structure policy. The displayed invalidation level should disclose which structure it invalidates.

## Methodology and strategy limitations

The POC is an estimated, quote-volume-weighted histogram over 50 bins. Each four-hour candle's volume is distributed with a triangular kernel centred on typical price. This reconstruction cannot recover the actual intrabar distribution of trades. Quote-volume weighting is a valid declared choice for a crypto profile, while results differ from base-volume weighting. Per-candle fallback between quote and base volume can also mix units when inputs are incomplete.

The value area expands in two-bin comparisons and reports bin midpoints. That is a defined implementation choice. Comparisons with an external platform need identical volume units, anchor times, row resolution, lower-timeframe data and value-area rules. TradingView documents lower-timeframe profile calculation and defines POC as the highest-volume level. [TradingView volume profile methodology](https://www.tradingview.com/support/solutions/43000502040-volume-profile-indicators-basic-concepts/)

Where fewer than 180 four-hour candles exist, the engine uses the shorter available history while retaining 30-day field names. Publish the actual profile start/end, bar count, volume unit and resolution so a short-listing profile can be interpreted correctly.

Long targets can be raised to 1.5R, 2.5R and 3.8R. Such risk-multiple projections define a plan but do not establish that volume structure supports those prices or that their hit probabilities justify the displayed reward/risk. Keep structural resistance levels visible alongside projected objectives. CME describes support and resistance as zones whose reliability depends on market context. [CME support and resistance](https://www.cmegroup.com/education/courses/trading-and-analysis/support-and-resistance)

Establishing a preferred configuration requires comparisons across periods, including adverse excursion, false breakouts, target/stop ordering, gaps, fees and slippage assumptions. Candle-level observations alone cannot establish execution returns or guarantee a stop fill price.

## Verification

- 71 focused tests passed across brackets/absorption, radar lifecycle, indicators and screener data contracts.
- Ruff passed for `src` and mypy passed for 91 source files.
- Frontend TypeScript checking passed.
- Deterministic direct calls reproduced the volume-loss, precision, capped-trigger, stop-clearance and radar activation/return-basis cases above.
- Current source trace confirms the ATR timeframe mismatch.

The passing tests do not cover the reproduced defects. This audit changes documentation only. Existing code, deployed services and enrolled research studies retain their current state.

## Implementation order

1. Correct ATR timeframe wiring and profile volume/precision invariants.
2. Keep breakout/breakdown levels tied to their identified structure and define excessive-distance handling.
3. Give each entry mode a consistent entry, stop and target contract. Persist its first observed trigger basis.
4. Expose profile coverage and projected-target metadata, then replay the affected cases with versioned source/runtime identities.

Any strategy correction should receive a new verified release. An already enrolled study requires its frozen runtime to retain comparable observations.
