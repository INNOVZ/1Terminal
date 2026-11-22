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
    <section aria-label="Published plan geometry" style={{ marginTop: "10px", padding: "10px 12px", border: "1px solid var(--border-subtle)", borderRadius: "8px", background: "rgba(255,255,255,0.025)", fontSize: "11px", color: "var(--text-secondary)", lineHeight: 1.5 }}>
      <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", gap: "4px 12px", marginBottom: "8px" }}>
        <strong style={{ color: "var(--text-primary)" }}>Published plan geometry</strong>
        <span>{mode}</span>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: "8px", fontFamily: "var(--font-mono)" }}>
        <div><div style={{ color: "var(--text-muted)" }}>Stop distance</div><strong>{metric(d.risk_pct, "%")}</strong></div>
        <div><div style={{ color: "var(--text-muted)" }}>T1 distance</div><strong>{metric(d.t1_distance_pct, "%")}</strong></div>
        <div><div style={{ color: "var(--text-muted)" }}>T1 gross R:R</div><strong>{metric(d.t1_gross_r, ":1")}</strong></div>
      </div>
      <div style={{ marginTop: "8px" }}>24h / 48h target timing: <strong>Not validated</strong></div>
      <details style={{ marginTop: "6px" }}>
        <summary style={{ cursor: "pointer", color: "var(--text-primary)" }}>Price references and plan notes</summary>
        <dl style={{ margin: "8px 0 0", display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1.5fr)", gap: "6px 12px", overflowWrap: "anywhere" }}>
          <dt>Entry vs scan reference</dt><dd style={{ margin: 0 }}>{metric(d.entry_change_from_scan_price_pct, "%", true)}</dd>
          <dt>Stop anchor</dt><dd style={{ margin: 0 }}>{d.stop_anchor ? anchors[d.stop_anchor] ?? "Not recorded" : "Not recorded"}</dd>
          <dt>Stop / scan ATR{d.atr_timeframe ? ` (${d.atr_timeframe})` : ""}</dt><dd style={{ margin: 0 }}>{metric(d.risk_atr, "×")}</dd>
          <dt>T1 / scan ATR{d.atr_timeframe ? ` (${d.atr_timeframe})` : ""}</dt><dd style={{ margin: 0 }}>{metric(d.t1_distance_atr, "×")}</dd>
          <dt>Nearest recorded level beyond entry</dt><dd style={{ margin: 0 }}>{reference ? `${reference.label}: ${price(reference.price)} · ${metric(reference.gross_r, "R")}${reference.before_t1 ? " (before T1)" : ""}` : "Not recorded"}</dd>
          <dt>Initial objective</dt><dd style={{ margin: 0 }}>{d.t1_extended_after_risk_sizing === true ? `${price(d.initial_t1)} → ${price(d.t1)} during risk sizing` : d.t1_extended_after_risk_sizing === false ? `${price(d.initial_t1)}; unchanged by risk sizing` : "Target-sizing trace not recorded"}</dd>
          <dt>Scan evaluated</dt><dd style={{ margin: 0 }}>{evaluated}</dd>
        </dl>
        <p style={{ margin: "8px 0 0", color: "var(--text-muted)" }}>Distances describe the saved plan, not a live quote or an executed fill. Recorded levels do not establish a probability or time to target.</p>
      </details>
    </section>
  );
}
