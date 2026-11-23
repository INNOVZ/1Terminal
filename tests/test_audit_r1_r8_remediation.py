"""End-to-end regression tests for independent review findings R2–R6 and R8."""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest

from algotrader.adapters.binance.futures import BinanceUsdmAdapter
from algotrader.adapters.external.models import MacroSentimentContext
from algotrader.api import portal
from algotrader.api.portal_app import create_app
from algotrader.application.crypto_screener import CryptoOpportunityScreener
from algotrader.application.radar_service import reconcile_radar_cohort
from algotrader.config import Settings
from algotrader.domain.models import (
    AssetClass,
    Candle,
    Instrument,
    InstrumentId,
    MarketKind,
    Quote,
    Venue,
)
from algotrader.domain.radar_lifecycle import (
    evaluate_radar_candidate_lifecycle,
    reverify_candidate_gates,
)
from algotrader.domain.scan_observations import (
    ScanReceipt,
    ScanRecord,
    decode_value,
    encode_value,
    snapshot_recorded_value,
)
from algotrader.domain.screener_policy import SCREENING_POLICY_VERSION
from algotrader.infrastructure.runtime import Runtime, ScreeningRun
from algotrader.infrastructure.scan_archive import LocalScanArchive


def _inst(symbol: str = "TESTUSDT") -> Instrument:
    return Instrument(
        id=InstrumentId(Venue.BINANCE_USDM, symbol),
        base_asset=symbol.removesuffix("USDT"),
        quote_asset="USDT",
        asset_class=AssetClass.CRYPTO,
        market_kind=MarketKind.FUTURES,
        trading=True,
        price_tick=Decimal("0.01"),
        quantity_step=Decimal("0.1"),
        min_quantity=Decimal("0.1"),
        min_notional=Decimal("5.0"),
    )


# --- R2: Chart and Live publication projection parity ---


@pytest.mark.parametrize(
    "variant",
    ["disabled_continuation", "obsolete_policy", "obsolete_radar_version", "stale_cache", "degraded_zero_inputs"],
)
async def test_r2_chart_and_live_enforce_identical_publication_policy(variant: str) -> None:
    now = datetime.now(UTC)
    settings = Settings(
        _env_file=None,
        screener_cron_enabled=False,
        bearish_continuation_enabled=False,
    )
    inst = _inst("TESTUSDT")
    candle = Candle(
        opened_at=now - timedelta(hours=2),
        closed_at=now - timedelta(hours=1),
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=Decimal("100"),
    )
    row = {
        "symbol": "TESTUSDT",
        "direction": "long",
        "setup_label": "VCP_COMPRESSION_LONG",
        "evaluated_at": now.isoformat(),
    }
    cache: dict[str, object] = {
        "timestamp": now.isoformat(),
        "screening_policy_version": SCREENING_POLICY_VERSION,
        "radar_lifecycle_version": portal.RADAR_PUBLICATION_VERSION,
        "opportunities": [dict(row)],
        "qualifying_count": 1,
    }
    cache_ts: datetime | None = now
    if variant == "disabled_continuation":
        cache["opportunities"] = [{**row, "direction": "short", "setup_label": "BREAKDOWN_RETEST_SHORT"}]
    elif variant == "obsolete_policy":
        cache["screening_policy_version"] = "obsolete-policy"
    elif variant == "obsolete_radar_version":
        cache["radar_lifecycle_version"] = "radar-v3"
    elif variant == "stale_cache":
        stale_ts = now - timedelta(days=2)
        cache["timestamp"] = stale_ts.isoformat()
        cache_ts = stale_ts
    elif variant == "degraded_zero_inputs":
        cache["data_status"] = "degraded_zero_usable_inputs"

    futures = SimpleNamespace(candles=AsyncMock(return_value=[candle]), venue=Venue.BINANCE_USDM)
    runtime = SimpleNamespace(settings=settings, futures_market=futures, _closed=False)

    with (
        patch.multiple(
            portal,
            _screener_cache=cache,
            _cache_timestamp=cache_ts,
            _screener_task=None,
            _is_refreshing=False,
            _shared_runtime=runtime,
            _cached_instruments=[inst],
        ),
        patch.object(portal, "get_portal_runtime", AsyncMock(return_value=runtime)),
        patch.object(portal, "_watchlist_service", SimpleNamespace(get_item=lambda _s: None)),
        patch.object(portal, "_do_crypto_screen", AsyncMock(return_value={})),
    ):
        live = await portal.get_live_screener()
        chart = await portal.get_symbol_chart("TESTUSDT")
        if portal._screener_task:
            await portal._screener_task

    assert live["opportunities"] == []
    assert chart["opportunity"] is None
    assert chart["record_kind"] == "chart_inspection"


# --- R3: Failure preserves episode continuity and persists across restart ---


async def test_r3_failure_preserves_episode_roster_and_persists_across_restart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    now = datetime.now(UTC)
    settings = Settings(
        _env_file=None,
        screener_cron_enabled=False,
        research_archive_dir=tmp_path / "research",
    )
    episode = {
        "symbol": "CONTUSDT",
        "base_asset": "CONT",
        "direction": "long",
        "setup_label": "VCP_COMPRESSION_LONG",
        "origin_scan_id": "11111111-1111-1111-1111-111111111111",
        "first_seen_at": (now - timedelta(hours=2)).isoformat(),
        "radar_triggered_at": (now - timedelta(hours=1)).isoformat(),
        "radar_trigger_basis": "breakout",
        "radar_trigger_entry": "104",
        "radar_trigger_stop": "101",
        "radar_signal": {"episode_id": "ep-001"},
        "evaluated_at": (now - timedelta(hours=2)).isoformat(),
    }
    initial_cache = {
        "timestamp": now.isoformat(),
        "screening_policy_version": SCREENING_POLICY_VERSION,
        "radar_lifecycle_version": portal.RADAR_PUBLICATION_VERSION,
        "data_status": "current",
        "stale": False,
        "macro_narrative": "Constructive risk-on tape",
        "opportunities": [episode],
        "qualifying_count": 1,
    }
    cache_file = tmp_path / "data" / "screener_latest.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(initial_cache), encoding="utf-8")

    futures = SimpleNamespace(venue=Venue.BINANCE_USDM)
    runtime = SimpleNamespace(settings=settings, futures_market=futures, _closed=False)

    monkeypatch.setattr(portal, "_screener_cache", dict(initial_cache))
    monkeypatch.setattr(portal, "_cache_timestamp", now)
    monkeypatch.setattr(portal, "_episode_roster", [])
    monkeypatch.setattr(portal, "_episode_policy_version", None)
    monkeypatch.setattr(portal, "_screener_task", None)
    monkeypatch.setattr(portal, "_is_refreshing", False)
    monkeypatch.setattr(portal, "_shared_runtime", runtime)
    monkeypatch.setattr(portal, "_consecutive_scan_failures", 0)
    monkeypatch.setattr(portal, "_last_scan_error", None)

    # 1. Scan fails -> public opportunities empty, failure persisted to disk, private roster intact
    with patch.object(portal, "get_portal_runtime", AsyncMock(side_effect=RuntimeError("upstream 503"))):
        fail_res = await portal._do_crypto_screen()

    assert fail_res["data_status"] == "scan_failed"
    assert fail_res["opportunities"] == []
    assert "_prior_episodes" not in fail_res
    disk_data = json.loads(cache_file.read_text(encoding="utf-8"))
    assert disk_data["data_status"] == "scan_failed"
    assert disk_data["opportunities"] == []
    assert len(disk_data["_prior_episodes"]) == 1
    assert disk_data["_prior_episodes"][0]["origin_scan_id"] == episode["origin_scan_id"]

    # 2. Simulate full process restart from disk -> must NOT resurrect pre-failure cards
    monkeypatch.setattr(portal, "_screener_cache", {})
    monkeypatch.setattr(portal, "_cache_timestamp", None)
    monkeypatch.setattr(portal, "_episode_roster", [])
    monkeypatch.setattr(portal, "_episode_policy_version", None)
    monkeypatch.setattr(portal, "_consecutive_scan_failures", 0)

    loaded = portal.warm_screener_cache(cache_file)
    assert loaded is not None
    assert loaded["data_status"] == "scan_failed"
    assert "_prior_episodes" not in loaded

    live_after_restart = await portal.get_live_screener()
    assert live_after_restart["data_status"] == "scan_failed"
    assert live_after_restart["opportunities"] == []

    # 3. Next successful scan reconciles the preserved private episode seamlessly!
    next_runtime = SimpleNamespace(
        settings=settings,
        futures_market=futures,
        run_crypto_scan=AsyncMock(
            return_value=ScreeningRun(
                [],
                {"attempted": 10, "evaluated": 10, "failed": 0},
                ScanReceipt(str(uuid4()), "saved"),
            )
        ),
    )
    reconcile_mock = AsyncMock(return_value=[])
    with (
        patch.object(portal, "get_portal_runtime", AsyncMock(return_value=next_runtime)),
        patch.object(portal, "reconcile_radar_cohort", reconcile_mock),
        patch.object(portal.LocalScanArchive, "prune_scans", return_value=()),
    ):
        await portal._do_crypto_screen()

    passed_prior = reconcile_mock.call_args.kwargs["prior_opportunities"]
    assert len(passed_prior) == 1
    assert passed_prior[0]["origin_scan_id"] == episode["origin_scan_id"]
    assert passed_prior[0]["radar_triggered_at"] == episode["radar_triggered_at"]
    assert passed_prior[0]["radar_signal"] == {"episode_id": "ep-001"}


# --- R4: Strict live quote component timestamp validation ---


@pytest.mark.parametrize(
    "variant",
    ["missing_both", "missing_book_time", "missing_ticker_time", "future_book_current_ticker", "stale_ticker_current_book"],
)
async def test_r4_live_quotes_reject_missing_stale_or_future_component_timestamps(variant: str) -> None:
    now = datetime.now(UTC)
    settings = Settings(_env_file=None, screener_cron_enabled=False)
    inst = _inst("TIMEUSDT")

    class FakeClient:
        async def request(self, method: str, path: str, **kwargs: object) -> list[dict[str, object]]:
            if path.endswith("bookTicker"):
                row: dict[str, object] = {"symbol": "TIMEUSDT", "bidPrice": "99.95", "askPrice": "100.05"}
                if variant == "missing_ticker_time":
                    row["time"] = int(now.timestamp() * 1000)
                elif variant == "future_book_current_ticker":
                    row["time"] = int((now + timedelta(days=1)).timestamp() * 1000)
                elif variant == "stale_ticker_current_book":
                    row["time"] = int(now.timestamp() * 1000)
                return [row]
            if path.endswith("24hr"):
                row = {"symbol": "TIMEUSDT", "quoteVolume": "5000000"}
                if variant in ("missing_book_time", "future_book_current_ticker"):
                    row["closeTime"] = int(now.timestamp() * 1000)
                elif variant == "stale_ticker_current_book":
                    row["closeTime"] = int((now - timedelta(days=2)).timestamp() * 1000)
                return [row]
            return []

    quotes = await BinanceUsdmAdapter(FakeClient()).quotes([inst])  # type: ignore[arg-type]
    assert len(quotes) == 1
    q = quotes[0]
    assert q.received_at is not None
    assert q.is_fresh_at(q.received_at) is False

    engine = CryptoOpportunityScreener(
        settings,
        SimpleNamespace(get_context=AsyncMock(return_value=MacroSentimentContext())),  # type: ignore[arg-type]
        feature_contract="discovery_v2",
    )
    selected: list[str] = []

    async def capture(**kwargs: object) -> list[object]:
        selected.append(str(kwargs["inst"]))
        return []

    engine._evaluate_single = capture  # type: ignore[method-assign]
    await engine.screen(
        SimpleNamespace(instruments=AsyncMock(return_value=[inst]), quotes=AsyncMock(return_value=[q])),  # type: ignore[arg-type]
        max_symbols=1,
    )
    assert selected == []


def test_r4_legacy_quote_wire_encoding_stays_byte_compatible() -> None:
    now = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
    inst = _inst("WIREUSDT")
    legacy = Quote(inst.id, Decimal("99"), Decimal("101"), Decimal("100"), Decimal("1000"), observed_at=now)
    encoded = encode_value(legacy)
    for opt_field in ("exchange_observed_at", "received_at", "book_observed_at", "ticker_observed_at"):
        assert opt_field not in encoded["fields"]
    assert encode_value(decode_value(encoded)) == encoded
    assert encode_value(snapshot_recorded_value(legacy)) == encoded


# --- R5: Observed-trigger profit exception, required guard inputs, immutable discovery_price ---


def test_r5_untriggered_setup_never_receives_profit_expansion_exception() -> None:
    now = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
    candles = [
        {
            "time": int((now - timedelta(hours=71 - i)).timestamp()),
            "opened_time": int((now - timedelta(hours=72 - i)).timestamp()),
            "close": "100",
            "high": "101",
            "low": "99",
        }
        for i in range(72)
    ]
    candidate = {
        "symbol": "PENDUSDT",
        "direction": "long",
        "setup_label": "VCP_COMPRESSION_LONG",
        "execution_mode": "RETEST_ACCUMULATION",
        "current_price": "100",
        "suggested_entry": "95",
        "breakout_trigger": "110",
        "suggested_stop": "90",
        "suggested_targets": ["120", "130", "140"],
        "poc_price": "99",
        "val_price": "97",
        "vah_price": "110",
        "evaluated_at": (now - timedelta(hours=3)).isoformat(),
        "radar_metrics": {
            "bb_bandwidth_4h": "0.10",
            "oi_change_7d_pct": "5%",
            "oi_change_1h_pct": "0%",
            "taker_buy_sell_ratio": "0.90",
            "funding_rate_bps": "20 bps",
            "volume_contraction_ratio": "0.8",
        },
    }

    # Pending setup: price=100 > retest entry=95 by +5.2%, but NEVER triggered (low=99 > 95, high=101 < 110)
    decision = evaluate_radar_candidate_lifecycle(
        candidate, current_price=Decimal("100"), recent_candles_1h=candles, ema50_4h=Decimal("99"), now=now
    )
    assert decision.triggered_at is None
    assert decision.gates_passed == 4
    assert "Smart Money Absorption" in decision.failed_gates

    # Direct reverify_candidate_gates without trigger also fails Smart Money
    _, passed, total, failed = reverify_candidate_gates(
        candidate, current_price=Decimal("100"), recent_candles_1h=candles, ema50_4h=Decimal("99")
    )
    assert total == 5 and passed == 4 and "Smart Money Absorption" in failed

    # Once genuinely triggered at 95 and trading at 100 (>+2% profit), expansion tolerance applies
    triggered_cand = {
        **candidate,
        "radar_triggered_at": (now - timedelta(hours=1)).isoformat(),
        "radar_trigger_basis": "retest",
        "radar_trigger_entry": "95",
        "radar_trigger_stop": "90",
    }
    trig_decision = evaluate_radar_candidate_lifecycle(
        triggered_cand, current_price=Decimal("100"), recent_candles_1h=candles, ema50_4h=Decimal("99"), now=now
    )
    assert trig_decision.triggered_at is not None
    assert trig_decision.gates_passed == 5


@pytest.mark.parametrize("missing_key", ["oi_change_1h_pct", "taker_buy_sell_ratio", "funding_rate_bps"])
def test_r5_missing_derivatives_guard_input_returns_unknown_not_five_passes(missing_key: str) -> None:
    now = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
    candles = [
        {"time": int((now - timedelta(hours=71 - i)).timestamp()), "close": "100", "high": "101", "low": "99"}
        for i in range(72)
    ]
    metrics = {
        "bb_bandwidth_4h": "0.10",
        "oi_change_7d_pct": "5%",
        "oi_change_1h_pct": "1%",
        "taker_buy_sell_ratio": "1.10",
        "funding_rate_bps": "2 bps",
        "volume_contraction_ratio": "0.8",
    }
    metrics.pop(missing_key)
    candidate = {
        "symbol": "MISSUSDT",
        "direction": "long",
        "setup_label": "VCP_COMPRESSION_LONG",
        "current_price": "100",
        "suggested_entry": "99",
        "poc_price": "99",
        "val_price": "97",
        "vah_price": "105",
        "radar_metrics": metrics,
    }
    checks, passed, total, _ = reverify_candidate_gates(
        candidate, current_price=Decimal("100"), recent_candles_1h=candles, ema50_4h=Decimal("99")
    )
    assert total == 5 and passed == 4
    sm_check = next(c for c in checks if c["title"] == "Smart Money Absorption")
    assert sm_check["passed"] is False
    assert sm_check["status"] == "unknown"


async def test_r5_discovery_price_stays_immutable_across_refreshes_and_field_renamed() -> None:
    now = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
    inst = _inst("DISCUSDT")
    candidate = {
        "symbol": "DISCUSDT",
        "direction": "long",
        "setup_label": "VCP_COMPRESSION_LONG",
        "current_price": "95.0",  # discovered below POC=100
        "suggested_entry": "94.0",
        "breakout_trigger": "105.0",
        "suggested_stop": "90.0",
        "suggested_targets": ["110.0", "115.0"],
        "poc_price": "100.0",
        "val_price": "93.0",
        "vah_price": "104.0",
        "evaluated_at": (now - timedelta(hours=2)).isoformat(),
    }

    class Provider:
        price = Decimal("101.0")  # Refresh 1 moves above POC

        async def instruments(self) -> list[Instrument]:
            return [inst]

        async def quotes(self, instruments: list[Instrument]) -> list[Quote]:
            return [
                Quote(
                    inst.id,
                    self.price,
                    self.price,
                    self.price,
                    Decimal("10000000"),
                    observed_at=now,
                    exchange_observed_at=now,
                    received_at=now,
                    book_observed_at=now,
                    ticker_observed_at=now,
                )
            ]

        async def candles(self, instrument: Instrument, interval: str, limit: int) -> list[Candle]:
            hours = 1 if interval == "1h" else 4
            return [
                Candle(
                    opened_at=now - timedelta(hours=hours * (limit - i)),
                    closed_at=now - timedelta(hours=hours * (limit - i - 1)),
                    open=Decimal("96"),
                    high=Decimal("102"),
                    low=Decimal("94"),
                    close=Decimal("96"),
                    volume=Decimal("100"),
                )
                for i in range(limit)
            ]

        async def derivatives_snapshot(self, instrument: Instrument) -> SimpleNamespace:
            return SimpleNamespace(
                observed_at=now,
                degraded_telemetry=False,
                open_interest_change_7d=Decimal("0.10"),
                open_interest_change_1h=Decimal("0.01"),
                funding_rate=Decimal("0.0001"),
                taker_buy_sell_ratio=Decimal("1.10"),
            )

    provider = Provider()
    first = (await reconcile_radar_cohort([candidate], set(), provider, now=now))[0]
    assert first["discovery_price"] == "95.0"
    assert first["current_price"] == "101.0"
    assert "pending_retention_healthy" in first
    assert "fresh_entry_eligible" not in first

    # Refresh 2 pulls back below POC (96.0) — immutable discovery_price=95.0 still preserves below-POC reclaim
    provider.price = Decimal("96.0")
    second = (await reconcile_radar_cohort([first], set(), provider, now=now))[0]
    assert second["discovery_price"] == "95.0"
    val_gate = next(c for c in second["checklist"] if c["title"] == "Value Acceptance")
    assert val_gate["passed"] is True


# --- R6: Zero usable inputs & missing cache timestamp fail readiness ---


async def test_r6_all_inputs_failed_marks_degraded_and_readyz_503(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    settings = Settings(
        _env_file=None,
        screener_cron_enabled=False,
        research_archive_dir=tmp_path / "research",
    )
    outage_runtime = SimpleNamespace(
        settings=settings,
        futures_market=SimpleNamespace(venue=Venue.BINANCE_USDM),
        _closed=False,
        run_crypto_scan=AsyncMock(
            return_value=ScreeningRun(
                [],
                {"attempted": 320, "evaluated": 0, "failed": 320, "unavailable_input_symbols": 320},
                ScanReceipt(str(uuid4()), "saved"),
            )
        ),
    )

    async def ready() -> tuple[bool, dict[str, str]]:
        return await Runtime.readiness(outage_runtime)  # type: ignore[arg-type]

    outage_runtime.readiness = ready

    with (
        patch.multiple(
            portal,
            _screener_cache={},
            _cache_timestamp=None,
            _episode_roster=[],
            _episode_policy_version=None,
            _screener_task=None,
            _is_refreshing=False,
            _shared_runtime=outage_runtime,
            _consecutive_scan_failures=0,
            _last_scan_error=None,
        ),
        patch.object(portal, "get_portal_runtime", AsyncMock(return_value=outage_runtime)),
        patch.object(portal, "reconcile_radar_cohort", AsyncMock(return_value=[])),
        patch.object(portal.LocalScanArchive, "prune_scans", return_value=()),
    ):
        completed = await portal._do_crypto_screen()
        app = create_app(settings)
        app.state.runtime = outage_runtime
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://local"
        ) as client:
            ready_resp = await client.get("/readyz")
            live_resp = await client.get("/api/screener/live")

    assert completed["data_status"] == "degraded_zero_usable_inputs"
    assert completed["stale"] is True
    assert ready_resp.status_code == 503
    assert ready_resp.json()["data_status"] == "degraded_zero_usable_inputs"
    assert live_resp.json()["data_status"] == "degraded_zero_usable_inputs"
    assert live_resp.json()["opportunities"] == []


async def test_r6_missing_cache_timestamp_is_treated_as_stale_and_withheld() -> None:
    settings = Settings(_env_file=None, screener_cron_enabled=False)
    runtime = SimpleNamespace(settings=settings, _closed=False)
    no_ts_cache = {
        "screening_policy_version": SCREENING_POLICY_VERSION,
        "radar_lifecycle_version": portal.RADAR_PUBLICATION_VERSION,
        "opportunities": [{"symbol": "NOTSUSDT"}],
        "qualifying_count": 1,
    }
    with patch.multiple(
        portal,
        _screener_cache=no_ts_cache,
        _cache_timestamp=None,
        _screener_task=None,
        _shared_runtime=runtime,
    ):
        assert portal.is_screener_cache_stale(settings) is True
        live = await portal.get_live_screener()
        assert live["data_status"] == "stale"
        assert live["opportunities"] == []


# --- R8: Inclusive 48h archive retention & protected/unpinned accounting ---


def test_r8_prune_separates_protected_and_unpinned_counts_and_bytes(tmp_path: Path) -> None:
    archive = LocalScanArchive(tmp_path)
    now = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)

    # Create 51 hourly scans (t-50h .. t-0h) and pin the oldest active episode origin (t-50h)
    scan_ids: list[str] = []
    for hours_ago in range(50, -1, -1):
        sid = str(uuid4())
        ts = now - timedelta(hours=hours_ago)
        rec = ScanRecord(
            scan_id=sid,
            started_at=ts,
            completed_at=ts,
            status="complete",
            identity={"policy": SCREENING_POLICY_VERSION},
            request={"mode": "all"},
            inputs=(),
            decisions=(),
            opportunities=(),
            counts={"evaluated": 1},
            cohort=(),
        )
        archive.write_scan(rec)
        mtime = ts.timestamp()
        os.utime(tmp_path / "scans" / sid / "manifest.json", (mtime, mtime))
        scan_ids.append(sid)

    pinned_id = scan_ids[0]  # oldest scan (50h ago)
    pruned = archive.prune_scans(
        max_age_days=5,
        max_scans=49,
        max_bytes=10_000_000,
        now=now,
        preserve_scan_ids=frozenset({pinned_id}),
        exclude_pinned_from_count=True,
    )

    remaining = set(archive.scan_ids())
    # Pinned origin is preserved AND 49 unpinned hourly scans cover inclusive 0h..48h (total 50 kept, 1 pruned)
    assert pinned_id in remaining
    assert len(pruned) == 1
    assert archive.last_prune_stats["protected_count"] == 1
    assert archive.last_prune_stats["unpinned_count"] == 49
    assert archive.last_prune_stats["total_kept_count"] == 50
    assert archive.last_prune_stats["protected_bytes"] > 0
    assert archive.last_prune_stats["unpinned_bytes"] > 0


def test_below_poc_bottom_coil_target1_never_inverts_below_breakout_trigger() -> None:
    """When close < poc_30d and local_compression_high_4h <= poc_30d (e.g. GALAUSDT bottom base),
    Target 1 must remain strictly above breakout_trigger.
    """
    from algotrader.domain.execution_brackets import build_long_bracket

    bracket = build_long_bracket(
        close=Decimal("0.001293"),
        ema50_4h=Decimal("0.001280"),
        poc_30d=Decimal("0.001716"),
        atr14_4h=Decimal("0.000040"),
        local_compression_high_4h=Decimal("0.001340"),
        swing_low_4h=Decimal("0.001180"),
        high_30d=Decimal("0.001848"),
        vah_30d=Decimal("0.001795"),
        val_30d=Decimal("0.001402"),
        tick_size=Decimal("0.000001"),
        resistance_absorption_score=Decimal("58.0"),
    )
    assert bracket.retest_entry < Decimal("0.001293")
    assert bracket.t1 > bracket.breakout_trigger
    assert bracket.t2 > bracket.t1
    assert bracket.t3 > bracket.t2


async def test_retention_uses_4h_structural_taker_flow_when_1h_bar_is_quiet() -> None:
    """A single quiet 1h bar (taker_1h=0.83) must not fail Smart Money when 4h taker flow (1.08) is healthy."""
    now = datetime(2026, 11, 23, 3, 1, tzinfo=UTC)
    inst = _inst("KITEUSDT")
    candidate = {
        "symbol": "KITEUSDT",
        "direction": "long",
        "setup_label": "VCP_COMPRESSION_LONG",
        "current_price": "0.1414",
        "discovery_price": "0.1414",
        "suggested_entry": "0.1342",
        "breakout_trigger": "0.1446",
        "suggested_stop": "0.1124",
        "suggested_targets": ["0.1620", "0.1735", "0.1886"],
        "poc_price": "0.1285",
        "val_price": "0.1146",
        "vah_price": "0.1372",
        "evaluated_at": (now - timedelta(hours=2)).isoformat(),
    }

    class Provider:
        async def instruments(self) -> list[Instrument]:
            return [inst]

        async def quotes(self, instruments: list[Instrument]) -> list[Quote]:
            return [
                Quote(
                    inst.id,
                    Decimal("0.1414"),
                    Decimal("0.1414"),
                    Decimal("0.1414"),
                    Decimal("10000000"),
                    observed_at=now,
                    exchange_observed_at=now,
                    received_at=now,
                    book_observed_at=now,
                    ticker_observed_at=now,
                )
            ]

        async def candles(self, instrument: Instrument, interval: str, limit: int) -> list[Candle]:
            hours = 1 if interval == "1h" else 4
            return [
                Candle(
                    opened_at=now - timedelta(hours=hours * (limit - i)),
                    closed_at=now - timedelta(hours=hours * (limit - i - 1)),
                    open=Decimal("0.1400"),
                    high=Decimal("0.1420"),
                    low=Decimal("0.1390"),
                    close=Decimal("0.1414"),
                    volume=Decimal("100"),
                )
                for i in range(limit)
            ]

        async def derivatives_snapshot(self, instrument: Instrument) -> SimpleNamespace:
            return SimpleNamespace(
                observed_at=now,
                degraded_telemetry=False,
                open_interest_change_7d=Decimal("0.014"),
                open_interest_change_1h=Decimal("-0.001"),
                funding_rate=Decimal("0.00005"),
                taker_buy_sell_ratio=Decimal("0.83"),
                taker_buy_sell_ratio_4h=Decimal("1.08"),
            )

    retained = await reconcile_radar_cohort([candidate], set(), Provider(), now=now)
    assert len(retained) == 1
    assert retained[0]["gates_passed"] == 5
    assert retained[0]["failed_gates"] == []

