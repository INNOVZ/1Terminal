from __future__ import annotations

import json

from algotrader.domain.crypto_screener_models import CryptoOpportunity
from algotrader.domain.models import Candle


def generate_chart_html(
    opportunities: list[CryptoOpportunity],
    candles_by_symbol: dict[str, list[Candle]],
    active_symbol: str | None = None,
) -> str:
    """Generate a self-contained, interactive TradingView Lightweight Charts verification dashboard."""
    if not opportunities:
        return "<html><body><h1>No opportunities to display</h1></body></html>"

    selected_symbol = active_symbol or opportunities[0].instrument.id.symbol

    # Prepare symbols payload
    opps_data = []
    for opp in opportunities:
        sym = opp.instrument.id.symbol
        candles = candles_by_symbol.get(sym, [])
        candle_dicts = [
            {
                "time": int(c.opened_at.timestamp()),
                "open": float(c.open),
                "high": float(c.high),
                "low": float(c.low),
                "close": float(c.close),
                "volume": float(c.volume),
            }
            for c in candles
        ]

        opps_data.append({
            "symbol": sym,
            "direction": opp.direction.value,
            "setup_label": opp.setup_label.value,
            "regime": opp.regime.value,
            "score": float(opp.score),
            "current_price": float(opp.current_price),
            "suggested_entry": float(opp.suggested_entry),
            "pullback_entry": float(opp.pullback_entry) if opp.pullback_entry is not None else float(opp.suggested_entry),
            "breakout_trigger": float(opp.breakout_trigger) if opp.breakout_trigger is not None else None,
            "suggested_stop": float(opp.suggested_stop),
            "targets": [float(t) for t in opp.suggested_targets],
            "risk_reward_ratio": float(opp.risk_reward_ratio),
            "poc_price": float(opp.poc_price) if opp.poc_price is not None else None,
            "vah_price": float(opp.vah_price) if opp.vah_price is not None else None,
            "val_price": float(opp.val_price) if opp.val_price is not None else None,
            "funding_rate_bps": float(opp.funding_rate * 10000) if opp.funding_rate is not None else 0.0,
            "oi_change_1h_pct": float(opp.oi_change_1h * 100) if opp.oi_change_1h is not None else 0.0,
            "oi_change_7d_pct": float(opp.oi_change_7d * 100) if opp.oi_change_7d is not None else 0.0,
            "volume_contraction_ratio": float(opp.volume_contraction_ratio) if opp.volume_contraction_ratio is not None else 1.0,
            "taker_ratio": float(opp.taker_ratio) if opp.taker_ratio is not None else 1.0,
            "checklist": [
                {"title": item[0], "passed": item[1], "detail": item[2]}
                for item in opp.checklist
            ],
            "rationale": list(opp.rationale),
            "macro_narrative": opp.macro_narrative,
            "candles": candle_dicts,
        })

    payload_json = json.dumps(opps_data)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Atlas Institutional Crypto Screener — Visual Verification Dashboard</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&family=Manrope:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
  <script src="https://unpkg.com/lightweight-charts@4.2.1/dist/lightweight-charts.standalone.production.js"></script>
  <style>
    :root {{
      --bg-dark: #070b12;
      --bg-surface: #0e1422;
      --bg-card: #141c2e;
      --bg-card-hover: #1b263e;
      --card-border: #1e293b;
      --border-subtle: #162032;
      --text-main: #f1f5f9;
      --text-muted: #94a3b8;
      --text-dim: #64748b;
      --accent-green: #10b981;
      --accent-green-glow: rgba(16, 185, 129, 0.2);
      --accent-red: #ef4444;
      --accent-red-glow: rgba(239, 68, 68, 0.2);
      --accent-cyan: #06b6d4;
      --accent-gold: #f59e0b;
      --accent-purple: #8b5cf6;
      --sidebar-width: 320px;
      --details-width: 330px;
      --header-height: 52px;
      --toolbar-height: 40px;
    }}
    
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background: var(--bg-dark);
      color: var(--text-main);
      font-family: "Manrope", sans-serif;
      height: 100vh;
      width: 100vw;
      overflow: hidden;
      display: flex;
      flex-direction: column;
    }}

    /* Top Navigation Header */
    #top-nav {{
      height: var(--header-height);
      background: var(--bg-surface);
      border-bottom: 1px solid var(--card-border);
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0 16px;
      z-index: 50;
      flex-shrink: 0;
    }}
    .nav-brand {{
      display: flex;
      align-items: center;
      gap: 10px;
    }}
    .brand-icon {{
      font-size: 18px;
      color: var(--accent-gold);
    }}
    .brand-text {{
      display: flex;
      flex-direction: column;
    }}
    .brand-title {{
      font-size: 13px;
      font-weight: 800;
      letter-spacing: 0.8px;
      color: #fff;
    }}
    .brand-subtitle {{
      font-size: 10px;
      color: var(--text-dim);
      font-weight: 600;
      letter-spacing: 0.3px;
    }}
    .badge-venue {{
      background: rgba(16, 185, 129, 0.15);
      color: var(--accent-green);
      font-size: 10px;
      font-weight: 700;
      padding: 2px 7px;
      border-radius: 4px;
      border: 1px solid rgba(16, 185, 129, 0.3);
    }}

    /* Active Symbol Pill in Header */
    .header-active-pill {{
      display: flex;
      align-items: center;
      gap: 12px;
      background: var(--bg-card);
      border: 1px solid var(--card-border);
      padding: 5px 14px;
      border-radius: 20px;
    }}
    .pill-symbol {{
      font-size: 16px;
      font-weight: 800;
      color: #fff;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    }}
    .pill-price {{
      font-size: 14px;
      font-weight: 700;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    }}
    .pill-setup {{
      font-size: 10px;
      font-weight: 700;
      color: var(--accent-cyan);
      background: rgba(6, 182, 212, 0.15);
      padding: 2px 8px;
      border-radius: 10px;
      text-transform: uppercase;
    }}
    .pill-score {{
      font-size: 11px;
      font-weight: 700;
      color: #a78bfa;
      background: rgba(139, 92, 246, 0.15);
      padding: 2px 8px;
      border-radius: 10px;
    }}

    /* Viewport & Layout Toggle Controls */
    .header-controls {{
      display: flex;
      align-items: center;
      gap: 8px;
    }}
    .ctrl-btn {{
      background: var(--bg-card);
      border: 1px solid var(--card-border);
      color: var(--text-muted);
      padding: 6px 12px;
      border-radius: 6px;
      font-size: 12px;
      font-weight: 600;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 6px;
      transition: all 0.15s ease;
      user-select: none;
    }}
    .ctrl-btn:hover {{
      background: var(--bg-card-hover);
      color: #fff;
      border-color: var(--text-dim);
    }}
    .ctrl-btn.active {{
      background: rgba(6, 182, 212, 0.15);
      color: var(--accent-cyan);
      border-color: var(--accent-cyan);
    }}
    .ctrl-btn.btn-zen {{
      background: rgba(16, 185, 129, 0.15);
      color: var(--accent-green);
      border-color: rgba(16, 185, 129, 0.4);
    }}
    .ctrl-btn.btn-zen:hover {{
      background: rgba(16, 185, 129, 0.25);
      box-shadow: 0 0 10px var(--accent-green-glow);
    }}

    /* Workspace Flex Layout */
    #workspace {{
      flex: 1;
      display: flex;
      height: calc(100vh - var(--header-height));
      overflow: hidden;
      position: relative;
    }}

    /* Left Candidates Sidebar */
    #sidebar {{
      width: var(--sidebar-width);
      min-width: var(--sidebar-width);
      background: var(--bg-surface);
      border-right: 1px solid var(--card-border);
      display: flex;
      flex-direction: column;
      transition: width 0.25s cubic-bezier(0.4, 0, 0.2, 1), min-width 0.25s cubic-bezier(0.4, 0, 0.2, 1), opacity 0.2s ease, transform 0.25s ease;
      flex-shrink: 0;
      overflow: hidden;
    }}
    body.sidebar-hidden #sidebar {{
      width: 0 !important;
      min-width: 0 !important;
      opacity: 0;
      pointer-events: none;
      transform: translateX(-10px);
    }}
    .sidebar-search-box {{
      padding: 10px 12px;
      border-bottom: 1px solid var(--border-subtle);
    }}
    .cand-search-input {{
      width: 100%;
      background: var(--bg-card);
      border: 1px solid var(--card-border);
      color: #fff;
      font-size: 12px;
      padding: 7px 10px;
      border-radius: 6px;
      outline: none;
      transition: border-color 0.15s ease;
    }}
    .cand-search-input:focus {{
      border-color: var(--accent-cyan);
    }}
    .cand-filter-bar {{
      display: flex;
      gap: 6px;
      padding: 8px 12px;
      border-bottom: 1px solid var(--border-subtle);
      background: #0b101c;
    }}
    .filter-chip {{
      background: transparent;
      border: 1px solid var(--card-border);
      color: var(--text-dim);
      font-size: 10px;
      font-weight: 700;
      padding: 3px 8px;
      border-radius: 4px;
      cursor: pointer;
      transition: all 0.15s ease;
    }}
    .filter-chip:hover, .filter-chip.active {{
      background: var(--bg-card);
      color: var(--accent-cyan);
      border-color: var(--accent-cyan);
    }}
    .opp-list {{
      flex: 1;
      overflow-y: auto;
      padding: 10px;
      display: flex;
      flex-direction: column;
      gap: 6px;
    }}
    .opp-card {{
      background: var(--bg-card);
      border: 1px solid var(--card-border);
      border-radius: 8px;
      padding: 10px 12px;
      cursor: pointer;
      transition: all 0.15s ease;
    }}
    .opp-card:hover {{
      background: var(--bg-card-hover);
      border-color: var(--accent-cyan);
      transform: translateY(-1px);
    }}
    .opp-card.active {{
      background: #172338;
      border-color: var(--accent-green);
      box-shadow: 0 0 10px var(--accent-green-glow);
    }}
    .opp-card-top {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 4px;
    }}
    .opp-symbol {{
      font-size: 14px;
      font-weight: 800;
      color: #fff;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    }}
    .opp-score {{
      background: rgba(139, 92, 246, 0.15);
      color: #a78bfa;
      font-size: 10px;
      font-weight: 700;
      padding: 2px 6px;
      border-radius: 4px;
    }}
    .opp-setup {{
      font-size: 10px;
      color: var(--accent-cyan);
      font-weight: 700;
      text-transform: uppercase;
      margin-bottom: 5px;
      letter-spacing: 0.2px;
    }}
    .opp-stats {{
      display: flex;
      justify-content: space-between;
      font-size: 11px;
      color: var(--text-muted);
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    }}

    /* Center Main Workspace */
    #main-content {{
      flex: 1;
      display: flex;
      flex-direction: column;
      height: 100%;
      min-width: 0;
      background: var(--bg-dark);
      position: relative;
    }}

    /* Chart Toolbar & Crosshair HUD */
    #chart-toolbar {{
      height: var(--toolbar-height);
      background: #0a0f1d;
      border-bottom: 1px solid var(--border-subtle);
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0 16px;
      flex-shrink: 0;
    }}
    #ohlcv-hud {{
      display: flex;
      align-items: center;
      gap: 14px;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 11px;
      color: var(--text-muted);
    }}
    .hud-time {{
      color: var(--text-dim);
      font-size: 11px;
    }}
    .hud-label {{
      color: var(--text-dim);
      margin-right: 3px;
    }}
    .hud-val {{
      color: var(--text-main);
      font-weight: 700;
    }}
    .toolbar-actions {{
      display: flex;
      align-items: center;
      gap: 12px;
    }}
    .btn-group {{
      display: flex;
      align-items: center;
      background: var(--bg-card);
      border: 1px solid var(--card-border);
      border-radius: 6px;
      overflow: hidden;
    }}
    .tool-btn {{
      background: transparent;
      border: none;
      color: var(--text-muted);
      padding: 4px 9px;
      font-size: 11px;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.15s ease;
      user-select: none;
    }}
    .tool-btn:hover {{
      color: #fff;
      background: var(--bg-card-hover);
    }}
    .tool-btn.active {{
      background: rgba(6, 182, 212, 0.2);
      color: var(--accent-cyan);
    }}

    /* Metrics Strip (Sleek Horizontal Pills) */
    #metrics-strip {{
      background: #0d1322;
      border-bottom: 1px solid var(--border-subtle);
      padding: 8px 16px;
      display: flex;
      align-items: center;
      gap: 16px;
      overflow-x: auto;
      flex-shrink: 0;
    }}
    .metric-chip {{
      display: flex;
      flex-direction: column;
      gap: 2px;
      flex-shrink: 0;
    }}
    .chip-label {{
      font-size: 9px;
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: var(--text-dim);
    }}
    .chip-val {{
      font-size: 13px;
      font-weight: 700;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      color: var(--text-main);
    }}
    .chip-val.gold {{ color: var(--accent-gold); }}
    .chip-val.cyan {{ color: var(--accent-cyan); }}
    .chip-val.green {{ color: var(--accent-green); }}
    .chip-val.red {{ color: var(--accent-red); }}
    .chip-val.purple {{ color: #a78bfa; }}

    /* Chart Viewport (Maximized Vision) */
    #chart-viewport {{
      flex: 1;
      position: relative;
      width: 100%;
      height: 100%;
      min-height: 0;
      overflow: hidden;
    }}
    #chart-container {{
      width: 100%;
      height: 100%;
      position: absolute;
      top: 0;
      left: 0;
      right: 0;
      bottom: 0;
    }}

    /* Right Details & Verification Panel */
    #details-panel {{
      width: var(--details-width);
      min-width: var(--details-width);
      background: var(--bg-surface);
      border-left: 1px solid var(--card-border);
      display: flex;
      flex-direction: column;
      transition: width 0.25s cubic-bezier(0.4, 0, 0.2, 1), min-width 0.25s cubic-bezier(0.4, 0, 0.2, 1), opacity 0.2s ease, transform 0.25s ease;
      flex-shrink: 0;
      overflow-y: auto;
      padding: 16px;
      gap: 16px;
    }}
    body.details-hidden #details-panel {{
      width: 0 !important;
      min-width: 0 !important;
      opacity: 0;
      pointer-events: none;
      transform: translateX(10px);
      padding: 0;
      border-left: none;
    }}
    .panel-section-title {{
      font-size: 11px;
      font-weight: 800;
      text-transform: uppercase;
      color: var(--text-dim);
      letter-spacing: 0.8px;
      display: flex;
      align-items: center;
      gap: 6px;
    }}
    .check-list {{
      display: flex;
      flex-direction: column;
      gap: 8px;
    }}
    .check-item {{
      background: var(--bg-card);
      border: 1px solid var(--border-subtle);
      border-radius: 6px;
      padding: 10px 12px;
      display: flex;
      align-items: flex-start;
      gap: 10px;
      transition: all 0.15s ease;
    }}
    .check-item.pass {{
      border-color: rgba(16, 185, 129, 0.35);
      background: rgba(16, 185, 129, 0.05);
    }}
    .check-item.pass .check-icon {{
      color: var(--accent-green);
    }}
    .check-item.fail {{
      border-color: rgba(239, 68, 68, 0.4);
      background: rgba(239, 68, 68, 0.08);
    }}
    .check-item.fail .check-icon {{
      color: var(--accent-red);
    }}
    .check-item.fail .check-label {{
      color: #fca5a5;
    }}
    .check-item.fail .check-desc {{
      color: #f87171;
    }}
    .check-icon {{
      font-size: 14px;
      font-weight: 800;
      color: var(--accent-green);
    }}
    .check-content {{
      display: flex;
      flex-direction: column;
      gap: 2px;
    }}
    .check-label {{
      font-size: 12px;
      font-weight: 700;
      color: #fff;
    }}
    .check-desc {{
      font-size: 11px;
      color: var(--text-muted);
      line-height: 1.4;
    }}

    /* Execution Plan Card */
    .bracket-box {{
      background: var(--bg-card);
      border: 1px solid var(--border-subtle);
      border-radius: 6px;
      padding: 12px;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }}
    .bracket-row {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 12px;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    }}
    .bracket-label {{
      color: var(--text-muted);
    }}
    .bracket-val-stop {{ color: var(--accent-red); font-weight: 700; }}
    .bracket-val-entry {{ color: #fff; font-weight: 700; }}
    .bracket-val-target {{ color: var(--accent-green); font-weight: 700; }}

    /* Quantitative Rationale Box */
    .rationale-box {{
      background: var(--bg-card);
      border: 1px solid var(--border-subtle);
      border-radius: 6px;
      padding: 12px;
      font-size: 11px;
      line-height: 1.6;
      color: var(--text-muted);
    }}

    /* Floating Quick Expand Buttons when Sidebars are Closed */
    .floating-expand-btn {{
      position: absolute;
      top: 50%;
      transform: translateY(-50%);
      background: var(--bg-card);
      border: 1px solid var(--card-border);
      color: var(--text-muted);
      width: 22px;
      height: 48px;
      display: none;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      z-index: 40;
      border-radius: 0 6px 6px 0;
      transition: all 0.15s ease;
      font-size: 12px;
    }}
    .floating-expand-btn:hover {{
      background: var(--bg-card-hover);
      color: var(--accent-cyan);
      border-color: var(--accent-cyan);
    }}
    body.sidebar-hidden #expand-left-btn {{
      display: flex;
      left: 0;
    }}
    body.details-hidden #expand-right-btn {{
      display: flex;
      right: 0;
      border-radius: 6px 0 0 6px;
    }}

    /* Keyboard Shortcuts Indicator */
    .kb-shortcut {{
      font-size: 9px;
      background: rgba(255, 255, 255, 0.1);
      padding: 1px 4px;
      border-radius: 3px;
      color: var(--text-muted);
      margin-left: 4px;
    }}
  </style>
</head>
<body>

  <!-- Top Navigation Header -->
  <header id="top-nav">
    <div class="nav-brand">
      <span class="brand-icon">⚡</span>
      <div class="brand-text">
        <span class="brand-title">ATLAS RADAR</span>
        <span class="brand-subtitle">PRE-BREAKOUT RADAR</span>
      </div>
      <span class="badge-venue">BINANCE USD-M</span>
    </div>

    <!-- Active Candidate Pill -->
    <div class="header-active-pill">
      <span class="pill-symbol" id="top-symbol">---</span>
      <span class="pill-price" id="top-price">---</span>
      <span class="pill-setup" id="top-setup">---</span>
      <span class="pill-score" id="top-score">---</span>
    </div>

    <!-- Layout & Vision Controls -->
    <div class="header-controls">
      <button class="ctrl-btn active" id="btn-toggle-sidebar" title="Toggle Candidates Sidebar (Shortcut: [ )">
        <span>◨</span> Candidates <span class="kb-shortcut">[</span>
      </button>
      <button class="ctrl-btn btn-zen" id="btn-zen" title="Focus Chart: Maximize Chart Estate (Shortcut: F )">
        <span>⛶</span> Focus Chart <span class="kb-shortcut">F</span>
      </button>
      <button class="ctrl-btn active" id="btn-toggle-details" title="Toggle Trade Details Panel (Shortcut: ] )">
        <span>◧</span> Details <span class="kb-shortcut">]</span>
      </button>
    </div>
  </header>

  <!-- Main Body Workspace -->
  <div id="workspace">
    <!-- Floating Edge Buttons when panels are collapsed -->
    <div class="floating-expand-btn" id="expand-left-btn" title="Expand Candidates">▶</div>
    <div class="floating-expand-btn" id="expand-right-btn" title="Expand Details">◀</div>

    <!-- Left Candidates Sidebar -->
    <aside id="sidebar">
      <div class="sidebar-search-box">
        <input type="text" id="cand-search" class="cand-search-input" placeholder="Search tokens (e.g. TIA, BTC)..." autocomplete="off" />
      </div>
      <div class="cand-filter-bar">
        <button class="filter-chip active" data-filter="all">All (<span id="count-all">0</span>)</button>
        <button class="filter-chip" data-filter="coil">Coils</button>
        <button class="filter-chip" data-filter="oi">OI Accum</button>
      </div>
      <div class="opp-list" id="opp-list"></div>
    </aside>

    <!-- Center Chart Space -->
    <main id="main-content">
      <!-- Toolbar with OHLCV Crosshair and Chart Controls -->
      <div id="chart-toolbar">
        <div id="ohlcv-hud">
          <span class="hud-time" id="hud-time">---</span>
          <span><span class="hud-label">O:</span><span class="hud-val" id="hud-open">--</span></span>
          <span><span class="hud-label">H:</span><span class="hud-val" id="hud-high">--</span></span>
          <span><span class="hud-label">L:</span><span class="hud-val" id="hud-low">--</span></span>
          <span><span class="hud-label">C:</span><span class="hud-val" id="hud-close">--</span></span>
          <span><span class="hud-label">Vol:</span><span class="hud-val" id="hud-vol">--</span></span>
          <span><span class="hud-label">Chg:</span><span class="hud-val" id="hud-chg">--</span></span>
        </div>

        <div class="toolbar-actions">
          <!-- Range Zoom -->
          <div class="btn-group">
            <button class="tool-btn" data-range="3d">3D</button>
            <button class="tool-btn" data-range="7d">7D</button>
            <button class="tool-btn" data-range="14d">14D</button>
            <button class="tool-btn active" data-range="all">All</button>
            <button class="tool-btn" id="btn-fit" title="Fit Entire Base">🔍 Fit</button>
          </div>

          <!-- Technical Overlays Toggles -->
          <div class="btn-group">
            <button class="tool-btn active" id="toggle-emas" title="Toggle EMA 20 (Cyan), EMA 50 (Orange), EMA 200 (Purple)">📈 EMAs</button>
            <button class="tool-btn active" id="toggle-levels" title="Toggle Volume Profile (POC, VAH) & Invalidation Stop">🎯 Levels</button>
            <button class="tool-btn active" id="toggle-targets" title="Toggle Fibonacci Targets (T1, T2, T3)">🎯 Targets</button>
            <button class="tool-btn active" id="toggle-vol" title="Toggle Bottom Volume Bars">📊 Vol</button>
          </div>
        </div>
      </div>

      <!-- Institutional Metrics Strip -->
      <div id="metrics-strip">
        <div class="metric-chip">
          <span class="chip-label">30d POC</span>
          <span class="chip-val gold" id="metric-poc">--</span>
        </div>
        <div class="metric-chip">
          <span class="chip-label">VAH</span>
          <span class="chip-val cyan" id="metric-vah">--</span>
        </div>
        <div class="metric-chip">
          <span class="chip-label">7D OI Growth</span>
          <span class="chip-val" id="metric-oi7d">--</span>
        </div>
        <div class="metric-chip">
          <span class="chip-label">Funding Rate</span>
          <span class="chip-val" id="metric-funding">--</span>
        </div>
        <div class="metric-chip">
          <span class="chip-label">Vol Dry-Up (7d/14d)</span>
          <span class="chip-val" id="metric-dryup">--</span>
        </div>
        <div class="metric-chip">
          <span class="chip-label">Taker Aggression</span>
          <span class="chip-val" id="metric-taker">--</span>
        </div>
        <div class="metric-chip">
          <span class="chip-label">R:R Ratio</span>
          <span class="chip-val green" id="metric-rr">--</span>
        </div>
        <div class="metric-chip">
          <span class="chip-label">Volatility Squeeze</span>
          <span class="chip-val purple" id="metric-squeeze">ANALYZING...</span>
        </div>
      </div>

      <!-- High-Vision TradingView Lightweight Chart Viewport -->
      <div id="chart-viewport">
        <div id="chart-container"></div>
      </div>
    </main>

    <!-- Right Verification & Execution Panel -->
    <aside id="details-panel">
      <div class="panel-section-title">Institutional Verification</div>
      <div class="check-list" id="check-list"></div>

      <div class="panel-section-title">Execution Bracket</div>
      <div class="bracket-box">
        <div class="bracket-row">
          <span class="bracket-label">Invalidation Stop</span>
          <span class="bracket-val-stop" id="plan-stop">---</span>
        </div>
        <div class="bracket-row">
          <span class="bracket-label">Optimal Entry</span>
          <span class="bracket-val-entry" id="plan-entry">---</span>
        </div>
        <div class="bracket-row">
          <span class="bracket-label">Target 1 (Base Ceiling)</span>
          <span class="bracket-val-target" id="plan-t1">---</span>
        </div>
        <div class="bracket-row">
          <span class="bracket-label">Target 2 (0.618 Fib)</span>
          <span class="bracket-val-target" id="plan-t2">---</span>
        </div>
        <div class="bracket-row">
          <span class="bracket-label">Target 3 (1.618 Fib)</span>
          <span class="bracket-val-target" id="plan-t3">---</span>
        </div>
      </div>

      <div class="panel-section-title">Quantitative Rationale</div>
      <div class="rationale-box" id="plan-rationale"></div>
    </aside>
  </div>

  <script>
    const opps = {payload_json};
    let currentOpp = opps.find(o => o.symbol === "{selected_symbol}") || opps[0];

    // Technical overlay visibility states
    let showEMAs = true;
    let showLevels = true;
    let showTargets = true;
    let showVolume = true;

    // Technical EMA Calculation Helper
    function calculateEMA(candles, period) {{
      if (!candles || candles.length < period) return [];
      const k = 2 / (period + 1);
      let ema = candles.slice(0, period).reduce((acc, c) => acc + c.close, 0) / period;
      const result = [{{ time: candles[period - 1].time, value: ema }}];
      for (let i = period; i < candles.length; i++) {{
        ema = candles[i].close * k + ema * (1 - k);
        result.push({{ time: candles[i].time, value: ema }});
      }}
      return result;
    }}

    // Initialize Chart
    const container = document.getElementById('chart-container');
    const chart = LightweightCharts.createChart(container, {{
      layout: {{
        background: {{ color: '#070b12' }},
        textColor: '#94a3b8',
        fontSize: 11,
      }},
      grid: {{
        vertLines: {{ color: '#131b2c' }},
        horzLines: {{ color: '#131b2c' }},
      }},
      crosshair: {{
        mode: LightweightCharts.CrosshairMode.Normal,
        vertLine: {{ color: '#334155', width: 1, style: LightweightCharts.LineStyle.Dashed }},
        horzLine: {{ color: '#334155', width: 1, style: LightweightCharts.LineStyle.Dashed }},
      }},
      rightPriceScale: {{
        borderColor: '#1e293b',
        scaleMargins: {{ top: 0.1, bottom: 0.22 }},
      }},
      timeScale: {{
        borderColor: '#1e293b',
        timeVisible: true,
        secondsVisible: false,
      }},
    }});

    // Candlestick Series
    const candleSeries = chart.addCandlestickSeries({{
      upColor: '#10b981',
      downColor: '#ef4444',
      borderVisible: true,
      borderColor: '#10b981',
      borderDownColor: '#ef4444',
      wickUpColor: '#10b981',
      wickDownColor: '#ef4444',
    }});

    // Volume Series (Isolated onto its own scale, lastValueVisible = false to prevent scale clutter!)
    const volumeSeries = chart.addHistogramSeries({{
      priceFormat: {{ type: 'volume' }},
      priceScaleId: 'volume_scale',
      lastValueVisible: false,
      priceLineVisible: false,
    }});
    chart.priceScale('volume_scale').applyOptions({{
      scaleMargins: {{ top: 0.82, bottom: 0 }},
      visible: false,
    }});

    // EMA Series
    const ema20Series = chart.addLineSeries({{
      color: '#06b6d4',
      lineWidth: 1.5,
      priceLineVisible: false,
      lastValueVisible: false,
      title: 'EMA 20',
    }});
    const ema50Series = chart.addLineSeries({{
      color: '#f97316',
      lineWidth: 1.5,
      priceLineVisible: false,
      lastValueVisible: false,
      title: 'EMA 50',
    }});
    const ema200Series = chart.addLineSeries({{
      color: '#a855f7',
      lineWidth: 2,
      lineStyle: LightweightCharts.LineStyle.Dashed,
      priceLineVisible: false,
      lastValueVisible: false,
      title: 'EMA 200',
    }});

    // Price lines tracker
    let levelLines = [];
    let targetLines = [];

    function clearAllPriceLines() {{
      levelLines.forEach(l => candleSeries.removePriceLine(l));
      targetLines.forEach(l => candleSeries.removePriceLine(l));
      levelLines = [];
      targetLines = [];
    }}

    // Crosshair HUD Update
    function updateHud(candle, prevCandle) {{
      if (!candle) return;
      const d = new Date(candle.time * 1000);
      document.getElementById('hud-time').innerText = d.toLocaleDateString(undefined, {{ month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }});
      document.getElementById('hud-open').innerText = candle.open.toFixed(4);
      document.getElementById('hud-high').innerText = candle.high.toFixed(4);
      document.getElementById('hud-low').innerText = candle.low.toFixed(4);
      document.getElementById('hud-close').innerText = candle.close.toFixed(4);
      
      if (candle.volume !== undefined && candle.volume !== null) {{
        document.getElementById('hud-vol').innerText = candle.volume > 1e6 ? (candle.volume / 1e6).toFixed(2) + 'M' : candle.volume > 1e3 ? (candle.volume / 1e3).toFixed(1) + 'K' : candle.volume.toFixed(0);
      }}

      const refPrice = prevCandle ? prevCandle.close : candle.open;
      const chgPct = ((candle.close - refPrice) / refPrice) * 100;
      const chgEl = document.getElementById('hud-chg');
      chgEl.innerText = (chgPct >= 0 ? '+' : '') + chgPct.toFixed(2) + '%';
      chgEl.style.color = chgPct >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';
    }}

    chart.subscribeCrosshairMove(param => {{
      if (!currentOpp || !currentOpp.candles || currentOpp.candles.length === 0) return;
      if (!param || !param.time || !param.seriesData) {{
        const c = currentOpp.candles[currentOpp.candles.length - 1];
        const p = currentOpp.candles.length > 1 ? currentOpp.candles[currentOpp.candles.length - 2] : null;
        updateHud(c, p);
        return;
      }}
      const candleData = param.seriesData.get(candleSeries);
      if (candleData) {{
        const volData = param.seriesData.get(volumeSeries);
        updateHud({{
          time: param.time,
          open: candleData.open,
          high: candleData.high,
          low: candleData.low,
          close: candleData.close,
          volume: volData ? volData.value : null,
        }});
      }}
    }});

    // Render Opportunity
    function renderOpportunity(opp) {{
      currentOpp = opp;

      // Update sidebar active classes
      document.querySelectorAll('.opp-card').forEach(el => {{
        el.classList.toggle('active', el.dataset.symbol === opp.symbol);
      }});

      // Update Header Pill
      document.getElementById('top-symbol').innerText = opp.symbol;
      document.getElementById('top-price').innerText = '$' + opp.current_price.toFixed(4);
      document.getElementById('top-setup').innerText = opp.setup_label;
      
      const passes = (opp.checklist || []).filter(c => c.passed).length;
      const totalChecks = (opp.checklist || []).length;
      const grade = (totalChecks > 0 && passes === totalChecks) ? 'GRADE A+' : passes >= 4 ? 'GRADE A' : 'GRADE B';
      const scoreBadge = document.getElementById('top-score');
      scoreBadge.innerText = `${{grade}} • SCORE ${{opp.score.toFixed(1)}}`;
      scoreBadge.style.color = (totalChecks > 0 && passes === totalChecks) ? '#10b981' : '#a78bfa';

      // Update Dynamic Verification Checklist
      const checkListEl = document.getElementById('check-list');
      if (opp.checklist && opp.checklist.length > 0) {{
        checkListEl.innerHTML = opp.checklist.map(item => `
          <div class="check-item ${{item.passed ? 'pass' : 'fail'}}">
            <span class="check-icon">${{item.passed ? '✓' : '✗'}}</span>
            <div class="check-content">
              <span class="check-label">${{item.title}}</span>
              <span class="check-desc">${{item.detail}}</span>
            </div>
          </div>
        `).join('');
      }} else {{
        checkListEl.innerHTML = '<div style="color:var(--text-dim);font-size:11px;">Standard quantitative filters active.</div>';
      }}

      // Update Metrics Strip
      document.getElementById('metric-poc').innerText = opp.poc_price ? opp.poc_price.toFixed(4) : 'N/A';
      document.getElementById('metric-vah').innerText = opp.vah_price ? opp.vah_price.toFixed(4) : 'N/A';
      
      const oi7d = document.getElementById('metric-oi7d');
      oi7d.innerText = (opp.oi_change_7d_pct >= 0 ? '+' : '') + opp.oi_change_7d_pct.toFixed(2) + '%';
      oi7d.className = 'chip-val ' + (opp.oi_change_7d_pct > 0 ? 'green' : 'red');

      const funding = document.getElementById('metric-funding');
      funding.innerText = (opp.funding_rate_bps >= 0 ? '+' : '') + opp.funding_rate_bps.toFixed(2) + ' bps';
      funding.className = 'chip-val ' + (opp.funding_rate_bps <= 0 ? 'green' : '');

      const dryup = document.getElementById('metric-dryup');
      dryup.innerText = opp.volume_contraction_ratio.toFixed(2) + 'x';
      dryup.className = 'chip-val ' + (opp.volume_contraction_ratio >= 0.45 && opp.volume_contraction_ratio <= 0.88 ? 'gold' : '');

      document.getElementById('metric-taker').innerText = opp.taker_ratio.toFixed(2);
      document.getElementById('metric-rr').innerText = opp.risk_reward_ratio.toFixed(1) + ':1';

      // Squeeze indicator determination
      const sqzEl = document.getElementById('metric-squeeze');
      if (opp.setup_label.includes('COIL') || opp.setup_label.includes('VCP') || opp.setup_label.includes('COMPRESSION')) {{
        sqzEl.innerText = '⚡ COIL ACTIVE';
        sqzEl.className = 'chip-val gold';
      }} else {{
        sqzEl.innerText = 'ABSORPTION';
        sqzEl.className = 'chip-val cyan';
      }}

      // Update Right Plan
      document.getElementById('plan-stop').innerText = opp.suggested_stop.toFixed(4);
      document.getElementById('plan-entry').innerText = opp.suggested_entry.toFixed(4);
      document.getElementById('plan-t1').innerText = opp.targets[0] ? opp.targets[0].toFixed(4) : '---';
      document.getElementById('plan-t2').innerText = opp.targets[1] ? opp.targets[1].toFixed(4) : '---';
      document.getElementById('plan-t3').innerText = opp.targets[2] ? opp.targets[2].toFixed(4) : '---';
      
      const ratEl = document.getElementById('plan-rationale');
      ratEl.innerHTML = opp.rationale.map(r => `<div>• ${{r}}</div>`).join('');

      // Populate Candle & Volume Data
      if (opp.candles && opp.candles.length > 0) {{
        candleSeries.setData(opp.candles.map(c => ({{
          time: c.time,
          open: c.open,
          high: c.high,
          low: c.low,
          close: c.close
        }})));

        if (showVolume) {{
          volumeSeries.setData(opp.candles.map(c => ({{
            time: c.time,
            value: c.volume,
            color: c.close >= c.open ? 'rgba(16, 185, 129, 0.28)' : 'rgba(239, 68, 68, 0.28)'
          }})));
        }} else {{
          volumeSeries.setData([]);
        }}

        // Calculate and set EMAs
        if (showEMAs) {{
          ema20Series.setData(calculateEMA(opp.candles, 20));
          ema50Series.setData(calculateEMA(opp.candles, 50));
          ema200Series.setData(calculateEMA(opp.candles, 200));
        }} else {{
          ema20Series.setData([]);
          ema50Series.setData([]);
          ema200Series.setData([]);
        }}

        const lastC = opp.candles[opp.candles.length - 1];
        const prevC = opp.candles.length > 1 ? opp.candles[opp.candles.length - 2] : null;
        updateHud(lastC, prevC);
      }}

      // Redraw Price Lines
      clearAllPriceLines();

      if (showLevels) {{
        // Invalidation Stop Line
        levelLines.push(candleSeries.createPriceLine({{
          price: opp.suggested_stop,
          color: '#ef4444',
          lineWidth: 2,
          lineStyle: LightweightCharts.LineStyle.Dashed,
          axisLabelVisible: true,
          title: 'STOP LOSS',
        }}));

        // 30d Point of Control
        if (opp.poc_price) {{
          levelLines.push(candleSeries.createPriceLine({{
            price: opp.poc_price,
            color: '#f59e0b',
            lineWidth: 2,
            lineStyle: LightweightCharts.LineStyle.Solid,
            axisLabelVisible: true,
            title: '30d POC',
          }}));
        }}

        // Value Area High
        if (opp.vah_price) {{
          levelLines.push(candleSeries.createPriceLine({{
            price: opp.vah_price,
            color: '#06b6d4',
            lineWidth: 1,
            lineStyle: LightweightCharts.LineStyle.Dotted,
            axisLabelVisible: true,
            title: 'VAH',
          }}));
        }}
      }}

      if (showTargets) {{
        // Target 1 (Base Launchpad Ceiling)
        if (opp.targets[0]) {{
          targetLines.push(candleSeries.createPriceLine({{
            price: opp.targets[0],
            color: '#10b981',
            lineWidth: 1,
            lineStyle: LightweightCharts.LineStyle.Dashed,
            axisLabelVisible: true,
            title: 'TARGET 1',
          }}));
        }}

        // Target 2 (0.618 Fib Extension)
        if (opp.targets[1]) {{
          targetLines.push(candleSeries.createPriceLine({{
            price: opp.targets[1],
            color: '#059669',
            lineWidth: 2,
            lineStyle: LightweightCharts.LineStyle.Solid,
            axisLabelVisible: true,
            title: 'TARGET 2 (0.618 Fib)',
          }}));
        }}

        // Target 3 (1.618 Fib Extension)
        if (opp.targets[2]) {{
          targetLines.push(candleSeries.createPriceLine({{
            price: opp.targets[2],
            color: '#14b8a6',
            lineWidth: 1,
            lineStyle: LightweightCharts.LineStyle.Solid,
            axisLabelVisible: true,
            title: 'TARGET 3 (1.618 Fib)',
          }}));
        }}
      }}

      chart.timeScale().fitContent();
    }}

    // Populate Sidebar Cards
    const listEl = document.getElementById('opp-list');
    document.getElementById('count-all').innerText = opps.length;

    function populateList(items) {{
      listEl.innerHTML = '';
      items.forEach(opp => {{
        const card = document.createElement('div');
        card.className = 'opp-card' + (opp.symbol === currentOpp.symbol ? ' active' : '');
        card.dataset.symbol = opp.symbol;
        card.innerHTML = `
          <div class="opp-card-top">
            <span class="opp-symbol">${{opp.symbol}}</span>
            <span class="opp-score">${{opp.score.toFixed(1)}}</span>
          </div>
          <div class="opp-setup">${{opp.setup_label}}</div>
          <div class="opp-stats">
            <span>7d OI: ${{opp.oi_change_7d_pct >= 0 ? '+' : ''}}${{opp.oi_change_7d_pct.toFixed(1)}}%</span>
            <span>R:R: ${{opp.risk_reward_ratio.toFixed(1)}}:1</span>
          </div>
        `;
        card.addEventListener('click', () => renderOpportunity(opp));
        listEl.appendChild(card);
      }});
    }}

    populateList(opps);

    // Search and Filter logic
    const searchInput = document.getElementById('cand-search');
    let currentFilter = 'all';

    function filterOpportunities() {{
      const query = searchInput.value.trim().toUpperCase();
      const filtered = opps.filter(opp => {{
        const matchesQuery = !query || opp.symbol.includes(query);
        let matchesType = true;
        if (currentFilter === 'coil') {{
          matchesType = opp.setup_label.includes('COIL') || opp.setup_label.includes('VCP');
        }} else if (currentFilter === 'oi') {{
          matchesType = opp.setup_label.includes('OI') || opp.oi_change_7d_pct > 10;
        }}
        return matchesQuery && matchesType;
      }});
      populateList(filtered);
    }}

    searchInput.addEventListener('input', filterOpportunities);

    document.querySelectorAll('.filter-chip').forEach(btn => {{
      btn.addEventListener('click', () => {{
        document.querySelectorAll('.filter-chip').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        currentFilter = btn.dataset.filter;
        filterOpportunities();
      }});
    }});

    // Layout resizing & Focus Chart (Zen Mode)
    function resizeChart() {{
      setTimeout(() => {{
        chart.resize(container.clientWidth, container.clientHeight);
      }}, 100);
    }}

    window.addEventListener('resize', resizeChart);

    const btnToggleSidebar = document.getElementById('btn-toggle-sidebar');
    const btnToggleDetails = document.getElementById('btn-toggle-details');
    const btnZen = document.getElementById('btn-zen');
    const expandLeftBtn = document.getElementById('expand-left-btn');
    const expandRightBtn = document.getElementById('expand-right-btn');

    function toggleSidebar() {{
      document.body.classList.toggle('sidebar-hidden');
      const isHidden = document.body.classList.contains('sidebar-hidden');
      btnToggleSidebar.classList.toggle('active', !isHidden);
      resizeChart();
    }}

    function toggleDetails() {{
      document.body.classList.toggle('details-hidden');
      const isHidden = document.body.classList.contains('details-hidden');
      btnToggleDetails.classList.toggle('active', !isHidden);
      resizeChart();
    }}

    function toggleZen() {{
      const isZen = document.body.classList.contains('sidebar-hidden') && document.body.classList.contains('details-hidden');
      if (isZen) {{
        document.body.classList.remove('sidebar-hidden', 'details-hidden');
        btnToggleSidebar.classList.add('active');
        btnToggleDetails.classList.add('active');
        btnZen.classList.remove('active');
      }} else {{
        document.body.classList.add('sidebar-hidden', 'details-hidden');
        btnToggleSidebar.classList.remove('active');
        btnToggleDetails.classList.remove('active');
        btnZen.classList.add('active');
      }}
      resizeChart();
    }}

    btnToggleSidebar.addEventListener('click', toggleSidebar);
    btnToggleDetails.addEventListener('click', toggleDetails);
    expandLeftBtn.addEventListener('click', toggleSidebar);
    expandRightBtn.addEventListener('click', toggleDetails);
    btnZen.addEventListener('click', toggleZen);

    // Keyboard Shortcuts
    window.addEventListener('keydown', (e) => {{
      if (e.target.tagName === 'INPUT') return;
      if (e.key === '[' ) {{
        toggleSidebar();
      }} else if (e.key === ']' ) {{
        toggleDetails();
      }} else if (e.key.toLowerCase() === 'f') {{
        toggleZen();
      }} else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {{
        const currentIndex = opps.findIndex(o => o.symbol === currentOpp.symbol);
        const nextIndex = e.key === 'ArrowDown' ? Math.min(opps.length - 1, currentIndex + 1) : Math.max(0, currentIndex - 1);
        if (nextIndex !== currentIndex) {{
          renderOpportunity(opps[nextIndex]);
        }}
      }}
    }});

    // Range Zoom Presets
    document.querySelectorAll('[data-range]').forEach(btn => {{
      btn.addEventListener('click', () => {{
        document.querySelectorAll('[data-range]').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        const range = btn.dataset.range;
        if (!currentOpp || !currentOpp.candles || currentOpp.candles.length === 0) return;
        const total = currentOpp.candles.length;
        if (range === 'all') {{
          chart.timeScale().fitContent();
        }} else {{
          let bars = range === '3d' ? 72 : range === '7d' ? 168 : 336;
          const fromIndex = Math.max(0, total - bars);
          chart.timeScale().setVisibleLogicalRange({{ from: fromIndex, to: total - 1 }});
        }}
      }});
    }});

    document.getElementById('btn-fit').addEventListener('click', () => {{
      chart.timeScale().fitContent();
    }});

    // Technical Overlays Toggles
    const toggleEmasBtn = document.getElementById('toggle-emas');
    toggleEmasBtn.addEventListener('click', () => {{
      showEMAs = !showEMAs;
      toggleEmasBtn.classList.toggle('active', showEMAs);
      renderOpportunity(currentOpp);
    }});

    const toggleLevelsBtn = document.getElementById('toggle-levels');
    toggleLevelsBtn.addEventListener('click', () => {{
      showLevels = !showLevels;
      toggleLevelsBtn.classList.toggle('active', showLevels);
      renderOpportunity(currentOpp);
    }});

    const toggleTargetsBtn = document.getElementById('toggle-targets');
    toggleTargetsBtn.addEventListener('click', () => {{
      showTargets = !showTargets;
      toggleTargetsBtn.classList.toggle('active', showTargets);
      renderOpportunity(currentOpp);
    }});

    const toggleVolBtn = document.getElementById('toggle-vol');
    toggleVolBtn.addEventListener('click', () => {{
      showVolume = !showVolume;
      toggleVolBtn.classList.toggle('active', showVolume);
      renderOpportunity(currentOpp);
    }});

    // Initial render
    renderOpportunity(currentOpp);
  </script>
</body>
</html>
"""
