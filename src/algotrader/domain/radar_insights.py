"""Publication provenance only; never participates in selection or lifecycle decisions."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from algotrader.domain.models import Quote, Venue


def radar_quote_snapshot(quote: Quote | None, symbol: str, now: datetime) -> dict[str, str] | None:
    """Only timestamped, positive, same-contract futures quotes with verified component freshness can anchor a signal."""
    if (
        quote is None
        or quote.instrument_id.symbol != symbol
        or quote.instrument_id.venue != Venue.BINANCE_USDM
        or quote.observed_at.tzinfo is None
        or now.tzinfo is None
        or not quote.is_valid_book
        or not quote.is_fresh_at(now, max_age_seconds=60.0, max_future_seconds=5.0)
    ):
        return None
    effective_observed_at = quote.exchange_observed_at or quote.observed_at
    return {
        "price": str((quote.bid + quote.ask) / Decimal("2")),
        "observed_at": effective_observed_at.isoformat(),
        "source": "binance_usdm_bid_ask_midpoint",
    }


def record_radar_publication(
    candidate: dict[str, Any], quote: Quote | None, now: datetime, scan_id: str,
) -> None:
    """Called exactly for a new published episode; never backfills legacy baselines."""
    snapshot = radar_quote_snapshot(quote, str(candidate.get("symbol", "")), now)
    candidate["radar_signal"] = {
        "version": 1, "episode_id": str(uuid4()), "published_at": now.isoformat(),
        "scan_id": scan_id, "price": snapshot["price"] if snapshot else None,
        "quote_observed_at": snapshot["observed_at"] if snapshot else None,
        "source": snapshot["source"] if snapshot else None,
    }
    candidate["radar_quote"] = snapshot
