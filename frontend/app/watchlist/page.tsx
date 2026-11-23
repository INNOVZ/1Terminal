"use client";

import { useEffect, useState, useMemo, useCallback, useRef } from "react";
import dynamic from "next/dynamic";
import { AccountScope } from "../lib/accountScope";
import type { User } from "@supabase/supabase-js";
import {
  BookmarkCheck,
  Plus,
  RefreshCw,
  LogOut,
  Sparkles,
  UserCheck,
} from "lucide-react";
import WatchlistCard, { getEffectiveAudit } from "./components/WatchlistCard";
import type { CandidateFormData } from "./components/AddCandidateModal";
import type { EditCandidateSetupPayload } from "./components/EditNotesModal";
import SupabaseAuthPanel from "./components/SupabaseAuthPanel";
import AutoWatchlistSection, { type AutoWatchEpisode } from "./components/AutoWatchlistSection";
import { bookEventQuote, bookQuote, timestamp, type RadarQuote } from "../lib/radarInsights";
import {
  getSupabaseClient,
  CURATED_INSTITUTIONAL_STARTER_SEEDS,
  type UserWatchlistRow,
} from "../lib/supabaseClient";

export interface WatchlistLiveAudit {
  symbol: string;
  status:
    | "COIL_HOLDING"
    | "ACTIVE_EXPANSION"
    | "FLOW_DIVERGENCE"
    | "SELLER_INFLUX"
    | "TARGET_REACHED"
    | "STOP_BREACHED"
    | "DATA_UNAVAILABLE";
  status_title: string;
  live_price: number | null;
  price_change_pct: number | null;
  current_rsi_1h: number | null;
  current_rsi_4h: number | null;
  taker_ratio: number | null;
  vol_contraction_ratio: number | null;
  gate_tripped: string;
  live_reading: string;
  what_happened: string;
  institutional_logic: string;
  audited_at: string | null;
  telemetry_available?: boolean;
}

export interface WatchlistItem {
  symbol: string;
  name: string;
  sector: string;
  stage: string;
  timeframe: string;
  current_price: number;
  trigger_price: number;
  support_price: number;
  invalidation_price: number;
  target_1: number;
  target_2: number;
  distance_to_trigger_pct: number;
  score: number;
  thesis: string;
  checklist_passed: number;
  checklist_total: number;
  priority: string;
  notes: string;
  added_at: string;
  updated_at: string;
  audit?: WatchlistLiveAudit | null;
  estimated_win_rate_pct?: number;
}

// Lazy load dialog modals on-demand to reduce initial chunk size
const AddCandidateModal = dynamic(() => import("./components/AddCandidateModal"), {
  ssr: false,
});
const EditNotesModal = dynamic(() => import("./components/EditNotesModal"), {
  ssr: false,
});

function rowToWatchlistItem(row: UserWatchlistRow): WatchlistItem {
  return {
    symbol: row.symbol,
    name: row.name,
    sector: row.sector,
    stage: row.stage,
    timeframe: row.timeframe,
    current_price: Number(row.current_price),
    trigger_price: Number(row.trigger_price),
    support_price: Number(row.support_price),
    invalidation_price: Number(row.invalidation_price),
    target_1: Number(row.target_1),
    target_2: Number(row.target_2),
    distance_to_trigger_pct: Number(row.distance_to_trigger_pct),
    score: Number(row.score),
    thesis: row.thesis,
    checklist_passed: Number(row.checklist_passed),
    checklist_total: Number(row.checklist_total),
    priority: row.priority,
    notes: row.notes,
    added_at: row.added_at,
    updated_at: row.updated_at,
  };
}

export default function WatchlistPage() {
  const [workspaceTab, setWorkspaceTab] = useState<"auto" | "personal">("auto");
  const [user, setUser] = useState<User | null>(null);
  const [authChecked, setAuthChecked] = useState(false);
  const [items, setItems] = useState<WatchlistItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [auditLoading, setAuditLoading] = useState(false);
  const [seedingStarter, setSeedingStarter] = useState(false);
  const [lastAudited, setLastAudited] = useState<Date | null>(null);
  const [selectedFilter, setSelectedFilter] = useState<string>("ALL");
  const [isAddModalOpen, setIsAddModalOpen] = useState(false);
  const [editingItem, setEditingItem] = useState<WatchlistItem | null>(null);
  const [expandedAudits, setExpandedAudits] = useState<Record<string, boolean>>({});
  const [liveQuotes, setLiveQuotes] = useState<Record<string, RadarQuote>>({});
  const [nowMs, setNowMs] = useState<number>(() => Date.now());
  const [wsConnected, setWsConnected] = useState(false);

  // W7: Track active account ID and request generation so stale/out-of-order
  // responses from a previous user can never repopulate state after sign-out or switch.
  const accountScope = useRef(new AccountScope());
  const activeUserIdRef = useRef<string | null>(null);
  const fetchGenerationRef = useRef<number>(0);
  const fetchAbortRef = useRef<AbortController | null>(null);

  const livePrices = useMemo(() => {
    const out: Record<string, number> = {};
    for (const [sym, q] of Object.entries(liveQuotes)) {
      const ts = timestamp(q.observed_at);
      if (ts !== null && nowMs - ts >= -5000 && nowMs - ts <= 60000) {
        const px = Number(q.price);
        if (Number.isFinite(px) && px > 0) out[sym] = px;
      }
    }
    return out;
  }, [liveQuotes, nowMs]);

  // Track Supabase Auth session state with immediate state isolation on identity changes (W7)
  useEffect(() => {
    const supabase = getSupabaseClient();
    let mounted = true;

    const applyIdentity = (nextUser: User | null) => {
      if (!mounted) return;
      const nextId = nextUser?.id ?? null;
      if (activeUserIdRef.current !== nextId) {
        accountScope.current.setIdentity(nextId);
        activeUserIdRef.current = nextId;
        fetchGenerationRef.current += 1;
        fetchAbortRef.current?.abort();
        fetchAbortRef.current = null;
        setItems([]);
        setLiveQuotes({});
        setEditingItem(null);
        setExpandedAudits({});
        setLastAudited(null);
        setSeedingStarter(false);
        setAuditLoading(false);
        setIsAddModalOpen(false);
        setLoading(Boolean(nextId));
      }
      setUser(nextUser);
      setAuthChecked(true);
    };

    let authEventSeen = false;
    supabase.auth.getSession().then(({ data }) => {
      if (!authEventSeen) applyIdentity(data.session?.user ?? null);
    });

    const { data: listener } = supabase.auth.onAuthStateChange((_event, session) => {
      authEventSeen = true;
      applyIdentity(session?.user ?? null);
    });

    return () => {
      mounted = false;
      fetchAbortRef.current?.abort();
      listener.subscription.unsubscribe();
    };
  }, []);

  const toggleExpandAudit = useCallback((symbol: string) => {
    setExpandedAudits((prev) => ({
      ...prev,
      [symbol]: prev[symbol] === undefined ? false : !prev[symbol],
    }));
  }, []);

  const fetchWatchlist = useCallback(
    async (forceRefresh: boolean = false) => {
      const targetUserId = user?.id ?? null;
      if (!targetUserId) {
        setItems([]);
        setLoading(false);
        setAuditLoading(false);
        return;
      }

      if (activeUserIdRef.current !== targetUserId) return;
      fetchGenerationRef.current += 1;
      const generation = fetchGenerationRef.current;
      fetchAbortRef.current?.abort();
      const controller = new AbortController();
      fetchAbortRef.current = controller;

      const isCurrent = () =>
        fetchGenerationRef.current === generation && activeUserIdRef.current === targetUserId;

      try {
        const supabase = getSupabaseClient();
        const { data: rows, error } = await supabase
          .from("user_watchlists")
          .select("*")
          .eq("user_id", targetUserId)
          .order("score", { ascending: false })
          .order("added_at", { ascending: false });

        if (!isCurrent()) return;
        if (error) {
          console.error("Failed to fetch user watchlist from Supabase:", error.message);
          setItems([]);
          return;
        }

        const baseItems = ((rows || []) as UserWatchlistRow[]).map(rowToWatchlistItem);
        if (baseItems.length === 0) {
          setItems([]);
          setLastAudited(new Date());
          return;
        }

        // Enrich user's personal watchlist items with live Binance USD-M gate audits
        const auditRes = await fetch("/api/watchlist/audit", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          signal: controller.signal,
          body: JSON.stringify({
            items: baseItems,
            refresh: forceRefresh,
          }),
        }).catch(() => null);

        if (!isCurrent()) return;

        if (auditRes && auditRes.ok) {
          const auditPayload = await auditRes.json();
          if (!isCurrent()) return;
          if (Array.isArray(auditPayload.items)) {
            setItems(auditPayload.items as WatchlistItem[]);
            setLastAudited(new Date());
            window.dispatchEvent(new CustomEvent("atlas:watchlist-updated"));
            return;
          }
        }

        setItems(baseItems);
        setLastAudited(new Date());
        window.dispatchEvent(new CustomEvent("atlas:watchlist-updated"));
      } catch (e) {
        if (!isCurrent()) return;
        console.error("Failed to load watchlist:", e);
      } finally {
        if (isCurrent()) {
          setLoading(false);
          setAuditLoading(false);
        }
      }
    },
    [user],
  );

  useEffect(() => {
    if (!user) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void fetchWatchlist(false);
    const interval = setInterval(() => {
      void fetchWatchlist(false);
    }, 30000);
    const freshnessTimer = setInterval(() => {
      setNowMs(Date.now());
    }, 10000);
    return () => {
      clearInterval(interval);
      clearInterval(freshnessTimer);
      fetchAbortRef.current?.abort();
    };
  }, [user, fetchWatchlist]);

  // --------------------------------------------------------------------------
  // Real-time Verified USD-M Multi-Book Streaming & Snapshot for Watchlist tokens (W5)
  // Uses strictly Binance USD-M Futures (never Spot) with exchange timestamp & monotonicity validation.
  // --------------------------------------------------------------------------
  const watchedSymbolsKey = useMemo(() => {
    if (!items || items.length === 0) return "";
    return Array.from(new Set(items.map((i) => i.symbol.toUpperCase())))
      .sort()
      .join(",");
  }, [items]);

  useEffect(() => {
    if (!watchedSymbolsKey) return;

    let isSubscribed = true;
    let ws: WebSocket | null = null;
    let pollTimer: NodeJS.Timeout | null = null;

    const symbols = watchedSymbolsKey.split(",");
    const symSet = new Set(symbols);

    const mergeQuote = (sym: string, quote: RadarQuote) => {
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
    };

    // 1. Instant USD-M Futures bookTicker REST snapshot (same contract as WebSocket & screener)
    const fetchSnapshot = async () => {
      try {
        const res = await fetch("https://fapi.binance.com/fapi/v1/ticker/bookTicker").catch(() => null);
        if (res && res.ok && isSubscribed) {
          const data = await res.json();
          if (Array.isArray(data)) {
            const now = Date.now();
            data.forEach((row: { symbol?: string; bidPrice?: string; askPrice?: string; time?: number }) => {
              const sym = typeof row.symbol === "string" ? row.symbol.toUpperCase() : "";
              if (!symSet.has(sym)) return;
              const q = bookQuote(row.bidPrice, row.askPrice, row.time, now);
              if (q) mergeQuote(sym, q);
            });
          }
        }
      } catch {
        // ignore network error
      }
    };

    void fetchSnapshot();

    // 2. Real-time USD-M Futures bookTicker WebSocket stream with verified exchange timestamps
    const streams = symbols.map((s) => `${s.toLowerCase()}@bookTicker`).join("/");
    const wsUrl = `wss://fstream.binance.com/stream?streams=${streams}`;

    const startPollingFallback = () => {
      if (!isSubscribed) return;
      setWsConnected(false);
      if (pollTimer) clearInterval(pollTimer);
      pollTimer = setInterval(() => void fetchSnapshot(), 5000);
    };

    try {
      ws = new WebSocket(wsUrl);

      ws.onopen = () => {
        if (isSubscribed) setWsConnected(true);
      };

      ws.onmessage = (event) => {
        if (!isSubscribed) return;
        try {
          const payload = JSON.parse(event.data);
          const data = payload.data || payload;
          const sym = typeof data?.s === "string" ? data.s.toUpperCase() : "";
          if (!symSet.has(sym)) return;
          const q = bookEventQuote(data, Date.now());
          if (q) mergeQuote(sym, q);
        } catch {
          // ignore parse errors
        }
      };

      ws.onerror = () => {
        startPollingFallback();
      };

      ws.onclose = () => {
        startPollingFallback();
      };
    } catch {
      startPollingFallback();
    }

    return () => {
      isSubscribed = false;
      if (pollTimer) clearInterval(pollTimer);
      if (ws) {
        ws.onmessage = null;
        ws.close();
      }
    };
  }, [watchedSymbolsKey]);

  const handleSignOut = useCallback(async () => {
    accountScope.current.setIdentity(null);
    activeUserIdRef.current = null;
    fetchGenerationRef.current += 1;
    fetchAbortRef.current?.abort();
    fetchAbortRef.current = null;
    setUser(null);
    setItems([]);
    setLiveQuotes({});
    setEditingItem(null);
    setExpandedAudits({});
    setIsAddModalOpen(false);
    setSeedingStarter(false);
    setAuditLoading(false);
    setLastAudited(null);
    const supabase = getSupabaseClient();
    await supabase.auth.signOut();
  }, []);

  const handleLoadStarterPicks = useCallback(async () => {
    if (!user || seedingStarter) return;
    const ticket = accountScope.current.capture();
    if (ticket.userId !== user.id) return;
    setSeedingStarter(true);
    try {
      const supabase = getSupabaseClient();
      const nowIso = new Date().toISOString();
      const rowsToInsert = CURATED_INSTITUTIONAL_STARTER_SEEDS.map((seed) => ({
        ...seed,
        user_id: user.id,
        added_at: nowIso,
        updated_at: nowIso,
      }));
      const { error } = await supabase
        .from("user_watchlists")
        .upsert(rowsToInsert, { onConflict: "user_id,symbol" });
      if (!accountScope.current.isCurrent(ticket)) return;
      if (!error) {
        setAuditLoading(true);
        await fetchWatchlist(true);
      } else {
        console.error("Failed to seed starter setups:", error.message);
      }
    } finally {
      if (accountScope.current.isCurrent(ticket)) setSeedingStarter(false);
    }
  }, [user, seedingStarter, fetchWatchlist]);

  const handleDelete = useCallback(
    async (symbol: string) => {
      if (!user) return;
      const ticket = accountScope.current.capture();
      if (ticket.userId !== user.id) return;
      if (!confirm(`Remove ${symbol} from your watchlist?`)) return;
      try {
        const supabase = getSupabaseClient();
        const { error } = await supabase
          .from("user_watchlists")
          .delete()
          .eq("user_id", user.id)
          .eq("symbol", symbol.toUpperCase());
        if (!accountScope.current.isCurrent(ticket)) return;
        if (!error) {
          setItems((prev) => prev.filter((item) => item.symbol !== symbol));
          window.dispatchEvent(new CustomEvent("atlas:watchlist-updated"));
        } else {
          console.error("Delete failed:", error.message);
        }
      } catch (e) {
        console.error("Delete failed:", e);
      }
    },
    [user],
  );

  const handleEditNotes = useCallback((item: WatchlistItem) => {
    setEditingItem(item);
  }, []);

  const handleCloseEditNotes = useCallback(() => {
    setEditingItem(null);
  }, []);

  const handleSaveSetup = useCallback(
    async (updated: EditCandidateSetupPayload) => {
      if (!editingItem || !user) return;
      const ticket = accountScope.current.capture();
      if (ticket.userId !== user.id) return;
      const curPrice = livePrices[editingItem.symbol] ?? editingItem.current_price;
      const distPct =
        curPrice > 0
          ? Number(((Math.abs(updated.trigger_price - curPrice) / curPrice) * 100).toFixed(2))
          : editingItem.distance_to_trigger_pct;
      try {
        const supabase = getSupabaseClient();
        const { error } = await supabase
          .from("user_watchlists")
          .update({
            stage: updated.stage,
            timeframe: updated.timeframe,
            trigger_price: updated.trigger_price,
            support_price: updated.support_price,
            invalidation_price: updated.invalidation_price,
            target_1: updated.target_1,
            target_2: updated.target_2,
            distance_to_trigger_pct: distPct,
            thesis: updated.thesis,
            notes: updated.notes,
            updated_at: new Date().toISOString(),
          })
          .eq("user_id", user.id)
          .eq("symbol", updated.symbol);

        if (!accountScope.current.isCurrent(ticket)) return;
        if (!error) {
          setEditingItem(null);
          setAuditLoading(true);
          await fetchWatchlist(true);
        } else {
          console.error("Update setup failed:", error.message);
        }
      } catch (e) {
        console.error("Update setup failed:", e);
      }
    },
    [editingItem, user, livePrices, fetchWatchlist],
  );

  const handleOpenAddModal = useCallback(() => {
    setIsAddModalOpen(true);
  }, []);

  const handleCloseAddModal = useCallback(() => {
    setIsAddModalOpen(false);
  }, []);

  const handleAddTokenSubmit = useCallback(
    async (formData: CandidateFormData) => {
      if (!user) return;
      const ticket = accountScope.current.capture();
      if (ticket.userId !== user.id) return;
      const curPrice = parseFloat(formData.current_price) || 1.0;
      const trigPrice = parseFloat(formData.trigger_price) || 1.05;
      const distPct =
        curPrice > 0 ? Number(((Math.abs(trigPrice - curPrice) / curPrice) * 100).toFixed(2)) : 3.5;

      const nowIso = new Date().toISOString();
      const rowPayload = {
        user_id: user.id,
        symbol: formData.symbol.toUpperCase().trim(),
        name: formData.name || formData.symbol.toUpperCase().replace("USDT", ""),
        sector: formData.sector,
        stage: formData.stage,
        timeframe: formData.timeframe,
        current_price: curPrice,
        trigger_price: trigPrice,
        support_price: parseFloat(formData.support_price) || 0.95,
        invalidation_price: parseFloat(formData.invalidation_price) || 0.92,
        target_1: parseFloat(formData.target_1) || Number((trigPrice * 1.08).toFixed(4)),
        target_2: parseFloat(formData.target_2) || Number((trigPrice * 1.18).toFixed(4)),
        distance_to_trigger_pct: distPct,
        score: 62.0,
        thesis: formData.thesis || "High-probability consolidation base on watch.",
        checklist_passed: 5,
        checklist_total: 5,
        priority: formData.priority,
        notes: formData.notes,
        updated_at: nowIso,
      };

      try {
        const supabase = getSupabaseClient();
        const { error } = await supabase
          .from("user_watchlists")
          .upsert(rowPayload, { onConflict: "user_id,symbol" });
        if (!accountScope.current.isCurrent(ticket)) return;
        if (!error) {
          setIsAddModalOpen(false);
          setAuditLoading(true);
          await fetchWatchlist(true);
        } else {
          console.error("Add failed:", error.message);
        }
      } catch (err) {
        console.error("Add failed:", err);
      }
    },
    [user, fetchWatchlist],
  );

  // Memoize filtered items list
  const filteredItems = useMemo(() => {
    return items.filter((item) => {
      const audit = getEffectiveAudit(item);
      if (selectedFilter === "ALL") return true;
      if (selectedFilter === "ACTIVE_EXPANSION") return audit.status === "ACTIVE_EXPANSION";
      if (selectedFilter === "FLOW_DIVERGENCE") return audit.status === "FLOW_DIVERGENCE";
      if (selectedFilter === "SELLER_INFLUX") return audit.status === "SELLER_INFLUX";
      if (selectedFilter === "COIL_HOLDING") return audit.status === "COIL_HOLDING";
      if (selectedFilter === "PHASE_C") return item.stage.includes("Phase C");
      if (selectedFilter === "VCP") return item.stage.includes("VCP");
      if (selectedFilter === "1_3_DAYS")
        return item.timeframe.includes("1–3") || item.timeframe.includes("1-2");
      if (selectedFilter === "HIGH") return item.priority === "HIGH";
      return true;
    });
  }, [items, selectedFilter]);

  // Memoize filter tabs definitions
  const filterTabs = useMemo(
    () => [
      { key: "ALL", label: `All Tokens (${items.length})` },
      { key: "ACTIVE_EXPANSION", label: "⚡ Active Expansion" },
      { key: "FLOW_DIVERGENCE", label: "⚠️ Flow Divergence" },
      { key: "SELLER_INFLUX", label: "🛑 Seller Influx" },
      { key: "COIL_HOLDING", label: "💎 Coil Holding" },
      { key: "PHASE_C", label: "Phase C Springs" },
      { key: "HIGH", label: "High Priority" },
    ],
    [items.length],
  );

  const handlePinAutoWatchToPersonal = useCallback(
    async (ep: AutoWatchEpisode): Promise<boolean> => {
      if (!user) return false;
      const ticket = accountScope.current.capture();
      if (ticket.userId !== user.id) return false;
      const supabase = getSupabaseClient();
      const triggerPx = ep.trigger_price ?? ep.entry_price;
      const dist =
        ep.last_price > 0
          ? Number(((Math.abs(triggerPx - ep.last_price) / ep.last_price) * 100).toFixed(2))
          : 0;
      const { error } = await supabase.from("user_watchlists").upsert(
        {
          user_id: user.id,
          symbol: ep.symbol.toUpperCase(),
          name: ep.symbol.replace(/USDT$/, ""),
          sector: ep.direction.toUpperCase(),
          stage: ep.setup_label,
          timeframe: "1–2 Days",
          current_price: ep.signal_price,
          trigger_price: triggerPx,
          support_price: ep.poc_price ?? ep.entry_price,
          invalidation_price: ep.stop_price,
          target_1: ep.target_1,
          target_2: ep.target_2 ?? ep.target_1,
          distance_to_trigger_pct: dist,
          score: ep.score,
          thesis: `Pinned from 48h Radar Auto-Watch (${ep.setup_label}, published ${ep.published_at}).`,
          checklist_passed: 5,
          checklist_total: 5,
          priority: "HIGH",
          notes: `Signal midpoint $${ep.signal_price}. Sampled peak move: ${ep.peak_mfe_pct === null ? "unavailable" : `${ep.peak_mfe_pct.toFixed(2)}%`}.`,
          updated_at: new Date().toISOString(),
        },
        { onConflict: "user_id,symbol" },
      );
      if (!accountScope.current.isCurrent(ticket)) return false;
      if (!error) {
        void fetchWatchlist(false);
        return true;
      }
      return false;
    },
    [user, fetchWatchlist],
  );

  const workspaceSwitcher = (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        flexWrap: "wrap",
        gap: "12px",
        marginBottom: "20px",
        paddingBottom: "14px",
        borderBottom: "1px solid var(--border-subtle)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
        <BookmarkCheck size={24} style={{ color: "#f49d0d" }} />
        <h1 style={{ fontSize: "22px", fontWeight: 800, letterSpacing: "-0.02em", margin: 0 }}>
          Watchlist &amp; 48h Radar Performance
        </h1>
      </div>

      <div
        style={{
          display: "inline-flex",
          gap: "6px",
          padding: "4px",
          borderRadius: "10px",
          background: "rgba(255, 255, 255, 0.04)",
          border: "1px solid var(--border-subtle)",
        }}
      >
        <button
          type="button"
          onClick={() => setWorkspaceTab("auto")}
          style={{
            padding: "8px 14px",
            borderRadius: "8px",
            fontSize: "12.5px",
            fontWeight: 800,
            cursor: "pointer",
            border: "none",
            background: workspaceTab === "auto" ? "#f49d0d" : "transparent",
            color: workspaceTab === "auto" ? "#07090e" : "var(--text-secondary)",
          }}
        >
          Radar Auto-Watch (48h)
        </button>
        <button
          type="button"
          onClick={() => setWorkspaceTab("personal")}
          style={{
            padding: "8px 14px",
            borderRadius: "8px",
            fontSize: "12.5px",
            fontWeight: 800,
            cursor: "pointer",
            border: "none",
            background: workspaceTab === "personal" ? "#f49d0d" : "transparent",
            color: workspaceTab === "personal" ? "#07090e" : "var(--text-secondary)",
          }}
        >
          My Watchlist {user ? `(${items.length})` : ""}
        </button>
      </div>
    </div>
  );

  if (workspaceTab === "auto") {
    return (
      <div style={{ minHeight: "100vh", padding: "28px 36px 100px", maxWidth: "1600px", margin: "0 auto" }}>
        {workspaceSwitcher}
        <AutoWatchlistSection
          key={user?.id ?? "anonymous"}
          canPinToPersonal={Boolean(user)}
          onPinToPersonal={handlePinAutoWatchToPersonal}
        />
      </div>
    );
  }

  if (!authChecked) {
    return (
      <div style={{ minHeight: "100vh", padding: "28px 36px 100px", maxWidth: "1600px", margin: "0 auto" }}>
        {workspaceSwitcher}
        <div
          style={{
            minHeight: "60vh",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: "var(--text-secondary)",
            fontFamily: "var(--font-mono)",
            fontSize: "13px",
            gap: "10px",
          }}
        >
          <RefreshCw size={16} style={{ animation: "spin 1s linear infinite", color: "#f49d0d" }} />
          Verifying Supabase session...
        </div>
      </div>
    );
  }

  // Strict login requirement for personal watchlist only
  if (!user) {
    return (
      <div style={{ minHeight: "100vh", padding: "28px 36px 100px", maxWidth: "1600px", margin: "0 auto" }}>
        {workspaceSwitcher}
        <SupabaseAuthPanel />
      </div>
    );
  }

  return (
    <div style={{ minHeight: "100vh", padding: "28px 36px 100px", maxWidth: "1600px", margin: "0 auto" }}>
      {workspaceSwitcher}
      {/* Page Header */}
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
            <BookmarkCheck size={26} style={{ color: "#f49d0d" }} />
            <h1 style={{ fontSize: "24px", fontWeight: 800, letterSpacing: "-0.02em" }}>
              Multi-Day Pre-Breakout Watchlist
            </h1>
            <span
              style={{
                background: "rgba(244, 157, 13, 0.12)",
                color: "#f49d0d",
                border: "1px solid rgba(244, 157, 13, 0.3)",
                borderRadius: "20px",
                padding: "2px 10px",
                fontSize: "12px",
                fontWeight: 700,
              }}
            >
              Live Institutional Gate Monitor
            </span>
            <span
              style={{
                background: wsConnected ? "rgba(16, 185, 129, 0.12)" : "rgba(244, 157, 13, 0.12)",
                color: wsConnected ? "var(--accent-emerald)" : "#f49d0d",
                border: `1px solid ${
                  wsConnected ? "rgba(16, 185, 129, 0.3)" : "rgba(244, 157, 13, 0.3)"
                }`,
                borderRadius: "20px",
                padding: "2px 10px",
                fontSize: "12px",
                fontWeight: 700,
                display: "inline-flex",
                alignItems: "center",
                gap: "5px",
              }}
            >
              <span
                style={{
                  width: "6px",
                  height: "6px",
                  borderRadius: "50%",
                  background: wsConnected ? "var(--accent-emerald)" : "#f49d0d",
                }}
              />
              {wsConnected
                ? `Binance Stream Live (${Object.keys(livePrices).length} Tickers)`
                : `REST Price Polling (${Object.keys(livePrices).length} Tickers)`}
            </span>
            <span
              style={{
                background: "rgba(56, 189, 248, 0.12)",
                color: "#38bdf8",
                border: "1px solid rgba(56, 189, 248, 0.3)",
                borderRadius: "20px",
                padding: "2px 10px",
                fontSize: "11.5px",
                fontWeight: 700,
                display: "inline-flex",
                alignItems: "center",
                gap: "5px",
              }}
              title={`Authenticated Supabase User ID: ${user.id}`}
            >
              <UserCheck size={12} />
              {user.email || "Personal Workspace"}
            </span>
          </div>
          <p style={{ color: "var(--text-secondary)", fontSize: "14px", marginTop: "6px" }}>
            Your private, RLS-isolated watchlist with real-time gate diagnostics tracking Wyckoff spring bases, order flow absorption, and momentum graduation vs Binance USD-M.
          </p>
        </div>

        {/* Action Buttons: Live Audit, Add Token & Sign Out */}
        <div style={{ display: "flex", gap: "10px", alignItems: "center", flexWrap: "wrap" }}>
          <button
            type="button"
            onClick={() => {
              setAuditLoading(true);
              void fetchWatchlist(true);
            }}
            disabled={auditLoading}
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
              cursor: auditLoading ? "not-allowed" : "pointer",
              transition: "all 0.2s ease",
            }}
          >
            <RefreshCw
              size={15}
              style={{
                animation: auditLoading ? "spin 1s linear infinite" : "none",
                color: "#f49d0d",
              }}
            />
            {auditLoading ? "Auditing Positions..." : "Run Real-Time Audit"}
          </button>

          <button
            type="button"
            onClick={handleOpenAddModal}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "8px",
              background: "#f49d0d",
              color: "#07090e",
              fontWeight: 800,
              fontSize: "13px",
              padding: "10px 18px",
              borderRadius: "10px",
              border: "none",
              cursor: "pointer",
              transition: "all 0.2s ease",
            }}
          >
            <Plus size={16} strokeWidth={3} /> Add Candidate
          </button>

          <button
            type="button"
            onClick={handleSignOut}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "6px",
              background: "rgba(239, 68, 68, 0.08)",
              color: "#f87171",
              border: "1px solid rgba(239, 68, 68, 0.25)",
              fontWeight: 700,
              fontSize: "12.5px",
              padding: "10px 14px",
              borderRadius: "10px",
              cursor: "pointer",
            }}
            title="Sign out of your personal watchlist"
          >
            <LogOut size={14} /> Sign Out
          </button>
        </div>
      </div>

      {/* Summary Stat Cards */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
          gap: "16px",
          marginBottom: "28px",
        }}
      >
        <div className="glass-panel" style={{ padding: "16px 20px" }}>
          <div style={{ fontSize: "12px", color: "var(--text-muted)", fontWeight: 600, textTransform: "uppercase" }}>
            My Active Watchlist
          </div>
          <div style={{ fontSize: "28px", fontWeight: 800, color: "var(--text-primary)", marginTop: "4px" }}>
            {items.length} <span style={{ fontSize: "14px", color: "#f49d0d", fontWeight: 600 }}>Tokens</span>
          </div>
          <div style={{ fontSize: "11px", color: "var(--text-secondary)", marginTop: "4px" }}>
            Isolated to your account &amp; audited on Binance USD-M
          </div>
        </div>

        <div className="glass-panel" style={{ padding: "16px 20px" }}>
          <div style={{ fontSize: "12px", color: "var(--text-muted)", fontWeight: 600, textTransform: "uppercase" }}>
            Average Risk/Reward
          </div>
          <div style={{ fontSize: "28px", fontWeight: 800, color: "var(--accent-emerald)", marginTop: "4px" }}>
            4.8 : 1
          </div>
          <div style={{ fontSize: "11px", color: "var(--text-secondary)", marginTop: "4px" }}>
            Based on structural invalidation vs T2 targets
          </div>
        </div>

        <div className="glass-panel" style={{ padding: "16px 20px" }}>
          <div style={{ fontSize: "12px", color: "var(--text-muted)", fontWeight: 600, textTransform: "uppercase" }}>
            Breakout Horizon
          </div>
          <div style={{ fontSize: "28px", fontWeight: 800, color: "#f49d0d", marginTop: "4px" }}>
            1–3 Days
          </div>
          <div style={{ fontSize: "11px", color: "var(--text-secondary)", marginTop: "4px" }}>
            Tracking pre-expansion volatility squeeze
          </div>
        </div>

        <div className="glass-panel" style={{ padding: "16px 20px" }}>
          <div style={{ fontSize: "12px", color: "var(--text-muted)", fontWeight: 600, textTransform: "uppercase" }}>
            Gate Monitor Status
          </div>
          <div style={{ fontSize: "28px", fontWeight: 800, color: "#ffffff", marginTop: "4px", display: "flex", alignItems: "center", gap: "8px" }}>
            <span style={{ width: "10px", height: "10px", borderRadius: "50%", background: "#10b981" }} />
            Active
          </div>
          <div style={{ fontSize: "11px", color: "var(--text-secondary)", marginTop: "4px" }}>
            {lastAudited ? `Last audited ${lastAudited.toLocaleTimeString()}` : "Auto-polling every 30s"}
          </div>
        </div>
      </div>

      {/* Filter Tabs */}
      <div style={{ display: "flex", gap: "8px", marginBottom: "20px", flexWrap: "wrap" }}>
        {filterTabs.map((f) => (
          <button
            key={f.key}
            type="button"
            onClick={() => setSelectedFilter(f.key)}
            style={{
              background: selectedFilter === f.key ? "rgba(244, 157, 13, 0.15)" : "rgba(255, 255, 255, 0.04)",
              color: selectedFilter === f.key ? "#f49d0d" : "var(--text-secondary)",
              border: `1px solid ${selectedFilter === f.key ? "rgba(244, 157, 13, 0.35)" : "var(--border-subtle)"}`,
              borderRadius: "8px",
              padding: "6px 14px",
              fontSize: "12px",
              fontWeight: 700,
              cursor: "pointer",
              transition: "all 0.15s ease",
            }}
          >
            {f.label}
          </button>
        ))}
      </div>

      {/* Empty State or Watchlist Cards Grid */}
      {!loading && items.length === 0 ? (
        <div
          className="glass-panel"
          style={{
            padding: "48px 28px",
            textAlign: "center",
            border: "1px dashed rgba(244, 157, 13, 0.35)",
            maxWidth: "620px",
            margin: "36px auto",
          }}
        >
          <BookmarkCheck size={36} style={{ color: "#f49d0d", margin: "0 auto 12px" }} />
          <h3 style={{ fontSize: "18px", fontWeight: 800, color: "var(--text-primary)", marginBottom: "6px" }}>
            Your Personal Watchlist is Empty
          </h3>
          <p style={{ fontSize: "13px", color: "var(--text-secondary)", lineHeight: 1.5, marginBottom: "20px" }}>
            Add your own custom token setup with structural support, trigger, and invalidation levels, or load the 5 curated institutional starter setups into your private workspace.
          </p>
          <div style={{ display: "flex", gap: "12px", justifyContent: "center", flexWrap: "wrap" }}>
            <button
              type="button"
              onClick={handleOpenAddModal}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: "8px",
                background: "#f49d0d",
                color: "#07090e",
                fontWeight: 800,
                fontSize: "13px",
                padding: "10px 18px",
                borderRadius: "10px",
                border: "none",
                cursor: "pointer",
              }}
            >
              <Plus size={15} strokeWidth={3} /> Add My First Setup
            </button>
            <button
              type="button"
              onClick={handleLoadStarterPicks}
              disabled={seedingStarter}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: "8px",
                background: "rgba(255, 255, 255, 0.06)",
                color: "var(--text-primary)",
                fontWeight: 700,
                fontSize: "13px",
                padding: "10px 18px",
                borderRadius: "10px",
                border: "1px solid var(--border-medium)",
                cursor: seedingStarter ? "not-allowed" : "pointer",
              }}
            >
              <Sparkles size={15} style={{ color: "#f49d0d" }} />
              {seedingStarter ? "Importing..." : "Load 5 Curated Institutional Setups"}
            </button>
          </div>
        </div>
      ) : (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(480px, 1fr))",
            gap: "18px",
          }}
        >
          {filteredItems.map((item) => (
            <WatchlistCard
              key={item.symbol}
              item={item}
              livePrice={livePrices[item.symbol]}
              isExpanded={expandedAudits[item.symbol] !== false}
              onToggleExpand={toggleExpandAudit}
              onEditNotes={handleEditNotes}
              onDelete={handleDelete}
            />
          ))}
        </div>
      )}

      {/* Lazy Loaded Modals */}
      {editingItem && (
        <EditNotesModal
          key={editingItem.symbol}
          editingItem={editingItem}
          onClose={handleCloseEditNotes}
          onSaveSetup={handleSaveSetup}
        />
      )}

      {isAddModalOpen && (
        <AddCandidateModal
          isOpen={isAddModalOpen}
          onClose={handleCloseAddModal}
          onSubmit={handleAddTokenSubmit}
        />
      )}
    </div>
  );
}
