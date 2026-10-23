from __future__ import annotations

import pytest
from pydantic import ValidationError
from pytest import MonkeyPatch

from algotrader.config import Settings


def test_legacy_deployment_flags_cannot_enable_trading(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("ATLAS_EXECUTION_MODE", "live")
    monkeypatch.setenv("ATLAS_LIVE_TRADING_ENABLED", "true")
    monkeypatch.setenv("ATLAS_BINANCE_FUTURES_API_KEY", "old-key")
    settings = Settings(_env_file=None)
    assert not hasattr(settings, "execution_mode")
    assert not hasattr(settings, "binance_futures_api_key")
    assert "old-key" not in settings.model_dump_json()


def test_screener_interval_aligns_to_utc_day() -> None:
    assert Settings(_env_file=None).screener_interval_hours == 4
    with pytest.raises(ValidationError, match="divide evenly"):
        Settings(_env_file=None, screener_interval_hours=5)


def test_operator_token_is_never_implicitly_weak() -> None:
    settings = Settings.model_validate({"operator_token": "short"})
    with pytest.raises(Exception, match="32 characters"):
        settings.require_operator_token()


def test_production_rejects_wildcard_trusted_hosts() -> None:
    with pytest.raises(ValidationError, match="cannot contain a wildcard"):
        Settings.model_validate({"environment": "production", "allowed_hosts": ("*",)})


def test_comma_separated_environment_lists_match_example(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("ATLAS_ALLOWED_HOSTS", "localhost,127.0.0.1")
    monkeypatch.setenv("ATLAS_EXCLUDED_BASE_ASSETS", "usdt,usdc,eur")
    monkeypatch.setenv("ATLAS_IGNORED_SPOT_BALANCE_ASSETS", "这是测试币, TEST_ONLY ")
    settings = Settings.model_validate({})
    assert settings.allowed_hosts == ("localhost", "127.0.0.1")
    assert settings.excluded_base_assets == ("USDT", "USDC", "EUR")
    assert not hasattr(settings, "ignored_spot_balance_assets")


def test_protective_bracket_configuration_is_coherent() -> None:
    with pytest.raises(ValidationError, match="structural stop distance"):
        Settings.model_validate(
            {
                "atr_stop_multiple": "2",
                "max_structural_stop_distance_atr": "1.5",
            }
        )


def test_rsi_divergence_age_must_fit_inside_lookback() -> None:
    with pytest.raises(ValidationError, match="maximum age"):
        Settings.model_validate(
            {
                "rsi_divergence_lookback_hours": 24,
                "rsi_divergence_max_age_hours": 25,
            }
        )


def test_early_retest_hold_buffer_cannot_be_looser_than_touch() -> None:
    with pytest.raises(ValidationError, match="hold buffer"):
        Settings.model_validate(
            {
                "early_retest_touch_buffer_atr": "0.25",
                "early_retest_hold_buffer_atr": "0.50",
            }
        )


def test_research_report_cannot_request_data_past_retention() -> None:
    with pytest.raises(ValidationError, match="lookback cannot exceed"):
        Settings.model_validate(
            {
                "research_retention_days": 90,
                "research_report_lookback_days": 180,
            }
        )


def test_minimum_reward_risk_cannot_exceed_planned_reward_risk() -> None:
    with pytest.raises(ValidationError, match="reward/risk"):
        Settings.model_validate(
            {
                "reward_risk_multiple": "1.5",
                "min_reward_risk_multiple": "2",
            }
        )
