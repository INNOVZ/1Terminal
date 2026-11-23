"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  Radar,
  BookmarkCheck,
  Globe2,
  User as UserIcon,
  LogIn,
  LogOut,
  ShieldCheck,
} from "lucide-react";
import { memo, useEffect, useState, useRef, useCallback } from "react";
import type { User } from "@supabase/supabase-js";
import { getSupabaseClient } from "../lib/supabaseClient";
import SupabaseAuthPanel from "../watchlist/components/SupabaseAuthPanel";

interface BottomDockProps {
  watchlistCount?: number;
  onRefresh?: () => void;
  isRefreshing?: boolean;
}

function BottomDock({ watchlistCount: initialCount = 0 }: BottomDockProps) {
  const pathname = usePathname();
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [userWatchlistCount, setUserWatchlistCount] = useState<number>(initialCount);
  const [menuOpen, setMenuOpen] = useState(false);
  const [authModalOpen, setAuthModalOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);

  const refreshWatchlistCount = useCallback(async (uid: string | undefined) => {
    if (!uid) {
      setUserWatchlistCount(0);
      return;
    }
    try {
      const supabase = getSupabaseClient();
      const { count } = await supabase
        .from("user_watchlists")
        .select("*", { count: "exact", head: true })
        .eq("user_id", uid);
      setUserWatchlistCount(count ?? 0);
    } catch {
      // ignore count error
    }
  }, []);

  useEffect(() => {
    const supabase = getSupabaseClient();
    let mounted = true;

    supabase.auth.getSession().then(({ data }) => {
      if (!mounted) return;
      const currentUser = data.session?.user ?? null;
      setUser(currentUser);
      void refreshWatchlistCount(currentUser?.id);
    });

    const { data: listener } = supabase.auth.onAuthStateChange((_event, session) => {
      if (!mounted) return;
      const currentUser = session?.user ?? null;
      setUser(currentUser);
      void refreshWatchlistCount(currentUser?.id);
    });

    const handleWatchlistUpdated = () => {
      void supabase.auth.getSession().then(({ data }) => {
        if (mounted) void refreshWatchlistCount(data.session?.user?.id);
      });
    };
    const handleOpenAuthModal = () => {
      setMenuOpen(false);
      setAuthModalOpen(true);
    };
    window.addEventListener("atlas:watchlist-updated", handleWatchlistUpdated);
    window.addEventListener("atlas:supabase-auth-required", handleOpenAuthModal);

    return () => {
      mounted = false;
      listener.subscription.unsubscribe();
      window.removeEventListener("atlas:watchlist-updated", handleWatchlistUpdated);
      window.removeEventListener("atlas:supabase-auth-required", handleOpenAuthModal);
    };
  }, [refreshWatchlistCount]);

  // Close popover when clicking outside
  useEffect(() => {
    if (!menuOpen) return;
    const handleOutsideClick = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", handleOutsideClick);
    return () => document.removeEventListener("mousedown", handleOutsideClick);
  }, [menuOpen]);

  const handleSignOut = useCallback(async () => {
    const supabase = getSupabaseClient();
    await supabase.auth.signOut();
    setUser(null);
    setUserWatchlistCount(0);
    setMenuOpen(false);
  }, []);

  return (
    <>
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
            <Radar size={18} strokeWidth={1.5} />
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
            <BookmarkCheck size={18} strokeWidth={1.5} />
            {user && userWatchlistCount > 0 && (
              <span
                style={{
                  position: "absolute",
                  top: "6px",
                  right: "6px",
                  minWidth: "16px",
                  height: "16px",
                  borderRadius: "8px",
                  background: "#f49d0d",
                  color: "#07090e",
                  fontSize: "10px",
                  fontWeight: 800,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  padding: "0 4px",
                  fontFamily: "var(--font-mono)",
                }}
              >
                {userWatchlistCount}
              </span>
            )}
            {pathname === "/watchlist" && <span className="dock-active-dot" />}
            <span className="dock-tooltip">
              {user ? `My Watchlist (${userWatchlistCount})` : "Watchlist (Sign In)"}
            </span>
          </Link>

          {/* Macro Regime & Metrics */}
          <Link
            href="/macro"
            className={`dock-item ${pathname === "/macro" ? "active" : ""}`}
            id="dock-macro"
            aria-label="Macro Intelligence"
          >
            <Globe2 size={18} strokeWidth={1.5} />
            {pathname === "/macro" && <span className="dock-active-dot" />}
            <span className="dock-tooltip">Macro &amp; Liquidity</span>
          </Link>

          <div className="dock-separator" />

          {/* User Account / Login Menu Item in Bottom Dock */}
          <div ref={menuRef} style={{ position: "relative" }}>
            <button
              type="button"
              id="dock-user-menu"
              aria-label={user ? "User Account Menu" : "Sign In"}
              onClick={() => setMenuOpen((prev) => !prev)}
              className={`dock-item ${menuOpen || user ? "active" : ""}`}
              style={{
                borderColor: user
                  ? "rgba(16, 185, 129, 0.4)"
                  : menuOpen
                    ? "rgba(244, 157, 13, 0.4)"
                    : undefined,
                background: user
                  ? "rgba(16, 185, 129, 0.12)"
                  : menuOpen
                    ? "rgba(244, 157, 13, 0.12)"
                    : undefined,
                color: user ? "var(--accent-emerald)" : undefined,
              }}
            >
              <UserIcon size={18} strokeWidth={1.75} />
              <span
                style={{
                  position: "absolute",
                  top: "8px",
                  right: "8px",
                  width: "7px",
                  height: "7px",
                  borderRadius: "50%",
                  background: user ? "#10b981" : "rgba(255, 255, 255, 0.3)",
                  boxShadow: user ? "0 0 6px rgba(16, 185, 129, 0.8)" : "none",
                }}
              />
              <span className="dock-tooltip">
                {user ? user.email || "Account" : "Sign In / Account"}
              </span>
            </button>

            {/* Popover User Menu above the Bottom Dock */}
            {menuOpen && (
              <div
                className="glass-panel"
                style={{
                  position: "absolute",
                  bottom: "68px",
                  right: 0,
                  width: "260px",
                  padding: "14px",
                  background: "rgba(11, 15, 25, 0.97)",
                  border: "1px solid var(--border-medium)",
                  borderRadius: "14px",
                  boxShadow: "0 16px 40px rgba(0, 0, 0, 0.65)",
                  display: "flex",
                  flexDirection: "column",
                  gap: "10px",
                  zIndex: 10001,
                }}
              >
                {user ? (
                  <>
                    <div
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: "10px",
                        paddingBottom: "10px",
                        borderBottom: "1px solid var(--border-subtle)",
                      }}
                    >
                      <div
                        style={{
                          width: "34px",
                          height: "34px",
                          borderRadius: "10px",
                          background: "rgba(16, 185, 129, 0.15)",
                          border: "1px solid rgba(16, 185, 129, 0.35)",
                          color: "#10b981",
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                          fontWeight: 800,
                          fontSize: "13px",
                          flexShrink: 0,
                        }}
                      >
                        {(user.email?.[0] || "U").toUpperCase()}
                      </div>
                      <div style={{ minWidth: 0, flex: 1 }}>
                        <div
                          style={{
                            fontSize: "12px",
                            fontWeight: 800,
                            color: "var(--text-primary)",
                            whiteSpace: "nowrap",
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                          }}
                          title={user.email || user.id}
                        >
                          {user.email || "Authenticated Trader"}
                        </div>
                        <div
                          style={{
                            fontSize: "10.5px",
                            color: "#10b981",
                            display: "flex",
                            alignItems: "center",
                            gap: "4px",
                            marginTop: "2px",
                            fontWeight: 700,
                          }}
                        >
                          <ShieldCheck size={11} /> Personal RLS Workspace
                        </div>
                      </div>
                    </div>

                    <button
                      type="button"
                      onClick={() => {
                        setMenuOpen(false);
                        router.push("/watchlist");
                      }}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "space-between",
                        width: "100%",
                        padding: "9px 12px",
                        borderRadius: "8px",
                        background: "rgba(244, 157, 13, 0.12)",
                        border: "1px solid rgba(244, 157, 13, 0.3)",
                        color: "#f49d0d",
                        fontSize: "12px",
                        fontWeight: 700,
                        cursor: "pointer",
                      }}
                    >
                      <span style={{ display: "inline-flex", alignItems: "center", gap: "7px" }}>
                        <BookmarkCheck size={14} /> My Watchlist
                      </span>
                      <span
                        style={{
                          fontFamily: "var(--font-mono)",
                          fontSize: "11px",
                          background: "rgba(0,0,0,0.3)",
                          padding: "1px 6px",
                          borderRadius: "6px",
                        }}
                      >
                        {userWatchlistCount}
                      </span>
                    </button>

                    <button
                      type="button"
                      onClick={handleSignOut}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: "7px",
                        width: "100%",
                        padding: "8px 12px",
                        borderRadius: "8px",
                        background: "rgba(239, 68, 68, 0.08)",
                        border: "1px solid rgba(239, 68, 68, 0.25)",
                        color: "#f87171",
                        fontSize: "12px",
                        fontWeight: 700,
                        cursor: "pointer",
                      }}
                    >
                      <LogOut size={14} /> Sign Out
                    </button>
                  </>
                ) : (
                  <>
                    <div>
                      <div style={{ fontSize: "13px", fontWeight: 800, color: "var(--text-primary)" }}>
                        Personal Watchlist Account
                      </div>
                      <div
                        style={{
                          fontSize: "11px",
                          color: "var(--text-secondary)",
                          marginTop: "3px",
                          lineHeight: 1.4,
                        }}
                      >
                        Sign in or create an account to save your own private tokens and bracket levels.
                      </div>
                    </div>

                    <button
                      type="button"
                      onClick={() => {
                        setMenuOpen(false);
                        setAuthModalOpen(true);
                      }}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        gap: "7px",
                        width: "100%",
                        padding: "9px 12px",
                        borderRadius: "8px",
                        background: "#f49d0d",
                        border: "none",
                        color: "#07090e",
                        fontSize: "12px",
                        fontWeight: 800,
                        cursor: "pointer",
                      }}
                    >
                      <LogIn size={14} /> Sign In / Create Account
                    </button>
                  </>
                )}
              </div>
            )}
          </div>
        </nav>
      </div>

      {authModalOpen && (
        <SupabaseAuthPanel
          isModal={true}
          onClose={() => setAuthModalOpen(false)}
          onSuccess={() => {
            setAuthModalOpen(false);
          }}
        />
      )}
    </>
  );
}

export default memo(BottomDock);
