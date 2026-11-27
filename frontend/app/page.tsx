"use client";

import { bookEventQuote, bookQuote } from "./lib/radarInsights";

import { useEffect, useState, useMemo, useCallback, useRef, Suspense } from "react";
import dynamic from "next/dynamic";
import { useSearchParams } from "next/navigation";
import type { Opportunity, SidebarWatchlistEpisode, UniverseToken } from "./components/CandidateSidebar";
import type { Candle, CandleInterval } from "./components/TradingChart";
import { Zap, RefreshCw, AlertCircle } from "lucide-react";
import baselineCandles from "@/public/baseline_candles.json";
import { useLocalStorage } from "./hooks/useLocalStorage";
import OperatorAuthModal from "./components/OperatorAuthModal";
import { operatorRequestHeaders } from "./lib/operatorClient";
import { isWithheldStatus, screenerPublication } from "./lib/screenerPublication";
import { getSupabaseClient, type UserWatchlistRow } from "./lib/supabaseClient";
import { AccountScope, type AccountTicket } from "./lib/accountScope";

async function resolveUserWatchlistView(
  symbol: string,
  scope: AccountScope,
  fallbackPrice?: string | null,
  tickSize?: string,
): Promise<ChartApiResponse["watchlist_view"] | null> {
  try {
    const ticket = scope.capture();
    if (!ticket.userId) return null;
    const supabase = getSupabaseClient();
    const { data: sessionData } = await supabase.auth.getSession();
    const userId = sessionData.session?.user?.id;
    if (!userId || ticket.userId !== userId || !scope.isCurrent(ticket)) return null;

    const { data: row, error } = await supabase
      .from("user_watchlists")
      .select("*")
      .eq("user_id", userId)
      .eq("symbol", symbol.toUpperCase())
      .maybeSingle();

    if (error || !row || !scope.isCurrent(ticket)) return null;
    const typed = row as UserWatchlistRow;
    const supportP = Number(typed.support_price);
    const stopP = Number(typed.invalidation_price);
    const t1 = Number(typed.target_1);
    const risk = Math.abs(supportP - stopP);
    const reward = Math.abs(t1 - supportP);
    const rr = risk > 0 && reward > 0 ? (reward / risk).toFixed(2) : null;

    return {
      account_ticket: ticket,
      symbol: typed.symbol,
      stage: typed.stage,
      timeframe: typed.timeframe,
      thesis: typed.thesis,
      current_price: fallbackPrice ?? String(typed.current_price),
      tick_size: tickSize,
      support_price: String(typed.support_price),
      trigger_price: String(typed.trigger_price),
      invalidation_price: String(typed.invalidation_price),
      target_1: String(typed.target_1),
      target_2: String(typed.target_2),
      risk_reward_ratio: rr,
      audit_verified: false,
      checklist: [
        {
          title: "Personal Watchlist Setup (Supabase RLS)",
          passed: false,
          status: "unknown",
          detail: "Displaying your private saved bracket levels on raw Binance USD-M candles.",
        },
      ],
      notes: typed.notes,
    };
  } catch {
    return null;
  }
}

const candleStore = baselineCandles as Record<string, Candle[]>;

const parseBool = (raw: string) => raw === "true";
const parseInterval = (raw: string) =>
  ["1h", "4h", "1d", "1w"].includes(raw) ? (raw as CandleInterval) : "1h";

interface ScreenerResponse {
  timestamp?: string;
  total_scanned?: number;
  qualifying_count?: number;
  macro_narrative?: string;
  is_scanning?: boolean;
  scan_source?: string;
  data_status?: string;
  stale?: boolean;
  stale_reason?: string;
  last_scan_error?: string;
  screening_policy_version?: string;
  opportunities?: Opportunity[];
}

// Sleek loading skeleton fallbacks for lazy-loaded components
const SidebarSkeleton = () => (
  <div
    style={{
      width: "320px",
      height: "100%",
      background: "rgba(11, 15, 25, 0.98)",
      borderRight: "1px solid var(--border-subtle)",
      padding: "16px",
      display: "flex",
      flexDirection: "column",
      gap: "12px",
    }}
  >
    <div
      style={{
        height: "36px",
        background: "rgba(255, 255, 255, 0.04)",
        borderRadius: "8px",
      }}
    />
    <div style={{ display: "flex", gap: "6px" }}>
      <div
        style={{
          height: "22px",
          width: "50px",
          background: "rgba(255, 255, 255, 0.04)",
          borderRadius: "6px",
        }}
      />
      <div
        style={{
          height: "22px",
          width: "60px",
          background: "rgba(255, 255, 255, 0.04)",
          borderRadius: "6px",
        }}
      />
      <div
        style={{
          height: "22px",
          width: "60px",
          background: "rgba(255, 255, 255, 0.04)",
          borderRadius: "6px",
        }}
      />
    </div>
    {[...Array(6)].map((_, i) => (
      <div
        key={i}
        style={{
          height: "82px",
          background: "rgba(255, 255, 255, 0.02)",
          border: "1px solid var(--border-subtle)",
          borderRadius: "10px",
        }}
      />
    ))}
  </div>
);

const ChartSkeleton = () => (
  <div
    style={{
      flex: 1,
      height: "100%",
      background: "#090d16",
      display: "flex",
      flexDirection: "column",
      alignItems: "center",
      justifyContent: "center",
      position: "relative",
    }}
  >
    <div
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "8px",
        background: "rgba(244, 157, 13, 0.08)",
        border: "1px solid rgba(244, 157, 13, 0.25)",
        borderRadius: "8px",
        padding: "8px 16px",
        color: "#f49d0d",
        fontSize: "12px",
        fontWeight: 700,
        fontFamily: "var(--font-mono)",
      }}
    >
      <RefreshCw size={14} style={{ animation: "spin 1s linear infinite" }} />
      Loading Institutional Chart Engine...
    </div>
  </div>
);

const PanelSkeleton = () => (
  <div
    style={{
      width: "360px",
      height: "100%",
      background: "rgba(11, 15, 25, 0.98)",
      borderLeft: "1px solid var(--border-subtle)",
      padding: "16px",
      display: "flex",
      flexDirection: "column",
      gap: "14px",
    }}
  >
    <div
      style={{
        height: "24px",
        width: "60%",
        background: "rgba(255, 255, 255, 0.04)",
        borderRadius: "6px",
      }}
    />
    {[...Array(5)].map((_, i) => (
      <div
        key={i}
        style={{
          height: "56px",
          background: "rgba(255, 255, 255, 0.02)",
          border: "1px solid var(--border-subtle)",
          borderRadius: "8px",
        }}
      />
    ))}
  </div>
);

// Dynamically lazy-loaded components
const CandidateSidebar = dynamic(
  () => import("./components/CandidateSidebar"),
  {
    loading: () => <SidebarSkeleton />,
  },
);

const TradingChart = dynamic(() => import("./components/TradingChart"), {
  ssr: false,
  loading: () => <ChartSkeleton />,
});

const VerificationPanel = dynamic(
  () => import("./components/VerificationPanel"),
  {
    loading: () => <PanelSkeleton />,
  },
);

interface ChartApiResponse {
  record_kind?: "published_radar_candidate" | "watchlist_view" | "chart_inspection";
  symbol?: string;
  candles?: Candle[];
  opportunity?: Opportunity | null;
  watchlist_view?: {
    account_ticket?: AccountTicket;
    symbol: string;
    stage?: string;
    timeframe?: string;
    thesis?: string;
    current_price?: string | null;
    tick_size?: string;
    support_price?: string;
    trigger_price?: string;
    invalidation_price?: string;
    target_1?: string;
    target_2?: string;
    risk_reward_ratio?: string | null;
    audit_verified?: boolean;
    checklist?: { title: string; passed: boolean; status?: string; detail: string }[];
    notes?: string;
  } | null;
  inspection?: {
    symbol: string;
    current_price?: string | null;
    tick_size?: string;
    note?: string;
  } | null;
}

interface InspectedSymbolView {
  record_kind: "watchlist_view" | "chart_inspection";
  symbol: string;
  candles: Candle[];
  current_price?: string | null;
  tick_size?: string;
  note?: string;
  watchlist_view?: ChartApiResponse["watchlist_view"];
}

function RadarPageContent() {
  const accountScope = useRef(new AccountScope());
  const searchParams = useSearchParams();
  const symbolParam = searchParams ? searchParams.get("symbol") : null;

  const [opportunities, setOpportunities] = useState<Opportunity[]>([]);
  const [autoWatchlist, setAutoWatchlist] = useState<SidebarWatchlistEpisode[]>([]);
  const [universeTokens, setUniverseTokens] = useState<UniverseToken[]>([]);
  const [activeOpportunity, setActiveOpportunity] =
    useState<Opportunity | null>(null);
  const [storedInspectedView, setInspectedView] = useState<InspectedSymbolView | null>(null);
  const [accountTicket, setAccountTicket] = useState<AccountTicket>({ userId: null, epoch: 0 });
  const privateTicket = storedInspectedView?.watchlist_view?.account_ticket;
  const inspectedView = privateTicket && (privateTicket.userId !== accountTicket.userId
    || privateTicket.epoch !== accountTicket.epoch) ? null : storedInspectedView;
  useEffect(() => {
    const supabase = getSupabaseClient();
    let alive = true;
    const applyIdentity = (id: string | null) => {
      if (!alive || !accountScope.current.setIdentity(id)) return;
      setAccountTicket(accountScope.current.capture());
      setInspectedView(view => view?.watchlist_view?.account_ticket ? null : view);
    };
    let authEventSeen = false;
    void supabase.auth.getSession().then(({ data }) => {
      if (!authEventSeen) applyIdentity(data.session?.user.id ?? null);
    });
    const { data } = supabase.auth.onAuthStateChange((_event, session) => {
      authEventSeen = true;
      applyIdentity(session?.user.id ?? null);
    });
    return () => { alive = false; data.subscription.unsubscribe(); };
  }, []);

  // Re-resolve personal watchlist overlay cleanly whenever the authenticated identity changes
  useEffect(() => {
    if (!symbolParam || !accountTicket.userId) return;
    const cleanTarget = symbolParam.toUpperCase();
    const ticket = accountTicket;
    let alive = true;

    const refreshPersonalOverlay = async () => {
      if (!accountScope.current.isCurrent(ticket)) return;
      const personalWv = await resolveUserWatchlistView(cleanTarget, accountScope.current);
      if (!alive || !personalWv || !accountScope.current.isCurrent(ticket)) return;
      setInspectedView((prev) => {
        if (!prev || prev.symbol.toUpperCase() !== cleanTarget) return prev;
        return {
          ...prev,
          record_kind: "watchlist_view",
          current_price: prev.current_price ?? personalWv.current_price ?? null,
          tick_size: prev.tick_size ?? personalWv.tick_size,
          watchlist_view: {
            ...personalWv,
            current_price: prev.current_price ?? personalWv.current_price ?? null,
            tick_size: prev.tick_size ?? personalWv.tick_size,
          },
        };
      });
    };

    void refreshPersonalOverlay();
    return () => {
      alive = false;
    };
  }, [symbolParam, accountTicket]);
  const [activeCandle, setActiveCandle] = useState<Candle | null>(null);
  const [radarQuotes, setRadarQuotes] = useState<Record<string, { price: number; observed_at: string }>>({});
  const [livePrices, setLivePrices] = useState<Record<string, number>>({});
  const [priceChanges24h, setPriceChanges24h] = useState<
    Record<string, number>
  >({});
  const [loading, setLoading] = useState(true);
  const [isScanning, setIsScanning] = useState(false);
  const [scanSource, setScanSource] = useState<string | null>(null);
  const [dataStatus, setDataStatus] = useState<string>("current");
  const [statusDetail, setStatusDetail] = useState<string | null>(null);
  const [scanNotification, setScanNotification] = useState<string | null>(null);
  const [operatorAuthOpen, setOperatorAuthOpen] = useState(false);
  const [operatorAuthActionLabel, setOperatorAuthActionLabel] = useState("manual universe rescan");
  const pendingOperatorActionRef = useRef<(() => void) | null>(null);
  const [policyVersion, setPolicyVersion] = useState<string>("atlas-v3.1-discovery-guards-1");
  const fetchInFlightRef = useRef(false);
  const fetchScreenerRef = useRef<((isLiveRefresh?: boolean, scope?: "core" | "full") => Promise<void>) | null>(null);
  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const notificationTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const awaitingScanCompletionRef = useRef(false);
  const manualScanRequestedRef = useRef(false);
  const scanStartedAtRef = useRef<number | null>(null);
  const [showLeftSidebar, setShowLeftSidebar] = useState(true);
  const [showRightPanel, setShowRightPanel] = useState(true);
  const [adaptedLevelsMap, setAdaptedLevelsMap] = useState<
    Record<
      string,
      {
        pocPrice: number;
        vahPrice: number;
        valPrice: number;
        entryPrice: number;
        stopPrice: number;
        breakoutTrigger: number;
        targets: number[];
      }
    >
  >({});

  // User indicator & timeframe preferences (persisted across tokens and browser reloads)
  const [showLevels, setShowLevels] = useLocalStorage(
    "atlas_pref_show_levels_v2",
    true,
    parseBool,
  );
  const [showTargets, setShowTargets] = useLocalStorage(
    "atlas_pref_show_targets",
    true,
    parseBool,
  );
  const [showVolume, setShowVolume] = useLocalStorage(
    "atlas_pref_show_volume",
    true,
    parseBool,
  );
  const [candleInterval, setCandleInterval] = useLocalStorage<CandleInterval>(
    "atlas_pref_candle_interval",
    "1h",
    parseInterval,
  );

  const handleToggleLevels = useCallback(
    () => setShowLevels((prev) => !prev),
    [setShowLevels],
  );
  const handleToggleTargets = useCallback(
    () => setShowTargets((prev) => !prev),
    [setShowTargets],
  );
  const handleToggleVolume = useCallback(
    () => setShowVolume((prev) => !prev),
    [setShowVolume],
  );
  const handleChangeInterval = useCallback(
    (intv: CandleInterval) => setCandleInterval(intv),
    [setCandleInterval],
  );
  const handleToggleLeftSidebar = useCallback(
    () => setShowLeftSidebar((v) => !v),
    [],
  );
  const handleToggleRightPanel = useCallback(
    () => setShowRightPanel((v) => !v),
    [],
  );
  const handleCandleChange = useCallback((c: Candle) => setActiveCandle(c), []);

  const handleSelectOpportunity = useCallback((opp: Opportunity) => {
    const existingCandles =
      opp.candles && opp.candles.length > 0
        ? opp.candles
        : candleStore[opp.symbol] || [];

    setInspectedView(null);
    setActiveOpportunity({ ...opp, candles: existingCandles });
    if (existingCandles && existingCandles.length > 0) {
      setActiveCandle(existingCandles[existingCandles.length - 1]);
    } else {
      // On-demand fetch from chart API if candles were not pre-fetched in screener payload
      void fetch(`/api/chart/${opp.symbol}`)
        .then((res) => (res.ok ? res.json() : null))
        .then((chartData) => {
          if (chartData && Array.isArray(chartData.candles) && chartData.candles.length > 0) {
            setActiveOpportunity((prev) => {
              if (prev && prev.symbol === opp.symbol) {
                return { ...prev, candles: chartData.candles };
              }
              return prev;
            });
            setActiveCandle(chartData.candles[chartData.candles.length - 1]);
          }
        })
        .catch(() => {});
    }
  }, []);

  const handleSelectWatchlistEpisode = useCallback((ep: SidebarWatchlistEpisode) => {
    const targets = [ep.target_1, ep.target_2, ep.target_3].filter(
      (v): v is number => v !== null && Number.isFinite(v) && v > 0,
    );
    const risk = Math.abs(ep.entry_price - ep.stop_price);
    const rrTarget = targets.length > 1 ? targets[1] : targets[0] ?? ep.entry_price;
    const rr = risk > 0 ? Math.abs(rrTarget - ep.entry_price) / risk : 0;
    const checklist =
      ep.checklist && ep.checklist.length > 0
        ? ep.checklist
        : [
            {
              title: "Watchlist Gate Status",
              passed: false,
              status: "failed" as const,
              detail: ep.soft_gate_summary || ep.eviction_reason || "Soft-evicted from 5/5 Radar",
            },
          ];

    const watchlistOpp: Opportunity = {
      symbol: ep.symbol,
      base_asset: ep.symbol.replace(/USDT$/, "").replace(/USDC$/, ""),
      direction: ep.direction,
      setup_label: ep.setup_label,
      regime: "watchlist_48h",
      score: ep.score,
      current_price: ep.last_price || ep.signal_price,
      suggested_entry: ep.entry_price,
      pullback_entry: ep.entry_price,
      breakout_trigger: ep.trigger_price ?? undefined,
      suggested_stop: ep.stop_price,
      suggested_targets: targets,
      risk_reward_ratio: rr.toFixed(2),
      risk_reward_target_index: targets.length > 1 ? 2 : 1,
      poc_price: ep.poc_price ?? undefined,
      vah_price: ep.vah_price ?? undefined,
      val_price: ep.val_price ?? undefined,
      execution_mode: ep.execution_mode,
      tick_size: ep.tick_size,
      checklist,
      gates_passed: ep.gates_passed,
      gates_total: ep.gates_total,
      failed_gates: checklist.filter((c) => !c.passed).map((c) => c.title),
      radar_status: "WATCHLIST_48H",
      radar_status_label:
        ep.radar_status_label === "PRE-IGNITION COIL"
          ? "PRE-IGNITION COIL · WATCHLIST"
          : `${ep.gates_passed}/${ep.gates_total} WATCHLIST · 48H`,
      first_seen_at: ep.published_at,
      evaluated_at: ep.published_at,
      radar_signal: {
        version: 1,
        episode_id: ep.episode_id,
        published_at: ep.published_at,
        price: String(ep.signal_price),
        quote_observed_at: ep.published_at,
      },
      rationale:
        ep.radar_status_label === "PRE-IGNITION COIL"
          ? [
              `Staged in ALFA v3.5 Pre-Ignition Watchlist (${ep.soft_gate_summary || "5/5 structural base coiled; awaiting kinetic trigger"}).`,
              "Automatically graduates onto 5/5 Actionable Radar once 15m/1h velocity and buyer absorption ignite.",
            ]
          : [
              `Promoted to 48h Potential Watchlist after leaving live 5/5 Radar (${ep.soft_gate_summary || ep.eviction_reason || "soft flow dip"}).`,
              "All 4 structural gates remained intact; removed automatically upon Target 1, Stop Breach, 48h expiry, or 5/5 Radar re-entry.",
            ],
      candles: [],
    };

    setInspectedView(null);
    setActiveOpportunity(watchlistOpp);

    void fetch(`/api/chart/${encodeURIComponent(ep.symbol)}`)
      .then((res) => (res.ok ? res.json() : null))
      .then((chartData) => {
        if (chartData && Array.isArray(chartData.candles) && chartData.candles.length > 0) {
          setActiveOpportunity((prev) => {
            if (prev && prev.symbol === ep.symbol) {
              return { ...prev, candles: chartData.candles };
            }
            return prev;
          });
          setActiveCandle(chartData.candles[chartData.candles.length - 1]);
        }
      })
      .catch(() => {});
  }, []);

  const inspectRequestIdRef = useRef(0);

  const handleInspectUniverseSymbol = useCallback(
    async (symbol: string) => {
      const cleanTarget = symbol.toUpperCase();
      const requestId = ++inspectRequestIdRef.current;

      const existingOpp = opportunities.find(
        (o) => o.symbol.toUpperCase() === cleanTarget,
      );
      if (existingOpp) {
        handleSelectOpportunity(existingOpp);
        return;
      }

      const existingWatchlist = autoWatchlist.find(
        (ep) => ep.symbol.toUpperCase() === cleanTarget,
      );
      if (existingWatchlist) {
        handleSelectWatchlistEpisode(existingWatchlist);
        return;
      }

      try {
        const res = await fetch(`/api/chart/${encodeURIComponent(cleanTarget)}`).catch(() => null);
        if (!res || !res.ok || inspectRequestIdRef.current !== requestId) return;
        const chartData = (await res.json()) as ChartApiResponse;
        if (inspectRequestIdRef.current !== requestId) return;
        const chartCandles =
          chartData.candles && chartData.candles.length > 0 ? chartData.candles : [];

        if (chartData.record_kind === "published_radar_candidate" && chartData.opportunity) {
          const oppWithCandles: Opportunity = {
            ...chartData.opportunity,
            candles: chartCandles,
          };
          setInspectedView(null);
          setActiveOpportunity(oppWithCandles);
        } else {
          const scopeTicket = accountScope.current.capture();
          const personalWv = await resolveUserWatchlistView(
            cleanTarget,
            accountScope.current,
            chartData.inspection?.current_price ?? chartData.watchlist_view?.current_price ?? null,
            chartData.inspection?.tick_size ?? chartData.watchlist_view?.tick_size,
          );
          if (
            inspectRequestIdRef.current !== requestId ||
            !accountScope.current.isCurrent(scopeTicket)
          ) {
            return;
          }
          const effectiveWv = personalWv ?? chartData.watchlist_view ?? null;
          setInspectedView({
            record_kind: effectiveWv ? "watchlist_view" : "chart_inspection",
            symbol: cleanTarget,
            candles: chartCandles,
            current_price: effectiveWv?.current_price ?? chartData.inspection?.current_price ?? null,
            tick_size: effectiveWv?.tick_size ?? chartData.inspection?.tick_size,
            note: chartData.inspection?.note,
            watchlist_view: effectiveWv,
          });
        }
        if (chartCandles.length > 0) {
          setActiveCandle(chartCandles[chartCandles.length - 1]);
        }
      } catch (err) {
        console.warn("Error loading universe symbol chart:", err);
      }
    },
    [opportunities, autoWatchlist, handleSelectOpportunity, handleSelectWatchlistEpisode],
  );

  const handleLiveTick = useCallback(
    (tick: { price: number }) => {
      const activeSym = inspectedView?.symbol ?? activeOpportunity?.symbol;
      if (!activeSym) return;
      setLivePrices((prev) => {
        if (prev[activeSym] === tick.price) return prev;
        return {
          ...prev,
          [activeSym]: tick.price,
        };
      });
    },
    [inspectedView, activeOpportunity],
  );

  const fetchScreener = useCallback(async (isLiveRefresh: boolean = false, scope: "core" | "full" = "core") => {
    const clearPollTimer = () => {
      if (pollTimerRef.current) {
        clearTimeout(pollTimerRef.current);
        pollTimerRef.current = null;
      }
    };

    const scheduleTransientNotification = (message: string) => {
      setScanNotification(message);
      if (notificationTimerRef.current) {
        clearTimeout(notificationTimerRef.current);
      }
      notificationTimerRef.current = setTimeout(() => {
        setScanNotification(null);
        notificationTimerRef.current = null;
      }, 4000);
    };

    if (isLiveRefresh) {
      clearPollTimer();
      manualScanRequestedRef.current = true;
      awaitingScanCompletionRef.current = true;
      if (!scanStartedAtRef.current) {
        scanStartedAtRef.current = Date.now();
      }
      setIsScanning(true);
      window.dispatchEvent(new CustomEvent("atlas:scan-start"));
    } else if (fetchInFlightRef.current) {
      return;
    }

    const applyScreenerData = async (incoming: ScreenerResponse, notifyCompletion: boolean) => {
      if (incoming?.screening_policy_version) {
        setPolicyVersion(incoming.screening_policy_version);
      }
      if (!incoming || !incoming.opportunities) return;
      if (incoming.opportunities.length === 0) {
        setOpportunities([]);
        setActiveOpportunity(null);
        setActiveCandle(null);
        return;
      }
      const enriched: Opportunity[] = incoming.opportunities.map((o: Opportunity) => ({
        ...o,
        candles:
          o.candles && o.candles.length > 0
            ? o.candles
            : candleStore[o.symbol] || [],
      }));

      const cleanTarget = symbolParam ? symbolParam.toUpperCase() : null;
      let matchedTarget: Opportunity | null = null;

      if (cleanTarget) {
        matchedTarget =
          enriched.find(
            (item: Opportunity) =>
              item.symbol.toUpperCase() === cleanTarget,
          ) || null;

        if (!matchedTarget) {
          try {
            const chartRes = await fetch(
              `/api/chart/${cleanTarget}`,
            ).catch(() => null);
            if (chartRes && chartRes.ok) {
              const chartData = (await chartRes.json()) as ChartApiResponse;
              const chartCandles =
                chartData.candles && chartData.candles.length > 0
                  ? chartData.candles
                  : candleStore[cleanTarget] || [];
              if (chartData.record_kind === "published_radar_candidate" && chartData.opportunity) {
                matchedTarget = {
                  ...chartData.opportunity,
                  candles: chartCandles,
                };
                setInspectedView(null);
                enriched.unshift(matchedTarget);
              } else {
                // Prefer the signed-in user's personal Supabase watchlist item when inspecting a chart
                const scopeTicket = accountScope.current.capture();
                const personalWv = await resolveUserWatchlistView(
                  cleanTarget,
                  accountScope.current,
                  chartData.inspection?.current_price ?? chartData.watchlist_view?.current_price ?? null,
                  chartData.inspection?.tick_size ?? chartData.watchlist_view?.tick_size,
                );
                if (!accountScope.current.isCurrent(scopeTicket)) return;
                const effectiveWv = personalWv ?? chartData.watchlist_view ?? null;
                setInspectedView({
                  record_kind: effectiveWv ? "watchlist_view" : "chart_inspection",
                  symbol: cleanTarget,
                  candles: chartCandles,
                  current_price: effectiveWv?.current_price ?? chartData.inspection?.current_price ?? null,
                  tick_size: effectiveWv?.tick_size ?? chartData.inspection?.tick_size,
                  note: chartData.inspection?.note,
                  watchlist_view: effectiveWv,
                });
                if (chartCandles.length > 0) {
                  setActiveCandle(chartCandles[chartCandles.length - 1]);
                }
              }
            }
          } catch (cErr) {
            console.warn(
              "Could not load targeted symbol chart:",
              cErr,
            );
          }
        } else {
          setInspectedView(null);
        }
      }

      // Client-side base-asset deduplication: enforce single canonical contract per underlying asset
      const seenBases = new Set<string>();
      const dedupedEnriched: Opportunity[] = [];
      for (const item of enriched) {
        const rawBase = item.base_asset || item.symbol.replace(/USDT$/, "").replace(/USDC$/, "");
        const baseKey = rawBase.toUpperCase();
        if (baseKey && !seenBases.has(baseKey)) {
          seenBases.add(baseKey);
          dedupedEnriched.push(item);
        }
      }

      setOpportunities(dedupedEnriched);
      setAdaptedLevelsMap({});

      // Preserve active token or activate targeted candidate
      setActiveOpportunity((prev) => {
        if (matchedTarget) {
          if (matchedTarget.candles && matchedTarget.candles.length > 0) {
            setActiveCandle(
              matchedTarget.candles[matchedTarget.candles.length - 1],
            );
          }
          return matchedTarget;
        }
        if (prev) {
          const matched = enriched.find(
            (item: Opportunity) =>
              item.symbol.toUpperCase() === prev.symbol.toUpperCase(),
          );
          if (matched) {
            if (matched.candles && matched.candles.length > 0) {
              setActiveCandle(
                matched.candles[matched.candles.length - 1],
              );
            }
            return matched;
          }
        }
        if (enriched[0]?.candles && enriched[0].candles.length > 0) {
          setActiveCandle(
            enriched[0].candles[enriched[0].candles.length - 1],
          );
        }
        return enriched[0];
      });

      if (notifyCompletion) {
        scheduleTransientNotification(
          `✓ Rescan complete: verified ${dedupedEnriched.length} setups`,
        );
        window.dispatchEvent(
          new CustomEvent("atlas:scan-complete", {
            detail: { count: dedupedEnriched.length, timestamp: incoming.timestamp },
          }),
        );
      }
    };

    fetchInFlightRef.current = true;
    try {
      const cacheBuster = `t=${Date.now()}`;
      const url = isLiveRefresh
        ? `/api/screener/live?refresh=true&scope=${scope}&${cacheBuster}`
        : `/api/screener/live?${cacheBuster}`;

      const controller = new AbortController();
      const requestTimeout = setTimeout(() => controller.abort(), 25000);
      const res = await fetch(url, {
        cache: "no-store",
        credentials: "same-origin",
        headers: isLiveRefresh ? operatorRequestHeaders() : undefined,
        signal: controller.signal,
      })
        .catch(() => null)
        .finally(() => clearTimeout(requestTimeout));
      let data: ScreenerResponse | null = null;
      if (res && res.ok) {
        data = (await res.json()) as ScreenerResponse;
      } else if (isLiveRefresh && res && !res.ok) {
        setIsScanning(false);
        setScanSource(null);
        awaitingScanCompletionRef.current = false;
        manualScanRequestedRef.current = false;
        scanStartedAtRef.current = null;
        if (res.status === 401) {
          pendingOperatorActionRef.current = () => {
            void fetchScreenerRef.current?.(true, scope);
          };
          setOperatorAuthActionLabel("manual universe rescan");
          setOperatorAuthOpen(true);
        }
        const msg =
          res.status === 401 || res.status === 403 || res.status === 503
            ? "Manual rescan requires operator authorization"
            : res.status === 429
              ? "Rescan cooldown active; please wait 60s"
              : `Rescan unavailable (HTTP ${res.status})`;
        scheduleTransientNotification(msg);
        window.dispatchEvent(new CustomEvent("atlas:scan-error"));
        return;
      }

      if (data?.screening_policy_version) {
        setPolicyVersion(data.screening_policy_version);
      }

      const publication = data ? screenerPublication(data) : null;
      // Withholding takes precedence over progress, including during a scheduled refresh.
      if (publication?.action === "withhold") {
        setDataStatus(publication.status);
        setStatusDetail(publication.detail);
        setOpportunities([]);
        setActiveOpportunity(null);
        setActiveCandle(null);
      }

      // If backend reports an active background scan:
      // - For scheduled hourly refreshes carrying current verified cards, render them immediately
      //   so visitors see active setups while the background scan runs.
      // - Schedule a single adaptive follow-up poll.
      if (data && data.is_scanning) {
        setLoading(false);
        setIsScanning(true);
        setScanSource(data.scan_source || "manual_or_init");
        if (publication?.action === "replace" && Array.isArray(data.opportunities)) {
          setDataStatus(data.data_status || "current");
          setStatusDetail(null);
          if (data.opportunities.length > 0) {
            await applyScreenerData(data, false);
          } else {
            setOpportunities([]);
            setActiveOpportunity((prev) => (prev?.radar_status === "WATCHLIST_48H" ? prev : null));
          }
        }
        awaitingScanCompletionRef.current = true;
        if (!scanStartedAtRef.current) {
          scanStartedAtRef.current = Date.now();
        }
        const elapsedMs = Date.now() - scanStartedAtRef.current;
        const nextDelayMs = elapsedMs < 120000 ? 3000 : elapsedMs < 600000 ? 5000 : 8000;
        clearPollTimer();
        pollTimerRef.current = setTimeout(() => {
          pollTimerRef.current = null;
          void fetchScreenerRef.current?.(false, scope);
        }, nextDelayMs);
        return;
      }

      if (!data) {
        // Transient network hiccup while a scan is in flight: retry non-blockingly without dropping scan state
        if (awaitingScanCompletionRef.current) {
          clearPollTimer();
          pollTimerRef.current = setTimeout(() => {
            pollTimerRef.current = null;
            void fetchScreenerRef.current?.(false, scope);
          }, 5000);
        } else if (isLiveRefresh) {
          setIsScanning(false);
          setScanSource(null);
          manualScanRequestedRef.current = false;
          scheduleTransientNotification("Rescan error: connection timeout");
          window.dispatchEvent(new CustomEvent("atlas:scan-error"));
        }
        return;
      }

      clearPollTimer();
      const notifyCompletion =
        isLiveRefresh || awaitingScanCompletionRef.current || manualScanRequestedRef.current;
      awaitingScanCompletionRef.current = false;
      manualScanRequestedRef.current = false;
      scanStartedAtRef.current = null;
      setIsScanning(false);
      setScanSource(null);

      const resolvedStatus = publication?.status || "current";
      setDataStatus(resolvedStatus);
      const isDegradedOrStale = publication?.action === "withhold";
      if (isDegradedOrStale) {
        const detail =
          data.macro_narrative ||
          data.last_scan_error ||
          (resolvedStatus === "stale"
            ? "Cached qualifications expired past freshness TTL; awaiting fresh live scan."
            : "Live scan unavailable; prior qualifications withheld.");
        setStatusDetail(detail);
        setOpportunities([]);
        setActiveOpportunity(null);
        setActiveCandle(null);
        if (notifyCompletion) {
          scheduleTransientNotification(
            resolvedStatus === "stale"
              ? "Qualifications expired (stale) — withheld until next live scan"
              : "Scan failed or inputs unavailable — qualifications withheld",
          );
          window.dispatchEvent(new CustomEvent("atlas:scan-error"));
        }
        return;
      }
      setStatusDetail(null);

      // Apply newly verified live scan data
      if (Array.isArray(data.opportunities) && data.opportunities.length > 0) {
        await applyScreenerData(data, notifyCompletion);
      } else {
        setOpportunities([]);
        setActiveOpportunity((prev) => (prev?.radar_status === "WATCHLIST_48H" ? prev : null));
        if (notifyCompletion) {
          scheduleTransientNotification(
            "Scan complete: 0 tokens currently qualify for institutional gates",
          );
          window.dispatchEvent(
            new CustomEvent("atlas:scan-complete", {
              detail: { count: 0, timestamp: data?.timestamp },
            }),
          );
        } else if (symbolParam) {
          // Screener returned 0 items; load direct inspection/watchlist chart without fabricating a radar candidate
          const cleanTarget = symbolParam.toUpperCase();
          try {
            const chartRes = await fetch(`/api/chart/${cleanTarget}`, { cache: "no-store" }).catch(
              () => null,
            );
            if (chartRes && chartRes.ok) {
              const chartData = (await chartRes.json()) as ChartApiResponse;
              const chartCandles =
                chartData.candles && chartData.candles.length > 0
                  ? chartData.candles
                  : [];
              if (chartData.record_kind === "published_radar_candidate" && chartData.opportunity) {
                const targetOpp: Opportunity = {
                  ...chartData.opportunity,
                  candles: chartCandles,
                };
                setInspectedView(null);
                setOpportunities([targetOpp]);
                setActiveOpportunity(targetOpp);
              } else {
                const scopeTicket = accountScope.current.capture();
                const personalWv = await resolveUserWatchlistView(
                  cleanTarget,
                  accountScope.current,
                  chartData.inspection?.current_price ?? chartData.watchlist_view?.current_price ?? null,
                  chartData.inspection?.tick_size ?? chartData.watchlist_view?.tick_size,
                );
                if (!accountScope.current.isCurrent(scopeTicket)) return;
                const effectiveWv = personalWv ?? chartData.watchlist_view ?? null;
                setInspectedView({
                  record_kind: effectiveWv ? "watchlist_view" : "chart_inspection",
                  symbol: cleanTarget,
                  candles: chartCandles,
                  current_price: effectiveWv?.current_price ?? chartData.inspection?.current_price ?? null,
                  tick_size: effectiveWv?.tick_size ?? chartData.inspection?.tick_size,
                  note: chartData.inspection?.note,
                  watchlist_view: effectiveWv,
                });
              }
              if (chartCandles.length > 0) {
                setActiveCandle(chartCandles[chartCandles.length - 1]);
              }
            }
          } catch (cErr) {
            console.warn(
              "Could not load targeted symbol chart:",
              cErr,
            );
          }
        }
      }
    } catch (err) {
      console.warn("Using offline radar baseline:", err);
      if (isLiveRefresh) {
        setIsScanning(false);
        awaitingScanCompletionRef.current = false;
        manualScanRequestedRef.current = false;
        scanStartedAtRef.current = null;
        scheduleTransientNotification("Rescan error: connection timeout");
        window.dispatchEvent(new CustomEvent("atlas:scan-error"));
      }
    } finally {
      fetchInFlightRef.current = false;
      setLoading(false);
    }
  }, [symbolParam]);

  useEffect(() => {
    fetchScreenerRef.current = fetchScreener;
  }, [fetchScreener]);

  // When 0 live 5/5 Radar candidates exist, keep the Pre-Breakout Radar page alive
  // using staged Pre-Breakout Watchlist coils instead of dead-ending on an empty screen.
  useEffect(() => {
    if (loading || isScanning || isWithheldStatus(dataStatus) || symbolParam || inspectedView) {
      return;
    }
    if (opportunities.length > 0 || autoWatchlist.length === 0) {
      return;
    }
    const stillValidWatchlistSelection =
      activeOpportunity?.radar_status === "WATCHLIST_48H" &&
      autoWatchlist.some(
        (ep) => ep.symbol.toUpperCase() === activeOpportunity.symbol.toUpperCase(),
      );
    if (!stillValidWatchlistSelection) {
      const t = setTimeout(() => {
        handleSelectWatchlistEpisode(autoWatchlist[0]);
      }, 0);
      return () => clearTimeout(t);
    }
  }, [
    loading,
    isScanning,
    dataStatus,
    symbolParam,
    inspectedView,
    opportunities.length,
    autoWatchlist,
    activeOpportunity,
    handleSelectWatchlistEpisode,
  ]);

  useEffect(() => {
    const fetchAutoWatchlist = async () => {
      try {
        const res = await fetch("/api/watchlist/auto?days=7", { cache: "no-store" }).catch(() => null);
        if (!res || !res.ok) return;
        const payload = (await res.json()) as { watchlist?: SidebarWatchlistEpisode[] };
        if (Array.isArray(payload.watchlist)) {
          setAutoWatchlist(payload.watchlist);
        }
      } catch {}
    };
    const fetchUniverse = async () => {
      try {
        const res = await fetch("/api/screener/universe", { cache: "no-store" }).catch(() => null);
        if (!res || !res.ok) return;
        const payload = (await res.json()) as { tokens?: UniverseToken[] };
        if (Array.isArray(payload.tokens)) {
          setUniverseTokens(payload.tokens);
        }
      } catch {}
    };
    const timer = setTimeout(() => {
      void fetchScreener(false);
      void fetchAutoWatchlist();
      void fetchUniverse();
    }, 0);
    const interval = setInterval(() => {
      if (!awaitingScanCompletionRef.current && !fetchInFlightRef.current) {
        void fetchScreener(false);
        void fetchAutoWatchlist();
      }
    }, 60000);
    return () => {
      clearTimeout(timer);
      clearInterval(interval);
      if (pollTimerRef.current) {
        clearTimeout(pollTimerRef.current);
        pollTimerRef.current = null;
      }
      if (notificationTimerRef.current) {
        clearTimeout(notificationTimerRef.current);
        notificationTimerRef.current = null;
      }
    };
  }, [fetchScreener]);

  // Dynamically react if URL symbol changes or candidate was directly linked
  useEffect(() => {
    if (!symbolParam) return;
    const cleanTarget = symbolParam.toUpperCase();
    if (activeOpportunity?.symbol.toUpperCase() === cleanTarget) return;

    const existing = opportunities.find(
      (o) => o.symbol.toUpperCase() === cleanTarget,
    );
    if (existing) {
      const selectTimer = setTimeout(() => {
        handleSelectOpportunity(existing);
      }, 0);
      return () => clearTimeout(selectTimer);
    }

    let isMounted = true;
    const loadSymbol = async () => {
      try {
        const res = await fetch(`/api/chart/${cleanTarget}`).catch(() => null);
        if (res && res.ok && isMounted) {
          const chartData = (await res.json()) as ChartApiResponse;
          const chartCandles =
            chartData.candles && chartData.candles.length > 0
              ? chartData.candles
              : [];
          if (chartData.record_kind === "published_radar_candidate" && chartData.opportunity) {
            const oppWithCandles: Opportunity = {
              ...chartData.opportunity,
              candles: chartCandles,
            };
            setInspectedView(null);
            setOpportunities((prev) => {
              const targetBase = cleanTarget.replace(/USDT$/, "").replace(/USDC$/, "");
              const filtered = prev.filter((o) => {
                const oBase = (o.base_asset || o.symbol).replace(/USDT$/, "").replace(/USDC$/, "");
                return oBase.toUpperCase() !== targetBase;
              });
              return [oppWithCandles, ...filtered];
            });
            setActiveOpportunity(oppWithCandles);
          } else {
            // Keep unqualified chart inspections and watchlist views out of the qualified radar list
            const personalWv = await resolveUserWatchlistView(
              cleanTarget,
              accountScope.current,
              chartData.inspection?.current_price ?? chartData.watchlist_view?.current_price ?? null,
              chartData.inspection?.tick_size ?? chartData.watchlist_view?.tick_size,
            );
            if (personalWv?.account_ticket && !accountScope.current.isCurrent(personalWv.account_ticket)) return;
            const effectiveWv = personalWv ?? chartData.watchlist_view ?? null;
            setInspectedView({
              record_kind: effectiveWv ? "watchlist_view" : "chart_inspection",
              symbol: cleanTarget,
              candles: chartCandles,
              current_price: effectiveWv?.current_price ?? chartData.inspection?.current_price ?? null,
              tick_size: effectiveWv?.tick_size ?? chartData.inspection?.tick_size,
              note: chartData.inspection?.note,
              watchlist_view: effectiveWv,
            });
          }
          if (chartCandles.length > 0) {
            setActiveCandle(chartCandles[chartCandles.length - 1]);
          }
        }
      } catch (err) {
        console.warn("Error loading targeted symbol chart:", err);
      }
    };
    void loadSymbol();
    return () => {
      isMounted = false;
    };
  }, [symbolParam, opportunities, activeOpportunity, handleSelectOpportunity]);

  useEffect(() => {
    const handleTriggerRescan = (e: Event) => {
      e.preventDefault(); // Prevents default in BottomDock to indicate in-place handling
      const customEvt = e as CustomEvent<{ scope?: "core" | "full" }>;
      const scope = customEvt.detail?.scope || "core";
      void fetchScreener(true, scope);
    };
    const handleOperatorAuthRequired = (e: Event) => {
      const customEvt = e as CustomEvent<{ actionLabel?: string; retry?: () => void }>;
      pendingOperatorActionRef.current = customEvt.detail?.retry || null;
      setOperatorAuthActionLabel(customEvt.detail?.actionLabel || "privileged operator action");
      setOperatorAuthOpen(true);
    };
    window.addEventListener("atlas:trigger-rescan", handleTriggerRescan);
    window.addEventListener("atlas:operator-auth-required", handleOperatorAuthRequired);
    return () => {
      window.removeEventListener("atlas:trigger-rescan", handleTriggerRescan);
      window.removeEventListener("atlas:operator-auth-required", handleOperatorAuthRequired);
    };
  }, [fetchScreener]);

  // --------------------------------------------------------------------------
  // Real-time Multi-Ticker Streaming & Snapshot for ALL candidate & watchlist tokens
  // --------------------------------------------------------------------------
  const trackedSymbolsKey = useMemo(() => {
    const syms = new Set<string>();
    for (const o of opportunities) syms.add(o.symbol.toUpperCase());
    for (const w of autoWatchlist) syms.add(w.symbol.toUpperCase());
    return Array.from(syms).sort().join(",");
  }, [opportunities, autoWatchlist]);

  useEffect(() => {
    if (!trackedSymbolsKey) return;

    let isSubscribed = true;
    let ws: WebSocket | null = null;
    let pollTimer: NodeJS.Timeout | null = null;

    const symbols = trackedSymbolsKey.split(",");
    if (symbols.length === 0) return;

    // 1. Instant REST snapshot for all candidate symbols
    const fetchSnapshot = async () => {
      try {
        const res = await fetch(
          "https://fapi.binance.com/fapi/v1/ticker/bookTicker",
        ).catch(() => null);
        if (res && res.ok && isSubscribed) {
          const data = await res.json();
          if (Array.isArray(data)) {
            const symSet = new Set(symbols);
            const prices: Record<string, number> = {};
            const quotes: Record<string, { price: number; observed_at: string }> = {};
            data.forEach((item: { symbol: string; bidPrice: string; askPrice: string; time?: number }) => {
              const quote = bookQuote(item.bidPrice, item.askPrice, item.time, Date.now());
              if (symSet.has(item.symbol) && quote) {
                prices[item.symbol] = Number(quote.price);
                quotes[item.symbol] = { price: Number(quote.price), observed_at: quote.observed_at };
              }
            });
            setRadarQuotes(prev => {
              const next = { ...prev };
              for (const [symbol, quote] of Object.entries(quotes)) {
                if (!prev[symbol] || quote.observed_at >= prev[symbol].observed_at) next[symbol] = quote;
              }
              return next;
            });
            setLivePrices((prev) => ({ ...prev, ...prices }));
          }
        }
      } catch {
        // ignore network error
      }
    };

    fetchSnapshot();
    const snapshotRefreshTimer = setInterval(fetchSnapshot, 12000);

    // 2. Real-time Multi-ticker WebSocket stream (Binance combined stream)
    const streams = symbols.flatMap((s) => [`${s.toLowerCase()}@ticker`, `${s.toLowerCase()}@bookTicker`]).join("/");
    const wsUrl = `wss://fstream.binance.com/stream?streams=${streams}`;

    const startPollingFallback = () => {
      if (pollTimer) clearInterval(pollTimer);
      pollTimer = setInterval(fetchSnapshot, 3000);
    };

    try {
      ws = new WebSocket(wsUrl);

      ws.onmessage = (event) => {
        if (!isSubscribed) return;
        try {
          const payload = JSON.parse(event.data);
          const data = payload.data || payload;
          if (data?.e === "bookTicker" && typeof data.s === "string" && symbols.includes(data.s)) {
            const quote = bookEventQuote(data, Date.now());
            if (quote) {
              const sym = data.s;
              setRadarQuotes(prev => prev[sym] && prev[sym].observed_at > quote.observed_at
                ? prev : { ...prev, [sym]: { price: Number(quote.price), observed_at: quote.observed_at } });
            }
            return;
          }
          if (data && typeof data.s === "string" && data.c) {
            const sym = data.s.toUpperCase();
            const curPrice = parseFloat(data.c);
            if (!symbols.includes(sym) || !Number.isFinite(curPrice) || curPrice <= 0
              || typeof data.E !== "number" || Date.now() - data.E < -5000 || Date.now() - data.E > 60000) return;
            const pct = data.P !== undefined ? parseFloat(data.P) : undefined;

            if (data.b !== undefined && data.a !== undefined) {
              const tickerBookQuote = bookQuote(data.b, data.a, data.E, Date.now());
              if (tickerBookQuote) {
                setRadarQuotes((prev) =>
                  prev[sym] && prev[sym].observed_at > tickerBookQuote.observed_at
                    ? prev
                    : {
                        ...prev,
                        [sym]: {
                          price: Number(tickerBookQuote.price),
                          observed_at: tickerBookQuote.observed_at,
                        },
                      },
                );
              }
            }

            setLivePrices((prev) => {
              if (prev[sym] === curPrice) return prev;
              return { ...prev, [sym]: curPrice };
            });

            if (pct !== undefined && Number.isFinite(pct)) {
              setPriceChanges24h((prev) => {
                if (prev[sym] === pct) return prev;
                return { ...prev, [sym]: pct };
              });
            }
          }
        } catch {}
      };

      ws.onerror = () => {
        if (isSubscribed) startPollingFallback();
      };

      ws.onclose = () => {
        if (isSubscribed) startPollingFallback();
      };
    } catch {
      startPollingFallback();
    }

    return () => {
      isSubscribed = false;
      clearInterval(snapshotRefreshTimer);
      if (ws) {
        ws.onopen = null;
        ws.onmessage = null;
        ws.onerror = null;
        ws.onclose = null;
        ws.close();
      }
      if (pollTimer) clearInterval(pollTimer);
    };
  }, [trackedSymbolsKey]);

  const handleLevelsUpdated = useCallback(
    (levels: {
      pocPrice: number;
      vahPrice: number;
      valPrice: number;
      entryPrice: number;
      stopPrice: number;
      breakoutTrigger: number;
      targets: number[];
    }) => {
      if (!activeOpportunity) return;
      setAdaptedLevelsMap((prev) => {
        const existing = prev[activeOpportunity.symbol];
        if (
          existing &&
          existing.pocPrice === levels.pocPrice &&
          existing.vahPrice === levels.vahPrice &&
          existing.entryPrice === levels.entryPrice &&
          existing.stopPrice === levels.stopPrice &&
          existing.breakoutTrigger === levels.breakoutTrigger
        ) {
          return prev;
        }
        return {
          ...prev,
          [activeOpportunity.symbol]: levels,
        };
      });
    },
    [activeOpportunity],
  );

  // Memoize header data & derived metrics
  const headerMetrics = useMemo(() => {
    if (!activeOpportunity) return null;
    const currentPrice =
      livePrices[activeOpportunity.symbol] ??
      Number(activeOpportunity.current_price);
    const adapted = adaptedLevelsMap[activeOpportunity.symbol];
    const poc = adapted?.pocPrice ?? Number(activeOpportunity.poc_price || 0);
    const vah = adapted?.vahPrice ?? Number(activeOpportunity.vah_price || 0);

    const tick = Number(activeOpportunity.tick_size);
    const dec = (!isNaN(tick) && tick > 0)
      ? Math.max(2, -Math.floor(Math.log10(tick)))
      : currentPrice < 1 ? 4 : 2;

    let cleanFunding = "N/A";
    if (activeOpportunity.funding_rate_bps !== undefined && activeOpportunity.funding_rate_bps !== null) {
      const raw = String(activeOpportunity.funding_rate_bps).replace(/bps/gi, "").trim();
      cleanFunding = `${raw} bps`;
    }

    return {
      symbol: activeOpportunity.symbol,
      dec,
      formattedPrice: currentPrice.toFixed(dec),
      poc: poc.toFixed(dec),
      vah: vah.toFixed(dec),
      oiChange: activeOpportunity.oi_change_7d_pct || "N/A",
      isOiNegative: String(activeOpportunity.oi_change_7d_pct).includes("-"),
      fundingRate: cleanFunding,
      riskReward: Number(activeOpportunity.risk_reward_ratio || 0).toFixed(2),
      riskRewardTarget: activeOpportunity.risk_reward_target_index ?? 2,
    };
  }, [activeOpportunity, livePrices, adaptedLevelsMap]);

  const candleMetrics = useMemo(() => {
    if (!activeCandle) return null;
    const dec = headerMetrics?.dec ?? (activeCandle.close < 0.001 ? 7 : activeCandle.close < 0.01 ? 6 : activeCandle.close < 1 ? 4 : 2);
    const isBullish = activeCandle.close >= activeCandle.open;
    const volFormatted =
      activeCandle.volume !== undefined
        ? activeCandle.volume >= 1_000_000_000
          ? `${(activeCandle.volume / 1_000_000_000).toFixed(2)}B`
          : activeCandle.volume >= 1_000_000
            ? `${(activeCandle.volume / 1_000_000).toFixed(1)}M`
            : activeCandle.volume >= 1_000
              ? `${(activeCandle.volume / 1000).toFixed(0)}k`
              : `${activeCandle.volume.toFixed(0)}`
        : null;
    return {
      open: Number(activeCandle.open).toFixed(dec),
      high: Number(activeCandle.high).toFixed(dec),
      low: Number(activeCandle.low).toFixed(dec),
      close: Number(activeCandle.close).toFixed(dec),
      isBullish,
      volFormatted,
    };
  }, [activeCandle, headerMetrics?.dec]);

  const activeCandles = useMemo(() => {
    return activeOpportunity?.candles || [];
  }, [activeOpportunity?.candles]);

  const isFrozenFamilySetup =
    activeOpportunity?.setup_label === "BREAKDOWN_RETEST_SHORT" ||
    activeOpportunity?.setup_label === "RECOVERY_RECLAIM_LONG";

  const memoizedTargets = useMemo(() => {
    const adapted = isFrozenFamilySetup ? undefined : adaptedLevelsMap[activeOpportunity?.symbol || ""];
    const rawList =
      adapted?.targets && adapted.targets.length > 0
        ? adapted.targets
        : (activeOpportunity?.suggested_targets || []).map((t) => Number(t));
    const lockedEntry = Number(activeOpportunity?.radar_trigger_entry || 0);
    if (
      activeOpportunity?.radar_triggered_at &&
      Number.isFinite(lockedEntry) &&
      lockedEntry > 0
    ) {
      const isShortDir = activeOpportunity.direction?.toLowerCase() === "short";
      const ahead = rawList.filter((t) =>
        isShortDir ? t > 0 && t < lockedEntry : t > lockedEntry,
      );
      if (ahead.length > 0) return ahead;
    }
    return rawList;
  }, [activeOpportunity, isFrozenFamilySetup, adaptedLevelsMap]);

  const breakoutTriggerPrice = useMemo(() => {
    if (!activeOpportunity) return 0;
    const adapted = isFrozenFamilySetup ? undefined : adaptedLevelsMap[activeOpportunity.symbol];
    if (adapted?.breakoutTrigger) return adapted.breakoutTrigger;
    return Number(
      activeOpportunity.breakout_trigger ||
        Number(activeOpportunity.current_price || 0) *
          (activeOpportunity.direction === "short" ? 0.975 : 1.025),
    );
  }, [activeOpportunity, isFrozenFamilySetup, adaptedLevelsMap]);

  const entryPriceVal = useMemo(() => {
    if (!activeOpportunity) return 0;
    const lockedEntry = Number(activeOpportunity.radar_trigger_entry || 0);
    if (
      activeOpportunity.radar_triggered_at &&
      activeOpportunity.radar_trigger_basis === "retest" &&
      Number.isFinite(lockedEntry) &&
      lockedEntry > 0
    ) {
      return lockedEntry;
    }
    const adapted = isFrozenFamilySetup ? undefined : adaptedLevelsMap[activeOpportunity.symbol];
    if (adapted?.entryPrice) return adapted.entryPrice;
    return Number(
      activeOpportunity.suggested_entry ||
        activeOpportunity.pullback_entry ||
        0,
    );
  }, [activeOpportunity, isFrozenFamilySetup, adaptedLevelsMap]);

  const pocPriceVal = useMemo(() => {
    const publishedPoc = Number(activeOpportunity?.poc_price || 0);
    if (publishedPoc > 0) return publishedPoc;
    const adapted = adaptedLevelsMap[activeOpportunity?.symbol || ""];
    if (adapted?.pocPrice) return adapted.pocPrice;
    return 0;
  }, [
    activeOpportunity?.symbol,
    activeOpportunity?.poc_price,
    adaptedLevelsMap,
  ]);

  const vahPriceVal = useMemo(() => {
    const publishedVah = Number(activeOpportunity?.vah_price || 0);
    if (publishedVah > 0) return publishedVah;
    const adapted = adaptedLevelsMap[activeOpportunity?.symbol || ""];
    if (adapted?.vahPrice) return adapted.vahPrice;
    return 0;
  }, [
    activeOpportunity?.symbol,
    activeOpportunity?.vah_price,
    adaptedLevelsMap,
  ]);

  const valPriceVal = useMemo(() => {
    const publishedVal = Number(activeOpportunity?.val_price || 0);
    if (publishedVal > 0) return publishedVal;
    const adapted = adaptedLevelsMap[activeOpportunity?.symbol || ""];
    if (adapted?.valPrice) return adapted.valPrice;
    return 0;
  }, [
    activeOpportunity?.symbol,
    activeOpportunity?.val_price,
    adaptedLevelsMap,
  ]);

  const stopPriceVal = useMemo(() => {
    const lockedStop = Number(activeOpportunity?.radar_trigger_stop || 0);
    if (
      activeOpportunity?.radar_triggered_at &&
      Number.isFinite(lockedStop) &&
      lockedStop > 0
    ) {
      return lockedStop;
    }
    const adapted = isFrozenFamilySetup ? undefined : adaptedLevelsMap[activeOpportunity?.symbol || ""];
    if (adapted?.stopPrice) return adapted.stopPrice;
    return Number(activeOpportunity?.suggested_stop || 0);
  }, [
    activeOpportunity?.symbol,
    activeOpportunity?.suggested_stop,
    activeOpportunity?.radar_triggered_at,
    activeOpportunity?.radar_trigger_stop,
    isFrozenFamilySetup,
    adaptedLevelsMap,
  ]);

  const hasWatchlistFallback = !isWithheldStatus(dataStatus) && autoWatchlist.length > 0 && !symbolParam;

  if (!activeOpportunity && !inspectedView && !hasWatchlistFallback) {
    if ((loading || isScanning) && !isWithheldStatus(dataStatus)) {
      return (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            height: "100vh",
            background: "#080c14",
            color: "var(--text-primary)",
            padding: "24px",
            position: "relative",
            overflow: "hidden",
          }}
        >
          <div
            style={{
              position: "relative",
              width: "88px",
              height: "88px",
              borderRadius: "50%",
              border: "1px solid rgba(244, 157, 13, 0.4)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              marginBottom: "24px",
            }}
          >
            <div
              style={{
                position: "absolute",
                width: "56px",
                height: "56px",
                borderRadius: "50%",
                border: "1px dashed rgba(244, 157, 13, 0.3)",
              }}
            />
            <Zap size={26} color="#f49d0d" />
          </div>

          <div style={{ textAlign: "center", zIndex: 1 }}>
            <div
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: "6px",
                padding: "4px 12px",
                borderRadius: "20px",
                background: "rgba(244, 157, 13, 0.1)",
                border: "1px solid rgba(244, 157, 13, 0.3)",
                fontSize: "11px",
                fontWeight: 800,
                letterSpacing: "0.08em",
                color: "#f49d0d",
                marginBottom: "12px",
              }}
            >
              ATLAS RADAR ENGINE
            </div>

            <h2
              style={{ fontSize: "18px", fontWeight: 800, margin: "0 0 8px 0" }}
            >
              Synchronizing Live USD-M Candidates
            </h2>

            <p
              style={{
                color: "var(--text-muted)",
                fontSize: "13px",
                fontFamily: "var(--font-mono)",
                maxWidth: "460px",
                lineHeight: 1.5,
                margin: 0,
              }}
            >
              Auditing 490 liquid contracts across 14 institutional
              microstructure gates (Volume Profile POC, Volatility Squeeze, MTF
              RSI & Smart Money Orderflow)...
            </p>
          </div>
        </div>
      );
    }

    const isDegradedState = isWithheldStatus(dataStatus);
    const emptyHeading =
      dataStatus === "stale"
        ? "Cached Qualifications Expired (Stale)"
        : dataStatus === "degraded_zero_usable_inputs"
          ? "Market Inputs Unavailable — Qualifications Withheld"
          : dataStatus === "startup_revalidation_required"
            ? "Saved Qualifications Await Live Re-verification"
          : dataStatus === "scan_failed"
            ? "Live Scan Failed — Qualifications Withheld"
            : "No Active Pre-Breakout Setups Qualified";
    const emptyMessage = isDegradedState
      ? statusDetail ||
        "Prior qualifications are withheld until a fresh live scan verifies current market structure."
      : null;

    return (
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          height: "100vh",
          background: "#080c14",
          color: "var(--text-primary)",
          padding: "24px",
        }}
      >
        <AlertCircle
          size={38}
          color={isDegradedState ? "#ef4444" : "#f49d0d"}
          style={{ marginBottom: "16px" }}
        />
        <h2 style={{ fontSize: "18px", fontWeight: 800, margin: isDegradedState ? "0 0 8px 0" : "0" }}>
          {emptyHeading}
        </h2>
        {emptyMessage && (
          <p
            style={{
              color: "var(--text-muted)",
              fontSize: "13px",
              fontFamily: "var(--font-mono)",
              maxWidth: "480px",
              textAlign: "center",
              marginBottom: "20px",
            }}
          >
            {emptyMessage}
          </p>
        )}
        {isDegradedState && (
          <button
            onClick={() => fetchScreener(true)}
            disabled={isScanning}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "8px",
              background: "#f49d0d",
              color: "#000",
              fontWeight: 700,
              fontSize: "13px",
              padding: "8px 18px",
              borderRadius: "6px",
              border: "none",
              cursor: isScanning ? "wait" : "pointer",
              opacity: isScanning ? 0.7 : 1,
            }}
          >
            <RefreshCw size={14} className={isScanning ? "animate-spin" : ""} />
            {isScanning
              ? "Deep Scanning Futures Universe..."
              : "Refresh Screener Now"}
          </button>
        )}
        <OperatorAuthModal
          open={operatorAuthOpen}
          actionLabel={operatorAuthActionLabel}
          onClose={() => {
            setOperatorAuthOpen(false);
            pendingOperatorActionRef.current = null;
          }}
          onAuthenticated={() => {
            setOperatorAuthOpen(false);
            const retry = pendingOperatorActionRef.current;
            pendingOperatorActionRef.current = null;
            if (retry) retry();
          }}
        />
      </div>
    );
  }

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100vh",
        paddingBottom: "75px",
      }}
    >
      {/* Top Navbar & Token Header */}
      <header
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          height: "52px",
          minHeight: "52px",
          maxHeight: "52px",
          flexShrink: 0,
          overflow: "hidden",
          whiteSpace: "nowrap",
          gap: "12px",
          padding: "0 20px",
          background: "rgba(11, 15, 25, 0.98)",
          borderBottom: "1px solid var(--border-subtle)",
          zIndex: 30,
        }}
      >
        {/* Left: Brand & Active Token */}
        <div style={{ display: "flex", alignItems: "center", gap: "14px", minWidth: 0, overflow: "hidden" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <span
              style={{
                fontWeight: 900,
                fontSize: "15px",
                letterSpacing: "0.06em",
                color: "var(--text-primary)",
              }}
            >
              ALFA TERMINAL
            </span>
          </div>

          <div
            className="portal-header-desktop-metrics"
            style={{
              height: "20px",
              width: "1px",
              background: "var(--border-subtle)",
            }}
          />

          {/* Active Symbol, Live Price & OHLCV in Header */}
          {headerMetrics && (
            <div
              className="portal-header-desktop-metrics"
              style={{ display: "flex", alignItems: "center", gap: "10px" }}
            >
              <span
                style={{
                  fontSize: "16px",
                  fontWeight: 800,
                  color: "var(--text-primary)",
                }}
              >
                {headerMetrics.symbol}
              </span>
              <span
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: "14px",
                  fontWeight: 700,
                  color: "#f49d0d",
                }}
              >
                ${headerMetrics.formattedPrice}
              </span>

              {/* OHLC Bar Metrics in Top Header */}
              {candleMetrics && (
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "9px",
                    fontSize: "11px",
                    fontFamily: "var(--font-mono)",
                    fontVariantNumeric: "tabular-nums",
                    whiteSpace: "nowrap",
                    flexShrink: 0,
                    background: "rgba(255, 255, 255, 0.04)",
                    border: "1px solid rgba(255, 255, 255, 0.08)",
                    borderRadius: "6px",
                    padding: "3px 9px",
                  }}
                >
                  <span>
                    <span
                      style={{ color: "var(--text-muted)", marginRight: "3px" }}
                    >
                      O
                    </span>
                    <strong
                      style={{ color: "var(--text-primary)", fontWeight: 600 }}
                    >
                      {candleMetrics.open}
                    </strong>
                  </span>
                  <span>
                    <span
                      style={{ color: "var(--text-muted)", marginRight: "3px" }}
                    >
                      H
                    </span>
                    <strong
                      style={{ color: "var(--text-primary)", fontWeight: 600 }}
                    >
                      {candleMetrics.high}
                    </strong>
                  </span>
                  <span>
                    <span
                      style={{ color: "var(--text-muted)", marginRight: "3px" }}
                    >
                      L
                    </span>
                    <strong
                      style={{ color: "var(--text-primary)", fontWeight: 600 }}
                    >
                      {candleMetrics.low}
                    </strong>
                  </span>
                  <span>
                    <span
                      style={{ color: "var(--text-muted)", marginRight: "3px" }}
                    >
                      C
                    </span>
                    <strong
                      style={{
                        color: candleMetrics.isBullish ? "#10b981" : "#ef4444",
                        fontWeight: 700,
                      }}
                    >
                      {candleMetrics.close}
                    </strong>
                  </span>
                  {candleMetrics.volFormatted && (
                    <span>
                      <span
                        style={{
                          color: "var(--text-muted)",
                          marginRight: "3px",
                        }}
                      >
                        Vol
                      </span>
                      <strong
                        style={{
                          color: "var(--text-primary)",
                          fontWeight: 600,
                        }}
                      >
                        {candleMetrics.volFormatted}
                      </strong>
                    </span>
                  )}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Right: Key Microstructure Metrics Bar */}
        <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
          {headerMetrics && (
            <div
              className="portal-header-desktop-metrics"
              style={{
                display: "flex",
                alignItems: "center",
                gap: "18px",
                fontSize: "11px",
                fontFamily: "var(--font-mono)",
              }}
            >
              <div>
                <span style={{ color: "var(--text-muted)" }}>30D POC: </span>
                <strong style={{ color: "#f49d0d" }}>${headerMetrics.poc}</strong>
              </div>
              <div>
                <span style={{ color: "var(--text-muted)" }}>VAH: </span>
                <strong style={{ color: "#ffffff" }}>${headerMetrics.vah}</strong>
              </div>
              <div>
                <span style={{ color: "var(--text-muted)" }}>7D OI: </span>
                <strong
                  style={{
                    color: headerMetrics.isOiNegative
                      ? "var(--accent-crimson)"
                      : "var(--accent-emerald)",
                  }}
                >
                  {headerMetrics.oiChange}
                </strong>
              </div>
              <div>
                <span style={{ color: "var(--text-muted)" }}>Funding: </span>
                <strong style={{ color: "var(--text-primary)" }}>
                  {headerMetrics.fundingRate}
                </strong>
              </div>
              <div>
                <span style={{ color: "var(--text-muted)" }}>R:R (T{headerMetrics.riskRewardTarget}): </span>
                <strong style={{ color: "var(--accent-emerald)" }}>
                  {headerMetrics.riskReward}:1
                </strong>
              </div>
              <div
                title={`Active Policy: ${policyVersion}`}
                style={{
                  background: "rgba(244, 157, 13, 0.12)",
                  color: "#f49d0d",
                  border: "1px solid rgba(244, 157, 13, 0.3)",
                  borderRadius: "6px",
                  padding: "2px 8px",
                  fontSize: "10px",
                  fontWeight: 800,
                  display: "flex",
                  alignItems: "center",
                  gap: "4px",
                  letterSpacing: "0.04em",
                }}
              >
                <Zap size={11} />
                <span>{policyVersion?.startsWith("alfa-v3.3") ? "ALFA v3.5" : "ALFA v3.2"}</span>
              </div>
            </div>
          )}

          {isScanning && (
            <div
              style={{
                background: "rgba(244, 157, 13, 0.16)",
                color: "#f49d0d",
                border: "1px solid rgba(244, 157, 13, 0.45)",
                borderRadius: "6px",
                padding: "2px 8px",
                fontSize: "10px",
                fontWeight: 800,
                display: "flex",
                alignItems: "center",
                gap: "5px",
                letterSpacing: "0.04em",
              }}
            >
              <RefreshCw size={10} className="animate-spin" />{" "}
              {scanSource === "scheduled"
                ? "REFRESHING HOURLY SCAN..."
                : "SCANNING LIVE UNIVERSE..."}
            </div>
          )}

          {!isScanning && scanNotification && (
            <div
              style={{
                background: "rgba(16, 185, 129, 0.14)",
                color: "#10b981",
                border: "1px solid rgba(16, 185, 129, 0.35)",
                borderRadius: "6px",
                padding: "2px 8px",
                fontSize: "10px",
                fontWeight: 800,
                display: "flex",
                alignItems: "center",
                gap: "4px",
              }}
            >
              {scanNotification}
            </div>
          )}
        </div>
      </header>

      {/* Main 3-Column Portal Body */}
      <main
        style={{
          display: "flex",
          flex: 1,
          overflow: "hidden",
          position: "relative",
        }}
      >
        {/* Column 1: Collapsible Candidate Sidebar */}
        <div
          className="portal-sidebar-column"
          style={{
            width: showLeftSidebar ? "320px" : "0px",
            minWidth: showLeftSidebar ? "320px" : "0px",
            overflow: "hidden",
            transition:
              "width 0.22s cubic-bezier(0.4, 0, 0.2, 1), min-width 0.22s cubic-bezier(0.4, 0, 0.2, 1)",
            height: "100%",
            flexShrink: 0,
            visibility: showLeftSidebar ? "visible" : "hidden",
          }}
        >
          <div className="portal-sidebar-inner" style={{ width: "320px", height: "100%" }}>
            <CandidateSidebar
              opportunities={opportunities}
              watchlistEpisodes={autoWatchlist}
              universeTokens={universeTokens}
              activeSymbol={inspectedView ? inspectedView.symbol : activeOpportunity ? activeOpportunity.symbol : ""}
              onSelect={handleSelectOpportunity}
              onSelectWatchlist={handleSelectWatchlistEpisode}
              onInspectSymbol={handleInspectUniverseSymbol}
              livePrices={livePrices}
              priceChanges24h={priceChanges24h}
            />
          </div>
        </div>

        {/* Column 2: TradingView Chart Canvas */}
        {(inspectedView || activeOpportunity) && (
          <section
            className="portal-chart-column"
            style={{
              flex: 1,
              height: "100%",
              minWidth: 0,
              minHeight: 0,
              overflow: "hidden",
              background: "#090d16",
              display: "flex",
              flexDirection: "column",
              position: "relative",
            }}
          >
            {inspectedView ? (
              <TradingChart
                key={`inspect-${inspectedView.symbol}`}
                symbol={inspectedView.symbol}
                direction="long"
                preservePublishedLevels={true}
                tickSize={Number(inspectedView.tick_size) || undefined}
                candles={inspectedView.candles}
                pocPrice={Number(inspectedView.watchlist_view?.support_price || 0)}
                vahPrice={Number(inspectedView.watchlist_view?.trigger_price || 0)}
                valPrice={Number(inspectedView.watchlist_view?.invalidation_price || 0)}
                entryPrice={Number(inspectedView.watchlist_view?.support_price || 0)}
                breakoutTrigger={Number(inspectedView.watchlist_view?.trigger_price || 0)}
                stopPrice={Number(inspectedView.watchlist_view?.invalidation_price || 0)}
                targets={
                  inspectedView.watchlist_view
                    ? [
                        Number(inspectedView.watchlist_view.target_1 || 0),
                        Number(inspectedView.watchlist_view.target_2 || 0),
                      ].filter((v) => v > 0)
                    : []
                }
                showLeftSidebar={showLeftSidebar}
                onToggleLeftSidebar={handleToggleLeftSidebar}
                showRightPanel={showRightPanel}
                onToggleRightPanel={handleToggleRightPanel}
                showLevels={inspectedView.record_kind === "watchlist_view" ? showLevels : false}
                onToggleLevels={handleToggleLevels}
                showTargets={inspectedView.record_kind === "watchlist_view" ? showTargets : false}
                onToggleTargets={handleToggleTargets}
                showVolume={showVolume}
                onToggleVolume={handleToggleVolume}
                candleInterval={candleInterval}
                onChangeInterval={handleChangeInterval}
                onCandleChange={handleCandleChange}
                onLiveTick={handleLiveTick}
              />
            ) : (
              activeOpportunity && (
                <TradingChart
                  key={activeOpportunity.symbol}
                  symbol={activeOpportunity.symbol}
                  direction={activeOpportunity.direction}
                  preservePublishedLevels={true}
                  isTriggered={Boolean(activeOpportunity.radar_triggered_at)}
                  triggerBasis={activeOpportunity.radar_trigger_basis}
                  tickSize={Number(activeOpportunity.tick_size) || undefined}
                  candles={activeCandles}
                  pocPrice={pocPriceVal}
                  vahPrice={vahPriceVal}
                  valPrice={valPriceVal}
                  entryPrice={entryPriceVal}
                  breakoutTrigger={breakoutTriggerPrice}
                  stopPrice={stopPriceVal}
                  targets={memoizedTargets}
                  showLeftSidebar={showLeftSidebar}
                  onToggleLeftSidebar={handleToggleLeftSidebar}
                  showRightPanel={showRightPanel}
                  onToggleRightPanel={handleToggleRightPanel}
                  showLevels={showLevels}
                  onToggleLevels={handleToggleLevels}
                  showTargets={showTargets}
                  onToggleTargets={handleToggleTargets}
                  showVolume={showVolume}
                  onToggleVolume={handleToggleVolume}
                  candleInterval={candleInterval}
                  onChangeInterval={handleChangeInterval}
                  onCandleChange={handleCandleChange}
                  onLiveTick={handleLiveTick}
                  onLevelsUpdated={handleLevelsUpdated}
                />
              )
            )}
          </section>
        )}

        {/* Column 3: Collapsible Institutional Verification Panel (or Non-Qualified Inspection Card) */}
        {(inspectedView || activeOpportunity) && (
          <div
            className="portal-verification-column"
            style={{
              width: showRightPanel ? "360px" : "0px",
              minWidth: showRightPanel ? "360px" : "0px",
              overflow: "hidden",
              transition:
                "width 0.22s cubic-bezier(0.4, 0, 0.2, 1), min-width 0.22s cubic-bezier(0.4, 0, 0.2, 1)",
              height: "100%",
              flexShrink: 0,
              visibility: showRightPanel ? "visible" : "hidden",
            }}
          >
            <div style={{ width: "360px", height: "100%" }}>
              {inspectedView ? (
                <aside
                  style={{
                    width: "360px",
                    height: "100%",
                    overflowY: "auto",
                    padding: "16px",
                    background: "rgba(10, 14, 24, 0.98)",
                    borderLeft: "1px solid var(--border-subtle)",
                    display: "flex",
                    flexDirection: "column",
                    gap: "12px",
                  }}
                >
                  <div
                    style={{
                      padding: "10px 12px",
                      borderRadius: "8px",
                      background: "rgba(244, 157, 13, 0.1)",
                      border: "1px solid rgba(244, 157, 13, 0.35)",
                      color: "#f49d0d",
                      fontSize: "11px",
                      fontWeight: 800,
                      letterSpacing: "0.05em",
                    }}
                  >
                    {inspectedView.record_kind === "watchlist_view"
                      ? "SAVED WATCHLIST VIEW — NOT A PUBLISHED RADAR SIGNAL"
                      : "CHART INSPECTION ONLY — NOT QUALIFIED"}
                  </div>
                  <div style={{ fontSize: "18px", fontWeight: 800, color: "var(--text-primary)" }}>
                    {inspectedView.symbol}
                  </div>
                  <p style={{ margin: 0, fontSize: "12px", color: "var(--text-muted)", lineHeight: 1.5 }}>
                    {inspectedView.record_kind === "watchlist_view"
                      ? inspectedView.watchlist_view?.thesis || "Saved watchlist item displayed with raw Binance USD-M candles. Live audit required before qualification."
                      : inspectedView.note || "Direct Binance USD-M futures chart inspection. No screener score, POC, win rate, or gate pass is synthesized."}
                  </p>
                  {inspectedView.watchlist_view?.checklist && inspectedView.watchlist_view.checklist.length > 0 && (
                    <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                      <div style={{ fontSize: "11px", fontWeight: 700, color: "var(--text-secondary)" }}>
                        WATCHLIST AUDIT STATUS ({inspectedView.watchlist_view.audit_verified ? "AUDITED" : "UNVERIFIED"})
                      </div>
                      {inspectedView.watchlist_view.checklist.map((item, idx) => (
                        <div
                          key={idx}
                          style={{
                            padding: "8px 10px",
                            borderRadius: "6px",
                            background: "rgba(255,255,255,0.03)",
                            border: "1px solid var(--border-subtle)",
                            fontSize: "11px",
                          }}
                        >
                          <div style={{ fontWeight: 700, color: item.passed ? "var(--accent-emerald)" : "#f49d0d" }}>
                            {item.title} ({item.status || (item.passed ? "passed" : "unknown")})
                          </div>
                          <div style={{ color: "var(--text-muted)", marginTop: "2px" }}>{item.detail}</div>
                        </div>
                      ))}
                    </div>
                  )}
                  {activeOpportunity && (
                    <button
                      type="button"
                      onClick={() => setInspectedView(null)}
                      style={{
                        marginTop: "4px",
                        padding: "8px 12px",
                        borderRadius: "6px",
                        border: "1px solid var(--border-subtle)",
                        background: "rgba(255,255,255,0.06)",
                        color: "var(--text-primary)",
                        fontSize: "12px",
                        fontWeight: 700,
                        cursor: "pointer",
                      }}
                    >
                      Return to Active Radar Candidate ({activeOpportunity.symbol})
                    </button>
                  )}
                </aside>
              ) : (
                activeOpportunity && (
                  <VerificationPanel
                    opportunity={activeOpportunity}
                    adaptedLevels={adaptedLevelsMap[activeOpportunity.symbol]}
                    livePrice={livePrices[activeOpportunity.symbol]}
                    radarQuote={radarQuotes[activeOpportunity.symbol]}
                  />
                )
              )}
            </div>
          </div>
        )}
      </main>
      <OperatorAuthModal
        open={operatorAuthOpen}
        actionLabel={operatorAuthActionLabel}
        onClose={() => {
          setOperatorAuthOpen(false);
          pendingOperatorActionRef.current = null;
        }}
        onAuthenticated={() => {
          setOperatorAuthOpen(false);
          const retry = pendingOperatorActionRef.current;
          pendingOperatorActionRef.current = null;
          if (retry) retry();
        }}
      />
    </div>
  );
}

export default function RadarPage() {
  return (
    <Suspense fallback={<ChartSkeleton />}>
      <RadarPageContent />
    </Suspense>
  );
}
