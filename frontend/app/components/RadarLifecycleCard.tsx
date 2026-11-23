"use client";

import { useEffect, useState } from "react";
import type { Opportunity } from "./CandidateSidebar";
import { formatCandidatePrice } from "./CandidateSidebar";
import { radarInsights, timestamp, type RadarQuote } from "../lib/radarInsights";

const COLORS = {
  white: "#ffffff",
  softWhite: "#e2e8f0",
  label: "#94a3b8",
  green: "#10b981",
  red: "#f87171",
  yellow: "#facc15",
  amber: "#f49d0d",
};

const pct = (n: number) => `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
function utc(value?: string | null) {
  const t = timestamp(value);
  return t === null ? "Not recorded" : new Date(t).toISOString().replace("T", " ").slice(0, 19) + " UTC";
}

export default function RadarLifecycleCard({ opportunity, quote }: { opportunity: Opportunity; quote?: RadarQuote }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 2000);
    return () => clearInterval(timer);
  }, []);
  const info = radarInsights(opportunity, quote, now);
  const price = (n: number) => `$${formatCandidatePrice(n, opportunity.tick_size)}`;
  const signedColor = (n: number) => (n >= 0 ? COLORS.green : COLORS.red);
  const signal = opportunity.radar_signal;
  const target = Number(opportunity.suggested_targets?.[0]);
  const targetMove = info.fresh && info.price !== null && Number.isFinite(target) && target > 0
    ? (target / info.price - 1) * 100 : null;
  const stopBreached = info.validTrigger && info.fresh && info.price !== null
    && (opportunity.direction.toLowerCase() === "short" ? info.price >= info.stop : info.price <= info.stop);
  const checks = opportunity.checklist || [];
  const warning = checks.some(c => c.status === "unknown")
    ? "Some checks lack fresh data; unknown checks are unverified."
    : checks.some(c => !c.passed) ? `Gate drift: ${checks.filter(c => !c.passed).map(c => c.title).join(", ")}.` : null;

  const statusLabel = opportunity.radar_status_label || "ACTIVE SETUP";
  const statusColor = stopBreached
    ? COLORS.red
    : warning
      ? COLORS.yellow
      : COLORS.green;

  const rowStyle: React.CSSProperties = {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "baseline",
    gap: "8px",
    padding: "3px 0",
  };

  return (
    <section
      aria-label="Radar lifecycle insights"
      style={{
        padding: "12px",
        marginBottom: "12px",
        borderRadius: "8px",
        background: "rgba(15, 23, 42, 0.78)",
        border: "1px solid rgba(16, 185, 129, 0.32)",
        fontSize: "11px",
        lineHeight: 1.55,
        overflowWrap: "anywhere",
        fontFamily: "var(--font-mono)",
        color: COLORS.white,
      }}
    >
      {/* Header */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: 8,
          flexWrap: "wrap",
          paddingBottom: "8px",
          marginBottom: "6px",
          borderBottom: "1px solid rgba(255, 255, 255, 0.08)",
        }}
      >
        <strong style={{ color: COLORS.white, letterSpacing: "0.04em" }}>
          RADAR LIFECYCLE
        </strong>
        <span
          style={{
            color: statusColor,
            background:
              statusColor === COLORS.green
                ? "rgba(16, 185, 129, 0.14)"
                : statusColor === COLORS.red
                  ? "rgba(248, 113, 113, 0.14)"
                  : "rgba(250, 204, 21, 0.14)",
            border: `1px solid ${
              statusColor === COLORS.green
                ? "rgba(16, 185, 129, 0.35)"
                : statusColor === COLORS.red
                  ? "rgba(248, 113, 113, 0.35)"
                  : "rgba(250, 204, 21, 0.35)"
            }`,
            padding: "2px 8px",
            borderRadius: "4px",
            fontWeight: 800,
            fontSize: "10px",
            letterSpacing: "0.04em",
          }}
        >
          {statusLabel}
        </span>
      </div>

      {/* Signal Baseline & Performance */}
      {signal?.version === 1 ? (
        <>
          <div style={rowStyle}>
            <span style={{ color: COLORS.label }}>First radar signal</span>
            <strong style={{ color: COLORS.white }}>{utc(signal.published_at)}</strong>
          </div>
          <div style={rowStyle}>
            <span style={{ color: COLORS.label }}>Signal midpoint</span>
            <strong style={{ color: COLORS.yellow }}>
              {Number(signal.price) > 0 ? price(Number(signal.price)) : "Unavailable at publication"}
            </strong>
          </div>
          {signal.quote_observed_at && (
            <div style={rowStyle}>
              <span style={{ color: COLORS.label }}>Signal quote recorded</span>
              <span style={{ color: COLORS.softWhite }}>{utc(signal.quote_observed_at)}</span>
            </div>
          )}
          {info.signalChange !== null ? (
            <>
              <div style={rowStyle}>
                <span style={{ color: COLORS.label }}>Price since signal</span>
                <strong style={{ color: signedColor(info.signalChange) }}>
                  {pct(info.signalChange)}
                </strong>
              </div>
              <div style={rowStyle}>
                <span style={{ color: COLORS.label }}>
                  {opportunity.direction.toUpperCase()} directional move
                </span>
                <strong style={{ color: signedColor(info.directionalChange!) }}>
                  {pct(info.directionalChange!)}
                </strong>
              </div>
            </>
          ) : (
            <div
              style={{
                color: COLORS.yellow,
                padding: "4px 0",
                fontWeight: 600,
              }}
            >
              Since-signal return unavailable:{" "}
              {!info.fresh ? "fresh quote required" : "publication price not recorded"}.
            </div>
          )}
        </>
      ) : (
        <>
          <div style={rowStyle}>
            <span style={{ color: COLORS.label }}>Setup first evaluated</span>
            <strong style={{ color: COLORS.white }}>
              {utc(opportunity.first_seen_at || opportunity.evaluated_at)}
            </strong>
          </div>
          <div style={{ color: COLORS.yellow, padding: "2px 0" }}>
            Legacy episode: first radar publication and price were not recorded.
          </div>
        </>
      )}

      {/* Observed Trigger Metrics (shown only when a trigger event was observed) */}
      {info.validTrigger && (
        <div
          style={{
            marginTop: "6px",
            paddingTop: "6px",
            borderTop: "1px solid rgba(255, 255, 255, 0.08)",
          }}
        >
          <div style={rowStyle}>
            <span style={{ color: COLORS.label }}>Trigger observed</span>
            <span>
              <strong style={{ color: COLORS.white }}>{utc(opportunity.radar_triggered_at)}</strong>{" "}
              <strong style={{ color: COLORS.yellow }}>({opportunity.radar_trigger_basis})</strong>
            </span>
          </div>
          <div style={rowStyle}>
            <span style={{ color: COLORS.label }}>Locked entry reference</span>
            <strong style={{ color: COLORS.yellow }}>{price(info.entry)}</strong>
          </div>
          {info.triggerChange !== null && (
            <div style={rowStyle}>
              <span style={{ color: COLORS.label }}>Move from locked entry</span>
              <span>
                <strong style={{ color: signedColor(info.triggerChange) }}>
                  {pct(info.triggerChange)}
                </strong>{" "}
                <strong style={{ color: COLORS.white }}>
                  ({info.riskMultiple! >= 0 ? "+" : ""}
                  {info.riskMultiple!.toFixed(2)}R)
                </strong>
              </span>
            </div>
          )}
          <div style={rowStyle}>
            <span style={{ color: COLORS.label }}>Recorded stop</span>
            <strong style={{ color: COLORS.red }}>{price(info.stop)}</strong>
          </div>
        </div>
      )}

      {/* T1 Reference */}
      {targetMove !== null && (
        <div style={rowStyle}>
          <span style={{ color: COLORS.label }}>T1 reference</span>
          <span>
            <strong style={{ color: COLORS.green }}>{price(target)}</strong>{" "}
            <span style={{ color: COLORS.yellow }}>({pct(targetMove)} from quote)</span>
          </span>
        </div>
      )}

      {stopBreached && (
        <div
          style={{
            color: COLORS.red,
            background: "rgba(248, 113, 113, 0.12)",
            border: "1px solid rgba(248, 113, 113, 0.3)",
            borderRadius: "4px",
            padding: "4px 8px",
            marginTop: "6px",
            fontWeight: 700,
          }}
        >
          Current quote is beyond the recorded stop; awaiting server lifecycle recheck.
        </div>
      )}

      {/* Live Quote & Gate Verification */}
      <div
        style={{
          marginTop: "6px",
          paddingTop: "6px",
          borderTop: "1px solid rgba(255, 255, 255, 0.08)",
        }}
      >
        <div style={rowStyle}>
          <span style={{ color: COLORS.label }}>Live quote</span>
          {info.quote ? (
            <span>
              <strong style={{ color: COLORS.white }}>{price(Number(info.quote.price))}</strong>
              <span style={{ color: COLORS.softWhite }}> · </span>
              <strong style={{ color: info.fresh ? COLORS.green : COLORS.red }}>
                {info.fresh ? "LIVE" : "STALE"}
              </strong>
            </span>
          ) : (
            <strong style={{ color: COLORS.red }}>Unavailable</strong>
          )}
        </div>
        <div style={rowStyle}>
          <span style={{ color: COLORS.label }}>Gate verification</span>
          <strong style={{ color: warning ? COLORS.yellow : COLORS.green }}>
            {utc(opportunity.radar_checked_at || opportunity.evaluated_at)}
          </strong>
        </div>
      </div>

      {warning && (
        <div
          style={{
            color: COLORS.yellow,
            background: "rgba(250, 204, 21, 0.1)",
            border: "1px solid rgba(250, 204, 21, 0.28)",
            borderRadius: "4px",
            padding: "4px 8px",
            marginTop: "6px",
            fontWeight: 600,
          }}
        >
          {warning}
        </div>
      )}

      <div
        style={{
          marginTop: "6px",
          paddingTop: "6px",
          borderTop: "1px solid rgba(255, 255, 255, 0.06)",
          fontSize: "10px",
          color: COLORS.label,
          lineHeight: 1.45,
        }}
      >
        Public futures bid/ask midpoints; trigger observations are not fills. Returns exclude fees,
        slippage and leverage. R uses the recorded entry-to-stop distance.
      </div>
    </section>
  );
}
