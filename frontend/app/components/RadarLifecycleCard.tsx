"use client";

import { useEffect, useState } from "react";
import type { Opportunity } from "./CandidateSidebar";
import { formatCandidatePrice } from "./CandidateSidebar";
import { radarInsights, timestamp, type RadarQuote } from "../lib/radarInsights";

const pct = (n: number) => `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
function utc(value?: string | null) {
  const t = timestamp(value);
  return t === null ? "Not recorded" : new Date(t).toISOString().replace("T", " ").slice(0, 19) + " UTC";
}
function age(value: string | undefined, now: number) {
  const t = timestamp(value);
  if (t === null || t > now) return "";
  const minutes = Math.floor((now - t) / 60000);
  return minutes < 60 ? `${minutes}m ago` : `${Math.floor(minutes / 60)}h ${minutes % 60}m ago`;
}
export default function RadarLifecycleCard({ opportunity, quote }: { opportunity: Opportunity; quote?: RadarQuote }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 15000);
    return () => clearInterval(timer);
  }, []);
  const info = radarInsights(opportunity, quote, now);
  const price = (n: number) => `$${formatCandidatePrice(n, opportunity.tick_size)}`;
  const color = (n: number) => n >= 0 ? "var(--accent-emerald)" : "#f87171";
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
  return <section aria-label="Radar lifecycle insights" style={{ padding: "10px", marginBottom: "12px", borderRadius: "8px", background: "rgba(16,185,129,.06)", border: "1px solid rgba(16,185,129,.28)", fontSize: "11px", lineHeight: 1.65, overflowWrap: "anywhere", fontFamily: "var(--font-mono)" }}>
    <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}><strong>RADAR LIFECYCLE</strong><strong style={{ color: "var(--accent-emerald)" }}>{opportunity.radar_status_label || "ACTIVE SETUP"}</strong></div>
    {signal?.version === 1 ? <>
      <div>First radar signal: <strong>{utc(signal.published_at)}</strong> <span style={{ color: "var(--text-muted)" }}>{age(signal.published_at, now)}</span></div>
      <div>Signal midpoint: {Number(signal.price) > 0 ? price(Number(signal.price)) : "Unavailable at publication"}</div>
      {signal.quote_observed_at && <div style={{ color: "var(--text-muted)" }}>Signal quote recorded: {utc(signal.quote_observed_at)}</div>}
      {info.signalChange !== null ? <>
        <div>Price since signal: <strong style={{ color: color(info.directionalChange!) }}>{pct(info.signalChange)}</strong></div>
        <div>{opportunity.direction.toUpperCase()} directional move: <strong style={{ color: color(info.directionalChange!) }}>{pct(info.directionalChange!)}</strong></div>
      </> : <div style={{ color: "var(--text-muted)" }}>Since-signal return unavailable: {!info.fresh ? "fresh quote required" : "publication price not recorded"}.</div>}
    </> : <>
      <div>Setup first evaluated: {utc(opportunity.first_seen_at || opportunity.evaluated_at)}</div>
      <div style={{ color: "var(--text-muted)" }}>Legacy episode: first radar publication and price were not recorded.</div>
    </>}
    {info.validTrigger ? <>
      <div>Trigger observed: {utc(opportunity.radar_triggered_at)} ({opportunity.radar_trigger_basis})</div>
      <div>Locked entry reference: {price(info.entry)}</div>
      {info.triggerChange !== null && <div>Gross move from entry reference: <strong style={{ color: color(info.triggerChange) }}>{pct(info.triggerChange)}</strong> ({info.riskMultiple!.toFixed(2)}R)</div>}
      <div>Recorded stop: {price(info.stop)}</div>
    </> : <div>Entry: {opportunity.radar_triggered_at ? "trigger recorded; locked entry reference unavailable" : "awaiting a verified trigger observation"}.</div>}
    {targetMove !== null && <div>T1 reference: {price(target)} ({pct(targetMove)} from current quote)</div>}
    {stopBreached && <div style={{ color: "#f49d0d" }}>Current quote is beyond the recorded stop; awaiting server lifecycle recheck.</div>}
    <div style={{ color: "var(--text-muted)" }}>Quote: {info.quote ? `${price(Number(info.quote.price))} · ${utc(info.quote.observed_at)}${info.fresh ? "" : " · STALE"}` : "Unavailable"}</div>
    <div style={{ color: "var(--text-muted)" }}>Gate verification: {utc(opportunity.radar_checked_at || opportunity.evaluated_at)}</div>
    {warning && <div style={{ color: "#f49d0d" }}>{warning}</div>}
    <div style={{ fontSize: "10px", color: "var(--text-muted)" }}>Public futures bid/ask midpoints; trigger observations are not fills. Returns exclude fees, slippage and leverage. R uses the recorded entry-to-stop distance.</div>
  </section>;
}
