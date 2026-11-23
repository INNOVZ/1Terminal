"use client";

export interface PlanDiagnostics {
  version: string;
  status: "available" | "unavailable";
  entry_mode?: "conditional_trigger" | "retest" | "unclassified";
  entry?: string;
  stop?: string;
  t1?: string;
  risk_pct?: string | null;
  t1_distance_pct?: string | null;
  t1_gross_r?: string | null;
  entry_change_from_scan_price_pct?: string | null;
  risk_atr?: string | null;
  t1_distance_atr?: string | null;
  atr_timeframe?: string | null;
  stop_anchor?: string | null;
  initial_t1?: string | null;
  t1_extended_after_risk_sizing?: boolean | null;
  snapshot_evaluated_at?: string | null;
  nearest_recorded_reference?: {
    label: string;
    price: string;
    distance_pct: string;
    gross_r: string;
    before_t1: boolean;
  } | null;
}

const anchors: Record<string, string> = {
  swing_low_7d: "Below the 7-day swing low",
  value_area_low_30d: "Below the 30-day value-area low",
  breakout_distance_bound: "Breakout distance bound",
  breakout_handle: "Below the consolidation handle",
  minimum_distance: "Minimum stop distance",
};

function numeric(value?: string | null): number | null {
  if (value == null || value.trim() === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function metric(value?: string | null, suffix = "", signed = false): string {
  const number = numeric(value);
  if (number == null) return "Not recorded";
  return `${signed && number >= 0 ? "+" : ""}${number.toFixed(2)}${suffix}`;
}

export default function PlanDiagnosticsPanel({ diagnostics: d, precision }: {
  diagnostics?: PlanDiagnostics;
  precision: number;
}) {
  if (!d || d.version !== "plan-geometry-v1" || d.status !== "available") return null;
  const price = (value?: string | null) => {
    const number = numeric(value);
    return number == null ? "Not recorded" : `$${number.toFixed(Math.min(20, Math.max(2, precision)))}`;
  };
  const reference = d.nearest_recorded_reference;
  const mode = d.entry_mode === "retest" ? "Retest entry" : d.entry_mode === "conditional_trigger" ? "Conditional trigger" : "Entry mode not recorded";
  const date = d.snapshot_evaluated_at ? new Date(d.snapshot_evaluated_at) : null;
  const evaluated = date && Number.isFinite(date.getTime()) ? `${date.toISOString().slice(0, 16).replace("T", " ")} UTC` : "Time not recorded";

  return (
    <section
      aria-label="Published plan geometry"
      style={{
        marginTop: "10px",
        padding: "10px 12px",
        border: "1px solid rgba(250, 204, 21, 0.25)",
        borderRadius: "8px",
        background: "rgba(15, 23, 42, 0.72)",
        fontSize: "11px",
        color: "#e2e8f0",
        lineHeight: 1.5,
      }}
    >
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          justifyContent: "space-between",
          alignItems: "center",
          gap: "4px 12px",
          marginBottom: "8px",
        }}
      >
        <strong style={{ color: "#ffffff" }}>Published plan geometry</strong>
        <span
          style={{
            color: "#facc15",
            background: "rgba(250, 204, 21, 0.12)",
            border: "1px solid rgba(250, 204, 21, 0.3)",
            padding: "1px 6px",
            borderRadius: "4px",
            fontWeight: 700,
            fontSize: "10px",
          }}
        >
          {mode}
        </span>
      </div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
          gap: "8px",
          fontFamily: "var(--font-mono)",
        }}
      >
        <div>
          <div style={{ color: "#94a3b8" }}>Stop distance</div>
          <strong style={{ color: "#f87171" }}>{metric(d.risk_pct, "%")}</strong>
        </div>
        <div>
          <div style={{ color: "#94a3b8" }}>T1 distance</div>
          <strong style={{ color: "#10b981" }}>{metric(d.t1_distance_pct, "%")}</strong>
        </div>
        <div>
          <div style={{ color: "#94a3b8" }}>T1 gross R:R</div>
          <strong style={{ color: "#facc15" }}>{metric(d.t1_gross_r, ":1")}</strong>
        </div>
      </div>
      <div style={{ marginTop: "8px", color: "#e2e8f0" }}>
        24h / 48h target timing: <strong style={{ color: "#facc15" }}>Not validated</strong>
      </div>
      <details style={{ marginTop: "6px" }}>
        <summary style={{ cursor: "pointer", color: "#ffffff", fontWeight: 600 }}>
          Price references and plan notes
        </summary>
        <dl
          style={{
            margin: "8px 0 0",
            display: "grid",
            gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1.5fr)",
            gap: "6px 12px",
            overflowWrap: "anywhere",
          }}
        >
          <dt style={{ color: "#94a3b8" }}>Entry vs scan reference</dt>
          <dd style={{ margin: 0, color: "#ffffff" }}>
            {metric(d.entry_change_from_scan_price_pct, "%", true)}
          </dd>
          <dt style={{ color: "#94a3b8" }}>Stop anchor</dt>
          <dd style={{ margin: 0, color: "#f87171" }}>
            {d.stop_anchor ? anchors[d.stop_anchor] ?? "Not recorded" : "Not recorded"}
          </dd>
          <dt style={{ color: "#94a3b8" }}>
            Stop / scan ATR{d.atr_timeframe ? ` (${d.atr_timeframe})` : ""}
          </dt>
          <dd style={{ margin: 0, color: "#f87171" }}>{metric(d.risk_atr, "×")}</dd>
          <dt style={{ color: "#94a3b8" }}>
            T1 / scan ATR{d.atr_timeframe ? ` (${d.atr_timeframe})` : ""}
          </dt>
          <dd style={{ margin: 0, color: "#10b981" }}>{metric(d.t1_distance_atr, "×")}</dd>
          <dt style={{ color: "#94a3b8" }}>Nearest recorded level beyond entry</dt>
          <dd style={{ margin: 0, color: "#facc15" }}>
            {reference
              ? `${reference.label}: ${price(reference.price)} · ${metric(reference.gross_r, "R")}${reference.before_t1 ? " (before T1)" : ""}`
              : "Not recorded"}
          </dd>
          <dt style={{ color: "#94a3b8" }}>Initial objective</dt>
          <dd style={{ margin: 0, color: "#10b981" }}>
            {d.t1_extended_after_risk_sizing === true
              ? `${price(d.initial_t1)} → ${price(d.t1)} during risk sizing`
              : d.t1_extended_after_risk_sizing === false
                ? `${price(d.initial_t1)}; unchanged by risk sizing`
                : "Target-sizing trace not recorded"}
          </dd>
          <dt style={{ color: "#94a3b8" }}>Scan evaluated</dt>
          <dd style={{ margin: 0, color: "#ffffff" }}>{evaluated}</dd>
        </dl>
        <p style={{ margin: "8px 0 0", color: "#94a3b8" }}>
          Distances describe the saved plan, not a live quote or an executed fill. Recorded levels
          do not establish a probability or time to target.
        </p>
      </details>
    </section>
  );
}
