import json
from datetime import timedelta

import pytest

from algotrader.cli import main
from algotrader.infrastructure.scan_archive import LocalScanArchive
from tests.test_shadow_runner import Market, runner_parent


def test_benchmark_cli_is_offline_and_validates_horizon(tmp_path, monkeypatch, capsys):
    from tests.test_shadow_benchmark import benchmark_fixture
    from tests.test_shadow_discovery import NOW

    _, child = benchmark_fixture(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("network construction forbidden")

    monkeypatch.setattr("httpx.AsyncClient", forbidden)
    monkeypatch.setattr(
        "sys.argv",
        [
            "atlas",
            "benchmark-shadow",
            "--shadow-id",
            child.shadow_id,
            "--archive-dir",
            str(tmp_path),
            "--horizon-hours",
            "24",
            "--as-of",
            (NOW + timedelta(hours=26)).isoformat(),
        ],
    )
    assert main() == 0
    output = json.loads(capsys.readouterr().out)
    assert output["comparison"] == "matched_forward_anchor_staggered_decisions"
    assert output["original_cohort_size"] == 20
    monkeypatch.setattr(
        "sys.argv",
        ["atlas", "benchmark-shadow", "--shadow-id", child.shadow_id, "--horizon-hours", "12"],
    )
    with pytest.raises(SystemExit):
        main()


def test_shadow_cli_runs_archives_and_replays_without_touching_core(
    settings, tmp_path, monkeypatch, capsys
):
    from algotrader.jobs import shadow_scan

    parent = runner_parent(settings)
    # Job uses wall clock; make the parent current and all fixture observations align for this test.
    monkeypatch.setattr(shadow_scan, "clock_now", lambda: parent.completed_at)

    class ClosedMarket(Market):
        async def close(self):
            self.closed = True

    markets = []

    def factory(budget):
        market = ClosedMarket(budget.policy)
        markets.append(market)
        return market

    monkeypatch.setattr(shadow_scan, "ShadowMarketData", factory)
    LocalScanArchive(tmp_path).write_scan(parent)
    monkeypatch.setattr(
        "sys.argv",
        ["atlas", "shadow-scan", "--scan-id", parent.scan_id, "--archive-dir", str(tmp_path)],
    )
    assert main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["arm"] == "discovery_shadow"
    assert markets[0].closed
    assert LocalScanArchive(tmp_path).read_scan(parent.scan_id) == parent
    monkeypatch.setattr(
        "sys.argv",
        [
            "atlas",
            "replay-shadow",
            "--shadow-id",
            result["shadow_id"],
            "--archive-dir",
            str(tmp_path),
        ],
    )
    assert main() == 0
    assert json.loads(capsys.readouterr().out)["matches"]


def test_shadow_cli_rejects_invalid_id_before_network(monkeypatch):
    monkeypatch.setattr("sys.argv", ["atlas", "shadow-scan", "--scan-id", "../unsafe"])
    with pytest.raises(SystemExit):
        main()


def test_observe_shadow_command_is_explicit_and_validates_horizon(tmp_path, monkeypatch, capsys):
    from algotrader.jobs import shadow_scan

    async def observe(root, shadow_id, horizon, max_symbols):
        assert root == tmp_path and horizon == 48 and max_symbols == 512
        return {"shadow_id": shadow_id, "comparison": "staggered_shadow"}

    monkeypatch.setattr(shadow_scan, "observe_shadow_scan", observe)
    monkeypatch.setattr(
        "sys.argv",
        [
            "atlas",
            "observe-shadow",
            "--shadow-id",
            "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            "--archive-dir",
            str(tmp_path),
            "--horizon-hours",
            "48",
            "--max-symbols",
            "512",
        ],
    )
    assert main() == 0
    assert json.loads(capsys.readouterr().out)["comparison"] == "staggered_shadow"
