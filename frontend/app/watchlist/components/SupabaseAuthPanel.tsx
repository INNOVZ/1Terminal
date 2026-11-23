"use client";

import { useState, useCallback } from "react";
import { Lock, Mail, KeyRound, Sparkles, ArrowRight, CheckCircle2, AlertCircle, X } from "lucide-react";
import { getSupabaseClient } from "../../lib/supabaseClient";

interface SupabaseAuthPanelProps {
  onSuccess?: () => void;
  onClose?: () => void;
  isModal?: boolean;
}

export default function SupabaseAuthPanel({
  onSuccess,
  onClose,
  isModal = false,
}: SupabaseAuthPanelProps) {
  const [mode, setMode] = useState<"signin" | "signup" | "magic">("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [infoMessage, setInfoMessage] = useState<string | null>(null);

  const handleSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      if (!email.trim() || submitting) return;
      setSubmitting(true);
      setErrorMessage(null);
      setInfoMessage(null);
      const supabase = getSupabaseClient();

      try {
        if (mode === "magic") {
          const { error } = await supabase.auth.signInWithOtp({
            email: email.trim(),
            options: {
              emailRedirectTo:
                typeof window !== "undefined" ? `${window.location.origin}/watchlist` : undefined,
            },
          });
          if (error) {
            setErrorMessage(error.message);
          } else {
            setInfoMessage("Magic sign-in link sent! Check your email inbox to access your watchlist.");
          }
        } else if (mode === "signup") {
          if (password.length < 6) {
            setErrorMessage("Password must be at least 6 characters.");
            return;
          }
          const { data, error } = await supabase.auth.signUp({
            email: email.trim(),
            password,
            options: {
              emailRedirectTo:
                typeof window !== "undefined" ? `${window.location.origin}/watchlist` : undefined,
            },
          });
          if (error) {
            setErrorMessage(error.message);
          } else if (data.session) {
            onSuccess?.();
          } else {
            setInfoMessage(
              "Account created! If email confirmation is enabled, please check your inbox to confirm your account, or switch to Sign In.",
            );
          }
        } else {
          const { data, error } = await supabase.auth.signInWithPassword({
            email: email.trim(),
            password,
          });
          if (error) {
            setErrorMessage(error.message);
          } else if (data.session) {
            onSuccess?.();
          }
        }
      } catch (err) {
        setErrorMessage(err instanceof Error ? err.message : "Authentication failed");
      } finally {
        setSubmitting(false);
      }
    },
    [email, password, mode, submitting, onSuccess],
  );

  return (
    <div
      style={
        isModal
          ? {
              position: "fixed",
              inset: 0,
              zIndex: 100000,
              background: "rgba(0, 0, 0, 0.72)",
              backdropFilter: "blur(8px)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              padding: "20px",
            }
          : {
              minHeight: "75vh",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              padding: "24px",
            }
      }
      onClick={isModal ? onClose : undefined}
    >
      <div
        className="glass-panel"
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "100%",
          maxWidth: "440px",
          padding: "32px 28px",
          border: "1px solid rgba(244, 157, 13, 0.3)",
          background: "rgba(11, 15, 25, 0.96)",
          boxShadow: "0 20px 50px rgba(0, 0, 0, 0.6)",
          position: "relative",
        }}
      >
        {onClose && (
          <button
            type="button"
            onClick={onClose}
            aria-label="Close login modal"
            style={{
              position: "absolute",
              top: "16px",
              right: "16px",
              background: "rgba(255, 255, 255, 0.05)",
              border: "1px solid var(--border-subtle)",
              borderRadius: "8px",
              width: "30px",
              height: "30px",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "var(--text-secondary)",
              cursor: "pointer",
            }}
          >
            <X size={15} />
          </button>
        )}
        <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "10px", paddingRight: onClose ? "28px" : 0 }}>
          <div
            style={{
              width: "38px",
              height: "38px",
              borderRadius: "10px",
              background: "rgba(244, 157, 13, 0.14)",
              border: "1px solid rgba(244, 157, 13, 0.35)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "#f49d0d",
            }}
          >
            <Lock size={18} />
          </div>
          <div>
            <h2 style={{ fontSize: "19px", fontWeight: 800, color: "var(--text-primary)", margin: 0 }}>
              Personal Institutional Watchlist
            </h2>
            <p style={{ fontSize: "12px", color: "var(--text-secondary)", margin: "2px 0 0" }}>
              Isolated per-user setups backed by Supabase RLS &amp; Live Binance Gate Audits
            </p>
          </div>
        </div>

        {/* Mode Switcher */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 1fr 1fr",
            gap: "6px",
            background: "rgba(255, 255, 255, 0.03)",
            padding: "4px",
            borderRadius: "10px",
            border: "1px solid var(--border-subtle)",
            margin: "20px 0 18px",
          }}
        >
          {(
            [
              { key: "signin", label: "Sign In" },
              { key: "signup", label: "Create Account" },
              { key: "magic", label: "Magic Link" },
            ] as const
          ).map((tab) => (
            <button
              key={tab.key}
              type="button"
              onClick={() => {
                setMode(tab.key);
                setErrorMessage(null);
                setInfoMessage(null);
              }}
              style={{
                background: mode === tab.key ? "rgba(244, 157, 13, 0.18)" : "transparent",
                color: mode === tab.key ? "#f49d0d" : "var(--text-secondary)",
                border: mode === tab.key ? "1px solid rgba(244, 157, 13, 0.35)" : "1px solid transparent",
                borderRadius: "7px",
                padding: "7px 8px",
                fontSize: "12px",
                fontWeight: 700,
                cursor: "pointer",
                transition: "all 0.15s ease",
              }}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {errorMessage && (
          <div
            style={{
              display: "flex",
              alignItems: "flex-start",
              gap: "8px",
              background: "rgba(239, 68, 68, 0.12)",
              border: "1px solid rgba(239, 68, 68, 0.35)",
              color: "#f87171",
              borderRadius: "8px",
              padding: "10px 12px",
              fontSize: "12px",
              marginBottom: "14px",
              lineHeight: 1.4,
            }}
          >
            <AlertCircle size={15} style={{ flexShrink: 0, marginTop: "1px" }} />
            <span>{errorMessage}</span>
          </div>
        )}

        {infoMessage && (
          <div
            style={{
              display: "flex",
              alignItems: "flex-start",
              gap: "8px",
              background: "rgba(16, 185, 129, 0.12)",
              border: "1px solid rgba(16, 185, 129, 0.35)",
              color: "#34d399",
              borderRadius: "8px",
              padding: "10px 12px",
              fontSize: "12px",
              marginBottom: "14px",
              lineHeight: 1.4,
            }}
          >
            <CheckCircle2 size={15} style={{ flexShrink: 0, marginTop: "1px" }} />
            <span>{infoMessage}</span>
          </div>
        )}

        <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
          <div>
            <label
              style={{
                display: "flex",
                alignItems: "center",
                gap: "6px",
                fontSize: "11px",
                fontWeight: 700,
                color: "var(--text-secondary)",
                marginBottom: "6px",
                textTransform: "uppercase",
              }}
            >
              <Mail size={12} /> Email Address
            </label>
            <input
              type="email"
              required
              placeholder="trader@institution.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              style={{
                width: "100%",
                background: "rgba(255, 255, 255, 0.04)",
                border: "1px solid var(--border-medium)",
                borderRadius: "8px",
                padding: "10px 12px",
                color: "var(--text-primary)",
                fontSize: "13px",
                outline: "none",
              }}
            />
          </div>

          {mode !== "magic" && (
            <div>
              <label
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "6px",
                  fontSize: "11px",
                  fontWeight: 700,
                  color: "var(--text-secondary)",
                  marginBottom: "6px",
                  textTransform: "uppercase",
                }}
              >
                <KeyRound size={12} /> Password
              </label>
              <input
                type="password"
                required
                minLength={6}
                placeholder={mode === "signup" ? "Create a password (min 6 chars)" : "Enter your password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                style={{
                  width: "100%",
                  background: "rgba(255, 255, 255, 0.04)",
                  border: "1px solid var(--border-medium)",
                  borderRadius: "8px",
                  padding: "10px 12px",
                  color: "var(--text-primary)",
                  fontSize: "13px",
                  outline: "none",
                }}
              />
            </div>
          )}

          <button
            type="submit"
            disabled={submitting}
            style={{
              marginTop: "6px",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              gap: "8px",
              background: "#f49d0d",
              color: "#07090e",
              border: "none",
              borderRadius: "9px",
              padding: "11px 16px",
              fontSize: "13px",
              fontWeight: 800,
              cursor: submitting ? "not-allowed" : "pointer",
              opacity: submitting ? 0.75 : 1,
            }}
          >
            {mode === "magic" ? <Sparkles size={15} /> : <ArrowRight size={15} />}
            {submitting
              ? "Authenticating..."
              : mode === "signin"
                ? "Sign In to My Watchlist"
                : mode === "signup"
                  ? "Create Private Watchlist Account"
                  : "Send Magic Sign-In Link"}
          </button>
        </form>

        <div
          style={{
            marginTop: "18px",
            paddingTop: "14px",
            borderTop: "1px solid var(--border-subtle)",
            fontSize: "11px",
            color: "var(--text-muted)",
            lineHeight: 1.5,
          }}
        >
          Every account receives its own dedicated watchlist isolated via Postgres Row-Level Security
          (<code>auth.uid() = user_id</code>). Tokens you add, edit, or delete are visible only to you.
        </div>
      </div>
    </div>
  );
}

