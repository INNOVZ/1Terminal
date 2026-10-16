import os
import subprocess
import shutil

html_content = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Atlas Institutional Crypto Platform — Strategy & Architecture Specification</title>
<style>
  @page {
    size: A4 portrait;
    margin: 11mm 13mm 11mm 13mm;
  }

  * {
    box-sizing: border-box;
    -webkit-print-color-adjust: exact !important;
    print-color-adjust: exact !important;
  }

  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    color: #1e293b;
    background-color: #ffffff;
    line-height: 1.42;
    font-size: 9pt;
    margin: 0;
    padding: 0;
  }

  .page-container {
    height: 270mm;
    max-height: 270mm;
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

  .page-footer {
    margin-top: auto;
    padding-top: 6px;
    border-top: 1px solid #e2e8f0;
    display: flex;
    justify-content: space-between;
    font-size: 7.5pt;
    color: #94a3b8;
    font-family: "SFMono-Regular", Consolas, monospace;
  }

  /* Header & Title Section */
  .doc-header {
    border-bottom: 2px solid #0f172a;
    padding-bottom: 8px;
    margin-bottom: 14px;
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
  }

  .doc-title-badge {
    display: inline-block;
    background: #0f172a;
    color: #f8fafc;
    font-size: 7.5pt;
    font-weight: 800;
    padding: 2px 7px;
    border-radius: 4px;
    letter-spacing: 0.8px;
    text-transform: uppercase;
    margin-bottom: 4px;
  }

  h1 {
    font-size: 19pt;
    font-weight: 900;
    color: #0f172a;
    margin: 0 0 2px 0;
    letter-spacing: -0.4px;
  }

  .doc-subtitle {
    font-size: 10pt;
    color: #475569;
    font-weight: 600;
    margin: 0;
  }

  .doc-meta {
    text-align: right;
    font-size: 7.5pt;
    color: #64748b;
    line-height: 1.35;
    font-family: "SFMono-Regular", Consolas, monospace;
  }

  /* Typography */
  h2 {
    font-size: 12pt;
    font-weight: 800;
    color: #0f172a;
    border-bottom: 1.5px solid #e2e8f0;
    padding-bottom: 4px;
    margin-top: 14px;
    margin-bottom: 8px;
    display: flex;
    align-items: center;
    gap: 6px;
  }

  h3 {
    font-size: 9.8pt;
    font-weight: 750;
    color: #0f172a;
    margin-top: 10px;
    margin-bottom: 5px;
  }

  p {
    margin: 0 0 8px 0;
    color: #334155;
    text-align: justify;
  }

  strong {
    color: #0f172a;
    font-weight: 700;
  }

  /* Executive Highlight Box */
  .exec-summary {
    background: #f8fafc;
    border: 1px solid #cbd5e1;
    border-left: 4px solid #0284c7;
    border-radius: 5px;
    padding: 10px 14px;
    margin-bottom: 14px;
  }

  .exec-summary h3 {
    margin: 0 0 4px 0;
    color: #0369a1;
    font-size: 9pt;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }

  .exec-summary p {
    margin: 0;
    font-size: 8.8pt;
  }

  /* Cards & Grid Containers */
  .grid-2 {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 10px;
    margin-bottom: 10px;
  }

  .grid-3 {
    display: grid;
    grid-template-columns: 1fr 1fr 1fr;
    gap: 9px;
    margin-bottom: 10px;
  }

  .grid-4 {
    display: grid;
    grid-template-columns: 1fr 1fr 1fr 1fr;
    gap: 8px;
    margin-bottom: 10px;
  }

  .info-card {
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 5px;
    padding: 8px 10px;
    box-shadow: 0 1px 2px rgba(0,0,0,0.02);
  }

  .info-card-header {
    font-size: 7.5pt;
    font-weight: 750;
    color: #64748b;
    text-transform: uppercase;
    letter-spacing: 0.4px;
    margin-bottom: 2px;
  }

  .info-card-value {
    font-size: 11.5pt;
    font-weight: 800;
    color: #0f172a;
    font-family: "SFMono-Regular", Consolas, monospace;
  }

  .info-card-desc {
    font-size: 7.5pt;
    color: #64748b;
    margin-top: 3px;
    line-height: 1.3;
  }

  /* Tables */
  table {
    width: 100%;
    border-collapse: collapse;
    margin: 8px 0 12px 0;
    font-size: 8pt;
  }

  th {
    background: #0f172a;
    color: #f8fafc;
    text-align: left;
    padding: 6px 8px;
    font-weight: 700;
    letter-spacing: 0.3px;
  }

  td {
    padding: 5px 8px;
    border-bottom: 1px solid #e2e8f0;
    color: #334155;
    vertical-align: top;
  }

  tr:nth-child(even) td {
    background-color: #f8fafc;
  }

  /* Badges & Tags */
  .badge {
    display: inline-block;
    padding: 1.5px 5px;
    border-radius: 3px;
    font-size: 7pt;
    font-weight: 750;
    text-transform: uppercase;
    letter-spacing: 0.4px;
  }

  .badge-emerald { background: #dcfce7; color: #15803d; border: 1px solid #bbf7d0; }
  .badge-blue { background: #e0f2fe; color: #0369a1; border: 1px solid #bae6fd; }
  .badge-amber { background: #fef3c7; color: #b45309; border: 1px solid #fde68a; }
  .badge-crimson { background: #fee2e2; color: #b91c1c; border: 1px solid #fecaca; }
  .badge-purple { background: #f3e8ff; color: #7e22ce; border: 1px solid #e9d5ff; }

  /* Math & Code blocks */
  .math-block {
    background: #f1f5f9;
    border-left: 3px solid #64748b;
    padding: 6px 10px;
    margin: 6px 0 10px 0;
    font-family: "SFMono-Regular", Consolas, monospace;
    font-size: 7.8pt;
    color: #0f172a;
  }

  .code-inline {
    font-family: "SFMono-Regular", Consolas, monospace;
    background: #f1f5f9;
    padding: 1px 4px;
    border-radius: 3px;
    font-size: 8pt;
    color: #0f172a;
  }

  /* Callout Boxes */
  .callout-rule {
    background: #fffbeb;
    border: 1px solid #fef3c7;
    border-left: 4px solid #f59e0b;
    border-radius: 4px;
    padding: 6px 10px;
    margin: 8px 0;
    font-size: 8.3pt;
  }

  .callout-rule strong {
    color: #b45309;
  }

  /* Diagram SVG wrappers */
  .diagram-container {
    background: #ffffff;
    border: 1px solid #cbd5e1;
    border-radius: 5px;
    padding: 10px;
    margin: 10px 0;
    text-align: center;
  }

  .diagram-caption {
    font-size: 7.5pt;
    color: #64748b;
    font-weight: 600;
    margin-top: 4px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }

  ul {
    margin: 0 0 8px 0;
    padding-left: 18px;
  }

  li {
    margin-bottom: 3px;
    color: #334155;
    font-size: 8.8pt;
  }
</style>
</head>
<body>

  <!-- ===================================================================== -->
  <!-- PAGE 1: TITLE, PHILOSOPHY & WYCKOFF MECHANICS                        -->
  <!-- ===================================================================== -->
  <div class="page-container">
    <div class="doc-header">
      <div>
        <div class="doc-title-badge">Institutional Architecture & Strategy</div>
        <h1>Atlas Crypto Trading Platform</h1>
        <p class="doc-subtitle">Pre-Breakout Volatility Radar, Multi-Day Watchlist & Macro Overextension Architecture</p>
      </div>
      <div class="doc-meta">
        <div><strong>Version:</strong> 2.4-Enterprise</div>
        <div><strong>Date:</strong> October 2026</div>
        <div><strong>Target:</strong> Binance USD-M Markets</div>
        <div><strong>Classification:</strong> Proprietary Quantitative</div>
      </div>
    </div>

    <div class="exec-summary">
      <h3>Executive Summary</h3>
      <p>
        The <strong>Atlas Institutional Crypto Trading Platform</strong> is an end-to-end quantitative trading infrastructure engineered to solve the primary failure mode of retail momentum strategies: <em>buying extended breakouts into liquidity traps</em>. The system identifies institutional base accumulation coils <strong>before</strong> the first expansion candle ignites, continuously audits resting setups against Binance real-time order books, and gauges market-wide overextension and leverage fragility across perpetual futures contracts.
      </p>
    </div>

    <div class="grid-3">
      <div class="info-card">
        <div class="info-card-header">Target Alpha Profile</div>
        <div class="info-card-value" style="color: #0369a1;">Pre-Breakout</div>
        <div class="info-card-desc">Volatility squeeze & Wyckoff Phase C accumulation holding institutional Point of Control (POC).</div>
      </div>
      <div class="info-card">
        <div class="info-card-header">Average Risk/Reward</div>
        <div class="info-card-value" style="color: #15803d;">4.8 : 1</div>
        <div class="info-card-desc">Tight structural invalidation below POC floor vs 2-stage multi-day expansion targets.</div>
      </div>
      <div class="info-card">
        <div class="info-card-header">Liquidity Universe</div>
        <div class="info-card-value" style="color: #b45309;">740+ USD-M</div>
        <div class="info-card-desc">Algorithmic volume-turnover filtering down to the top 120-470 liquid perpetual contracts.</div>
      </div>
    </div>

    <h2>1. Core Trading Philosophy: Pre-Breakout Edge vs Momentum Chasing</h2>
    <p>
      Traditional momentum screeners scan for tokens gaining <code>+15%</code> to <code>+30%</code> in 24 hours. In institutional crypto trading, buying after a large impulsive candle represents negative asymmetric expectancy: retail traders are buying the liquidity exit of institutional accumulators. Atlas enters strictly during compression.
    </p>

    <!-- SVG Strategy Paradigm Comparison Diagram -->
    <div class="diagram-container">
      <svg width="100%" height="150" viewBox="0 0 760 150" xmlns="http://www.w3.org/2000/svg">
        <line x1="20" y1="120" x2="740" y2="120" stroke="#e2e8f0" stroke-width="1" />
        <line x1="20" y1="75" x2="740" y2="75" stroke="#e2e8f0" stroke-dasharray="3,3" stroke-width="1" />
        
        <rect x="60" y="65" width="300" height="55" fill="#f0fdf4" stroke="#86efac" stroke-dasharray="4,4" rx="4" />
        <text x="210" y="58" font-size="8.5" font-weight="700" fill="#15803d" text-anchor="middle">Institutional Accumulation Coil (Low Volatility / Narrow ATR)</text>
        
        <path d="M 60,90 Q 110,80 150,105 T 220,112 T 280,85 T 340,78 L 400,26 L 460,22 L 530,8 L 620,75" 
              fill="none" stroke="#0f172a" stroke-width="2.2" />
        
        <line x1="50" y1="95" x2="370" y2="95" stroke="#0284c7" stroke-width="2" />
        <text x="70" y="107" font-size="7.5" font-weight="800" fill="#0284c7">30-Day POC Acceptance Shelf</text>

        <line x1="300" y1="75" x2="430" y2="75" stroke="#d97706" stroke-width="1.5" stroke-dasharray="3,3" />
        <text x="360" y="69" font-size="7.5" font-weight="800" fill="#d97706">Breakout Trigger Ceiling</text>

        <circle cx="340" cy="78" r="5" fill="#10b981" stroke="#ffffff" stroke-width="1.5" />
        <rect x="270" y="128" width="140" height="18" fill="#dcfce7" stroke="#86efac" rx="3" />
        <text x="340" y="140" font-size="7.5" font-weight="800" fill="#15803d" text-anchor="middle">ATLAS ENTRY: Tight Coil</text>

        <circle cx="530" cy="8" r="5" fill="#ef4444" stroke="#ffffff" stroke-width="1.5" />
        <rect x="460" y="2" width="140" height="18" fill="#fee2e2" stroke="#fca5a5" rx="3" />
        <text x="530" y="14" font-size="7.5" font-weight="800" fill="#b91c1c" text-anchor="middle">RETAIL TRAP: Chasing +25%</text>

        <path d="M 530,8 L 620,75" fill="none" stroke="#ef4444" stroke-width="1.8" stroke-dasharray="3,3" />
        <text x="635" y="55" font-size="7.5" font-weight="700" fill="#b91c1c">Liquidation Cascade</text>
      </svg>
      <div class="diagram-caption">Figure 1: Institutional Pre-Breakout Entry Geometry vs Retail Momentum Chasing Trap</div>
    </div>

    <p><strong>Wyckoff Volume Profile Mechanics:</strong></p>
    <ul>
      <li><strong>Float Absorption:</strong> Smart money accumulates float quietly within a narrow volatility range without driving price higher.</li>
      <li><strong>Volume Dry-Up:</strong> As consolidation nears maturity, supply contracts sharply. Down-candles print lighter volume than up-candles.</li>
      <li><strong>Asymmetric Trigger:</strong> A resting conditional stop-market order is positioned immediately above the base ceiling. If ignited, the spring uncoils into markup. If invalidation is breached, risk is strictly cut with minimal drawdown.</li>
    </ul>

    <div class="page-footer">
      <span>Atlas Institutional Quantitative Portal — Confidential & Proprietary</span>
      <span>Page 1 of 5</span>
    </div>
  </div>

  <!-- ===================================================================== -->
  <!-- PAGE 2: THE 6 INSTITUTIONAL GATES & QUALITY SCORING                   -->
  <!-- ===================================================================== -->
  <div class="page-container">
    <h2>2. The 6 Institutional Filtering Gates</h2>
    <p>
      Every asset in the liquid universe must pass through 6 rigorous quantitative gates before entering the candidate pipeline:
    </p>

    <table>
      <thead>
        <tr>
          <th style="width: 14%;">Gate</th>
          <th style="width: 25%;">Metric / Indicator</th>
          <th style="width: 25%;">Mathematical Condition</th>
          <th style="width: 36%;">Institutional Rationale</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td><strong>Gate 1: POC Acceptance</strong></td>
          <td>30-Day Volume Profile & 4H EMA50</td>
          <td><code>Price &gt;= POC_30d</code><br><code>Price &gt;= EMA50_4h</code></td>
          <td>Price must be accepted above highest-volume value area. Trading below POC represents overhead bagholder supply.</td>
        </tr>
        <tr>
          <td><strong>Gate 2: Anti-Chasing</strong></td>
          <td>72H Max Excursion & Dist from High</td>
          <td><code>Excursion_72h &lt;= 18.0%</code><br><code>Dist_From_High &lt;= 7.5%</code><br><code>RSI_1H &lt;= 65 &amp; RSI_4H &lt;= 65</code></td>
          <td>Guarantees entry before markup ignition. Disqualifies assets that already experienced extended parabolic runs.</td>
        </tr>
        <tr>
          <td><strong>Gate 3: Volatility Squeeze</strong></td>
          <td>Bollinger Bandwidth & Daily ATR</td>
          <td><code>BBW &lt; 5.5%</code> (Bottom 20% quantile)<br><code>ATR_14d Contracted</code></td>
          <td>Volatility cycles alternate between contraction and expansion. Entry occurs during extreme energy compression.</td>
        </tr>
        <tr>
          <td><strong>Gate 4: Volume Dry-Up</strong></td>
          <td>Down-Candle Volume Ratio</td>
          <td><code>Vol_Down / Vol_Up &lt;= 1.0x</code><br><code>Vol_Contraction &lt;= 1.25</code></td>
          <td>Floating supply is exhausted. Smart money is holding coins; sellers lack aggression to push prices down.</td>
        </tr>
        <tr>
          <td><strong>Gate 5: Derivatives Flow</strong></td>
          <td>7-Day Open Interest & Funding Rate</td>
          <td><code>OI_Change_7d &gt;= +2.0%</code><br><code>Funding_APR &lt; 25.0%</code></td>
          <td>Capital is entering the derivatives contract without punitive long crowding (funding is cheap and organic).</td>
        </tr>
        <tr>
          <td><strong>Gate 6: Order Aggression</strong></td>
          <td>Taker Buy/Sell Volume Ratio</td>
          <td><code>Taker_Ratio &gt;= 0.85</code><br><code>Depth_Bid &gt;= 0.90 * Depth_Ask</code></td>
          <td>Active market buyers absorb resting limit asks on Binance perpetual order books.</td>
        </tr>
      </tbody>
    </table>

    <h3>Candidate Quality Grading & Multi-Factor Scoring Formula</h3>
    <p>
      Candidates passing all gates receive quantitative scores from <code>0.0</code> to <code>100.0</code> based on a weighted multi-factor regression:
    </p>

    <div class="math-block">
Score = 0.25 * CompressionScore + 0.20 * VolumeProfileScore + 0.20 * OrderBookFlowScore + 0.20 * MacroTailwind + 0.15 * DerivativesOIScore
    </div>

    <div class="grid-2">
      <div class="info-card">
        <div class="info-card-header">Grade A+ Setup (Score 75.0 – 100.0)</div>
        <div style="color: #15803d; font-weight: 800; font-size: 10pt;">Perfect 5/5 or 6/6 Gate Clearance</div>
        <div class="info-card-desc">
          Ideal Wyckoff Phase C test with tight float absorption, cheap funding (&lt;10% APR), and resting breakout ceiling within 4% of current price. Full institutional sizing.
        </div>
      </div>
      <div class="info-card">
        <div class="info-card-header">Grade A Setup (Score 60.0 – 74.9)</div>
        <div style="color: #b45309; font-weight: 800; font-size: 10pt;">High-Quality 4/5 or 5/6 Gate Clearance</div>
        <div class="info-card-desc">
          Strong compression structure with minor metric deviation (e.g. 72h excursion at 14% or taker ratio at 0.92). Approved for standard risk allocation.
        </div>
      </div>
    </div>

    <div class="callout-rule">
      <strong>Anti-Chasing Enforcement:</strong> If an asset expands &gt;18% within 72 hours, or if 1H RSI crosses above 65 into overbought territory, it is permanently disqualified from candidate status. The engine strictly forbids chasing runaway momentum.
    </div>

    <div class="page-footer">
      <span>Atlas Institutional Quantitative Portal — Confidential & Proprietary</span>
      <span>Page 2 of 5</span>
    </div>
  </div>

  <!-- ===================================================================== -->
  <!-- PAGE 3: HEXAGONAL ARCHITECTURE & RADAR SUBSYSTEM                     -->
  <!-- ===================================================================== -->
  <div class="page-container">
    <h2>3. End-to-End System Architecture (Clean / Hexagonal)</h2>
    <p>
      Atlas is built upon a <strong>Domain-Driven Hexagonal Architecture</strong> separating quantitative domain evaluation from external broker adapters, fast async background schedulers, and reactive presentation layers:
    </p>

    <!-- SVG Architecture Flow Diagram -->
    <div class="diagram-container">
      <svg width="100%" height="180" viewBox="0 0 760 180" xmlns="http://www.w3.org/2000/svg">
        <!-- Presentation Layer -->
        <rect x="20" y="15" width="220" height="150" fill="#f8fafc" stroke="#cbd5e1" rx="6" />
        <text x="130" y="34" font-size="9" font-weight="800" fill="#0f172a" text-anchor="middle">PRESENTATION & CLIENT</text>
        <rect x="35" y="46" width="190" height="26" fill="#ffffff" stroke="#e2e8f0" rx="4" />
        <text x="130" y="63" font-size="7.8" font-weight="700" fill="#0284c7" text-anchor="middle">Next.js 16 (Turbopack + App Router)</text>
        <rect x="35" y="80" width="190" height="26" fill="#ffffff" stroke="#e2e8f0" rx="4" />
        <text x="130" y="97" font-size="7.8" font-weight="700" fill="#0284c7" text-anchor="middle">Binance Multi-Ticker WS Stream</text>
        <rect x="35" y="114" width="190" height="26" fill="#ffffff" stroke="#e2e8f0" rx="4" />
        <text x="130" y="131" font-size="7.8" font-weight="700" fill="#0284c7" text-anchor="middle">Interactive Radar & Macro Canvas</text>

        <!-- Arrows -->
        <line x1="240" y1="90" x2="270" y2="90" stroke="#64748b" stroke-width="2" />

        <!-- Application Layer -->
        <rect x="270" y="15" width="220" height="150" fill="#f1f5f9" stroke="#94a3b8" rx="6" />
        <text x="380" y="34" font-size="9" font-weight="800" fill="#0f172a" text-anchor="middle">APPLICATION & API GATEWAY</text>
        <rect x="285" y="46" width="190" height="26" fill="#ffffff" stroke="#cbd5e1" rx="4" />
        <text x="380" y="63" font-size="7.8" font-weight="700" fill="#334155" text-anchor="middle">FastAPI Asynchronous Gateway</text>
        <rect x="285" y="80" width="190" height="26" fill="#ffffff" stroke="#cbd5e1" rx="4" />
        <text x="380" y="97" font-size="7.8" font-weight="700" fill="#334155" text-anchor="middle">WatchlistService & Live Audits</text>
        <rect x="285" y="114" width="190" height="26" fill="#ffffff" stroke="#cbd5e1" rx="4" />
        <text x="380" y="131" font-size="7.8" font-weight="700" fill="#334155" text-anchor="middle">MacroIntelligenceService (MOEA)</text>

        <!-- Arrows -->
        <line x1="490" y1="90" x2="520" y2="90" stroke="#64748b" stroke-width="2" />

        <!-- Domain & Adapters -->
        <rect x="520" y="15" width="220" height="150" fill="#f8fafc" stroke="#cbd5e1" rx="6" />
        <text x="630" y="34" font-size="9" font-weight="800" fill="#0f172a" text-anchor="middle">DOMAIN & ADAPTERS</text>
        <rect x="535" y="46" width="190" height="26" fill="#ffffff" stroke="#e2e8f0" rx="4" />
        <text x="630" y="63" font-size="7.8" font-weight="700" fill="#15803d" text-anchor="middle">Binance USD-M Futures Port (FAPI)</text>
        <rect x="535" y="80" width="190" height="26" fill="#ffffff" stroke="#e2e8f0" rx="4" />
        <text x="630" y="97" font-size="7.8" font-weight="700" fill="#15803d" text-anchor="middle">Institutional Gate Decision Tree</text>
        <rect x="535" y="114" width="190" height="26" fill="#ffffff" stroke="#e2e8f0" rx="4" />
        <text x="630" y="131" font-size="7.8" font-weight="700" fill="#15803d" text-anchor="middle">DeFiLlama & AltMe Telemetry</text>
      </svg>
      <div class="diagram-caption">Figure 2: Clean Hexagonal System Topology & Micro-Service Boundaries</div>
    </div>

    <h2>4. Subsystem Deep Dive</h2>
    <h3>Subsystem A: The Institutional Pre-Breakout Radar (Scanner Engine)</h3>
    <p>
      The Radar scans 740+ Binance USD-M contracts aligned with 8-hour candle closes (00:00, 08:00, 16:00 UTC). It executes an intelligent two-tier pipeline to guarantee zero Binance API rate limiting:
    </p>
    <ul>
      <li><strong>Tier 1 Liquidity Universe Filter:</strong> Aggregates 24h quote volume and open interest; selects top liquid contracts (470 in full mode, 120 in deep rescan mode).</li>
      <li><strong>Tier 2 Concurrent Gate Evaluation:</strong> Computes 4H/1D klines, volume profiles, and taker ratios in bounded worker pools (concurrency = 8) with proactive <code>X-MBX-USED-WEIGHT</code> throttling.</li>
      <li><strong>Volume Profile & Execution Levels:</strong> Evaluates Value Area High (VAH), Value Area Low (VAL), and POC. Defines Target 1 at resistance ceiling and Target 2 at Fibonacci 1.618 expansion.</li>
    </ul>

    <div class="page-footer">
      <span>Atlas Institutional Quantitative Portal — Confidential & Proprietary</span>
      <span>Page 3 of 5</span>
    </div>
  </div>

  <!-- ===================================================================== -->
  <!-- PAGE 4: WATCHLIST & MACRO OVEREXTENSION ENGINE (MOEA)                -->
  <!-- ===================================================================== -->
  <div class="page-container">
    <h3>Subsystem B: Multi-Day Watchlist & Real-Time Audit Engine</h3>
    <p>
      Once candidates are added to the Watchlist, they enter a continuous real-time diagnostic audit loop. Rather than static bookmarks, the Watchlist evaluates real-time market data from Binance against the initial thesis:
    </p>

    <table>
      <thead>
        <tr>
          <th style="width: 24%;">Audit Status</th>
          <th style="width: 36%;">Order Book & Diagnostic Reading</th>
          <th style="width: 40%;">Algorithmic Execution Directive</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td><span class="badge badge-blue">COIL_HOLDING</span></td>
          <td>Price holds between POC support and ceiling. Taker ratio &gt;= 0.85. 1H RSI &lt; 65.</td>
          <td>Thesis intact. Maintain resting stop-market order at trigger ceiling.</td>
        </tr>
        <tr>
          <td><span class="badge badge-emerald">ACTIVE_EXPANSION</span></td>
          <td>Price moved &gt;5% from base or 1H RSI &gt; 65 into overbought territory.</td>
          <td>Setup graduated from pre-breakout to markup. Anti-chasing active; trail stops.</td>
        </tr>
        <tr>
          <td><span class="badge badge-emerald">TARGET_REACHED</span></td>
          <td>Live price touched or exceeded Target 1 level.</td>
          <td>Take 50% profit off the table. Move stop-loss on remaining 50% to breakeven.</td>
        </tr>
        <tr>
          <td><span class="badge badge-amber">FLOW_DIVERGENCE</span></td>
          <td>Taker buy/sell collapsed &lt;0.85 or down-volume expanded &gt;1.25x.</td>
          <td>Smart money absorption paused. Tighten invalidation floor; pause new adds.</td>
        </tr>
        <tr>
          <td><span class="badge badge-crimson">SELLER_INFLUX</span></td>
          <td>Taker ratio collapsed &lt;0.60 (aggressive market sellers outnumber buyers 2:1).</td>
          <td>Order flow distribution. Cancel resting breakout trigger orders; stand aside.</td>
        </tr>
        <tr>
          <td><span class="badge badge-crimson">STOP_BREACHED</span></td>
          <td>Live price pierced below structural invalidation support floor.</td>
          <td>Thesis nullified. Hard stop triggered immediately to protect trading capital.</td>
        </tr>
      </tbody>
    </table>

    <h3>Subsystem C: Macro Market Intelligence & Overextension Architecture (MOEA)</h3>
    <p>
      The MOEA engine calculates the <strong>Composite Overextension Index (COI: 0 – 100)</strong>, evaluating whether the broader market is in an unsustainable blow-off regime susceptible to sharp liquidation flushes:
    </p>

    <div class="math-block">
COI = 0.35 * BenchmarkExcursion + 0.30 * DerivativesLeverageHeat + 0.20 * UniverseBreadth + 0.15 * LiquidityDivergence
    </div>

    <div class="grid-2">
      <div class="info-card">
        <div class="info-card-header">1. Benchmark Excursion (BTC & ETH)</div>
        <div class="info-card-desc">
          Quantifies statistical stretch from the 20-day SMA and 50-day EMA. Normalizes 20-day Z-scores (σ) and Bollinger %B across dual timeframes (Daily & 4H RSI).
        </div>
      </div>
      <div class="info-card">
        <div class="info-card-header">2. Derivatives Leverage Heatmap</div>
        <div class="info-card-desc">
          Scans all 920+ USD-M perpetual funding rates. Evaluates annualized APR (&gt;25% indicates heated crowding; &gt;40% indicates danger climax) and crowded long ratios.
        </div>
      </div>
      <div class="info-card">
        <div class="info-card-header">3. Universe Breadth Saturation</div>
        <div class="info-card-desc">
          Measures the percentage of the universe trading above moving averages and density of overbought clusters (&gt;7% 24h expansion).
        </div>
      </div>
      <div class="info-card">
        <div class="info-card-header">4. Synthetic Leverage Detector</div>
        <div class="info-card-desc">
          Cross-references token price gains against DeFiLlama 7-day stablecoin net flows. Flags rallies funded purely by borrowed perps without fiat inflow.
        </div>
      </div>
    </div>

    <div class="callout-rule">
      <strong>Cool-Off Probability Model:</strong> When COI exceeds 68/100, the probability of a 4% to 8% benchmark flush or multi-day consolidation exceeds 74%. Atlas automatically switches the execution stance to profit-taking and halts fresh breakout deployments.
    </div>

    <div class="page-footer">
      <span>Atlas Institutional Quantitative Portal — Confidential & Proprietary</span>
      <span>Page 4 of 5</span>
    </div>
  </div>

  <!-- ===================================================================== -->
  <!-- PAGE 5: RISK MANAGEMENT & OPERATIONAL TOPOLOGY                       -->
  <!-- ===================================================================== -->
  <div class="page-container">
    <h2>5. Risk Management, Position Sizing & Execution Playbook</h2>
    <p>
      Quantitative alpha is only as robust as its capital preservation framework. Atlas strictly enforces programmatic risk parameters across all execution states:
    </p>

    <div class="callout-rule">
      <strong>Programmatic Capital Preservation Rule:</strong> Maximum allowable account risk per trade is strictly capped at <code>1.0% to 1.5%</code> of total equity. Risk is measured as the distance between the entry trigger and the structural invalidation floor.
    </div>

    <table>
      <thead>
        <tr>
          <th style="width: 20%;">Execution Phase</th>
          <th style="width: 30%;">Operational Action</th>
          <th style="width: 50%;">Risk Rule & Capital Management</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td><strong>Phase 1: Pre-Breakout Setup</strong></td>
          <td>Place resting Stop-Market order at Breakout Trigger ceiling.</td>
          <td>No position opened until trigger confirms. Capital remains 100% liquid in yield-bearing USD collateral.</td>
        </tr>
        <tr>
          <td><strong>Phase 2: Ignition & Entry</strong></td>
          <td>Order fills on ceiling breach. Immediately place GTC Stop-Loss at Invalidation price.</td>
          <td>Initial protective stop set at <code>POC Support - 0.25 ATR</code>. Maximum loss fixed at predefined budget.</td>
        </tr>
        <tr>
          <td><strong>Phase 3: Target 1 Scale-Out</strong></td>
          <td>Price reaches Target 1 (average +10% to +18% gain).</td>
          <td><strong>Take 50% profit off the table.</strong> Automatically ratchet protective stop on remaining 50% to Breakeven.</td>
        </tr>
        <tr>
          <td><strong>Phase 4: Target 2 Runner</strong></td>
          <td>Price expands toward Target 2 Fibonacci extension.</td>
          <td>Trail stop using 4H EMA20 or 4H Parabolic SAR. Risk-free runner with locked-in profits.</td>
        </tr>
      </tbody>
    </table>

    <h2>6. Technical Specifications & Operational Topology</h2>
    <div class="grid-3">
      <div class="info-card">
        <div class="info-card-header">Backend Runtime</div>
        <div class="info-card-value" style="font-size: 10pt;">Python 3.13 / FastAPI</div>
        <div class="info-card-desc">Asyncio event loop, Uvicorn ASGI daemon, non-blocking HTTP connection pools.</div>
      </div>
      <div class="info-card">
        <div class="info-card-header">Frontend Framework</div>
        <div class="info-card-value" style="font-size: 10pt;">Next.js 16 (Turbopack)</div>
        <div class="info-card-desc">React 19 Server Components, full client-side memoization, on-demand dynamic dialogs.</div>
      </div>
      <div class="info-card">
        <div class="info-card-header">Real-Time Telemetry</div>
        <div class="info-card-value" style="font-size: 10pt;">WSS Combined Streams</div>
        <div class="info-card-desc">Binance WebSocket multi-ticker combined streams with 3-second REST fallback polling.</div>
      </div>
    </div>

    <h3>Resilience & Fault Isolation Matrix</h3>
    <ul>
      <li><strong>Binance Weight Isolation:</strong> Live audits and macro telemetry utilize zero-cost bulk endpoints (<code>/fapi/v1/ticker/price</code> and <code>/fapi/v1/premiumIndex</code> with weight = 1) rather than individual ticker hammering.</li>
      <li><strong>External Provider Graceful Degradation:</strong> If DeFiLlama or Alternative.me experience network downtime, <code>MacroSentimentProvider</code> isolates the failure with async <code>return_exceptions=True</code> and serves cached baselines without interrupting trade execution.</li>
      <li><strong>Client-Side Zero-Layout-Shift:</strong> Candidate cards, watchlists, and macro gauges utilize CSS Grid virtual containment and React <code>memo</code> architectures to prevent UI re-renders during high-frequency price ticks.</li>
    </ul>

    <div class="page-footer">
      <span>Atlas Institutional Quantitative Portal — Confidential & Proprietary</span>
      <span>Page 5 of 5</span>
    </div>
  </div>

</body>
</html>
"""

html_path = "/tmp/atlas_strategy_architecture.html"
pdf_path = "/Users/jithinjacob/Desktop/trdindicator/Atlas_Trading_System_Architecture_and_Strategy.pdf"
artifact_pdf_path = "/Users/jithinjacob/.gemini/antigravity-ide/brain/71d33a7d-01ff-46a8-b776-64991cec576f/Atlas_Trading_System_Architecture_and_Strategy.pdf"

with open(html_path, "w", encoding="utf-8") as f:
    f.write(html_content)

print(f"HTML written to {html_path}")

cmd = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "--headless",
    "--disable-gpu",
    "--no-pdf-header-footer",
    f"--print-to-pdf={pdf_path}",
    html_path,
]

result = subprocess.run(cmd, capture_output=True, text=True)

if os.path.exists(pdf_path):
    size = os.path.getsize(pdf_path)
    print(f"PDF successfully generated at: {pdf_path} (Size: {size:,} bytes)")
    shutil.copyfile(pdf_path, artifact_pdf_path)
    print(f"PDF copied to artifacts at: {artifact_pdf_path}")
else:
    print("PDF generation failed.")
