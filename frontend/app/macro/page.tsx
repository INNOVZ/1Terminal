"use client";

import { useEffect, useState, useMemo, memo, useCallback } from "react";
import {
  Globe2,
  TrendingUp,
  DollarSign,
  Activity,
  ShieldCheck,
  AlertTriangle,
  AlertCircle,
  RefreshCw,
  Flame,
  Gauge,
  Zap,
  BarChart3,
  Layers,
  ArrowUpRight,
  ArrowDownRight,
} from "lucide-react";

interface BenchmarkExcursion {
  symbol: string;
  current_price: number;
  sma_20: number;
  ema_50: number;
  dist_sma20_pct: number;
  dist_ema50_pct: number;
  z_score_20d: number;
  bollinger_pct_b: number;
  rsi_14_daily: number;
  rsi_14_4h: number;
  status: string;
}

interface HeatedSymbol {
  symbol: string;
  funding_rate_8h_pct: number;
  annualized_apr_pct: number;
}

interface DerivativesLeverageHeat {
  avg_funding_8h: number;
  annualized_apr: number;
  crowded_long_ratio: number;
  funding_regime: string;
  top_heated_symbols: HeatedSymbol[];
  top_discount_symbols: HeatedSymbol[];
}

interface MarketBreadthSaturation {
  total_pairs_analyzed: number;
  pct_above_20_sma: number;
  pct_rsi_overbought: number;
  pct_rsi_oversold: number;
  median_24h_change_pct: number;
  breadth_regime: string;
}

interface LiquidityDivergence {
  stablecoin_net_flow_7d: number;
  stablecoin_total_usd: number;
  fear_greed_score: number;
  fear_greed_classification: string;
  btc_dominance: number;
  is_synthetic_leverage_divergence: boolean;
  status: string;
}

interface SupportDefenseShelf {
  asset: string;
  level_name: string;
  price: number;
  distance_pct: number;
}

interface MacroOverextensionReport {
  composite_score: number;
  regime: string;
  regime_title: string;
  regime_description: string;
  cool_off_probability_pct: number;
  benchmark_btc: BenchmarkExcursion;
  benchmark_eth: BenchmarkExcursion;
  derivatives_heat: DerivativesLeverageHeat;
  market_breadth: MarketBreadthSaturation;
  liquidity_divergence: LiquidityDivergence;
  support_shelves: SupportDefenseShelf[];
  recommendations: string[];
  anti_chasing_alert: string;
  analyzed_at: string;
}

function getRegimeStyle(regime: string) {
  switch (regime) {
    case "CLIMAX_EXHAUSTION":
      return {
        color: "#ef4444",
        bg: "rgba(239, 68, 68, 0.12)",
        border: "rgba(239, 68, 68, 0.35)",
        icon: <AlertCircle size={16} strokeWidth={2.5} />,
        label: "Euphoric Climax: Severe Pullback Imminent",
        tag: "CRITICAL OVERBOUGHT",
      };
    case "HIGH_PULLBACK_RISK":
      return {
        color: "#ef4444",
        bg: "rgba(239, 68, 68, 0.12)",
        border: "rgba(239, 68, 68, 0.35)",
        icon: <AlertTriangle size={16} strokeWidth={2.5} />,
        label: "High Overextension: Mean-Reversion Expected",
        tag: "ELEVATED RISK",
      };
    case "MODERATE_OVEREXTENSION":
      return {
        color: "#f49d0d",
        bg: "rgba(244, 157, 13, 0.12)",
        border: "rgba(244, 157, 13, 0.35)",
        icon: <Flame size={16} strokeWidth={2.5} />,
        label: "Trending Expansion: Moderate Stretch",
        tag: "CAUTIOUS MOMENTUM",
      };
    case "BALANCED_EXPANSION":
      return {
        color: "#10b981",
        bg: "rgba(16, 185, 129, 0.12)",
        border: "rgba(16, 185, 129, 0.35)",
        icon: <ShieldCheck size={16} strokeWidth={2.5} />,
        label: "Balanced Market: Low Pullback Risk",
        tag: "HEALTHY EXPANSION",
      };
    case "HEALTHY_BASE":
    default:
      return {
        color: "#10b981",
        bg: "rgba(16, 185, 129, 0.12)",
        border: "rgba(16, 185, 129, 0.35)",
        icon: <Zap size={16} strokeWidth={2.5} />,
        label: "Oversold Base: High Accumulation Runway",
        tag: "OPTIMAL ACCUMULATION",
      };
  }
}

// ----------------------------------------------------------------------------
// Hero Risk Radar Gauge Component
// ----------------------------------------------------------------------------
const OverextensionRadarHero = memo(function OverextensionRadarHero({
  report,
  loading,
}: {
  report: MacroOverextensionReport;
  loading: boolean;
}) {
  const regimeStyle = useMemo(() => getRegimeStyle(report.regime), [report.regime]);
  const score = report.composite_score;

  return (
    <div
      className="glass-panel"
      style={{
        padding: "28px",
        marginBottom: "28px",
        position: "relative",
        border: `1px solid ${regimeStyle.border}`,
        boxShadow: `0 8px 32px ${regimeStyle.bg}`,
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: "16px", marginBottom: "20px" }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
            <span
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: "6px",
                background: regimeStyle.bg,
                color: regimeStyle.color,
                border: `1px solid ${regimeStyle.border}`,
                padding: "4px 12px",
                borderRadius: "20px",
                fontSize: "12px",
                fontWeight: 800,
                letterSpacing: "0.5px",
                textTransform: "uppercase",
              }}
            >
              {regimeStyle.icon}
              {regimeStyle.tag}
            </span>

            <span style={{ fontSize: "12px", color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
              {report.analyzed_at ? `Telemetry: ${new Date(report.analyzed_at).toLocaleTimeString()}` : "Real-time Telemetry"}
            </span>
          </div>

          <h2 style={{ fontSize: "24px", fontWeight: 900, marginTop: "8px", letterSpacing: "-0.02em" }}>
            {report.regime_title}
          </h2>

          <p style={{ fontSize: "14px", color: "var(--text-secondary)", marginTop: "6px", maxWidth: "880px", lineHeight: "1.55" }}>
            {report.regime_description}
          </p>
        </div>

        {/* Right side metrics callout */}
        <div style={{ display: "flex", gap: "20px", alignItems: "center", background: "rgba(0,0,0,0.3)", padding: "14px 20px", borderRadius: "12px", border: "1px solid var(--border-subtle)" }}>
          <div style={{ textAlign: "center" }}>
            <div style={{ fontSize: "11px", color: "var(--text-muted)", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.5px" }}>
              Composite Heat
            </div>
            <div style={{ fontSize: "42px", fontWeight: 900, color: regimeStyle.color, lineHeight: "1.1", marginTop: "2px" }}>
              {score.toFixed(1)}
              <span style={{ fontSize: "14px", color: "var(--text-muted)", fontWeight: 600 }}>/100</span>
            </div>
          </div>

          <div style={{ width: "1px", height: "46px", background: "var(--border-subtle)" }} />

          <div style={{ textAlign: "center" }}>
            <div style={{ fontSize: "11px", color: "var(--text-muted)", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.5px" }}>
              Cool-Off Probability
            </div>
            <div style={{ fontSize: "42px", fontWeight: 900, color: report.cool_off_probability_pct > 60 ? "#ef4444" : "#f49d0d", lineHeight: "1.1", marginTop: "2px" }}>
              {report.cool_off_probability_pct}%
            </div>
          </div>
        </div>
      </div>

      {/* 5-Zone Quantitative Overextension Speedometer Bar */}
      <div style={{ marginTop: "10px", marginBottom: "14px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: "11px", fontWeight: 700, color: "var(--text-muted)", marginBottom: "6px" }}>
          <span>0 (Oversold Base)</span>
          <span>30 (Healthy Trend)</span>
          <span>55 (Neutral Excursion)</span>
          <span>75 (Overextended Caution)</span>
          <span>100 (Euphoric Climax)</span>
        </div>

        <div style={{ position: "relative", width: "100%", height: "14px", background: "rgba(255, 255, 255, 0.05)", borderRadius: "8px", overflow: "hidden", display: "flex" }}>
          <div style={{ width: "30%", height: "100%", background: "#10b981" }} title="Healthy Accumulation Zone (0-30)" />
          <div style={{ width: "25%", height: "100%", background: "linear-gradient(90deg, #10b981, #f49d0d)" }} title="Balanced Expansion (31-55)" />
          <div style={{ width: "20%", height: "100%", background: "#f49d0d" }} title="Moderate Stretch (56-74)" />
          <div style={{ width: "15%", height: "100%", background: "linear-gradient(90deg, #f49d0d, #ef4444)" }} title="High Pullback Risk (75-85)" />
          <div style={{ width: "10%", height: "100%", background: "#ef4444" }} title="Euphoric Climax (86-100)" />

          {/* Needle Indicator */}
          <div
            style={{
              position: "absolute",
              top: 0,
              bottom: 0,
              left: `calc(${Math.min(99, Math.max(1, score))}% - 3px)`,
              width: "6px",
              background: "#ffffff",
              boxShadow: "0 0 10px #ffffff, 0 0 20px rgba(255,255,255,0.8)",
              borderRadius: "4px",
              transition: "left 0.5s ease-out",
            }}
          />
        </div>
      </div>

      {/* Anti-Chasing Protocol Banner */}
      <div
        style={{
          background: "rgba(0, 0, 0, 0.4)",
          borderLeft: `4px solid ${regimeStyle.color}`,
          padding: "10px 14px",
          borderRadius: "0 8px 8px 0",
          display: "flex",
          alignItems: "center",
          gap: "10px",
          marginTop: "16px",
        }}
      >
        <span style={{ color: regimeStyle.color, display: "flex" }}>{regimeStyle.icon}</span>
        <div style={{ fontSize: "12.5px", color: "var(--text-primary)", fontWeight: 600 }}>
          <strong style={{ color: regimeStyle.color }}>Institutional Protocol: </strong>
          {report.anti_chasing_alert}
        </div>
      </div>
    </div>
  );
});

// ----------------------------------------------------------------------------
// Pillar 1: Benchmark Technical Excursion (BTC & ETH)
// ----------------------------------------------------------------------------
const BenchmarkExcursionCard = memo(function BenchmarkExcursionCard({
  btc,
  eth,
}: {
  btc: BenchmarkExcursion;
  eth: BenchmarkExcursion;
}) {
  return (
    <div className="glass-panel" style={{ padding: "22px", display: "flex", flexDirection: "column" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "14px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <Gauge size={18} style={{ color: "#f49d0d" }} />
          <h3 style={{ fontSize: "14px", fontWeight: 800, textTransform: "uppercase", letterSpacing: "0.5px" }}>
            1. Benchmark Excursion (BTC & ETH)
          </h3>
        </div>
        <span
          style={{
            fontSize: "11px",
            fontWeight: 700,
            color: btc.dist_sma20_pct > 6 ? "#f49d0d" : "var(--accent-emerald)",
            background: "rgba(255,255,255,0.04)",
            padding: "2px 8px",
            borderRadius: "6px",
          }}
        >
          {btc.status.replace("_", " ")}
        </span>
      </div>

      <p style={{ fontSize: "12px", color: "var(--text-secondary)", marginBottom: "16px", lineHeight: "1.45" }}>
        Measures statistical distance from institutional moving averages. High positive Z-scores and excursions &gt;7% from the 20-day SMA indicate mean-reversion pullbacks.
      </p>

      {/* BTC Breakdown */}
      <div style={{ background: "rgba(255,255,255,0.02)", border: "1px solid var(--border-subtle)", borderRadius: "8px", padding: "12px", marginBottom: "10px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
          <span style={{ fontSize: "13px", fontWeight: 800, color: "#f49d0d" }}>BTC / USD-M Perpetual</span>
          <span style={{ fontFamily: "var(--font-mono)", fontSize: "13px", fontWeight: 800 }}>${btc.current_price.toLocaleString()}</span>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "6px", fontFamily: "var(--font-mono)", fontSize: "10.5px" }}>
          <div>
            <div style={{ color: "var(--text-muted)", fontSize: "9.5px" }}>20D SMA</div>
            <div style={{ fontWeight: 700 }}>${btc.sma_20.toLocaleString()}</div>
            <div style={{ color: btc.dist_sma20_pct >= 0 ? "var(--accent-emerald)" : "#ef4444", fontWeight: 700 }}>
              {btc.dist_sma20_pct >= 0 ? "+" : ""}{btc.dist_sma20_pct.toFixed(1)}%
            </div>
          </div>
          <div>
            <div style={{ color: "var(--text-muted)", fontSize: "9.5px" }}>50D EMA</div>
            <div style={{ fontWeight: 700 }}>${btc.ema_50.toLocaleString()}</div>
            <div style={{ color: btc.dist_ema50_pct >= 0 ? "var(--accent-emerald)" : "#ef4444", fontWeight: 700 }}>
              {btc.dist_ema50_pct >= 0 ? "+" : ""}{btc.dist_ema50_pct.toFixed(1)}%
            </div>
          </div>
          <div>
            <div style={{ color: "var(--text-muted)", fontSize: "9.5px" }}>20D Z-SCORE</div>
            <div style={{ fontWeight: 800, color: Math.abs(btc.z_score_20d) > 2 ? "#ef4444" : "#ffffff" }}>
              {btc.z_score_20d > 0 ? "+" : ""}{btc.z_score_20d.toFixed(2)}σ
            </div>
            <div style={{ color: "var(--text-muted)", fontSize: "9px" }}>%B: {btc.bollinger_pct_b.toFixed(2)}</div>
          </div>
          <div>
            <div style={{ color: "var(--text-muted)", fontSize: "9.5px" }}>RSI (1D / 4H)</div>
            <div style={{ fontWeight: 800, color: btc.rsi_14_daily > 70 ? "#ef4444" : "#ffffff" }}>
              {btc.rsi_14_daily.toFixed(0)} / {btc.rsi_14_4h.toFixed(0)}
            </div>
            <div style={{ color: "var(--text-muted)", fontSize: "9px" }}>14-Period</div>
          </div>
        </div>
      </div>

      {/* ETH Breakdown */}
      <div style={{ background: "rgba(255,255,255,0.02)", border: "1px solid var(--border-subtle)", borderRadius: "8px", padding: "12px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
          <span style={{ fontSize: "13px", fontWeight: 800, color: "#ffffff" }}>ETH / USD-M Perpetual</span>
          <span style={{ fontFamily: "var(--font-mono)", fontSize: "13px", fontWeight: 800 }}>${eth.current_price.toLocaleString()}</span>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "6px", fontFamily: "var(--font-mono)", fontSize: "10.5px" }}>
          <div>
            <div style={{ color: "var(--text-muted)", fontSize: "9.5px" }}>20D SMA</div>
            <div style={{ fontWeight: 700 }}>${eth.sma_20.toLocaleString()}</div>
            <div style={{ color: eth.dist_sma20_pct >= 0 ? "var(--accent-emerald)" : "#ef4444", fontWeight: 700 }}>
              {eth.dist_sma20_pct >= 0 ? "+" : ""}{eth.dist_sma20_pct.toFixed(1)}%
            </div>
          </div>
          <div>
            <div style={{ color: "var(--text-muted)", fontSize: "9.5px" }}>50D EMA</div>
            <div style={{ fontWeight: 700 }}>${eth.ema_50.toLocaleString()}</div>
            <div style={{ color: eth.dist_ema50_pct >= 0 ? "var(--accent-emerald)" : "#ef4444", fontWeight: 700 }}>
              {eth.dist_ema50_pct >= 0 ? "+" : ""}{eth.dist_ema50_pct.toFixed(1)}%
            </div>
          </div>
          <div>
            <div style={{ color: "var(--text-muted)", fontSize: "9.5px" }}>20D Z-SCORE</div>
            <div style={{ fontWeight: 800, color: Math.abs(eth.z_score_20d) > 2 ? "#ef4444" : "#ffffff" }}>
              {eth.z_score_20d > 0 ? "+" : ""}{eth.z_score_20d.toFixed(2)}σ
            </div>
            <div style={{ color: "var(--text-muted)", fontSize: "9px" }}>%B: {eth.bollinger_pct_b.toFixed(2)}</div>
          </div>
          <div>
            <div style={{ color: "var(--text-muted)", fontSize: "9.5px" }}>RSI (1D / 4H)</div>
            <div style={{ fontWeight: 800, color: eth.rsi_14_daily > 70 ? "#ef4444" : "#ffffff" }}>
              {eth.rsi_14_daily.toFixed(0)} / {eth.rsi_14_4h.toFixed(0)}
            </div>
            <div style={{ color: "var(--text-muted)", fontSize: "9px" }}>14-Period</div>
          </div>
        </div>
      </div>
    </div>
  );
});

// ----------------------------------------------------------------------------
// Pillar 2: Derivatives Leverage & Funding Heatmap
// ----------------------------------------------------------------------------
const DerivativesHeatCard = memo(function DerivativesHeatCard({
  heat,
}: {
  heat: DerivativesLeverageHeat;
}) {
  const isHeated = heat.annualized_apr > 25.0;
  return (
    <div className="glass-panel" style={{ padding: "22px", display: "flex", flexDirection: "column" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "14px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <Flame size={18} style={{ color: isHeated ? "#ef4444" : "#f49d0d" }} />
          <h3 style={{ fontSize: "14px", fontWeight: 800, textTransform: "uppercase", letterSpacing: "0.5px" }}>
            2. Derivatives Leverage Heat
          </h3>
        </div>
        <span
          style={{
            fontSize: "11px",
            fontWeight: 700,
            color: isHeated ? "#ef4444" : "var(--accent-emerald)",
            background: "rgba(255,255,255,0.04)",
            padding: "2px 8px",
            borderRadius: "6px",
          }}
        >
          {heat.funding_regime.replace("_", " ")}
        </span>
      </div>

      <div style={{ display: "flex", alignItems: "baseline", gap: "10px", marginBottom: "6px" }}>
        <span style={{ fontSize: "36px", fontWeight: 900, color: isHeated ? "#ef4444" : "#ffffff", fontFamily: "var(--font-mono)" }}>
          {heat.annualized_apr > 0 ? "+" : ""}{heat.annualized_apr.toFixed(1)}%
        </span>
        <span style={{ fontSize: "13px", color: "var(--text-secondary)", fontWeight: 700 }}>
          Annualized Funding APR across USD-M
        </span>
      </div>

      <p style={{ fontSize: "12px", color: "var(--text-secondary)", marginBottom: "14px", lineHeight: "1.45" }}>
        Average 8h rate: <strong style={{ color: "#ffffff", fontFamily: "var(--font-mono)" }}>{(heat.avg_funding_8h * 100).toFixed(4)}%</strong>.{" "}
        {heat.crowded_long_ratio}% of contracts trade above 15 bps/8h (&gt;16% APR). Elevated funding forces long squeezes.
      </p>

      {/* Top Heated / Crowded contracts */}
      <div style={{ marginTop: "auto" }}>
        <div style={{ fontSize: "11px", fontWeight: 700, color: "#f49d0d", marginBottom: "6px", textTransform: "uppercase" }}>
          Extreme Leverage Outliers (Top Heated)
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: "4px", fontSize: "11px", fontFamily: "var(--font-mono)" }}>
          {heat.top_heated_symbols.slice(0, 3).map((s) => (
            <div key={s.symbol} style={{ display: "flex", justifyContent: "space-between", padding: "4px 8px", background: "rgba(255,255,255,0.02)", borderRadius: "4px" }}>
              <span style={{ fontWeight: 700, color: "var(--text-primary)" }}>{s.symbol}</span>
              <span style={{ color: "#ef4444", fontWeight: 800 }}>
                +{s.annualized_apr_pct.toFixed(0)}% APR ({s.funding_rate_8h_pct.toFixed(3)}%/8h)
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
});

// ----------------------------------------------------------------------------
// Pillar 3: Market Breadth Saturation (741 Pairs)
// ----------------------------------------------------------------------------
const MarketBreadthCard = memo(function MarketBreadthCard({
  breadth,
}: {
  breadth: MarketBreadthSaturation;
}) {
  return (
    <div className="glass-panel" style={{ padding: "22px", display: "flex", flexDirection: "column" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "14px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <BarChart3 size={18} style={{ color: "var(--accent-emerald)" }} />
          <h3 style={{ fontSize: "14px", fontWeight: 800, textTransform: "uppercase", letterSpacing: "0.5px" }}>
            3. Universe Breadth Saturation
          </h3>
        </div>
        <span
          style={{
            fontSize: "11px",
            fontWeight: 700,
            color: breadth.pct_above_20_sma > 75 ? "#f49d0d" : "var(--accent-emerald)",
            background: "rgba(255,255,255,0.04)",
            padding: "2px 8px",
            borderRadius: "6px",
          }}
        >
          {breadth.breadth_regime.replace("_", " ")}
        </span>
      </div>

      <div style={{ display: "flex", alignItems: "baseline", gap: "10px", marginBottom: "6px" }}>
        <span style={{ fontSize: "36px", fontWeight: 900, color: "var(--accent-emerald)", fontFamily: "var(--font-mono)" }}>
          {breadth.pct_above_20_sma.toFixed(1)}%
        </span>
        <span style={{ fontSize: "13px", color: "var(--text-secondary)", fontWeight: 700 }}>
          Universe Gaining in 24h ({breadth.total_pairs_analyzed} Contracts)
        </span>
      </div>

      <p style={{ fontSize: "12px", color: "var(--text-secondary)", marginBottom: "16px", lineHeight: "1.45" }}>
        Median 24h movement across all liquid USD-M pairs is <strong style={{ color: "#ffffff", fontFamily: "var(--font-mono)" }}>{breadth.median_24h_change_pct > 0 ? "+" : ""}{breadth.median_24h_change_pct.toFixed(2)}%</strong>. High participation cluster confirms whether momentum is organic or exhausted.
      </p>

      {/* Breadth Distribution Bars */}
      <div style={{ marginTop: "auto", display: "flex", flexDirection: "column", gap: "8px" }}>
        <div>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: "11px", marginBottom: "3px" }}>
            <span style={{ color: "var(--text-muted)" }}>Expanded &gt;7% in 24h (Overbought Cluster)</span>
            <span style={{ color: "#f49d0d", fontWeight: 700 }}>{breadth.pct_rsi_overbought.toFixed(1)}% of universe</span>
          </div>
          <div style={{ width: "100%", height: "6px", background: "rgba(255,255,255,0.05)", borderRadius: "3px", overflow: "hidden" }}>
            <div style={{ width: `${Math.min(100, breadth.pct_rsi_overbought * 2.5)}%`, height: "100%", background: "#f49d0d" }} />
          </div>
        </div>

        <div>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: "11px", marginBottom: "3px" }}>
            <span style={{ color: "var(--text-muted)" }}>Declining &lt;-7% in 24h (Oversold Cluster)</span>
            <span style={{ color: "var(--text-secondary)", fontWeight: 700 }}>{breadth.pct_rsi_oversold.toFixed(1)}% of universe</span>
          </div>
          <div style={{ width: "100%", height: "6px", background: "rgba(255,255,255,0.05)", borderRadius: "3px", overflow: "hidden" }}>
            <div style={{ width: `${Math.min(100, breadth.pct_rsi_oversold * 2.5)}%`, height: "100%", background: "var(--accent-crimson)" }} />
          </div>
        </div>
      </div>
    </div>
  );
});

// ----------------------------------------------------------------------------
// Pillar 4: Liquidity Fuel vs Leverage Divergence
// ----------------------------------------------------------------------------
const LiquidityDivergenceCard = memo(function LiquidityDivergenceCard({
  liq,
}: {
  liq: LiquidityDivergence;
}) {
  const isSynthetic = liq.is_synthetic_leverage_divergence;
  return (
    <div className="glass-panel" style={{ padding: "22px", display: "flex", flexDirection: "column" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "14px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <DollarSign size={18} style={{ color: isSynthetic ? "#f49d0d" : "var(--accent-emerald)" }} />
          <h3 style={{ fontSize: "14px", fontWeight: 800, textTransform: "uppercase", letterSpacing: "0.5px" }}>
            4. Macro Fuel vs Leverage Divergence
          </h3>
        </div>
        <span
          style={{
            fontSize: "11px",
            fontWeight: 700,
            color: isSynthetic ? "#f49d0d" : "var(--accent-emerald)",
            background: "rgba(255,255,255,0.04)",
            padding: "2px 8px",
            borderRadius: "6px",
          }}
        >
          {isSynthetic ? "Synthetic Leverage Divergence" : "Organic Capital Inflow"}
        </span>
      </div>

      <div style={{ display: "flex", alignItems: "baseline", gap: "10px", marginBottom: "6px" }}>
        <span style={{ fontSize: "36px", fontWeight: 900, color: "#ffffff", fontFamily: "var(--font-mono)" }}>
          ${(liq.stablecoin_total_usd / 1e9).toFixed(1)}B
        </span>
        <span style={{ fontSize: "13px", color: liq.stablecoin_net_flow_7d >= 0 ? "var(--accent-emerald)" : "var(--accent-crimson)", fontWeight: 800 }}>
          {liq.stablecoin_net_flow_7d >= 0 ? "+" : ""}{liq.stablecoin_net_flow_7d.toFixed(2)}% (7D Net Flow)
        </span>
      </div>

      <p style={{ fontSize: "12px", color: "var(--text-secondary)", marginBottom: "14px", lineHeight: "1.45" }}>
        {isSynthetic
          ? "Warning: Crypto market prices have stretched upward while 7D fiat stablecoin supply remains stagnant or negative. This divergence signals the rally is driven by synthetic leveraged perps rather than new spot capital, leaving it prone to abrupt flushes."
          : "DeFiLlama stablecoin minting is positive. Organic fiat capital is entering the ecosystem, providing real liquidity backing for price discovery."}
      </p>

      {/* Secondary metrics row */}
      <div style={{ marginTop: "auto", display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px", background: "rgba(255,255,255,0.02)", padding: "8px 12px", borderRadius: "6px", fontSize: "11px" }}>
        <div>
          <span style={{ color: "var(--text-muted)" }}>Fear & Greed: </span>
          <strong style={{ color: liq.fear_greed_score > 70 ? "#f49d0d" : "#ffffff" }}>
            {liq.fear_greed_score} ({liq.fear_greed_classification})
          </strong>
        </div>
        <div>
          <span style={{ color: "var(--text-muted)" }}>BTC Dominance: </span>
          <strong style={{ color: "#ffffff" }}>{liq.btc_dominance.toFixed(1)}%</strong>
        </div>
      </div>
    </div>
  );
});

// ----------------------------------------------------------------------------
// Institutional Support Defense Shelves & Playbook
// ----------------------------------------------------------------------------
const DefenseShelvesAndPlaybook = memo(function DefenseShelvesAndPlaybook({
  shelves,
  recommendations,
}: {
  shelves: SupportDefenseShelf[];
  recommendations: string[];
}) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(440px, 1fr))", gap: "20px" }}>
      {/* Key Support Defense Shelves */}
      <div className="glass-panel" style={{ padding: "24px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "14px" }}>
          <Layers size={18} style={{ color: "#f49d0d" }} />
          <h3 style={{ fontSize: "15px", fontWeight: 800 }}>Institutional Mean-Reversion Defense Shelves</h3>
        </div>
        <p style={{ fontSize: "12.5px", color: "var(--text-secondary)", marginBottom: "16px", lineHeight: "1.45" }}>
          If a market cool-off or liquidation cascade initiates, these are the primary high-liquidity moving average shelves where institutional bids historically absorb supply:
        </p>

        <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
          {shelves.map((s, idx) => (
            <div
              key={idx}
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                padding: "10px 14px",
                background: "rgba(255, 255, 255, 0.02)",
                border: "1px solid var(--border-subtle)",
                borderRadius: "8px",
              }}
            >
              <div>
                <div style={{ fontSize: "12.5px", fontWeight: 800, color: "var(--text-primary)" }}>
                  {s.asset} — {s.level_name}
                </div>
                <div style={{ fontSize: "11px", color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
                  Institutional Floor Target
                </div>
              </div>

              <div style={{ textAlign: "right", fontFamily: "var(--font-mono)" }}>
                <div style={{ fontSize: "14px", fontWeight: 800, color: "#ffffff" }}>
                  ${s.price.toLocaleString()}
                </div>
                <div style={{ fontSize: "11px", color: s.distance_pct >= 0 ? "var(--accent-emerald)" : "#ef4444", fontWeight: 700 }}>
                  {s.distance_pct >= 0 ? "+" : ""}{s.distance_pct.toFixed(2)}% from price
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Institutional Actionable Playbook */}
      <div className="glass-panel" style={{ padding: "24px", border: "1px solid rgba(244, 157, 13, 0.3)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "14px" }}>
          <ShieldCheck size={18} style={{ color: "#f49d0d" }} />
          <h3 style={{ fontSize: "15px", fontWeight: 800 }}>Actionable Playbook for Current Regime</h3>
        </div>
        <p style={{ fontSize: "12.5px", color: "var(--text-secondary)", marginBottom: "16px", lineHeight: "1.45" }}>
          Prescriptive tactical rules generated dynamically based on active leverage crowding and technical saturation:
        </p>

        <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: "10px" }}>
          {recommendations.map((rec, idx) => (
            <li
              key={idx}
              style={{
                display: "flex",
                gap: "10px",
                fontSize: "12.5px",
                color: "var(--text-secondary)",
                lineHeight: "1.5",
              }}
            >
              <span style={{ color: "#f49d0d", fontWeight: 800 }}>●</span>
              <span>{rec}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
});

// ----------------------------------------------------------------------------
// Main Page Component
// ----------------------------------------------------------------------------
export default function MacroPage() {
  const [data, setData] = useState<MacroOverextensionReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null);

  const fetchMacroReport = useCallback(async (forceRefresh: boolean = false) => {
    setLoading(true);
    try {
      const url = forceRefresh ? "/api/macro?refresh=true" : "/api/macro";
      const res = await fetch(url);
      if (res.ok) {
        const report = await res.json();
        setData(report);
        setLastRefreshed(new Date());
      }
    } catch (err) {
      console.error("Failed to load macro overextension report:", err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => {
      void fetchMacroReport(false);
    }, 0);
    const interval = setInterval(() => {
      void fetchMacroReport(false);
    }, 45000);
    return () => {
      clearTimeout(timer);
      clearInterval(interval);
    };
  }, [fetchMacroReport]);

  const report = useMemo(() => data, [data]);

  return (
    <div style={{ minHeight: "100vh", padding: "32px 36px 100px", maxWidth: "1600px", margin: "0 auto" }}>
      {/* Header */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          marginBottom: "28px",
          flexWrap: "wrap",
          gap: "16px",
        }}
      >
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
            <Globe2 size={26} style={{ color: "#f49d0d" }} />
            <h1 style={{ fontSize: "24px", fontWeight: 800, letterSpacing: "-0.02em" }}>
              Macro Market Intelligence & Liquidity Architecture
            </h1>
            <span
              style={{
                background: "rgba(16, 185, 129, 0.12)",
                color: "var(--accent-emerald)",
                border: "1px solid rgba(16, 185, 129, 0.3)",
                borderRadius: "20px",
                padding: "2px 10px",
                fontSize: "12px",
                fontWeight: 700,
                display: "inline-flex",
                alignItems: "center",
                gap: "5px",
              }}
            >
              <span style={{ width: "6px", height: "6px", borderRadius: "50%", background: "var(--accent-emerald)", boxShadow: "0 0 6px var(--accent-emerald)" }} />
              Live Telemetry Active
            </span>
          </div>
          <p style={{ color: "var(--text-secondary)", fontSize: "14px", marginTop: "6px" }}>
            Continuous quantitative monitoring of market-wide overextension, perpetual leverage fragility, and liquidity exhaustion across Binance USD-M.
          </p>
        </div>

        {/* Action button */}
        <div style={{ display: "flex", gap: "10px", alignItems: "center" }}>
          <button
            type="button"
            onClick={() => fetchMacroReport(true)}
            disabled={loading}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "8px",
              background: "rgba(255, 255, 255, 0.05)",
              color: "#ffffff",
              border: "1px solid var(--border-medium)",
              fontWeight: 700,
              fontSize: "13px",
              padding: "10px 16px",
              borderRadius: "10px",
              cursor: loading ? "not-allowed" : "pointer",
              transition: "all 0.2s ease",
            }}
          >
            <RefreshCw
              size={15}
              style={{
                animation: loading ? "spin 1s linear infinite" : "none",
                color: "#f49d0d",
              }}
            />
            {loading ? "Calculating Telemetry..." : "Recalculate Macro Heat"}
          </button>
        </div>
      </div>

      {/* Render Main Content if available */}
      {report ? (
        <>
          {/* Hero Risk Radar Gauge */}
          <OverextensionRadarHero report={report} loading={loading} />

          {/* 4 Quantitative Intelligence Pillars */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(350px, 1fr))", gap: "20px", marginBottom: "28px" }}>
            <BenchmarkExcursionCard btc={report.benchmark_btc} eth={report.benchmark_eth} />
            <DerivativesHeatCard heat={report.derivatives_heat} />
            <MarketBreadthCard breadth={report.market_breadth} />
            <LiquidityDivergenceCard liq={report.liquidity_divergence} />
          </div>

          {/* Defense Shelves & Playbook */}
          <DefenseShelvesAndPlaybook
            shelves={report.support_shelves}
            recommendations={report.recommendations}
          />
        </>
      ) : (
        <div style={{ padding: "60px", textAlign: "center", color: "var(--text-secondary)" }}>
          <Activity size={32} style={{ animation: "spin 2s linear infinite", margin: "0 auto 16px", color: "#f49d0d" }} />
          <p style={{ fontSize: "16px", fontWeight: 700 }}>Assembling Multi-Factor Overextension Telemetry...</p>
          <p style={{ fontSize: "13px", color: "var(--text-muted)", marginTop: "4px" }}>
            Fetching Binance USD-M klines, funding rate distribution, and DeFiLlama liquidity...
          </p>
        </div>
      )}
    </div>
  );
}
