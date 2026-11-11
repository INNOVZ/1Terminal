# AGENTS.md — Coding Agent Reference for Atlas Screener (`trdindicator`)

## 1. Mandatory First Read for Screener & Research Tasks
Before modifying the screener pipeline, setup archetypes, radar lifecycle, or 24h/48h prospective research runner, read:
- **[`docs/screener-post-observation-and-48h-research-blueprint.md`](docs/screener-post-observation-and-48h-research-blueprint.md)** — Complete post-observation architecture, `discovery_v2` & `radar-v2` specifications, 24h/48h research study preparation, and the forensic catalog of vulnerabilities (`V1–V5`) and fixes.
- **[`docs/security/2026-11-11-pre-gainer-deep-research.md`](docs/security/2026-11-11-pre-gainer-deep-research.md)** — Empirical study of the Nov 8–10 48h observation cohort (`71346822-f686-4bb1-99ec-0f56ef8f0ea1`).
- **[`docs/security/2026-11-11-discovery-upgrade.md`](docs/security/2026-11-11-discovery-upgrade.md)** — `discovery-v2-full336-rvol4h-watch20` challenger specification and sampler/reporter lock isolation.

## 2. Core Pipeline Invariants (Do Not Regress)
1. **Strict `5/5` Gate Retention (`radar-v2`):**
   - Retained candidates in [`src/algotrader/api/portal.py`](src/algotrader/api/portal.py) (`_do_crypto_screen`) are re-verified every scan cycle via [`src/algotrader/application/radar_lifecycle.py`](src/algotrader/application/radar_lifecycle.py).
   - Any retained candidate with `len(failed_gates) > 0` (`<5/5` checklist gates) or `age_hours > 96.0` must be immediately evicted (`radar_gates_degraded` / `max_trade_horizon_96h_exceeded`).
2. **Stage-1 vs. Stage-2 Archetype Parity (`src/algotrader/domain/archetypes.py`):**
   - Stage 1 runs on OHLCV data (`enriched=False`, `oi_delta_7d=0`) before Stage-2 derivatives enrichment.
   - Never require Stage-2-only fields in Stage 1 without an OHLCV structural path (`VCP_COMPRESSION_LONG`, `RANGE_BOTTOM_ACCUMULATION_LONG`, `Stealth Coil Fallback`, or `vvi_ignition`).
   - In `Stealth Coil Fallback`, tight `bb_inside_kc_4h` squeezes above `4H EMA50` and `30d POC` must allow **either** supply exhaustion (`rvol_4h <= 1.15`) **or** active buyer absorption (`taker_4h >= 1.02`).
3. **L6 Checklist & Conviction Parity (`src/algotrader/application/crypto_screener.py`):**
   - When `evaluate_accumulation_gate` passes `L6` under `discovery_v2` (`oi_delta_7d >= -3.0%` with positive 4H OI or taker flow), `is_deriv_flow_ok` (`"Smart Money Absorption"`) must also evaluate to `True`.
4. **24h / 48h Research Isolation (`src/algotrader/application/research_runner.py`):**
   - Sampler owns `.worker.lock`; offline report generator owns `.report.lock` with network disabled.

## 3. Pre-Commit Verification Suite
```bash
uv run ruff check src
uv run mypy
uv run pytest tests/test_radar_lifecycle.py tests/test_research_discovery.py tests/test_live_screener_no_cache.py -q
```
