"use client";

import { useEffect, useState, useMemo, useCallback } from "react";
import dynamic from "next/dynamic";
import {
  BookmarkCheck,
  Plus,
  RefreshCw,
} from "lucide-react";
import WatchlistCard, { getEffectiveAudit } from "./components/WatchlistCard";
import type { CandidateFormData } from "./components/AddCandidateModal";

export interface WatchlistLiveAudit {
  symbol: string;
  status: "COIL_HOLDING" | "ACTIVE_EXPANSION" | "FLOW_DIVERGENCE" | "SELLER_INFLUX" | "TARGET_REACHED" | "STOP_BREACHED";
  status_title: string;
  live_price: number;
  price_change_pct: number;
  current_rsi_1h: number;
  current_rsi_4h: number;
  taker_ratio: number | null;
  vol_contraction_ratio: number | null;
  gate_tripped: string;
  live_reading: string;
  what_happened: string;
  institutional_logic: string;
  audited_at: string;
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

export default function WatchlistPage() {
  const [items, setItems] = useState<WatchlistItem[]>([]);
  const [, setLoading] = useState(true);
  const [auditLoading, setAuditLoading] = useState(false);
  const [lastAudited, setLastAudited] = useState<Date | null>(null);
  const [selectedFilter, setSelectedFilter] = useState<string>("ALL");
  const [isAddModalOpen, setIsAddModalOpen] = useState(false);
  const [editingItem, setEditingItem] = useState<WatchlistItem | null>(null);
  const [editNotesText, setEditNotesText] = useState("");
  const [expandedAudits, setExpandedAudits] = useState<Record<string, boolean>>({});
  const [livePrices, setLivePrices] = useState<Record<string, number>>({});
  const [wsConnected, setWsConnected] = useState(false);

  const toggleExpandAudit = useCallback((symbol: string) => {
    setExpandedAudits((prev) => ({
      ...prev,
      [symbol]: prev[symbol] === undefined ? false : !prev[symbol],
    }));
  }, []);

  const fetchWatchlist = useCallback(async (forceRefresh: boolean = false) => {
    try {
      const url = forceRefresh ? "/api/watchlist?live_audit=true&refresh=true" : "/api/watchlist?live_audit=true";
      const res = await fetch(url);
      if (res.ok) {
        const data = await res.json();
        setItems(data.items || []);
        setLastAudited(new Date());
      } else {
        const directUrl = forceRefresh
          ? "http://127.0.0.1:8000/api/watchlist?live_audit=true&refresh=true"
          : "http://127.0.0.1:8000/api/watchlist?live_audit=true";
        const direct = await fetch(directUrl);
        if (direct.ok) {
          const d = await direct.json();
          setItems(d.items || []);
          setLastAudited(new Date());
        }
      }
    } catch (e) {
      console.error("Failed to load watchlist:", e);
    } finally {
      setLoading(false);
      setAuditLoading(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    fetchWatchlist();
    const interval = setInterval(() => {
      fetchWatchlist(false);
    }, 30000);
    return () => clearInterval(interval);
  }, [fetchWatchlist]);

  // --------------------------------------------------------------------------
  // Real-time Multi-Ticker Streaming & Snapshot for Watchlist tokens
  // --------------------------------------------------------------------------
  useEffect(() => {
    if (!items || items.length === 0) return;

    let isSubscribed = true;
    let ws: WebSocket | null = null;
    let pollTimer: NodeJS.Timeout | null = null;

    const symbols = Array.from(new Set(items.map((i) => i.symbol.toUpperCase())));
    if (symbols.length === 0) return;

    // 1. Instant REST snapshot for all watchlist symbols
    const fetchSnapshot = async () => {
      try {
        const encoded = encodeURIComponent(JSON.stringify(symbols));
        let res = await fetch(`https://api.binance.com/api/v3/ticker/price?symbols=${encoded}`).catch(() => null);
        if (!res || !res.ok) {
          res = await fetch("https://fapi.binance.com/fapi/v1/ticker/price").catch(() => null);
        }
        if (res && res.ok && isSubscribed) {
          const data = await res.json();
          if (Array.isArray(data)) {
            const symSet = new Set(symbols);
            const prices: Record<string, number> = {};
            data.forEach((item: { symbol: string; price: string }) => {
              if (symSet.has(item.symbol)) {
                prices[item.symbol] = parseFloat(item.price);
              }
            });
            setLivePrices((prev) => ({ ...prev, ...prices }));
          }
        }
      } catch {
        // ignore network error
      }
    };

    fetchSnapshot();

    // 2. Real-time Multi-ticker WebSocket stream
    const streams = symbols.map((s) => `${s.toLowerCase()}@ticker`).join("/");
    const wsUrl = `wss://fstream.binance.com/stream?streams=${streams}`;

    const startPollingFallback = () => {
      setWsConnected(false);
      if (pollTimer) clearInterval(pollTimer);
      pollTimer = setInterval(fetchSnapshot, 3000);
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
          if (data && data.s && data.c) {
            const sym = data.s.toUpperCase();
            const curPrice = parseFloat(data.c);

            setLivePrices((prev) => {
              if (prev[sym] === curPrice) return prev;
              return { ...prev, [sym]: curPrice };
            });
          }
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
        ws.close();
      }
    };
  }, [items]);

  const handleDelete = useCallback(async (symbol: string) => {
    if (!confirm(`Remove ${symbol} from watchlist?`)) return;
    try {
      const res = await fetch(`/api/watchlist/${symbol}`, { method: "DELETE" });
      if (res.ok) {
        setItems((prev) => prev.filter((item) => item.symbol !== symbol));
      }
    } catch (e) {
      console.error("Delete failed:", e);
    }
  }, []);

  const handleEditNotes = useCallback((item: WatchlistItem) => {
    setEditingItem(item);
    setEditNotesText(item.notes);
  }, []);

  const handleCloseEditNotes = useCallback(() => {
    setEditingItem(null);
  }, []);

  const handleNotesTextChange = useCallback((val: string) => {
    setEditNotesText(val);
  }, []);

  const handleSaveNotes = useCallback(async () => {
    if (!editingItem) return;
    try {
      const res = await fetch(`/api/watchlist/${editingItem.symbol}/notes`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ notes: editNotesText }),
      });
      if (res.ok) {
        setItems((prev) =>
          prev.map((i) =>
            i.symbol === editingItem.symbol ? { ...i, notes: editNotesText } : i
          )
        );
        setEditingItem(null);
      }
    } catch (e) {
      console.error("Update notes failed:", e);
    }
  }, [editingItem, editNotesText]);

  const handleOpenAddModal = useCallback(() => {
    setIsAddModalOpen(true);
  }, []);

  const handleCloseAddModal = useCallback(() => {
    setIsAddModalOpen(false);
  }, []);

  const handleAddTokenSubmit = useCallback(async (formData: CandidateFormData) => {
    const payload = {
      symbol: formData.symbol.toUpperCase(),
      name: formData.name || formData.symbol.toUpperCase().replace("USDT", ""),
      sector: formData.sector,
      stage: formData.stage,
      timeframe: formData.timeframe,
      current_price: parseFloat(formData.current_price) || 1.0,
      trigger_price: parseFloat(formData.trigger_price) || 1.05,
      support_price: parseFloat(formData.support_price) || 0.95,
      invalidation_price: parseFloat(formData.invalidation_price) || 0.92,
      target_1: parseFloat(formData.target_1) || 1.15,
      target_2: parseFloat(formData.target_2) || 1.3,
      distance_to_trigger_pct: 3.5,
      score: 62.0,
      thesis: formData.thesis || "High-probability consolidation base on watch.",
      checklist_passed: 5,
      checklist_total: 5,
      priority: formData.priority,
      notes: formData.notes,
    };

    try {
      const res = await fetch("/api/watchlist", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (res.ok) {
        setIsAddModalOpen(false);
        fetchWatchlist(true);
      }
    } catch (err) {
      console.error("Add failed:", err);
    }
  }, [fetchWatchlist]);

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
      if (selectedFilter === "1_3_DAYS") return item.timeframe.includes("1–3") || item.timeframe.includes("1-2");
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
    [items.length]
  );

  return (
    <div style={{ minHeight: "100vh", padding: "28px 36px 100px", maxWidth: "1600px", margin: "0 auto" }}>
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
                border: `1px solid ${wsConnected ? "rgba(16, 185, 129, 0.3)" : "rgba(244, 157, 13, 0.3)"}`,
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
                  boxShadow: `0 0 6px ${wsConnected ? "var(--accent-emerald)" : "#f49d0d"}`,
                }}
              />
              {wsConnected
                ? `Binance Stream Live (${Object.keys(livePrices).length} Tickers)`
                : `REST Price Polling (${Object.keys(livePrices).length} Tickers)`}
            </span>
          </div>
          <p style={{ color: "var(--text-secondary)", fontSize: "14px", marginTop: "6px" }}>
            Continuous real-time gate diagnostics tracking Wyckoff spring bases, order flow absorption, and momentum graduation vs Binance USD-M.
          </p>
        </div>

        {/* Action Buttons: Live Audit & Add Token */}
        <div style={{ display: "flex", gap: "10px", alignItems: "center" }}>
          <button
            type="button"
            onClick={() => {
              setAuditLoading(true);
              fetchWatchlist(true);
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
              boxShadow: "0 4px 16px rgba(244, 157, 13, 0.3)",
              transition: "all 0.2s ease",
            }}
          >
            <Plus size={16} strokeWidth={3} /> Add Candidate
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
            Active Watchlist
          </div>
          <div style={{ fontSize: "28px", fontWeight: 800, color: "var(--text-primary)", marginTop: "4px" }}>
            {items.length} <span style={{ fontSize: "14px", color: "#f49d0d", fontWeight: 600 }}>Tokens</span>
          </div>
          <div style={{ fontSize: "11px", color: "var(--text-secondary)", marginTop: "4px" }}>
            Audited in real-time on Binance USD-M
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
            <span style={{ width: "10px", height: "10px", borderRadius: "50%", background: "#10b981", boxShadow: "0 0 10px #10b981" }} />
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

      {/* Watchlist Cards Grid */}
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

      {/* Lazy Loaded Modals */}
      {editingItem && (
        <EditNotesModal
          editingItem={editingItem}
          editNotesText={editNotesText}
          onNotesChange={handleNotesTextChange}
          onClose={handleCloseEditNotes}
          onSave={handleSaveNotes}
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
