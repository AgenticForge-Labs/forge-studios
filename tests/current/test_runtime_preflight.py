"""Runtime preflight protects the normal-profile storyboard from prod or stale credentials."""

from __future__ import annotations

import json
import subprocess
import sys
from io import BytesIO

import pytest

from forge_studios import runtime_preflight


def _metadata(
    *,
    project: str = "agenticforge-platform-dev",
    compose_app_env: str = "development",
    actual_app_env: str = "development",
    configured_token: str = "development-token-example",
    actual_token: str = "development-token-example",
    configured_port: str = "18090",
    actual_port: str = "18090",
    running: bool = True,
):
    compose = {
        "name": project,
        "services": {"ai-runtime": {
            "environment": {"APP_ENV": compose_app_env,
                            "AI_RUNTIME_TOKEN": configured_token},
            "ports": [{"target": 8090, "published": configured_port,
                       "host_ip": "127.0.0.1"}],
        }},
    }
    active = {
        "State": {"Running": running},
        "Config": {"Env": [
            f"APP_ENV={actual_app_env}",
            f"AI_RUNTIME_TOKEN={actual_token}",
        ]},
        "NetworkSettings": {"Ports": {
            "8090/tcp": [{"HostIp": "127.0.0.1",
                          "HostPort": actual_port}],
        }},
    }
    return compose, active


def test_development_identity_and_actual_token_are_verified():
    config, running = _metadata()
    url, token = runtime_preflight.select_development_runtime(config, running)
    assert url == "http://127.0.0.1:18090"
    assert token == "development-token-example"


@pytest.mark.parametrize("override", [
    {"project": "agenticforge-platform"},
    {"compose_app_env": "production"},
    {"actual_app_env": "production"},
    {"configured_token": "stale-token"},
    {"configured_port": "8090", "actual_port": "8090"},
    {"actual_port": "8091"},
    {"running": False},
])
def test_development_preflight_rejects_incorrect_container_identity(override):
    config, active = _metadata(**override)
    with pytest.raises(runtime_preflight.DevelopmentRuntimeError) as error:
        runtime_preflight.select_development_runtime(config, active)
    assert "development-token-example" not in str(error.value)
    assert "stale-token" not in str(error.value)


def test_local_dev_inspection_uses_no_mutating_docker_commands(tmp_path, monkeypatch):
    (tmp_path / "docker-compose.yml").write_text("services: {}\\n")
    compose, running = _metadata()
    inspected = []

    def fake_run(command, **kwargs):
        assert kwargs["cwd"] == tmp_path
        assert kwargs["capture_output"] is True
        inspected.append(command)
        if command[1:4] == ["compose", "config", "--format"]:
            return subprocess.CompletedProcess(command, 0, json.dumps(compose), "")
        if command[1:4] == ["compose", "ps", "-q"]:
            return subprocess.CompletedProcess(command, 0, "development-container-id\\n", "")
        if command[1] == "inspect":
            return subprocess.CompletedProcess(command, 0, json.dumps([running]), "")
        raise AssertionError(f"unexpected command: {command}")

    def fake_open(url, timeout):
        assert url == "http://127.0.0.1:18090/health/ready"
        assert timeout == 5
        return BytesIO(json.dumps({
            "status": "ready", "providers": {"fal": True, "openrouter": False},
        }).encode())

    monkeypatch.setattr(runtime_preflight.subprocess, "run", fake_run)
    monkeypatch.setattr(runtime_preflight, "urlopen", fake_open)
    assert runtime_preflight.resolve_development_runtime(tmp_path) == (
        "http://127.0.0.1:18090", "development-token-example",
    )
    assert all("up" not in cmd and "restart" not in cmd for cmd in inspected)
    assert len(inspected) == 3


def test_preflight_exec_does_not_write_token_to_output(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(
        runtime_preflight, "resolve_development_runtime",
        lambda _path: ("http://127.0.0.1:18090", "not-for-logs-secret"),
    )
    called = {}

    class ExecCalled(Exception):
        pass

    def fake_exec(command, argv, env):
        called.update(command=command, argv=argv, env=env)
        raise ExecCalled

    monkeypatch.setattr(runtime_preflight.os, "execvpe", fake_exec)
    monkeypatch.setattr(sys, "argv", [
        "runtime_preflight", "--platform-dir", str(tmp_path),
        "--", "forge-studios", "generate",
    ])
    with pytest.raises(ExecCalled):
        runtime_preflight.main()
    captured = capsys.readouterr()
    assert "not-for-logs-secret" not in (captured.err + captured.out)
    assert called["command"] == "forge-studios"
    assert called["env"]["AI_RUNTIME_TOKEN"] == "not-for-logs-secret"
    assert called["env"]["AI_RUNTIME_URL"] == "http://127.0.0.1:18090"
    assert captured.out == ""
