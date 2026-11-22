from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from algotrader.application.radar_service import reconcile_radar_cohort
from algotrader.domain.models import Quote, Venue
from algotrader.domain.radar_insights import radar_quote_snapshot, record_radar_publication
from tests.conftest import make_instrument

NOW = datetime(2026, 11, 22, 10, tzinfo=UTC)
INST = make_instrument(symbol="TESTUSDT", venue=Venue.BINANCE_USDM)
QUOTE = Quote(INST.id, Decimal("99"), Decimal("101"), Decimal("100"), Decimal("1000"), NOW)


@pytest.mark.parametrize("seconds", [-6, 61])
def test_bad_quote_time_never_becomes_signal_baseline(seconds):
    quote = replace(QUOTE, observed_at=NOW - timedelta(seconds=seconds))
    candidate = {"symbol": "TESTUSDT", "current_price": "999"}
    record_radar_publication(candidate, quote, NOW, "scan")
    assert candidate["radar_signal"]["price"] is None
    assert candidate["radar_signal"]["published_at"] == NOW.isoformat()


@pytest.mark.parametrize(
    "bad_live_quote",
    [
        # Live adapter quote with missing exchange timestamps (observed_at fell back to received_at=NOW)
        replace(QUOTE, observed_at=NOW, exchange_observed_at=None, received_at=NOW),
        # Live adapter quote with future bookTicker.time concealed by current ticker.closeTime
        replace(
            QUOTE,
            observed_at=NOW,
            exchange_observed_at=NOW,
            received_at=NOW,
            book_observed_at=NOW + timedelta(days=1),
            ticker_observed_at=NOW,
        ),
        # Live adapter quote with stale ticker.closeTime concealed by current bookTicker.time
        replace(
            QUOTE,
            observed_at=NOW,
            exchange_observed_at=NOW - timedelta(hours=2),
            received_at=NOW,
            book_observed_at=NOW,
            ticker_observed_at=NOW - timedelta(hours=2),
        ),
        # Live adapter quote with only one component timestamp present
        replace(
            QUOTE,
            observed_at=NOW,
            exchange_observed_at=NOW,
            received_at=NOW,
            book_observed_at=NOW,
            ticker_observed_at=None,
        ),
        # Crossed book (ask < bid)
        replace(QUOTE, bid=Decimal("101"), ask=Decimal("99")),
    ],
)
def test_signal_baseline_rejects_unverified_or_corrupted_live_component_quotes(bad_live_quote):
    assert radar_quote_snapshot(bad_live_quote, "TESTUSDT", NOW) is None
    candidate = {"symbol": "TESTUSDT", "current_price": "100"}
    record_radar_publication(candidate, bad_live_quote, NOW, "scan-id")
    assert candidate["radar_signal"]["price"] is None
    assert candidate["radar_signal"]["quote_observed_at"] is None
    assert candidate["radar_quote"] is None


@pytest.mark.parametrize("price", ["0", "-1", "NaN", "Infinity"])
def test_invalid_price_never_becomes_baseline(price):
    assert radar_quote_snapshot(replace(QUOTE, last=Decimal(price)), "TESTUSDT", NOW) is None


def test_signal_is_serializable_distinct_from_evaluation_and_wrong_venue_rejected():
    candidate = {"symbol": "TESTUSDT", "first_seen_at": (NOW - timedelta(minutes=10)).isoformat()}
    record_radar_publication(candidate, QUOTE, NOW, "scan")
    restored = json.loads(json.dumps(candidate))
    assert restored["radar_signal"]["price"] == "100"
    assert restored["radar_signal"]["published_at"] != restored["first_seen_at"]
    assert radar_quote_snapshot(QUOTE, "OTHERUSDT", NOW) is None
    spot = replace(QUOTE, instrument_id=replace(INST.id, venue=Venue.BINANCE_SPOT))
    assert radar_quote_snapshot(spot, "TESTUSDT", NOW) is None
    assert radar_quote_snapshot(replace(QUOTE, observed_at=NOW.replace(tzinfo=None)), "TESTUSDT", NOW) is None


@pytest.mark.asyncio
async def test_fresh_only_quote_batch_deduplicated_and_failure_does_not_make_candidates():
    provider = SimpleNamespace(instruments=AsyncMock(), quotes=AsyncMock(return_value=[QUOTE]))
    sink = {}
    assert await reconcile_radar_cohort([], set(), provider, NOW,
                                       additional_instruments=[INST, INST], quote_sink=sink) == []
    provider.instruments.assert_not_called()
    provider.quotes.assert_awaited_once_with([INST])
    assert sink == {"TESTUSDT": QUOTE}
    provider.quotes.side_effect = RuntimeError("offline")
    assert await reconcile_radar_cohort([], set(), provider, NOW,
                                       additional_instruments=[INST], quote_sink={}) == []


@pytest.mark.asyncio
async def test_portal_publication_baseline_survives_rescan_and_replacement_gets_new_episode(monkeypatch, tmp_path):
    from algotrader.api import portal

    now = datetime.now(UTC)
    row = {"symbol": "TESTUSDT", "direction": "long", "setup_label": "COIL_SPRING_LONG",
           "evaluated_at": now.isoformat(), "current_price": "99", "suggested_stop": "90",
           "checklist": [{"title": str(i), "passed": True} for i in range(5)],
           "candles": [{"time": 1}], "radar_data_status": "current"}

    class Opportunity:
        instrument = INST
        checklist = [(str(i), True, "test") for i in range(5)]
        macro_narrative = "fixture"

        def as_dict(self):
            return dict(row)

    runtime = SimpleNamespace(settings=SimpleNamespace(bearish_continuation_enabled=False),
        futures_market=object(), run_crypto_scan=AsyncMock(return_value=SimpleNamespace(
            opportunities=[Opportunity()], counts={}, receipt=SimpleNamespace(scan_id="scan", as_dict=lambda: {}))))

    async def reconcile(**kwargs):
        kwargs["quote_sink"]["TESTUSDT"] = replace(QUOTE, observed_at=datetime.now(UTC))
        return [dict(p, radar_origin="retained") for p in kwargs["prior_opportunities"]]

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(portal, "get_portal_runtime", AsyncMock(return_value=runtime))
    monkeypatch.setattr(portal, "reconcile_radar_cohort", reconcile)
    monkeypatch.setattr(portal, "_screener_cache", {})
    monkeypatch.setattr(portal, "_cache_timestamp", None)
    monkeypatch.setattr(portal, "atomic_write_json", lambda *a, **kw: None)
    first = (await portal._do_crypto_screen())["opportunities"][0]
    signal = json.loads(json.dumps(first["radar_signal"]))
    assert signal["price"] == "100"  # Actual quote, not screening candle's 99.
    row["current_price"] = "120"
    second = (await portal._do_crypto_screen())["opportunities"][0]
    assert second["radar_signal"] == signal
    # Simulate restored legacy cache; do not invent a historic publication price.
    portal._screener_cache["opportunities"][0].pop("radar_signal")
    assert "radar_signal" not in (await portal._do_crypto_screen())["opportunities"][0]
    row["setup_label"] = "VCP_COMPRESSION_LONG"
    row["evaluated_at"] = (now + timedelta(seconds=1)).isoformat()
    replacement = (await portal._do_crypto_screen())["opportunities"][0]
    assert replacement["radar_signal"]["episode_id"] != signal["episode_id"]


@pytest.mark.asyncio
async def test_prior_and_fresh_share_one_bounded_quote_call():
    other = make_instrument(symbol="OTHERUSDT", venue=Venue.BINANCE_USDM)
    provider = SimpleNamespace(instruments=AsyncMock(return_value=[INST, other]),
        quotes=AsyncMock(return_value=[QUOTE]))
    # Invalid legacy timestamp avoids candle calls, while exercising the shared quote batch.
    prior = {"symbol": "MISSINGUSDT", "direction": "long"}
    sink = {}
    rows = await reconcile_radar_cohort([prior], set(), provider, NOW,
        additional_instruments=[INST, other, INST], quote_sink=sink)
    provider.instruments.assert_awaited_once()
    provider.quotes.assert_awaited_once_with([INST, other])
    assert rows[0]["radar_data_status"] == "unavailable"
    assert sink == {"TESTUSDT": QUOTE}
