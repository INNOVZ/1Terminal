from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from algotrader import cli
from algotrader.api import portal
from algotrader.application.crypto_screener import CryptoOpportunityScreener
from algotrader.domain.crypto_screener_models import (
    ChecklistVerification,
    CryptoMarketRegime,
    CryptoOpportunity,
    CryptoScreenerScoreBreakdown,
    SetupLabel,
)
from algotrader.domain.models import Direction, MarketKind, Quote, Venue
from algotrader.domain.scan_observations import ScanReceipt
from algotrader.domain.screener_policy import SCREENING_POLICY_VERSION
from algotrader.infrastructure.runtime import ScreeningRun
from algotrader.jobs import screener_cron
from tests.conftest import make_instrument


def _opportunity(direction: Direction = Direction.SHORT) -> CryptoOpportunity:
    score = Decimal("80")
    instrument = make_instrument(venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES, symbol="TESTUSDT")
    return CryptoOpportunity(
        instrument=instrument, direction=direction, regime=CryptoMarketRegime.VOLATILITY_SQUEEZE,
        setup_label=SetupLabel.DISTRIBUTION_SHORT if direction is Direction.SHORT else SetupLabel.VCP_COMPRESSION_LONG,
        score=score,
        score_breakdown=CryptoScreenerScoreBreakdown(score, score, score, score, score, score, Decimal("1"), score),
        current_price=Decimal("100"), atr14=Decimal("2"), suggested_entry=Decimal("100"),
        suggested_stop=Decimal("105"), suggested_targets=(Decimal("95"), Decimal("92.25"), Decimal("90")),
        risk_reward_ratio=Decimal("1.55"), btc_beta=Decimal("1"), residual_return_24h=Decimal("0"),
        funding_rate=Decimal("0.0001"), oi_change_1h=Decimal("0.01"), taker_ratio=Decimal("0.9"),
        rationale=(), macro_narrative="neutral", conviction=score, expected_value_r=Decimal("0.25"),
        checklist_verification=ChecklistVerification(True, True, True, True, False), grade="Grade B",
        # Radar admission uses the displayed checklist, separately from core consensus.
        checklist=tuple((f"gate-{i}", True, "verified") for i in range(5)),
    )


@pytest.mark.parametrize(
    ("direction", "rr", "expected"),
    [(Direction.SHORT, "1.55", True), (Direction.SHORT, "1.49", False), (Direction.LONG, "1.55", False)],
)
async def test_public_screen_min_score_cannot_bypass_directional_reward_risk_policy(
    settings: Any, monkeypatch: Any, direction: Direction, rr: str, expected: bool
) -> None:
    opportunity = replace(_opportunity(direction), risk_reward_ratio=Decimal(rr))
    instrument = opportunity.instrument
    market = SimpleNamespace(
        instruments=AsyncMock(return_value=[instrument]),
        quotes=AsyncMock(return_value=[Quote(instrument.id, Decimal("99.95"), Decimal("100.05"), Decimal("100"), Decimal("5000000"))]),
    )
    macro = SimpleNamespace(get_context=AsyncMock(return_value=SimpleNamespace(narrative=lambda: "neutral", coi_score=None)))
    screener = CryptoOpportunityScreener(settings, macro_provider=macro)
    monkeypatch.setattr(screener, "_evaluate_single", AsyncMock(return_value=[opportunity]))

    result = await screener.screen(market, min_score=Decimal("10"), enforce_quality=True)

    assert bool(result) is expected


async def test_api_preserves_a_qualified_short_below_two_r(monkeypatch: Any) -> None:
    runtime = SimpleNamespace(
        settings=None, futures_market=None, last_scan_counts={"evaluated": 1},
        run_crypto_scan=AsyncMock(return_value=ScreeningRun([_opportunity()], {"evaluated": 1}, ScanReceipt("fixture-scan", "saved"))),
    )
    # Avoid the unrelated optional chart-candle network request in this route.
    runtime.run_crypto_scan.return_value.opportunities[0] = replace(_opportunity(), candles=({"time": 1},))
    monkeypatch.setattr(portal, "get_portal_runtime", AsyncMock(return_value=runtime))
    monkeypatch.setattr(portal, "atomic_write_json", lambda *_: None)
    monkeypatch.setattr(portal, "_screener_cache", {})
    monkeypatch.setattr(portal, "_cache_timestamp", None)

    result = await portal._do_crypto_screen()

    assert result["qualifying_count"] == 1
    assert result["opportunities"][0]["symbol"] == "TESTUSDT"


@pytest.mark.parametrize("surface", ["api", "cli", "cron"])
async def test_entry_points_preserve_runtime_publication_order(
    settings: Any, monkeypatch: Any, tmp_path: Any, capsys: Any, surface: str
) -> None:
    short = replace(_opportunity(), candles=({"time": 1},),
                    checklist_verification=ChecklistVerification(True, True, True, True, True))
    long = replace(
        _opportunity(Direction.LONG),
        instrument=make_instrument(venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES, symbol="MOVEUSDT"),
        score=Decimal("85"), conviction=Decimal("82"), risk_reward_ratio=Decimal("2"),
        suggested_stop=Decimal("95"), suggested_targets=(Decimal("105"), Decimal("110"), Decimal("115")),
        checklist_verification=ChecklistVerification(True, True, True, False, False), candles=({"time": 1},),
    )
    runtime = SimpleNamespace(
        settings=settings, futures_market=None, last_scan_counts={"evaluated": 2},
        run_crypto_scan=AsyncMock(return_value=ScreeningRun([short, long], {"evaluated": 2}, ScanReceipt("fixture-scan", "saved"))), close=AsyncMock(),
    )
    monkeypatch.chdir(tmp_path)
    if surface == "api":
        monkeypatch.setattr(portal, "get_portal_runtime", AsyncMock(return_value=runtime))
        monkeypatch.setattr(portal, "_screener_cache", {})
        monkeypatch.setattr(portal, "_cache_timestamp", None)
        result = await portal._do_crypto_screen()
    elif surface == "cli":
        monkeypatch.setattr(cli, "load_settings", lambda: settings)
        monkeypatch.setattr(cli, "build_runtime", AsyncMock(return_value=runtime))
        await cli._screen("all", 50, 2)
        result = json.loads(capsys.readouterr().out)
    else:
        monkeypatch.setattr(screener_cron, "load_settings", lambda: settings)
        result = await screener_cron.execute_screener_job(runtime, max_symbols=2)

    assert [item["symbol"] for item in result["opportunities"]] == ["TESTUSDT", "MOVEUSDT"]
    assert result["screening_policy_version"] == SCREENING_POLICY_VERSION
    assert result["scan_id"] == "fixture-scan"
    assert result["scan_archive"]["status"] == "saved"


@pytest.mark.parametrize("stop", [Decimal("-0.60"), Decimal("0"), Decimal("101"), Decimal("NaN"), Decimal("Infinity")])
@pytest.mark.parametrize("enforce_quality", [False, True])
async def test_invalid_execution_prices_cannot_publish_through_screen(settings, monkeypatch, stop, enforce_quality):
    from algotrader.domain.screener_policy import publication_rejection_reasons

    opportunity = replace(_opportunity(Direction.LONG), suggested_stop=stop,
                          suggested_targets=(Decimal("110"), Decimal("120"), Decimal("130")),
                          risk_reward_ratio=Decimal("2.5"))
    assert publication_rejection_reasons(opportunity) == ("invalid_execution_prices",)
    market = SimpleNamespace(
        instruments=AsyncMock(return_value=[opportunity.instrument]),
        quotes=AsyncMock(return_value=[Quote(opportunity.instrument.id, Decimal("100"), Decimal("100"),
                                            Decimal("100"), Decimal("5000000"))]),
    )
    macro = SimpleNamespace(get_context=AsyncMock(return_value=SimpleNamespace(narrative=lambda: "neutral", coi_score=None)))
    screener = CryptoOpportunityScreener(settings, macro_provider=macro)
    monkeypatch.setattr(screener, "_evaluate_single", AsyncMock(return_value=[opportunity]))
    assert await screener.screen(market, min_score=Decimal("10"), enforce_quality=enforce_quality) == []
