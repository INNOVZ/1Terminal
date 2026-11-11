# Atlas Screener: Post-Observation Engineering, Vulnerability Forensics & 24h/48h Research Blueprint

> **Document Classification:** Canonical High-Order Engineering & Research Reference (Human + Coding Agent Accessible)  
> **Baseline Observation Study:** `71346822-f686-4bb1-99ec-0f56ef8f0ea1` (Nov 8, 2026 13:50 UTC – Nov 10, 2026 13:50 UTC)  
> **Active Strategy & Lifecycle Versions:** `screener_discovery_mode="discovery_v2"`, `radar_lifecycle_version="radar-v2"`, Research Challenger Policy `discovery-v2-full336-rvol4h-watch20`  
> **Head Revision at Publication:** `403393d` (`main`)

---

## 1. Executive Summary & Context for Coding Agents

Following the completion of the 48-hour prospective rank-observation study (`71346822-f686-4bb1-99ec-0f56ef8f0ea1`, 512 frozen USD-M perpetual contracts, 55 subsequent top-10 daily gainer entrants), a deep forensic reconstruction (`docs/security/2026-11-11-pre-gainer-deep-research.md`) revealed why the legacy v1 screener missed the majority of upcoming 24h/48h top gainers and where live operational discrepancies occurred.

Between commits `4b5174b` and `403393d`, we re-architected both:
1. **The Live Production Screener (`CryptoOpportunityScreener` + `radar-v2` in `portal.py`)** to surface only verified **5/5 gate pre-breakout accumulation coils** and early **Volume-Velocity Ignition (VVI)** setups while immediately evicting degraded (`<5/5`) or expired (`>96h`) setups.
2. **The Prospective 24h/48h Research & Collection Harness (`DiscoveryScreener` + isolated Sampler/Reporter workers)** to run controlled, zero-gap paired 24h/48h studies with full Stage-1/Stage-2 decision journals and temporal state-transition tracking (`--previous-study`).

---

## 2. Empirical Findings from the 48h Observation Collection (`Nov 8–10`)

Reconstruction of 53,332 1H bars, 27,068 4H bars, and 53,332 1H Open Interest snapshots across the 55 top-10 entrants vs. 64 matched controls established four structural bottlenecks in the legacy v1 pipeline:

| Bottleneck in Legacy v1 | Empirical Evidence from 48h Study | Implemented Architectural Upgrade |
| :--- | :--- | :--- |
| **1. Universe Budget Truncation (`48` symbols)** | Only **10 / 55** (`18.2%`) future top-10 gainers were even evaluated in Stage 1 under the 48-symbol cap. | Expanded `screener_stage1_Candidate_budget` to **320 symbols** (`260` Core + `60` Discovery Anomaly + `20` Sub-$1.5M Watch Reserve), capturing **38 / 55** (`69.1%`) at `$1.5M` and covering the remaining sub-`$1.5M` sleepers via deterministic 4h hash rotation. |
| **2. Truncated `vcr_4h` Window (`200` 1H bars = 8.3d)** | `compute_contraction_ratio` requested 336 hours (14d) vs. 168 hours (7d), but Stage 1 only fetched `200` 1H bars—collapsing the baseline from 14 days to 32 hours. | Increased Stage-1 1H kline request (`screener_1h_bars`) from `200` to **360 bars (15 days)** so 7d-vs-14d contraction (`168h / 336h`) is mathematically exact. |
| **3. Noisy 1H RVOL (`rvol_4h` computed from 1H bar)** | Legacy `rvol_4h` divided a single 1H bar by a 20-hour mean (`23.6%` pass rate on coiled future gainers during quiet hours). | Replaced with true **4H closed-bar RVOL** (latest closed 4H bar volume divided by inclusive 20-bar 4H mean) + quiet-coil supply-exhaustion path (`bb_bandwidth_4h <= 0.14` and `vcr_4h <= 0.95`). |
| **4. Pre-Breakout Velocity Blindspot (T-6h to T-1h)** | Future top gainers separated strongly from controls at `T-6h` to `T-1h` via **6h BTC-relative strength** (`+4.04%` vs `-0.27%`) and **6h volume acceleration** (`1.44x` vs `0.85x`). | Added **Volume-Velocity Ignition (VVI)** (`6h/18h accel >= 1.35x`, `rs_6h_vs_btc >= +1.5%`, `0% <= ret_24h <= +9.0%`) to Stage-1 archetypes, Stage-2 Track-B promotion, and conviction scoring. |

---

## 3. Live Screener Upgrades Implemented (`discovery_v2` & `radar-v2`)

### 3.1 Stage-0 Universe Selection (`CryptoOpportunityScreener._select_universe`)
- **File:** [`src/algotrader/application/crypto_screener.py`](file:///Users/jithinjacob/Desktop/trdindicator/src/algotrader/application/crypto_screener.py#L280-L435)
- **320-Symbol Budget Split:**
  - **Core Liquidity Pool (80% / up to 256 symbols):** Contracts with `quote_vol >= $5M` and `spread <= 15 bps`, ranked by liquidity.
  - **Discovery Anomaly Pool (20% / up to 64 symbols):** Contracts with `quote_vol >= $1.2M` (lowered from `$1.5M` in `403393d` to eliminate the `$1.45M–$1.50M` volume cliff) and `spread <= 25 bps`. Scored across 5 anomaly criteria:
    1. Volume-to-Range Compression Ratio (`quote_vol / max(rng, 0.01)`, capped at `35.0`)
    2. Quiet Tight Range (`rng <= 0.06` and `|chg_24h| <= 4.0%`, `+25.0`)
    3. Early Stealth Bid (`+1.5% <= chg_24h <= +8.0%` and `rng <= 0.10`, `+20.0`)
    4. Mid-Cap Sweet Spot (`$1.5M <= quote_vol <= $25M`, `+15.0`)
    5. **Criterion #5 (`403393d`) — Upper-Range Coiled Accumulation:** Tight 24h range (`rng <= 0.080`), non-negative 24h change (`0.0% <= chg_24h <= +8.5%`), and closing in the top 30% of the 24h range (`close_pos_24h >= 0.70`), adding `25.0 * close_pos_24h` to break `35.0` compression-ratio ties in favor of upper-range coils like `CROSSUSDT`.
  - **Sub-`$1.5M` Watch Reserve (up to 20 slots):** Deterministic 4-hour bucket SHA256 rotation (`discovery-v2-watch20|<4h_bucket>|<symbol>`) for `$500k–$1.5M` sleepers (`spread <= 25 bps`).

### 3.2 Stage-1 Feature Engineering & Dual-Track Promotion (`_stage1_filter`)
- **Files:**
  - [`src/algotrader/application/crypto_screener.py`](file:///Users/jithinjacob/Desktop/trdindicator/src/algotrader/application/crypto_screener.py#L440-L830)
  - [`src/algotrader/domain/archetypes.py`](file:///Users/jithinjacob/Desktop/trdindicator/src/algotrader/domain/archetypes.py#L220-L296)
- **360-Bar 1H + 4H Feature Set:**
  - Computes true `336h` volume contraction ratio (`vcr_4h`), true 4H closed-bar `rvol_4h`, `vol_accel_6h` (`mean(vol_1h[-6:]) / mean(vol_1h[-24:-6])`), and `rs_6h_vs_btc` (`ret_6h_symbol - ret_6h_btc`).
- **Dual-Track Stage-1 -> Stage-2 Shortlist (`screener_stage2_shortlist_size = 75`):**
  - **Track A (`45` slots):** Ranked by Stage-1 structural pre-score (`stage1_score >= 38.0`, requiring an accumulation archetype).
  - **Track B (`30` slots):** Under `discovery_v2`, promotes any symbol that passes structural gates `L1–L5` (`stage1_l1_l5_ready`) **or** triggers `vvi_ignition`, sorted by `(stage1_l1_l5_ready, vvi_ignition, vol_accel_6h, rs_6h_vs_btc)` so quiet accumulation coils are never crowded out of Stage-2 derivatives enrichment before `L6` is checked.

### 3.3 Six-Layer Institutional Accumulation Gate (`evaluate_accumulation_gate`)
- **File:** [`src/algotrader/application/crypto_screener.py`](file:///Users/jithinjacob/Desktop/trdindicator/src/algotrader/application/crypto_screener.py#L845-L965)
- **L1 (Volatility Contraction):** `bb_bandwidth_4h < 0.20` OR `vcr_4h < 0.85` OR (`discovery_v2`: `bb_inside_kc_4h and bb_bandwidth_4h <= 0.24`).
- **L2 (Range Position):** `pos_in_range_30d <= 0.72` (not extended at 30-day highs).
- **L3 (Multi-Timeframe Trend Alignment):** At least 2 of 3 (`close >= ema20_1h`, `close >= ema20_4h`, `close >= ema50_4h`).
- **L4 (Momentum Turning Up):** `macd_hist_4h > macd_hist_prev_4h` OR (`rsi_4h >= 50` and `close > open`).
- **L5 (Volume Confirmation / Quiet Coil):** `rvol_4h >= 1.15` OR (`discovery_v2`: `bb_bandwidth_4h <= 0.14 and vcr_4h <= 0.95` quiet coil, or `vvi_ignition`).
- **L6 (Derivatives / Orderflow Confirmation):** `oi_delta_24h >= +2.0%` OR `taker_buy_sell_ratio_4h >= 1.04` OR (`discovery_v2`: `oi_delta_7d >= -3.0%` and (`oi_delta_4h >= 0.0%` or `taker_4h >= 1.01`)).

### 3.4 Stateful Candidate Lifecycle Engine (`radar-v2`)
- **Files:**
  - [`src/algotrader/application/radar_lifecycle.py`](file:///Users/jithinjacob/Desktop/trdindicator/src/algotrader/application/radar_lifecycle.py#L1-L260)
  - [`src/algotrader/api/portal.py`](file:///Users/jithinjacob/Desktop/trdindicator/src/algotrader/api/portal.py#L285-L345)
- Every candidate on the panel is re-audited on every scan cycle against live 1H/4H candles and live 5-check checklist gates:
  - **Strict `5/5` Gate Retention Rule (`83c8cc9`):** Any previously discovered candidate whose live `failed_gates` becomes non-empty (`<5/5` gates) is **immediately evicted** (`reason="radar_gates_degraded"`) rather than lingering on the live Candidates panel.
  - **96-Hour Maximum Coiling Horizon (`403393d`):** Any retained setup older than `96.0h` (4 days) without breaking out is graduated (`reason="max_trade_horizon_96h_exceeded"`).
  - **Structural Stop & Expansion Graduation:** Candidates breaking below `stop_loss` (`STRUCTURE_BROKEN`) or exceeding `+12%` above entry (`EXPANSION_COMPLETE`) are automatically evicted and logged to `data/radar_events/events.jsonl`.

---

## 4. Preparation for Upcoming 24h / 48h Prospective Research Studies

To guarantee that the next 24h and 48h prospective studies produce zero-gap, cryptographically verifiable evidence comparing Legacy Control vs. `discovery-v2-full336-rvol4h-watch20`, the following research infrastructure was built and verified:

### 4.1 Process & Lock Isolation (`Sampler` vs. `Reporter`)
- **Files:**
  - [`src/algotrader/application/research_runner.py`](file:///Users/jithinjacob/Desktop/trdindicator/src/algotrader/application/research_runner.py)
  - [`src/algotrader/cli/commands/research.py`](file:///Users/jithinjacob/Desktop/trdindicator/src/algotrader/cli/commands/research.py)
- **Sampler Worker (`research-sampler`):** Holds `.worker.lock` only and performs lightweight 5-minute snapshot acquisition (`512 MiB / 0.5 CPU`). Never blocks on report generation.
- **Reporter Worker (`research-reports`):** Holds `.report.lock` only (`256 MiB / 0.25 CPU`, network disabled), reads immutable snapshots, and publishes 24h/48h outcome reports atomically and idempotently.
- **Strict Slot Accounting:** Distinguishes `observed`, `missed`, `failed`, and `absent` 5-minute slots (`unobserved_slots`). Any gap automatically marks the study report as `degraded`.

### 4.2 Full-Pipeline Decision Journal & Compaction (`ScanDecisionJournal`)
- **Files:**
  - [`src/algotrader/domain/research_models.py`](file:///Users/jithinjacob/Desktop/trdindicator/src/algotrader/domain/research_models.py)
  - [`src/algotrader/application/research_discovery.py`](file:///Users/jithinjacob/Desktop/trdindicator/src/algotrader/application/research_discovery.py)
- Records every symbol's exact trajectory across Stage 0 (`selection`), Stage 1 (`stage1`), Stage 2 (`accumulation`), Risk/Scoring (`scoring`), and Curation (`publication`), including raw L1–L6 inputs, archetype inputs, and rejection reasons.
- **Hot Definition vs. Companion Split:** Compacts the hot study definition to `~7.9 MiB` (well below the `16 MiB` read guard) while binding the full 320-symbol diagnostic payload (`up to 128 MiB`) via SHA256 digest in `comparison_companion.json`.

### 4.3 Temporal Transition Tracking (`prepare --previous-study PATH`)
- Binds a prior study definition (`<= 8 hours` old, matching source/runtime/policy fingerprint) to measure prospective outcomes of **state transitions across consecutive 4H scans**:
  - `emerging_false_to_true` (symbols transitioning into VVI / BTC-relative acceleration)
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
