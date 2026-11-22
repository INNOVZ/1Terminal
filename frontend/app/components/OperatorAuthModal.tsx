"use client";

import { useState, type FormEvent } from "react";
import { Lock, X } from "lucide-react";
import { establishOperatorSession } from "../lib/operatorClient";

interface OperatorAuthModalProps {
  open: boolean;
  actionLabel?: string;
  onClose: () => void;
  onAuthenticated: () => void;
}

export default function OperatorAuthModal({
  open,
  actionLabel = "privileged operator action",
  onClose,
  onAuthenticated,
}: OperatorAuthModalProps) {
  const [token, setToken] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!open) return null;

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!token.trim() || submitting) return;
    setSubmitting(true);
    setError(null);
    const result = await establishOperatorSession(token);
    setSubmitting(false);
    if (result.ok) {
      setToken("");
      onAuthenticated();
    } else {
      setError(result.error || "Invalid operator token");
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Operator Authentication"
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 1000,
        background: "rgba(3, 6, 14, 0.78)",
        backdropFilter: "blur(6px)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: "16px",
      }}
    >
      <div
        style={{
          width: "100%",
          maxWidth: "420px",
          background: "#0d1322",
          border: "1px solid rgba(244, 157, 13, 0.35)",
          borderRadius: "12px",
          padding: "20px",
          boxShadow: "0 20px 50px rgba(0, 0, 0, 0.5)",
          color: "var(--text-primary)",
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            marginBottom: "12px",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <Lock size={16} color="#f49d0d" />
            <span style={{ fontSize: "14px", fontWeight: 800, letterSpacing: "0.03em" }}>
              OPERATOR SESSION REQUIRED
            </span>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close operator authentication dialog"
            style={{
              background: "transparent",
              border: "none",
              color: "var(--text-muted)",
              cursor: "pointer",
              padding: "4px",
            }}
          >
            <X size={16} />
          </button>
        </div>

        <p
          style={{
            margin: "0 0 14px 0",
            fontSize: "12px",
            color: "var(--text-muted)",
            lineHeight: 1.5,
          }}
        >
          Enter your Atlas operator token to unlock {actionLabel}. The server verifies your
          token and issues a signed, HttpOnly, SameSite=Strict session cookie (12h TTL).
        </p>

        <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
          <input
            type="password"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="ATLAS_OPERATOR_TOKEN"
            autoComplete="off"
            autoFocus
            style={{
              width: "100%",
              padding: "10px 12px",
              borderRadius: "8px",
              border: "1px solid var(--border-subtle)",
              background: "rgba(255, 255, 255, 0.04)",
              color: "var(--text-primary)",
              fontSize: "13px",
              fontFamily: "var(--font-mono)",
            }}
          />

          {error && (
            <div
              style={{
                fontSize: "12px",
                color: "#ef4444",
                background: "rgba(239, 68, 68, 0.1)",
                border: "1px solid rgba(239, 68, 68, 0.3)",
                borderRadius: "6px",
                padding: "8px 10px",
              }}
            >
              {error}
            </div>
          )}

          <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px" }}>
            <button
              type="button"
              onClick={onClose}
              style={{
                padding: "8px 14px",
                borderRadius: "6px",
                border: "1px solid var(--border-subtle)",
                background: "transparent",
                color: "var(--text-muted)",
                fontSize: "12px",
                fontWeight: 600,
                cursor: "pointer",
              }}
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting || !token.trim()}
              style={{
                padding: "8px 16px",
                borderRadius: "6px",
                border: "none",
                background: "#f49d0d",
                color: "#000",
                fontSize: "12px",
                fontWeight: 800,
                cursor: submitting ? "wait" : "pointer",
                opacity: submitting || !token.trim() ? 0.6 : 1,
              }}
            >
              {submitting ? "Verifying..." : "Unlock Operator Session"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

