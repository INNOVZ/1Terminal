"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Radar, BookmarkCheck, Globe2, RefreshCw } from "lucide-react";
import { useState, useEffect, useCallback, memo } from "react";

interface BottomDockProps {
  watchlistCount?: number;
  onRefresh?: () => void;
  isRefreshing?: boolean;
}

function BottomDock({
  watchlistCount = 5,
  onRefresh,
  isRefreshing = false,
}: BottomDockProps) {
  const pathname = usePathname();
  const [internalRefreshing, setInternalRefreshing] = useState(false);
  const [cronInfo, setCronInfo] = useState<string>("Auto-Scan: Every 1h");

  useEffect(() => {
    fetch("/api/screener/cron/status")
      .then((res) => res.json())
      .then((data) => {
        if (data && data.next_run) {
          const nextDate = new Date(data.next_run);
          const timeStr = nextDate.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
          const interval = data.interval_hours || 1;
          setCronInfo(`Auto-Scan: Every ${interval}h (Next: ${timeStr})`);
        }
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    const handleScanStart = () => setInternalRefreshing(true);
    const handleScanEnd = () => setInternalRefreshing(false);

    window.addEventListener("atlas:scan-start", handleScanStart);
    window.addEventListener("atlas:scan-complete", handleScanEnd);
    window.addEventListener("atlas:scan-error", handleScanEnd);

    return () => {
      window.removeEventListener("atlas:scan-start", handleScanStart);
      window.removeEventListener("atlas:scan-complete", handleScanEnd);
      window.removeEventListener("atlas:scan-error", handleScanEnd);
    };
  }, []);

  const handleRefreshClick = useCallback(async () => {
    if (onRefresh) {
      onRefresh();
      return;
    }

    // Dispatch trigger event to let active page (like Radar) handle in-place update
    const evt = new CustomEvent("atlas:trigger-rescan", { cancelable: true });
    const dispatched = window.dispatchEvent(evt);

    // If event was default-prevented, the active page is handling the refresh in-place
    if (!dispatched) {
      return;
    }

    // Fallback if no page listener handled it (e.g. user is on /watchlist or /macro)
    setInternalRefreshing(true);
    window.dispatchEvent(new CustomEvent("atlas:scan-start"));
    try {
      const res = await fetch("/api/screener/live?refresh=true").catch(() => null);
      if (res && res.ok) {
        const data = await res.json();
        window.dispatchEvent(
          new CustomEvent("atlas:scan-complete", {
            detail: { count: data.qualifying_count, timestamp: data.timestamp },
          })
        );
      } else {
        window.dispatchEvent(new CustomEvent("atlas:scan-error"));
      }
    } catch {
      window.dispatchEvent(new CustomEvent("atlas:scan-error"));
    } finally {
      setInternalRefreshing(false);
    }
  }, [onRefresh]);

  const refreshing = isRefreshing || internalRefreshing;

  return (
    <div className="floating-dock-container" role="navigation" aria-label="Quick App Navigation">
      <nav className="floating-dock">
        {/* Radar Screener */}
        <Link
          href="/"
          className={`dock-item ${pathname === "/" ? "active" : ""}`}
          id="dock-radar"
          aria-label="Radar Screener Dashboard"
        >
          <Radar size={22} strokeWidth={2} />
          {pathname === "/" && <span className="dock-active-dot" />}
          <span className="dock-tooltip">Pre-Breakout Radar</span>
        </Link>

        {/* Watchlist Portal for Coming Days */}
        <Link
          href="/watchlist"
          className={`dock-item ${pathname === "/watchlist" ? "active" : ""}`}
          id="dock-watchlist"
          aria-label="Multi-Day Watchlist"
        >
          <BookmarkCheck size={22} strokeWidth={2} />
          {watchlistCount > 0 && (
            <span
              style={{
                position: "absolute",
                top: "4px",
                right: "4px",
                background: "#f49d0d",
                color: "#07090e",
                borderRadius: "10px",
                fontSize: "9px",
                fontWeight: 800,
                padding: "1px 5px",
                lineHeight: "1.2",
              }}
            >
              {watchlistCount}
            </span>
          )}
          {pathname === "/watchlist" && <span className="dock-active-dot" />}
          <span className="dock-tooltip">Watchlist (Coming Days)</span>
        </Link>

        {/* Macro Regime & Metrics */}
        <Link
          href="/macro"
          className={`dock-item ${pathname === "/macro" ? "active" : ""}`}
          id="dock-macro"
          aria-label="Macro Intelligence"
        >
          <Globe2 size={22} strokeWidth={2} />
          {pathname === "/macro" && <span className="dock-active-dot" />}
          <span className="dock-tooltip">Macro & Liquidity</span>
        </Link>

        <div className="dock-separator" />

        {/* Live Rescan Button */}
        <button
          type="button"
          onClick={handleRefreshClick}
          disabled={refreshing}
          className={`dock-item ${refreshing ? "active" : ""}`}
          id="dock-rescan"
          aria-label="Rescan Binance USD-M Perpetual Contracts"
          style={{ background: "transparent" }}
        >
          <RefreshCw
            size={20}
            strokeWidth={2}
            className={refreshing ? "animate-spin" : ""}
            style={{
              animation: refreshing ? "spin 1s linear infinite" : undefined,
            }}
          />
          <span className="dock-tooltip">
            {refreshing ? "Scanning Liquid Futures Universe..." : `Deep Rescan • ${cronInfo}`}
          </span>
        </button>
      </nav>
    </div>
  );
}

export default memo(BottomDock);
