"use client";

import { useState, useMemo, useCallback, memo } from "react";
import { Search, ShieldCheck } from "lucide-react";
import type { Candle } from "./TradingChart";

export interface Opportunity {
  symbol: string;
  base_asset?: string;
  direction: string;
  setup_label: string;
  regime: string;
  score: number | string;
  current_price: number | string;
  suggested_entry: number | string;
  pullback_entry?: number | string;
  breakout_trigger?: number | string;
  suggested_stop: number | string;
  suggested_targets: (number | string)[];
  risk_reward_ratio: number | string;
  risk_reward_target_index?: number;
  taker_buy_sell_ratio_window_hours?: number | null;
  taker_buy_sell_ratio_1h?: number | string;
  taker_buy_sell_ratio_4h?: number | string;
  taker_buy_sell_ratio_24h?: number | string;
  estimated_win_rate_pct?: number | string;
  expected_value_r?: number | string;
  poc_price?: number | string;
  vah_price?: number | string;
  val_price?: number | string;
  funding_rate_bps?: number | string;
  oi_change_7d_pct?: number | string;
  taker_ratio?: number | string;
  volume_contraction_ratio?: number | string;
  checklist?: { title: string; passed: boolean; status?: "passed" | "failed" | "unknown"; detail: string }[];
  rationale?: string[];
  macro_narrative?: string;
  candles?: Candle[];
  cvd_24h?: string;
  cvd_z_score?: string;
  orderflow_absorption?: boolean;
  max_recommended_size_usd?: string;
  execution_mode?: "RETEST_ACCUMULATION" | "CEILING_BREAKOUT" | "WAIT_FOR_RETEST_ONLY" | string;
  resistance_absorption_score?: number | string;
  breakout_readiness_status?: "BREAKOUT_CONFIRMED" | "RETEST_ONLY_WAIT_FOR_DIP" | "HIGH_REJECTION_RISK" | string;
  tick_size?: string | number;
  radar_status?: "FRESH_COIL" | "AT_ENTRY_ZONE" | "IN_ACTIVE_TRADE" | "ACTIVE_COIL" | string;
  radar_status_label?: string;
  trade_pnl_pct?: string;
  gates_passed?: number;
  gates_total?: number;
  failed_gates?: string[];
  radar_data_status?: "current" | "unavailable";
  score_breakdown?: {
    trend?: string;
    momentum?: string;
    derivatives?: string;
    alpha?: string;
    volume?: string;
    structure?: string;
    macro_multiplier?: string;
  };
}

export function formatCandidatePrice(price: number, tickSize?: string | number | null): string {
  if (isNaN(price) || price === 0) return "0.00";
  const tick = Number(tickSize);
  if (!isNaN(tick) && tick > 0) {
    const decimals = Math.max(2, -Math.floor(Math.log10(tick)));
    return price.toFixed(decimals);
  }
  if (price < 0.0001) return price.toFixed(6);
  if (price < 0.1) return price.toFixed(4);
  if (price < 1) return price.toFixed(4);
  if (price < 10) return price.toFixed(3);
  if (price < 1000) return price.toFixed(2);
  return price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

interface CandidateCardProps {
  opp: Opportunity;
  isActive: boolean;
  onSelect: (opp: Opportunity) => void;
  price?: number;
  priceChange24h?: number;
}

const CandidateCard = memo(function CandidateCard({
  opp,
  isActive,
  onSelect,
  price,
  priceChange24h,
}: CandidateCardProps) {
  const handleClick = useCallback(() => {
    onSelect(opp);
  }, [onSelect, opp]);

  const isShort = opp.direction?.toLowerCase() === "short";
  const passCount =
    opp.gates_passed !== undefined
      ? opp.gates_passed
      : opp.checklist
        ? opp.checklist.filter((c) => c.passed).length
        : 0;
  const totalCount =
    opp.gates_total !== undefined
      ? opp.gates_total
      : opp.checklist
        ? opp.checklist.length
        : 5;
  const failedGates =
    opp.failed_gates && opp.failed_gates.length > 0
      ? opp.failed_gates
      : opp.checklist
        ? opp.checklist.filter((c) => !c.passed).map((c) => c.title)
        : [];
  const unknownCount = opp.checklist?.filter((c) => c.status === "unknown").length || 0;
  const dataUnavailable = opp.radar_data_status === "unavailable" || unknownCount > 0;
  const isHighRejection = opp.breakout_readiness_status === "HIGH_REJECTION_RISK";
  const isGradeAPlus = totalCount > 0 && passCount === totalCount && !isHighRejection && !dataUnavailable;

  const currentPrice = price !== undefined && !isNaN(price) && price > 0 ? price : Number(opp.current_price || 0);
  const formattedPrice = formatCandidatePrice(currentPrice, opp.tick_size);
  const entryNum = Number(opp.suggested_entry || opp.pullback_entry || 0);
  const formattedEntry = entryNum > 0 ? formatCandidatePrice(entryNum, opp.tick_size) : null;

  return (
    <div
      onClick={handleClick}
      style={{
        padding: "12px 14px",
        borderRadius: "10px",
        marginBottom: "8px",
        cursor: "pointer",
        background: isActive ? "var(--bg-card-active)" : "var(--bg-card)",
        border: `1px solid ${isActive ? (isShort ? "var(--accent-crimson)" : "#f49d0d") : "var(--border-subtle)"}`,
        boxShadow: isActive ? `0 0 16px ${isShort ? "rgba(239, 68, 68, 0.25)" : "rgba(244, 157, 13, 0.2)"}` : "none",
        transition: "all 0.18s ease",
      }}
    >
      {/* Header row: Symbol + Direction Badge + Score */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
          <span style={{ fontWeight: 800, fontSize: "14px", color: "var(--text-primary)" }}>
            {opp.symbol}
          </span>
          <span
            style={{
              fontSize: "9px",
              fontWeight: 800,
              padding: "1px 5px",
              borderRadius: "4px",
              letterSpacing: "0.04em",
              background: isShort ? "rgba(239, 68, 68, 0.15)" : "rgba(16, 185, 129, 0.15)",
              color: isShort ? "var(--accent-crimson)" : "var(--accent-emerald)",
              border: `1px solid ${isShort ? "rgba(239, 68, 68, 0.3)" : "rgba(16, 185, 129, 0.3)"}`,
            }}
          >
            {isShort ? "SHORT" : "LONG"}
          </span>
          {dataUnavailable ? (
            <span title="Some lifecycle checks lack fresh market data" style={{ fontSize: "9px", color: "#f49d0d" }}>
              DATA INCOMPLETE
            </span>
          ) : isGradeAPlus ? (
            <span
              className="badge-grade-a-plus"
              style={{ fontSize: "9px", padding: "1px 5px" }}
              title="All displayed screening checks passed at the last verification"
            >
              <ShieldCheck size={10} /> {passCount}/{totalCount}
            </span>
          ) : passCount < totalCount ? (
            <span
              style={{
                fontSize: "9px",
                fontWeight: 800,
                padding: "1px 5px",
                borderRadius: "4px",
                background: passCount <= 3 ? "rgba(239, 68, 68, 0.16)" : "rgba(244, 157, 13, 0.16)",
                color: passCount <= 3 ? "var(--accent-crimson)" : "#f49d0d",
                border: `1px solid ${passCount <= 3 ? "rgba(239, 68, 68, 0.4)" : "rgba(244, 157, 13, 0.4)"}`,
              }}
              title={`Gate Drift Warning — Failed: ${failedGates.join(", ")}`}
            >
              {passCount}/{totalCount} GATES
            </span>
          ) : isHighRejection ? (
            <span className="badge-grade-b" style={{ fontSize: "9px", padding: "1px 5px" }}>
              GRADE B
            </span>
          ) : null}
        </div>
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: "12px",
            fontWeight: 700,
            color: Number(opp.score) >= 60 ? "var(--accent-emerald)" : "#f49d0d",
            background: "rgba(255, 255, 255, 0.05)",
            padding: "2px 6px",
            borderRadius: "6px",
          }}
        >
          {Number(opp.score).toFixed(1)}
        </span>
      </div>

      {/* Row 2: Setup label on left + Real-time Live Price & 24h change on right */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "baseline",
          marginTop: "5px",
          gap: "8px",
        }}
      >
        <div
          style={{
            fontSize: "10px",
            fontWeight: 600,
            color: isShort ? "var(--accent-crimson)" : "#f49d0d",
            letterSpacing: "0.04em",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {opp.setup_label}
        </div>
        <div style={{ display: "flex", alignItems: "baseline", gap: "5px", flexShrink: 0 }}>
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: "13px",
              fontWeight: 800,
              color: "var(--text-primary)",
              letterSpacing: "-0.01em",
            }}
          >
            ${formattedPrice}
          </span>
          {priceChange24h !== undefined && !isNaN(priceChange24h) && (
            <span
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: "10px",
                fontWeight: 700,
                color: priceChange24h >= 0 ? "var(--accent-emerald)" : "var(--accent-crimson)",
              }}
            >
              {priceChange24h >= 0 ? "+" : ""}{priceChange24h.toFixed(1)}%
            </span>
          )}
        </div>
      </div>

      {/* Key Metrics row: 7d OI + Uncalibrated */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginTop: "8px",
          fontSize: "11px",
          fontFamily: "var(--font-mono)",
          color: "var(--text-secondary)",
        }}
      >
        <span>
          7d OI:{" "}
          <strong
            style={{
              color:
                String(opp.oi_change_7d_pct).includes("-") ? "var(--accent-crimson)" : "var(--accent-emerald)",
            }}
          >
            {opp.oi_change_7d_pct || "N/A"}
          </strong>
        </span>
        {opp.estimated_win_rate_pct !== undefined && (
          <span
            style={{
              fontSize: "10px",
              fontWeight: 700,
              padding: "1px 5px",
              borderRadius: "4px",
              background: "rgba(244, 157, 13, 0.12)",
              color: "#f49d0d",
              border: "1px solid rgba(244, 157, 13, 0.25)",
            }}
            title="Research score has not been calibrated against forward outcomes"
          >
            Uncalibrated
          </span>
        )}
      </div>

      {/* Bottom Line: Status text on left + Entry on right (no badge box) */}
      {(opp.radar_status_label || formattedEntry) && (
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            marginTop: "7px",
            paddingTop: "6px",
            borderTop: "1px solid rgba(255, 255, 255, 0.06)",
            gap: "8px",
            fontSize: "10.5px",
            fontFamily: "var(--font-mono)",
          }}
        >
          <span
            style={{
              fontWeight: 700,
              whiteSpace: "nowrap",
              color:
                opp.radar_status === "IN_ACTIVE_TRADE" || opp.radar_status === "AT_ENTRY_ZONE"
                  ? "var(--accent-emerald)"
                  : passCount < totalCount
                    ? "#f49d0d"
                    : "#60a5fa",
            }}
          >
            {(opp.radar_status_label || "ACTIVE SETUP").replace(/^[^\w\s]+\s*/, "")}
          </span>
          {formattedEntry && (
            <span
              style={{
                color: "var(--text-secondary)",
                whiteSpace: "nowrap",
              }}
            >
              Entry:{" "}
              <strong style={{ color: "var(--text-primary)" }}>
                ${formattedEntry}
              </strong>
            </span>
          )}
        </div>
      )}
    </div>
  );
});

export interface CandidateSidebarProps {
  opportunities: Opportunity[];
  activeSymbol: string;
  onSelect: (opp: Opportunity) => void;
  livePrices?: Record<string, number>;
  priceChanges24h?: Record<string, number>;
}

function CandidateSidebar({
  opportunities,
  activeSymbol,
  onSelect,
  livePrices,
  priceChanges24h,
}: CandidateSidebarProps) {
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<"ALL" | "LONGS" | "SHORTS" | "COIL" | "OI">("ALL");

  // Memoize counts across tabs to avoid re-filtering on every keystroke
  const counts = useMemo(() => {
    let longs = 0;
    let shorts = 0;
    for (let i = 0; i < opportunities.length; i++) {
      if (opportunities[i].direction?.toLowerCase() === "short") {
        shorts++;
      } else {
        longs++;
      }
    }
    return {
      all: opportunities.length,
      longs,
      shorts,
    };
  }, [opportunities]);

  // Memoize filtered candidates list
  const filteredOpps = useMemo(() => {
    const query = search.trim().toLowerCase();
    return opportunities.filter((opp) => {
      if (query && !opp.symbol.toLowerCase().includes(query)) {
        return false;
      }
      const isShort = opp.direction?.toLowerCase() === "short";
      if (filter === "LONGS") return !isShort;
      if (filter === "SHORTS") return isShort;
      if (filter === "COIL") return opp.setup_label.includes("COIL") || opp.setup_label.includes("VCP");
      if (filter === "OI") return opp.setup_label.includes("OI");
      return true;
    });
  }, [opportunities, search, filter]);

  return (
    <aside
      className="candidate-sidebar"
      style={{
        width: "320px",
        height: "100%",
        display: "flex",
        flexDirection: "column",
        background: "rgba(11, 15, 25, 0.98)",
        borderRight: "1px solid var(--border-subtle)",
        zIndex: 20,
      }}
    >
      {/* Search Input */}
      <div style={{ padding: "14px 16px 10px", borderBottom: "1px solid var(--border-subtle)" }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "8px",
            background: "rgba(255, 255, 255, 0.04)",
            border: "1px solid var(--border-subtle)",
            borderRadius: "8px",
            padding: "8px 12px",
          }}
        >
          <Search size={15} style={{ color: "var(--text-muted)" }} />
          <input
            type="text"
            placeholder="Search symbols (e.g. ONT, DIST)..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{
              background: "transparent",
              border: "none",
              color: "var(--text-primary)",
              fontSize: "12px",
              fontFamily: "var(--font-main)",
              width: "100%",
              outline: "none",
            }}
          />
        </div>

        {/* Filter Pills */}
        <div style={{ display: "flex", flexWrap: "wrap", gap: "5px", marginTop: "10px" }}>
          {(["ALL", "LONGS", "SHORTS", "COIL", "OI"] as const).map((tab) => {
            const isTabActive = filter === tab;
            const count =
              tab === "ALL"
                ? counts.all
                : tab === "LONGS"
                ? counts.longs
                : tab === "SHORTS"
                ? counts.shorts
                : undefined;

            return (
              <button
                key={tab}
                type="button"
                onClick={() => setFilter(tab)}
                style={{
                  background: isTabActive ? "rgba(244, 157, 13, 0.15)" : "transparent",
                  color: isTabActive
                    ? tab === "SHORTS"
                      ? "var(--accent-crimson)"
                      : tab === "LONGS"
                      ? "var(--accent-emerald)"
                      : "#f49d0d"
                    : "var(--text-muted)",
                  border: `1px solid ${
                    isTabActive
                      ? tab === "SHORTS"
                        ? "rgba(239, 68, 68, 0.4)"
                        : tab === "LONGS"
                        ? "rgba(16, 185, 129, 0.4)"
                        : "rgba(244, 157, 13, 0.35)"
                      : "var(--border-subtle)"
                  }`,
                  borderRadius: "6px",
                  padding: "3px 8px",
                  fontSize: "10px",
                  fontWeight: 700,
                  cursor: "pointer",
                  transition: "all 0.15s ease",
                }}
              >
                {tab === "ALL" && `All (${counts.all})`}
                {tab === "LONGS" && `Longs (${count})`}
                {tab === "SHORTS" && `Shorts (${count})`}
                {tab === "COIL" && "Coils"}
                {tab === "OI" && "OI Accum"}
              </button>
            );
          })}
        </div>
      </div>

      {/* Candidate List */}
      <div className="candidate-list-container" style={{ flex: 1, overflowY: "auto", padding: "10px" }}>
        {filteredOpps.length === 0 ? (
          <div style={{ textAlign: "center", padding: "40px 16px", color: "var(--text-muted)", fontSize: "12px" }}>
            No tokens matching filter.
          </div>
        ) : (
          filteredOpps.map((opp) => (
            <CandidateCard
              key={opp.symbol}
              opp={opp}
              isActive={opp.symbol === activeSymbol}
              onSelect={onSelect}
              price={livePrices?.[opp.symbol]}
              priceChange24h={priceChanges24h?.[opp.symbol]}
            />
          ))
        )}
      </div>
    </aside>
  );
}

export default memo(CandidateSidebar);
