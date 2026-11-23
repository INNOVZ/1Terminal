"use client";

import { memo, useState, useCallback } from "react";
import { X } from "lucide-react";
import type { WatchlistItem } from "../page";

export interface EditCandidateSetupPayload {
  symbol: string;
  stage: string;
  timeframe: string;
  trigger_price: number;
  support_price: number;
  invalidation_price: number;
  target_1: number;
  target_2: number;
  thesis: string;
  notes: string;
}

interface EditNotesModalProps {
  editingItem: WatchlistItem | null;
  onClose: () => void;
  onSaveSetup: (updated: EditCandidateSetupPayload) => Promise<void>;
}

function EditNotesModal({ editingItem, onClose, onSaveSetup }: EditNotesModalProps) {
  const [stage, setStage] = useState(editingItem?.stage || "Phase C: Spring / Shakeout Test");
  const [timeframe, setTimeframe] = useState(editingItem?.timeframe || "1–3 Days");
  const [triggerPrice, setTriggerPrice] = useState(String(editingItem?.trigger_price ?? ""));
  const [supportPrice, setSupportPrice] = useState(String(editingItem?.support_price ?? ""));
  const [invalidationPrice, setInvalidationPrice] = useState(
    String(editingItem?.invalidation_price ?? ""),
  );
  const [target1, setTarget1] = useState(String(editingItem?.target_1 ?? ""));
  const [target2, setTarget2] = useState(String(editingItem?.target_2 ?? ""));
  const [thesis, setThesis] = useState(editingItem?.thesis || "");
  const [notes, setNotes] = useState(editingItem?.notes || "");
  const [saving, setSaving] = useState(false);

  const handleSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      if (!editingItem || saving) return;
      setSaving(true);
      try {
        await onSaveSetup({
          symbol: editingItem.symbol,
          stage,
          timeframe,
          trigger_price: parseFloat(triggerPrice) || editingItem.trigger_price,
          support_price: parseFloat(supportPrice) || editingItem.support_price,
          invalidation_price: parseFloat(invalidationPrice) || editingItem.invalidation_price,
          target_1: parseFloat(target1) || editingItem.target_1,
          target_2: parseFloat(target2) || editingItem.target_2,
          thesis,
          notes,
        });
      } finally {
        setSaving(false);
      }
    },
    [
      editingItem,
      saving,
      onSaveSetup,
      stage,
      timeframe,
      triggerPrice,
      supportPrice,
      invalidationPrice,
      target1,
      target2,
      thesis,
      notes,
    ],
  );

  if (!editingItem) return null;

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
        }}
      >
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            marginBottom: "16px",
          }}
        >
          <div>
            <h3 style={{ fontSize: "17px", fontWeight: 800 }}>
              Edit Bracket Levels &amp; Notes — {editingItem.symbol}
            </h3>
            <p style={{ fontSize: "11.5px", color: "var(--text-secondary)", marginTop: "2px" }}>
              Adjust trigger, support, invalidation, targets, or thesis for your real-time gate audit.
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
              gap: "10px",
              marginBottom: "12px",
            }}
          >
            <div>
              <label
                style={{
                  display: "block",
                  fontSize: "10.5px",
                  fontWeight: 700,
                  color: "var(--text-secondary)",
                  marginBottom: "4px",
                }}
              >
                Stage
              </label>
              <select
                value={stage}
                onChange={(e) => setStage(e.target.value)}
                style={{
                  width: "100%",
                  background: "var(--bg-card)",
                  border: "1px solid var(--border-medium)",
                  borderRadius: "6px",
                  padding: "7px 9px",
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
                  fontSize: "10.5px",
                  fontWeight: 700,
                  color: "var(--text-secondary)",
                  marginBottom: "4px",
                }}
              >
                Timeframe
              </label>
              <select
                value={timeframe}
                onChange={(e) => setTimeframe(e.target.value)}
                style={{
                  width: "100%",
                  background: "var(--bg-card)",
                  border: "1px solid var(--border-medium)",
                  borderRadius: "6px",
                  padding: "7px 9px",
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

          {/* Price Bracket Grid */}
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
                  color: "#f49d0d",
                  marginBottom: "4px",
                }}
              >
                Trigger (Ceil) ($)
              </label>
              <input
                type="number"
                step="any"
                required
                value={triggerPrice}
                onChange={(e) => setTriggerPrice(e.target.value)}
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
                value={supportPrice}
                onChange={(e) => setSupportPrice(e.target.value)}
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
                value={invalidationPrice}
                onChange={(e) => setInvalidationPrice(e.target.value)}
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
                value={target1}
                onChange={(e) => setTarget1(e.target.value)}
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
                value={target2}
                onChange={(e) => setTarget2(e.target.value)}
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
              value={thesis}
              onChange={(e) => setThesis(e.target.value)}
              style={{
                width: "100%",
                background: "rgba(255, 255, 255, 0.04)",
                border: "1px solid var(--border-medium)",
                borderRadius: "6px",
                padding: "8px 10px",
                color: "var(--text-primary)",
                fontSize: "12px",
                fontFamily: "var(--font-main)",
                resize: "vertical",
                outline: "none",
              }}
            />
          </div>

          <div style={{ marginBottom: "16px" }}>
            <label
              style={{
                display: "block",
                fontSize: "11px",
                fontWeight: 700,
                color: "var(--text-secondary)",
                marginBottom: "4px",
              }}
            >
              Strategy Notes
            </label>
            <textarea
              rows={3}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Add key trigger conditions, limit order setup, orderbook observations..."
              style={{
                width: "100%",
                background: "rgba(255, 255, 255, 0.04)",
                border: "1px solid var(--border-medium)",
                borderRadius: "8px",
                padding: "10px",
                color: "var(--text-primary)",
                fontSize: "12.5px",
                fontFamily: "var(--font-main)",
                resize: "vertical",
                outline: "none",
              }}
            />
          </div>

          <div style={{ display: "flex", justifyContent: "flex-end", gap: "10px" }}>
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
              disabled={saving}
              style={{
                background: "#f49d0d",
                border: "none",
                borderRadius: "8px",
                padding: "8px 18px",
                color: "#07090e",
                cursor: saving ? "not-allowed" : "pointer",
                fontSize: "12px",
                fontWeight: 800,
              }}
            >
              {saving ? "Saving..." : "Save Levels & Notes"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export default memo(EditNotesModal);
