import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
import yaml

from scripts.research_compose_control import compose_argv, parse_research_control


def test_deployment_control_is_persistent_strict_and_disabled_by_default(tmp_path):
    control_file = tmp_path / "research-control.env"
    assert parse_research_control(control_file) == {"ATLAS_RESEARCH_ENABLED": "false"}
    session_id = str(uuid4())
    control_file.write_text(
        f"ATLAS_RESEARCH_ENABLED=true\nATLAS_RESEARCH_SESSION_ID={session_id}\n"
    )
    control = parse_research_control(control_file)
    for sha in ("a" * 40, "b" * 40):
        release = Path("/home/deploy/atlas-releases") / sha
        for action in (["build"], ["up"], ["health"]):
            argv = compose_argv(release, control, action)
            assert argv[:4] == ["docker", "--context", "default", "compose"]
            assert argv[argv.index("--project-name") + 1] == "atlas-research"
            assert str(release / "compose.yaml") in argv
            assert argv[-2:] == ["research-ranks", "research-outcomes"]
            assert "atlas" not in argv
    disabled = compose_argv(
        Path("/home/deploy/atlas-releases") / ("a" * 40),
        {"ATLAS_RESEARCH_ENABLED": "false"},
        ["up"],
    )
    assert "up" not in disabled and "stop" in disabled


@pytest.mark.parametrize(
    "contents",
    [
        "ATLAS_RESEARCH_ENABLED=yes\n",
        "ATLAS_RESEARCH_ENABLED=true\n",
        "ATLAS_RESEARCH_ENABLED=false\nATLAS_RESEARCH_ENABLED=true\n",
        "UNKNOWN=true\n",
        "export ATLAS_RESEARCH_ENABLED=true\n",
        "ATLAS_RESEARCH_ENABLED=false\nATLAS_RESEARCH_SESSION_ID=../bad\n",
        "ATLAS_RESEARCH_ENABLED=$(touch /tmp/unsafe)\n",
    ],
)
def test_invalid_control_fails_before_docker(tmp_path, contents):
    path = tmp_path / "control"
    path.write_text(contents)
    with pytest.raises(ValueError):
        parse_research_control(path)


def test_symlink_and_writable_control_refused(tmp_path):
    actual = tmp_path / "actual"
    actual.write_text("ATLAS_RESEARCH_ENABLED=false\n")
    link = tmp_path / "link"
    link.symlink_to(actual)
    with pytest.raises(ValueError):
        parse_research_control(link)
    actual.chmod(0o666)
    with pytest.raises(ValueError):
        parse_research_control(actual)


@pytest.mark.parametrize("enabled", ["false", "true"])
def test_wrapper_executes_only_scoped_docker_argv_and_sanitizes_enablement(
    tmp_path, monkeypatch, capfd, enabled
):
    from scripts import research_compose_control as wrapper

    control = tmp_path / "control"
    identifier = str(uuid4())
    control.write_text(
        f"ATLAS_RESEARCH_ENABLED={enabled}\n"
        + (f"ATLAS_RESEARCH_SESSION_ID={identifier}\n" if enabled == "true" else "")
    )
    monkeypatch.setattr(wrapper, "CONTROL_FILE", control)
    release = tmp_path / ("a" * 40)
    release.mkdir()
    fake_docker = tmp_path / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env python3\nimport json,os,sys\nprint(json.dumps({'argv':sys.argv[1:],'enabled':os.getenv('ATLAS_RESEARCH_ENABLED'),'session':os.getenv('ATLAS_RESEARCH_SESSION_ID'),'tag':os.getenv('ATLAS_IMAGE_TAG')}))\n"
    )
    fake_docker.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("ATLAS_RESEARCH_ENABLED", "true")
    monkeypatch.setenv("ATLAS_RESEARCH_SESSION_ID", str(uuid4()))
    monkeypatch.setattr(
        "sys.argv", ["research-control", "--release", str(release), "--action", "up"]
    )
    assert wrapper.main() == 0
    result = json.loads(capfd.readouterr().out)
    assert result["enabled"] == enabled
    assert result["session"] == (identifier if enabled == "true" else None)
    assert ("stop" in result["argv"]) == (enabled == "false")
    assert ("up" in result["argv"]) == (enabled == "true")
    assert result["argv"][-2:] == ["research-ranks", "research-outcomes"]
    assert result["tag"] == "a" * 40


@pytest.mark.parametrize("state,expected", [("healthy", 0), ("unhealthy", 1), ("starting", 1)])
def test_wrapper_health_requires_both_healthy_workers(
    tmp_path, monkeypatch, capsys, state, expected
):
    from scripts import research_compose_control as wrapper

    control = tmp_path / "control"
    control.write_text(f"ATLAS_RESEARCH_ENABLED=true\nATLAS_RESEARCH_SESSION_ID={uuid4()}\n")
    monkeypatch.setattr(wrapper, "CONTROL_FILE", control)
    release = tmp_path / ("a" * 40)
    release.mkdir()
    docker = tmp_path / "docker"
    rows = [
        {"Service": role, "State": "running", "Health": state}
        for role in ("research-ranks", "research-outcomes")
    ]
    docker.write_text("#!/usr/bin/env python3\nprint(" + repr(json.dumps(rows)) + ")\n")
    docker.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.setattr(
        "sys.argv", ["research-control", "--release", str(release), "--action", "health"]
    )
    assert wrapper.main() == expected
    assert json.loads(capsys.readouterr().out)["healthy"] is (expected == 0)


def test_compose_workers_are_profile_gated_and_archive_only():
    compose = yaml.safe_load(Path("compose.yaml").read_text())
    services = compose["services"]
    assert services["atlas"].get("profiles") is None
    for role in ("ranks", "outcomes"):
        worker = services[f"research-{role}"]
        assert worker["profiles"] == ["research"]
        assert not worker.get("ports")
        assert worker["user"] == "10001:10001"
        assert worker["read_only"] is True
        assert worker["cap_drop"] == ["ALL"]
        assert worker["security_opt"] == ["no-new-privileges:true"]
        assert worker["tmpfs"] == ["/tmp:size=64m,noexec,nosuid"]
        assert worker["mem_limit"] == "256m" and worker["cpus"] == 0.5
        assert len(worker["volumes"]) == 1 and worker["volumes"][0].endswith(":/app/data/research")
        assert worker["command"][2] == "algotrader.cli"
        assert worker["command"][3] == "research-worker"
        assert worker["command"][worker["command"].index("--role") + 1] == role
        assert "research-health" in worker["healthcheck"]["test"]
        assert worker["image"] == services["atlas"]["image"]
