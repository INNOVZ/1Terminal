# Historical strategy specification

The product is now a pure public-data screener. Current architecture and the ordered work plan
are in docs/architecture.md and docs/screener-research-roadmap.md. The rules below describe an
older research hypothesis and differ from current implementation; they are not validation evidence.
Do not restore exchange execution, credentials or trading workers to the screener runtime.

---

# ATLAS INSTITUTIONAL CRYPTO SCREENER — MASTER AGENT.MD SPECIFICATION

## 1. SYSTEM CONTEXT & HEXAGONAL ARCHITECTURE
You are operating on the Atlas Institutional Crypto Screener (Python 3.13 / FastAPI / Next.js 16).
The platform enforces strict Hexagonal (Ports & Adapters) separation:
1. `domain/`: Pure business models (`CryptoOpportunity`, `Direction`, `SetupLabel`, `Candle`, `IndicatorSnapshot`, `ChecklistVerification`). ZERO external I/O, network calls, or framework dependencies.
2. `application/`: `CryptoOpportunityScreener` and `AlgorithmicTradingSystem` orchestrating multi-factor scoring, Wyckoff classification, VPVR computation, and execution bracket construction.
3. `adapters/`: Binance USD-M async client (`/fapi/v1/`), CCXT bridge, DeFiLlama stablecoin telemetry, Alternative.me Fear & Greed API, and FastAPI REST/WSS endpoints.

### Strict Engineering Guardrails:
- Never introduce network calls, database queries, or async I/O inside `domain/`.
- Preserve deterministic unit testability for all gates, archetype classifiers, scoring functions, and bracket builders.
- Enforce rate-limit budgeting (`asyncio.Semaphore(10)`) and batch endpoints (`/fapi/v1/premiumIndex`, `/fapi/v1/ticker/24hr`) before making per-symbol kline requests.
- Guard all divisions with `max(denominator, 1e-8)` to prevent `ZeroDivisionError` on flat candles or zero-volume bars.

---

## 2. UNIVERSE SELECTION & TELEMETRY PIPELINE (5-STAGE HYGIENE)

### 2.1 Multi-Stage Filtering Pipeline
1. **Stage 1 (Asset Class Gate)**: Include ONLY `USDT` and `USDC` perpetual futures (`contractType == "PERPETUAL"`, `status == "TRADING"`).
2. **Stage 2 (Toxic Asset Blacklist)**: Exclude non-crypto pegs, synthetic commodities, dead coins, and stablecoin pairs:
   `{"PAXG", "XAU", "XAG", "COPPER", "WLFI", "USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "USDPUSDT", "DAIUSDT"}`
3. **Stage 3 (Symbol Hygiene & Tick Normalization)**:
   - Enforce strict ASCII validation.
   - Parse `PRICE_FILTER.tickSize` and `LOT_SIZE.stepSize` from `/fapi/v1/exchangeInfo`.
   - Normalize all output execution bracket prices and quantities:
     `normalized_price = round(raw_price / tick_size) * tick_size`
     `normalized_qty = floor(raw_qty / step_size) * step_size`
4. **Stage 4 (Liquidity, Spread & Realized Volatility Floor)**:
   - Minimum 24h Quote Volume (`quoteVolume`) >= `$500,000`.
   - Maximum Bid-Ask Spread `(askPrice - bidPrice) / midPrice <= 0.0050` (50 bps).
   - Minimum 4H Normalized ATR (`natr_4h = atr14_4h / max(close, 1e-8) >= 0.012`) to filter out dormant low-volatility pairs.
5. **Stage 5 (Top 160 Institutional Liquidity Stratification)**:
   - Compute the Institutional Liquidity Index (`ILI`) across all symbols passing Stage 4 and select the **Top 160** instruments descending:
     `ILI = 0.40 * pct_rank(median_quote_vol_30d) + 0.30 * pct_rank(open_interest_usd) + 0.30 * pct_rank(quote_vol_24h * natr_4h)`

### 2.2 Multi-Timeframe Telemetry & 1H Triangular VPVR
- Concurrently ingest Daily (`1d`), 4-Hour (`4h`), and 1-Hour (`1h`) candle series.
- Compute the 30-day Volume Profile (50 price bins: `poc_30d`, `vah_30d`, `val_30d` covering 70% value area) using **720 x 1H candles** (`limit=720`).
- Distribute each 1H candle's `quote_volume` across bins intersecting `[low, high]` using a triangular kernel centered at Typical Price `tp = (high + low + close) / 3.0`.
- Extract `taker_buy_ratio` directly from Binance kline index `[10]` (`taker_buy_quote_vol / max(quote_vol, 1e-8)`).
- Extract Perp-Spot Basis from `/fapi/v1/premiumIndex`:
  `basis_bps = ((mark_price - index_price) / max(index_price, 1e-8)) * 10000.0`

---

## 3. DETERMINISTIC SETUP ARCHETYPE CLASSIFICATION

If a candidate satisfies multiple archetypes within the same direction, assign the highest-priority archetype listed below.

### 3.1 LONG Setup Archetypes (Priority Order)
1. **`COIL_SPRING_LONG` (Wyckoff Phase C Shakeout)**:
   - `low_24h < min(ema50_4h, poc_30d) - 0.10 * atr14_4h` (terminal shakeout below support)
   - `close >= max(ema50_4h, poc_30d * 0.99)` (rapid reclaim of 4H EMA50 / POC shelf)
   - `vcr_4h <= 0.90` (down-volume drying up on pullback bars)
2. **`OI_ACCUMULATION_LONG` (Smart Money Absorption)**:
   - `0.10 <= oi_delta_7d <= 0.60` (+10% to +60% 7-day OI expansion)
   - `bb_inside_kc_4h == True or bb_bandwidth_4h <= 0.20` (tight volatility base)
   - `abs(ret_7d) <= 0.12` (price remains compressed during OI buildup)
3. **`BREAKOUT_LONG` (Volume & OI Expansion)**:
   - `close >= local_swing_high_4h or close >= vah_30d`
   - `rvol_4h >= 1.25 and norm_momentum_1h > 0 and oi_delta_4h >= 0.01`
4. **`MOMENTUM_EMERGING_LONG` (Residual Alpha Regime Shift)**:
   - `btc_residual_alpha_24h >= 0.03` (>= +3% 24h outperformance vs BTC)
   - `macd_hist_4h > macd_hist_4h_prev and macd_hist_1h > 0`
   - `taker_buy_ratio_4h >= 0.535` (aggressive taker buying > 1.15x)

### 3.2 SHORT Setup Archetypes (Priority Order)
1. **`UTAD_DIVERGENCE_SHORT` (Wyckoff Phase C Upthrust After Distribution)**:
   - `pos_in_range_30d >= 0.65 and high_24h >= high_30d * 0.985` (sweeps or tests 30d high)
   - `close <= high_24h - 0.50 * atr14_4h` (confirmed rejection back inside range)
   - `has_4h_bearish_rsi_div == True and macd_hist_4h < macd_hist_4h_prev`
2. **`EXHAUSTION_REVERSAL_SHORT` (Multi-Timeframe Overbought Climax)**:
   - `pos_in_range_30d >= 0.65 and close <= high_24h - 0.50 * atr14_4h`
   - `(rsi_1d >= 76.0 or rsi_1w >= 76.0) and upper_wick_ratio_4h >= 0.45`
   - `oi_delta_7d >= 0.25` (heavy open interest surge into climax wick)
3. **`DISTRIBUTION_SHORT` (Macro Rollover Breakdown)**:
   - `runup_30d >= 0.35 or ret_14d >= 0.22` (prior vertical markup)
   - `close < ema50_4h and close < poc_30d` (coiling below both 4H EMA50 and 30d POC)
   - `macd_hist_4h < 0.0 and macd_hist_4h < macd_hist_4h_prev and taker_buy_ratio_24h <= 0.47`

---

## 4. MANDATORY QUANTITATIVE LONG GATES (L1–L6)

Every LONG candidate must pass all 6 gates (L1 through L6):

### Gate L1: Minervini Trend & Wyckoff Spring Baseline
- `is_standard_trend = (close >= ema50_4h * 0.985) and (ema20_1h >= ema50_4h * 0.975) and (ema50_4h >= ema200_4h * 0.96)`
- `is_wyckoff_spring = (low_24h < ema50_4h - 0.15 * atr14_4h) and (close >= ema50_4h) and (vcr_4h <= 0.90)`
- **Pass Condition**: `is_standard_trend or is_wyckoff_spring`

### Gate L2: Anti-Chasing 2.0 & High-Tight Flag Continuation
- `atr_extension_4h = (close - ema20_4h) / max(atr14_4h, 1e-8)`
- `standard_l2 = (excursion_72h <= 0.36) and (-0.08 <= ret_24h <= 0.14) and (rsi_1h <= 76.0) and (rsi_4h <= 70.0) and (atr_extension_4h <= 2.25)`
- `is_high_tight_flag = (((high_4h - low_4h) / max(close, 1e-8)) <= 1.5 * natr_4h) and (close >= ema20_1h * 0.99) and (((close - ema20_1h) / max(atr14_1h, 1e-8)) <= 1.10) and (funding_rate_8h <= 0.0001) and (oi_delta_24h >= 0.10) and (taker_buy_ratio_4h >= 0.52)`
- `flag_l2 = is_high_tight_flag and (excursion_72h <= 0.60) and (ret_24h <= 0.35) and (rsi_1h <= 78.0)`
- **Pass Condition**: `standard_l2 or flag_l2`

### Gate L3: Multi-Week Base Duration & Ceiling Proximity
- `range_height_30d = (high_30d - low_30d) / max(low_30d, 1e-8)`
- `dist_to_ceiling = (high_30d - close) / max(close, 1e-8)`
- `standard_l3 = (range_height_30d <= 0.65) and (-0.03 <= dist_to_ceiling <= 0.35)`
- `flag_l3 = is_high_tight_flag and (range_height_30d <= 1.20) and (-0.05 <= dist_to_ceiling <= 0.35)`
- **Pass Condition**: `standard_l3 or flag_l3`

### Gate L4: 30-Day VPVR Value Area Acceptance
- `va_position = (close - val_30d) / max(vah_30d - val_30d, 1e-8)`
- **Pass Condition**: `(close >= poc_30d * 0.985) and (va_position >= 0.25)`

### Gate L5: Archetype-Conditional Volatility Compression
- `in_current_squeeze = bb_inside_kc_4h or (bb_bandwidth_4h <= 0.24)`
- `in_recent_squeeze_expansion = any(bb_inside_kc_4h_lookback[1:7]) and (rvol_4h >= 1.25)`
- **Pass Condition**:
  - For `COIL_SPRING_LONG`, `OI_ACCUMULATION_LONG`: `in_current_squeeze`
  - For `BREAKOUT_LONG`, `MOMENTUM_EMERGING_LONG`: `in_current_squeeze or in_recent_squeeze_expansion`

### Gate L6: S/R Ceiling Absorption & Bull-Trap Filter
- If `dist_to_ceiling <= 0.045`:
  - **Pass Condition**: `(oi_delta_7d >= 0.0) and (oi_delta_4h >= 0.015) and (rvol_4h >= 1.20) and (taker_buy_ratio_4h >= 0.52)`
- Else: `True`

---

## 5. MANDATORY QUANTITATIVE SHORT GATES (S1–S5 — TWO-TRACK PRECISION SPEC)

Resolves the collision between Upper-Range Climax Tops (`Track A`) and Structural EMA50/POC Breakdowns (`Track B`) without sacrificing screening quality:

### Gate S1: Mandatory Macro Run-Up
- `ema50_peak_excursion = (high_7d - ema50_4h) / max(ema50_4h, 1e-8)`
- **Pass Condition**:
  `(runup_30d >= 0.35) or (ret_14d >= 0.22 and ema50_peak_excursion >= 0.15)`

### Gate S3: Anti-Capitulation & Floor Protection (Evaluated Before Track Split)
- `dist_72h_low = (close - low_72h) / max(low_72h, 1e-8)`
- `pos_in_range_30d = (close - low_30d) / max(high_30d - low_30d, 1e-8)`
- `downward_atr_ext = (ema20_4h - close) / max(atr14_4h, 1e-8)`
- **Pass Condition**:
  `(dist_72h_low > 0.04) and (rsi_1h >= 30.0) and (rsi_4h >= 38.0) and (pos_in_range_30d >= 0.45) and (downward_atr_ext <= 1.75)`

### Gate S2 + S4 Paired Evaluation (Two-Track Structure & Exhaustion):
- **Track A (Upper-Range Climax Blowoff / Wyckoff UTAD)**:
  - `rejected_from_high = close <= (high_24h - 0.50 * atr14_4h)`
  - `is_climax_blowoff = (rsi_1d >= 76.0) and (upper_wick_ratio_4h >= 0.45)`
  - `is_utad_divergence = has_4h_bearish_rsi_div and (macd_hist_4h < macd_hist_4h_prev)`
  - `track_a_pass = (pos_in_range_30d >= 0.65) and rejected_from_high and (is_climax_blowoff or is_utad_divergence)`
- **Track B (Structural Breakdown / Macro Distribution Rollover)**:
  - `below_key_structure = (close < ema50_4h) and (close < poc_30d)`
  - `momentum_and_orderflow_decay = (macd_hist_4h < 0.0) and (macd_hist_4h < macd_hist_4h_prev) and (taker_buy_ratio_24h <= 0.47)`
  - `track_b_pass = below_key_structure and momentum_and_orderflow_decay`
- **Pass Condition (S2 & S4 Combined)**: `track_a_pass or track_b_pass`

### Gate S5: Bimodal Derivatives Crowding (Trapped Longs OR Short-Squeeze Flush)
- `trapped_longs = (funding_rate_8h >= 0.0001 or basis_bps >= 12.0) and (oi_delta_7d >= 0.15)`
- `squeeze_flush = (funding_rate_8h <= -0.0003) and (oi_delta_7d >= 0.25) and (oi_delta_1h <= -0.015) and (taker_buy_ratio_1h <= 0.46)`
- **Pass Condition**: `trapped_longs or squeeze_flush`

---

## 6. EXECUTION BRACKET CONSTRUCTION & R-MULTIPLE GEOMETRY

All bracket prices must be rounded to the nearest `tick_size` (Section 2.1).
Compute volatility-scaled stop multiplier:
`stop_atr_mult = clip(0.35 + 5.0 * max(0.0, natr_4h - 0.02), 0.35, 0.65)`

### 6.1 LONG 5-Level Execution Bracket
- `primary_shelf = max(ema50_4h, poc_30d)`
- `retest_entry = primary_shelf + 0.15 * atr14_4h` (Limit Buy)
- `breakout_trigger = local_compression_high_4h + 0.05 * atr14_4h` (Stop-Market Buy)
- `invalidation_stop = swing_low_4h - stop_atr_mult * atr14_4h` (Hard Stop Loss)
- `effective_entry = retest_entry if (close <= breakout_trigger) else breakout_trigger`
- `risk_unit = max(effective_entry - invalidation_stop, 0.25 * atr14_4h)`
- `swing_range = max(high_30d - swing_low_4h, atr14_4h * 3.0)`
- **Targets**:
  - `t1 = max(high_30d, vah_30d)`
  - `t2 = swing_low_4h + 1.618 * swing_range`
  - `t3 = swing_low_4h + 2.618 * swing_range`

### 6.2 SHORT 5-Level Execution Bracket
- **Strict Hierarchy**: `invalidation_stop > retest_entry >= close > breakdown_trigger > t1 > t2 > t3`
- `primary_ceiling = max(ema50_4h, poc_30d)`
- `retest_entry = max(primary_ceiling - 0.15 * atr14_4h, close)` (Limit Sell)
- `breakdown_trigger = local_consolidation_low_4h - 0.05 * atr14_4h` (Stop-Market Sell)
- `invalidation_stop = max(swing_high_4h, vah_30d) + stop_atr_mult * atr14_4h` (Hard Stop Loss)
- `effective_entry = retest_entry if (close >= breakdown_trigger) else breakdown_trigger`
- `risk_unit = max(invalidation_stop - effective_entry, 0.25 * atr14_4h)`
- `swing_range = max(swing_high_4h - low_30d, atr14_4h * 3.0)`
- **Targets**:
  - `t1 = min(poc_30d, val_30d) if (close > poc_30d) else low_30d`
  - `t2 = swing_high_4h - 1.618 * swing_range`
  - `t3 = swing_high_4h - 2.618 * swing_range`

### 6.3 R-Multiples
- `r_t1 = abs(t1 - effective_entry) / risk_unit`
- `r_t2 = abs(t2 - effective_entry) / risk_unit`
- `r_t3 = abs(t3 - effective_entry) / risk_unit`
- `asymmetric_rr = r_t2` (Mandate `asymmetric_rr >= 2.50`)

---

## 7. MULTI-FACTOR SCORING, CONVICTION & EXPECTANCY (EV_R)

### 7.1 5-Factor Orthogonal Regression (0.0 to 100.0)
`quantitative_score = 0.25 * trend_score + 0.25 * momentum_score + 0.25 * derivatives_score + 0.15 * alpha_score + 0.10 * volume_score`

1. **`trend_score` (25% Weight — Regime Adaptive)**:
   - For `COIL_SPRING_LONG` or `OI_ACCUMULATION_LONG`:
     `adx_subscore = clip(100.0 - 4.0 * max(0.0, adx_4h - 14.0), 0.0, 100.0) if (dmi_plus >= dmi_minus * 0.90) else 25.0`
   - For all other Long and Short archetypes:
     `adx_subscore = clip(4.0 * (adx_4h - 15.0) + (20.0 if adx_4h > adx_4h_prev else 0.0), 0.0, 100.0)`
   - `trend_score = 0.40 * ema_alignment_score + 0.35 * adx_subscore + 0.25 * stage_score`
2. **`momentum_score` (25% Weight)**:
   - Longs: Bollinger Bandwidth compression (`< 0.10` = 100 pts), `bb_inside_kc_4h` bonus (+20 pts), MACD histogram velocity.
   - Shorts: 4H/1D RSI divergence depth, upper climax wick ratio (`>= 0.45`), negative MACD histogram acceleration.
3. **`derivatives_score` (25% Weight)**:
   - Longs: 7d/4h OI velocity, negative/neutral funding arbitrage, `taker_buy_ratio_4h >= 0.52`, spot premium (`basis_bps < -5.0`).
   - Shorts: Crowded positive funding (`APR > 25%`) + 7d OI surge, OR short-squeeze OI flush (`oi_delta_1h <= -0.015`), perp premium (`basis_bps >= 12.0`).
4. **`alpha_score` (15% Weight)**:
   - Beta-adjusted residual alpha vs BTC over 6h, 24h, and 7d windows.
5. **`volume_score` (10% Weight)**:
   - Relative volume surge (`rvol_4h >= 1.25`), pullback volume contraction (`vcr_4h <= 0.90`), and 24h USD book depth.

### 7.2 Composite Conviction Ranking (70.0 Minimum Floor)
Empirical archetype baseline win rates (`W_base`):
- `COIL_SPRING_LONG`: `0.58`
- `OI_ACCUMULATION_LONG`: `0.56`
- `BREAKOUT_LONG`: `0.52`
- `MOMENTUM_EMERGING_LONG`: `0.54`
- `UTAD_DIVERGENCE_SHORT`: `0.57`
- `EXHAUSTION_REVERSAL_SHORT`: `0.53`
- `DISTRIBUTION_SHORT`: `0.55`

Compute regime-adjusted win probability `p_win` using Macro Overextension Index (`coi` in 0..100):
- `gamma = -0.12 if direction == "LONG" else +0.14`
- `p_win = clip(W_base[archetype] + gamma * ((coi - 50.0) / 50.0) + 0.08 * ((quantitative_score - 70.0) / 30.0), 0.30, 0.78)`
- `conviction = 0.40 * (p_win * 100.0) + 0.60 * quantitative_score`
- Disqualify any candidate with `conviction < 70.0`.

### 7.3 3-Tier Conditional Mathematical Expectancy (EV_R)
Weights: `w1 = 0.50` (T1), `w2 = 0.30` (T2), `w3 = 0.20` (T3).
Conditional probabilities with stop ratcheted to breakeven (`0.0R`) after T1:
- `p_t1 = p_win`
- `p_t2_given_t1 = 0.58`
- `p_t3_given_t2 = 0.42`
- `ev_r = (p_t1 * w1 * r_t1) + (p_t1 * p_t2_given_t1 * w2 * r_t2) + (p_t1 * p_t2_given_t1 * p_t3_given_t2 * w3 * r_t3) - ((1.0 - p_t1) * 1.0)`
- **Grade A Threshold**: `ev_r >= 1.50` and `conviction >= 75.0` and `asymmetric_rr >= 2.50`.

---

## 8. THE 5-POINT INSTITUTIONAL CHECKLIST
Populate `ChecklistVerification` with 5 deterministic boolean flags:
1. `value_acceptance`: `(close >= poc_30d * 0.985)` (Long) OR `(close < poc_30d or close <= high_24h - 0.50 * atr14_4h)` (Short).
2. `volatility_structure`: `(in_current_squeeze or in_recent_squeeze_expansion)` (Long) OR `(track_a_pass or track_b_pass)` (Short).
3. `momentum_alignment`: `(ema20_4h >= ema50_4h * 0.985 and macd_hist_4h >= macd_hist_4h_prev)` (Long) OR `(macd_hist_4h < macd_hist_4h_prev or macd_hist_4h < 0)` (Short).
4. `volume_microstructure`: `(vcr_4h <= 0.95 or rvol_4h >= 1.20)` (Long) OR `(upper_wick_ratio_4h >= 0.45 or taker_buy_ratio_24h <= 0.47)` (Short).
5. `derivatives_positioning`: `(oi_delta_7d >= 0.0 and taker_buy_ratio_4h >= 0.51)` (Long) OR `(trapped_longs or squeeze_flush)` (Short).

---

## 9. MACRO OVEREXTENSION ARCHITECTURE (MOEA)
Compute Composite Overextension Index (`coi` in 0.0..100.0):
`coi = 0.35 * benchmark_excursion + 0.30 * leverage_crowding + 0.20 * market_breadth + 0.15 * stablecoin_liquidity`
- `benchmark_excursion` (35%): BTC & ETH distance from Daily EMA50, Daily Bollinger Upper Band, and 14d RSI excursion.
- `leverage_crowding` (30%): Annualized funding rate across top 20 perpetuals (`> 25% APR` = extreme long vulnerability).
- `market_breadth` (20%): % of altcoins trading above 20d SMA + Alternative.me Fear & Greed Index.
- `stablecoin_liquidity` (15%): 7d net issuance flow of USDT/USDC via DeFiLlama.

---

## 10. OPERATIONAL CADENCE (DUAL-LOOP SCHEDULER)
1. **Fast Evaluation Loop (Every 15 Minutes on Kline Close +3s)**:
   - Polls `/fapi/v1/premiumIndex` and `/fapi/v1/ticker/24hr` in batch, refreshes incremental 1H/4H candles for the Top 160 universe, writes `data/screener_latest.json`, and pushes WebSocket diffs.
2. **Settlement & Macro Loop (Every 8 Hours at `00:00:30`, `08:00:30`, `16:00:30 UTC`)**:
   - Re-computes Top 160 `ILI` universe, refreshes 30d 1H VPVR profiles, updates DeFiLlama stablecoin flows, and snapshots post-settlement funding positioning.

---

## 11. REFERENCE PYTHON DOMAIN IMPLEMENTATION (`domain/screener_gates.py`)

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class GateEvaluationInput:
    close: float
    open_4h: float
    high_4h: float
    low_4h: float
    high_24h: float
    low_24h: float
    low_72h: float
    high_7d: float
    high_30d: float
    low_30d: float
    ema20_1h: float
    ema20_4h: float
    ema50_4h: float
    ema200_4h: float
    atr14_1h: float
    atr14_4h: float
    rsi_1h: float
    rsi_4h: float
    rsi_1d: float
    macd_hist_1h: float
    macd_hist_4h: float
    macd_hist_4h_prev: float
    bb_inside_kc_4h: bool
    bb_inside_kc_4h_lookback: tuple[bool, ...]
    bb_bandwidth_4h: float
    poc_30d: float
    vah_30d: float
    val_30d: float
    rvol_4h: float
    vcr_4h: float
    ret_24h: float
    ret_7d: float
    ret_14d: float
    runup_30d: float
    excursion_72h: float
    oi_delta_1h: float
    oi_delta_4h: float
    oi_delta_24h: float
    oi_delta_7d: float
    funding_rate_8h: float
    basis_bps: float
    taker_buy_ratio_1h: float
    taker_buy_ratio_4h: float
    taker_buy_ratio_24h: float
    has_4h_bearish_rsi_div: bool


def evaluate_long_gates(s: GateEvaluationInput, archetype: str) -> tuple[bool, str | None]:
    natr_4h = s.atr14_4h / max(s.close, 1e-8)

    # Gate L1: Minervini Trend & Wyckoff Spring Baseline
    is_standard_trend = (
        (s.close >= s.ema50_4h * 0.985)
        and (s.ema20_1h >= s.ema50_4h * 0.975)
        and (s.ema50_4h >= s.ema200_4h * 0.96)
    )
    is_wyckoff_spring = (
        (s.low_24h < s.ema50_4h - 0.15 * s.atr14_4h)
        and (s.close >= s.ema50_4h)
        and (s.vcr_4h <= 0.90)
    )
    if not (is_standard_trend or is_wyckoff_spring):
        return False, "REJECT_L1_TREND_OR_SPRING_BASELINE"

    # Gate L2: Anti-Chasing 2.0 & High-Tight Flag Continuation
    atr_ext_4h = (s.close - s.ema20_4h) / max(s.atr14_4h, 1e-8)
    standard_l2 = (
        (s.excursion_72h <= 0.36)
        and (-0.08 <= s.ret_24h <= 0.14)
        and (s.rsi_1h <= 76.0)
        and (s.rsi_4h <= 70.0)
        and (atr_ext_4h <= 2.25)
    )
    is_high_tight_flag = (
        (((s.high_4h - s.low_4h) / max(s.close, 1e-8)) <= 1.5 * natr_4h)
        and (s.close >= s.ema20_1h * 0.99)
        and (((s.close - s.ema20_1h) / max(s.atr14_1h, 1e-8)) <= 1.10)
        and (s.funding_rate_8h <= 0.0001)
        and (s.oi_delta_24h >= 0.10)
        and (s.taker_buy_ratio_4h >= 0.52)
    )
    flag_l2 = (
        is_high_tight_flag
        and (s.excursion_72h <= 0.60)
        and (s.ret_24h <= 0.35)
        and (s.rsi_1h <= 78.0)
    )
    if not (standard_l2 or flag_l2):
        return False, "REJECT_L2_ANTI_CHASING"

    # Gate L3: Base Duration & Ceiling Proximity
    range_height_30d = (s.high_30d - s.low_30d) / max(s.low_30d, 1e-8)
    dist_to_ceiling = (s.high_30d - s.close) / max(s.close, 1e-8)
    standard_l3 = (range_height_30d <= 0.65) and (-0.03 <= dist_to_ceiling <= 0.35)
    flag_l3 = is_high_tight_flag and (range_height_30d <= 1.20) and (-0.05 <= dist_to_ceiling <= 0.35)
    if not (standard_l3 or flag_l3):
        return False, "REJECT_L3_BASE_DURATION"

    # Gate L4: 30-Day VPVR Value Area Acceptance
    va_pos = (s.close - s.val_30d) / max(s.vah_30d - s.val_30d, 1e-8)
    if not ((s.close >= s.poc_30d * 0.985) and (va_pos >= 0.25)):
        return False, "REJECT_L4_VPVR_VALUE_ACCEPTANCE"

    # Gate L5: Archetype-Conditional Volatility Compression
    in_current_squeeze = s.bb_inside_kc_4h or (s.bb_bandwidth_4h <= 0.24)
    in_recent_squeeze_exp = any(s.bb_inside_kc_4h_lookback[:6]) and (s.rvol_4h >= 1.25)
    if archetype in ("COIL_SPRING_LONG", "OI_ACCUMULATION_LONG"):
        l5_pass = in_current_squeeze
    else:
        l5_pass = in_current_squeeze or in_recent_squeeze_exp
    if not l5_pass:
        return False, "REJECT_L5_VOLATILITY_COMPRESSION"

    # Gate L6: S/R Ceiling Absorption & Bull-Trap Filter
    if dist_to_ceiling <= 0.045:
        l6_pass = (
            (s.oi_delta_7d >= 0.0)
            and (s.oi_delta_4h >= 0.015)
            and (s.rvol_4h >= 1.20)
            and (s.taker_buy_ratio_4h >= 0.52)
        )
        if not l6_pass:
            return False, "REJECT_L6_BULL_TRAP_RESISTANCE"

    return True, None

def evaluate_short_gates(s: GateEvaluationInput) -> tuple[bool, str | None]:
    # Gate S1: Mandatory Macro Run-Up
    ema50_peak_excursion = (s.high_7d - s.ema50_4h) / max(s.ema50_4h, 1e-8)
    s1_pass = (s.runup_30d >= 0.35) or (s.ret_14d >= 0.22 and ema50_peak_excursion >= 0.15)
    if not s1_pass:
        return False, "REJECT_S1_NO_MACRO_RUNUP"

    # Gate S3: Anti-Capitulation & Floor Protection
    dist_72h_low = (s.close - s.low_72h) / max(s.low_72h, 1e-8)
    pos_in_range_30d = (s.close - s.low_30d) / max(s.high_30d - s.low_30d, 1e-8)
    downward_atr_ext = (s.ema20_4h - s.close) / max(s.atr14_4h, 1e-8)
    s3_pass = (
        (dist_72h_low > 0.04)
        and (s.rsi_1h >= 30.0)
        and (s.rsi_4h >= 38.0)
        and (pos_in_range_30d >= 0.45)
        and (downward_atr_ext <= 1.75)
    )
    if not s3_pass:
        return False, "REJECT_S3_CAPITULATION_FLOOR"

    # Gate S2 + S4 Paired Track A: Upper-Range Climax Blowoff / Wyckoff UTAD
    upper_wick_ratio_4h = (s.high_4h - max(s.open_4h, s.close)) / max(s.high_4h - s.low_4h, 1e-8)
    rejected_from_high = s.close <= (s.high_24h - 0.50 * s.atr14_4h)
    is_climax_blowoff = (s.rsi_1d >= 76.0) and (upper_wick_ratio_4h >= 0.45)
    is_utad_divergence = s.has_4h_bearish_rsi_div and (s.macd_hist_4h < s.macd_hist_4h_prev)
    track_a_climax_utad = (
        (pos_in_range_30d >= 0.65)
        and rejected_from_high
        and (is_climax_blowoff or is_utad_divergence)
    )

    # Gate S2 + S4 Paired Track B: Structural Breakdown / Macro Distribution Rollover
    below_key_structure = (s.close < s.ema50_4h) and (s.close < s.poc_30d)
    momentum_and_orderflow_decay = (
        (s.macd_hist_4h < 0.0)
        and (s.macd_hist_4h < s.macd_hist_4h_prev)
        and (s.taker_buy_ratio_24h <= 0.47)
    )
    track_b_distribution = below_key_structure and momentum_and_orderflow_decay

    if not (track_a_climax_utad or track_b_distribution):
        return False, "REJECT_S2_S4_STRUCTURE_EXHAUSTION_MISMATCH"

    # Gate S5: Bimodal Derivatives Crowding (Trapped Longs OR Short-Squeeze Flush)
    trapped_longs = (s.funding_rate_8h >= 0.0001 or s.basis_bps >= 12.0) and (s.oi_delta_7d >= 0.15)
    squeeze_flush = (
        (s.funding_rate_8h <= -0.0003)
        and (s.oi_delta_7d >= 0.25)
        and (s.oi_delta_1h <= -0.015)
        and (s.taker_buy_ratio_1h <= 0.46)
    )
    if not (trapped_longs or squeeze_flush):
        return False, "REJECT_S5_NO_DERIVATIVES_CROWDING"

    return True, None
