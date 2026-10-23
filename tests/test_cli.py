import json
from unittest.mock import AsyncMock

import pytest

from algotrader import cli


@pytest.mark.parametrize("command", ["run-once", "worker", "reconcile", "adopt-position", "kill-switch"])
def test_trading_commands_are_removed(monkeypatch, command):
    monkeypatch.setattr("sys.argv", ["atlas", command])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2


def test_doctor_requires_no_exchange_keys(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["atlas", "doctor"])
    assert cli.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "screener"
    assert result["order_execution_enabled"] is False
    assert result["credentials_required"] is False


async def test_screen_always_closes_runtime(monkeypatch, capsys):
    runtime = AsyncMock()
    runtime.crypto_screen.return_value = []
    monkeypatch.setattr(cli, "build_runtime", AsyncMock(return_value=runtime))
    assert await cli._screen("all", 68, 250) == 0
    runtime.close.assert_awaited_once()
    assert json.loads(capsys.readouterr().out)["opportunities"] == []


async def test_failed_scan_also_closes_runtime(monkeypatch):
    runtime = AsyncMock()
    runtime.crypto_screen.side_effect = RuntimeError("market unavailable")
    monkeypatch.setattr(cli, "build_runtime", AsyncMock(return_value=runtime))
    with pytest.raises(RuntimeError, match="market unavailable"):
        await cli._screen("all", 68, 250)
    runtime.close.assert_awaited_once()


