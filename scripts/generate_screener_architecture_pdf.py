import os
import subprocess
import shutil

html_content = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Atlas Institutional Crypto Screening Engine — Architecture & Strategy Specification</title>
<style>
  @page {
    size: A4 portrait;
    margin: 10mm 12mm 10mm 12mm;
  }

  * {
    box-sizing: border-box;
    -webkit-print-color-adjust: exact !important;
    print-color-adjust: exact !important;
  }

  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    color: #0f172a;
    background-color: #ffffff;
    line-height: 1.4;
    font-size: 8.5pt;
    margin: 0;
    padding: 0;
  }

  .page-container {
    height: 272mm;
    max-height: 272mm;
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
    padding-top: 5px;
    border-top: 1px solid #cbd5e1;
    display: flex;
    justify-content: space-between;
    font-size: 7.5pt;
    color: #64748b;
    font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
  }

  /* Header */
  .doc-header {
    border-bottom: 2px solid #0f172a;
    padding-bottom: 8px;
    margin-bottom: 10px;
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
  }

  .doc-title {
    font-size: 16pt;
    font-weight: 900;
    color: #0f172a;
    letter-spacing: -0.02em;
    margin: 0;
    line-height: 1.15;
  }

  .doc-subtitle {
    font-size: 9pt;
    font-weight: 600;
    color: #b45309;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin-top: 3px;
  }

  .doc-meta {
    text-align: right;
    font-size: 7.5pt;
    color: #64748b;
    font-family: "SFMono-Regular", Consolas, monospace;
    line-height: 1.3;
  }

  h2 {
    font-size: 11pt;
    font-weight: 800;
    color: #0f172a;
    border-left: 3.5px solid #d97706;
    padding-left: 6px;
    margin: 8px 0 6px 0;
    text-transform: uppercase;
    letter-spacing: 0.02em;
  }

  h3 {
    font-size: 9pt;
    font-weight: 800;
    color: #1e293b;
    margin: 6px 0 3px 0;
    text-transform: uppercase;
    letter-spacing: 0.01em;
  }

  p {
    margin: 0 0 5px 0;
    text-align: justify;
  }

  /* Highlights & Callouts */
  .badge {
    display: inline-block;
    padding: 1px 5px;
    border-radius: 3px;
    font-size: 7pt;
    font-weight: 800;
    font-family: "SFMono-Regular", Consolas, monospace;
    text-transform: uppercase;
  }
  .badge-long { background: #dcfce7; color: #15803d; border: 1px solid #86efac; }
  .badge-short { background: #fee2e2; color: #b91c1c; border: 1px solid #fca5a5; }
  .badge-gold { background: #fef3c7; color: #b45309; border: 1px solid #fde68a; }
  .badge-blue { background: #e0f2fe; color: #0369a1; border: 1px solid #bae6fd; }

  .callout-box {
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    border-left: 3px solid #0284c7;
    border-radius: 4px;
    padding: 6px 10px;
    margin: 6px 0;
    font-size: 8pt;
  }

  .callout-box.gold { border-left-color: #d97706; background: #fffbeb; }
  .callout-box.green { border-left-color: #16a34a; background: #f0fdf4; }
  .callout-box.red { border-left-color: #dc2626; background: #fef2f2; }

  /* Tables */
  table {
    width: 100%;
    border-collapse: collapse;
    margin: 5px 0;
    font-size: 7.5pt;
  }

  th {
    background: #0f172a;
    color: #ffffff;
    font-weight: 700;
    text-align: left;
    padding: 4px 6px;
    border: 1px solid #0f172a;
    font-family: -apple-system, BlinkMacSystemFont, sans-serif;
    letter-spacing: 0.02em;
  }

  td {
    padding: 3.5px 6px;
    border: 1px solid #cbd5e1;
    vertical-align: middle;
  }

  tr:nth-child(even) { background-color: #f8fafc; }

  code {
    font-family: "SFMono-Regular", Consolas, monospace;
    font-size: 7.5pt;
    background: #f1f5f9;
    padding: 1px 3px;
    border-radius: 3px;
    border: 1px solid #e2e8f0;
    color: #0f172a;
  }

  /* Grid Layouts */
  .grid-2 {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
    margin: 5px 0;
  }

  .grid-3 {
    display: grid;
    grid-template-columns: 1fr 1fr 1fr;
    gap: 6px;
    margin: 5px 0;
  }

  .card {
    background: #ffffff;
    border: 1px solid #cbd5e1;
    border-radius: 4px;
    padding: 7px 9px;
  }

  .card-header {
    font-size: 8pt;
    font-weight: 800;
    color: #0f172a;
    margin-bottom: 3px;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }

  .formula-box {
    background: #0f172a;
    color: #f8fafc;
    font-family: "SFMono-Regular", Consolas, monospace;
    font-size: 7.5pt;
    padding: 6px 9px;
    border-radius: 4px;
    margin: 5px 0;
    line-height: 1.35;
  }
</style>
</head>
<body>

  <!-- ========================================================================= -->
  <!-- PAGE 1: EXECUTIVE OVERVIEW & CORE SYSTEM ARCHITECTURE                     -->
  <!-- ========================================================================= -->
  <div class="page-container">
    <div class="doc-header">
      <div>
        <h1 class="doc-title">ATLAS INSTITUTIONAL CRYPTO SCREENER</h1>
        <div class="doc-subtitle">System Architecture & Quantitative Strategy Specification (v2.4)</div>
      </div>
      <div class="doc-meta">
        <div><strong>Status:</strong> Production Institutional Release</div>
        <div><strong>Venue:</strong> Binance USD-M & Spot Universes</div>
        <div><strong>Execution:</strong> Multi-Timeframe Algorithmic Radar</div>
      </div>
    </div>

    <h2>1. Executive Summary & The Quantitative Edge</h2>
    <p>
      The Atlas Institutional Crypto Screener is a multi-timeframe quantitative screening engine designed to systematically identify high-expectancy trade opportunities across Binance USD-M perpetual contracts. Rather than chasing post-expansion volatility (+20% to +50% pumps) where retail traders routinely become exit liquidity, Atlas identifies:
    </p>
    <ul>
      <li><strong>Long Opportunities:</strong> Pre-breakout volatility coils, Wyckoff Phase C springs, and quiet volume-profile absorption <em>before</em> trend markup.</li>
      <li><strong>Short Opportunities:</strong> Post-vertical run-up blow-off tops, Wyckoff Phase C Upthrusts After Distribution (UTAD), and multi-timeframe momentum exhaustion in the upper 55% to 100% of the 30-day range.</li>
    </ul>

    <h2>2. Clean Hexagonal System Architecture</h2>
    <p>
      The platform is structured using strict Hexagonal (Ports & Adapters) architectural separation to isolate data feeds, quantitative mathematical modeling, and execution logic:
    </p>

    <div class="grid-3">
      <div class="card">
        <div class="card-header">1. Domain Layer</div>
        <p style="font-size: 7.5pt; margin: 0;">
          Pure business models: <code>CryptoOpportunity</code>, <code>Direction</code>, <code>SetupLabel</code>, <code>Candle</code>, <code>IndicatorSnapshot</code>, and <code>ChecklistVerification</code>. Zero external dependencies.
        </p>
      </div>
      <div class="card">
        <div class="card-header">2. Application Core</div>
        <p style="font-size: 7.5pt; margin: 0;">
          <code>CryptoOpportunityScreener</code> and <code>AlgorithmicTradingSystem</code> orchestrate multi-factor scoring, Wyckoff classification, and execution bracket construction.
        </p>
      </div>
      <div class="card">
        <div class="card-header">3. Adapters & API</div>
        <p style="font-size: 7.5pt; margin: 0;">
          Binance USD-M client, CCXT bridge, DeFiLlama stablecoin telemetry, Alternative.me Fear & Greed API, and high-performance FastAPI asynchronous REST/WSS endpoints.
        </p>
      </div>
    </div>

    <h2>3. Universe Selection & Liquidity Filtering Pipeline</h2>
    <p>
      From an unfiltered raw universe of 740+ instruments on Binance, the screening pipeline applies rigorous multi-stage hygiene gates before computing quantitative factors:
    </p>

    <table>
      <thead>
        <tr>
          <th style="width: 22%;">Pipeline Stage</th>
          <th style="width: 38%;">Filtering Mechanism</th>
          <th style="width: 40%;">Institutional Rationale</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td><strong>1. Asset Class Gate</strong></td>
          <td>Only USDT and USDC perpetual futures; trading enabled.</td>
          <td>Ensures deep programmatic derivative liquidity and continuous two-sided order books.</td>
        </tr>
        <tr>
          <td><strong>2. Toxic Asset Blacklist</strong></td>
          <td>Excludes gold pegs (<code>PAXG</code>), synthetic commodities (<code>XAU</code>, <code>COPPER</code>), dead coins (<code>WLFI</code>), and stablecoin pairs.</td>
          <td>Eliminates non-crypto volatility profiles and illiquid abandoned project anomalies.</td>
        </tr>
        <tr>
          <td><strong>3. Multiplier & Symbol Hygiene</strong></td>
          <td>Strict ASCII validation; blocks 1000x meme multiplier contracts (<code>1000SHIB</code>, <code>1000LUNC</code>).</td>
          <td>Prevents tick-size math distortion and decimal rounding slippage during execution bracket sizing.</td>
        </tr>
        <tr>
          <td><strong>4. Liquidity & Spread Floor</strong></td>
          <td>Minimum 24h quote volume of <strong>$500,000</strong>; maximum bid-ask spread capped at <strong>50 bps</strong>.</td>
          <td>Eliminates illiquid wash-trading pairs vulnerable to catastrophic stop-loss slippage.</td>
        </tr>
        <tr>
          <td><strong>5. Top ADV Stratification</strong></td>
          <td>Sorts liquid universe descending by 24h volume and selects the <strong>top 160 liquid USD-M instruments</strong>.</td>
          <td>Focuses processing power and derivatives queries on assets with deep institutional participation ($20M to $1.2B ADV).</td>
        </tr>
      </tbody>
    </table>

    <h2>4. Multi-Timeframe Telemetry Pipeline</h2>
    <p>
      For every selected instrument, Atlas concurrently ingests <strong>Daily, 4-Hour, and 1-Hour candle series</strong>, evaluating:
    </p>
    <div class="callout-box">
      <strong>Ingested Signals:</strong> 1H Indicator Snapshot (EMA9/20/50/200, ATR14, MACD, ADX, DMI, Normalized Momentum) &bull; 4H Multi-Week Base Structure (EMA20/50, Keltner Channels, Bollinger Bands, Choppiness Index) &bull; Volume Profile (50-bin VPVR POC, VAH, VAL) &bull; 7-Day / 1-Hour Open Interest Delta &bull; Funding Rate APR &bull; Taker Buy/Sell Aggression.
    </div>

    <div class="page-footer">
      <span>Atlas Institutional Quantitative Platform &bull; System Specification</span>
      <span>Page 1 of 5</span>
    </div>
  </div>

  <!-- ========================================================================= -->
  <!-- PAGE 2: THE QUANTITATIVE LONG STRATEGY                                    -->
  <!-- ========================================================================= -->
  <div class="page-container">
    <div class="doc-header">
      <div>
        <h1 class="doc-title">QUANTITATIVE LONG SCREENING STRATEGY</h1>
        <div class="doc-subtitle">Pre-Breakout Volatility Squeeze & Wyckoff Phase C Accumulation</div>
      </div>
      <div class="doc-meta">
        <div><strong>Direction:</strong> LONG Setups</div>
        <div><strong>Horizon:</strong> Multi-Day Swing (1–5 Days)</div>
        <div><strong>Min Conviction:</strong> 70.0 / 100.0 Floor</div>
      </div>
    </div>

    <h2>1. The Pre-Breakout Philosophy</h2>
    <p>
      Traditional retail screeners alert on tokens <em>after</em> they appear on "Top Gainers" (+20% to +40% daily pumps). By that point, smart money is actively distributing inventory into breakout chasers. The Atlas Long engine operates on the opposite principle: identify <strong>volatility compression</strong>, <strong>supply exhaustion</strong>, and <strong>order-flow absorption</strong> <em>before</em> the volatility expansion occurs.
    </p>

    <h2>2. The 6 Mandatory Quantitative Long Gates</h2>
    <table>
      <thead>
        <tr>
          <th style="width: 20%;">Gate Name</th>
          <th style="width: 35%;">Mathematical Formulation</th>
          <th style="width: 45%;">Institutional Function</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td><strong>Gate 1: Minervini Trend Baseline</strong></td>
          <td><code>Close &gt;= 4H_EMA50 * 0.985</code><br><code>4H_EMA20 &gt;= 4H_EMA50 * 0.975</code></td>
          <td>Mandates Stage 1 accumulation or Stage 2 markup. Eliminates Stage 4 downtrends and falling knives.</td>
        </tr>
        <tr>
          <td><strong>Gate 2: Anti-Chasing 2.0</strong></td>
          <td><code>Excursion_72h &lt;= 36%</code><br><code>Ret_24h in [-8%, +14%]</code><br><code>RSI_1H &lt;= 76 &amp; RSI_4H &lt;= 70</code></td>
          <td>Disqualifies parabolic blow-off pumps and post-pump exhaustion wicks. Ensures price is tightly coiled.</td>
        </tr>
        <tr>
          <td><strong>Gate 3: Base Duration &amp; Ceiling Proximity</strong></td>
          <td><code>Range_Height_30d &lt;= 65%</code><br><code>Dist_to_Ceiling in [-3%, +35%]</code></td>
          <td>Verifies multi-week consolidation base. Price must be resting beneath resistance, ready for expansion.</td>
        </tr>
        <tr>
          <td><strong>Gate 4: Value Area Acceptance</strong></td>
          <td><code>Close &gt;= 30D_POC * 0.975</code><br><code>Value_Area_Pos &gt;= 25%</code></td>
          <td>Confirms price is accepted above the 30-day Point of Control. Rejects tokens trading beneath heavy overhead volume.</td>
        </tr>
        <tr>
          <td><strong>Gate 5: Volatility Compression</strong></td>
          <td><code>BB_Inside_KC == True</code> or<br><code>Bandwidth &lt; 28% (Ideal &lt; 10%)</code></td>
          <td>Identifies John Carter Squeeze geometry: Bollinger Bands contracting completely inside Keltner Channels.</td>
        </tr>
        <tr>
          <td><strong>Gate 6: S/R Breakout Resistance Gate</strong></td>
          <td><code>if Dist_to_Ceiling &lt;= 4.5%:</code><br><code>&nbsp;&nbsp;OI_7d &gt;= -1% &amp; RVOL &gt;= 1.15x</code></td>
          <td><strong>Critical Bull-Trap Filter:</strong> If price tests ceiling resistance while OI is bleeding and volume is flat, it is rejected immediately. Breakouts require expanding OI.</td>
        </tr>
      </tbody>
    </table>

    <h2>3. Long Setup Archetypes</h2>
    <div class="grid-2">
      <div class="card">
        <div class="card-header"><span class="badge badge-long">COIL_SPRING_LONG</span> Wyckoff Phase C</div>
        <p style="font-size: 7.5pt; margin: 0;">
          Price conducts a terminal shakeout/spring test below support, rapidly reclaims the 4H EMA50 / POC shelf, with down-volume drying up (VCR &le; 0.90x).
        </p>
      </div>
      <div class="card">
        <div class="card-header"><span class="badge badge-long">BREAKOUT_LONG</span> Volume Expansion</div>
        <p style="font-size: 7.5pt; margin: 0;">
          Price breaks above local swing pivot / VAH ceiling with relative volume expansion (&ge; 1.25x), positive normalized momentum, and expanding open interest.
        </p>
      </div>
      <div class="card">
        <div class="card-header"><span class="badge badge-long">MOMENTUM_EMERGING_LONG</span> Residual Alpha</div>
        <p style="font-size: 7.5pt; margin: 0;">
          Early phase regime shift with strong positive BTC-residual alpha (&ge; +3% over 24h), bullish MACD histogram expansion, and aggressive taker buying (&gt;1.15x).
        </p>
      </div>
      <div class="card">
        <div class="card-header"><span class="badge badge-long">OI_ACCUMULATION_LONG</span> Smart Money Absorption</div>
        <p style="font-size: 7.5pt; margin: 0;">
          Continuous institutional accumulation characterized by massive 7-day open interest expansion (+10% to +60%) while price remains compressed within a tight base.
        </p>
      </div>
    </div>

    <h2>4. Execution Bracket Construction (Asymmetric R:R &gt; 2.5:1)</h2>
    <p>
      Every long candidate is stamped with a deterministic 5-level execution bracket:
    </p>
    <ul>
      <li><strong>Retest Entry (Limit):</strong> Anchored at the 4H EMA50 / POC support shelf (<code>Primary_Shelf + 0.15 * ATR</code>).</li>
      <li><strong>Breakout Trigger (Stop-Buy):</strong> Placed immediately above local compression pivot resistance (<code>Resistance + 0.05 * ATR</code>).</li>
      <li><strong>Invalidation Stop Loss:</strong> Placed structurally below the recent 4H swing low shelf (<code>Support - 0.35 * ATR</code>).</li>
      <li><strong>Multi-Tier Targets:</strong> <strong>Target 1:</strong> 30d High / VAH ceiling &bull; <strong>Target 2:</strong> Fibonacci 1.618 expansion &bull; <strong>Target 3:</strong> 2.618 runner.</li>
    </ul>

    <div class="page-footer">
      <span>Atlas Institutional Quantitative Platform &bull; System Specification</span>
      <span>Page 2 of 5</span>
    </div>
  </div>

  <!-- ========================================================================= -->
  <!-- PAGE 3: THE QUANTITATIVE SHORT STRATEGY                                   -->
  <!-- ========================================================================= -->
  <div class="page-container">
    <div class="doc-header">
      <div>
        <h1 class="doc-title">QUANTITATIVE SHORT SCREENING STRATEGY</h1>
        <div class="doc-subtitle">Vertical Run-Up Blow-Offs, Wyckoff UTAD & Upper-Range Distribution</div>
      </div>
      <div class="doc-meta">
        <div><strong>Direction:</strong> SHORT Setups</div>
        <div><strong>Horizon:</strong> Multi-Day Swing (1–5 Days)</div>
        <div><strong>Min Conviction:</strong> 70.0 / 100.0 Floor</div>
      </div>
    </div>

    <h2>1. The Short Engine Philosophy: Never Short Support Floors</h2>
    <p>
      A common flaw in algorithmic screeners is shorting assets that have already suffered severe multi-week downtrends and are grinding into support floors (e.g. TRB, AXS, HBAR at range lows). These setups offer poor risk/reward and suffer vicious short-squeeze spikes. The Atlas Short engine enforces that <strong>shorts are sold HIGH</strong>—specifically targeting overextended assets in the <strong>upper 55% to 100% of their 30-day range following a confirmed vertical run-up</strong> where late retail buyers are trapped.
    </p>

    <h2>2. The 5 Mandatory Quantitative Short Gates</h2>
    <table>
      <thead>
        <tr>
          <th style="width: 22%;">Gate Name</th>
          <th style="width: 35%;">Mathematical Formulation</th>
          <th style="width: 43%;">Institutional Function</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td><strong>Gate 1: Mandatory Macro Run-Up</strong></td>
          <td><code>Range_Height &gt;= 16%</code> or<br><code>Ret_7d &gt;= 10%</code> or <code>Ret_14d &gt;= 14%</code> or<br><code>(Close - EMA50_4H) &gt;= +8%</code></td>
          <td><strong>Distribution requires prior markup.</strong> Eliminates flat, lifeless, range-bound tokens that never experienced speculative vertical expansion.</td>
        </tr>
        <tr>
          <td><strong>Gate 2: Strict Upper-Range Positioning</strong></td>
          <td><code>Pos_in_Range = (Close - Low_30d) / (High_30d - Low_30d)</code><br><strong>Mandate: <code>Pos_in_Range &gt;= 0.55</code></strong></td>
          <td>Guarantees shorts are initiated strictly in the upper half of the range near distribution ceilings. Rejects bottom-chopping tokens.</td>
        </tr>
        <tr>
          <td><strong>Gate 3: Anti-Chasing &amp; Oversold Filter</strong></td>
          <td><code>Ret_24h &gt;= -18% &amp; Ret_7d &gt;= -35%</code><br><code>RSI_1H &gt;= 28 &amp; RSI_4H &gt;= 38</code></td>
          <td>Prevents shorting into deeply exhausted bottoms or selling into capitulation wicks where mean-reversion bounces occur.</td>
        </tr>
        <tr>
          <td><strong>Gate 4: Multi-Timeframe Bearish Divergence</strong></td>
          <td><code>RSI_4H &lt; Prev_RSI_4H</code> on Higher High<br>4H MACD Histogram Bearish Divergence</td>
          <td>Detects institutional exhaustion: price prints a nominal higher high, but momentum indicators decisively print lower peaks.</td>
        </tr>
        <tr>
          <td><strong>Gate 5: Trapped Longs &amp; OI Crowding</strong></td>
          <td><code>Funding_Rate &gt; 0</code> (Longs paying)<br>Large positive 7d OI (+15% to +200%)</td>
          <td>Identifies overcrowded retail longs paying funding fees into smart-money distribution wicks, providing fuel for cascading liquidations.</td>
        </tr>
      </tbody>
    </table>

    <h2>3. Short Setup Archetypes</h2>
    <div class="grid-3">
      <div class="card">
        <div class="card-header"><span class="badge badge-short">UTAD_DIVERGENCE_SHORT</span> Phase C</div>
        <p style="font-size: 7.5pt; margin: 0;">
          <strong>Wyckoff Upthrust After Distribution:</strong> Price sweeps the 30-day high with buying climax wicks, followed by confirmed 4H/Daily bearish divergence and immediate rejection back inside the range.
        </p>
      </div>
      <div class="card">
        <div class="card-header"><span class="badge badge-short">DISTRIBUTION_SHORT</span> Macro Rollover</div>
        <p style="font-size: 7.5pt; margin: 0;">
          <strong>Parabolic Markup Breakdown:</strong> Multi-week runners (&gt;75% to 240% expansion) that coil below the 4H EMA50 and 30-day POC, displaying heavy seller volume dominance and structural momentum decay.
        </p>
      </div>
      <div class="card">
        <div class="card-header"><span class="badge badge-short">EXHAUSTION_REVERSAL_SHORT</span> Climax</div>
        <p style="font-size: 7.5pt; margin: 0;">
          <strong>Multi-Timeframe Overbought Blow-Off:</strong> Assets where Weekly and Daily RSI exceed 75–84 with massive open interest surges (+50% to +216% 7d OI), ready for an immediate mean-reversion cascade.
        </p>
      </div>
    </div>

    <h2>4. Short Execution Hierarchy &amp; Invalidation Rules</h2>
    <p>
      Short execution mandates strict geometric discipline to eliminate squeeze exposure:
    </p>
    <div class="formula-box">
      EXECUTION HIERARCHY: Invalidation Stop &gt; Retest Entry (EMA50/POC) &gt;= Current Price &gt; Breakdown Trigger &gt; Downside Targets
    </div>
    <ul>
      <li><strong>Retest Entry (Sell-Limit):</strong> Set at the 4H EMA50 / POC retest shelf (<code>POC_Price - 0.15 * ATR</code>) for maximum R:R (&gt;5.5:1).</li>
      <li><strong>Breakdown Trigger (Stop-Market):</strong> Set below the local consolidation floor (<code>Local_Low - 0.05 * ATR</code>) for momentum capture.</li>
      <li><strong>Invalidation Stop:</strong> Placed structurally above the local distribution swing high / VAH ceiling (<code>Resistance + 0.35 * ATR</code>).</li>
    </ul>

    <div class="page-footer">
      <span>Atlas Institutional Quantitative Platform &bull; System Specification</span>
      <span>Page 3 of 5</span>
    </div>
  </div>

  <!-- ========================================================================= -->
  <!-- PAGE 4: SCORING, CONVICTION & MATHEMATICAL EXPECTANCY                     -->
  <!-- ========================================================================= -->
  <div class="page-container">
    <div class="doc-header">
      <div>
        <h1 class="doc-title">SCORING ENGINE & MATHEMATICAL EXPECTANCY</h1>
        <div class="doc-subtitle">Multi-Factor Regression, Composite Conviction & EV Optimization</div>
      </div>
      <div class="doc-meta">
        <div><strong>Model:</strong> Non-Linear Factor Weighting</div>
        <div><strong>Metric:</strong> Expected Value in R ($EV_R$)</div>
        <div><strong>Threshold:</strong> 5-Point Verification Matrix</div>
      </div>
    </div>

    <h2>1. Multi-Factor Regression Formula</h2>
    <p>
      Every candidate passing the hard structural gates is evaluated through a non-linear composite scoring engine (0.0 to 100.0) across five orthogonal quantitative dimensions:
    </p>

    <div class="formula-box">
      SCORE = 0.25 * Trend_Score + 0.25 * Momentum_Score + 0.25 * Derivatives_Score + 0.15 * Alpha_Score + 0.10 * Volume_Score
    </div>

    <table>
      <thead>
        <tr>
          <th style="width: 25%;">Factor Dimension</th>
          <th style="width: 15%;">Weight</th>
          <th style="width: 60%;">Underlying Quantitative Metric Inputs</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td><strong>Trend Architecture</strong></td>
          <td>25%</td>
          <td>4H EMA20 vs EMA50 alignment, ADX trend strength (&ge;25), DMI directionality, Minervini stage positioning.</td>
        </tr>
        <tr>
          <td><strong>Momentum &amp; Squeeze</strong></td>
          <td>25%</td>
          <td>Bollinger Bandwidth (&lt;10%), BB nested inside KC, MACD histogram velocity, normalized price momentum.</td>
        </tr>
        <tr>
          <td><strong>Derivatives Positioning</strong></td>
          <td>25%</td>
          <td>7-day &amp; 1-hour Open Interest velocity, contrarian funding rate arbitrage, taker buy/sell ratio, order book depth imbalance.</td>
        </tr>
        <tr>
          <td><strong>BTC-Residual Alpha</strong></td>
          <td>15%</td>
          <td>Residual alpha vs Bitcoin over 6h, 24h, and 7d horizons; beta adjustment to isolate idiosyncratic token momentum.</td>
        </tr>
        <tr>
          <td><strong>Volume Microstructure</strong></td>
          <td>10%</td>
          <td>Relative volume surge (RVOL &ge; 1.25x), volume contraction ratio (down-bar dry-up), 24h quote volume depth.</td>
        </tr>
      </tbody>
    </table>

    <h2>2. Composite Conviction Ranking</h2>
    <p>
      To prevent ranking bias between high-win-rate conservative setups and high-R:R aggressive setups, Atlas ranks candidates by <strong>Composite Conviction</strong>:
    </p>
    <div class="formula-box">
      CONVICTION = 0.40 * Estimated_Win_Rate_Pct + 0.60 * Quantitative_Score
    </div>
    <p>
      The system enforces a <strong>70.0 minimum score threshold</strong>. Low-conviction noise (scores 40–60) is filtered out before reaching the operator dashboard.
    </p>

    <h2>3. The Mathematical Expectancy Model ($EV_R$)</h2>
    <p>
      Trade viability is judged not by win rate alone, but by mathematical expected value per unit of risk ($R$). The platform calculates expected return using:
    </p>
    <div class="formula-box">
      EV_R = [ P_win * (0.60 * R_T1 + 0.40 * R_T2) ] - [ (1 - P_win) * 1.0 ]
    </div>
    <p>
      Where $R_{T1}$ and $R_{T2}$ represent the risk-adjusted payoff at Target 1 and Target 2, assuming a 1.0R loss on invalidation. Trades must achieve $EV_R \ge +1.50R$ to earn a Grade A recommendation.
    </p>

    <h2>4. The 5-Point Institutional Checklist</h2>
    <p>
      Every candidate must undergo a deterministic 5-point verification audit displayed as badges on the trader dashboard:
    </p>
    <div class="grid-2">
      <div class="card">
        <div class="card-header"><span class="badge badge-gold">1. Value Acceptance / Breakdown</span></div>
        <p style="font-size: 7.5pt; margin: 0;">Verified trading above 30d POC (Long) or rejected below 30d POC / 4H EMA50 (Short).</p>
      </div>
      <div class="card">
        <div class="card-header"><span class="badge badge-gold">2. Volatility Compression / UTAD</span></div>
        <p style="font-size: 7.5pt; margin: 0;">Verified BB inside KC squeeze (Long) or confirmed 4H/Daily bearish divergence (Short).</p>
      </div>
      <div class="card">
        <div class="card-header"><span class="badge badge-gold">3. Momentum &amp; Trend Alignment</span></div>
        <p style="font-size: 7.5pt; margin: 0;">4H EMA20/50 alignment and MACD histogram acceleration in trade direction.</p>
      </div>
      <div class="card">
        <div class="card-header"><span class="badge badge-gold">4. Volume Contraction / Exhaustion</span></div>
        <p style="font-size: 7.5pt; margin: 0;">Volume drying up on pullbacks (VCR &le; 1.0x) or volume surge on breakout trigger.</p>
      </div>
      <div class="card" style="grid-column: span 2;">
        <div class="card-header"><span class="badge badge-gold">5. Smart Money Order Flow &amp; Derivatives Positioning</span></div>
        <p style="font-size: 7.5pt; margin: 0;">Non-negative 7d OI on long breakouts; trapped overleveraged retail longs (+20% to +200% OI, positive funding) on short setups.</p>
      </div>
    </div>

    <div class="page-footer">
      <span>Atlas Institutional Quantitative Platform &bull; System Specification</span>
      <span>Page 4 of 5</span>
    </div>
  </div>

  <!-- ========================================================================= -->
  <!-- PAGE 5: MACRO INTELLIGENCE & OPERATIONAL TOPOLOGY                         -->
  <!-- ========================================================================= -->
  <div class="page-container">
    <div class="doc-header">
      <div>
        <h1 class="doc-title">MACRO INTELLIGENCE & OPERATIONAL TOPOLOGY</h1>
        <div class="doc-subtitle">Market Overextension Architecture, Real-Time Telemetry & System Resilience</div>
      </div>
      <div class="doc-meta">
        <div><strong>Service:</strong> Macro Liquidity &amp; Radar Port</div>
        <div><strong>Cadence:</strong> 8-Hour UTC Funding Aligned Cron</div>
        <div><strong>Stack:</strong> Python 3.13 / FastAPI / Next.js 16</div>
      </div>
    </div>

    <h2>1. Macro Overextension Architecture (MOEA)</h2>
    <p>
      The platform incorporates a Macro Market Intelligence engine to determine overall market regimes and compute the probability of a market-wide sell-off or cool-off. The <strong>Composite Overextension Index (COI: 0–100)</strong> unifies four distinct market pillars:
    </p>

    <div class="formula-box">
      COI = 0.35 * Benchmark_Excursion + 0.30 * Leverage_Crowding + 0.20 * Market_Breadth + 0.15 * Stablecoin_Liquidity
    </div>

    <div class="grid-2">
      <div class="card">
        <div class="card-header">1. Benchmark Excursion (35%)</div>
        <p style="font-size: 7.5pt; margin: 0;">
          BTC &amp; ETH distance from Daily EMA50 and Daily Bollinger Upper Band, plus 14-day RSI excursion.
        </p>
      </div>
      <div class="card">
        <div class="card-header">2. Leverage &amp; Funding Crowding (30%)</div>
        <p style="font-size: 7.5pt; margin: 0;">
          Annualized perpetual funding rates across the top 20 perpetuals. Rates &gt; 25% APR indicate extreme long vulnerability.
        </p>
      </div>
      <div class="card">
        <div class="card-header">3. Market Breadth &amp; Euphoria (20%)</div>
        <p style="font-size: 7.5pt; margin: 0;">
          Percentage of altcoins trading above their 20-day moving average combined with Alternative.me Fear &amp; Greed index.
        </p>
      </div>
      <div class="card">
        <div class="card-header">4. Stablecoin Fuel vs Leverage (15%)</div>
        <p style="font-size: 7.5pt; margin: 0;">
          7-day net issuance flow of USDT/USDC via DeFiLlama. Divergence between price gains and stablecoin outflows signals synthetic leverage.
        </p>
      </div>
    </div>

    <h2>2. Automated 8-Hour Cron Service Aligned with Funding Boundaries</h2>
    <p>
      The screener background daemon executes every 8 hours, synchronized precisely with global Binance USD-M funding settlement boundaries:
    </p>
    <ul>
      <li><strong>Settlement Boundaries:</strong> <strong>00:00 UTC</strong> &bull; <strong>08:00 UTC</strong> &bull; <strong>16:00 UTC</strong> (+30 second settlement grace).</li>
      <li><strong>Operational Rationale:</strong> Institutional funding fee payments reset derivatives positioning at these exact intervals, creating the cleanest multi-day directional setups.</li>
      <li><strong>Instant Client Caching:</strong> Results are cached to <code>data/screener_latest.json</code> and served in &lt;10ms to trader dashboards.</li>
    </ul>

    <h2>3. Production Infrastructure &amp; Resilience Matrix</h2>
    <table style="margin-top: 6px;">
      <thead>
        <tr>
          <th style="width: 25%;">Infrastructure Layer</th>
          <th style="width: 35%;">Technology Implementation</th>
          <th style="width: 40%;">Fault Isolation Guarantee</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td><strong>Backend Core</strong></td>
          <td>Python 3.13 / FastAPI asynchronous runtime with Uvicorn.</td>
          <td>Non-blocking async worker pool with rate-limit budgeting (semaphore = 10).</td>
        </tr>
        <tr>
          <td><strong>Frontend Portal</strong></td>
          <td>Next.js 16 (Turbopack) / React 19 / Canvas Charting.</td>
          <td>Zero-lag HTML5 Canvas chart engine; virtualized candidate grids with zero layout shift.</td>
        </tr>
        <tr>
          <td><strong>Real-Time Ticker</strong></td>
          <td>Combined Binance WebSocket Multi-Ticker Stream.</td>
          <td>Instant sub-second price updates with automatic REST fallback polling upon connection loss.</td>
        </tr>
        <tr>
          <td><strong>External APIs</strong></td>
          <td>Async fallback wrappers for DeFiLlama &amp; CoinGecko.</td>
          <td>Graceful degradation to cached baseline metrics if external third-party endpoints timeout.</td>
        </tr>
      </tbody>
    </table>

    <div class="page-footer">
      <span>Atlas Institutional Quantitative Platform &bull; System Specification</span>
      <span>Page 5 of 5</span>
    </div>
  </div>

</body>
</html>
"""

html_path = "/tmp/atlas_screener_architecture.html"
pdf_path = "/Users/jithinjacob/Desktop/trdindicator/Atlas_Institutional_Screener_Architecture.pdf"
artifact_pdf_path = "/Users/jithinjacob/.gemini/antigravity-ide/brain/71d33a7d-01ff-46a8-b776-64991cec576f/Atlas_Institutional_Screener_Architecture.pdf"

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
    print(f"PDF generation failed: {result.stderr}")
