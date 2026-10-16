from __future__ import annotations

import pytest
from pydantic import ValidationError
from pytest import MonkeyPatch

from algotrader.config import LIVE_ACKNOWLEDGEMENT, Settings


def test_live_mode_requires_all_three_interlocks() -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"execution_mode": "live"})


def test_live_mode_accepts_explicit_production_acknowledgement() -> None:
    settings = Settings.model_validate(
        {
            "environment": "production",
            "execution_mode": "live",
            "live_trading_enabled": True,
            "live_trading_ack": LIVE_ACKNOWLEDGEMENT,
        }
    )
    assert settings.execution_mode.value == "live"


def test_research_profiles_are_rejected_in_live_mode() -> None:
    with pytest.raises(ValidationError, match="research-only strategy profiles"):
        Settings.model_validate(
            {
                "environment": "production",
                "execution_mode": "live",
                "live_trading_enabled": True,
                "live_trading_ack": LIVE_ACKNOWLEDGEMENT,
                "strategy_profile": "empirical_tsmom_v5",
            }
        )


def test_layered_profile_is_research_only_and_shorts_default_off() -> None:
    configured = Settings.model_validate({"strategy_profile": "layered_tsmom_v6"})
    assert not configured.strategy_shorts_enabled

    with pytest.raises(ValidationError, match="research-only strategy profiles"):
        Settings.model_validate(
            {
                "environment": "production",
                "execution_mode": "live",
                "live_trading_enabled": True,
                "live_trading_ack": LIVE_ACKNOWLEDGEMENT,
                "strategy_profile": "layered_tsmom_v6",
            }
        )


def test_early_momentum_profile_is_research_only_and_shorts_default_off() -> None:
    configured = Settings.model_validate({"strategy_profile": "early_momentum_v7"})
    assert not configured.strategy_shorts_enabled

    with pytest.raises(ValidationError, match="research-only strategy profiles"):
        Settings.model_validate(
            {
                "environment": "production",
                "execution_mode": "live",
                "live_trading_enabled": True,
                "live_trading_ack": LIVE_ACKNOWLEDGEMENT,
                "strategy_profile": "early_momentum_v7",
            }
        )


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
    assert settings.ignored_spot_balance_assets == ("这是测试币", "TEST_ONLY")


def test_discovery_aligns_to_utc_day_but_rolling_cooldown_does_not() -> None:
    settings = Settings.model_validate({"execution_cooldown_hours": 5})
    assert settings.execution_cooldown_hours == 5
    with pytest.raises(ValidationError, match="divide evenly"):
        Settings.model_validate({"signal_discovery_interval_hours": 5})


def test_deprecated_screening_interval_maps_to_execution_cooldown() -> None:
    settings = Settings.model_validate({"screening_interval_hours": 4})
    assert settings.execution_cooldown_hours == 4


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
