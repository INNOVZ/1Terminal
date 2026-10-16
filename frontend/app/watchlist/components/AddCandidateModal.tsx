"use client";

import { memo, useState, useCallback } from "react";
import { X } from "lucide-react";

export interface CandidateFormData {
  symbol: string;
  name: string;
  sector: string;
  stage: string;
  timeframe: string;
  current_price: string;
  trigger_price: string;
  support_price: string;
  invalidation_price: string;
  target_1: string;
  target_2: string;
  thesis: string;
  notes: string;
  priority: string;
}

interface AddCandidateModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSubmit: (data: CandidateFormData) => Promise<void>;
}

const initialFormState: CandidateFormData = {
  symbol: "",
  name: "",
  sector: "Layer 1 / Ecosystem",
  stage: "Phase C: Spring / Shakeout Test",
  timeframe: "1–3 Days",
  current_price: "",
  trigger_price: "",
  support_price: "",
  invalidation_price: "",
  target_1: "",
  target_2: "",
  thesis: "",
  notes: "",
  priority: "HIGH",
};

function AddCandidateModal({ isOpen, onClose, onSubmit }: AddCandidateModalProps) {
  const [formData, setFormData] = useState<CandidateFormData>(initialFormState);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      if (!formData.symbol || isSubmitting) return;
      setIsSubmitting(true);
      try {
        await onSubmit(formData);
        setFormData(initialFormState);
      } finally {
        setIsSubmitting(false);
      }
    },
    [formData, onSubmit, isSubmitting]
  );

  if (!isOpen) return null;

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0, 0, 0, 0.75)",
        backdropFilter: "blur(6px)",
        zIndex: 99999,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <div
        className="glass-panel"
        style={{
          width: "560px",
          maxHeight: "90vh",
          overflowY: "auto",
          padding: "24px",
          background: "var(--bg-surface)",
          boxShadow: "0 24px 60px rgba(0, 0, 0, 0.8)",
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "20px" }}>
          <div>
            <h3 style={{ fontSize: "18px", fontWeight: 800 }}>Add Candidate to Watchlist</h3>
            <p style={{ color: "var(--text-secondary)", fontSize: "12px", marginTop: "2px" }}>
              Monitor a developing crypto setup for upcoming 1–5 day breakout.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            style={{ background: "transparent", border: "none", color: "var(--text-muted)", cursor: "pointer" }}
          >
            <X size={18} />
          </button>
        </div>

        <form onSubmit={handleSubmit}>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px", marginBottom: "12px" }}>
            <div>
              <label style={{ display: "block", fontSize: "11px", fontWeight: 700, color: "var(--text-secondary)", marginBottom: "4px" }}>
                Symbol (e.g. SUIUSDT)
              </label>
              <input
                type="text"
                required
                value={formData.symbol}
                onChange={(e) => setFormData({ ...formData, symbol: e.target.value })}
                style={{
                  width: "100%",
                  background: "rgba(255, 255, 255, 0.04)",
                  border: "1px solid var(--border-medium)",
                  borderRadius: "6px",
                  padding: "8px 10px",
                  color: "var(--text-primary)",
                  fontSize: "12px",
                  outline: "none",
                }}
              />
            </div>
            <div>
              <label style={{ display: "block", fontSize: "11px", fontWeight: 700, color: "var(--text-secondary)", marginBottom: "4px" }}>
                Name (e.g. Sui Network)
              </label>
              <input
                type="text"
                value={formData.name}
                onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                style={{
                  width: "100%",
                  background: "rgba(255, 255, 255, 0.04)",
                  border: "1px solid var(--border-medium)",
                  borderRadius: "6px",
                  padding: "8px 10px",
                  color: "var(--text-primary)",
                  fontSize: "12px",
                  outline: "none",
                }}
              />
            </div>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px", marginBottom: "12px" }}>
            <div>
              <label style={{ display: "block", fontSize: "11px", fontWeight: 700, color: "var(--text-secondary)", marginBottom: "4px" }}>
                Wyckoff Accumulation Stage
              </label>
              <select
                value={formData.stage}
                onChange={(e) => setFormData({ ...formData, stage: e.target.value })}
                style={{
                  width: "100%",
                  background: "var(--bg-card)",
                  border: "1px solid var(--border-medium)",
                  borderRadius: "6px",
                  padding: "8px 10px",
                  color: "var(--text-primary)",
                  fontSize: "12px",
                  outline: "none",
                }}
              >
                <option value="Phase C: Spring / Shakeout Test">Phase C: Spring / Shakeout Test</option>
                <option value="Phase B: Supply Absorption">Phase B: Supply Absorption</option>
                <option value="VCP: Volatility Compression">VCP: Volatility Compression</option>
                <option value="POC Retest & Acceptance">POC Retest & Acceptance</option>
                <option value="Pre-Breakout Coil Imminent">Pre-Breakout Coil Imminent</option>
              </select>
            </div>
            <div>
              <label style={{ display: "block", fontSize: "11px", fontWeight: 700, color: "var(--text-secondary)", marginBottom: "4px" }}>
                Breakout Horizon
              </label>
              <select
                value={formData.timeframe}
                onChange={(e) => setFormData({ ...formData, timeframe: e.target.value })}
                style={{
                  width: "100%",
                  background: "var(--bg-card)",
                  border: "1px solid var(--border-medium)",
                  borderRadius: "6px",
                  padding: "8px 10px",
                  color: "var(--text-primary)",
                  fontSize: "12px",
                  outline: "none",
                }}
              >
                <option value="1–2 Days">1–2 Days</option>
                <option value="1–3 Days">1–3 Days</option>
                <option value="3–5 Days">3–5 Days</option>
                <option value="Coming Week">Coming Week</option>
              </select>
            </div>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "10px", marginBottom: "12px" }}>
            <div>
              <label style={{ display: "block", fontSize: "10px", fontWeight: 700, color: "var(--text-secondary)", marginBottom: "4px" }}>
                Current Price
              </label>
              <input
                type="number"
                step="any"
                required
                value={formData.current_price}
                onChange={(e) => setFormData({ ...formData, current_price: e.target.value })}
                style={{
                  width: "100%",
                  background: "rgba(255, 255, 255, 0.04)",
                  border: "1px solid var(--border-medium)",
                  borderRadius: "6px",
                  padding: "6px 8px",
                  color: "var(--text-primary)",
                  fontSize: "12px",
                  outline: "none",
                }}
              />
            </div>
            <div>
              <label style={{ display: "block", fontSize: "10px", fontWeight: 700, color: "#f49d0d", marginBottom: "4px" }}>
                Trigger Price
              </label>
              <input
                type="number"
                step="any"
                required
                value={formData.trigger_price}
                onChange={(e) => setFormData({ ...formData, trigger_price: e.target.value })}
                style={{
                  width: "100%",
                  background: "rgba(255, 255, 255, 0.04)",
                  border: "1px solid var(--border-medium)",
                  borderRadius: "6px",
                  padding: "6px 8px",
                  color: "var(--text-primary)",
                  fontSize: "12px",
                  outline: "none",
                }}
              />
            </div>
            <div>
              <label style={{ display: "block", fontSize: "10px", fontWeight: 700, color: "#ffffff", marginBottom: "4px" }}>
                POC Support
              </label>
              <input
                type="number"
                step="any"
                required
                value={formData.support_price}
                onChange={(e) => setFormData({ ...formData, support_price: e.target.value })}
                style={{
                  width: "100%",
                  background: "rgba(255, 255, 255, 0.04)",
                  border: "1px solid var(--border-medium)",
                  borderRadius: "6px",
                  padding: "6px 8px",
                  color: "var(--text-primary)",
                  fontSize: "12px",
                  outline: "none",
                }}
              />
            </div>
            <div>
              <label style={{ display: "block", fontSize: "10px", fontWeight: 700, color: "var(--accent-crimson)", marginBottom: "4px" }}>
                Invalidation
              </label>
              <input
                type="number"
                step="any"
                required
                value={formData.invalidation_price}
                onChange={(e) => setFormData({ ...formData, invalidation_price: e.target.value })}
                style={{
                  width: "100%",
                  background: "rgba(255, 255, 255, 0.04)",
                  border: "1px solid var(--border-medium)",
                  borderRadius: "6px",
                  padding: "6px 8px",
                  color: "var(--text-primary)",
                  fontSize: "12px",
                  outline: "none",
                }}
              />
            </div>
          </div>

          <div style={{ marginBottom: "12px" }}>
            <label style={{ display: "block", fontSize: "11px", fontWeight: 700, color: "var(--text-secondary)", marginBottom: "4px" }}>
              Institutional Thesis
            </label>
            <textarea
              rows={2}
              value={formData.thesis}
              onChange={(e) => setFormData({ ...formData, thesis: e.target.value })}
              placeholder="Why is smart money accumulating? (e.g. Volume profile absorption, quiet OI growth)..."
              style={{
                width: "100%",
                background: "rgba(255, 255, 255, 0.04)",
                border: "1px solid var(--border-medium)",
                borderRadius: "6px",
                padding: "8px 10px",
                color: "var(--text-primary)",
                fontSize: "12px",
                fontFamily: "var(--font-main)",
                outline: "none",
              }}
            />
          </div>

          <div style={{ display: "flex", justifyContent: "flex-end", gap: "10px", marginTop: "18px" }}>
            <button
              type="button"
              onClick={onClose}
              style={{
                background: "transparent",
                border: "1px solid var(--border-medium)",
                borderRadius: "8px",
                padding: "8px 16px",
                color: "var(--text-secondary)",
                cursor: "pointer",
                fontSize: "12px",
                fontWeight: 600,
              }}
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isSubmitting}
              style={{
                background: "#f49d0d",
                border: "none",
                borderRadius: "8px",
                padding: "8px 20px",
                color: "#07090e",
                cursor: isSubmitting ? "not-allowed" : "pointer",
                fontSize: "12px",
                fontWeight: 800,
              }}
            >
              {isSubmitting ? "Adding..." : "Add to Watchlist"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export default memo(AddCandidateModal);
