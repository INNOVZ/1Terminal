"use client";

import { memo, useState, useCallback } from "react";
import { X, RefreshCw } from "lucide-react";

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

function formatPriceSmart(val: number): string {
  if (!Number.isFinite(val) || val <= 0) return "";
  if (val < 0.001) return val.toFixed(6);
  if (val < 1) return val.toFixed(4);
  if (val < 10) return val.toFixed(3);
  return val.toFixed(2);
}

function AddCandidateModal({ isOpen, onClose, onSubmit }: AddCandidateModalProps) {
  const [formData, setFormData] = useState<CandidateFormData>(initialFormState);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [fetchingPrice, setFetchingPrice] = useState(false);

  const handleAutoFillPrice = useCallback(async (rawSymbol: string) => {
    const clean = rawSymbol.trim().toUpperCase();
    if (!clean) return;
    const sym = clean.endsWith("USDT") || clean.endsWith("USDC") ? clean : `${clean}USDT`;
    setFetchingPrice(true);
    try {
      let res = await fetch(
        `https://fapi.binance.com/fapi/v1/ticker/price?symbol=${encodeURIComponent(sym)}`,
      ).catch(() => null);
      if (!res || !res.ok) {
        res = await fetch(
          `https://api.binance.com/api/v3/ticker/price?symbol=${encodeURIComponent(sym)}`,
        ).catch(() => null);
      }
      if (res && res.ok) {
        const data = (await res.json()) as { price?: string };
        const p = Number(data?.price);
        if (Number.isFinite(p) && p > 0) {
          setFormData((prev) => ({
            ...prev,
            symbol: sym,
            name: prev.name || sym.replace(/USDT$/, "").replace(/USDC$/, ""),
            current_price: prev.current_price || formatPriceSmart(p),
            trigger_price: prev.trigger_price || formatPriceSmart(p * 1.03),
            support_price: prev.support_price || formatPriceSmart(p * 0.975),
            invalidation_price: prev.invalidation_price || formatPriceSmart(p * 0.945),
            target_1: prev.target_1 || formatPriceSmart(p * 1.1),
            target_2: prev.target_2 || formatPriceSmart(p * 1.22),
          }));
        }
      }
    } catch {
      // ignore fetch error
    } finally {
      setFetchingPrice(false);
    }
  }, []);

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
    [formData, onSubmit, isSubmitting],
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
          width: "600px",
          maxHeight: "90vh",
          overflowY: "auto",
          padding: "24px",
          background: "var(--bg-surface)",
        }}
      >
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            marginBottom: "20px",
          }}
        >
          <div>
            <h3 style={{ fontSize: "18px", fontWeight: 800 }}>Add Candidate to My Watchlist</h3>
            <p style={{ color: "var(--text-secondary)", fontSize: "12px", marginTop: "2px" }}>
              Monitor a developing crypto setup with custom trigger, support, invalidation, and profit targets.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            style={{
              background: "transparent",
              border: "none",
              color: "var(--text-muted)",
              cursor: "pointer",
            }}
          >
            <X size={18} />
          </button>
        </div>

        <form onSubmit={handleSubmit}>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "1fr 1fr",
              gap: "12px",
              marginBottom: "12px",
            }}
          >
            <div>
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  marginBottom: "4px",
                }}
              >
                <label
                  style={{
                    fontSize: "11px",
                    fontWeight: 700,
                    color: "var(--text-secondary)",
                  }}
                >
                  Symbol (e.g. SOLUSDT)
                </label>
                <button
                  type="button"
                  onClick={() => void handleAutoFillPrice(formData.symbol)}
                  disabled={!formData.symbol.trim() || fetchingPrice}
                  style={{
                    background: "transparent",
                    border: "none",
                    color: "#f49d0d",
                    fontSize: "10.5px",
                    fontWeight: 700,
                    cursor: "pointer",
                    display: "inline-flex",
                    alignItems: "center",
                    gap: "4px",
                  }}
                >
                  <RefreshCw
                    size={10}
                    style={{ animation: fetchingPrice ? "spin 1s linear infinite" : "none" }}
                  />
                  {fetchingPrice ? "Fetching..." : "Auto-Fill Live Price"}
                </button>
              </div>
              <input
                type="text"
                required
                placeholder="SOLUSDT"
                value={formData.symbol}
                onChange={(e) => setFormData({ ...formData, symbol: e.target.value })}
                onBlur={() => {
                  if (formData.symbol && !formData.current_price) {
                    void handleAutoFillPrice(formData.symbol);
                  }
                }}
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
              <label
                style={{
                  display: "block",
                  fontSize: "11px",
                  fontWeight: 700,
                  color: "var(--text-secondary)",
                  marginBottom: "4px",
                }}
              >
                Name (e.g. Solana)
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

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "1fr 1fr",
              gap: "12px",
              marginBottom: "12px",
            }}
          >
            <div>
              <label
                style={{
                  display: "block",
                  fontSize: "11px",
                  fontWeight: 700,
                  color: "var(--text-secondary)",
                  marginBottom: "4px",
                }}
              >
                Wyckoff Accumulation / Distribution Stage
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
                <option value="Phase C: Spring / Shakeout Test">
                  Phase C: Spring / Shakeout Test
                </option>
                <option value="Phase B: Supply Absorption">Phase B: Supply Absorption</option>
                <option value="VCP: Volatility Compression">VCP: Volatility Compression</option>
                <option value="POC Retest & Acceptance">POC Retest &amp; Acceptance</option>
                <option value="Pre-Breakout Coil Imminent">Pre-Breakout Coil Imminent</option>
                <option value="Phase C: UTAD Distribution Short">
                  Phase C: UTAD Distribution Short
                </option>
                <option value="Phase D: Breakdown Coil Short">
                  Phase D: Breakdown Coil Short
                </option>
              </select>
            </div>
            <div>
              <label
                style={{
                  display: "block",
                  fontSize: "11px",
                  fontWeight: 700,
                  color: "var(--text-secondary)",
                  marginBottom: "4px",
                }}
              >
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

          {/* 6-Field Bracket Level Grid: Current, Trigger, Support, Invalidation, Target 1, Target 2 */}
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(3, 1fr)",
              gap: "10px",
              marginBottom: "12px",
            }}
          >
            <div>
              <label
                style={{
                  display: "block",
                  fontSize: "10px",
                  fontWeight: 700,
                  color: "var(--text-secondary)",
                  marginBottom: "4px",
                }}
              >
                Current Price ($)
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
                  padding: "7px 8px",
                  color: "var(--text-primary)",
                  fontSize: "12px",
                  fontFamily: "var(--font-mono)",
                  outline: "none",
                }}
              />
            </div>
            <div>
              <label
                style={{
                  display: "block",
                  fontSize: "10px",
                  fontWeight: 700,
                  color: "#f49d0d",
                  marginBottom: "4px",
                }}
              >
                Trigger Price ($)
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
                  padding: "7px 8px",
                  color: "var(--text-primary)",
                  fontSize: "12px",
                  fontFamily: "var(--font-mono)",
                  outline: "none",
                }}
              />
            </div>
            <div>
              <label
                style={{
                  display: "block",
                  fontSize: "10px",
                  fontWeight: 700,
                  color: "#ffffff",
                  marginBottom: "4px",
                }}
              >
                POC Support ($)
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
                  padding: "7px 8px",
                  color: "var(--text-primary)",
                  fontSize: "12px",
                  fontFamily: "var(--font-mono)",
                  outline: "none",
                }}
              />
            </div>
            <div>
              <label
                style={{
                  display: "block",
                  fontSize: "10px",
                  fontWeight: 700,
                  color: "var(--accent-crimson)",
                  marginBottom: "4px",
                }}
              >
                Invalidation Stop ($)
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
                  padding: "7px 8px",
                  color: "var(--text-primary)",
                  fontSize: "12px",
                  fontFamily: "var(--font-mono)",
                  outline: "none",
                }}
              />
            </div>
            <div>
              <label
                style={{
                  display: "block",
                  fontSize: "10px",
                  fontWeight: 700,
                  color: "var(--accent-emerald)",
                  marginBottom: "4px",
                }}
              >
                Target 1 ($)
              </label>
              <input
                type="number"
                step="any"
                required
                value={formData.target_1}
                onChange={(e) => setFormData({ ...formData, target_1: e.target.value })}
                style={{
                  width: "100%",
                  background: "rgba(255, 255, 255, 0.04)",
                  border: "1px solid var(--border-medium)",
                  borderRadius: "6px",
                  padding: "7px 8px",
                  color: "var(--text-primary)",
                  fontSize: "12px",
                  fontFamily: "var(--font-mono)",
                  outline: "none",
                }}
              />
            </div>
            <div>
              <label
                style={{
                  display: "block",
                  fontSize: "10px",
                  fontWeight: 700,
                  color: "var(--accent-emerald)",
                  marginBottom: "4px",
                }}
              >
                Target 2 ($)
              </label>
              <input
                type="number"
                step="any"
                required
                value={formData.target_2}
                onChange={(e) => setFormData({ ...formData, target_2: e.target.value })}
                style={{
                  width: "100%",
                  background: "rgba(255, 255, 255, 0.04)",
                  border: "1px solid var(--border-medium)",
                  borderRadius: "6px",
                  padding: "7px 8px",
                  color: "var(--text-primary)",
                  fontSize: "12px",
                  fontFamily: "var(--font-mono)",
                  outline: "none",
                }}
              />
            </div>
          </div>

          <div style={{ marginBottom: "12px" }}>
            <label
              style={{
                display: "block",
                fontSize: "11px",
                fontWeight: 700,
                color: "var(--text-secondary)",
                marginBottom: "4px",
              }}
            >
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

          <div style={{ marginBottom: "12px" }}>
            <label
              style={{
                display: "block",
                fontSize: "11px",
                fontWeight: 700,
                color: "var(--text-secondary)",
                marginBottom: "4px",
              }}
            >
              Strategy Note (Optional)
            </label>
            <input
              type="text"
              value={formData.notes}
              onChange={(e) => setFormData({ ...formData, notes: e.target.value })}
              placeholder="e.g. Place limit trigger on 4H close above ceiling..."
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
