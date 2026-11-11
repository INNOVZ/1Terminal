"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Radar, BookmarkCheck, Globe2 } from "lucide-react";
import { memo } from "react";

interface BottomDockProps {
  watchlistCount?: number;
  onRefresh?: () => void;
  isRefreshing?: boolean;
}

function BottomDock({
  watchlistCount = 5,
}: BottomDockProps) {
  const pathname = usePathname();

  return (
    <div
      className="floating-dock-container"
      role="navigation"
      aria-label="Quick App Navigation"
    >
      <nav className="floating-dock">
        {/* Radar Screener */}
        <Link
          href="/"
          className={`dock-item ${pathname === "/" ? "active" : ""}`}
          id="dock-radar"
          aria-label="Radar Screener Dashboard"
        >
          <Radar size={18} strokeWidth={1} />
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
          <BookmarkCheck size={18} strokeWidth={1} />
          {watchlistCount > 0 && <span></span>}
          {pathname === "/watchlist" && <span className="dock-active-dot" />}
          <span className="dock-tooltip">Watchlist</span>
        </Link>

        {/* Macro Regime & Metrics */}
        <Link
          href="/macro"
          className={`dock-item ${pathname === "/macro" ? "active" : ""}`}
          id="dock-macro"
          aria-label="Macro Intelligence"
        >
          <Globe2 size={18} strokeWidth={1} />
          {pathname === "/macro" && <span className="dock-active-dot" />}
          <span className="dock-tooltip">Macro & Liquidity</span>
        </Link>
      </nav>
    </div>
  );
}

export default memo(BottomDock);
