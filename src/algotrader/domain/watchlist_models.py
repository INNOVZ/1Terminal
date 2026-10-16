from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum


class WatchlistPriority(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    WATCH = "WATCH"


class WyckoffStage(StrEnum):
    PHASE_B_ABSORPTION = "Phase B: Supply Absorption"
    PHASE_C_SPRING = "Phase C: Spring / Shakeout Test"
    VCP_CONTRACTION = "VCP: Volatility Compression"
    POC_RETEST = "POC Retest & Acceptance"
    COIL_IMMINENT = "Pre-Breakout Coil Imminent"
    PHASE_C_UTAD = "Phase C: UTAD Distribution Short"
    BREAKDOWN_COIL = "Phase D: Breakdown Coil Short"


class AuditStatus(StrEnum):
    COIL_HOLDING = "COIL_HOLDING"
    ACTIVE_EXPANSION = "ACTIVE_EXPANSION"
    FLOW_DIVERGENCE = "FLOW_DIVERGENCE"
    SELLER_INFLUX = "SELLER_INFLUX"
    TARGET_REACHED = "TARGET_REACHED"
    STOP_BREACHED = "STOP_BREACHED"


@dataclass(frozen=True, slots=True)
class WatchlistLiveAudit:
    """Real-time technical diagnosis and lifecycle gate status for a watched position."""

    symbol: str
    status: AuditStatus
    status_title: str
    live_price: Decimal
    price_change_pct: Decimal
    current_rsi_1h: Decimal
    current_rsi_4h: Decimal
    taker_ratio: Decimal | None
    vol_contraction_ratio: Decimal | None
    gate_tripped: str
    live_reading: str
    what_happened: str
    institutional_logic: str
    audited_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def as_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "status": self.status.value,
            "status_title": self.status_title,
            "live_price": float(self.live_price),
            "price_change_pct": float(self.price_change_pct),
            "current_rsi_1h": float(self.current_rsi_1h),
            "current_rsi_4h": float(self.current_rsi_4h),
            "taker_ratio": float(self.taker_ratio) if self.taker_ratio is not None else None,
            "vol_contraction_ratio": float(self.vol_contraction_ratio) if self.vol_contraction_ratio is not None else None,
            "gate_tripped": self.gate_tripped,
            "live_reading": self.live_reading,
            "what_happened": self.what_happened,
            "institutional_logic": self.institutional_logic,
            "audited_at": self.audited_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class WatchlistItem:
    """A high-probability cryptocurrency token on institutional watch for coming days."""

    symbol: str
    name: str
    sector: str
    stage: WyckoffStage | str
    timeframe: str  # e.g., "1-3 Days", "3-5 Days", "Coming Week"
    current_price: Decimal
    trigger_price: Decimal
    support_price: Decimal
    invalidation_price: Decimal
    target_1: Decimal
    target_2: Decimal
    distance_to_trigger_pct: Decimal
    score: Decimal
    thesis: str
    checklist_passed: int
    checklist_total: int
    priority: WatchlistPriority = WatchlistPriority.HIGH
    notes: str = ""
    added_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    audit: WatchlistLiveAudit | None = None
    estimated_win_rate_pct: Decimal = Decimal("66.0")

    def as_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "sector": self.sector,
            "stage": str(self.stage),
            "timeframe": self.timeframe,
            "current_price": float(self.current_price),
            "trigger_price": float(self.trigger_price),
            "support_price": float(self.support_price),
            "invalidation_price": float(self.invalidation_price),
            "target_1": float(self.target_1),
            "target_2": float(self.target_2),
            "distance_to_trigger_pct": float(self.distance_to_trigger_pct),
            "score": float(self.score),
            "estimated_win_rate_pct": float(self.estimated_win_rate_pct),
            "thesis": self.thesis,
            "checklist_passed": self.checklist_passed,
            "checklist_total": self.checklist_total,
            "priority": self.priority.value if isinstance(self.priority, WatchlistPriority) else str(self.priority),
            "notes": self.notes,
            "added_at": self.added_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "audit": self.audit.as_dict() if self.audit else None,
        }

