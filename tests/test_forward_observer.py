from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

from algotrader.adapters.binance.client import BinanceHttpClient
from algotrader.adapters.binance.futures import BinanceUsdmAdapter
from algotrader.application.forward_observer import ForwardObserver
from algotrader.domain.models import MarketKind, Venue
from algotrader.infrastructure.scan_archive import LocalScanArchive
from tests.conftest import make_instrument
from tests.test_forward_outcomes import ANCHOR, minute_path
from tests.test_scan_archive import SCAN_ID, record


def archive_fixture(tmp_path: Path) -> LocalScanArchive:
    archive = LocalScanArchive(tmp_path)
    cohort = tuple(make_instrument(venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES, symbol=symbol)
                   for symbol in ("AAAUSDT", "OLDUSDT"))
    archive.write_scan(replace(record(), completed_at=ANCHOR, cohort=cohort))
    return archive


class History:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail_old: BaseException | None = None

    async def minute_candles_between(self, instrument: Any, start: datetime, end: datetime) -> list[Any]:
        self.calls.append(instrument.id.symbol)
        assert start == ANCHOR and end == ANCHOR + timedelta(hours=24)
        if instrument.id.symbol == "OLDUSDT" and self.fail_old:
            raise self.fail_old
        return minute_path()


async def test_observer_keeps_missing_delisted_cohort_and_resumes_without_rewriting_success(tmp_path: Path) -> None:
    archive = archive_fixture(tmp_path)
    before = (tmp_path / "scans" / SCAN_ID / "payload.json").read_bytes()
    history = History()
    history.fail_old = RuntimeError("delisted or history unavailable")
    observer = ForwardObserver(archive, history, pace_seconds=0)
    first = await observer.observe(SCAN_ID, 24, 2, now=ANCHOR + timedelta(hours=25))
    assert first["observed"] == 1 and first["total"] == 2
    assert first["full_coverage"] is False
    assert first["missing"] == {"OLDUSDT": "unavailable"}
    history.fail_old = None
    second = await observer.observe(SCAN_ID, 24, 2, now=ANCHOR + timedelta(hours=26))
    assert second["full_coverage"] is True
    assert second["ranks"] == {"AAAUSDT": 1, "OLDUSDT": 1}
    assert history.calls == ["AAAUSDT", "OLDUSDT", "OLDUSDT"]
    assert first["attempts"]["AAAUSDT"] == second["attempts"]["AAAUSDT"]
    assert len(archive.outcome_attempts(SCAN_ID, 24, "OLDUSDT")) == 2
    assert (tmp_path / "scans" / SCAN_ID / "payload.json").read_bytes() == before


async def test_interruptions_preserve_completed_symbols_for_restart(tmp_path: Path) -> None:
    archive = archive_fixture(tmp_path)
    history = History()
    history.fail_old = asyncio.CancelledError()
    observer = ForwardObserver(archive, history, pace_seconds=0)
    with pytest.raises(asyncio.CancelledError):
        await observer.observe(SCAN_ID, 24, 2, now=ANCHOR + timedelta(hours=25))
    history.fail_old = None
    result = await observer.observe(SCAN_ID, 24, 2, now=ANCHOR + timedelta(hours=26))
    assert result["observed"] == 2
    assert history.calls.count("AAAUSDT") == 1


async def test_limit_refusal_and_not_due_do_not_request_or_drop_cohort(tmp_path: Path) -> None:
    archive = archive_fixture(tmp_path)
    history = History()
    observer = ForwardObserver(archive, history, pace_seconds=0)
    with pytest.raises(ValueError, match="cohort"):
        await observer.observe(SCAN_ID, 24, 1, now=ANCHOR + timedelta(hours=25))
    assert not (tmp_path / "outcomes").exists()
    result = await observer.observe(SCAN_ID, 24, 2, now=ANCHOR + timedelta(hours=2))
    assert result["missing"] == {"AAAUSDT": "not_due", "OLDUSDT": "not_due"}
    assert history.calls == []


async def test_published_short_keeps_directional_measurement_and_separate_touch_diagnostics(tmp_path: Path) -> None:
    archive = archive_fixture(tmp_path)
    original = archive.read_scan(SCAN_ID)
    from uuid import uuid4
    candidate_id = str(uuid4())
    archive.write_scan(replace(original, scan_id=candidate_id, opportunities=({"symbol": "AAAUSDT", "direction": "short",
        "suggested_entry": "100", "suggested_stop": "105", "suggested_targets": ["95", "90", "85"]},)))
    result = await ForwardObserver(archive, History(), pace_seconds=0).observe(candidate_id, 24, 2, now=ANCHOR + timedelta(hours=25))
    assert result["published_symbols"] == ("AAAUSDT",)
    (attempt,) = archive.outcome_attempts(candidate_id, 24, "AAAUSDT")
    assert attempt["observation"]["directional_return_pct"] == Decimal("-10")
    assert attempt["touches"]["ambiguous_order"] is True


async def test_minute_history_uses_bounded_public_gets_without_listing_or_account_access() -> None:
    start = datetime(2026, 10, 20, tzinfo=UTC)
    end = start + timedelta(hours=24)
    first_ms = int(start.timestamp() * 1000)
    requests = []
    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET" and request.url.path == "/fapi/v1/klines"
        assert request.url.params["interval"] == "1m"
        assert int(request.url.params["endTime"]) == first_ms + 1440 * 60000 - 1
        offset = (int(request.url.params["startTime"]) - first_ms) // 60000
        size = min(1000, 1440 - offset)
        rows = [[first_ms + i * 60000, "100", "112", "95", "110", "10", first_ms + (i + 1) * 60000 - 1, "1000", 10, "5", "500", "0"] for i in range(offset, offset + size)]
        return httpx.Response(200, json=rows)
    client = BinanceHttpClient("https://fapi.binance.com", transport=httpx.MockTransport(respond))
    try:
        instrument = make_instrument(venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES, symbol="DELISTEDUSDT")
        candles = await BinanceUsdmAdapter(client).minute_candles_between(instrument, start, end)
        assert len(candles) == 1440
        assert len(requests) == 2
        assert candles[0].open == Decimal("100")
        assert candles[-1].closed_at == end - timedelta(milliseconds=1)
    finally:
        await client.close()
