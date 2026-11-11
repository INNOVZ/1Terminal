# AGENTS.md — Coding Agent Reference for Atlas Screener (`trdindicator`)

## 1. Mandatory First Read for Screener & Research Tasks
Before modifying the screener pipeline, setup archetypes, radar lifecycle, or 24h/48h prospective research runner, read:
- **[`docs/screener-post-observation-and-48h-research-blueprint.md`](docs/screener-post-observation-and-48h-research-blueprint.md)** — Complete post-observation architecture, `discovery_v2` & `radar-v2` specifications, 24h/48h research study preparation, and the forensic catalog of vulnerabilities (`V1–V5`) and fixes.
- **[`docs/security/2026-11-11-pre-gainer-deep-research.md`](docs/security/2026-11-11-pre-gainer-deep-research.md)** — Empirical study of the Nov 8–10 48h observation cohort (`71346822-f686-4bb1-99ec-0f56ef8f0ea1`).
- **[`docs/security/2026-11-11-discovery-upgrade.md`](docs/security/2026-11-11-discovery-upgrade.md)** — `discovery-v2-full336-rvol4h-watch20` challenger specification and sampler/reporter lock isolation.

- **[`docs/security/2026-11-11-discovery-corrections.md`](docs/security/2026-11-11-discovery-corrections.md)** — Corrected live admission/VVI/retention behavior, versioned challenger, and bounded historical replay results.

## 2. Core Pipeline Invariants (Do Not Regress)
1. **Strict `5/5` Gate Retention (`radar-v3` publication):**
   - Retained candidates in [`src/algotrader/api/portal.py`](src/algotrader/api/portal.py) (`_do_crypto_screen`) are re-verified every scan cycle via [`src/algotrader/application/radar_service.py`](src/algotrader/application/radar_service.py).
   - Visible retained candidates require five positively verified current gates. Failed or unknown telemetry removes the card from publication while audit events preserve the episode. Candidates older than 96 hours are expired.
2. **Stage-1 vs. Stage-2 Archetype Parity (`src/algotrader/domain/archetypes.py`):**
   - Stage 1 runs on OHLCV data (`enriched=False`, `oi_delta_7d=0`) before Stage-2 derivatives enrichment.
   - Never require Stage-2-only fields in Stage 1 without an OHLCV structural path (`VCP_COMPRESSION_LONG`, `RANGE_BOTTOM_ACCUMULATION_LONG`, `Stealth Coil Fallback`, or `vvi_ignition`).
   - In `Stealth Coil Fallback`, tight `bb_inside_kc_4h` squeezes above `4H EMA50` and `30d POC` must allow **either** supply exhaustion (`rvol_4h <= 1.15`) **or** active buyer absorption (`taker_4h >= 1.02`).
3. **L6 Checklist & Conviction Parity (`src/algotrader/application/crypto_screener.py`):**
   - When the active `evaluate_long_gates` passes enriched `L6` under `discovery_v2`, `is_deriv_flow_ok` must agree. The -3% 7d OI path needs nonnegative 4H OI or taker flow >= 1.01. Confirmed fresh OI inflection, ceiling confirmation, ignition and acute-liquidation guards remain explicit domain rules.
4. **24h / 48h Research Isolation (`scripts/research_prospective.py`):**
   - Sampler owns `.worker.lock`; offline report generator owns `.report.lock` with network disabled.

## 3. Pre-Commit Verification Suite
```bash
uv run ruff check src
uv run mypy
uv run pytest tests/test_radar_lifecycle.py tests/test_research_discovery.py tests/test_live_screener_no_cache.py -q
```
