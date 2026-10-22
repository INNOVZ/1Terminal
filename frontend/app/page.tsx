"use client";

import { useEffect, useState, useMemo, useCallback, Suspense } from "react";
import dynamic from "next/dynamic";
import { useSearchParams } from "next/navigation";
import type { Opportunity } from "./components/CandidateSidebar";
import type { Candle, CandleInterval } from "./components/TradingChart";
import { Zap, RefreshCw, AlertCircle } from "lucide-react";
import baselineCandles from "@/public/baseline_candles.json";
import { useLocalStorage } from "./hooks/useLocalStorage";

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

function RadarPageContent() {
  const searchParams = useSearchParams();
  const symbolParam = searchParams ? searchParams.get("symbol") : null;

  const [opportunities, setOpportunities] = useState<Opportunity[]>([]);
  const [activeOpportunity, setActiveOpportunity] =
    useState<Opportunity | null>(null);
  const [activeCandle, setActiveCandle] = useState<Candle | null>(null);
  const [livePrices, setLivePrices] = useState<Record<string, number>>({});
  const [priceChanges24h, setPriceChanges24h] = useState<
    Record<string, number>
  >({});
  const [loading, setLoading] = useState(true);
  const [isScanning, setIsScanning] = useState(false);
  const [scanNotification, setScanNotification] = useState<string | null>(null);
  const [showLeftSidebar, setShowLeftSidebar] = useState(true);
  const [showRightPanel, setShowRightPanel] = useState(true);

  // User indicator & timeframe preferences (persisted across tokens and browser reloads)
  const [showLevels, setShowLevels] = useLocalStorage(
    "atlas_pref_show_levels",
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
  }, [candleStore]);

  const handleLiveTick = useCallback(
    (tick: { price: number }) => {
      if (!activeOpportunity) return;
      setLivePrices((prev) => {
        if (prev[activeOpportunity.symbol] === tick.price) return prev;
        return {
          ...prev,
          [activeOpportunity.symbol]: tick.price,
        };
      });
    },
    [activeOpportunity],
  );

  const fetchScreener = useCallback(async (isLiveRefresh: boolean = false, scope: "core" | "full" = "core") => {
    if (isLiveRefresh) {
      setIsScanning(true);
      window.dispatchEvent(new CustomEvent("atlas:scan-start"));
    }

    const applyScreenerData = async (incoming: ScreenerResponse) => {
      if (!incoming || !incoming.opportunities || incoming.opportunities.length === 0) return;
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
              const chartData = (await chartRes.json()) as { opportunity?: Opportunity; candles?: Candle[] };
              if (chartData && chartData.opportunity) {
                matchedTarget = {
                  ...chartData.opportunity,
                  candles:
                    chartData.candles && chartData.candles.length > 0
                      ? chartData.candles
                      : candleStore[cleanTarget] || [],
                };
                enriched.unshift(matchedTarget);
              }
            }
          } catch (cErr) {
            console.warn(
              "Could not load targeted watchlist candidate:",
              cErr,
            );
          }
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

      if (isLiveRefresh) {
        setScanNotification(
          `✓ Rescan complete: verified ${enriched.length} setups`,
        );
        window.dispatchEvent(
          new CustomEvent("atlas:scan-complete", {
            detail: { count: enriched.length, timestamp: incoming.timestamp },
          }),
        );
      }
    };

    try {
      const cacheBuster = `t=${Date.now()}`;
      const url = isLiveRefresh
        ? `/api/screener/live?refresh=true&scope=${scope}&${cacheBuster}`
        : `/api/screener/live?${cacheBuster}`;

      const res = await fetch(url, { cache: "no-store" }).catch(() => null);
      let data: ScreenerResponse | null = null;
      if (res && res.ok) {
        data = (await res.json()) as ScreenerResponse;
      }

      // If backend reports an active background scan, poll non-blockingly until it finishes
      if (data && data.is_scanning) {
        setIsScanning(true);
        let pollAttempts = 0;
        const maxPollAttempts = 40; // Max 80 seconds
        let finalData: ScreenerResponse | null = null;
        while (pollAttempts < maxPollAttempts) {
          await new Promise((r) => setTimeout(r, 2000));
          pollAttempts++;
          try {
            const pollRes = await fetch(`/api/screener/live?t=${Date.now()}`, { cache: "no-store" }).catch(() => null);
            if (pollRes && pollRes.ok) {
              const pollData = (await pollRes.json()) as ScreenerResponse;
              if (pollData && !pollData.is_scanning) {
                finalData = pollData;
                break;
              }
            }
          } catch (pollErr) {
            console.warn("Polling error during rescan:", pollErr);
          }
        }
        if (finalData) {
          data = finalData;
        }
      }

      // Apply newly verified live scan data (never intermediate cached data)
      if (data && Array.isArray(data.opportunities) && data.opportunities.length > 0) {
        await applyScreenerData(data);
      } else {
        setOpportunities([]);
        setActiveOpportunity(null);
        setActiveCandle(null);
        if (isLiveRefresh) {
          setScanNotification(
            "Scan complete: 0 tokens currently qualify for institutional gates",
          );
          window.dispatchEvent(
            new CustomEvent("atlas:scan-complete", {
              detail: { count: 0, timestamp: data?.timestamp },
            }),
          );
        } else if (symbolParam) {
          // Screener returned 0 items, but we have a symbol query parameter!
          const cleanTarget = symbolParam.toUpperCase();
          try {
            const chartRes = await fetch(`/api/chart/${cleanTarget}`, { cache: "no-store" }).catch(
              () => null,
            );
            if (chartRes && chartRes.ok) {
              const chartData = (await chartRes.json()) as { opportunity?: Opportunity; candles?: Candle[] };
              if (chartData && chartData.opportunity) {
                const targetOpp: Opportunity = {
                  ...chartData.opportunity,
                  candles:
                    chartData.candles && chartData.candles.length > 0
                      ? chartData.candles
                      : [],
                };
                setOpportunities([targetOpp]);
                setActiveOpportunity(targetOpp);
                if (targetOpp.candles && targetOpp.candles.length > 0) {
                  setActiveCandle(
                    targetOpp.candles[targetOpp.candles.length - 1],
                  );
                }
              }
            }
          } catch (cErr) {
            console.warn(
              "Could not load targeted watchlist candidate:",
              cErr,
            );
          }
        }
      }
    } catch (err) {
      console.warn("Using offline radar baseline:", err);
      if (isLiveRefresh) {
        setScanNotification("Rescan error: connection timeout");
        window.dispatchEvent(new CustomEvent("atlas:scan-error"));
      }
    } finally {
      setLoading(false);
      if (isLiveRefresh) {
        setIsScanning(false);
        setTimeout(() => setScanNotification(null), 4000);
      }
    }
  }, [symbolParam]);

  useEffect(() => {
    const timer = setTimeout(() => {
      void fetchScreener(false);
    }, 0);
    const interval = setInterval(() => {
      void fetchScreener(false);
    }, 60000);
    return () => {
      clearTimeout(timer);
      clearInterval(interval);
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
          const chartData = (await res.json()) as { opportunity?: Opportunity; candles?: Candle[] };
          if (chartData && chartData.opportunity) {
            const oppWithCandles: Opportunity = {
              ...chartData.opportunity,
              candles:
                chartData.candles && chartData.candles.length > 0
                  ? chartData.candles
                  : [],
            };
            setOpportunities((prev) => {
              const targetBase = cleanTarget.replace(/USDT$/, "").replace(/USDC$/, "");
              const filtered = prev.filter((o) => {
                const oBase = (o.base_asset || o.symbol).replace(/USDT$/, "").replace(/USDC$/, "");
                return oBase.toUpperCase() !== targetBase;
              });
              return [oppWithCandles, ...filtered];
            });
            setActiveOpportunity(oppWithCandles);
            if (
              oppWithCandles.candles &&
              oppWithCandles.candles.length > 0
            ) {
              setActiveCandle(
                oppWithCandles.candles[oppWithCandles.candles.length - 1],
              );
            }
          }
        }
      } catch (err) {
        console.warn("Error loading targeted candidate chart:", err);
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
    window.addEventListener("atlas:trigger-rescan", handleTriggerRescan);
    return () =>
      window.removeEventListener("atlas:trigger-rescan", handleTriggerRescan);
  }, [fetchScreener]);

  // --------------------------------------------------------------------------
  // Real-time Multi-Ticker Streaming & Snapshot for ALL candidate tokens
  // --------------------------------------------------------------------------
  useEffect(() => {
    if (!opportunities || opportunities.length === 0) return;

    let isSubscribed = true;
    let ws: WebSocket | null = null;
    let pollTimer: NodeJS.Timeout | null = null;

    const symbols = Array.from(
      new Set(opportunities.map((o) => o.symbol.toUpperCase())),
    );
    if (symbols.length === 0) return;

    // 1. Instant REST snapshot for all candidate symbols
    const fetchSnapshot = async () => {
      try {
        const res = await fetch(
          "https://fapi.binance.com/fapi/v1/ticker/price",
        ).catch(() => null);
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

    // 2. Real-time Multi-ticker WebSocket stream (Binance combined stream)
    const streams = symbols.map((s) => `${s.toLowerCase()}@ticker`).join("/");
    const wsUrl = `wss://stream.binance.com:9443/stream?streams=${streams}`;

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
          if (data && data.s && data.c) {
            const sym = data.s.toUpperCase();
            const curPrice = parseFloat(data.c);
            const pct = data.P !== undefined ? parseFloat(data.P) : undefined;

            setLivePrices((prev) => {
              if (prev[sym] === curPrice) return prev;
              return { ...prev, [sym]: curPrice };
            });

            if (pct !== undefined) {
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
      if (ws) {
        ws.onopen = null;
        ws.onmessage = null;
        ws.onerror = null;
        ws.onclose = null;
        ws.close();
      }
      if (pollTimer) clearInterval(pollTimer);
    };
  }, [opportunities]);

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
      formattedPrice: currentPrice.toFixed(dec),
      poc: poc.toFixed(dec),
      vah: vah.toFixed(dec),
      oiChange: activeOpportunity.oi_change_7d_pct || "N/A",
      isOiNegative: String(activeOpportunity.oi_change_7d_pct).includes("-"),
      fundingRate: cleanFunding,
      riskReward: Math.min(Number(activeOpportunity.risk_reward_ratio || 0), 8.0).toFixed(2),
    };
  }, [activeOpportunity, livePrices, adaptedLevelsMap]);

  const candleMetrics = useMemo(() => {
    if (!activeCandle) return null;
    const isBullish = activeCandle.close >= activeCandle.open;
    const volFormatted =
      activeCandle.volume !== undefined
        ? activeCandle.volume >= 1_000_000
          ? `${(activeCandle.volume / 1_000_000).toFixed(1)}M`
          : `${(activeCandle.volume / 1000).toFixed(0)}k`
        : null;
    return {
      open: activeCandle.open,
      high: activeCandle.high,
      low: activeCandle.low,
      close: activeCandle.close,
      isBullish,
      volFormatted,
    };
  }, [activeCandle]);

  const activeCandles = useMemo(() => {
    return activeOpportunity?.candles || [];
  }, [activeOpportunity?.candles]);

  const memoizedTargets = useMemo(() => {
    const adapted = adaptedLevelsMap[activeOpportunity?.symbol || ""];
    if (adapted?.targets && adapted.targets.length > 0) return adapted.targets;
    return (activeOpportunity?.suggested_targets || []).map((t) => Number(t));
  }, [
    activeOpportunity?.symbol,
    activeOpportunity?.suggested_targets,
    adaptedLevelsMap,
  ]);

  const breakoutTriggerPrice = useMemo(() => {
    if (!activeOpportunity) return 0;
    const adapted = adaptedLevelsMap[activeOpportunity.symbol];
    if (adapted?.breakoutTrigger) return adapted.breakoutTrigger;
    return Number(
      activeOpportunity.breakout_trigger ||
        Number(activeOpportunity.current_price || 0) *
          (activeOpportunity.direction === "short" ? 0.975 : 1.025),
    );
  }, [activeOpportunity, adaptedLevelsMap]);

  const entryPriceVal = useMemo(() => {
    if (!activeOpportunity) return 0;
    const adapted = adaptedLevelsMap[activeOpportunity.symbol];
    if (adapted?.entryPrice) return adapted.entryPrice;
    return Number(
      activeOpportunity.pullback_entry ||
        activeOpportunity.suggested_entry ||
        0,
    );
  }, [activeOpportunity, adaptedLevelsMap]);

  const pocPriceVal = useMemo(() => {
    const adapted = adaptedLevelsMap[activeOpportunity?.symbol || ""];
    if (adapted?.pocPrice) return adapted.pocPrice;
    return Number(activeOpportunity?.poc_price || 0);
  }, [
    activeOpportunity?.symbol,
    activeOpportunity?.poc_price,
    adaptedLevelsMap,
  ]);

  const vahPriceVal = useMemo(() => {
    const adapted = adaptedLevelsMap[activeOpportunity?.symbol || ""];
    if (adapted?.vahPrice) return adapted.vahPrice;
    return Number(activeOpportunity?.vah_price || 0);
  }, [
    activeOpportunity?.symbol,
    activeOpportunity?.vah_price,
    adaptedLevelsMap,
  ]);

  const valPriceVal = useMemo(() => {
    const adapted = adaptedLevelsMap[activeOpportunity?.symbol || ""];
    if (adapted?.valPrice) return adapted.valPrice;
    return Number(activeOpportunity?.val_price || 0);
  }, [
    activeOpportunity?.symbol,
    activeOpportunity?.val_price,
    adaptedLevelsMap,
  ]);

  const stopPriceVal = useMemo(() => {
    const adapted = adaptedLevelsMap[activeOpportunity?.symbol || ""];
    if (adapted?.stopPrice) return adapted.stopPrice;
    return Number(activeOpportunity?.suggested_stop || 0);
  }, [
    activeOpportunity?.symbol,
    activeOpportunity?.suggested_stop,
    adaptedLevelsMap,
  ]);

  if (!activeOpportunity) {
    if (loading) {
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
              boxShadow: "0 0 35px rgba(244, 157, 13, 0.12)",
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
          color="#f49d0d"
          style={{ marginBottom: "16px" }}
        />
        <h2 style={{ fontSize: "18px", fontWeight: 800, margin: "0 0 8px 0" }}>
          No Active Pre-Breakout Setups Qualified
        </h2>
        <p
          style={{
            color: "var(--text-muted)",
            fontSize: "13px",
            fontFamily: "var(--font-mono)",
            maxWidth: "460px",
            textAlign: "center",
            marginBottom: "20px",
          }}
        >
          All evaluated contracts are either in active expansion or failed
          strict risk-reward bounds. The screener runs automatically every hour on
          UTC candle closes.
        </p>
        <button
          onClick={() => fetchScreener(true)}
          disabled={isScanning}
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: "8px",
            background: "linear-gradient(135deg, #f49d0d 0%, #d97706 100%)",
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
          padding: "10px 20px",
          background: "rgba(11, 15, 25, 0.98)",
          borderBottom: "1px solid var(--border-subtle)",
          zIndex: 30,
        }}
      >
        {/* Left: Brand & Active Token */}
        <div style={{ display: "flex", alignItems: "center", gap: "16px" }}>
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
                <span style={{ color: "var(--text-muted)" }}>R:R: </span>
                <strong style={{ color: "var(--accent-emerald)" }}>
                  {headerMetrics.riskReward}:1
                </strong>
              </div>
              <div
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
                }}
              >
                <Zap size={11} />
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
              <RefreshCw size={10} className="animate-spin" /> SCANNING LIVE
              UNIVERSE...
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
              activeSymbol={activeOpportunity ? activeOpportunity.symbol : ""}
              onSelect={handleSelectOpportunity}
              livePrices={livePrices}
              priceChanges24h={priceChanges24h}
            />
          </div>
        </div>

        {/* Column 2: TradingView Chart Canvas */}
        {activeOpportunity && (
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
            <TradingChart
              key={activeOpportunity.symbol}
              symbol={activeOpportunity.symbol}
              direction={activeOpportunity.direction}
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
          </section>
        )}

        {/* Column 3: Collapsible Institutional Verification Panel */}
        {activeOpportunity && (
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
              <VerificationPanel
                opportunity={activeOpportunity}
                adaptedLevels={adaptedLevelsMap[activeOpportunity.symbol]}
                livePrice={livePrices[activeOpportunity.symbol]}
              />
            </div>
          </div>
        )}
      </main>
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
