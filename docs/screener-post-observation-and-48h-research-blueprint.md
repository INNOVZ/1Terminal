# Atlas Screener: Post-Observation Engineering, Vulnerability Forensics & 24h/48h Research Blueprint

> **Document Classification:** Canonical High-Order Engineering & Research Reference (Human + Coding Agent Accessible)  
> **Baseline Observation Study:** `71346822-f686-4bb1-99ec-0f56ef8f0ea1` (Nov 8, 2026 13:50 UTC – Nov 10, 2026 14:10 UTC, 577 5-minute snapshots)  
> **Active Strategy & Lifecycle Versions:** `screener_discovery_mode="discovery_v2"`, `radar_lifecycle_version="radar-v2"`, Research Challenger Policy `discovery-v2-full336-rvol4h-watch20`  
> **Head Revision at Publication:** `b1f510b` (`main`)

---

## 1. Executive Summary & Context for Coding Agents

Following the completion of the 48-hour prospective rank-observation study (`71346822-f686-4bb1-99ec-0f56ef8f0ea1`, 512 frozen USD-M perpetual contracts, 55 subsequent top-10 daily gainer entrants), we conducted a complete forensic reconstruction (`docs/security/2026-11-11-pre-gainer-deep-research.md`) of every top gainer over the 24h and 48h horizons against 64 reproducibly sampled non-entrants.

Between commits `4b5174b` and `403393d`, we re-architected both:
1. **The Live Production Screener (`CryptoOpportunityScreener` + `radar-v2` in `portal.py`)** to capture upcoming 24h/48h top gainers during their **quiet pre-breakout accumulation coil (`T-24h` to `T-6h`)** and **early Volume-Velocity Ignition (`T-6h` to `T-1h`)**, while strictly requiring **5/5 checklist gates** and immediately evicting degraded (`<5/5`) or expired (`>96h`) setups.
2. **The Prospective 24h/48h Research & Collection Harness (`DiscoveryScreener` + isolated Sampler/Reporter workers)** to execute zero-gap paired 24h/48h studies with full Stage-0-to-Stage-2 decision journals and temporal state-transition tracking (`--previous-study`).

---

## 2. What the 24h / 48h Observation Study Revealed About Future Top Gainers

### 2.1 Ground-Truth Top Gainers vs. Momentum Traps (`Nov 8–10, 2026`)
During the 48-hour study window, blindly buying the existing Top-10 Gainers at scan time (**Momentum Reference Arm**) lost **-2.47% on average** (`7/10` negative, including catastrophic traps like `JCTUSDT` **-35.51%**, `QUSDT` **-20.29%**, `TAUSDT` **-11.90%**, and `BMTUSDT` **-10.68%**). Our anti-chasing gates successfully vetoed 100% of those traps.

Meanwhile, the **true upcoming 24h and 48h top gainers** all originated from quiet, unextended mid-to-low turnover coils that were completely invisible to top-gainer leaderboards at `T=0`:

| Symbol | 24h Return | 48h Return | Peak Return | Pre-Pump 24h Vol & Rank | Pre-Pump 24h Chg / Range | Why Legacy v1 Missed It at `T=0` |How `discovery_v2` Now Captures It |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- | :--- |
| **`INITUSDT`** | +1.42% | **+82.66%** | **+84.41%** | `$6.33M` (#151) | `-2.39%` / `8.42%` | Blocked at Stage 0 (`max_symbols=48` cap) | Admitted in **320-symbol Core/Discovery pool** + passes **336h VCR & Quiet-Coil L5** |
| **`ONUSDT`** | **+54.00%** | -34.42% | **+84.73%** | `$5.05M` (#171) | `-4.40%` / `12.12%` | Blocked at Stage 0 (`max_symbols=48` cap) | Admitted in **320-symbol pool**; rapid vertical spike governed by trailing risk brackets |
| **`PIEVERSEUSDT`** | **+22.26%** | **+41.13%** | **+49.09%** | `$8.01M` (#136) | `+7.99%` / `10.44%` | Blocked at Stage 0 (`max_symbols=48` cap) | Admitted in **320-symbol pool** + **VVI Ignition** (`6h RS vs BTC` + `6h vol accel`) |
| **`ARIAUSDT`** | +5.51% | **+25.98%** | **+35.21%** | `$24.38M` (#73) | `+2.64%` / `11.31%` | Blocked at Stage 0 (`max_symbols=48` cap) | Admitted in **320-symbol Core pool** + **Track-B L1–L5 Stage-2 promotion** |
| **`TSTUSDT`** | **+27.05%** | +6.55% | **+28.68%** | `$832k` (#481) | `-2.68%` / `4.48%` | Excluded by `$1.5M` Discovery floor | Covered by **20-slot Sub-`$1.5M` Watch Reserve** (`$500k–$1.5M`) + **VVI Ignition** |
| **`EDGEUSDT`** | **+15.66%** | **+19.35%** | **+32.54%** | `$6.80M` (#146) | `+6.59%` / `10.08%` | Blocked at Stage 0 (`max_symbols=48` cap) | Admitted in **320-symbol pool** + **Early Stealth Bid / Criterion #5 Upper-Range Coil** |
| **`B2USDT`** | **+14.60%** | **+13.65%** | **+16.80%** | `$3.04M` (#238) | `+0.05%` / `5.06%` | Blocked at Stage 0 (`max_symbols=48` cap) | Admitted via **Discovery Anomaly Quiet Tight Range** (`rng <= 6%`, `|chg| <= 4%`) |
| **`SOPHUSDT`** | -4.24% | **+13.58%** | **+15.20%** | `$5.73M` (#163) | `-1.82%` / `9.57%` | Blocked at Stage 0 (`max_symbols=48` cap) | Admitted in **320-symbol Core pool** |
| **`MERLUSDT`** | **+11.72%** | **+10.61%** | **+14.10%** | `$1.41M` (#375) | `+1.11%` / `4.86%` | Excluded by `$1.5M` Discovery floor | Admitted by lowered **`$1.2M` Discovery Anomaly floor** (`403393d`) |
| **`CAPUSDT`** | +1.03% | **+11.52%** | **+13.40%** | `$32.31M` (#62) | `-9.34%` / `14.05%` | Blocked at Stage 0 (`48` cap) & Stage-1 `OI_ACCUMULATION` chicken-and-egg bug | Admitted in **320-symbol Core pool** + **Stealth Coil Fallback buyer-absorption fix** (`86.6`, `5/5`) |
| **`NIGHTUSDT`** | +4.12% | **+6.39%** | **+10.70%** | `$18.40M` (#94) | `+1.20%` / `6.10%` | Reached Stage 2 (`7d OI +4.36%`, `taker 1.367`), vetoed by `RVOL 1.1923 < 1.20` | Passes **4H closed-bar RVOL / Quiet-Coil L5** (`84.7`, `5/5`) |

---

### 2.2 The Two-Phase Pre-Gainer Signature (`T-24h` to `T-1h`)
Across all 55 top-10 entrants (vs. 64 controls), empirical feature reconstruction across 53,332 1H bars and 27,068 4H bars proved that future top gainers exhibit two distinct, measurable phases before entering the top-10 gainer leaderboard:

1. **Phase A — Multi-Day Structural Compression & Stealth Absorption (`T-24h` to `T-6h`):**
   - True 14-day (`336h`) volume contraction (`vcr_4h = mean_168h / mean_336h <= 0.85–0.95`) combined with 4H Bollinger Bands coiling inside Keltner Channels (`bb_inside_kc_4h = True`, `bb_bandwidth_4h <= 0.14`).
   - Price holds above at least 2 of 3 structural moving averages (`1H EMA20`, `4H EMA20`, `4H EMA50`) and 30-day POC, while 7d/4h Open Interest (`oi_delta_7d >= -3.0%`, `oi_delta_4h >= 0.0%`) or 4H taker buy/sell flow (`>= 1.01–1.04`) shows smart-money absorption without price extension (`pos_in_range_30d <= 0.72`).
2. **Phase B — Pre-Breakout Velocity Inflection (`T-6h` to `T-1h` before Top-10 entry):**
   - At `T-6h` to `T-1h` before entering the Top-10 Gainers list, entrants separated sharply from non-entrant controls on two fast-moving features while their 24h return was still modest (`<= +9%`):
     - **6-Hour BTC-Relative Strength (`rs_6h_vs_btc`):** Median **+4.04%** for entrants vs. **-0.27%** for controls at `T-1h` (**+1.58%** vs. **-0.22%** at `T-6h`).
     - **6-Hour Volume Acceleration (`vol_accel_6h = mean_6h / mean_prev_18h`):** Median **1.44x** for entrants vs. **0.85x** for controls at `T-1h`.

---

## 3. Exact Screener Changes Implemented to Capture Upcoming 24h/48h Gainers

The table below maps every stage of `CryptoOpportunityScreener` from Legacy v1 to the upgraded `discovery_v2` + `radar-v2` production engine:

| Pipeline Stage | Legacy v1 Behavior (During Nov 8–10 Study) | Upgraded `discovery_v2` + `radar-v2` Behavior (Now Live) | Measured Impact on Capturing Future Gainers |
| :--- | :--- | :--- | :--- |
| **Stage 0: Universe Ceiling** | `max_symbols = 48` (`38` Core + `10` Discovery). Discarded 414 liquid pairs before fetching a single candle. | `screener_stage1_candidate_budget = 320` (`256` Core + `64` Discovery Anomaly + `20` Watch Reserve). | Future top-10 gainer recall at Stage 0 jumped from **10/55 (`18.2%`)** to **38/55 (`69.1%`)** + 20 rotating sub-`$1.5M` watch slots. |
| **Stage 0: Discovery Anomaly Floor & Ranking** | Hard `$1.5M` cutoff; `compression_ratio` (`quote_vol / rng`) capped at `35.0`, causing ~100 symbols to tie at `35.0` and sort by raw volume. | Lowered floor to **`$1.2M`** (`403393d`) + added **Criterion #5 (Upper-Range Coiled Accumulation: `+25.0 * close_pos_24h` for tight ranges closing in top 30% of 24h range)** + **20-slot `$500k–$1.5M` Watch Reserve**. | Captures `$1.2M–$1.5M` coils (`MERLUSDT`, `CROSSUSDT`), sub-`$1.5M` sleepers (`TSTUSDT`), and ranks upper-range coils above red drifting tokens. |
| **Stage 1: 1H History & Contraction (`vcr_4h`)** | Fetched only `200` 1H bars (`8.3d`), truncating the `336h` (14d) contraction denominator to just `32` hours (`200 - 168`). | Fetches **`360` 1H bars (`15d`)** (`screener_1h_bars = 360`) so `compute_contraction_ratio` computes exact `168h / 336h` (7d vs. 14d) volume contraction. | Restores true multi-week volume dry-up detection (`L1`) across all 320 symbols. |
| **Stage 1 & L5: Relative Volume (`rvol_4h`)** | Computed `rvol_4h` on the 1H timeframe (single 1H bar / 20h mean) and required `rvol_4h >= 1.15` even for dormant coils. | Computes `rvol_4h` on **closed 4H bars** (latest closed 4H bar / inclusive 20-bar 4H mean) AND allows **Quiet-Coil Supply Exhaustion (`bb_bandwidth_4h <= 0.14` and `vcr_4h <= 0.95`)** or **VVI Ignition** to pass `L5`. | `L5` pass rate on future top-10 gainers increased from **23.6% (`13/55`)** to **67.3% (`37/55`)**; prevents false vetoes like `NIGHTUSDT` (`RVOL 1.192`). |
| **Stage 1: Pre-Breakout Velocity (`VVI Ignition`)** | No sub-24h velocity or BTC-relative strength feature existed in Stage 1. | Computes `vol_accel_6h` (`6h / 18h` turnover ratio) and `rs_6h_vs_btc` (`6h` return vs. BTC). Triggers **`vvi_ignition`** when `vol_accel_6h >= 1.35x`, `rs_6h_vs_btc >= +1.5%`, and `0% <= ret_24h <= +9.0%`. | Captures `T-6h` to `T-1h` inflection setups (`PIEVERSEUSDT`, `EDGEUSDT`, `INITUSDT`) before they reach `+10%+` on the 24h ticker. |
| **Stage 1 -> Stage 2 Shortlist Promotion** | Single-track top-75 shortlist by raw `stage1_score`, allowing high-RVOL noisy tokens to crowd out quiet `L1–L5` coils before Stage-2 OI/taker fetch. | **Dual-Track Stage-2 Promotion (`75` slots):**<br>• **Track A (`45` slots):** Top `stage1_score >= 38.0`<br>• **Track B (`30` slots):** Any setup passing structural `L1–L5` (`stage1_l1_l5_ready`) or `vvi_ignition`. | Structural `L1–L5` readiness among future gainers rose from **0/55** (legacy) to **16/55 (`29.1%`)**, with 100% guaranteed promotion to Stage-2 derivatives enrichment. |
| **Stage 1 & 2: Setup Archetypes (`archetypes.py`)** | `VCP_COMPRESSION_LONG` required `pos_in_range > 0.60`; `Stealth Coil Fallback` rejected expanding volume (`rvol_4h > 1.15`); `OI_ACCUMULATION_LONG` was impossible in Stage 1 (`oi_delta_7d = 0`). | 1. Tight `bb_inside_kc_4h` squeezes (`bw <= 0.12`) above POC & EMA50 qualify for `VCP_COMPRESSION_LONG` down to `pos_in_range >= 0.45` (`FOLKSUSDT`).<br>2. `Stealth Coil Fallback` accepts either supply exhaustion (`rvol_4h <= 1.15`) OR buyer absorption (`taker_4h >= 1.02`, `CAPUSDT`).<br>3. `vvi_ignition` qualifies in Stage 1 (`pos_in_range <= 0.78`). | Eliminates Stage-1 archetype blindspots so `OI_ACCUMULATION_LONG` and mid-range Keltner squeezes reliably reach Stage 2. |
| **Stage 2: L6 Derivatives & Checklist Parity** | Required `oi_delta_24h >= +2%` or `taker_4h >= 1.04`; `is_deriv_flow_ok` hardcoded `oi_change_7d >= 0.0` even when `discovery_v2` L6 passed. | `L6` accepts `oi_delta_7d >= -3.0%` when paired with positive 4H OI (`>= 0%`) or taker flow (`>= 1.01`), and `is_deriv_flow_ok` ("Smart Money Absorption" badge) is strictly aligned with `L6` (`403393d`). | Prevents `0.1`-point conviction cliff rejections (`FOLKSUSDT` `70.9 -> 78.8`) and ensures `5/5` checklist consistency. |
| **Post-Scan: Candidate Lifecycle (`radar-v2`)** | Retained candidates stayed on the panel even after dropping to `4/5` or `3/5` gates (`MEGAUSDT`, `1000PEPEUSDT`, `INJUSDT`) or aging past 4 days. | Re-verifies all 5 checklist gates every scan cycle: **immediately evicts any candidate with `<5/5` gates (`radar_gates_degraded`)** or **`age_hours > 96.0` (`max_trade_horizon_96h_exceeded`)**. | Guarantees that 100% of candidates displayed on the live panel are active, non-expired `5/5` setups. |

---

## 4. Preparation for Upcoming 24h / 48h Prospective Research Studies

To guarantee that the next 24h and 48h prospective studies produce zero-gap, cryptographically verifiable evidence comparing Legacy Control vs. `discovery-v2-full336-rvol4h-watch20`, the following research infrastructure was built and verified:

### 4.1 Process & Lock Isolation (`Sampler` vs. `Reporter`)
- **Files:**
  - [`src/algotrader/application/research_runner.py`](file:///Users/jithinjacob/Desktop/trdindicator/src/algotrader/application/research_runner.py)
  - [`src/algotrader/cli/commands/research.py`](file:///Users/jithinjacob/Desktop/trdindicator/src/algotrader/cli/commands/research.py)
- **Why this was needed:** In the Nov 8–10 study, synchronous 24h report generation inside the sampler caused Slot 289 (at the 24h mark) to miss its 5-minute deadline.
- **How it works now:**
  - **Sampler Worker (`research-sampler`):** Holds `.worker.lock` only and performs lightweight 5-minute snapshot acquisition (`512 MiB / 0.5 CPU`). Never blocks on report generation.
  - **Reporter Worker (`research-reports`):** Holds `.report.lock` only (`256 MiB / 0.25 CPU`, network disabled), reads immutable snapshots, and publishes 24h/48h outcome reports atomically and idempotently.
  - **Strict Slot Accounting:** Distinguishes `observed`, `missed`, `failed`, and `absent` 5-minute slots (`unobserved_slots`). Any gap automatically marks the study report as `degraded`.

### 4.2 Full-Pipeline Decision Journal & Compaction (`ScanDecisionJournal`)
- **Files:**
  - [`src/algotrader/domain/research_models.py`](file:///Users/jithinjacob/Desktop/trdindicator/src/algotrader/domain/research_models.py)
  - [`src/algotrader/application/research_discovery.py`](file:///Users/jithinjacob/Desktop/trdindicator/src/algotrader/application/research_discovery.py)
- Records every symbol's exact trajectory across Stage 0 (`selection`), Stage 1 (`stage1`), Stage 2 (`accumulation`), Risk/Scoring (`scoring`), and Curation (`publication`), including raw L1–L6 inputs, archetype inputs, `vol_accel_6h`, `rs_6h_vs_btc`, and rejection reasons.
- **Hot Definition vs. Companion Split:** Compacts the hot study definition to `~7.9 MiB` (well below the `16 MiB` read guard) while binding the full 320-symbol diagnostic payload (`up to 128 MiB`) via SHA256 digest in `comparison_companion.json`.
- **Separate Prospective Outcome Views:** The 24h/48h report generator now outputs dedicated prospective outcome benchmarks for:
  1. Published `5/5` Radar Candidates
  2. `L1–L5` Structurally Ready Coils (`stage1_l1_l5_ready`) vs. `L6`-Activated Setups
  3. Exploratory `Emerging` Velocity Inflections (`rs_6h_vs_btc > 0`, `vol_accel_6h > 1.0x`, `ret_24h <= 9%`)
  4. The 20-slot Sub-`$1.5M` Watch Reserve (`$500k–$1.5M`)

### 4.3 Temporal Transition Tracking (`prepare --previous-study PATH`)
- Binds a prior study definition (`<= 8 hours` old, matching source/runtime/policy fingerprint) to measure prospective outcomes of **state transitions across consecutive 4H scans**:
  - `emerging_false_to_true` (symbols transitioning into VVI / BTC-relative acceleration between Scan $t-1$ and Scan $t$)
  - `readiness_to_activated` (symbols that were `L1–L5` structurally ready in Scan $t-1$ and triggered `L6` derivatives absorption in Scan $t$).

---

## 5. Complete Catalog of Vulnerabilities Discovered & How They Were Fixed

| ID | Vulnerability / Discrepancy | Root Cause & Exact Location | Fix Implemented & Commit |
| :--- | :--- | :--- | :--- |
| **V1** | **Frontend showed "No Active Pre-Breakout Setups Qualified" while Backend API had candidates** | 1. During active scan or container restart, `GET /api/screener/live` returned `opportunities: []` with `scan_in_progress: true`, which [`frontend/app/page.tsx`](file:///Users/jithinjacob/Desktop/trdindicator/frontend/app/page.tsx#L423-L465) rendered as the empty state.<br>2. On `alfa-terminal`, `/dev/sda1` hit 100% disk full (`2.6 GB` in `data/research/scans` + `1.1 GB` in GCP/syslog), leaving a truncated 3-byte `atlas_state.sqlite3-shm` sidecar that crashed `/api/watchlist` with `sqlite3.OperationalError: disk I/O error`. | 1. Reclaimed `1.7 GB` on `alfa-terminal`, added log/scan pruning to [`.github/workflows/deploy.yml`](file:///Users/jithinjacob/Desktop/trdindicator/.github/workflows/deploy.yml#L86-L130).<br>2. Added automatic recovery for truncated `<32KB` `-shm`/`-wal` sidecars in [`WatchlistService._ensure_table`](file:///Users/jithinjacob/Desktop/trdindicator/src/algotrader/application/watchlist_service.py#L50-L89).<br>3. Updated [`page.tsx`](file:///Users/jithinjacob/Desktop/trdindicator/frontend/app/page.tsx#L423-L465) to preserve cached candidates and show a scanning state while `scan_in_progress` is true.<br>*(Commit `3622830`)* |
| **V2** | **Degraded setups (`MEGAUSDT` `4/5`, `1000PEPEUSDT` `3/5`, `INJUSDT` `3/5`) remained visible in Candidates panel** | `radar-v2` correctly detected gate failures during 4H re-verification and decremented their gate badge to `4/5` or `3/5`, but [`portal.py`](file:///Users/jithinjacob/Desktop/trdindicator/src/algotrader/api/portal.py#L288-L330) still retained `ACTIVE_COIL` / `DEGRADING` setups with `failed_gates != []` in the live `opportunities` array. | Updated [`_do_crypto_screen` in `portal.py`](file:///Users/jithinjacob/Desktop/trdindicator/src/algotrader/api/portal.py#L288-L330) so any retained candidate with `len(failed_gates) > 0` (`<5/5` checklist gates) is immediately evicted (`reason="radar_gates_degraded"`). Also updated `_screener_cron_loop` to trigger an immediate scan on startup if the disk cache is stale (`>4h`) or pre-`radar-v2`.<br>*(Commit `83c8cc9`)* |
| **V3** | **`CAPUSDT` (`86.6`, `5/5`) dropped from Stage 1 despite `$70.3M` volume, Keltner squeeze, and `+18.3%` 7d OI** | **Stage-1 / Stage-2 Chicken-and-Egg Bug:** In Stage 1 (`enriched=False`), `oi_delta_7d` is `0` (not fetched until Stage 2), so no token can match `OI_ACCUMULATION_LONG` in Stage 1. When `CAPUSDT`'s `macd_hist_4h` ticked by `-0.00006`, it fell through to `Stealth Coil Fallback` ([`archetypes.py:269`](file:///Users/jithinjacob/Desktop/trdindicator/src/algotrader/domain/archetypes.py#L269)), which rejected it because `rvol_4h` was **expanding (`1.38x > 1.15x`) with positive buyer absorption (`taker_4h = 1.055`)**! | Updated `Stealth Coil Fallback` in [`classify_setup_archetype`](file:///Users/jithinjacob/Desktop/trdindicator/src/algotrader/domain/archetypes.py#L269-L295) so a tight `bb_inside_kc_4h` squeeze above `4H EMA50` and `30d POC` qualifies when either supply exhaustion (`rvol_4h <= 1.15`) **or** active buyer absorption (`taker_4h >= 1.02`) is present.<br>*(Commit `403393d`)* |
| **V4** | **`FOLKSUSDT` (`78.8`, `5/5`) dropped due to `pos_in_range_30d = 0.58` dead zone and Stage-2 L6 checklist contradiction** | 1. In Stage 1, `FOLKSUSDT` (`bb_bandwidth_4h = 0.0868`, `bb_inside_kc_4h = True`, above all EMAs and POC) had `pos_in_range_30d = 0.5804`—missing `VCP_COMPRESSION_LONG` (`>0.60`) by `0.02` and `RANGE_BOTTOM` (`taker_4h >= 0.95`) during a quiet `0.79x` RVOL coil.<br>2. In Stage 2, it passed `L1–L6` (`oi_delta_7d = -2.01% >= -3.0%` tolerance), **but** [`is_deriv_flow_ok` at `crypto_screener.py:1516`](file:///Users/jithinjacob/Desktop/trdindicator/src/algotrader/application/crypto_screener.py#L1515-L1538) still checked legacy `oi_change_7d >= 0.0`, docking `-3` conviction points (`70.9` vs `71.0` floor). | 1. Allowed tight `bb_inside_kc_4h` squeezes (`bb_bandwidth_4h <= 0.12`) above `30d POC` and `4H EMA50` with `pos_in_range_30d >= 0.45` to classify as `VCP_COMPRESSION_LONG` in [`archetypes.py:240–255`](file:///Users/jithinjacob/Desktop/trdindicator/src/algotrader/domain/archetypes.py#L240-L255).<br>2. Aligned [`is_deriv_flow_ok`](file:///Users/jithinjacob/Desktop/trdindicator/src/algotrader/application/crypto_screener.py#L1515-L1525) and [`volume_microstructure`](file:///Users/jithinjacob/Desktop/trdindicator/src/algotrader/application/crypto_screener.py#L1528-L1538) with `gate_details["L6"]` and `bb_inside_kc` squeeze structure.<br>*(Commit `403393d`)* |
| **V5** | **`CROSSUSDT` (`83.1`, `5/5`) missed Stage 0 when 24h volume dipped `$28k` to `$1.47M` & `compression_ratio` tied at `35.0`** | 1. `CROSSUSDT` 24h volume dipped from `$1.50M` to `$1.471M`, falling just below the `$1.5M` Discovery Anomaly floor.<br>2. Criterion #1 (`compression_ratio = min(35.0, (quote_vol / 1M) / max(rng, 0.01))`) saturated at `35.0` for almost every `$2.5M–$6M` symbol, creating a ~100-way tie sorted purely by volume rank. | Lowered `discovery_v2` anomaly floor from `$1.5M` to `$1.2M` and added Criterion #5 (`Upper-Range Coiled Accumulation`: `+25.0 * close_pos_24h` when `rng <= 0.080`, `0% <= chg <= 8.5%`, and `close_pos_24h >= 0.70`) in [`crypto_screener.py:328–371`](file:///Users/jithinjacob/Desktop/trdindicator/src/algotrader/application/crypto_screener.py#L328-L371).<br>*(Commit `403393d`)* |

---

## 6. Quick Reference Map for Coding Agents

When inspecting, debugging, or extending the screener or 24h/48h research pipeline, use these canonical entrypoints:

### Core Screener & Lifecycle Files
- **Universe Selection, Stage-1 Filter, 6-Layer Gate (`L1–L6`), Conviction & Checklist:** [`src/algotrader/application/crypto_screener.py`](file:///Users/jithinjacob/Desktop/trdindicator/src/algotrader/application/crypto_screener.py)
- **Setup Archetype Classifier (`VCP_COMPRESSION_LONG`, `RANGE_BOTTOM_ACCUMULATION_LONG`, `OI_ACCUMULATION_LONG`, `VVI`):** [`src/algotrader/domain/archetypes.py`](file:///Users/jithinjacob/Desktop/trdindicator/src/algotrader/domain/archetypes.py)
- **Stateful Candidate Lifecycle (`radar-v2` Re-Verification & Eviction Rules):** [`src/algotrader/application/radar_lifecycle.py`](file:///Users/jithinjacob/Desktop/trdindicator/src/algotrader/application/radar_lifecycle.py) and [`src/algotrader/api/portal.py`](file:///Users/jithinjacob/Desktop/trdindicator/src/algotrader/api/portal.py#L245-L360)
- **Runtime Settings & `discovery_v2` Thresholds:** [`src/algotrader/config/settings.py`](file:///Users/jithinjacob/Desktop/trdindicator/src/algotrader/config/settings.py)
- **Frontend Candidate Ordering & Card Telemetry:** [`frontend/app/page.tsx`](file:///Users/jithinjacob/Desktop/trdindicator/frontend/app/page.tsx) and [`frontend/app/components/CandidateSidebar.tsx`](file:///Users/jithinjacob/Desktop/trdindicator/frontend/app/components/CandidateSidebar.tsx)

### 24h / 48h Research Study & Audit Files
- **Discovery Challenger & Companion Journal:** [`src/algotrader/application/research_discovery.py`](file:///Users/jithinjacob/Desktop/trdindicator/src/algotrader/application/research_discovery.py)
- **Prospective Sampler & Offline Reporter:** [`src/algotrader/application/research_runner.py`](file:///Users/jithinjacob/Desktop/trdindicator/src/algotrader/application/research_runner.py)
- **Historical Pre-Gainer Study Evidence (`Nov 8–10`):** [`docs/security/2026-11-11-pre-gainer-deep-research.md`](file:///Users/jithinjacob/Desktop/trdindicator/docs/security/2026-11-11-pre-gainer-deep-research.md)
- **Discovery Challenger Specification:** [`docs/security/2026-11-11-discovery-upgrade.md`](file:///Users/jithinjacob/Desktop/trdindicator/docs/security/2026-11-11-discovery-upgrade.md)

### Verification Commands (Must Pass Before Any Deployment)
```bash
uv run ruff check src
uv run mypy
uv run pytest tests/test_radar_lifecycle.py tests/test_research_discovery.py tests/test_live_screener_no_cache.py -q
```
