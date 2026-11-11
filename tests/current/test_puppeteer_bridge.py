import hashlib
import importlib.metadata
import json
import shutil
import sys
import types

import pytest

from forge_studios.contracts import EpisodePackage, Shot
from forge_studios.puppeteer_bridge import dispatch_behavior_pack


def _package():
    return EpisodePackage(
        package_version="episode_package", production_id="prod", episode_id="ep",
        world_id="world", show_id="show", title="Test", arc="Test arc",
        target_duration_seconds=1,
        shots=[Shot(shot_id="shot", duration_seconds=1, site_id="site",
                    start_frame_prompt="Frame.", video_prompt="Performance.")],
    )


def test_opt_in_behavior_pack_handoff_preserves_operator_pins(monkeypatch, tmp_path):
    calls = {}

    class ServerConfig:
        @classmethod
        def model_validate(cls, value):
            calls["server_config"] = value
            return value

    class MCPClient:
        def __init__(self, servers, *, behavior_packs_path):
            calls["servers"] = servers
            calls["path"] = behavior_packs_path

        def compile_behavior_pack(self, pack_id, revision, take_id, *, server):
            calls["compile"] = (pack_id, revision, take_id, server)
            return object()

        async def execute(self, plan):
            calls["plan"] = plan
            return ({"cue_id": "hello:0", "take_id": "take-1", "is_error": False},)

    package = _package()
    monkeypatch.setitem(sys.modules, "forge_puppeteer", types.ModuleType("forge_puppeteer"))
    mcp = types.ModuleType("forge_puppeteer.mcp")
    client_module = types.ModuleType("forge_puppeteer.mcp.client")
    client_module.MCPClient = MCPClient
    client_module.ServerConfig = ServerConfig
    monkeypatch.setitem(sys.modules, "forge_puppeteer.mcp", mcp)
    monkeypatch.setitem(sys.modules, "forge_puppeteer.mcp.client", client_module)

    asset = dispatch_behavior_pack(
        package, "shot", server_name="robot",
        server_config={"command": "configured-mcp", "args": ["--profile", "mock"]},
        behavior_packs_path=tmp_path / "operator-packs.json",
        behavior_pack_id="hello", behavior_pack_revision="sha256:operator-pin",
        take_id="take-1",
    )

    assert calls["compile"] == ("hello", "sha256:operator-pin", "take-1", "robot")
    assert calls["servers"]["robot"]["command"] == "configured-mcp"
    assert asset.status == "candidate"
    assert asset.kind == "physical_take"
    assert asset.metadata["behavior_pack_revision"] == "sha256:operator-pin"
    assert asset.shot_id == "shot"
    assert package.assets[-1] is asset


def test_missing_puppeteer_mcp_extra_has_install_command(monkeypatch, tmp_path):
    class ServerConfig:
        @classmethod
        def model_validate(cls, value):
            return value

    class MCPClient:
        def __init__(self, *args, **kwargs):
            pass

        def compile_behavior_pack(self, *args, **kwargs):
            return object()

        async def execute(self, plan):
            raise RuntimeError("Install forge-puppeteer[mcp] to use MCP clients")

    monkeypatch.setitem(sys.modules, "forge_puppeteer", types.ModuleType("forge_puppeteer"))
    monkeypatch.setitem(sys.modules, "forge_puppeteer.mcp", types.ModuleType("forge_puppeteer.mcp"))
    client_module = types.ModuleType("forge_puppeteer.mcp.client")
    client_module.MCPClient = MCPClient
    client_module.ServerConfig = ServerConfig
    monkeypatch.setitem(sys.modules, "forge_puppeteer.mcp.client", client_module)

    with pytest.raises(RuntimeError, match=r"forge-puppeteer\[mcp\].*same Python environment"):
        dispatch_behavior_pack(
            _package(), "shot", server_name="robot", server_config={"command": "fake"},
            behavior_packs_path=tmp_path / "packs.json", behavior_pack_id="pack",
            behavior_pack_revision="sha256:pin", take_id="take",
        )


def test_installed_puppeteer_wheel_stdio_handoff_and_stale_pin(tmp_path):
    """Exercise the installed Puppeteer package and real fake MCP stdio server.

    This intentionally uses only an installed wheel/console script. It does not
    import from or add a Puppeteer source checkout to sys.path.
    """
    try:
        importlib.metadata.version("forge-puppeteer")
        importlib.metadata.version("mcp")
    except importlib.metadata.PackageNotFoundError:
        pytest.skip("install forge-puppeteer[mcp] to run the stdio integration")
    if shutil.which("forge-puppeteer-mcp") is None:
        pytest.skip("forge-puppeteer-mcp console script is not on PATH")

    server_config = {
        "backend": "fake",
        "device": "reachy",
        "instance_id": "studios-wheel-stdio",
        "allowed_tools": [
            "device_info", "device_state", "list_assets", "action_lookup",
            "control_acquire", "control_release", "play_move",
        ],
        "assets": ["hello"],
    }
    pack_path = tmp_path / "packs.json"
    pack_path.write_text(json.dumps({
        "schema_version": 1,
        "performer": "reachy",
        "packs": [{
            "id": "studios-smoke",
            "description": "Deterministic Studios to Puppeteer wheel smoke.",
            "cues": [{
                "at": 0,
                "operation": "play_move",
                "asset_id": "reachy:hello",
                "asset_revision": "sha256:forge-puppeteer-fake-v1",
                "parameters": {"sound": False},
            }],
        }],
    }), encoding="utf-8")
    (tmp_path / "server.json").write_text(json.dumps(server_config), encoding="utf-8")
    revision = "sha256:" + hashlib.sha256(pack_path.read_bytes()).hexdigest()
    package = _package()

    asset = dispatch_behavior_pack(
        package,
        "shot",
        server_name="robot",
        server_config={
            "command": shutil.which("forge-puppeteer-mcp"),
            "args": ["--config", str(tmp_path / "server.json")],
        },
        behavior_packs_path=pack_path,
        behavior_pack_id="studios-smoke",
        behavior_pack_revision=revision,
        take_id="stdio-take-1",
    )
    assert asset.metadata["behavior_pack_revision"] == revision
    assert asset.metadata["results"][0]["result"]["state"] == "completed"
    assert package.assets[-1] is asset

    with pytest.raises(ValueError, match="stale_behavior_pack"):
        dispatch_behavior_pack(
            _package(),
            "shot",
            server_name="robot",
            server_config={
                "command": shutil.which("forge-puppeteer-mcp"),
                "args": ["--config", str(tmp_path / "server.json")],
            },
            behavior_packs_path=pack_path,
            behavior_pack_id="studios-smoke",
            behavior_pack_revision="sha256:deliberately-stale",
            take_id="stale-take",
        )
