from __future__ import annotations

import json
from decimal import Decimal
from types import SimpleNamespace
from typing import cast

from algotrader.application.backtest import BacktestResult
from algotrader.cli import _package_version, _promotion_gate, _screen
from algotrader.config import StrategyProfile


def test_promotion_gate_rejects_an_empty_result_set() -> None:
    gate = _promotion_gate((), cost_multiplier=Decimal("2"), untouched_holdout_attested=True)

    assert gate["positive_expectancy_and_profit_factor"] is False
    assert gate["eligible_for_governed_review"] is False
    assert gate["eligible_for_promotion"] is False


def test_promotion_gate_never_treats_attestation_as_technical_proof() -> None:
    positive = cast(
        BacktestResult,
        SimpleNamespace(expectancy_r=Decimal("0.1"), profit_factor=Decimal("1.2")),
    )

    gate = _promotion_gate(
        (positive,), cost_multiplier=Decimal("2"), untouched_holdout_attested=True
    )

    assert gate["eligible_for_governed_review"] is True
    assert gate["eligible_for_promotion"] is False
    assert "immutable experiment registry" in str(gate["blocked_reason"])


def test_package_version_uses_installed_distribution_name(monkeypatch: object) -> None:
    requested: list[str] = []

    def version(distribution_name: str) -> str:
        requested.append(distribution_name)
        return "0.1.2"

    monkeypatch.setattr("algotrader.cli.metadata.version", version)  # type: ignore[attr-defined]

    assert _package_version() == "0.1.2"
    assert requested == ["atlas-algotrader"]


async def test_screen_reports_execution_and_observation_only_alt_momentum(
    monkeypatch: object, capsys: object
) -> None:
    class System:
        async def screen_with_diagnostics(self) -> tuple[list[object], dict[str, object]]:
            return [], {"long": {"evaluated": 1, "eligible": 0}}

        async def alt_momentum_leaderboard(self) -> dict[str, object]:
            return {
                "execution_authorized": False,
                "long_leaders": [{"instrument": "binance_usdm:AAAUSDT"}],
                "short_leaders": [{"instrument": "binance_usdm:BBBUSDT"}],
            }

    class Runtime:
        system = System()

        async def close(self) -> None:
            return None

    settings = SimpleNamespace(strategy_profile=StrategyProfile.LEGACY_MOMENTUM)
    monkeypatch.setattr("algotrader.cli.load_settings", lambda: settings)  # type: ignore[attr-defined]

    async def build_runtime(_: object) -> Runtime:
        return Runtime()

    monkeypatch.setattr("algotrader.cli.build_runtime", build_runtime)  # type: ignore[attr-defined]

    assert await _screen() == 0
    output = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert output["execution_screen"]["candidate_count"] == 0
    assert output["execution_screen"]["strategy_profile"] == "legacy_momentum_v1"
    assert output["execution_screen"]["order_path"] == "configured_strategy"
    assert "Only execution_screen.candidates" in output["order_authority"]


async def test_crypto_screen_cli_json_and_table_output(
    monkeypatch: object, capsys: object
) -> None:
    from algotrader.cli import _crypto_screen
    from algotrader.domain.crypto_screener_models import (
        CryptoMarketRegime,
        CryptoOpportunity,
        CryptoScreenerScoreBreakdown,
        SetupLabel,
    )
    from algotrader.domain.models import Direction
    from tests.conftest import make_instrument

    inst = make_instrument(symbol="SOLUSDT")
    mock_opp = CryptoOpportunity(
        instrument=inst,
        direction=Direction.LONG,
        regime=CryptoMarketRegime.STRONG_TREND_UP,
        setup_label=SetupLabel.BREAKOUT_LONG,
        score=Decimal("88.5"),
        score_breakdown=CryptoScreenerScoreBreakdown(
            trend_score=Decimal("90"),
            momentum_score=Decimal("85"),
            derivatives_score=Decimal("80"),
            alpha_score=Decimal("95"),
            volume_score=Decimal("80"),
            structure_score=Decimal("90"),
            macro_multiplier=Decimal("1.05"),
            final_composite_score=Decimal("88.5"),
        ),
        current_price=Decimal("150.00"),
        atr14=Decimal("4.5"),
        suggested_entry=Decimal("150.00"),
        suggested_stop=Decimal("143.25"),
        suggested_targets=(Decimal("156.75"), Decimal("163.50"), Decimal("170.25")),
        risk_reward_ratio=Decimal("2.0"),
        btc_beta=Decimal("1.15"),
        residual_return_24h=Decimal("0.045"),
        funding_rate=Decimal("-0.0001"),
        oi_change_1h=Decimal("0.035"),
        taker_ratio=Decimal("1.25"),
        rationale=("Trend Breakout with volume expansion",),
        macro_narrative="F&G: 60/100 (Greed) | Stablecoins 7d: +0.70% (Inflow)",
    )

    class MockSystem:
        async def crypto_screen(self, **kwargs: object) -> list[CryptoOpportunity]:
            return [mock_opp]

    class MockRuntime:
        system = MockSystem()

        async def close(self) -> None:
            return None

    settings = SimpleNamespace(strategy_profile=StrategyProfile.LEGACY_MOMENTUM)
    monkeypatch.setattr("algotrader.cli.load_settings", lambda: settings)  # type: ignore[attr-defined]

    async def mock_build_runtime(_: object) -> MockRuntime:
        return MockRuntime()

    monkeypatch.setattr("algotrader.cli.build_runtime", mock_build_runtime)  # type: ignore[attr-defined]

    # Test JSON output
    res_json = await _crypto_screen(
        min_score=Decimal("60"), direction="long", top_n=5, max_symbols=5, as_json=True
    )
    assert res_json == 0
    raw_json = capsys.readouterr().out  # type: ignore[attr-defined]
    parsed = json.loads(raw_json)
    assert len(parsed) == 1
    assert parsed[0]["symbol"] == "SOLUSDT"
    assert parsed[0]["setup_label"] == "BREAKOUT_LONG"
    assert parsed[0]["score"] == "88.5"

    # Test Table / Text output
    res_text = await _crypto_screen(
        min_score=Decimal("60"), direction=None, top_n=5, max_symbols=5, as_json=False
    )
    assert res_text == 0
    text_out = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "ATLAS CRYPTO OPPORTUNITY SCREENER" in text_out
    assert "SOLUSDT" in text_out
    assert "BREAKOUT_LONG" in text_out
    assert "🟢 LONG" in text_out


async def test_chart_verify_cli(monkeypatch: object, tmp_path: object) -> None:
    from datetime import UTC, datetime, timedelta
    from pathlib import Path
    from algotrader.cli import _chart_verify
    from algotrader.domain.crypto_screener_models import (
        CryptoMarketRegime,
        CryptoOpportunity,
        CryptoScreenerScoreBreakdown,
        SetupLabel,
    )
    from algotrader.domain.models import Candle, Direction
    from tests.conftest import make_instrument

    inst = make_instrument(symbol="SOLUSDT")
    mock_opp = CryptoOpportunity(
        instrument=inst,
        direction=Direction.LONG,
        regime=CryptoMarketRegime.STRONG_TREND_UP,
        setup_label=SetupLabel.BREAKOUT_LONG,
        score=Decimal("88.5"),
        score_breakdown=CryptoScreenerScoreBreakdown(
            trend_score=Decimal("90"),
            momentum_score=Decimal("85"),
            derivatives_score=Decimal("80"),
            alpha_score=Decimal("95"),
            volume_score=Decimal("80"),
            structure_score=Decimal("90"),
            macro_multiplier=Decimal("1.05"),
            final_composite_score=Decimal("88.5"),
        ),
        current_price=Decimal("150.00"),
        atr14=Decimal("4.5"),
        suggested_entry=Decimal("150.00"),
        suggested_stop=Decimal("143.25"),
        suggested_targets=(Decimal("156.75"), Decimal("163.50"), Decimal("170.25")),
        risk_reward_ratio=Decimal("2.0"),
        btc_beta=Decimal("1.15"),
        residual_return_24h=Decimal("0.045"),
        funding_rate=Decimal("-0.0001"),
        oi_change_1h=Decimal("0.035"),
        taker_ratio=Decimal("1.25"),
        rationale=("Trend Breakout",),
        macro_narrative="Neutral",
    )

    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    mock_candles = [
        Candle(
            opened_at=t0 + timedelta(hours=i),
            closed_at=t0 + timedelta(hours=i + 1),
            open=Decimal("150"),
            high=Decimal("155"),
            low=Decimal("149"),
            close=Decimal("152"),
            volume=Decimal("100"),
            quote_volume=Decimal("15000"),
            closed=True,
        )
        for i in range(20)
    ]

    class MockMarketData:
        venue = SimpleNamespace(value="binance_usdm")

        async def candles(self, *args: object, **kwargs: object) -> list[Candle]:
            return mock_candles

    class MockContext:
        market_data = MockMarketData()

    class MockSystem:
        _contexts = [MockContext()]

        async def crypto_screen(self, **kwargs: object) -> list[CryptoOpportunity]:
            return [mock_opp]

    class MockRuntime:
        system = MockSystem()

        async def close(self) -> None:
            return None

    settings = SimpleNamespace(strategy_profile=StrategyProfile.LEGACY_MOMENTUM)
    monkeypatch.setattr("algotrader.cli.load_settings", lambda: settings)  # type: ignore[attr-defined]

    async def mock_build_runtime(_: object) -> MockRuntime:
        return MockRuntime()

    monkeypatch.setattr("algotrader.cli.build_runtime", mock_build_runtime)  # type: ignore[attr-defined]
    monkeypatch.setattr("webbrowser.open", lambda _: None)  # type: ignore[attr-defined]
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)  # type: ignore[attr-defined]

    res = await _chart_verify(symbol="SOLUSDT")
    assert res == 0
    target_file = cast(Path, tmp_path) / "dashboard.html"
    assert target_file.exists()
    content = target_file.read_text(encoding="utf-8")
    assert "SOLUSDT" in content
    assert "lightweight-charts" in content


