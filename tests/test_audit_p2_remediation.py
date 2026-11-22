"""Regression tests for Audit Batch 2 remediation (A2, A7, A8, A9, A10, A11)."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

from algotrader.api import portal
from algotrader.application.crypto_screener import (
    CryptoOpportunityScreener,
    _select_canonical_pairs,
)
from algotrader.config import Settings
from algotrader.domain.liquidity import SymbolLiquidityMetrics, compute_ili_ranks
from algotrader.domain.models import (
    AssetClass,
    Instrument,
    InstrumentId,
    MarketKind,
    Quote,
    Venue,
)
from algotrader.domain.radar_lifecycle import reverify_candidate_gates
from algotrader.domain.scan_observations import ScanReceipt
from algotrader.domain.screener_policy import SCREENING_POLICY_VERSION
from algotrader.infrastructure.runtime import ScreeningRun


def _inst(symbol: str, quote_asset: str = "USDT") -> Instrument:
    base = symbol.removesuffix(quote_asset)
    return Instrument(
        id=InstrumentId(Venue.BINANCE_USDM, symbol),
        base_asset=base,
        quote_asset=quote_asset,
        asset_class=AssetClass.CRYPTO,
        market_kind=MarketKind.FUTURES,
        trading=True,
        price_tick=Decimal("0.0001"),
        quantity_step=Decimal("0.1"),
        min_quantity=Decimal("0.1"),
        min_notional=Decimal("5.0"),
    )


def _quote(symbol: str, vol: str, bid: str = "99.95", ask: str = "100.05") -> Quote:
    return Quote(
        instrument_id=InstrumentId(Venue.BINANCE_USDM, symbol),
        bid=Decimal(bid),
        ask=Decimal(ask),
        last=(Decimal(bid) + Decimal(ask)) / Decimal("2"),
        quote_volume_24h=Decimal(vol),
    )


# --- A2: Archetype- and derivatives-aware retention gates ---


def test_a2_retention_rejects_acute_oi_flush_and_heavy_selling():
    candles = [{"close": 100.0, "high": 102.0, "low": 98.0} for _ in range(72)]
    candidate = {
        "symbol": "DRAINUSDT",
        "direction": "long",
        "setup_label": "VCP_COMPRESSION_LONG",
        "current_price": "100.0",
        "suggested_entry": "99.5",
        "poc_price": "99.0",
        "val_price": "97.0",
        "vah_price": "103.0",
        "radar_metrics": {
            "bb_bandwidth_4h": "0.12",
            "oi_change_7d_pct": "+5.0%",
            "oi_change_1h_pct": "-10.0%",
            "taker_buy_sell_ratio": "0.10",
            "funding_rate_bps": "+100.00 bps",
            "volume_contraction_ratio": "0.90",
        },
    }
    items, passed, total, failed = reverify_candidate_gates(
        candidate,
        current_price=Decimal("100.0"),
        recent_candles_1h=candles,
        ema50_4h=Decimal("98.0"),
    )
    assert total == 5
    assert passed == 4
    assert "Smart Money Absorption" in failed
    sm_item = next(i for i in items if i["title"] == "Smart Money Absorption")
    assert sm_item["passed"] is False
    assert sm_item["status"] == "failed"


def test_a2_predict_below_poc_emerging_reclaim_passes_value_gate_at_discovery_price():
    frozen_path = Path("tmp/architecture-audit-20261122/frozen-predict.json")
    if frozen_path.exists():
        candidate = json.loads(frozen_path.read_text(encoding="utf-8"))
        candles = candidate.get("candles", [])
    else:
        candles = [{"close": 1.1203, "high": 1.13, "low": 1.05} for _ in range(72)]
        candidate = {
            "symbol": "PREDICTUSDT",
            "direction": "long",
            "setup_label": "MOMENTUM_EMERGING_LONG",
            "current_price": "1.1203",
            "suggested_entry": "1.1034",
            "poc_price": "1.2781000",
            "val_price": "1.1931000",
            "vah_price": "1.5119000",
        }
    candidate = {
        **candidate,
        "radar_metrics": {
            "bb_bandwidth_4h": "0.14",
            "oi_change_7d_pct": "+19.09%",
            "oi_change_1h_pct": "-1.45%",
            "taker_buy_sell_ratio": "1.3586",
            "funding_rate_bps": "+0.50 bps",
            "volume_contraction_ratio": "1.39",
        },
    }
    items, passed, total, failed = reverify_candidate_gates(
        candidate,
        current_price=Decimal("1.1203"),
        recent_candles_1h=candles,
        ema50_4h=Decimal("1.0839"),
    )
    val_item = next(i for i in items if i["title"] == "Value Acceptance")
    assert val_item["passed"] is True
    assert val_item["status"] == "passed"
    assert passed == 5
    assert failed == ()


# --- A7: Permutation-invariant canonical USDT/USDC selection ---


def test_a7_canonical_pair_selection_is_order_invariant():
    usdt = _inst("TESTUSDT", "USDT")
    usdc = _inst("TESTUSDC", "USDC")
    # Within 5% band ($1.00M vs $1.04M): USDT must win regardless of input order
    quotes_within = {"TESTUSDT": _quote("TESTUSDT", "1000000"), "TESTUSDC": _quote("TESTUSDC", "1040000")}
    fwd = _select_canonical_pairs([usdt, usdc], quotes_within)
    rev = _select_canonical_pairs([usdc, usdt], quotes_within)
    assert fwd["TEST"].id.symbol == "TESTUSDT"
    assert rev["TEST"].id.symbol == "TESTUSDT"

    # Outside 5% band ($1.00M vs $1.10M): USDC must win regardless of input order
    quotes_outside = {"TESTUSDT": _quote("TESTUSDT", "1000000"), "TESTUSDC": _quote("TESTUSDC", "1100000")}
    fwd2 = _select_canonical_pairs([usdt, usdc], quotes_outside)
    rev2 = _select_canonical_pairs([usdc, usdt], quotes_outside)
    assert fwd2["TEST"].id.symbol == "TESTUSDC"
    assert rev2["TEST"].id.symbol == "TESTUSDC"


# --- A8: Honest ILI provenance when 30d median vol / OI USD are missing ---


def test_a8_ili_exposes_missing_inputs_instead_of_fabricating_values():
    ranked = compute_ili_ranks(
        [
            SymbolLiquidityMetrics(symbol="HIGH", quote_vol_24h=Decimal("2000000")),
            SymbolLiquidityMetrics(symbol="LOW", quote_vol_24h=Decimal("1000000")),
        ]
    )
    assert ranked[0].symbol == "HIGH"
    for item in ranked:
        assert item.median_quote_vol_30d is None
        assert item.open_interest_usd is None
        assert item.inputs_mode == "volume_24h_proxy_only"
        assert item.missing_inputs == ("median_quote_vol_30d", "open_interest_usd")


# --- A9: Honest fetch-failure accounting & scheduled scan is_scanning visibility ---


async def test_a9_failed_candle_fetches_counted_as_failed_not_evaluated():
    inst = _inst("FAILUSDT")
    market = AsyncMock()
    market.instruments.return_value = [inst]
    market.quotes.return_value = [_quote("FAILUSDT", "5000000")]
    market.candles.side_effect = RuntimeError("upstream 502")
    macro = AsyncMock()
    macro.get_context.return_value = AsyncMock(
        multiplier_for=lambda _d: Decimal("1"), narrative=lambda: "Neutral", coi_score=None
    )

    screener = CryptoOpportunityScreener(
        Settings(_env_file=None, screener_cron_enabled=False),
        macro_provider=macro,
        feature_contract="discovery_v2",
    )
    await screener.screen(market, max_symbols=10)
    assert screener.scan_counts["attempted"] == 1
    assert screener.scan_counts["evaluated"] == 0
    assert screener.scan_counts["failed"] == 1
    assert screener.scan_counts["unavailable_input_symbols"] == 1


async def test_a9_scheduled_scan_visibility_exposes_is_scanning_true(monkeypatch):
    monkeypatch.setattr(portal, "_screener_task", None)
    monkeypatch.setattr(portal, "_is_refreshing", True)
    monkeypatch.setattr(portal, "_shared_runtime", None)
    monkeypatch.setattr(
        portal,
        "_screener_cache",
        {
            "timestamp": datetime.now(UTC).isoformat(),
            "screening_policy_version": SCREENING_POLICY_VERSION,
            "radar_lifecycle_version": portal.RADAR_PUBLICATION_VERSION,
            "opportunities": [{"symbol": "KEEPUSDT"}],
            "qualifying_count": 1,
        },
    )
    res = await portal.get_live_screener()
    assert res["is_scanning"] is True
    assert res["scan_source"] == "scheduled"
    assert res["opportunities"] == [{"symbol": "KEEPUSDT"}]


# --- A11: Active episode origin_scan_id pinned during pruning ---


async def test_a11_active_episode_origin_scan_id_is_pinned_during_prune(monkeypatch, tmp_path):
    origin_id = "11111111-1111-1111-1111-111111111111"
    current_id = "22222222-2222-2222-2222-222222222222"
    candidate = {
        "symbol": "PINUSDT",
        "base_asset": "PIN",
        "origin_scan_id": origin_id,
        "first_seen_at": datetime.now(UTC).isoformat(),
        "radar_data_status": "current",
        "failed_gates": [],
        "checklist": [{"passed": True, "status": "passed"} for _ in range(5)],
    }
    runtime = AsyncMock()
    runtime.settings = Settings(
        _env_file=None, research_archive_dir=tmp_path / "research", screener_cron_enabled=False
    )
    runtime.run_crypto_scan.return_value = ScreeningRun([], {}, ScanReceipt(current_id, "saved"))

    captured_kwargs: dict[str, object] = {}

    def fake_prune(self, **kwargs):
        captured_kwargs.update(kwargs)
        return ()

    monkeypatch.chdir(tmp_path)
    with (
        patch.object(
            portal,
            "_screener_cache",
            {
                "opportunities": [candidate],
                "screening_policy_version": SCREENING_POLICY_VERSION,
            },
        ),
        patch.object(portal, "get_portal_runtime", return_value=runtime),
        patch.object(portal, "reconcile_radar_cohort", return_value=[candidate]),
        patch.object(portal, "atomic_write_json"),
        patch.object(portal.LocalScanArchive, "prune_scans", fake_prune),
    ):
        await portal._do_crypto_screen()

    assert captured_kwargs["max_scans"] == 49
    assert origin_id in captured_kwargs["preserve_scan_ids"]
    assert current_id in captured_kwargs["preserve_scan_ids"]
