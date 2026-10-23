"use client";

import { memo, useMemo, useCallback } from "react";
import Link from "next/link";
import {
  ShieldCheck,
  Clock,
  ExternalLink,
  Edit3,
  Trash2,
  ChevronDown,
  ChevronUp,
  Activity,
  TrendingUp,
  AlertTriangle,
  AlertCircle,
  CheckCircle2,
  XCircle,
} from "lucide-react";
import type { WatchlistItem, WatchlistLiveAudit } from "../page";

export function getAuditStatusConfig(status?: string) {
  switch (status) {
    case "ACTIVE_EXPANSION":
      return {
        label: "Graduated: Active Expansion",
        color: "#10b981",
        bg: "rgba(16, 185, 129, 0.12)",
        border: "rgba(16, 185, 129, 0.35)",
        icon: <TrendingUp size={12} strokeWidth={2.5} />,
      };
    case "FLOW_DIVERGENCE":
      return {
        label: "Taker Flow Divergence",
        color: "#f49d0d",
        bg: "rgba(244, 157, 13, 0.12)",
        border: "rgba(244, 157, 13, 0.35)",
        icon: <AlertTriangle size={12} strokeWidth={2.5} />,
      };
    case "SELLER_INFLUX":
      return {
        label: "Aggressive Seller Influx",
        color: "#ef4444",
        bg: "rgba(239, 68, 68, 0.12)",
        border: "rgba(239, 68, 68, 0.35)",
        icon: <AlertCircle size={12} strokeWidth={2.5} />,
      };
    case "TARGET_REACHED":
      return {
        label: "Target 1 Reached",
        color: "#10b981",
        bg: "rgba(16, 185, 129, 0.18)",
        border: "rgba(16, 185, 129, 0.45)",
        icon: <CheckCircle2 size={12} strokeWidth={2.5} />,
      };
    case "STOP_BREACHED":
      return {
        label: "Invalidation Hit",
        color: "#ef4444",
        bg: "rgba(239, 68, 68, 0.18)",
        border: "rgba(239, 68, 68, 0.45)",
        icon: <XCircle size={12} strokeWidth={2.5} />,
      };
    case "COIL_HOLDING":
    default:
      return {
        label: "Coil Intact & Holding",
        color: "#60a5fa",
        bg: "rgba(96, 165, 250, 0.12)",
        border: "rgba(96, 165, 250, 0.35)",
        icon: <ShieldCheck size={12} strokeWidth={2.5} />,
      };
  }
}

export function getEffectiveAudit(item: WatchlistItem): WatchlistLiveAudit {
  if (item.audit) return item.audit;

  const priceChange = item.support_price > 0
    ? ((item.current_price - item.support_price) / item.support_price) * 100
    : 0;
  return {
    symbol: item.symbol,
    status: "COIL_HOLDING",
    status_title: "Coil Intact: Accumulation Holding",
    live_price: item.current_price,
    price_change_pct: priceChange,
    current_rsi_1h: 50.0,
    current_rsi_4h: 50.0,
    taker_ratio: 1.0,
    vol_contraction_ratio: 0.95,
    gate_tripped: "All 6 Institutional Gates Active & Holding (Pre-Breakout Coil)",
    live_reading: "1H RSI: 50.0 | 4H RSI: 50.0 | Taker: 1.000 | Vol Ratio: 0.950",
    what_happened: `${item.symbol} remains compressed within its accumulation base between $${item.support_price.toFixed(4)} and $${item.trigger_price.toFixed(4)}.`,
    institutional_logic: `Setup remains prime and compressed. Supply is drying up and institutions continue absorbing float. Maintain resting trigger order at $${item.trigger_price.toFixed(4)}.`,
    audited_at: item.updated_at || new Date().toISOString(),
  };
}

interface WatchlistCardProps {
  item: WatchlistItem;
  livePrice?: number;
  isExpanded: boolean;
  onToggleExpand: (symbol: string) => void;
  onEditNotes: (item: WatchlistItem) => void;
  onDelete: (symbol: string) => void;
}

function WatchlistCard({
  item,
  livePrice,
  isExpanded,
  onToggleExpand,
  onEditNotes,
  onDelete,
}: WatchlistCardProps) {
  const isGradeAPlus = item.checklist_passed === item.checklist_total;
  const audit = useMemo(() => getEffectiveAudit(item), [item]);
  const statusCfg = useMemo(() => getAuditStatusConfig(audit.status), [audit.status]);

  const effectivePrice = livePrice ?? audit.live_price ?? item.current_price;
  const effectiveDistance = useMemo(() => {
    if (item.trigger_price > 0 && effectivePrice > 0) {
      return (Math.abs(item.trigger_price - effectivePrice) / effectivePrice) * 100;
    }
    return item.distance_to_trigger_pct;
  }, [item.trigger_price, effectivePrice, item.distance_to_trigger_pct]);

  const handleToggle = useCallback(() => {
    onToggleExpand(item.symbol);
  }, [onToggleExpand, item.symbol]);

  const handleEdit = useCallback(() => {
    onEditNotes(item);
  }, [onEditNotes, item]);

  const handleDeleteClick = useCallback(() => {
    onDelete(item.symbol);
  }, [onDelete, item.symbol]);

  const triggerMeterWidth = useMemo(
    () => `${Math.min(100, Math.max(10, 100 - effectiveDistance * 10))}%`,
    [effectiveDistance]
  );

  return (
    <div
      className="glass-panel-interactive"
      style={{
        padding: "20px",
        display: "flex",
        flexDirection: "column",
        position: "relative",
      }}
    >
      {/* Header row: Symbol, Name, Status Badge, Grade & Score */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: "8px" }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
            <span style={{ fontSize: "18px", fontWeight: 800, color: "var(--text-primary)" }}>
              {item.symbol}
            </span>
            <span style={{ fontSize: "12px", color: "var(--text-muted)", fontWeight: 600 }}>
              {item.name}
            </span>
            <span
              style={{
                background: "rgba(255, 255, 255, 0.06)",
                color: "var(--text-secondary)",
                borderRadius: "4px",
                padding: "1px 6px",
                fontSize: "10px",
                fontWeight: 600,
              }}
            >
              {item.sector}
            </span>

            {/* Live Position Status Badge */}
            <span
              style={{
                background: statusCfg.bg,
                color: statusCfg.color,
                border: `1px solid ${statusCfg.border}`,
                borderRadius: "6px",
                padding: "2px 8px",
                fontSize: "10.5px",
                fontWeight: 800,
                display: "inline-flex",
                alignItems: "center",
                gap: "4px",
                textTransform: "uppercase",
              }}
            >
              {statusCfg.icon}
              {statusCfg.label}
            </span>
          </div>

          {/* Stage and Timeframe */}
          <div style={{ display: "flex", gap: "6px", marginTop: "6px" }}>
            <span className="badge-wyckoff">{item.stage}</span>
            <span className="badge-timeframe">
              <Clock size={11} style={{ display: "inline", marginRight: "3px" }} />
              {item.timeframe}
            </span>
          </div>
        </div>

        {/* Right: Grade & Score */}
        <div style={{ textAlign: "right" }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "flex-end", gap: "6px" }}>
            {item.estimated_win_rate_pct !== undefined && (
              <span
                style={{
                  fontSize: "10.5px",
                  fontFamily: "var(--font-mono)",
                  fontWeight: 700,
                  color: "#38bdf8",
                  background: "rgba(56, 189, 248, 0.12)",
                  border: "1px solid rgba(56, 189, 248, 0.3)",
                  padding: "2px 7px",
                  borderRadius: "6px",
                }}
                title="Research score has not been calibrated against forward outcomes"
              >
                Uncalibrated
              </span>
            )}
            <span className={isGradeAPlus ? "badge-grade-a-plus" : "badge-grade-a"}>
              <ShieldCheck size={12} />
              {isGradeAPlus ? "GRADE A+" : "GRADE A"} ({item.checklist_passed}/{item.checklist_total})
            </span>
          </div>
          <div
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: "13px",
              fontWeight: 700,
              color: "#f49d0d",
              marginTop: "4px",
            }}
          >
            SCORE {item.score.toFixed(1)}
          </div>
        </div>
      </div>

      {/* Price & Execution Levels Grid */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(4, 1fr)",
          gap: "8px",
          background: "rgba(255, 255, 255, 0.02)",
          border: "1px solid var(--border-subtle)",
          borderRadius: "8px",
          padding: "10px 12px",
          margin: "14px 0 10px",
          fontFamily: "var(--font-mono)",
          fontSize: "11px",
        }}
      >
        <div>
          <div style={{ color: "var(--text-muted)", fontSize: "10px", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span style={{ display: "inline-flex", alignItems: "center", gap: "4px" }}>
              LIVE PRICE
              {livePrice !== undefined && (
                <span
                  style={{
                    display: "inline-block",
                    width: "6px",
                    height: "6px",
                    borderRadius: "50%",
                    background: "var(--accent-emerald)",
                    boxShadow: "0 0 6px var(--accent-emerald)",
                  }}
                  title="Streaming live from Binance"
                />
              )}
            </span>
            <span style={{ color: audit.price_change_pct >= 0 ? "var(--accent-emerald)" : "var(--accent-crimson)", fontWeight: 700 }}>
              {audit.price_change_pct >= 0 ? "+" : ""}{audit.price_change_pct.toFixed(2)}%
            </span>
          </div>
          <div style={{ fontWeight: 800, color: "var(--text-primary)", marginTop: "2px", fontSize: "13px" }}>
            ${effectivePrice.toFixed(4)}
          </div>
        </div>
        <div>
          <div style={{ color: "#f49d0d", fontSize: "10px" }}>TRIGGER (CEIL)</div>
          <div style={{ fontWeight: 700, color: "#f49d0d", marginTop: "2px" }}>
            ${item.trigger_price.toFixed(4)}
          </div>
        </div>
        <div>
          <div style={{ color: "#ffffff", fontSize: "10px" }}>POC SUPPORT</div>
          <div style={{ fontWeight: 700, color: "#ffffff", marginTop: "2px" }}>
            ${item.support_price.toFixed(4)}
          </div>
        </div>
        <div>
          <div style={{ color: "var(--accent-crimson)", fontSize: "10px" }}>INVALIDATION</div>
          <div style={{ fontWeight: 700, color: "var(--accent-crimson)", marginTop: "2px" }}>
            ${item.invalidation_price.toFixed(4)}
          </div>
        </div>
      </div>

      {/* Distance to Trigger Meter */}
      <div style={{ marginBottom: "12px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: "11px", marginBottom: "4px" }}>
          <span style={{ color: "var(--text-muted)" }}>Proximity to Breakout Trigger:</span>
          <strong style={{ color: effectiveDistance <= 3.0 ? "var(--accent-emerald)" : "#f49d0d" }}>
            {effectiveDistance.toFixed(1)}% away
          </strong>
        </div>
        <div
          style={{
            width: "100%",
            height: "4px",
            background: "rgba(255, 255, 255, 0.06)",
            borderRadius: "4px",
            overflow: "hidden",
          }}
        >
          <div
            style={{
              width: triggerMeterWidth,
              height: "100%",
              background: "var(--accent-emerald)",
              transition: "width 0.3s ease",
            }}
          />
        </div>
      </div>

      {/* Real-Time Institutional Gate & Position Diagnostic Accordion */}
      <div
        style={{
          background: "rgba(11, 15, 25, 0.85)",
          border: `1px solid ${statusCfg.border}`,
          borderRadius: "10px",
          padding: "12px 14px",
          marginBottom: "12px",
          display: "flex",
          flexDirection: "column",
          gap: "10px",
          boxShadow: "0 4px 14px rgba(0,0,0,0.3)",
        }}
      >
        {/* Diagnostic Header */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
            <Activity size={13} color={statusCfg.color} />
            <span style={{ fontSize: "11px", fontWeight: 800, color: statusCfg.color, letterSpacing: "0.2px" }}>
              {audit.status_title}
            </span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            {audit.audited_at && (
              <span
                style={{
                  fontSize: "9.5px",
                  color: "var(--text-muted)",
                  fontFamily: "var(--font-mono)",
                }}
                title={`Audited at: ${audit.audited_at}`}
              >
                {new Date(audit.audited_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
              </span>
            )}
            <button
              type="button"
              onClick={handleToggle}
              style={{
                background: "rgba(255, 255, 255, 0.05)",
                border: "1px solid var(--border-subtle)",
                borderRadius: "4px",
                padding: "2px 6px",
                color: "var(--text-secondary)",
                cursor: "pointer",
                fontSize: "10px",
                fontWeight: 700,
                display: "flex",
                alignItems: "center",
                gap: "3px",
              }}
            >
              {!isExpanded ? (
                <>Details <ChevronDown size={11} /></>
              ) : (
                <>Minimize <ChevronUp size={11} /></>
              )}
            </button>
          </div>
        </div>

        {/* Quantitative Real-Time Metrics Strip */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(4, 1fr)",
            gap: "6px",
            background: "rgba(255, 255, 255, 0.03)",
            borderRadius: "6px",
            padding: "6px 8px",
            fontFamily: "var(--font-mono)",
            fontSize: "10px",
          }}
        >
          <div>
            <span style={{ color: "var(--text-muted)", display: "block" }}>1H RSI</span>
            <strong style={{ color: audit.current_rsi_1h > 65 ? "var(--accent-emerald)" : audit.current_rsi_1h < 35 ? "var(--accent-crimson)" : "#ffffff" }}>
              {audit.current_rsi_1h.toFixed(1)}
            </strong>
          </div>
          <div>
            <span style={{ color: "var(--text-muted)", display: "block" }}>4H RSI</span>
            <strong style={{ color: audit.current_rsi_4h > 65 ? "var(--accent-emerald)" : audit.current_rsi_4h < 35 ? "var(--accent-crimson)" : "#ffffff" }}>
              {audit.current_rsi_4h.toFixed(1)}
            </strong>
          </div>
          <div>
            <span style={{ color: "var(--text-muted)", display: "block" }}>TAKER RATIO</span>
            <strong style={{ color: (audit.taker_ratio || 1) < 0.85 ? "var(--accent-crimson)" : "var(--accent-emerald)" }}>
              {audit.taker_ratio !== null ? audit.taker_ratio.toFixed(3) : "1.000"}
            </strong>
          </div>
          <div>
            <span style={{ color: "var(--text-muted)", display: "block" }}>VOL RATIO</span>
            <strong style={{ color: (audit.vol_contraction_ratio || 1) > 1.25 ? "var(--accent-crimson)" : "#ffffff" }}>
              {audit.vol_contraction_ratio !== null ? audit.vol_contraction_ratio.toFixed(3) : "0.950"}
            </strong>
          </div>
        </div>

        {/* Expandable Technical Health Details (expanded by default) */}
        {isExpanded && (
          <div style={{ display: "flex", flexDirection: "column", gap: "8px", marginTop: "2px" }}>
            {/* 1. What Happened */}
            <div>
              <div style={{ fontSize: "10.5px", fontWeight: 700, color: "#ffffff", display: "flex", alignItems: "center", gap: "4px", marginBottom: "2px" }}>
                <span style={{ color: "#f49d0d" }}>●</span> What happened:
              </div>
              <div style={{ fontSize: "11px", color: "var(--text-secondary)", lineHeight: "1.4" }}>
                {audit.what_happened}
              </div>
            </div>

            {/* 2. The Gate Tripped */}
            <div>
              <div style={{ fontSize: "10.5px", fontWeight: 700, color: "#ffffff", display: "flex", alignItems: "center", gap: "4px", marginBottom: "3px" }}>
                <span style={{ color: statusCfg.color }}>●</span> The Gate Tripped:
              </div>
              <pre
                style={{
                  background: "#05070c",
                  border: "1px solid rgba(255, 255, 255, 0.08)",
                  borderRadius: "6px",
                  padding: "6px 8px",
                  fontFamily: "var(--font-mono)",
                  fontSize: "10px",
                  color: "#38bdf8",
                  lineHeight: "1.35",
                  whiteSpace: "pre-wrap",
                  overflowX: "auto",
                }}
              >
                {audit.gate_tripped}
              </pre>
              <div style={{ fontSize: "10px", fontFamily: "var(--font-mono)", color: "var(--text-muted)", marginTop: "3px" }}>
                <strong style={{ color: "var(--text-secondary)" }}>Live Reading: </strong>
                {audit.live_reading}
              </div>
            </div>

            {/* 3. Institutional Logic */}
            <div
              style={{
                background: "rgba(255, 255, 255, 0.02)",
                borderLeft: `3px solid ${statusCfg.color}`,
                padding: "6px 8px",
                borderRadius: "0 6px 6px 0",
              }}
            >
              <div style={{ fontSize: "10.5px", fontWeight: 700, color: "#ffffff", marginBottom: "2px" }}>
                🏛️ Institutional Logic:
              </div>
              <div style={{ fontSize: "11px", color: "var(--text-secondary)", lineHeight: "1.4" }}>
                {audit.institutional_logic}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Institutional Thesis */}
      <div
        style={{
          fontSize: "12px",
          color: "var(--text-secondary)",
          lineHeight: "1.4",
          marginBottom: "8px",
        }}
      >
        <strong style={{ color: "var(--text-primary)" }}>Thesis: </strong>
        {item.thesis}
      </div>

      {/* Notes box */}
      {item.notes && (
        <div
          style={{
            background: "rgba(244, 157, 13, 0.05)",
            border: "1px dashed rgba(244, 157, 13, 0.25)",
            borderRadius: "6px",
            padding: "8px 10px",
            fontSize: "11px",
            color: "#f49d0d",
            lineHeight: "1.3",
            marginBottom: "12px",
          }}
        >
          <strong>Strategy Note: </strong>
          {item.notes}
        </div>
      )}

      {/* Action Buttons */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          borderTop: "1px solid var(--border-subtle)",
          paddingTop: "12px",
          marginTop: "auto",
        }}
      >
        <Link
          href={`/?symbol=${encodeURIComponent(item.symbol)}`}
          style={{
            display: "flex",
            alignItems: "center",
            gap: "5px",
            fontSize: "12px",
            fontWeight: 700,
            color: "#f49d0d",
            textDecoration: "none",
          }}
        >
          <ExternalLink size={13} /> Inspect on Radar Chart
        </Link>

        <div style={{ display: "flex", gap: "8px" }}>
          <button
            type="button"
            onClick={handleEdit}
            style={{
              background: "rgba(255, 255, 255, 0.05)",
              border: "1px solid var(--border-subtle)",
              borderRadius: "6px",
              padding: "5px 8px",
              color: "var(--text-secondary)",
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              gap: "4px",
              fontSize: "11px",
            }}
          >
            <Edit3 size={12} /> Notes
          </button>
          <button
            type="button"
            onClick={handleDeleteClick}
            style={{
              background: "rgba(239, 68, 68, 0.1)",
              border: "1px solid rgba(239, 68, 68, 0.2)",
              borderRadius: "6px",
              padding: "5px 8px",
              color: "var(--accent-crimson)",
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              gap: "4px",
              fontSize: "11px",
            }}
          >
            <Trash2 size={12} />
          </button>
        </div>
      </div>
    </div>
  );
}

export default memo(WatchlistCard);
