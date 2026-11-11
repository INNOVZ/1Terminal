"""Versioned discovery selection and volume features shared by live and research scans."""
from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal

from algotrader.domain.indicators import volume_contraction_metrics
from algotrader.domain.models import Candle, Instrument, Quote

DISCOVERY_VERSION = "discovery-v2-full336-rvol4h-watch20"


class DiscoveryFeatureUnavailable(ValueError):
    pass


def discovery_volume_metrics(
    hourly: Sequence[Candle], four_hour: Sequence[Candle],
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    window, four = hourly[-336:], four_hour[-20:]
    if len(window) != 336 or any(b.opened_at - a.opened_at != timedelta(hours=1)
                                for a, b in zip(window, window[1:], strict=False)):
        raise DiscoveryFeatureUnavailable("complete contiguous 336-hour volume history required")
    if len(four) != 20 or any(b.opened_at - a.opened_at != timedelta(hours=4)
                              for a, b in zip(four, four[1:], strict=False)):
        raise DiscoveryFeatureUnavailable("complete contiguous 4H RVOL window required")
    mean = sum((c.volume for c in four), Decimal("0")) / 20
    if mean <= 0:
        raise DiscoveryFeatureUnavailable("positive 4H volume baseline required")
    short, long, contraction = volume_contraction_metrics(window, 7, 14)
    return short, long, contraction, four[-1].volume / mean


def discovery_watch_selection(
    selected: list[Instrument], liquid: list[Instrument], quotes: dict[str, Quote],
    budget: int, at: datetime,
) -> list[Instrument]:
    selected_ids = {i.id for i in selected}
    remaining = max(0, budget - len(selected))
    bucket = int(at.timestamp()) // (4 * 3600)
    pool = [i for i in liquid if i.id not in selected_ids
            and Decimal("500000") <= quotes[i.id.symbol].quote_volume_24h < Decimal("1500000")
            and min(quotes[i.id.symbol].bid, quotes[i.id.symbol].ask, quotes[i.id.symbol].last) > 0
            and Decimal("0") <= quotes[i.id.symbol].spread_bps <= Decimal("25")]
    pool.sort(key=lambda i: hashlib.sha256(f"{DISCOVERY_VERSION}|{bucket}|{i.id.symbol}".encode()).hexdigest())
    return [*selected, *pool[:min(remaining, 20)]]


def discovery_temporal_features(
    hourly: Sequence[Candle], btc: Sequence[Candle],
) -> dict[str, object]:
    """Compute aligned 6h BTC-relative strength and 6h/18h turnover acceleration on closed 1H bars."""
    if len(hourly) < 24 or len(btc) < 6 or hourly[-1].closed_at != btc[-1].closed_at:
        return {"available": False, "reason": "unaligned_or_missing_closed_history"}
    if any(
        b.opened_at - a.opened_at != timedelta(hours=1)
        for sequence in (hourly[-24:], btc[-6:])
        for a, b in zip(sequence, sequence[1:], strict=False)
    ):
        return {"available": False, "reason": "noncontiguous_closed_history"}
    recent = sum((c.quote_volume for c in hourly[-6:]), Decimal("0")) / 6
    previous = sum((c.quote_volume for c in hourly[-24:-6]), Decimal("0")) / 18
    if previous <= 0 or min(hourly[-6].open, hourly[-24].open, btc[-6].open) <= 0:
        return {"available": False, "reason": "nonpositive_return_or_turnover_baseline"}
    ret6 = (hourly[-1].close / hourly[-6].open - 1) * 100
    ret24 = (hourly[-1].close / hourly[-24].open - 1) * 100
    btc6 = (btc[-1].close / btc[-6].open - 1) * 100
    return {
        "available": True,
        "last_closed_at": hourly[-1].closed_at.isoformat(),
        "return_6h_pct": str(ret6),
        "return_24h_pct": str(ret24),
        "btc_relative_6h_pp": str(ret6 - btc6),
        "turnover_acceleration_6h_vs_prior18h": str(recent / previous),
        "emerging": bool(ret6 - btc6 > 0 and recent / previous > 1 and ret24 <= 9),
    }
