"use client";

import { useEffect, useState, useCallback, useMemo } from "react";
import Link from "next/link";
import {
  RefreshCw,
  Clock,
  ExternalLink,
  CheckCircle2,
  XCircle,
  Timer,
  Radio,
  BarChart3,
  Pin,
} from "lucide-react";
import { formatCandidatePrice } from "../../components/CandidateSidebar";
import { bookEventQuote, timestamp, type RadarQuote } from "../../lib/radarInsights";

export interface AutoWatchEpisode {
  episode_id: string;
  symbol: string;
  direction: "long" | "short" | string;
  setup_label: string;
  execution_mode: string;
  score: number;
  tick_size: string;
  published_at: string;
  published_date: string;
  expires_at: string;
  remaining_seconds: number;
  elapsed_seconds: number;
  signal_price: number;
  entry_price: number;
  trigger_price: number | null;
  stop_price: number;
  target_1: number;
  target_2: number | null;
  target_3: number | null;
  poc_price: number | null;
  vah_price: number | null;
  val_price: number | null;
  status: "ON_RADAR" | "WATCHLIST" | "ACTIVE" | "TARGET_REACHED" | "STOP_BREACHED" | "EXPIRED_48H" | "HARD_INVALIDATED";
  radar_presence: "ON_RADAR" | "EVICTED_FROM_RADAR";
  radar_status_label: string;
  eviction_reason: string | null;
  soft_gate_summary?: string | null;
  gates_passed?: number;
  gates_total?: number;
  last_price: number;
  last_observed_price?: number | null;
  last_checked_at: string | null;
  current_return_pct: number | null;
  peak_mfe_pct: number | null;
  peak_mfe_at: string | null;
  worst_mae_pct: number | null;
  has_observations?: boolean;
  coverage_complete: boolean;
  coverage_gaps?: number;
  barrier_order_certified: boolean;
  endpoint_observed_at?: string | null;
  measurement_status: string;
  worst_mae_at: string | null;
  retest_touched: boolean;
  breakout_touched: boolean;
  dist_to_t1_pct: number | null;
  dist_to_stop_pct: number | null;
  resolved_at: string | null;
  resolved_price: number | null;
  resolved_return_pct: number | null;
  resolution_reason: string | null;
}

interface CohortStats {
  total: number;
  active: number;
  resolved: number;
  measured_resolved?: number;
  unknown_outcome_count?: number;
  observed_extrema_count?: number;
  complete_coverage_count?: number;
  uncertified_barrier_count?: number;
  legacy_unverified_count?: number;
  target_reached: number;
  stop_breached: number;
  expired_48h: number;
  target_hit_rate_pct: number | null;
  stop_rate_pct: number | null;
  decisive_t1_vs_stop_pct: number | null;
  resolved_positive_rate_pct: number | null;
  all_positive_rate_pct: number | null;
  avg_return_pct: number | null;
  median_return_pct: number | null;
  avg_resolved_return_pct: number | null;
  avg_peak_mfe_pct: number | null;
  median_peak_mfe_pct: number | null;
  avg_worst_mae_pct: number | null;
  retest_touch_rate_pct: number | null;
  breakout_touch_rate_pct: number | null;
}

interface AutoWatchPayload {
  window_hours: number;
  lookback_days: number;
  generated_at: string;
  active: AutoWatchEpisode[];
  resolved: AutoWatchEpisode[];
  summary: CohortStats;
  on_radar_cohort: CohortStats;
  evicted_from_radar_cohort: CohortStats;
  daily_breakdown: (CohortStats & { date: string })[];
  archetype_breakdown: (CohortStats & { setup_label: string })[];
  semantics: string;
  refresh_warning?: string;
}

interface AutoWatchlistSectionProps {
  canPinToPersonal: boolean;
  onPinToPersonal?: (ep: AutoWatchEpisode) => Promise<boolean>;
}

const C = {
  white: "#ffffff",
  softWhite: "#e2e8f0",
  muted: "#94a3b8",
  green: "#10b981",
  red: "#f87171",
  yellow: "#facc15",
  amber: "#f49d0d",
};

function fmtPct(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

function fmtRate(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  return `${v.toFixed(1)}%`;
}

function fmtUtc(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (!Number.isFinite(d.getTime())) return "—";
  return d.toISOString().replace("T", " ").slice(5, 16) + " UTC";
}

function fmtDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "0m";
  const hrs = Math.floor(seconds / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  if (hrs >= 24) {
    const days = Math.floor(hrs / 24);
    return `${days}d ${hrs % 24}h`;
  }
  return hrs > 0 ? `${hrs}h ${mins}m` : `${mins}m`;
}

export default function AutoWatchlistSection({
  canPinToPersonal,
  onPinToPersonal,
}: AutoWatchlistSectionProps) {
  const [data, setData] = useState<AutoWatchPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [subTab, setSubTab] = useState<"active" | "resolved" | "analytics">("active");
  const [days, setDays] = useState<number>(14);
  const [resolvedFilter, setResolvedFilter] = useState<
    "ALL" | "TARGET_REACHED" | "STOP_BREACHED" | "EXPIRED_48H" | "HARD_INVALIDATED"
  >("ALL");
  const [liveQuotes, setLiveQuotes] = useState<Record<string, RadarQuote>>({});
  const [nowMs, setNowMs] = useState<number>(() => Date.now());
  const [pinningId, setPinningId] = useState<string | null>(null);
  const [pinnedIds, setPinnedIds] = useState<Record<string, boolean>>({});
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const fetchAutoWatch = useCallback(
    async () => {
      try {
        setErrorMsg(null);
        const res = await fetch(`/api/watchlist/auto?days=${days}`, { cache: "no-store" });
        if (!res.ok) {
          throw new Error(`HTTP ${res.status}`);
        }
        const payload = (await res.json()) as AutoWatchPayload;
        setData(payload);
      } catch (err) {
        setErrorMsg(err instanceof Error ? err.message : "Failed to load 48h auto-watchlist");
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [days],
  );

  useEffect(() => {
    const initTimer = setTimeout(() => {
      void fetchAutoWatch();
    }, 0);
    const interval = setInterval(() => {
      void fetchAutoWatch();
    }, 30000);
    const freshnessTimer = setInterval(() => {
      setNowMs(Date.now());
    }, 10000);
    return () => {
      clearTimeout(initTimer);
      clearInterval(interval);
      clearInterval(freshnessTimer);
    };
  }, [fetchAutoWatch]);

  // Live Binance USD-M WebSocket quotes with verified exchange timestamp, monotonicity, and 60s expiry (W5)
  const activeSymbolsKey = useMemo(() => {
    if (!data?.active?.length) return "";
    return Array.from(new Set(data.active.map((e) => e.symbol.toUpperCase())))
      .sort()
      .join(",");
  }, [data]);

  useEffect(() => {
    if (!activeSymbolsKey) {
      return;
    }
    const symbols = activeSymbolsKey.split(",");
    const activeSet = new Set(symbols);
    let alive = true;
    const streams = symbols.map((s) => `${s.toLowerCase()}@bookTicker`).join("/");
    let ws: WebSocket | null = null;
    try {
      ws = new WebSocket(`wss://fstream.binance.com/stream?streams=${streams}`);
      ws.onmessage = (event) => {
        if (!alive) return;
        try {
          const payload = JSON.parse(event.data);
          const d = payload.data || payload;
          if (typeof d?.s !== "string") return;
          const sym = d.s.toUpperCase();
          if (!activeSet.has(sym)) return;
          const now = Date.now();
          const quote = bookEventQuote(d, now);
          if (!quote) return;
          const nextTs = timestamp(quote.observed_at);
          if (nextTs === null) return;
          setLiveQuotes((prev) => {
            const existing = prev[sym];
            const prevTs = timestamp(existing?.observed_at);
            if (prevTs !== null && nextTs < prevTs) return prev;
            if (existing && existing.price === quote.price && existing.observed_at === quote.observed_at) {
              return prev;
            }
            return { ...prev, [sym]: quote };
          });
        } catch {}
      };
    } catch {}
    return () => {
      alive = false;
      if (ws) {
        ws.onmessage = null;
        ws.close();
      }
    };
  }, [activeSymbolsKey]);

  const filteredResolved = useMemo(() => {
    const list = data?.resolved ?? [];
    if (resolvedFilter === "ALL") return list;
    return list.filter((e) => e.status === resolvedFilter);
  }, [data, resolvedFilter]);

  const handlePin = async (ep: AutoWatchEpisode) => {
    if (!onPinToPersonal || pinningId) return;
    setPinningId(ep.episode_id);
    try {
      if (await onPinToPersonal(ep)) {
        setPinnedIds((prev) => ({ ...prev, [ep.episode_id]: true }));
      }
    } catch {
      setErrorMsg("Unable to pin this episode to the current account.");
    } finally {
      setPinningId(null);
    }
  };

  if (loading && !data) {
    return (
      <div
        className="glass-panel"
        style={{
          padding: "48px 24px",
          textAlign: "center",
          fontFamily: "var(--font-mono)",
          fontSize: "13px",
          color: C.softWhite,
        }}
      >
        <RefreshCw
          size={18}
          style={{ animation: "spin 1s linear infinite", color: C.amber, marginBottom: "10px" }}
        />
        <div>Loading 48h Radar Auto-Watch &amp; Post-Performance Ledger...</div>
      </div>
    );
  }

  const summary = data?.summary;
  const activeList = data?.active ?? [];
  const resolvedList = data?.resolved ?? [];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "20px" }}>
      {/* Top Toolbar */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: "12px",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
          <span
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "6px",
              padding: "4px 10px",
              borderRadius: "20px",
              background: "rgba(250, 204, 21, 0.12)",
              border: "1px solid rgba(250, 204, 21, 0.35)",
              color: C.yellow,
              fontSize: "11px",
              fontWeight: 700,
              fontFamily: "var(--font-mono)",
            }}
          >
            <Radio size={12} /> 48H POTENTIAL WATCHLIST (DISJOINT FROM 5/5 RADAR)
          </span>
          <span style={{ fontSize: "12px", color: C.muted }}>
            Promotes soft-evicted setups (4/4 structure intact) without duplicating live Radar; removes immediately on{" "}
            <strong style={{ color: C.green }}>TARGET REACHED</strong>,{" "}
            <strong style={{ color: C.red }}>STOP BREACHED</strong>, or{" "}
            <strong style={{ color: C.yellow }}>48H EXPIRY</strong>.
          </span>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <select
            aria-label="Lookback window"
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            style={{
              background: "rgba(15, 23, 42, 0.9)",
              color: C.white,
              border: "1px solid var(--border-medium)",
              borderRadius: "8px",
              padding: "7px 10px",
              fontSize: "12px",
              fontFamily: "var(--font-mono)",
              fontWeight: 700,
            }}
          >
            <option value={3}>Last 3 Days</option>
            <option value={7}>Last 7 Days</option>
            <option value={14}>Last 14 Days</option>
            <option value={30}>Last 30 Days</option>
          </select>

          <button
            type="button"
            onClick={() => {
              setRefreshing(true);
              void fetchAutoWatch();
            }}
            disabled={refreshing}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "7px",
              background: "rgba(255, 255, 255, 0.06)",
              color: C.white,
              border: "1px solid var(--border-medium)",
              borderRadius: "8px",
              padding: "7px 14px",
              fontSize: "12px",
              fontWeight: 700,
              cursor: refreshing ? "not-allowed" : "pointer",
            }}
          >
            <RefreshCw
              size={14}
              style={{
                color: C.amber,
                animation: refreshing ? "spin 1s linear infinite" : "none",
              }}
            />
            {refreshing ? "Evaluating..." : "Refresh Live Outcomes"}
          </button>
        </div>
      </div>

      {errorMsg && (
        <div
          style={{
            padding: "10px 14px",
            borderRadius: "8px",
            background: "rgba(248, 113, 113, 0.12)",
            border: "1px solid rgba(248, 113, 113, 0.35)",
            color: C.red,
            fontSize: "12px",
            fontFamily: "var(--font-mono)",
          }}
        >
          Unable to refresh auto-watchlist: {errorMsg}
        </div>
      )}

      {/* Performance Summary KPI Cards */}
      {summary && (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(195px, 1fr))",
            gap: "12px",
            fontFamily: "var(--font-mono)",
          }}
        >
          <div className="glass-panel" style={{ padding: "14px 16px" }}>
            <div style={{ fontSize: "10.5px", color: C.muted, textTransform: "uppercase", fontWeight: 700 }}>
              Active 48h Watch
            </div>
            <div style={{ fontSize: "24px", fontWeight: 800, color: C.white, marginTop: "4px" }}>
              {summary.active}{" "}
              <span style={{ fontSize: "12px", color: C.yellow }}>
                / {summary.total} tracked
              </span>
            </div>
            <div style={{ fontSize: "11px", color: C.softWhite, marginTop: "4px" }}>
              <span style={{ color: C.green }}>{data?.on_radar_cohort.active ?? 0} on 5/5</span>
              {" · "}
              <span style={{ color: C.yellow }}>{data?.evicted_from_radar_cohort.active ?? 0} evicted</span>
            </div>
          </div>

          <div className="glass-panel" style={{ padding: "14px 16px" }}>
            <div style={{ fontSize: "10.5px", color: C.muted, textTransform: "uppercase", fontWeight: 700 }}>
              Certified Target 1 First
            </div>
            <div style={{ fontSize: "24px", fontWeight: 800, color: C.green, marginTop: "4px" }}>
              {summary.target_reached}{" "}
              <span style={{ fontSize: "13px", color: C.softWhite }}>
                ({fmtRate(summary.target_hit_rate_pct)})
              </span>
            </div>
            <div style={{ fontSize: "11px", color: C.muted, marginTop: "4px" }}>
              Decisive T1 vs Stop:{" "}
              <strong style={{ color: C.green }}>{fmtRate(summary.decisive_t1_vs_stop_pct)}</strong>
            </div>
          </div>

          <div className="glass-panel" style={{ padding: "14px 16px" }}>
            <div style={{ fontSize: "10.5px", color: C.muted, textTransform: "uppercase", fontWeight: 700 }}>
              Certified Stop First
            </div>
            <div style={{ fontSize: "24px", fontWeight: 800, color: C.red, marginTop: "4px" }}>
              {summary.stop_breached}{" "}
              <span style={{ fontSize: "13px", color: C.softWhite }}>
                ({fmtRate(summary.stop_rate_pct)})
              </span>
            </div>
            <div style={{ fontSize: "11px", color: C.muted, marginTop: "4px" }}>
              Expired 48h:{" "}
              <strong style={{ color: C.yellow }}>{summary.expired_48h}</strong>
              {(summary.unknown_outcome_count ?? 0) > 0 && (
                <>
                  {" · "}Unknown:{" "}
                  <strong style={{ color: C.softWhite }}>{summary.unknown_outcome_count}</strong>
                </>
              )}
              {(summary.uncertified_barrier_count ?? 0) > 0 && (
                <>
                  {" · "}Uncertified:{" "}
                  <strong style={{ color: C.softWhite }}>{summary.uncertified_barrier_count}</strong>
                </>
              )}
            </div>
          </div>

          <div className="glass-panel" style={{ padding: "14px 16px" }}>
            <div style={{ fontSize: "10.5px", color: C.muted, textTransform: "uppercase", fontWeight: 700 }}>
              Avg / Median Return
            </div>
            <div
              style={{
                fontSize: "22px",
                fontWeight: 800,
                color:
                  summary.avg_return_pct === null
                    ? C.muted
                    : summary.avg_return_pct >= 0
                      ? C.green
                      : C.red,
                marginTop: "4px",
              }}
            >
              {fmtPct(summary.avg_return_pct)}
            </div>
            <div style={{ fontSize: "11px", color: C.softWhite, marginTop: "4px" }}>
              Median:{" "}
              <strong
                style={{
                  color:
                    summary.median_return_pct === null
                      ? C.muted
                      : summary.median_return_pct >= 0
                        ? C.green
                        : C.red,
                }}
              >
                {fmtPct(summary.median_return_pct)}
              </strong>
              {" · "}Pos: <strong style={{ color: C.yellow }}>{fmtRate(summary.all_positive_rate_pct)}</strong>
              {summary.measured_resolved !== undefined && summary.resolved > 0 && (
                <>
                  {" · "}
                  <span style={{ color: C.muted }}>
                    {summary.measured_resolved}/{summary.resolved} measured
                  </span>
                </>
              )}
            </div>
          </div>

          <div className="glass-panel" style={{ padding: "14px 16px" }}>
            <div style={{ fontSize: "10.5px", color: C.muted, textTransform: "uppercase", fontWeight: 700 }}>
              Avg Peak MFE / MAE
            </div>
            <div style={{ fontSize: "20px", fontWeight: 800, marginTop: "6px" }}>
              <span style={{ color: C.green }}>{fmtPct(summary.avg_peak_mfe_pct)}</span>
              <span style={{ color: C.muted }}> / </span>
              <span style={{ color: C.red }}>{fmtPct(summary.avg_worst_mae_pct)}</span>
            </div>
            <div style={{ fontSize: "11px", color: C.muted, marginTop: "4px" }}>
              Retest: <strong style={{ color: C.white }}>{fmtRate(summary.retest_touch_rate_pct)}</strong>
              {" · "}Break:{" "}
              <strong style={{ color: C.yellow }}>{fmtRate(summary.breakout_touch_rate_pct)}</strong>
            </div>
          </div>

          <div className="glass-panel" style={{ padding: "14px 16px" }}>
            <div style={{ fontSize: "10.5px", color: C.muted, textTransform: "uppercase", fontWeight: 700 }}>
              On-Radar vs Evicted
            </div>
            <div style={{ fontSize: "12px", color: C.softWhite, marginTop: "6px", lineHeight: 1.6 }}>
              <div>
                On 5/5 ({data?.on_radar_cohort.total ?? 0}):{" "}
                <strong style={{ color: C.green }}>{fmtPct(data?.on_radar_cohort.avg_peak_mfe_pct)}</strong> MFE
              </div>
              <div>
                Evicted ({data?.evicted_from_radar_cohort.total ?? 0}):{" "}
                <strong style={{ color: C.yellow }}>
                  {fmtPct(data?.evicted_from_radar_cohort.avg_peak_mfe_pct)}
                </strong>{" "}
                MFE
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Sub-navigation tabs */}
      <div style={{ display: "flex", gap: "8px", flexWrap: "wrap", alignItems: "center" }}>
        {[
          { key: "active", label: `Active 48h Watch (${activeList.length})` },
          { key: "resolved", label: `Resolved Outcomes (${resolvedList.length})` },
          { key: "analytics", label: "Daily & Archetype Performance" },
        ].map((t) => {
          const active = subTab === t.key;
          return (
            <button
              key={t.key}
              type="button"
              onClick={() => setSubTab(t.key as "active" | "resolved" | "analytics")}
              style={{
                padding: "8px 14px",
                borderRadius: "8px",
                fontSize: "12px",
                fontWeight: 700,
                cursor: "pointer",
                background: active ? "rgba(244, 157, 13, 0.16)" : "rgba(255, 255, 255, 0.04)",
                color: active ? C.yellow : C.softWhite,
                border: `1px solid ${active ? "rgba(250, 204, 21, 0.45)" : "var(--border-subtle)"}`,
              }}
            >
              {t.label}
            </button>
          );
        })}
      </div>

      {/* VIEW 1: ACTIVE 48H WATCH */}
      {subTab === "active" && (
        activeList.length === 0 ? (
          <div
            className="glass-panel"
            style={{
              padding: "40px 24px",
              textAlign: "center",
              color: C.muted,
              fontFamily: "var(--font-mono)",
              fontSize: "13px",
            }}
          >
            No currently active 48h auto-watch tokens. Fresh 5/5 Radar signals enroll automatically on every scan.
          </div>
        ) : (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(360px, 1fr))",
              gap: "16px",
            }}
          >
            {activeList.map((ep) => {
              const isShort = ep.direction.toLowerCase() === "short";
              const rawQuote = liveQuotes[ep.symbol.toUpperCase()];
              const quoteTs = timestamp(rawQuote?.observed_at);
              const quoteFresh =
                quoteTs !== null && nowMs - quoteTs >= -5000 && nowMs - quoteTs <= 60000;
              const serverObservedPx =
                ep.last_observed_price !== undefined
                  ? ep.last_observed_price
                  : ep.has_observations !== false
                    ? ep.last_price
                    : null;
              const livePx = quoteFresh && rawQuote ? Number(rawQuote.price) : serverObservedPx;
              const liveRet =
                quoteFresh && livePx !== null && ep.signal_price > 0
                  ? (isShort ? -1 : 1) * ((livePx - ep.signal_price) / ep.signal_price) * 100
                  : ep.current_return_pct;
              const effectiveMfe = quoteFresh && liveRet !== null ? Math.max(ep.peak_mfe_pct ?? 0, liveRet) : ep.peak_mfe_pct;
              const effectiveMae = quoteFresh && liveRet !== null ? Math.min(ep.worst_mae_pct ?? 0, liveRet) : ep.worst_mae_pct;
              const px = (n: number | null) =>
                n !== null && Number.isFinite(n) && n > 0
                  ? `$${formatCandidatePrice(n, ep.tick_size)}`
                  : "—";
              const onRadar = ep.radar_presence === "ON_RADAR";

              return (
                <div
                  key={ep.episode_id}
                  className="glass-panel-interactive"
                  style={{
                    padding: "16px",
                    display: "flex",
                    flexDirection: "column",
                    gap: "10px",
                    fontFamily: "var(--font-mono)",
                    border: onRadar
                      ? "1px solid rgba(16, 185, 129, 0.35)"
                      : "1px solid rgba(250, 204, 21, 0.3)",
                  }}
                >
                  {/* Header */}
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "8px" }}>
                    <div>
                      <div style={{ display: "flex", alignItems: "center", gap: "7px", flexWrap: "wrap" }}>
                        <strong style={{ fontSize: "16px", color: C.white }}>{ep.symbol}</strong>
                        <span
                          style={{
                            fontSize: "10px",
                            fontWeight: 800,
                            padding: "1px 6px",
                            borderRadius: "4px",
                            color: isShort ? C.red : C.green,
                            background: isShort ? "rgba(248, 113, 113, 0.14)" : "rgba(16, 185, 129, 0.14)",
                            border: `1px solid ${isShort ? "rgba(248, 113, 113, 0.35)" : "rgba(16, 185, 129, 0.35)"}`,
                          }}
                        >
                          {ep.direction.toUpperCase()}
                        </span>
                        <span
                          style={{
                            fontSize: "10px",
                            fontWeight: 800,
                            padding: "1px 7px",
                            borderRadius: "4px",
                            color: onRadar ? C.green : C.yellow,
                            background: onRadar ? "rgba(16, 185, 129, 0.12)" : "rgba(250, 204, 21, 0.12)",
                            border: `1px solid ${onRadar ? "rgba(16, 185, 129, 0.3)" : "rgba(250, 204, 21, 0.35)"}`,
                          }}
                        >
                          {onRadar ? "ON 5/5 RADAR" : "EVICTED · 48H WATCH"}
                        </span>
                      </div>
                      <div style={{ fontSize: "10.5px", color: C.muted, marginTop: "4px" }}>
                        {ep.setup_label} · Score <strong style={{ color: C.white }}>{ep.score.toFixed(1)}</strong>
                      </div>
                    </div>

                    <div style={{ textAlign: "right", flexShrink: 0 }}>
                      <div style={{ fontSize: "15px", fontWeight: 800, color: C.white }}>
                        {px(livePx)}
                      </div>
                      <div
                        style={{
                          fontSize: "12px",
                          fontWeight: 800,
                          color: liveRet === null ? C.muted : liveRet >= 0 ? C.green : C.red,
                        }}
                      >
                        {fmtPct(liveRet)}
                      </div>
                    </div>
                  </div>

                  {/* Excursion & Clock Bar */}
                  <div
                    style={{
                      display: "grid",
                      gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
                      gap: "8px",
                      padding: "8px 10px",
                      borderRadius: "6px",
                      background: "rgba(15, 23, 42, 0.72)",
                      border: "1px solid rgba(255, 255, 255, 0.07)",
                      fontSize: "11px",
                    }}
                  >
                    <div>
                      <div style={{ color: C.muted, fontSize: "9.5px" }}>PEAK MFE</div>
                      <strong style={{ color: C.green }}>{fmtPct(effectiveMfe)}</strong>
                    </div>
                    <div>
                      <div style={{ color: C.muted, fontSize: "9.5px" }}>WORST MAE</div>
                      <strong style={{ color: C.red }}>{fmtPct(effectiveMae)}</strong>
                    </div>
                    <div>
                      <div style={{ color: C.muted, fontSize: "9.5px" }}>48H CLOCK</div>
                      <strong style={{ color: C.yellow, display: "inline-flex", alignItems: "center", gap: "3px" }}>
                        <Clock size={10} /> {fmtDuration(ep.remaining_seconds)} left
                      </strong>
                    </div>
                  </div>

                  {/* Levels Grid */}
                  <div
                    style={{
                      display: "grid",
                      gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
                      gap: "5px 12px",
                      fontSize: "11px",
                    }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between" }}>
                      <span style={{ color: C.muted }}>Signal Mid:</span>
                      <strong style={{ color: C.yellow }}>{px(ep.signal_price)}</strong>
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between" }}>
                      <span style={{ color: C.muted }}>Target 1:</span>
                      <strong style={{ color: C.green }}>{px(ep.target_1)}</strong>
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between" }}>
                      <span style={{ color: C.muted }}>Limit Retest:</span>
                      <strong style={{ color: C.white }}>{px(ep.entry_price)}</strong>
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between" }}>
                      <span style={{ color: C.muted }}>Stop Loss:</span>
                      <strong style={{ color: C.red }}>{px(ep.stop_price)}</strong>
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between" }}>
                      <span style={{ color: C.muted }}>Breakout:</span>
                      <strong style={{ color: C.amber }}>{px(ep.trigger_price)}</strong>
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between" }}>
                      <span style={{ color: C.muted }}>Published:</span>
                      <span style={{ color: C.softWhite }}>{fmtUtc(ep.published_at)}</span>
                    </div>
                  </div>

                  {/* Plan touch indicators + eviction note */}
                  <div style={{ display: "flex", gap: "6px", flexWrap: "wrap", fontSize: "10px" }}>
                    {ep.retest_touched && (
                      <span
                        style={{
                          padding: "2px 6px",
                          borderRadius: "4px",
                          background: "rgba(16, 185, 129, 0.14)",
                          color: C.green,
                          fontWeight: 700,
                        }}
                      >
                        ✓ Retest Touched
                      </span>
                    )}
                    {ep.breakout_touched && (
                      <span
                        style={{
                          padding: "2px 6px",
                          borderRadius: "4px",
                          background: "rgba(250, 204, 21, 0.14)",
                          color: C.yellow,
                          fontWeight: 700,
                        }}
                      >
                        ⚡ Breakout Touched
                      </span>
                    )}
                    {ep.eviction_reason && !onRadar && (
                      <span
                        style={{
                          padding: "2px 6px",
                          borderRadius: "4px",
                          background: "rgba(255, 255, 255, 0.06)",
                          color: C.muted,
                        }}
                      >
                        Radar exit: {ep.eviction_reason}
                      </span>
                    )}
                  </div>

                  {/* Footer Actions */}
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      paddingTop: "8px",
                      borderTop: "1px solid rgba(255, 255, 255, 0.08)",
                      gap: "8px",
                    }}
                  >
                    <Link
                      href={`/?symbol=${encodeURIComponent(ep.symbol)}`}
                      style={{
                        display: "inline-flex",
                        alignItems: "center",
                        gap: "5px",
                        fontSize: "11.5px",
                        fontWeight: 700,
                        color: C.white,
                        textDecoration: "none",
                      }}
                    >
                      Open Live Chart <ExternalLink size={12} style={{ color: C.amber }} />
                    </Link>

                    {canPinToPersonal && onPinToPersonal && (
                      <button
                        type="button"
                        disabled={pinningId === ep.episode_id || pinnedIds[ep.episode_id]}
                        onClick={() => void handlePin(ep)}
                        style={{
                          display: "inline-flex",
                          alignItems: "center",
                          gap: "5px",
                          fontSize: "11px",
                          fontWeight: 700,
                          padding: "4px 10px",
                          borderRadius: "6px",
                          border: "1px solid rgba(250, 204, 21, 0.35)",
                          background: pinnedIds[ep.episode_id]
                            ? "rgba(16, 185, 129, 0.16)"
                            : "rgba(250, 204, 21, 0.12)",
                          color: pinnedIds[ep.episode_id] ? C.green : C.yellow,
                          cursor:
                            pinningId === ep.episode_id || pinnedIds[ep.episode_id]
                              ? "default"
                              : "pointer",
                        }}
                      >
                        <Pin size={11} />
                        {pinnedIds[ep.episode_id]
                          ? "Pinned to My Watchlist"
                          : pinningId === ep.episode_id
                            ? "Pinning..."
                            : "Pin to My Watchlist"}
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )
      )}

      {/* VIEW 2: RESOLVED OUTCOMES */}
      {subTab === "resolved" && (
        <div className="glass-panel" style={{ padding: "16px", fontFamily: "var(--font-mono)" }}>
          <div style={{ display: "flex", gap: "8px", marginBottom: "14px", flexWrap: "wrap" }}>
            {(["ALL", "TARGET_REACHED", "STOP_BREACHED", "EXPIRED_48H", "HARD_INVALIDATED"] as const).map((f) => {
              const active = resolvedFilter === f;
              const col =
                f === "TARGET_REACHED"
                  ? C.green
                  : f === "STOP_BREACHED" || f === "HARD_INVALIDATED"
                    ? C.red
                    : f === "EXPIRED_48H"
                      ? C.yellow
                      : C.white;
              return (
                <button
                  key={f}
                  type="button"
                  onClick={() => setResolvedFilter(f)}
                  style={{
                    padding: "5px 10px",
                    borderRadius: "6px",
                    fontSize: "11px",
                    fontWeight: 700,
                    cursor: "pointer",
                    background: active ? "rgba(255, 255, 255, 0.1)" : "transparent",
                    color: active ? col : C.muted,
                    border: `1px solid ${active ? col : "var(--border-subtle)"}`,
                  }}
                >
                  {f === "ALL" ? "All Resolved" : f.replace(/_/g, " ")}
                </button>
              );
            })}
          </div>

          {filteredResolved.length === 0 ? (
            <div style={{ padding: "28px", textAlign: "center", color: C.muted, fontSize: "12px" }}>
              No resolved episodes in this filter yet. Episodes appear here immediately upon Target 1, Stop Loss, or 48h expiration.
            </div>
          ) : (
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "11.5px" }}>
                <thead>
                  <tr style={{ borderBottom: "1px solid var(--border-subtle)", color: C.muted, textAlign: "left" }}>
                    <th style={{ padding: "8px" }}>Symbol</th>
                    <th style={{ padding: "8px" }}>Outcome</th>
                    <th style={{ padding: "8px" }}>Published (UTC)</th>
                    <th style={{ padding: "8px" }}>Resolved (UTC)</th>
                    <th style={{ padding: "8px", textAlign: "right" }}>Signal → Exit</th>
                    <th style={{ padding: "8px", textAlign: "right" }}>Return</th>
                    <th style={{ padding: "8px", textAlign: "right" }}>Peak MFE</th>
                    <th style={{ padding: "8px", textAlign: "right" }}>Worst MAE</th>
                    <th style={{ padding: "8px" }}>Plan Touch</th>
                    <th style={{ padding: "8px" }}>Radar</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredResolved.map((ep) => {
                    const ret = ep.resolved_return_pct;
                    const badgeColor =
                      ep.status === "TARGET_REACHED"
                        ? C.green
                        : ep.status === "STOP_BREACHED" || ep.status === "HARD_INVALIDATED"
                          ? C.red
                          : C.yellow;
                    return (
                      <tr
                        key={ep.episode_id}
                        style={{ borderBottom: "1px solid rgba(255, 255, 255, 0.05)" }}
                      >
                        <td style={{ padding: "8px", fontWeight: 700, color: C.white }}>
                          <Link
                            href={`/?symbol=${encodeURIComponent(ep.symbol)}`}
                            style={{ color: C.white, textDecoration: "none" }}
                          >
                            {ep.symbol}
                          </Link>{" "}
                          <span style={{ fontSize: "10px", color: ep.direction === "short" ? C.red : C.green }}>
                            {ep.direction.toUpperCase()}
                          </span>
                        </td>
                        <td style={{ padding: "8px" }}>
                          <span
                            style={{
                              display: "inline-flex",
                              alignItems: "center",
                              gap: "4px",
                              color: badgeColor,
                              fontWeight: 800,
                              fontSize: "10.5px",
                            }}
                          >
                            {ep.status === "TARGET_REACHED" && <CheckCircle2 size={12} />}
                            {ep.status === "STOP_BREACHED" && <XCircle size={12} />}
                            {ep.status === "EXPIRED_48H" && <Timer size={12} />}
                            {ep.status.replace(/_/g, " ")}
                          </span>
                        </td>
                        <td style={{ padding: "8px", color: C.softWhite }}>{fmtUtc(ep.published_at)}</td>
                        <td style={{ padding: "8px", color: C.softWhite }}>
                          {fmtUtc(ep.resolved_at)} ({fmtDuration(ep.elapsed_seconds)})
                        </td>
                        <td style={{ padding: "8px", textAlign: "right", color: C.softWhite }}>
                          ${formatCandidatePrice(ep.signal_price, ep.tick_size)} → {ep.resolved_price === null
                            ? "Unknown" : `$${formatCandidatePrice(ep.resolved_price, ep.tick_size)}`}
                          <div style={{ color: C.muted, fontSize: "10px" }}>
                            {ep.measurement_status === "legacy_unverified"
                              ? "Legacy measurement unverified"
                              : ep.resolved_price === null
                                ? ep.last_observed_price != null && ep.last_checked_at
                                  ? `48h endpoint unavailable (last obs $${formatCandidatePrice(ep.last_observed_price, ep.tick_size)} @ ${fmtUtc(ep.last_checked_at)})`
                                  : "48h endpoint unavailable (no post-signal data)"
                                : ep.coverage_complete
                                  ? "Complete candle coverage"
                                  : ep.status === "EXPIRED_48H"
                                    ? "Measured 48h close; partial path coverage"
                                    : "Incomplete coverage; first barrier unverified"}
                          </div>
                        </td>
                        <td
                          style={{
                            padding: "8px",
                            textAlign: "right",
                            fontWeight: 800,
                            color: ret === null ? C.muted : ret >= 0 ? C.green : C.red,
                          }}
                        >
                          {fmtPct(ret)}
                        </td>
                        <td style={{ padding: "8px", textAlign: "right", color: C.green, fontWeight: 700 }}>
                          {fmtPct(ep.peak_mfe_pct)}
                        </td>
                        <td style={{ padding: "8px", textAlign: "right", color: C.red, fontWeight: 700 }}>
                          {fmtPct(ep.worst_mae_pct)}
                        </td>
                        <td style={{ padding: "8px", color: C.yellow, fontSize: "10.5px" }}>
                          {[ep.retest_touched && "Retest", ep.breakout_touched && "Breakout"]
                            .filter(Boolean)
                            .join(" + ") || "None"}
                        </td>
                        <td style={{ padding: "8px", fontSize: "10.5px", color: ep.radar_presence === "ON_RADAR" ? C.green : C.muted }}>
                          {ep.radar_presence === "ON_RADAR" ? "On 5/5" : "Evicted"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* VIEW 3: DAILY & ARCHETYPE PERFORMANCE */}
      {subTab === "analytics" && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(440px, 1fr))", gap: "16px", fontFamily: "var(--font-mono)" }}>
          <div className="glass-panel" style={{ padding: "16px" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "12px" }}>
              <BarChart3 size={15} style={{ color: C.amber }} />
              <strong style={{ color: C.white, fontSize: "13px" }}>Daily Cohort Performance (UTC)</strong>
            </div>
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "11px" }}>
                <thead>
                  <tr style={{ borderBottom: "1px solid var(--border-subtle)", color: C.muted, textAlign: "left" }}>
                    <th style={{ padding: "6px" }}>Date</th>
                    <th style={{ padding: "6px", textAlign: "right" }}>Total</th>
                    <th style={{ padding: "6px", textAlign: "right" }}>Active</th>
                    <th style={{ padding: "6px", textAlign: "right" }}>T1</th>
                    <th style={{ padding: "6px", textAlign: "right" }}>Stop</th>
                    <th style={{ padding: "6px", textAlign: "right" }}>48h Exp</th>
                    <th style={{ padding: "6px", textAlign: "right" }}>Avg Ret</th>
                    <th style={{ padding: "6px", textAlign: "right" }}>Avg MFE</th>
                  </tr>
                </thead>
                <tbody>
                  {(data?.daily_breakdown ?? []).map((row) => (
                    <tr key={row.date} style={{ borderBottom: "1px solid rgba(255,255,255,0.05)" }}>
                      <td style={{ padding: "6px", color: C.white, fontWeight: 700 }}>{row.date}</td>
                      <td style={{ padding: "6px", textAlign: "right", color: C.softWhite }}>{row.total}</td>
                      <td style={{ padding: "6px", textAlign: "right", color: C.yellow }}>{row.active}</td>
                      <td style={{ padding: "6px", textAlign: "right", color: C.green, fontWeight: 700 }}>{row.target_reached}</td>
                      <td style={{ padding: "6px", textAlign: "right", color: C.red, fontWeight: 700 }}>{row.stop_breached}</td>
                      <td style={{ padding: "6px", textAlign: "right", color: C.muted }}>{row.expired_48h}</td>
                      <td style={{ padding: "6px", textAlign: "right", fontWeight: 700, color: (row.avg_return_pct ?? 0) >= 0 ? C.green : C.red }}>
                        {fmtPct(row.avg_return_pct)}
                      </td>
                      <td style={{ padding: "6px", textAlign: "right", color: C.green }}>
                        {fmtPct(row.avg_peak_mfe_pct)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="glass-panel" style={{ padding: "16px" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "12px" }}>
              <BarChart3 size={15} style={{ color: C.green }} />
              <strong style={{ color: C.white, fontSize: "13px" }}>Setup Archetype Performance</strong>
            </div>
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "11px" }}>
                <thead>
                  <tr style={{ borderBottom: "1px solid var(--border-subtle)", color: C.muted, textAlign: "left" }}>
                    <th style={{ padding: "6px" }}>Archetype</th>
                    <th style={{ padding: "6px", textAlign: "right" }}>Total</th>
                    <th style={{ padding: "6px", textAlign: "right" }}>T1</th>
                    <th style={{ padding: "6px", textAlign: "right" }}>Stop</th>
                    <th style={{ padding: "6px", textAlign: "right" }}>Avg Ret</th>
                    <th style={{ padding: "6px", textAlign: "right" }}>Avg MFE</th>
                    <th style={{ padding: "6px", textAlign: "right" }}>Avg MAE</th>
                  </tr>
                </thead>
                <tbody>
                  {(data?.archetype_breakdown ?? []).map((row) => (
                    <tr key={row.setup_label} style={{ borderBottom: "1px solid rgba(255,255,255,0.05)" }}>
                      <td style={{ padding: "6px", color: C.white, fontWeight: 700 }}>{row.setup_label}</td>
                      <td style={{ padding: "6px", textAlign: "right", color: C.softWhite }}>{row.total}</td>
                      <td style={{ padding: "6px", textAlign: "right", color: C.green, fontWeight: 700 }}>{row.target_reached}</td>
                      <td style={{ padding: "6px", textAlign: "right", color: C.red, fontWeight: 700 }}>{row.stop_breached}</td>
                      <td style={{ padding: "6px", textAlign: "right", fontWeight: 700, color: (row.avg_return_pct ?? 0) >= 0 ? C.green : C.red }}>
                        {fmtPct(row.avg_return_pct)}
                      </td>
                      <td style={{ padding: "6px", textAlign: "right", color: C.green }}>
                        {fmtPct(row.avg_peak_mfe_pct)}
                      </td>
                      <td style={{ padding: "6px", textAlign: "right", color: C.red }}>
                        {fmtPct(row.avg_worst_mae_pct)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {data?.semantics && (
        <div style={{ fontSize: "10.5px", color: C.muted, fontFamily: "var(--font-mono)" }}>
          {data.semantics}
        </div>
      )}
    </div>
  );
}
