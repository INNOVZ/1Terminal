#!/usr/bin/env python3
"""
Generates the comprehensive high-level architectural PDF specification
for the Atlas / Alfa Terminal cryptocurrency screener and research platform.
"""

import os
import shutil
import subprocess
from pathlib import Path

WORKSPACE = Path("/Users/jithinjacob/Desktop/trdindicator")
DOCS_DIR = WORKSPACE / "docs"
OUTPUT_PDF = DOCS_DIR / "Atlas_Trading_Screener_Architecture.pdf"
ROOT_PDF = WORKSPACE / "Atlas_Trading_Screener_Architecture.pdf"
ARTIFACT_DIR = Path("/Users/jithinjacob/.gemini/antigravity-ide/brain/9dcf9479-e34b-456e-bd2d-423488bb9794")
ARTIFACT_PDF = ARTIFACT_DIR / "Atlas_Trading_Screener_Architecture.pdf"
TMP_HTML = Path("/tmp/atlas_trading_screener_architecture.html")

HTML_CONTENT = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Atlas Institutional Crypto Screener & Research Platform — System Architecture</title>
<style>
  @page {
    size: A4 portrait;
    margin: 9mm 11mm 9mm 11mm;
  }

  * {
    box-sizing: border-box;
    -webkit-print-color-adjust: exact !important;
    print-color-adjust: exact !important;
  }

  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    color: #0f172a;
    background-color: #ffffff;
    line-height: 1.34;
    font-size: 8.0pt;
    margin: 0;
    padding: 0;
  }

  .page-container {
    height: 279mm;
    max-height: 279mm;
    overflow: hidden;
    page-break-after: always;
    break-after: page;
    position: relative;
    display: flex;
    flex-direction: column;
  }

  .page-container:last-child {
    page-break-after: avoid;
    break-after: avoid;
  }

  /* Header */
  .doc-header {
    border-bottom: 2px solid #0f172a;
    padding-bottom: 5px;
    margin-bottom: 6px;
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
  }
6
  .doc-title {
    font-size: 14pt;
    font-weight: 900;
    color: #0f172a;
    letter-spacing: -0.02em;
    margin: 0;
    line-height: 1.15;
  }

  .doc-subtitle {
    font-size: 8.2pt;
    font-weight: 700;
    color: #0284c7;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    margin-top: 2px;
  }

  .doc-meta {
    text-align: right;
    font-size: 7.2pt;
    color: #475569;
    font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
    line-height: 1.25;
  }

  /* Section Headings */
  h2 {
    font-size: 9.8pt;
    font-weight: 800;
    color: #0f172a;
    border-left: 3.5px solid #0284c7;
    padding-left: 6px;
    margin: 6px 0 4px 0;
    text-transform: uppercase;
    letter-spacing: 0.02em;
  }

  h2.emerald { border-left-color: #059669; }
  h2.amber { border-left-color: #d97706; }
  h2.rose { border-left-color: #e11d48; }
  h2.indigo { border-left-color: #4f46e5; }

  h3 {
    font-size: 8.4pt;
    font-weight: 800;
    color: #1e293b;
    margin: 4px 0 2px 0;
    text-transform: uppercase;
    letter-spacing: 0.01em;
  }

  p {
    margin: 0 0 3.5px 0;
    text-align: justify;
  }

  ul, ol {
    margin: 0 0 4px 0;
    padding-left: 14px;
  }

  li {
    margin-bottom: 1.5px;
  }

  /* Footer */
  .page-footer {
    margin-top: auto;
    padding-top: 3px;
    border-top: 1px solid #cbd5e1;
    display: flex;
    justify-content: space-between;
    font-size: 7.0pt;
    color: #64748b;
    font-family: "SFMono-Regular", Consolas, monospace;
  }

  /* Badges & Tags */
  .badge {
    display: inline-block;
    padding: 1px 4px;
    border-radius: 3px;
    font-size: 6.8pt;
    font-weight: 700;
    font-family: "SFMono-Regular", Consolas, monospace;
    text-transform: uppercase;
  }
  .badge-blue { background: #e0f2fe; color: #0369a1; border: 1px solid #bae6fd; }
  .badge-emerald { background: #dcfce7; color: #15803d; border: 1px solid #86efac; }
  .badge-amber { background: #fef3c7; color: #b45309; border: 1px solid #fde68a; }
  .badge-rose { background: #ffe4e6; color: #be123c; border: 1px solid #fecdd3; }
  .badge-purple { background: #f3e8ff; color: #6b21a8; border: 1px solid #d8b4fe; }
  .badge-slate { background: #f1f5f9; color: #334155; border: 1px solid #cbd5e1; }

  /* Callout Boxes */
  .callout-box {
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    border-left: 3px solid #0284c7;
    border-radius: 4px;
    padding: 4px 7px;
    margin: 4px 0;
    font-size: 7.4pt;
  }
  .callout-box.emerald { border-left-color: #059669; background: #f0fdf4; }
  .callout-box.amber { border-left-color: #d97706; background: #fffbeb; }
  .callout-box.rose { border-left-color: #e11d48; background: #fff1f2; }
  .callout-box.indigo { border-left-color: #4f46e5; background: #eef2ff; }

  /* Tables */
  table {
    width: 100%;
    border-collapse: collapse;
    margin: 3.5px 0;
    font-size: 7.1pt;
  }

  th {
    background: #0f172a;
    color: #ffffff;
    font-weight: 700;
    text-align: left;
    padding: 3px 5px;
    border: 1px solid #0f172a;
    letter-spacing: 0.01em;
  }

  td {
    padding: 2.8px 5px;
    border: 1px solid #cbd5e1;
    vertical-align: middle;
  }

  tr:nth-child(even) { background-color: #f8fafc; }

  code {
    font-family: "SFMono-Regular", Consolas, monospace;
    font-size: 7.0pt;
    background: #f1f5f9;
    padding: 1px 3px;
    border-radius: 2px;
    border: 1px solid #e2e8f0;
    color: #0f172a;
  }

  /* Grid Layouts */
  .grid-2 {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 6px;
    margin: 3.5px 0;
  }

  .grid-3 {
    display: grid;
    grid-template-columns: 1fr 1fr 1fr;
    gap: 5px;
    margin: 3.5px 0;
  }

  .grid-4 {
    display: grid;
    grid-template-columns: 1fr 1fr 1fr 1fr;
    gap: 5px;
    margin: 3.5px 0;
  }

  .card {
    background: #ffffff;
    border: 1px solid #cbd5e1;
    border-radius: 4px;
    padding: 4px 6px;
  }

  .card-header {
    font-size: 7.5pt;
    font-weight: 800;
    color: #0f172a;
    margin-bottom: 2px;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }

  .formula-box {
    background: #0f172a;
    color: #f8fafc;
    font-family: "SFMono-Regular", Consolas, monospace;
    font-size: 7.0pt;
    padding: 4px 7px;
    border-radius: 3px;
    margin: 3.5px 0;
    line-height: 1.3;
  }

  .diagram-box {
    background: #090d16;
    color: #38bdf8;
    font-family: "SFMono-Regular", Consolas, monospace;
    font-size: 6.8pt;
    padding: 6px 8px;
    border-radius: 4px;
    margin: 4px 0;
    line-height: 1.28;
    border: 1px solid #1e293b;
    white-space: pre;
  }
</style>
</head>
<body>

  <!-- ========================================================================= -->
  <!-- PAGE 1: EXECUTIVE ARCHITECTURE & PURE-SCREENER SYSTEM TOPOLOGY            -->
  <!-- ========================================================================= -->
  <div class="page-container">
    <div class="doc-header">
      <div>
        <h1 class="doc-title">ALFA TERMINAL (ATLAS) — SYSTEM ARCHITECTURE</h1>
        <div class="doc-subtitle">Institutional Cryptocurrency Screener, Analytical Scenarios & Forward Research Engine</div>
      </div>
      <div class="doc-meta">
        <div><strong>Status:</strong> Active Local Engineering Baseline</div>
        <div><strong>Test Verification:</strong> 514 Passing Tests &bull; Strict Mypy (0 Errors)</div>
        <div><strong>Operational Paradigm:</strong> Pure Public Screener &bull; Zero Execution Authority</div>
      </div>
    </div>

    <h2>1. Executive Summary & Product Boundary</h2>
    <p>
      <strong>Atlas</strong> (Alfa Terminal) is an institutional-grade, multi-timeframe cryptocurrency opportunity screening and prospective research platform. The system ingests public market microstructure across Binance Spot and USD-M perpetual futures, identifies high-conviction structural coils, order-flow absorption, and distribution exhaustions, and executes rigorous forward-outcome observation.
    </p>
    <div class="callout-box emerald">
      <strong>Core Engineering Decision — The Pure-Screener Boundary:</strong> All legacy broker abstractions, order routers, position managers, paper trading, and credential signers have been completely eliminated from the production runtime. The application possesses <strong>zero private exchange credentials, zero order submission capabilities, and zero account endpoint access</strong>. Both historical ASGI paths (<code>algotrader.api.portal_app:app</code> and <code>algotrader.api.app:app</code>) serve exclusively the read-only screener portal.
    </div>

    <h2>2. High-Level Modular Monolith Topology</h2>
    <p>
      Atlas is architected as an in-process, high-concurrency modular monolith deployed on a single VM. It enforces strict Hexagonal separation (Domain Core &bull; Application Orchestration &bull; Public Adapters &bull; Persistence &bull; Presentation):
    </p>

    <div class="diagram-box">
+---------------------------------------------------------------------------------------------------------------+
|                                      PRESENTATION & CONSUMER INTERFACES                                       |
|  * Next.js 16 Web Dashboard (Lightweight Charts v5, Depth & VPVR, Macro Panel, Real-time Watchlist Audits)     |
|  * Unified 'atlas' CLI Suite: crypto-screen, replay-scan, observe-scan, shadow-scan, snapshot-ranks, etc.      |
+---------------------------------------------------------------------------------------------------------------+
                                                      | HTTP / JSON REST
+---------------------------------------------------------------------------------------------------------------+
|                                            FASTAPI PORTAL GATEWAY                                             |
|  GET /api/screener/live  |  POST /api/screener/rescan  |  GET /api/chart/{symbol}  |  /api/watchlist          |
|  GET /api/macro          |  GET /api/screener/cron/status                          |  /healthz & /readyz      |
+---------------------------------------------------------------------------------------------------------------+
                                                      | In-Process Async Lifespan
+---------------------------------------------------------------------------------------------------------------+
|                                        APPLICATION ORCHESTRATION LAYER                                        |
|  * CryptoOpportunityScreener : Coordinates 2-stage scanning, gate evaluation, scoring, ranking & desk caps    |
|  * ShadowRunner & Observer   : Bounded Track B discovery runner, quote anomaly detection & shadow replays     |
|  * RankCollector & Benchmark : Rolling 24h ticker snapshots, top-10 membership tracking & offline benchmarks  |
|  * ForwardObserver           : Prospective 24h/48h outcome tracking, MFE/MAE excursion & touch diagnostics   |
|  * ScreenerCronJob           : In-process scheduler aligned to 4-Hour UTC candle closes (+30s grace)          |
|  * WatchlistService          : SQLite persistence of curated assets & asynchronous Wyckoff audit cache        |
+---------------------------------------------------------------------------------------------------------------+
                    |                                                    |
+---------------------------------------------+   +-------------------------------------------------------------+
|             DOMAIN CORE (PURE MATH)         |   |                 ADAPTERS & INFRASTRUCTURE                   |
|  * Screening Gates: 6 Long & 5 Short gates  |   |  * Public Binance Spot & USD-M Market Data Adapters         |
|  * Setup Archetype Classifiers & Squeezes   |   |  * Guarded GET-only Transport (strict allowlist, zero auth) |
|  * Vectorized Volume Profile (POC/VAH/VAL)  |   |  * DeFiLlama & Alternative.me Macro Telemetry Adapters      |
|  * Continuous Scoring & 3-Tier EV/R Model   |   |  * LocalScanArchive (immutable checksummed SHA-256 store)   |
|  * Analytical Execution Bracket Generator   |   |  * LocalShadowArchive, RankArchive & SQLite State DB        |
+---------------------------------------------+   +-------------------------------------------------------------+
    </div>

    <h2>3. The Research Contract & Analytical Scenarios</h2>
    <div class="grid-2">
      <div class="card">
        <div class="card-header"><span class="badge badge-blue">Uncalibrated Research Notice</span></div>
        <p style="font-size: 7.1pt; margin: 0;">
          All scores, grades (A+, A, B), and mathematical expected values (<code>expected_value_r</code>) are structured analytical hypotheses. In accordance with the project contract, all API responses explicitly declare <code>validation_status: "uncalibrated_research"</code> and <code>order_execution_enabled: false</code>.
        </p>
      </div>
      <div class="card">
        <div class="card-header"><span class="badge badge-emerald">Analytical Brackets vs Orders</span></div>
        <p style="font-size: 7.1pt; margin: 0;">
          Calculated Entry (Limit), Trigger (Stop), Invalidation Stop Loss, and Multi-Tier Targets (T1, T2, T3) are strictly <strong>analytical price levels</strong> intended for discretionary trader chart visualization and forward statistical measurement. No automated sizing, exchange orders, or margin checks follow.
        </p>
      </div>
    </div>

    <h2>4. Verified Architectural Health & Quality Ledger</h2>
    <table>
      <thead>
        <tr>
          <th style="width: 25%;">Component</th>
          <th style="width: 35%;">Engineered Capability</th>
          <th style="width: 40%;">Verification & Test Evidence</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td><strong>Test Suite</strong></td>
          <td>514 automated unit, integration, and regression tests.</td>
          <td><span class="badge badge-emerald">514 Passed</span> in 30.9s; zero failures; ResourceWarning-clean.</td>
        </tr>
        <tr>
          <td><strong>Type Discipline</strong></td>
          <td>Strict Mypy type-checking across all 81 source modules.</td>
          <td><span class="badge badge-emerald">Strict Passed</span>; 0 type errors; complete type annotations.</td>
        </tr>
        <tr>
          <td><strong>Static Analysis</strong></td>
          <td>Ruff linter adhering to E, F, I, UP, B, SIM, ASYNC rules.</td>
          <td><span class="badge badge-emerald">Clean</span>; 0 lint errors in application and domain code.</td>
        </tr>
        <tr>
          <td><strong>Frontend Build</strong></td>
          <td>Next.js 16 App Router with Turbopack compilation.</td>
          <td><span class="badge badge-emerald">Compiled Successfully</span>; static route generation; 0 build errors.</td>
        </tr>
      </tbody>
    </table>

    <div class="page-footer">
      <span>Alfa Terminal (Atlas) &bull; High-Level System Architecture Specification</span>
      <span>Page 1 of 6</span>
    </div>
  </div>

  <!-- ========================================================================= -->
  <!-- PAGE 2: DOMAIN ANALYTICAL ENGINE & MULTI-STAGE SCREENING                  -->
  <!-- ========================================================================= -->
  <div class="page-container">
    <div class="doc-header">
      <div>
        <h1 class="doc-title">DOMAIN ANALYTICAL ENGINE & SCREENING PIPELINE</h1>
        <div class="doc-subtitle">Two-Stage Funnel, Wyckoff Archetypes, Volume Profile & Resistance Absorption</div>
      </div>
      <div class="doc-meta">
        <div><strong>Stage 1:</strong> Pre-Filtering (Zero Network Latency)</div>
        <div><strong>Stage 2:</strong> Deep Order Book & Derivatives Enrichment</div>
        <div><strong>Throughput:</strong> 250 Tokens Evaluated in &lt;22 Seconds</div>
      </div>
    </div>

    <h2>1. The Two-Stage Screening Funnel Architecture</h2>
    <p>
      To screen hundreds of contracts without hitting exchange rate limits or stalling the async event loop, Atlas implements a decoupled <strong>Two-Stage Screening Pipeline</strong>:
    </p>

    <div class="grid-2">
      <div class="card">
        <div class="card-header"><span class="badge badge-blue">Stage 1: Vectorized Pre-Filtering</span></div>
        <p style="font-size: 7.1pt; margin-bottom: 2px;">
          Ingests 1H and 4H historical candles across top liquid contracts. Derives 1D and 1W structures in-memory via bar aggregation. Evaluates baseline trend alignment, Minervini criteria, and John Carter compression without requesting secondary order books or open interest.
        </p>
        <div class="callout-box" style="margin: 0; padding: 2px 4px;">
          <strong>Filter Rate:</strong> Discards ~80% of uncoiled or downtrending assets at zero network cost.
        </div>
      </div>
      <div class="card">
        <div class="card-header"><span class="badge badge-purple">Stage 2: Deep Derivatives Enrichment</span></div>
        <p style="font-size: 7.1pt; margin-bottom: 2px;">
          Only candidates that survive Stage 1 are admitted to Stage 2. The pipeline fetches:
        </p>
        <ul style="font-size: 7.0pt; margin: 0; padding-left: 12px;">
          <li>500-level order book bid-ask liquidity & spread depth</li>
          <li>7-day and 1-hour Open Interest delta & historical velocity</li>
          <li>Real-time contrarian perpetual funding rates and basis skew</li>
          <li>Taker buy/sell volume aggression ratios</li>
        </ul>
      </div>
    </div>

    <h2>2. Setup Archetype Classification Matrix</h2>
    <p>
      The domain layer classifies technical setups into distinct, non-overlapping institutional archetypes:
    </p>
    <table>
      <thead>
        <tr>
          <th style="width: 22%;">Archetype Identifier</th>
          <th style="width: 14%;">Setup Family</th>
          <th style="width: 32%;">Quantitative Mechanics</th>
          <th style="width: 32%;">Institutional Setup Context</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td><code>COIL_SPRING_LONG</code></td>
          <td><span class="badge badge-emerald">Structural</span></td>
          <td>Wyckoff Phase C shakeout below support, rapid reclaim of 4H EMA50 / POC, down-volume dry-up (VCR &le; 0.90x).</td>
          <td>Trapped retail sellers liquidated on terminal spring; smart money re-accumulates prior to markup.</td>
        </tr>
        <tr>
          <td><code>VCP_COMPRESSION_LONG</code></td>
          <td><span class="badge badge-emerald">Structural</span></td>
          <td>Volatility Contraction Pattern; progressively tighter candle ranges with drying volume beneath overhead resistance.</td>
          <td>Supply exhaustion; float drying up as buyers absorb circulating liquidity into a tight coil.</td>
        </tr>
        <tr>
          <td><code>OI_ACCUMULATION_LONG</code></td>
          <td><span class="badge badge-amber">Positioning</span></td>
          <td>Massive 7-day Open Interest expansion (+10% to +60%) while price remains flat/compressed in base.</td>
          <td>Institutional positioning without triggering retail price slippage; coiled spring dynamic.</td>
        </tr>
        <tr>
          <td><code>SHORT_SQUEEZE_ABSORPTION</code></td>
          <td><span class="badge badge-amber">Positioning</span></td>
          <td>Negative perpetual funding rate, aggressive taker buying into passive asks, spot/perp basis discount.</td>
          <td>Overleveraged short sellers trapped at resistance; vulnerable to violent upward cascade.</td>
        </tr>
        <tr>
          <td><code>BREAKOUT_LONG</code></td>
          <td><span class="badge badge-blue">Expansion</span></td>
          <td>Price clears swing pivot / VAH ceiling with relative volume expansion (&ge; 1.25x) and expanding OI.</td>
          <td>Momentum expansion confirming transition from Stage 1 base into Stage 2 markup.</td>
        </tr>
        <tr>
          <td><code>UTAD_DIVERGENCE_SHORT</code></td>
          <td><span class="badge badge-rose">Distribution</span></td>
          <td>Wyckoff Upthrust After Distribution; 30d high sweep with buying climax wicks, followed by 4H RSI divergence.</td>
          <td>Late retail longs trapped at multi-week highs as smart money distributes into liquidity wicks.</td>
        </tr>
        <tr>
          <td><code>EXHAUSTION_REVERSAL_SHORT</code></td>
          <td><span class="badge badge-rose">Distribution</span></td>
          <td>Daily/Weekly RSI &gt; 75–84, extreme 7d OI surge (+50% to +200%), vertical parabola blowout.</td>
          <td>Mean-reversion rollover; speculative buying exhausted, leading to cascading margin liquidations.</td>
        </tr>
      </tbody>
    </table>

    <h2>3. Vectorized Volume Profile & Resistance Absorption Engine</h2>
    <div class="grid-2">
      <div class="card">
        <div class="card-header"><span class="badge badge-blue">Inlined Triangular VPVR (66ms)</span></div>
        <p style="font-size: 7.1pt; margin-bottom: 2px;">
          Traditional volume-at-price iterations require hundreds of milliseconds per token. Atlas implements an inlined float triangular volume profile using precomputed inverse spans:
        </p>
        <div class="formula-box" style="margin: 2px 0;">
          VPVR: 180 4H bars (30 Days) &bull; POC (Point of Control) &bull; VAH (70% Value Area High) &bull; VAL (Value Area Low)
        </div>
        <p style="font-size: 7.0pt; margin: 0;">
          Computes accurate institutional liquidity shelves in <strong>&lt;66ms per token</strong>, yielding an 86% CPU reduction and enabling high-frequency scanning without worker starvation.
        </p>
      </div>
      <div class="card">
        <div class="card-header"><span class="badge badge-amber">Resistance Absorption Analytics</span></div>
        <p style="font-size: 7.1pt; margin-bottom: 2px;">
          Evaluates whether price is being actively absorbed beneath an institutional resistance ceiling:
        </p>
        <ul style="font-size: 7.0pt; margin: 0; padding-left: 12px;">
          <li><strong>Proximity:</strong> Distance to ceiling must be within -3% to +35% of local base.</li>
          <li><strong>Dry-Up:</strong> Volume Contraction Ratio (VCR) on down-bars confirms lack of selling pressure.</li>
          <li><strong>Bull-Trap Veto:</strong> If price tests ceiling while Open Interest bleeds and volume is flat, the setup is vetoed immediately.</li>
        </ul>
      </div>
    </div>

    <h2>4. The Squeeze Geometry & Trend Strength Baseline</h2>
    <p>
      Atlas couples John Carter's Squeeze geometry with Alexander Elder and Mark Minervini trend criteria:
    </p>
    <ul>
      <li><strong>John Carter Squeeze:</strong> Bollinger Bands (20, 2.0) must contract completely inside Keltner Channels (20, 1.5 ATR), or normalized bandwidth must be &lt; 28% (ideal &lt; 10%), signaling impending explosive volatility release.</li>
      <li><strong>Minervini Trend Alignment:</strong> Close &ge; 4H EMA50 * 0.985, and 4H EMA20 &ge; 4H EMA50 * 0.975. Stage 4 downtrends and falling knives are strictly disqualified.</li>
      <li><strong>ADX Directional Strength:</strong> Measures non-directional trend velocity. ADX &ge; 25 with +DI &gt; -DI confirms active directional regime.</li>
    </ul>

    <div class="page-footer">
      <span>Alfa Terminal (Atlas) &bull; High-Level System Architecture Specification</span>
      <span>Page 2 of 6</span>
    </div>
  </div>

  <!-- ========================================================================= -->
  <!-- PAGE 3: SCORING, CONVICTION & MATHEMATICAL BRACKETS                       -->
  <!-- ========================================================================= -->
  <div class="page-container">
    <div class="doc-header">
      <div>
        <h1 class="doc-title">SCORING ENGINE, CONVICTION & EXECUTION BRACKETS</h1>
        <div class="doc-subtitle">Non-Linear Multi-Factor Model, 3-Tier EV/R & Bounded Analytical Brackets</div>
      </div>
      <div class="doc-meta">
        <div><strong>Quality Floor:</strong> Grade B (Score &ge; 68.0 &bull; Conviction &ge; 65.0)</div>
        <div><strong>Target R:R:</strong> Risk-to-Reward Floor &ge; 2.0</div>
        <div><strong>Desk Cap:</strong> Strictly 16 Curated Institutional Opportunities</div>
      </div>
    </div>

    <h2>1. Continuous Multi-Factor Composite Scoring (0.0 – 100.0)</h2>
    <p>
      Candidates surviving hard gate filters are graded across five orthogonal, continuous quantitative dimensions:
    </p>
    <div class="formula-box">
      SCORE = 0.25 * Trend_Score + 0.25 * Momentum_Score + 0.25 * Derivatives_Score + 0.15 * Alpha_Score + 0.10 * Volume_Score
    </div>

    <table>
      <thead>
        <tr>
          <th style="width: 24%;">Factor Pillar</th>
          <th style="width: 12%;">Weight</th>
          <th style="width: 64%;">Underlying Mathematical & Market Microstructure Inputs</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td><strong>Trend Architecture</strong></td>
          <td>25%</td>
          <td>4H EMA20 vs EMA50 spread, 4H EMA50 vs 200 distance, ADX trend velocity (&ge;25), Minervini Stage 2 alignment.</td>
        </tr>
        <tr>
          <td><strong>Momentum & Squeeze</strong></td>
          <td>25%</td>
          <td>Bollinger Bandwidth compression (&lt;10%), BB nested inside KC, MACD histogram velocity, normalized price momentum.</td>
        </tr>
        <tr>
          <td><strong>Derivatives Positioning</strong></td>
          <td>25%</td>
          <td>7-day & 1-hour Open Interest delta, contrarian funding rate arbitrage, taker buy/sell ratio, order book depth imbalance.</td>
        </tr>
        <tr>
          <td><strong>BTC-Residual Alpha</strong></td>
          <td>15%</td>
          <td>Residual idiosyncratic alpha relative to Bitcoin across 6h, 24h, and 7d horizons; beta-adjusted return isolation.</td>
        </tr>
        <tr>
          <td><strong>Volume Microstructure</strong></td>
          <td>10%</td>
          <td>Relative Volume surge (RVOL &ge; 1.25x), Volume Contraction Ratio (down-bar volume dry-up), 24h quote volume depth.</td>
        </tr>
      </tbody>
    </table>

    <h2>2. Mathematical 3-Tier Expected Value ($E[V]_R$) Model</h2>
    <p>
      Rather than relying on naive single-target risk/reward, Atlas implements a probabilistic <strong>Three-Tier Expected Value</strong> model that reflects realistic institutional scaling across multiple price objectives:
    </p>
    <div class="formula-box">
      E[V]_R = [ P(T1) * R_T1 + P(T2) * R_T2 + P(T3) * R_T3 ] - [ (1 - P(Win)) * 1.0R + Friction_Cost ]
    </div>
    <div class="grid-3">
      <div class="card">
        <div class="card-header">Target 1 (Scale-Out)</div>
        <p style="font-size: 7.0pt; margin: 0;"><strong>40% of Position:</strong> Anchored at 30-day High / VAH ceiling. First liquidity milestone where risk is mitigated to breakeven.</p>
      </div>
      <div class="card">
        <div class="card-header">Target 2 (Core Objective)</div>
        <p style="font-size: 7.0pt; margin: 0;"><strong>35% of Position:</strong> Anchored at Fibonacci 1.618 expansion of the compression base. Primary swing target.</p>
      </div>
      <div class="card">
        <div class="card-header">Target 3 (Runner)</div>
        <p style="font-size: 7.0pt; margin: 0;"><strong>25% of Position:</strong> Anchored at Fibonacci 2.618 expansion. Position runner capturing full multi-week trend markup.</p>
      </div>
    </div>

    <h2>3. Conviction Rating & Quality Sizing Floor</h2>
    <p>
      The system enforces a strict institutional quality floor to prevent marginal setups from diluting the watchlist:
    </p>
    <div class="formula-box">
      CONVICTION = [ 0.40 * Estimated_Win_Rate_Pct + 0.60 * Score ] * Consensus_Multiplier
    </div>
    <ul>
      <li><strong>Consensus Multiplier:</strong> Multi-indicator alignment rewards candidates: 5-System agreement earns a <strong>1.08x bonus</strong>; 4-System agreement earns <strong>1.04x</strong>; discordant signals (&le; 2 systems) are penalized.</li>
      <li><strong>Strict Quality Veto:</strong> Candidates with Score &lt; 68.0, Conviction &lt; 65.0, or Expected Value $E[V]_R &lt; +0.20R$ are rejected.</li>
      <li><strong>Curated 16-Slot Desk Cap:</strong> Watchlist is strictly capped at <strong>16 high-conviction opportunities</strong> (up to 12 Longs, up to 6 Shorts), deduplicated by base asset to prevent multi-pair concentration risk.</li>
    </ul>

    <h2>4. Analytical Execution Bracket Construction</h2>
    <div class="grid-2">
      <div class="card">
        <div class="card-header"><span class="badge badge-emerald">Long Bracket Geometry (R:R &ge; 2.0)</span></div>
        <ul style="font-size: 7.0pt; margin: 0; padding-left: 12px;">
          <li><strong>Retest Entry (Limit):</strong> Anchored at 4H EMA50 / POC support shelf (<code>Primary_Shelf + 0.15 * ATR</code>).</li>
          <li><strong>Breakout Trigger (Stop-Buy):</strong> Placed immediately above local compression pivot (<code>Resistance + 0.05 * ATR</code>).</li>
          <li><strong>Invalidation Stop Loss:</strong> Anchored structurally below recent 4H swing low (<code>Support - 0.35 * ATR</code>).</li>
          <li><strong>Targets:</strong> T1 at 30d VAH; T2 at Fib 1.618; T3 at Fib 2.618.</li>
        </ul>
      </div>
      <div class="card">
        <div class="card-header"><span class="badge badge-rose">Short Bracket Geometry (Bounded)</span></div>
        <ul style="font-size: 7.0pt; margin: 0; padding-left: 12px;">
          <li><strong>Mandatory Positioning:</strong> Must be in upper 55% to 100% of 30d range.</li>
          <li><strong>Retest Entry (Sell-Limit):</strong> At 4H EMA50 / POC retest shelf (<code>POC - 0.15 * ATR</code>).</li>
          <li><strong>Breakdown Trigger:</strong> Below local support floor (<code>Low - 0.05 * ATR</code>).</li>
          <li><strong>Invalidation Stop:</strong> Above distribution swing high (<code>Resistance + 0.35 * ATR</code>).</li>
          <li><strong>Realistic Bounding:</strong> Target 1 capped at max 1.75R / max 14% drop to eliminate ancient historical wick overshoot.</li>
        </ul>
      </div>
    </div>

    <div class="page-footer">
      <span>Alfa Terminal (Atlas) &bull; High-Level System Architecture Specification</span>
      <span>Page 3 of 6</span>
    </div>
  </div>

  <!-- ========================================================================= -->
  <!-- PAGE 4: RESEARCH SUPERPOWERS: RECORDING, REPLAY & SHADOW RUNNER           -->
  <!-- ========================================================================= -->
  <div class="page-container">
    <div class="doc-header">
      <div>
        <h1 class="doc-title">RESEARCH SUPERPOWERS — RECORDING, REPLAY & SHADOW RUNNER</h1>
        <div class="doc-subtitle">Immutable Scan Archives, Forward Outcomes & Bounded Dual-Track Discovery</div>
      </div>
      <div class="doc-meta">
        <div><strong>Milestone 1:</strong> Immutable Input Archives &amp; 24h/48h Forward Outcomes</div>
        <div><strong>Milestone 2:</strong> Dual-Track Shadow Runner (Track B Discovery)</div>
        <div><strong>Storage:</strong> Checksummed SHA-256 JSON Directory Stores</div>
      </div>
    </div>

    <h2>1. Milestone 1: Immutable Scan Recording & Forward Outcomes Engine</h2>
    <p>
      To transition from speculative chart eyeballing to empirical quantitative research, Atlas records point-in-time decision evidence immutably. Every production scan produces an immutable record under <code>data/research/scans/&lt;scan_id&gt;/</code>:
    </p>

    <div class="grid-3">
      <div class="card">
        <div class="card-header">1. Normalized Port Archive</div>
        <p style="font-size: 7.0pt; margin: 0;">
          Captures typed market-data responses: eligible canonical cohort, instruments, quotes, all candle series, derivatives snapshots, and macro context. Decimal values are stored as exact strings; timestamps are aware UTC.
        </p>
      </div>
      <div class="card">
        <div class="card-header">2. Provenance Manifest</div>
        <p style="font-size: 7.0pt; margin: 0;">
          Manifest records schema version, payload SHA-256 hash, byte size, policy version, effective screening arguments, Git commit SHA, and Python environment fingerprints. Replay cryptographically verifies checksums before execution.
        </p>
      </div>
      <div class="card">
        <div class="card-header">3. Deterministic Replay</div>
        <p style="font-size: 7.0pt; margin: 0;">
          <code>atlas replay-scan</code> re-executes the exact screening engine offline using recorded inputs with zero network access. Guarantees byte-for-byte reproducibility of all candidate decisions, stage gates, and funnel counts.
        </p>
      </div>
    </div>

    <h2>2. Prospective Forward Observation Engine (24h & 48h Horizons)</h2>
    <p>
      Forward outcomes measure price trajectories prospectively across the <strong>entire original eligible canonical base-asset universe</strong>, not merely the curated candidates. This enables honest calculation of top-gainer recall without hindsight bias:
    </p>
    <ul>
      <li><strong>Anchor Point:</strong> Open price of the first full UTC minute interval whose start is at or after scan completion (never an earlier quote or hypothetical fill).</li>
      <li><strong>Excursion Diagnostics:</strong> Maximum Favorable Excursion (MFE) and Maximum Adverse Excursion (MAE) recorded across the exact 1-minute OHLCV path over 24-hour and 48-hour horizons.</li>
      <li><strong>Ambiguous Touch Classification:</strong> Suggested entry, stop loss, and target touches are tracked independently. A same-bar stop and target touch is explicitly classified as ambiguous, never an assumed winning fill.</li>
    </ul>

    <h2>3. Milestone 2: Dual-Track Shadow Discovery Runner (Track B)</h2>
    <p>
      The <strong>Shadow Discovery Runner</strong> solves a critical research dilemma: discovering emerging long opportunities outside the current deep shortlist <em>without altering or risking the live published core screener</em>.
    </p>

    <div class="diagram-box">
+---------------------------------------------------------------------------------------------------------------+
| CORE PRODUCTION ARM (TRACK A)                        | SHADOW DISCOVERY ARM (TRACK B - OPT-IN EXPERIMENTAL)    |
| * 250 Top Liquid Instruments                         | * Admitted Candidate Pool (outside Core Shortlist)     |
| * Standard Stage 1 & Stage 2 Screening               | * Maximum 15 Admitted Tokens per Child Run             |
| * Published 16-Slot Curated Desk (0.00-21.6s)        | * Strict Resource Budget: 150 HTTP Attempts / 90s      |
| * Immutable Scan Record (data/research/scans/)       | * Child Record (data/research/shadow/CHILD_UUID/)      |
| * 24h/48h Forward Outcomes (Anchor: Core +0m)        | * 24h/48h Outcomes (Staggered Anchor: Child +0m)       |
+---------------------------------------------------------------------------------------------------------------+
    </div>

    <h2>4. Deterministic Shadow Admission Buckets & Resource Budget</h2>
    <p>
      Invoked via <code>atlas shadow-scan --scan-id CORE_UUID</code>, the shadow runner requires a complete, supported parent scan no older than 300 seconds. It admits up to <strong>15 unique tokens</strong> partitioned across three deterministic buckets:
    </p>
    <table>
      <thead>
        <tr>
          <th style="width: 25%;">Admission Bucket</th>
          <th style="width: 15%;">Allocation</th>
          <th style="width: 60%;">Deterministic Admission Rules & Hygiene Requirements</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td><strong>1. Quote Anomalies</strong></td>
          <td>Up to 5 Tokens</td>
          <td>Ranks tokens outside parent deep universe exhibiting negative funding rates and positive spot/perp basis evidence. Deep data hygiene requires quote volume &ge; $1.5m and spread &le; 25 bps.</td>
        </tr>
        <tr>
          <td><strong>2. Momentum Controls</strong></td>
          <td>Up to 5 Tokens</td>
          <td>Ranks observed 24h price change with deterministic volume and symbol tie-breaks across eligible pool, excluding core-published tokens. Serves as honest empirical control.</td>
        </tr>
        <tr>
          <td><strong>3. Coverage Probes</strong></td>
          <td>Up to 5 Tokens</td>
          <td>Deterministic hash sampling (<code>hash(parent_scan_id + symbol)</code>) outside selected universe to discover unranked Open Interest build-ups without scanning the entire exchange.</td>
        </tr>
      </tbody>
    </table>

    <div class="callout-box amber">
      <strong>Strict Budget & Freshness Safeguards:</strong> Shadow runner enforces a hard cap of <strong>150 actual HTTP attempts (including retries)</strong> and a <strong>90-second timeout</strong>. Derivatives observations must be &le;60s old; macro context &le;300s old with components &le;48h old. Budget exhaustion or stale telemetry marks the child run partial, never a manufactured negative outcome.
    </div>

    <div class="page-footer">
      <span>Alfa Terminal (Atlas) &bull; High-Level System Architecture Specification</span>
      <span>Page 4 of 6</span>
    </div>
  </div>

  <!-- ========================================================================= -->
  <!-- PAGE 5: PROSPECTIVE RANK OBSERVATIONS & MATCHED BENCHMARKS                -->
  <!-- ========================================================================= -->
  <div class="page-container">
    <div class="doc-header">
      <div>
        <h1 class="doc-title">PROSPECTIVE RANK OBSERVATIONS & MATCHED BENCHMARKS</h1>
        <div class="doc-subtitle">Rolling 24h Ticker Capture, First-Observed Membership & Honest Comparative Reports</div>
      </div>
      <div class="doc-meta">
        <div><strong>Milestone 3:</strong> Prospective Rank Timeline &amp; Matched-Anchor Benchmark</div>
        <div><strong>Binance Endpoint:</strong> Public USD-M GET /fapi/v1/ticker/24hr (Weight 40)</div>
        <div><strong>Benchmark Sets:</strong> Core Longs vs Shadow Longs vs Top 5 Momentum Control</div>
      </div>
    </div>

    <h2>1. Milestone 3: The Rolling Leaderboard Problem & Rank Snapshots</h2>
    <p>
      Fixed-horizon forward observations (24h/48h) answer whether an asset gained over 1 or 2 days, but they cannot answer the crucial operational research question: <em>Were qualified candidates identified before they appeared on the exchange's rolling top-gainers leaderboard?</em>
    </p>
    <div class="callout-box blue">
      <strong>Prospective Rank Capture Architecture:</strong> Atlas captures whole-market rolling 24h price changes via the public bulk USD-M ticker (<code>GET /fapi/v1/ticker/24hr</code>, weight 40). Captures are stored as checksummed immutable records under <code>data/research/ranks/&lt;parent_id&gt;/&lt;snapshot_id&gt;/</code>. Invocations are strictly bounded: cohort &le; 1000 symbols, max 3 HTTP attempts, 15-second acquisition deadline.
    </div>

    <h2>2. First-Observed Top-10 Membership & Sampling Hygiene</h2>
    <p>
      Rank normalization calculates candidate standing within the exact original eligible canonical cohort (using $1 + \text{count of strictly larger returns}$, accommodating ties and market-wide beta):
    </p>
    <ul>
      <li><strong>Top-10 Cohort Group:</strong> Classifies whether a token is inside the cohort's top 10 rolling performers (including ties). Because market-wide declines can produce negative top-10 returns, return sign is preserved and reported independently.</li>
      <li><strong>First-Observed State:</strong> Captures whether a candidate was observed <code>outside</code> the top-10 at admission time and subsequently observed <code>inside</code> the top-10 during the prospective monitoring window.</li>
      <li><strong>Sampling Hygiene Target:</strong> Target sampling interval is <strong>5 minutes</strong> with a <strong>10-minute maximum acceptable gap</strong>. Boundary coverage at the horizon start and end is required to establish reliable continuous tracking.</li>
    </ul>

    <h2>3. Honest Matched-Anchor Offline Benchmarks</h2>
    <p>
      Invoked via <code>atlas benchmark-shadow --shadow-id CHILD_UUID --horizon-hours 24|48</code>, this fully offline engine synthesizes parent scan data, shadow discovery runs, rank snapshots, and forward price paths into reproducible comparative research reports:
    </p>

    <table>
      <thead>
        <tr>
          <th style="width: 25%;">Comparison Set</th>
          <th style="width: 20%;">Constituent Scope</th>
          <th style="width: 55%;">Research Function & Transparency Guardrails</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td><strong>1. Core Longs</strong></td>
          <td>Published Track A opportunities</td>
          <td>Evaluates the performance of the baseline published strategy. Decisions use parent timestamp information.</td>
        </tr>
        <tr>
          <td><strong>2. Qualified Shadow Longs</strong></td>
          <td>Admitted Track B opportunities</td>
          <td>Evaluates the performance of the independent discovery arm. Qualified via shared publication-quality rules.</td>
        </tr>
        <tr>
          <td><strong>3. Momentum Control</strong></td>
          <td>Top 5 24h price momentum tokens</td>
          <td>Volume-filtered control cohort. Provides a naive market-momentum reference to test for true quantitative edge.</td>
        </tr>
      </tbody>
    </table>

    <div class="callout-box amber">
      <strong>Transparent Timing Disclosure — Matched Forward Anchor with Staggered Decisions:</strong> Because the child shadow run starts after parent scan completion, the child possesses newer market information. The benchmark engine aligns all three comparison sets at <strong>one shared forward anchor</strong> (the first full minute following child completion), while explicitly labelling the comparison as <code>matched_forward_anchor_staggered_decisions</code>. It never manufactures a false claim of simultaneous evaluation.
    </div>

    <h2>4. Macro Intelligence & Composite Overextension Index (COI)</h2>
    <p>
      Atlas incorporates macro market intelligence to compute the aggregate probability of market-wide deleveraging:
    </p>
    <div class="formula-box">
      COI (0–100) = 0.35 * Benchmark_Excursion + 0.30 * Leverage_Crowding + 0.20 * Market_Breadth + 0.15 * Stablecoin_Liquidity
    </div>
    <div class="grid-2">
      <div class="card">
        <div class="card-header">1. Benchmark Excursion & Breadth</div>
        <p style="font-size: 7.0pt; margin: 0;">
          BTC/ETH distance from Daily EMA50 & Bollinger Upper Band &bull; 14-day RSI excursion &bull; % of altcoins trading above 20-day moving average.
        </p>
      </div>
      <div class="card">
        <div class="card-header">2. Leverage Crowding & Stablecoins</div>
        <p style="font-size: 7.0pt; margin: 0;">
          Annualized perpetual funding rates across top 20 perpetuals (&gt;25% APR signals vulnerability) &bull; DeFiLlama 7d net stablecoin issuance flows.
        </p>
      </div>
    </div>

    <h2>5. In-Process Scheduler Aligned to UTC Candle Boundaries</h2>
    <p>
      The screener daemon (<code>ScreenerCronJob</code>) runs in-process inside the FastAPI portal lifespan, synchronized with global 4-Hour UTC candle closures (<strong>00:00, 04:00, 08:00, 12:00, 16:00, 20:00 UTC</strong>) plus a 30-second grace window to guarantee closed exchange bars.
    </p>

    <div class="page-footer">
      <span>Alfa Terminal (Atlas) &bull; High-Level System Architecture Specification</span>
      <span>Page 5 of 6</span>
    </div>
  </div>

  <!-- ========================================================================= -->
  <!-- PAGE 6: INTERFACE SPECS, CLI SUITE & OPERATIONAL DEPLOYMENT               -->
  <!-- ========================================================================= -->
  <div class="page-container">
    <div class="doc-header">
      <div>
        <h1 class="doc-title">INTERFACES, CLI SUITE & OPERATIONAL TOPOLOGY</h1>
        <div class="doc-subtitle">FastAPI REST Endpoints, Complete CLI Tooling, Watchlist SQLite & GCP VM Release</div>
      </div>
      <div class="doc-meta">
        <div><strong>Deployment Target:</strong> Single GCP Compute Engine VM (e2-standard)</div>
        <div><strong>Container Runtime:</strong> Docker Compose (alfa-terminal) &bull; UID 10001</div>
        <div><strong>Database Engine:</strong> SQLite 3 (WAL Mode &bull; Persistent Volume)</div>
      </div>
    </div>

    <h2>1. Unified 'atlas' Command-Line Interface Suite</h2>
    <p>
      The <code>algotrader.cli</code> module provides a unified command dispatcher covering screening, research, and benchmarking:
    </p>
    <table>
      <thead>
        <tr>
          <th style="width: 25%;">CLI Command Syntax</th>
          <th style="width: 20%;">Operational Mode</th>
          <th style="width: 55%;">Functional Scope & Execution Parameters</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td><code>atlas crypto-screen</code></td>
          <td><span class="badge badge-blue">Production Scan</span></td>
          <td>Executes full screener sweep (<code>--mode all|long|short</code>, <code>--min-score 68</code>, <code>--max-symbols 250</code>). Persists scan archive.</td>
        </tr>
        <tr>
          <td><code>atlas portal</code></td>
          <td><span class="badge badge-emerald">API Gateway</span></td>
          <td>Serves FastAPI portal via uvicorn (<code>--host 127.0.0.1 --port 8000</code>). Starts 4H background cron scheduler.</td>
        </tr>
        <tr>
          <td><code>atlas doctor</code></td>
          <td><span class="badge badge-slate">Diagnostics</span></td>
          <td>Offline configuration health audit; reports screener mode, environment, and verifies zero-credential state.</td>
        </tr>
        <tr>
          <td><code>atlas replay-scan</code></td>
          <td><span class="badge badge-purple">Research Replay</span></td>
          <td>Replays recorded scan offline using saved market-data inputs (<code>--scan-id UUID</code>). Verifies byte reproducibility.</td>
        </tr>
        <tr>
          <td><code>atlas observe-scan</code></td>
          <td><span class="badge badge-purple">Research Outcome</span></td>
          <td>Measures prospective 24h or 48h forward price paths and excursions (<code>--scan-id UUID --horizon-hours 24|48</code>).</td>
        </tr>
        <tr>
          <td><code>atlas shadow-scan</code></td>
          <td><span class="badge badge-amber">Shadow Discovery</span></td>
          <td>Executes bounded Track B discovery scan on a fresh parent scan (<code>--scan-id UUID</code>). Max 15 tokens, 150 attempts.</td>
        </tr>
        <tr>
          <td><code>atlas replay-shadow</code></td>
          <td><span class="badge badge-amber">Shadow Replay</span></td>
          <td>Replays recorded shadow child run offline with parent archive (<code>--shadow-id UUID</code>).</td>
        </tr>
        <tr>
          <td><code>atlas observe-shadow</code></td>
          <td><span class="badge badge-amber">Shadow Outcome</span></td>
          <td>Measures prospective 24h/48h forward outcomes anchored at child completion time (<code>--shadow-id UUID</code>).</td>
        </tr>
        <tr>
          <td><code>atlas snapshot-ranks</code></td>
          <td><span class="badge badge-blue">Rank Capture</span></td>
          <td>Captures bulk USD-M rolling 24h ticker snapshot into prospective timeline (<code>--scan-id UUID</code>). Max 3 attempts, 15s.</td>
        </tr>
        <tr>
          <td><code>atlas benchmark-shadow</code></td>
          <td><span class="badge badge-blue">Benchmark Report</span></td>
          <td>Builds offline matched-anchor benchmark comparing Core, Shadow, and Momentum controls (<code>--shadow-id UUID</code>).</td>
        </tr>
      </tbody>
    </table>

    <h2>2. FastAPI REST Interface Specifications</h2>
    <div class="grid-2">
      <div class="card">
        <div class="card-header"><span class="badge badge-blue">Core Screener & Chart Endpoints</span></div>
        <ul style="font-size: 7.0pt; margin: 0; padding-left: 12px;">
          <li><code>GET /api/screener/live</code>: Returns cached latest scan opportunities and scan counts.</li>
          <li><code>POST /api/screener/rescan</code>: Triggers on-demand screening sweep.</li>
          <li><code>GET /api/chart/{symbol}</code>: Multi-timeframe OHLCV candles, POC, VAH, VAL, and analytical execution brackets.</li>
          <li><code>GET /api/screener/cron/status</code>: Cron cadence, next fire time, and observed scan count.</li>
        </ul>
      </div>
      <div class="card">
        <div class="card-header"><span class="badge badge-emerald">Watchlist & Health Endpoints</span></div>
        <ul style="font-size: 7.0pt; margin: 0; padding-left: 12px;">
          <li><code>GET /api/watchlist</code>: User research watchlist with real-time Wyckoff audit status.</li>
          <li><code>POST /api/watchlist</code> & <code>DELETE /api/watchlist/{symbol}</code>: Watchlist mutations.</li>
          <li><code>GET /api/macro</code>: Composite Overextension Index & breadth metrics.</li>
          <li><code>GET /healthz</code> & <code>GET /readyz</code>: Kubernetes/Docker process health checks.</li>
        </ul>
      </div>
    </div>

    <h2>3. WatchlistService & SQLite State Architecture</h2>
    <p>
      The <code>WatchlistService</code> manages persistent user watchlists in an operational SQLite database (<code>atlas_state.sqlite3</code>). It is completely decoupled from the research archives:
    </p>
    <ul>
      <li><strong>Thread-Safe Connection Pooling:</strong> Employs WAL (Write-Ahead Logging) and fine-grained mutex locking to eliminate database lock contention during concurrent API reads and cron writes.</li>
      <li><strong>Asynchronous Live Wyckoff Audit Cache:</strong> Audits active watchlist assets against current 4H price structure, verifying whether the original setup thesis remains valid or has experienced structural invalidation.</li>
    </ul>

    <h2>4. Production Deployment & GCP VM Migration Architecture</h2>
    <div class="grid-2">
      <div class="card">
        <div class="card-header"><span class="badge badge-slate">Single GCP VM Architecture</span></div>
        <p style="font-size: 7.0pt; margin-bottom: 2px;">
          Deployed on Google Cloud Platform Compute Engine using Docker Compose:
        </p>
        <ul style="font-size: 6.8pt; margin: 0; padding-left: 12px;">
          <li>Compose project: <code>alfa-terminal</code></li>
          <li>Persistent volume: <code>alfa-terminal_atlas-state</code> mounted at <code>/app/data</code></li>
          <li>Container execution: Runs as non-root unprivileged <code>UID 10001</code></li>
          <li>Zero credentials: No secret files or API keys injected</li>
        </ul>
      </div>
      <div class="card">
        <div class="card-header"><span class="badge badge-emerald">CI/CD & Immutable Worktree Releases</span></div>
        <p style="font-size: 7.0pt; margin-bottom: 2px;">
          GitHub Actions workflow (<code>.github/workflows/deploy.yml</code>):
        </p>
        <ul style="font-size: 6.8pt; margin: 0; padding-left: 12px;">
          <li>Runs ruff, mypy, 514 pytest unit tests, and frontend build</li>
          <li>Performs network-disabled container smoke verification</li>
          <li>Deploys to GCP VM using detached release worktrees by commit SHA</li>
          <li>Zero-downtime container replacement with health check verification</li>
        </ul>
      </div>
    </div>

    <div class="page-footer">
      <span>Alfa Terminal (Atlas) &bull; High-Level System Architecture Specification</span>
      <span>Page 6 of 6</span>
    </div>
  </div>

</body>
</html>
"""

def generate_pdf():
    print(f"Writing HTML template to {TMP_HTML}...")
    TMP_HTML.write_text(HTML_CONTENT, encoding="utf-8")

    chrome_bin = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if not os.path.exists(chrome_bin):
        raise FileNotFoundError(f"Chrome binary not found at {chrome_bin}")

    print(f"Rendering PDF with headless Chrome to {OUTPUT_PDF}...")
    OUTPUT_PDF.parent.mkdir(parents=True, exist_ok=True)
    
    cmd = [
        chrome_bin,
        "--headless",
        "--disable-gpu",
        "--no-pdf-header-footer",
        f"--print-to-pdf={OUTPUT_PDF}",
        str(TMP_HTML),
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Chrome error: {result.stderr}")
        raise RuntimeError("Failed to generate PDF via Chrome")

    if not OUTPUT_PDF.exists():
        raise FileNotFoundError(f"Expected PDF was not generated at {OUTPUT_PDF}")

    size_kb = OUTPUT_PDF.stat().st_size / 1024
    print(f"PDF generated successfully at: {OUTPUT_PDF} ({size_kb:.1f} KB)")

    # Copy to project root
    shutil.copyfile(OUTPUT_PDF, ROOT_PDF)
    print(f"Copied to root: {ROOT_PDF}")

    # Copy to artifacts directory
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(OUTPUT_PDF, ARTIFACT_PDF)
    print(f"Copied to artifacts: {ARTIFACT_PDF}")

if __name__ == "__main__":
    generate_pdf()
