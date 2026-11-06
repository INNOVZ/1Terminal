import json
from unittest.mock import AsyncMock

import pytest

from algotrader import cli
from algotrader.domain.scan_observations import ScanReceipt
from algotrader.infrastructure.runtime import ScreeningRun


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
    runtime.run_crypto_scan.return_value = ScreeningRun([], {}, ScanReceipt("fixture", "saved"))
    monkeypatch.setattr(cli, "build_runtime", AsyncMock(return_value=runtime))
    assert await cli._screen("all", 68, 250) == 0
    runtime.close.assert_awaited_once()
    assert json.loads(capsys.readouterr().out)["opportunities"] == []


async def test_failed_scan_also_closes_runtime(monkeypatch):
    runtime = AsyncMock()
    runtime.run_crypto_scan.side_effect = RuntimeError("market unavailable")
    monkeypatch.setattr(cli, "build_runtime", AsyncMock(return_value=runtime))
    with pytest.raises(RuntimeError, match="market unavailable"):
        await cli._screen("all", 68, 250)
    runtime.close.assert_awaited_once()


async def test_replay_cli_uses_archive_offline(monkeypatch, capsys, tmp_path, settings):
    from algotrader.infrastructure.scan_archive import LocalScanArchive
    from tests.test_scan_replay import captured_fixture
    captured = await captured_fixture(settings)
    LocalScanArchive(tmp_path).write_scan(captured.record)
    capsys.readouterr()
    def forbidden(*args, **kwargs):
        raise AssertionError("offline replay constructed a network client")
    monkeypatch.setattr("httpx.AsyncClient", forbidden)
    assert await cli._replay(tmp_path, captured.record.scan_id) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["matches"] is True
    assert result["scan_id"] == captured.record.scan_id


@pytest.mark.parametrize("args", [
    ["replay-scan", "--scan-id", "../escape"],
    ["observe-scan", "--scan-id", "6158a217-8f7c-4888-8f92-3cb3fa656ef7", "--horizon-hours", "25"],
])
def test_observation_commands_reject_invalid_input_before_running_jobs(monkeypatch, args):
    monkeypatch.setattr("sys.argv", ["atlas", *args])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
