"use client";

import { memo } from "react";
import { X } from "lucide-react";
import type { WatchlistItem } from "../page";

interface EditNotesModalProps {
  editingItem: WatchlistItem | null;
  editNotesText: string;
  onNotesChange: (val: string) => void;
  onClose: () => void;
  onSave: () => void;
}

function EditNotesModal({
  editingItem,
  editNotesText,
  onNotesChange,
  onClose,
  onSave,
}: EditNotesModalProps) {
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
          width: "480px",
          padding: "24px",
          background: "var(--bg-surface)",
          boxShadow: "0 24px 60px rgba(0, 0, 0, 0.8)",
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px" }}>
          <h3 style={{ fontSize: "16px", fontWeight: 800 }}>Edit Strategy Notes — {editingItem.symbol}</h3>
          <button
            type="button"
            onClick={onClose}
            style={{ background: "transparent", border: "none", color: "var(--text-muted)", cursor: "pointer" }}
          >
            <X size={18} />
          </button>
        </div>
        <textarea
          rows={4}
          value={editNotesText}
          onChange={(e) => onNotesChange(e.target.value)}
          placeholder="Add key trigger conditions, limit order setup, orderbook observations..."
          style={{
            width: "100%",
            background: "rgba(255, 255, 255, 0.04)",
            border: "1px solid var(--border-medium)",
            borderRadius: "8px",
            padding: "10px",
            color: "var(--text-primary)",
            fontSize: "13px",
            fontFamily: "var(--font-main)",
            resize: "vertical",
            outline: "none",
            marginBottom: "16px",
          }}
        />
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
            type="button"
            onClick={onSave}
            style={{
              background: "#f49d0d",
              border: "none",
              borderRadius: "8px",
              padding: "8px 18px",
              color: "#07090e",
              cursor: "pointer",
              fontSize: "12px",
              fontWeight: 800,
            }}
          >
            Save Notes
          </button>
        </div>
      </div>
    </div>
  );
}

export default memo(EditNotesModal);
