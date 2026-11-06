"""Separate research envelope; core ScanRecord v1 is deliberately unchanged."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from algotrader.domain.models import Instrument
from algotrader.domain.scan_observations import RecordedCall


@dataclass(frozen=True)
class ShadowRunRecord:
    shadow_id: str
    parent_id: str
    parent_checksum: str
    started_at: datetime
    completed_at: datetime
    status: str
    identity: dict[str, Any]
    policy: dict[str, Any]
    cohort: tuple[Instrument, ...]
    admission: tuple[dict[str, Any], ...] = ()
    inputs: tuple[RecordedCall, ...] = ()
    results: tuple[dict[str, Any], ...] = ()
    labels: tuple[dict[str, Any], ...] = ()
    coverage: dict[str, Any] = field(default_factory=dict)
    timing: dict[str, Any] = field(default_factory=dict)
    budget: dict[str, Any] = field(default_factory=dict)
    journal: tuple[dict[str, Any], ...] = ()
    reused: tuple[dict[str, Any], ...] = ()
    schema_version: int = 1
    error: str | None = None
