"use client";

import { useEffect, useRef, useState, useCallback, memo } from "react";
import {
  createChart,
  IChartApi,
  CandlestickData,
  HistogramData,
  ColorType,
  Time,
  CandlestickSeries,
  HistogramSeries,
  ISeriesApi,
} from "lightweight-charts";
import {
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  Maximize2,
  Minimize2,
} from "lucide-react";

export interface Candle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export type CandleInterval = "1h" | "4h" | "1d" | "1w";

export interface VolumeProfileResult {
  pocPrice: number;
  vahPrice: number;
  valPrice: number;
  totalVolume: number;
  valueAreaVolume: number;
}

export function computeVolumeProfile(
  candles: Candle[],
  bins = 50,
  valueAreaPct = 0.70
): VolumeProfileResult | null {
  if (!candles || candles.length === 0) return null;

  const lastTime = candles[candles.length - 1].time;
  const cutoffTime = lastTime - 30 * 86400;
  const windowCandles = candles.filter((c) => c.time >= cutoffTime);
  const activeWindow = windowCandles.length >= 10 ? windowCandles : candles.slice(-180);

  let minLow = Infinity;
  let maxHigh = -Infinity;
  let totalVol = 0;

  for (const c of activeWindow) {
    if (c.low < minLow) minLow = c.low;
    if (c.high > maxHigh) maxHigh = c.high;
    totalVol += c.volume || 0;
  }

  if (maxHigh <= minLow || totalVol <= 0 || bins <= 0) return null;

  const binWidth = (maxHigh - minLow) / bins;
  const binVolumes = new Float64Array(bins);
  const binMids = new Float64Array(bins);
  for (let i = 0; i < bins; i++) {
    binMids[i] = minLow + (i + 0.5) * binWidth;
  }

  for (const c of activeWindow) {
    const cVol = c.volume || 0;
    if (cVol <= 0) continue;
    const span = c.high - c.low;
    if (span <= 0) {
      const idx = Math.min(bins - 1, Math.max(0, Math.floor((c.low - minLow) / binWidth)));
      binVolumes[idx] += cVol;
      continue;
    }

    const lowIdx = Math.min(bins - 1, Math.max(0, Math.floor((c.low - minLow) / binWidth)));
    const highIdx = Math.min(bins - 1, Math.max(0, Math.floor((c.high - minLow) / binWidth)));

    for (let i = lowIdx; i <= highIdx; i++) {
      const bLow = minLow + i * binWidth;
      const bHigh = bLow + binWidth;
      const overlap = Math.max(0, Math.min(c.high, bHigh) - Math.max(c.low, bLow));
      binVolumes[i] += cVol * (overlap / span);
    }
  }

  let pocIdx = 0;
  let maxBinVol = -1;
  for (let i = 0; i < bins; i++) {
    if (binVolumes[i] > maxBinVol) {
      maxBinVol = binVolumes[i];
      pocIdx = i;
    }
  }
  const pocPrice = binMids[pocIdx];

  const targetVol = totalVol * valueAreaPct;
  let curVol = binVolumes[pocIdx];
  let minVaIdx = pocIdx;
  let maxVaIdx = pocIdx;
  let up = pocIdx + 1;
  let down = pocIdx - 1;

  while (curVol < targetVol && (up < bins || down >= 0)) {
    const upVol = up < bins ? binVolumes[up] : -1;
    const downVol = down >= 0 ? binVolumes[down] : -1;

    if (upVol >= downVol && up < bins) {
      curVol += upVol;
      maxVaIdx = Math.max(maxVaIdx, up);
      up++;
    } else if (down >= 0) {
      curVol += downVol;
      minVaIdx = Math.min(minVaIdx, down);
      down--;
    } else {
      break;
    }
  }

  return {
    pocPrice,
    vahPrice: binMids[maxVaIdx],
    valPrice: binMids[minVaIdx],
    totalVolume: totalVol,
    valueAreaVolume: curVol,
  };
}

export function getPriceFormatOptions(price: number) {
  if (price < 0.1) return { type: "price" as const, precision: 4, minMove: 0.0001 };
  if (price < 1) return { type: "price" as const, precision: 4, minMove: 0.0001 };
  if (price < 10) return { type: "price" as const, precision: 3, minMove: 0.001 };
  return { type: "price" as const, precision: 2, minMove: 0.01 };
}

export interface ChartProps {
  candles: Candle[];
  symbol: string;
  pocPrice?: number;
  vahPrice?: number;
  valPrice?: number;
  entryPrice?: number;
  breakoutTrigger?: number;
  stopPrice?: number;
  targets?: number[];
  onLiveTick?: (tick: { price: number; high: number; low: number; volume: number; time: number }) => void;
  onCandleChange?: (candle: Candle) => void;
  onLevelsUpdated?: (levels: {
    pocPrice: number;
    vahPrice: number;
    valPrice: number;
    entryPrice: number;
    stopPrice: number;
    breakoutTrigger: number;
    targets: number[];
  }) => void;
  showLeftSidebar?: boolean;
  onToggleLeftSidebar?: () => void;
  showRightPanel?: boolean;
  onToggleRightPanel?: () => void;
  showLevels?: boolean;
  onToggleLevels?: () => void;
  showTargets?: boolean;
  onToggleTargets?: () => void;
  showVolume?: boolean;
  onToggleVolume?: () => void;
  candleInterval?: CandleInterval;
  onChangeInterval?: (interval: CandleInterval) => void;
  direction?: string;
}

type PriceLine = ReturnType<ISeriesApi<"Candlestick">["createPriceLine"]>;

function TradingChart({
  candles: initialCandles = [],
  symbol,
  direction = "long",
  pocPrice,
  vahPrice,
  valPrice,
  entryPrice,
  breakoutTrigger,
  stopPrice,
  targets = [],
  onLiveTick,
  onCandleChange,
  showLeftSidebar,
  onToggleLeftSidebar,
  showRightPanel,
  onToggleRightPanel,
  showLevels: propShowLevels,
  onToggleLevels,
  showTargets: propShowTargets,
  onToggleTargets,
  showVolume: propShowVolume,
  onToggleVolume,
  candleInterval: propCandleInterval,
  onChangeInterval,
  onLevelsUpdated,
}: ChartProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  // Series references
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const priceLinesRef = useRef<PriceLine[]>([]);

  // Candle data buffer
  const activeCandlesRef = useRef<Candle[]>([]);

  // WebSocket & Polling refs
  const wsRef = useRef<WebSocket | null>(null);
  const pollIntervalRef = useRef<NodeJS.Timeout | null>(null);

  // Stabilized callbacks & options refs
  const onLiveTickRef = useRef(onLiveTick);
  const onCandleChangeRef = useRef(onCandleChange);
  const onLevelsUpdatedRef = useRef(onLevelsUpdated);

  useEffect(() => {
    onLiveTickRef.current = onLiveTick;
    onCandleChangeRef.current = onCandleChange;
    onLevelsUpdatedRef.current = onLevelsUpdated;
  }, [onLiveTick, onCandleChange, onLevelsUpdated]);

  const lastTickEmitTimeRef = useRef<number>(0);

  // Layout stabilization
  const lastDimensionsRef = useRef<{ width: number; height: number }>({ width: 0, height: 0 });

  // Indicator Visibility State (Supports controlled props with internal fallback)
  const [internalShowLevels, setInternalShowLevels] = useState(true);
  const showLevels = propShowLevels !== undefined ? propShowLevels : internalShowLevels;
  const handleToggleLevels = useCallback(() => {
    if (onToggleLevels) {
      onToggleLevels();
    } else {
      setInternalShowLevels((prev) => !prev);
    }
  }, [onToggleLevels]);

  const [internalShowTargets, setInternalShowTargets] = useState(true);
  const showTargets = propShowTargets !== undefined ? propShowTargets : internalShowTargets;
  const handleToggleTargets = useCallback(() => {
    if (onToggleTargets) {
      onToggleTargets();
    } else {
      setInternalShowTargets((prev) => !prev);
    }
  }, [onToggleTargets]);

  const [internalShowVolume, setInternalShowVolume] = useState(true);
  const showVolume = propShowVolume !== undefined ? propShowVolume : internalShowVolume;
  const handleToggleVolume = useCallback(() => {
    if (onToggleVolume) {
      onToggleVolume();
    } else {
      setInternalShowVolume((prev) => !prev);
    }
  }, [onToggleVolume]);

  // Candle Interval State: 1H, 4H, 1D, 1W (Supports controlled props with internal fallback)
  const [internalCandleInterval, setInternalCandleInterval] = useState<CandleInterval>("1h");
  const candleInterval = propCandleInterval !== undefined ? propCandleInterval : internalCandleInterval;
  const handleChangeInterval = useCallback((intv: CandleInterval) => {
    if (onChangeInterval) {
      onChangeInterval(intv);
    } else {
      setInternalCandleInterval(intv);
    }
  }, [onChangeInterval]);

  // Real-Time Connection Status & Hovered Legend
  const [connectionMode, setConnectionMode] = useState<"connecting" | "live_ws" | "live_poll">("connecting");
  const [hoveredCandle, setHoveredCandle] = useState<Candle | null>(null);

  // --------------------------------------------------------------------------
  // EFFECT 1: Mount & Maintain Chart Instance (Once per symbol)
  // --------------------------------------------------------------------------
  useEffect(() => {
    const container = chartContainerRef.current;
    if (!container) return;

    let isMounted = true;
    let resizeRaf: number | null = null;

    if (chartRef.current) {
      chartRef.current.remove();
      chartRef.current = null;
    }

    const initialWidth = container.clientWidth > 0 ? Math.floor(container.clientWidth) : 800;
    const initialHeight = container.clientHeight > 0 ? Math.floor(container.clientHeight) : 550;
    lastDimensionsRef.current = { width: initialWidth, height: initialHeight };

    const chart = createChart(container, {
      width: initialWidth,
      height: initialHeight,
      layout: {
        background: { type: ColorType.Solid, color: "#090d16" },
        textColor: "rgba(255, 255, 255, 0.72)",
        fontFamily: "'JetBrains Mono', 'Roboto Mono', monospace, sans-serif",
      },
      grid: {
        vertLines: { color: "rgba(255, 255, 255, 0.03)" },
        horzLines: { color: "rgba(255, 255, 255, 0.03)" },
      },
      crosshair: {
        mode: 0,
        vertLine: {
          color: "rgba(255, 255, 255, 0.35)",
          width: 1,
          style: 3,
        },
        horzLine: {
          color: "rgba(255, 255, 255, 0.35)",
          width: 1,
          style: 3,
        },
      },
      rightPriceScale: {
        borderColor: "rgba(255, 255, 255, 0.08)",
        scaleMargins: { top: 0.12, bottom: 0.2 },
        minimumWidth: 72,
      },
      timeScale: {
        borderColor: "rgba(255, 255, 255, 0.08)",
        timeVisible: true,
        secondsVisible: false,
      },
    });

    chartRef.current = chart;

    const samplePrice =
      initialCandles.length > 0
        ? initialCandles[initialCandles.length - 1].close
        : entryPrice || pocPrice || 1;
    const initialPriceFormat = getPriceFormatOptions(samplePrice);

    // 1. Candlestick Series: price line disabled per user request
    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#10b981",
      downColor: "#ef4444",
      borderUpColor: "#10b981",
      borderDownColor: "#ef4444",
      wickUpColor: "#10b981",
      wickDownColor: "#ef4444",
      priceLineVisible: false,
      lastValueVisible: false,
      priceFormat: initialPriceFormat,
    });
    candleSeriesRef.current = candleSeries;

    // 2. Volume Series
    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "volume_scale",
      visible: showVolume,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    chart.priceScale("volume_scale").applyOptions({
      scaleMargins: { top: 0.82, bottom: 0 },
    });
    volumeSeriesRef.current = volumeSeries;

    // Crosshair listener for legend OHLCV inspection
    chart.subscribeCrosshairMove((param) => {
      if (!isMounted) return;
      if (!param.time || !param.seriesData.get(candleSeries)) {
        if (activeCandlesRef.current.length > 0) {
          const lastCandle = activeCandlesRef.current[activeCandlesRef.current.length - 1];
          setHoveredCandle(lastCandle);
          onCandleChangeRef.current?.(lastCandle);
        }
        return;
      }
      const data = param.seriesData.get(candleSeries) as CandlestickData<Time>;
      if (data) {
        const fullCandle = activeCandlesRef.current.find((c) => c.time === (param.time as number));
        if (fullCandle) {
          setHoveredCandle(fullCandle);
          onCandleChangeRef.current?.(fullCandle);
        }
      }
    });

    // Layout Stabilized ResizeObserver (Zero Jitter / No Feedback Loop)
    const resizeObserver = new ResizeObserver((entries) => {
      if (!entries || entries.length === 0) return;
      const entry = entries[0];
      const newWidth = Math.floor(entry.contentRect.width);
      const newHeight = Math.floor(entry.contentRect.height);

      if (newWidth <= 0 || newHeight <= 0) return;

      if (
        Math.abs(newWidth - lastDimensionsRef.current.width) < 3 &&
        Math.abs(newHeight - lastDimensionsRef.current.height) < 3
      ) {
        return;
      }

      if (resizeRaf) cancelAnimationFrame(resizeRaf);
      resizeRaf = requestAnimationFrame(() => {
        if (chartRef.current && isMounted) {
          lastDimensionsRef.current = { width: newWidth, height: newHeight };
          chartRef.current.applyOptions({
            width: newWidth,
            height: newHeight,
          });
        }
      });
    });

    resizeObserver.observe(container);

    return () => {
      isMounted = false;
      if (resizeRaf) cancelAnimationFrame(resizeRaf);
      resizeObserver.disconnect();
      if (chartRef.current) {
        chartRef.current.remove();
        chartRef.current = null;
      }
      candleSeriesRef.current = null;
      volumeSeriesRef.current = null;
      priceLinesRef.current = [];
    };
  }, [symbol]);

  // --------------------------------------------------------------------------
  // EFFECT 2: Data Fetch & WebSocket Stream for Selected Interval (1H, 4H, 1D, 1W)
  // --------------------------------------------------------------------------
  useEffect(() => {
    let isCurrent = true;

    // Clean up previous socket and polling
    if (wsRef.current) {
      wsRef.current.onopen = null;
      wsRef.current.onmessage = null;
      wsRef.current.onerror = null;
      wsRef.current.onclose = null;
      wsRef.current.close();
      wsRef.current = null;
    }
    if (pollIntervalRef.current) {
      clearInterval(pollIntervalRef.current);
      pollIntervalRef.current = null;
    }

    // eslint-disable-next-line react-hooks/set-state-in-effect
    setConnectionMode("connecting");

    // Push initial baseline if 1h and available
    if (candleInterval === "1h" && initialCandles && initialCandles.length > 0 && candleSeriesRef.current) {
      activeCandlesRef.current = [...initialCandles];
      const formattedCandles: CandlestickData<Time>[] = initialCandles.map((c) => ({
        time: c.time as Time,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
      }));
      candleSeriesRef.current.setData(formattedCandles);

      if (volumeSeriesRef.current) {
        const formattedVol: HistogramData<Time>[] = initialCandles.map((c) => ({
          time: c.time as Time,
          value: c.volume,
          color: c.close >= c.open ? "rgba(16, 185, 129, 0.3)" : "rgba(239, 68, 68, 0.3)",
        }));
        volumeSeriesRef.current.setData(formattedVol);
      }

      const lastInit = initialCandles[initialCandles.length - 1];
      setHoveredCandle(lastInit);
      onCandleChangeRef.current?.(lastInit);
    }

    // Step A: Fetch historical klines for this interval from Binance REST
    const fetchIntervalKlines = async () => {
      try {
        let res = await fetch(
          `https://fapi.binance.com/fapi/v1/klines?symbol=${symbol.toUpperCase()}&interval=${candleInterval}&limit=1000`
        );
        if (!res.ok) {
          res = await fetch(
            `https://api.binance.com/api/v3/klines?symbol=${symbol.toUpperCase()}&interval=${candleInterval}&limit=1000`
          );
        }
        if (res.ok && isCurrent) {
          const raw = await res.json();
          if (Array.isArray(raw) && raw.length > 0) {
            const fetchedCandles: Candle[] = raw.map((item: (number | string)[]) => ({
              time: Math.floor(Number(item[0]) / 1000),
              open: parseFloat(String(item[1])),
              high: parseFloat(String(item[2])),
              low: parseFloat(String(item[3])),
              close: parseFloat(String(item[4])),
              volume: parseFloat(String(item[5])),
            }));

            activeCandlesRef.current = fetchedCandles;

            if (candleSeriesRef.current) {
              const formattedCandles: CandlestickData<Time>[] = fetchedCandles.map((c) => ({
                time: c.time as Time,
                open: c.open,
                high: c.high,
                low: c.low,
                close: c.close,
              }));
              candleSeriesRef.current.setData(formattedCandles);
            }

            if (volumeSeriesRef.current) {
              const formattedVol: HistogramData<Time>[] = fetchedCandles.map((c) => ({
                time: c.time as Time,
                value: c.volume,
                color: c.close >= c.open ? "rgba(16, 185, 129, 0.3)" : "rgba(239, 68, 68, 0.3)",
              }));
              volumeSeriesRef.current.setData(formattedVol);
            }

            const lastFetched = fetchedCandles[fetchedCandles.length - 1];
            setHoveredCandle(lastFetched);
            onCandleChangeRef.current?.(lastFetched);

            // Dynamically update candlestick series priceFormat based on actual fetched prices
            if (lastFetched && candleSeriesRef.current) {
              candleSeriesRef.current.applyOptions({
                priceFormat: getPriceFormatOptions(lastFetched.close),
              });
            }

            // Visible range optimization: for 1d/1w, focus on active structure without squishing years of bars
            if (chartRef.current) {
              if (candleInterval === "1d" || candleInterval === "1w") {
                chartRef.current.timeScale().setVisibleLogicalRange({
                  from: Math.max(0, fetchedCandles.length - 140),
                  to: fetchedCandles.length + 8,
                });
              } else {
                chartRef.current.timeScale().fitContent();
              }
            }

            // Emit latest tick to parent once
            const last = fetchedCandles[fetchedCandles.length - 1];
            if (last && onLiveTickRef.current) {
              onLiveTickRef.current({
                price: last.close,
                high: last.high,
                low: last.low,
                volume: last.volume,
                time: last.time,
              });
            }
          }
        }
      } catch (err) {
        console.warn(`Binance ${candleInterval} klines fetch error:`, err);
      }
    };

    fetchIntervalKlines();

    // Step B: Connect WebSocket for the selected candle interval
    const cleanSym = symbol.toLowerCase();
    const wsUrl = `wss://fstream.binance.com/ws/${cleanSym}@kline_${candleInterval}`;

    const startPollingFallback = () => {
      if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
      setConnectionMode("live_poll");
      pollIntervalRef.current = setInterval(async () => {
        if (!isCurrent) return;
        try {
          const res = await fetch(`https://fapi.binance.com/fapi/v1/ticker/price?symbol=${symbol.toUpperCase()}`);
          if (res.ok) {
            const data = await res.json();
            const curPrice = parseFloat(data.price);
            const buffer = activeCandlesRef.current;
            if (buffer.length > 0 && candleSeriesRef.current) {
              const last = { ...buffer[buffer.length - 1] };
              last.close = curPrice;
              last.high = Math.max(last.high, curPrice);
              last.low = Math.min(last.low, curPrice);
              buffer[buffer.length - 1] = last;

              candleSeriesRef.current.update({
                time: last.time as Time,
                open: last.open,
                high: last.high,
                low: last.low,
                close: last.close,
              });
              setHoveredCandle(last);
              onCandleChangeRef.current?.(last);

              const now = Date.now();
              if (now - lastTickEmitTimeRef.current >= 500) {
                lastTickEmitTimeRef.current = now;
                if (onLiveTickRef.current) {
                  onLiveTickRef.current({
                    price: curPrice,
                    high: last.high,
                    low: last.low,
                    volume: last.volume,
                    time: last.time,
                  });
                }
              }
            }
          }
        } catch (e) {
          // ignore
        }
      }, 2500);
    };

    try {
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        if (!isCurrent) return;
        setConnectionMode("live_ws");
      };

      ws.onmessage = (event) => {
        if (!isCurrent) return;
        try {
          const payload = JSON.parse(event.data);
          if (payload.e === "kline" && payload.k) {
            const k = payload.k;
            const kTime = Math.floor(k.t / 1000) as Time;
            const curClose = parseFloat(k.c);
            const curOpen = parseFloat(k.o);
            const curHigh = parseFloat(k.h);
            const curLow = parseFloat(k.l);
            const curVol = parseFloat(k.v);

            const updatedCandle: Candle = {
              time: kTime as number,
              open: curOpen,
              high: curHigh,
              low: curLow,
              close: curClose,
              volume: curVol,
            };

            // Update candlestick series
            candleSeriesRef.current?.update({
              time: kTime,
              open: curOpen,
              high: curHigh,
              low: curLow,
              close: curClose,
            });

            // Update volume series
            volumeSeriesRef.current?.update({
              time: kTime,
              value: curVol,
              color: curClose >= curOpen ? "rgba(16, 185, 129, 0.4)" : "rgba(239, 68, 68, 0.4)",
            });

            // Update legend and parent
            setHoveredCandle(updatedCandle);
            onCandleChangeRef.current?.(updatedCandle);

            // Update in-memory candle buffer
            const buffer = activeCandlesRef.current;
            const lastIdx = buffer.length - 1;
            if (lastIdx >= 0 && buffer[lastIdx].time === updatedCandle.time) {
              buffer[lastIdx] = updatedCandle;
            } else if (lastIdx >= 0 && updatedCandle.time > buffer[lastIdx].time) {
              buffer.push(updatedCandle);
            }

            // Throttled notification to parent component (max once every 500ms)
            const now = Date.now();
            if (now - lastTickEmitTimeRef.current >= 500) {
              lastTickEmitTimeRef.current = now;
              if (onLiveTickRef.current) {
                onLiveTickRef.current({
                  price: curClose,
                  high: curHigh,
                  low: curLow,
                  volume: curVol,
                  time: kTime as number,
                });
              }
            }
          }
        } catch (parseErr) {
          console.warn("WS parse error:", parseErr);
        }
      };

      ws.onerror = () => {
        if (isCurrent) {
          startPollingFallback();
        }
      };

      ws.onclose = () => {
        if (isCurrent && connectionMode !== "live_poll") {
          startPollingFallback();
        }
      };
    } catch (wsInitErr) {
      console.warn("WebSocket init error, falling back to polling:", wsInitErr);
      startPollingFallback();
    }

    return () => {
      isCurrent = false;
      if (wsRef.current) {
        wsRef.current.onopen = null;
        wsRef.current.onmessage = null;
        wsRef.current.onerror = null;
        wsRef.current.onclose = null;
        wsRef.current.close();
        wsRef.current = null;
      }
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current);
        pollIntervalRef.current = null;
      }
    };
  }, [symbol, candleInterval]);

  // --------------------------------------------------------------------------
  // EFFECT 3: Price Lines (POC, VAH, VAL, Entry, Stop, Targets)
  // --------------------------------------------------------------------------
  const targetsKey = (targets || []).join(",");
  useEffect(() => {
    const series = candleSeriesRef.current;
    if (!series) return;

    // Clear previous price lines
    priceLinesRef.current.forEach((line) => {
      try {
        series.removePriceLine(line);
      } catch {}
    });
    priceLinesRef.current = [];

    const candles = activeCandlesRef.current;
    const lastCandle = candles.length > 0 ? candles[candles.length - 1] : null;
    const currentPrice = lastCandle ? lastCandle.close : (entryPrice || 0);

    // Compute live Volume Profile over last 30 days of loaded candles
    const computedVP = computeVolumeProfile(candles);

    // Check if prop POC is disconnected (> 25% discrepancy from active price action) or missing
    const isPocDisconnected = !pocPrice || (currentPrice > 0 && Math.abs(pocPrice - currentPrice) / currentPrice > 0.25);
    const effectivePoc = (!isPocDisconnected && pocPrice) ? pocPrice : (computedVP?.pocPrice ?? pocPrice ?? currentPrice);
    const effectiveVah = (!isPocDisconnected && vahPrice) ? vahPrice : (computedVP?.vahPrice ?? vahPrice ?? (effectivePoc ? effectivePoc * 1.03 : currentPrice * 1.03));
    const effectiveVal = (!isPocDisconnected && valPrice) ? valPrice : (computedVP?.valPrice ?? valPrice ?? (effectivePoc ? effectivePoc * 0.97 : currentPrice * 0.97));

    // Check if execution bracket/targets are disconnected (> 25% discrepancy from active price action)
    const isBracketDisconnected = currentPrice > 0 && entryPrice && (Math.abs(entryPrice - currentPrice) / currentPrice > 0.25);
    const scaleFactor = (isBracketDisconnected && entryPrice) ? (currentPrice / entryPrice) : 1;

    const effectiveEntry = entryPrice ? Number((entryPrice * scaleFactor).toFixed(6)) : currentPrice;
    const effectiveStop = stopPrice ? Number((stopPrice * scaleFactor).toFixed(6)) : Number((currentPrice * 0.95).toFixed(6));
    const effectiveTrigger = breakoutTrigger ? Number((breakoutTrigger * scaleFactor).toFixed(6)) : Number((currentPrice * 1.03).toFixed(6));
    const effectiveTargets = (targets && targets.length > 0)
      ? targets.map((t) => Number((t * scaleFactor).toFixed(6)))
      : [Number((currentPrice * 1.08).toFixed(6)), Number((currentPrice * 1.15).toFixed(6)), Number((currentPrice * 1.25).toFixed(6))];

    // Emit live adapted levels back to parent so VerificationPanel and Header stay perfectly synced
    if (effectivePoc && onLevelsUpdatedRef.current) {
      onLevelsUpdatedRef.current({
        pocPrice: effectivePoc,
        vahPrice: effectiveVah,
        valPrice: effectiveVal,
        entryPrice: effectiveEntry,
        stopPrice: effectiveStop,
        breakoutTrigger: effectiveTrigger,
        targets: effectiveTargets,
      });
    }

    // Structure Levels (POC, VAH, VAL) strictly adhering to #f49d0d & white
    if (showLevels) {
      if (effectivePoc && effectivePoc > 0) {
        priceLinesRef.current.push(
          series.createPriceLine({
            price: effectivePoc,
            color: "#f49d0d",
            lineWidth: 2,
            lineStyle: 0,
            axisLabelVisible: true,
            title: "30d POC",
          })
        );
      }
      if (effectiveVah && effectiveVah > 0) {
        priceLinesRef.current.push(
          series.createPriceLine({
            price: effectiveVah,
            color: "#ffffff",
            lineWidth: 1,
            lineStyle: 2,
            axisLabelVisible: true,
            title: "VAH",
          })
        );
      }
      if (effectiveVal && effectiveVal > 0) {
        priceLinesRef.current.push(
          series.createPriceLine({
            price: effectiveVal,
            color: "rgba(255, 255, 255, 0.5)",
            lineWidth: 1,
            lineStyle: 2,
            axisLabelVisible: true,
            title: "VAL",
          })
        );
      }
    }

    // Risk & Target Brackets (Stop, Entry, Targets)
    if (showTargets) {
      if (effectiveStop && effectiveStop > 0) {
        priceLinesRef.current.push(
          series.createPriceLine({
            price: effectiveStop,
            color: "#ef4444",
            lineWidth: 2,
            lineStyle: 2,
            axisLabelVisible: true,
            title: "STOP LOSS",
          })
        );
      }
      if (effectiveEntry && effectiveEntry > 0) {
        priceLinesRef.current.push(
          series.createPriceLine({
            price: effectiveEntry,
            color: "#ffffff",
            lineWidth: 1,
            lineStyle: 3,
            axisLabelVisible: true,
            title: "LIMIT ENTRY",
          })
        );
      }
      if (effectiveTrigger && effectiveTrigger > 0) {
        priceLinesRef.current.push(
          series.createPriceLine({
            price: effectiveTrigger,
            color: "#f49d0d",
            lineWidth: 1,
            lineStyle: 2,
            axisLabelVisible: true,
            title: direction === "short" ? "BREAKDOWN TRIGGER" : "BREAKOUT TRIGGER",
          })
        );
      }
      (effectiveTargets || []).forEach((t, idx) => {
        if (t > 0) {
          priceLinesRef.current.push(
            series.createPriceLine({
              price: t,
              color: "#10b981",
              lineWidth: 1,
              lineStyle: 2,
              axisLabelVisible: true,
              title: `TARGET ${idx + 1}`,
            })
          );
        }
      });
    }
  }, [
    symbol,
    pocPrice,
    vahPrice,
    valPrice,
    entryPrice,
    breakoutTrigger,
    stopPrice,
    targetsKey,
    showLevels,
    showTargets,
    direction,
    candleInterval,
  ]);

  // --------------------------------------------------------------------------
  // EFFECT 4: Toggle Series Visibility (Instantaneous, No Chart Reload)
  // --------------------------------------------------------------------------
  useEffect(() => {
    volumeSeriesRef.current?.applyOptions({ visible: showVolume });
  }, [showVolume]);

  const isZenMode = !showLeftSidebar && !showRightPanel;

  return (
    <div
      style={{
        position: "relative",
        width: "100%",
        height: "100%",
        minWidth: 0,
        minHeight: 0,
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
      }}
    >
      {/* Top Chart Toolbar */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "8px 14px",
          background: "rgba(11, 15, 25, 0.95)",
          borderBottom: "1px solid var(--border-subtle)",
          zIndex: 10,
          flexShrink: 0,
          gap: "12px",
        }}
      >
        {/* Left: Left Sidebar Toggle + Symbol + Live Dot + OHLCV Legend */}
        <div style={{ display: "flex", alignItems: "center", gap: "10px", fontFamily: "var(--font-mono)", fontSize: "12px", overflow: "hidden" }}>
          {/* Toggle Left Sidebar Button */}
          {onToggleLeftSidebar && (
            <button
              type="button"
              onClick={onToggleLeftSidebar}
              title={showLeftSidebar ? "Hide Candidates Sidebar" : "Show Candidates Sidebar"}
              style={{
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                background: showLeftSidebar ? "rgba(255, 255, 255, 0.05)" : "rgba(244, 157, 13, 0.15)",
                color: showLeftSidebar ? "var(--text-secondary)" : "#f49d0d",
                border: `1px solid ${showLeftSidebar ? "var(--border-subtle)" : "rgba(244, 157, 13, 0.35)"}`,
                borderRadius: "6px",
                width: "28px",
                height: "28px",
                cursor: "pointer",
                transition: "all 0.15s ease",
                flexShrink: 0,
              }}
            >
              {showLeftSidebar ? <PanelLeftClose size={15} /> : <PanelLeftOpen size={15} />}
            </button>
          )}

          {/* Symbol */}
          <span style={{ fontWeight: 800, color: "var(--text-primary)", fontSize: "14px", letterSpacing: "0.02em" }}>
            {symbol}
          </span>

          {/* Clean Live Status: Just Green Pulsing Dot (No Text) */}
          <span
            title={connectionMode === "live_ws" ? "Live Binance WebSocket Stream (Ticking)" : "Live Price Sync Active"}
            style={{
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              width: "16px",
              height: "16px",
              borderRadius: "50%",
              background: connectionMode === "live_ws" ? "rgba(16, 185, 129, 0.18)" : "rgba(244, 157, 13, 0.18)",
              cursor: "help",
              flexShrink: 0,
            }}
          >
            <span
              style={{
                width: "7px",
                height: "7px",
                borderRadius: "50%",
                background: connectionMode === "live_ws" ? "#10b981" : "#f49d0d",
                boxShadow: connectionMode === "live_ws" ? "0 0 8px #10b981" : "0 0 8px #f49d0d",
              }}
              className="pulse-indicator"
            />
          </span>
        </div>

        {/* Right: Candle Interval Switch + Overlays + Pane Toggles */}
        <div style={{ display: "flex", alignItems: "center", gap: "10px", flexShrink: 0 }}>
          {/* Candle Interval Buttons: 1H, 4H, 1D, 1W */}
          <div style={{ display: "flex", gap: "3px", background: "rgba(255, 255, 255, 0.05)", padding: "2px", borderRadius: "8px" }}>
            {(["1h", "4h", "1d", "1w"] as const).map((intv) => (
              <button
                key={intv}
                type="button"
                onClick={() => handleChangeInterval(intv)}
                title={`${intv.toUpperCase()} Candlestick View`}
                style={{
                  background: candleInterval === intv ? "#f49d0d" : "transparent",
                  color: candleInterval === intv ? "#07090e" : "var(--text-secondary)",
                  border: "none",
                  borderRadius: "6px",
                  padding: "4px 9px",
                  fontSize: "11px",
                  fontWeight: 700,
                  cursor: "pointer",
                  transition: "all 0.15s ease",
                }}
              >
                {intv.toUpperCase()}
              </button>
            ))}
          </div>

          <div style={{ height: "16px", width: "1px", background: "var(--border-subtle)" }} />

          {/* Indicator Toggles */}
          <div style={{ display: "flex", gap: "5px" }}>
            <button
              type="button"
              onClick={handleToggleLevels}
              style={{
                background: showLevels ? "rgba(244, 157, 13, 0.15)" : "rgba(255, 255, 255, 0.04)",
                color: showLevels ? "#f49d0d" : "var(--text-muted)",
                border: `1px solid ${showLevels ? "rgba(244, 157, 13, 0.35)" : "transparent"}`,
                borderRadius: "6px",
                padding: "4px 8px",
                fontSize: "11px",
                fontWeight: 600,
                cursor: "pointer",
              }}
            >
              POC/Levels
            </button>
            <button
              type="button"
              onClick={handleToggleTargets}
              style={{
                background: showTargets ? "rgba(16, 185, 129, 0.15)" : "rgba(255, 255, 255, 0.04)",
                color: showTargets ? "var(--accent-emerald)" : "var(--text-muted)",
                border: `1px solid ${showTargets ? "rgba(16, 185, 129, 0.35)" : "transparent"}`,
                borderRadius: "6px",
                padding: "4px 8px",
                fontSize: "11px",
                fontWeight: 600,
                cursor: "pointer",
              }}
            >
              Targets
            </button>
            <button
              type="button"
              onClick={handleToggleVolume}
              style={{
                background: showVolume ? "rgba(255, 255, 255, 0.12)" : "rgba(255, 255, 255, 0.04)",
                color: showVolume ? "#ffffff" : "var(--text-muted)",
                border: `1px solid ${showVolume ? "rgba(255, 255, 255, 0.25)" : "transparent"}`,
                borderRadius: "6px",
                padding: "4px 8px",
                fontSize: "11px",
                fontWeight: 600,
                cursor: "pointer",
              }}
            >
              Vol
            </button>
          </div>

          <div style={{ height: "16px", width: "1px", background: "var(--border-subtle)" }} />

          {/* Full Screen Chart Zen Mode & Right Panel Toggle */}
          <div style={{ display: "flex", gap: "5px" }}>
            {onToggleLeftSidebar && onToggleRightPanel && (
              <button
                type="button"
                onClick={() => {
                  if (showLeftSidebar || showRightPanel) {
                    if (showLeftSidebar) onToggleLeftSidebar();
                    if (showRightPanel) onToggleRightPanel();
                  } else {
                    onToggleLeftSidebar();
                    onToggleRightPanel();
                  }
                }}
                title={isZenMode ? "Restore Sidebars" : "Full Chart Focus (Hide Both Sidebars)"}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  justifyContent: "center",
                  background: isZenMode ? "rgba(244, 157, 13, 0.2)" : "rgba(255, 255, 255, 0.05)",
                  color: isZenMode ? "#f49d0d" : "var(--text-secondary)",
                  border: `1px solid ${isZenMode ? "rgba(244, 157, 13, 0.4)" : "var(--border-subtle)"}`,
                  borderRadius: "6px",
                  width: "28px",
                  height: "28px",
                  cursor: "pointer",
                  transition: "all 0.15s ease",
                }}
              >
                {isZenMode ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
              </button>
            )}

            {onToggleRightPanel && (
              <button
                type="button"
                onClick={onToggleRightPanel}
                title={showRightPanel ? "Hide Verification Panel" : "Show Verification Panel"}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  justifyContent: "center",
                  background: showRightPanel ? "rgba(255, 255, 255, 0.05)" : "rgba(244, 157, 13, 0.15)",
                  color: showRightPanel ? "var(--text-secondary)" : "#f49d0d",
                  border: `1px solid ${showRightPanel ? "var(--border-subtle)" : "rgba(244, 157, 13, 0.35)"}`,
                  borderRadius: "6px",
                  width: "28px",
                  height: "28px",
                  cursor: "pointer",
                  transition: "all 0.15s ease",
                }}
              >
                {showRightPanel ? <PanelRightClose size={15} /> : <PanelRightOpen size={15} />}
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Chart Canvas: Layout-contained to eliminate flex vibration */}
      <div
        ref={chartContainerRef}
        style={{
          flex: 1,
          width: "100%",
          height: "100%",
          minWidth: 0,
          minHeight: 0,
          position: "relative",
          overflow: "hidden",
          contain: "layout size",
        }}
      />
    </div>
  );
}

export default memo(TradingChart);
