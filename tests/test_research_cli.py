import json

import pytest

from algotrader.cli import main
from tests.test_research_outcome_worker import setup_session


def test_offline_research_operator_commands(tmp_path, monkeypatch, capsys):
    sessions, session, child = setup_session(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("offline command constructed HTTP")

    monkeypatch.setattr("httpx.AsyncClient", forbidden)
    for command, flag, identifier in (
        ("research-start", "--shadow-id", child.shadow_id),
        ("research-status", "--session-id", session.session_id),
        ("research-stop", "--session-id", session.session_id),
    ):
        monkeypatch.setattr(
            "sys.argv", ["atlas", command, flag, identifier, "--archive-dir", str(tmp_path)]
        )
        assert main() == 0
        result = json.loads(capsys.readouterr().out)
        assert result["session_id"] == session.session_id
    assert result["state"] == "stopped"
    monkeypatch.setattr(
        "sys.argv",
        [
            "atlas",
            "research-health",
            "--session-id",
            session.session_id,
            "--role",
            "ranks",
            "--archive-dir",
            str(tmp_path),
        ],
    )
    assert main() == 1
    health = json.loads(capsys.readouterr().out)
    assert health["healthy"] is False and health["collecting"] is False


@pytest.mark.parametrize(
    "arguments",
    [
        ["research-worker"],
        ["research-worker", "--role", "ranks"],
        ["research-worker", "--role", "trader", "--session-id", "../x"],
        ["research-start", "--shadow-id", "../unsafe"],
    ],
)
def test_research_cli_requires_explicit_valid_selection(monkeypatch, arguments):
    monkeypatch.setattr("sys.argv", ["atlas", *arguments])
    with pytest.raises(SystemExit) as caught:
        main()
    assert caught.value.code == 2
