"use client";

import { useState, useMemo, useCallback, memo } from "react";
import {
  Check,
  X,
  ShieldCheck,
  BookmarkPlus,
  CheckCircle2,
} from "lucide-react";
import { Opportunity } from "./CandidateSidebar";

export interface AdaptedLevels {
  pocPrice?: number;
  vahPrice?: number;
  valPrice?: number;
  entryPrice?: number;
  stopPrice?: number;
  breakoutTrigger?: number;
  targets?: number[];
}

interface VerificationPanelProps {
  opportunity: Opportunity | null;
  onWatchlistAdded?: () => void;
  adaptedLevels?: AdaptedLevels;
  livePrice?: number;
}

interface ChecklistItemProps {
  title: string;
  passed: boolean;
  detail: string;
}

const ChecklistItemRow = memo(function ChecklistItemRow({
  title,
  passed,
  detail,
}: ChecklistItemProps) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: "10px",
        padding: "10px 12px",
        borderRadius: "8px",
        background: passed
          ? "rgba(16, 185, 129, 0.05)"
          : "rgba(239, 68, 68, 0.05)",
        border: `1px solid ${passed ? "rgba(16, 185, 129, 0.2)" : "rgba(239, 68, 68, 0.2)"}`,
      }}
    >
      <div
        style={{
          width: "20px",
          height: "20px",
          borderRadius: "50%",
          background: passed
            ? "rgba(16, 185, 129, 0.2)"
            : "rgba(239, 68, 68, 0.2)",
          color: passed ? "var(--accent-emerald)" : "var(--accent-crimson)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexShrink: 0,
          marginTop: "1px",
        }}
      >
        {passed ? (
          <Check size={13} strokeWidth={3} />
        ) : (
          <X size={13} strokeWidth={3} />
        )}
      </div>
      <div>
        <div
          style={{
            fontSize: "12px",
            fontWeight: 700,
            color: passed ? "var(--text-primary)" : "var(--accent-crimson)",
          }}
        >
          {title}
        </div>
        <div
          style={{
            fontSize: "11px",
            color: "var(--text-muted)",
            marginTop: "2px",
            lineHeight: "1.3",
          }}
        >
          {detail}
        </div>
      </div>
    </div>
  );
});

function getPrecision(
  tickSize?: string | number | null,
  fallbackPrice?: string | number | null,
): number {
  const tick = Number(tickSize);
  if (!isNaN(tick) && tick > 0) {
    return Math.max(2, -Math.floor(Math.log10(tick)));
  }
  const p = Number(fallbackPrice);
  if (!isNaN(p) && p > 0) {
    if (p < 0.0001) return 6;
    if (p < 1) return 4;
    if (p < 10) return 3;
    return 2;
  }
  return 4;
}

function VerificationPanel({
  opportunity,
  onWatchlistAdded,
  adaptedLevels,
  livePrice,
}: VerificationPanelProps) {
  const [added, setAdded] = useState(false);
  const [adding, setAdding] = useState(false);

  const decimals = useMemo(() => {
    return getPrecision(
      opportunity?.tick_size,
      livePrice ?? opportunity?.current_price,
    );
  }, [opportunity?.tick_size, livePrice, opportunity?.current_price]);

  const checklist = useMemo(() => {
    const raw = opportunity?.checklist || [];
    if (!adaptedLevels?.pocPrice) return raw;
    return raw.map((item) => {
      let detail = item.detail;
      if (item.title === "Value Acceptance" && adaptedLevels.pocPrice) {
        detail = `Accepted above 30d POC ($${adaptedLevels.pocPrice.toFixed(decimals)})${adaptedLevels.vahPrice ? ` | VAH: $${adaptedLevels.vahPrice.toFixed(decimals)}` : ""}`;
      }
      return { ...item, detail };
    });
  }, [opportunity?.checklist, adaptedLevels, decimals]);

  const passCount = useMemo(
    () => checklist.filter((c) => c.passed).length,
    [checklist],
  );
  const totalCount = checklist.length || 5;
  const isHighRejection =
    opportunity?.breakout_readiness_status === "HIGH_REJECTION_RISK";
  const isGradeAPlus = passCount === totalCount && !isHighRejection;
  const isShort = opportunity?.direction?.toLowerCase() === "short";

  // Memoize bracket calculations with adapted live levels and dynamic tick precision
  const bracket = useMemo(() => {
    if (!opportunity) return null;
    const currentPriceNum = livePrice ?? Number(opportunity.current_price || 0);
    const stopPrice =
      adaptedLevels?.stopPrice ?? Number(opportunity.suggested_stop || 0);
    const entryPrice =
      adaptedLevels?.entryPrice ??
      Number(opportunity.pullback_entry || opportunity.suggested_entry || 0);
    const triggerPrice =
      adaptedLevels?.breakoutTrigger ??
      Number(
        opportunity.breakout_trigger ||
          currentPriceNum * (isShort ? 0.975 : 1.025),
      );
    const targets =
      adaptedLevels?.targets && adaptedLevels.targets.length > 0
        ? adaptedLevels.targets
        : (opportunity.suggested_targets || []).map((t) => Number(t || 0));

    return {
      currentPriceNum,
      stopPrice: stopPrice.toFixed(decimals),
      entryPrice: entryPrice.toFixed(decimals),
      triggerPrice: triggerPrice.toFixed(decimals),
      rawTriggerPrice: triggerPrice,
      target1: (targets[0] || 0).toFixed(decimals),
      target2: (targets[1] || 0).toFixed(decimals),
      target3: (targets[2] || 0).toFixed(decimals),
      rawTarget1: targets[0] || currentPriceNum,
      rawTarget2: targets[1] || currentPriceNum,
    };
  }, [opportunity, isShort, adaptedLevels, livePrice, decimals]);

  const handleAddToWatchlist = useCallback(async () => {
    if (!opportunity || !bracket || adding || added) return;
    setAdding(true);
    try {
      const payload = {
        symbol: opportunity.symbol,
        name: opportunity.symbol.replace("USDT", ""),
        sector: "Layer 1 / DeFi",
        stage: isShort
          ? opportunity.setup_label.includes("UTAD")
            ? "Phase C: UTAD Distribution Short"
            : "Phase D: Breakdown Coil Short"
          : opportunity.setup_label.includes("VCP")
            ? "VCP: Volatility Compression"
            : "Phase C: Spring / Shakeout Test",
        timeframe: "1–3 Days",
        current_price: bracket.currentPriceNum,
        trigger_price: Number(
          isShort
            ? opportunity.breakout_trigger || bracket.currentPriceNum
            : opportunity.breakout_trigger ||
                bracket.rawTarget1 ||
                bracket.currentPriceNum,
        ),
        support_price: Number(
          opportunity.poc_price ||
            opportunity.suggested_entry ||
            bracket.currentPriceNum,
        ),
        invalidation_price: Number(opportunity.suggested_stop),
        target_1: Number(bracket.rawTarget1),
        target_2: Number(bracket.rawTarget2),
        distance_to_trigger_pct: Number(
          opportunity.breakout_trigger && bracket.currentPriceNum > 0
            ? Math.abs(
                ((Number(opportunity.breakout_trigger) -
                  bracket.currentPriceNum) /
                  bracket.currentPriceNum) *
                  100,
              ).toFixed(2)
            : 2.5,
        ),
        score: Number(opportunity.score),
        thesis: (opportunity.rationale || []).join(" | "),
        checklist_passed: passCount,
        checklist_total: totalCount,
        priority: isGradeAPlus ? "HIGH" : "MEDIUM",
        notes: `Added from Radar. 7d OI: ${opportunity.oi_change_7d_pct || "N/A"}.`,
      };

      const res = await fetch("/api/watchlist", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (res.ok) {
        setAdded(true);
        if (onWatchlistAdded) onWatchlistAdded();
        setTimeout(() => setAdded(false), 3000);
      }
    } catch (e) {
      console.error("Failed to add to watchlist:", e);
    } finally {
      setAdding(false);
    }
  }, [
    opportunity,
    bracket,
    adding,
    added,
    isShort,
    passCount,
    totalCount,
    isGradeAPlus,
    onWatchlistAdded,
  ]);

  if (!opportunity || !bracket) {
    return (
      <aside
        style={{
          width: "360px",
          height: "100%",
          padding: "24px",
          background: "rgba(11, 15, 25, 0.98)",
          borderLeft: "1px solid var(--border-subtle)",
          color: "var(--text-muted)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        Select a token to view institutional verification.
      </aside>
    );
  }

  return (
    <aside
      style={{
        width: "360px",
        height: "100%",
        display: "flex",
        flexDirection: "column",
        background: "rgba(11, 15, 25, 0.98)",
        borderLeft: "1px solid var(--border-subtle)",
        overflowY: "auto",
        padding: "16px",
        zIndex: 20,
      }}
    >
      {/* Header with Institutional Grade */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: "14px",
        }}
      >
        <span
          style={{
            fontSize: "12px",
            fontWeight: 700,
            color: "var(--text-secondary)",
            letterSpacing: "0.05em",
            textTransform: "uppercase",
          }}
        >
          Alfa Verification
        </span>
        <span
          className={
            isGradeAPlus
              ? "badge-grade-a-plus"
              : isHighRejection
                ? "badge-grade-b"
                : "badge-grade-a"
          }
        >
          <ShieldCheck size={12} />
          {isHighRejection
            ? "GRADE B (RETEST ONLY)"
            : isGradeAPlus
              ? `GRADE A+ (${passCount}/${totalCount} PASS)`
              : `GRADE A (${passCount}/${totalCount} PASS)`}
        </span>
      </div>

      {/* Dynamic Verification Checklist */}
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: "8px",
          marginBottom: "20px",
        }}
      >
        {checklist.map((item, idx) => (
          <ChecklistItemRow
            key={idx}
            title={item.title}
            passed={item.passed}
            detail={item.detail}
          />
        ))}
      </div>

      {/* Execution Bracket */}
      <div style={{ marginBottom: "20px" }}>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            marginBottom: "8px",
            flexWrap: "wrap",
            gap: "6px",
          }}
        >
          <span
            style={{
              fontSize: "11px",
              fontWeight: 700,
              color: "var(--text-secondary)",
              letterSpacing: "0.05em",
              textTransform: "uppercase",
            }}
          >
            Execution
          </span>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "6px",
              flexWrap: "wrap",
            }}
          >
            {opportunity.resistance_absorption_score !== undefined &&
              opportunity.resistance_absorption_score !== "N/A" && (
                <span
                  style={{
                    fontSize: "10px",
                    fontFamily: "var(--font-mono)",
                    color: "#38bdf8",
                    background: "rgba(56, 189, 248, 0.12)",
                    border: "1px solid rgba(56, 189, 248, 0.25)",
                    padding: "1px 6px",
                    borderRadius: "4px",
                    fontWeight: 700,
                  }}
                  title="Institutional Resistance Absorption Score (Orderflow, OI, Funding, Geometry, VPVR)"
                >
                  Absorption:{" "}
                  {isHighRejection
                    ? Math.min(
                        Number(opportunity.resistance_absorption_score),
                        44.0,
                      ).toFixed(1)
                    : Number(opportunity.resistance_absorption_score).toFixed(
                        1,
                      )}
                  /100
                </span>
              )}

            {opportunity.expected_value_r !== undefined && (
              <span
                style={{
                  fontSize: "10px",
                  fontFamily: "var(--font-mono)",
                  color:
                    Number(opportunity.expected_value_r) >= 0
                      ? "var(--accent-emerald)"
                      : "var(--accent-crimson)",
                  background:
                    Number(opportunity.expected_value_r) >= 0
                      ? "rgba(16, 185, 129, 0.12)"
                      : "rgba(239, 68, 68, 0.12)",
                  border: `1px solid ${Number(opportunity.expected_value_r) >= 0 ? "rgba(16, 185, 129, 0.25)" : "rgba(239, 68, 68, 0.25)"}`,
                  padding: "1px 6px",
                  borderRadius: "4px",
                  fontWeight: 700,
                }}
                title="Mathematical Expectancy (+R per trade)"
              >
                EV:{" "}
                {(() => {
                  const evNum = Number(opportunity.expected_value_r);
                  if (isNaN(evNum)) return `${opportunity.expected_value_r}R`;
                  return evNum >= 0
                    ? `+${evNum.toFixed(2)}R`
                    : `${evNum.toFixed(2)}R`;
                })()}
              </span>
            )}
            <span
              style={{
                fontSize: "10px",
                fontFamily: "var(--font-mono)",
                color: "var(--accent-emerald)",
                background: "rgba(16, 185, 129, 0.1)",
                border: "1px solid rgba(16, 185, 129, 0.25)",
                padding: "1px 6px",
                borderRadius: "4px",
                fontWeight: 700,
              }}
            >
              R:R{" "}
              {Math.min(
                Number(opportunity.risk_reward_ratio || 0),
                8.0,
              ).toFixed(2)}
              :1
            </span>
          </div>
        </div>

        {!isShort && (
          <div
            style={{
              marginBottom: "8px",
              padding: "5px 10px",
              borderRadius: "6px",
              fontSize: "10px",
              fontWeight: 800,
              letterSpacing: "0.04em",
              display: "flex",
              alignItems: "center",
              gap: "6px",
              background:
                opportunity.breakout_readiness_status === "BREAKOUT_CONFIRMED"
                  ? "rgba(16, 185, 129, 0.12)"
                  : opportunity.breakout_readiness_status ===
                      "HIGH_REJECTION_RISK"
                    ? "rgba(239, 68, 68, 0.12)"
                    : "rgba(244, 157, 13, 0.12)",
              color:
                opportunity.breakout_readiness_status === "BREAKOUT_CONFIRMED"
                  ? "var(--accent-emerald)"
                  : opportunity.breakout_readiness_status ===
                      "HIGH_REJECTION_RISK"
                    ? "var(--accent-crimson)"
                    : "#f49d0d",
              border: `1px solid ${
                opportunity.breakout_readiness_status === "BREAKOUT_CONFIRMED"
                  ? "rgba(16, 185, 129, 0.3)"
                  : opportunity.breakout_readiness_status ===
                      "HIGH_REJECTION_RISK"
                    ? "rgba(239, 68, 68, 0.3)"
                    : "rgba(244, 157, 13, 0.3)"
              }`,
            }}
          >
            <ShieldCheck size={13} />
            {opportunity.breakout_readiness_status === "BREAKOUT_CONFIRMED"
              ? "BREAKOUT CONFIRMED (ABSORPTION HIGH)"
              : opportunity.breakout_readiness_status === "HIGH_REJECTION_RISK"
                ? "REJECTION RISK (BLEEDING OI / CVD)"
                : "WAIT FOR POC RETEST (DO NOT CHASE CEILING)"}
          </div>
        )}

        <div
          style={{
            background: "rgba(255, 255, 255, 0.03)",
            borderRadius: "8px",
            border: "1px solid var(--border-subtle)",
            overflow: "hidden",
            fontFamily: "var(--font-mono)",
            fontSize: "12px",
          }}
        >
          {/* Invalidation Stop */}
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              padding: "8px 12px",
              borderBottom: "1px solid var(--border-subtle)",
            }}
          >
            <span style={{ color: "var(--accent-crimson)" }}>
              Invalidation Stop
            </span>
            <strong style={{ color: "var(--accent-crimson)" }}>
              ${bracket.stopPrice}
            </strong>
          </div>

          {/* Optimal Limit Entry */}
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              padding: "8px 12px",
              borderBottom: "1px solid var(--border-subtle)",
              background: "rgba(255, 255, 255, 0.02)",
            }}
          >
            <div style={{ display: "flex", flexDirection: "column" }}>
              <span style={{ color: "var(--text-primary)", fontWeight: 600 }}>
                Limit Retest Entry
              </span>
              <span style={{ fontSize: "9px", color: "var(--text-muted)" }}>
                {isShort
                  ? "POC / EMA50 Resistance Ceiling"
                  : "POC / EMA50 Support Shelf"}
              </span>
            </div>
            <strong
              style={{ color: "var(--text-primary)", alignSelf: "center" }}
            >
              ${bracket.entryPrice}
            </strong>
          </div>

          {/* Breakout / Breakdown Trigger */}
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              padding: "8px 12px",
              borderBottom: "1px solid var(--border-subtle)",
            }}
          >
            <div style={{ display: "flex", flexDirection: "column" }}>
              <span
                style={{
                  color: isShort ? "var(--accent-crimson)" : "#f49d0d",
                  fontWeight: 600,
                }}
              >
                {isShort ? "Breakdown Trigger" : "Breakout Trigger"}
              </span>
              <span style={{ fontSize: "9px", color: "var(--text-muted)" }}>
                {isShort
                  ? "Stop-Sell on Floor Breakdown"
                  : "Stop-Buy on Compression Break"}
              </span>
            </div>
            <strong
              style={{
                color: isShort ? "var(--accent-crimson)" : "#f49d0d",
                alignSelf: "center",
              }}
            >
              ${bracket.triggerPrice}
            </strong>
          </div>

          {/* Targets */}
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              padding: "8px 12px",
              borderBottom: "1px solid var(--border-subtle)",
            }}
          >
            <span style={{ color: "var(--accent-emerald)" }}>
              {isShort ? "Target 1 (Floor / VAL)" : "Target 1 (Ceiling)"}
            </span>
            <strong style={{ color: "var(--accent-emerald)" }}>
              ${bracket.target1}
            </strong>
          </div>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              padding: "8px 12px",
              borderBottom: "1px solid var(--border-subtle)",
            }}
          >
            <span style={{ color: "var(--accent-emerald)" }}>
              Target 2 (0.618 Fib)
            </span>
            <strong style={{ color: "var(--accent-emerald)" }}>
              ${bracket.target2}
            </strong>
          </div>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              padding: "8px 12px",
            }}
          >
            <span style={{ color: "var(--accent-emerald)" }}>
              Target 3 (1.618 Fib)
            </span>
            <strong style={{ color: "var(--accent-emerald)" }}>
              ${bracket.target3}
            </strong>
          </div>
        </div>

        {/* Strategy Context & Anti-Chasing Guidance */}
        <div
          style={{
            marginTop: "8px",
            padding: "8px 10px",
            borderRadius: "6px",
            background: isShort
              ? "rgba(239, 68, 68, 0.05)"
              : "rgba(244, 157, 13, 0.05)",
            border: `1px solid ${isShort ? "rgba(239, 68, 68, 0.2)" : "rgba(244, 157, 13, 0.15)"}`,
            fontSize: "11px",
            color: "var(--text-secondary)",
            lineHeight: "1.4",
          }}
        >
          <div
            style={{
              fontWeight: 700,
              color: isShort ? "var(--accent-crimson)" : "#f49d0d",
              marginBottom: "2px",
              fontSize: "10px",
              textTransform: "uppercase",
              letterSpacing: "0.04em",
            }}
          >
            Institutional Execution Rule ({isShort ? "SHORT" : "LONG"})
          </div>
          <div>
            {isShort ? (
              <>
                Do not market-short into support. Accumulate short limit orders
                on the <strong>POC/EMA50 resistance ceiling</strong>, or place a{" "}
                <strong>Breakdown Trigger</strong> below the compression floor
                to ride downside momentum into Target 1.
              </>
            ) : opportunity.breakout_readiness_status ===
              "BREAKOUT_CONFIRMED" ? (
              <>
                Strong order-flow absorption &amp; short-squeeze fuel at
                resistance. Valid for <strong>Breakout Trigger</strong> above
                VAH/Pivot OR <strong>Limit Retest</strong> entry.
              </>
            ) : opportunity.breakout_readiness_status ===
              "HIGH_REJECTION_RISK" ? (
              <>
                Warning: Negative CVD or unwinding Open Interest into resistance
                ceiling. High fakeout probability on breakout.{" "}
                <strong>Bid deep support only or skip</strong>.
              </>
            ) : (
              <>
                Moderate overhead supply at resistance.{" "}
                <strong>Do NOT buy the ceiling breakout</strong>; place Limit
                Retest orders at the 30d POC / 4H EMA50 shelf and take profit
                into VAH/Target 1.
              </>
            )}
          </div>
        </div>

        {/* Institutional Order Flow & Desk Capacity Card */}
        <div
          style={{
            marginTop: "8px",
            background: "rgba(15, 23, 42, 0.6)",
            border: "1px solid rgba(244, 157, 13, 0.2)",
            borderRadius: "6px",
            padding: "8px 10px",
            fontSize: "11px",
            fontFamily: "var(--font-mono)",
          }}
        >
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: "4px",
            }}
          >
            <span
              style={{
                fontSize: "9px",
                fontWeight: 800,
                color: "#f49d0d",
                textTransform: "uppercase",
                letterSpacing: "0.04em",
              }}
            >
              Order Flow Microstructure
            </span>
            {opportunity.orderflow_absorption && (
              <span
                style={{
                  fontSize: "8.5px",
                  fontWeight: 800,
                  padding: "1px 5px",
                  borderRadius: "3px",
                  background: isShort
                    ? "rgba(239, 68, 68, 0.2)"
                    : "rgba(16, 185, 129, 0.2)",
                  color: isShort
                    ? "var(--accent-crimson)"
                    : "var(--accent-emerald)",
                  border: `1px solid ${isShort ? "rgba(239, 68, 68, 0.4)" : "rgba(16, 185, 129, 0.4)"}`,
                }}
              >
                {isShort ? "DISTRIBUTION ABSORPTION" : "PASSIVE BUY ABSORPTION"}
              </span>
            )}
          </div>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "1fr 1fr",
              gap: "6px",
            }}
          >
            <div>
              <div style={{ fontSize: "9.5px", color: "var(--text-muted)" }}>
                24h Net CVD Flow
              </div>
              <div
                style={{
                  fontWeight: 700,
                  color: "var(--text-primary)",
                  fontSize: "11px",
                }}
              >
                {opportunity.cvd_24h && opportunity.cvd_24h !== "N/A"
                  ? `${Number(opportunity.cvd_24h) > 0 ? "+" : ""}${Number(opportunity.cvd_24h).toLocaleString()} (${opportunity.cvd_z_score || "0.0"}z)`
                  : "Balanced Flow"}
              </div>
            </div>
            <div>
              <div style={{ fontSize: "9.5px", color: "var(--text-muted)" }}>
                Max Allocation (1% ADV)
              </div>
              <div
                style={{ fontWeight: 700, color: "#f49d0d", fontSize: "11px" }}
              >
                {opportunity.max_recommended_size_usd &&
                opportunity.max_recommended_size_usd !== "N/A"
                  ? `$${Number(opportunity.max_recommended_size_usd).toLocaleString()}`
                  : "$50,000"}
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Factor Confirmation Breakdown */}
      {opportunity.score_breakdown && (
        <div style={{ marginBottom: "20px" }}>
          <div
            style={{
              fontSize: "11px",
              fontWeight: 700,
              color: "var(--text-secondary)",
              letterSpacing: "0.05em",
              textTransform: "uppercase",
              marginBottom: "8px",
            }}
          >
            Factor Confirmation Scores
          </div>
          <div
            style={{
              background: "rgba(255, 255, 255, 0.02)",
              borderRadius: "8px",
              border: "1px solid var(--border-subtle)",
              padding: "10px 12px",
              display: "grid",
              gridTemplateColumns: "1fr 1fr",
              gap: "8px 12px",
              fontFamily: "var(--font-mono)",
              fontSize: "11px",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span style={{ color: "var(--text-muted)" }}>Volume Flow:</span>
              <span
                style={{
                  fontWeight: 700,
                  color:
                    Number(opportunity.score_breakdown.volume || 0) >= 60
                      ? "var(--accent-emerald)"
                      : "var(--text-primary)",
                }}
              >
                {opportunity.score_breakdown.volume ?? "N/A"}
              </span>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span style={{ color: "var(--text-muted)" }}>Momentum:</span>
              <span
                style={{
                  fontWeight: 700,
                  color:
                    Number(opportunity.score_breakdown.momentum || 0) >= 60
                      ? "var(--accent-emerald)"
                      : "var(--text-primary)",
                }}
              >
                {opportunity.score_breakdown.momentum ?? "N/A"}
              </span>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span style={{ color: "var(--text-muted)" }}>
                Derivatives &amp; OI:
              </span>
              <span
                style={{
                  fontWeight: 700,
                  color:
                    Number(opportunity.score_breakdown.derivatives || 0) >= 50
                      ? "var(--accent-emerald)"
                      : "var(--accent-crimson)",
                }}
              >
                {opportunity.score_breakdown.derivatives ?? "N/A"}
              </span>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span style={{ color: "var(--text-muted)" }}>Trend Regime:</span>
              <span
                style={{
                  fontWeight: 700,
                  color:
                    Number(opportunity.score_breakdown.trend || 0) >= 70
                      ? "var(--accent-emerald)"
                      : "var(--text-primary)",
                }}
              >
                {opportunity.score_breakdown.trend ?? "N/A"}
              </span>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span style={{ color: "var(--text-muted)" }}>Alpha Outperf:</span>
              <span
                style={{
                  fontWeight: 700,
                  color:
                    Number(opportunity.score_breakdown.alpha || 0) >= 70
                      ? "var(--accent-emerald)"
                      : "var(--text-primary)",
                }}
              >
                {opportunity.score_breakdown.alpha ?? "N/A"}
              </span>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span style={{ color: "var(--text-muted)" }}>Structure:</span>
              <span
                style={{
                  fontWeight: 700,
                  color:
                    Number(opportunity.score_breakdown.structure || 0) >= 80
                      ? "var(--accent-emerald)"
                      : "var(--text-primary)",
                }}
              >
                {opportunity.score_breakdown.structure ?? "N/A"}
              </span>
            </div>
          </div>
        </div>
      )}

      {/* Quantitative Rationale */}
      <div style={{ marginBottom: "20px" }}>
        <div
          style={{
            fontSize: "11px",
            fontWeight: 700,
            color: "var(--text-secondary)",
            letterSpacing: "0.05em",
            textTransform: "uppercase",
            marginBottom: "8px",
          }}
        >
          Quantitative Rationale
        </div>
        <div
          style={{
            background: "rgba(255, 255, 255, 0.02)",
            borderRadius: "8px",
            border: "1px solid var(--border-subtle)",
            padding: "10px 12px",
            fontSize: "11px",
            color: "var(--text-secondary)",
            lineHeight: "1.5",
          }}
        >
          {opportunity.rationale?.map((r, i) => (
            <div key={i} style={{ marginBottom: "4px" }}>
              • {r}
            </div>
          ))}
        </div>
      </div>

      {/* Action Button: Add to Watchlist */}
      <div style={{ marginTop: "auto", paddingTop: "10px" }}>
        <button
          type="button"
          onClick={handleAddToWatchlist}
          disabled={adding || added}
          style={{
            width: "100%",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: "8px",
            padding: "12px",
            borderRadius: "10px",
            background: added ? "var(--accent-emerald)" : "#f49d0d",
            color: "#07090e",
            fontWeight: 800,
            fontSize: "13px",
            border: "none",
            cursor: adding || added ? "default" : "pointer",
            boxShadow: added
              ? "0 4px 16px rgba(16, 185, 129, 0.3)"
              : "0 4px 16px rgba(244, 157, 13, 0.3)",
            transition: "all 0.2s ease",
          }}
        >
          {added ? (
            <>
              <CheckCircle2 size={16} /> Added to Watchlist!
            </>
          ) : (
            <>
              <BookmarkPlus size={16} /> Add to Watchlist
            </>
          )}
        </button>
      </div>
    </aside>
  );
}

export default memo(VerificationPanel);
