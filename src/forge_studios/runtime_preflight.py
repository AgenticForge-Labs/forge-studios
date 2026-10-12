"""Read-only check of the running isolated development AI Runtime.

Never infer a runtime credential from the Studios default or mix a development
storyboard experiment with the production Platform Compose project. No provider
generation requests are made here. Tokens are emitted only on stdout for an
immediate foreground shell consumer; diagnostics never print their values.
"""

from __future__ import annotations

import argparse
import hmac
import json
import subprocess
import sys
from pathlib import Path
from urllib.request import urlopen


class DevelopmentRuntimeError(ValueError):
    """Safe operator-facing failure without any secret values."""


def select_development_runtime(
    compose: dict,
    running: dict,
) -> tuple[str, str]:
    """Validate both intended Compose config and the actual running container."""
    if compose.get("name") != "agenticforge-platform-dev":
        raise DevelopmentRuntimeError(
            "Expected Compose project agenticforge-platform-dev, refusing non-development runtime"
        )
    service = (compose.get("services") or {}).get("ai-runtime")
    if not isinstance(service, dict):
        raise DevelopmentRuntimeError("Development Compose has no ai-runtime service")
    env = service.get("environment") or {}
    if not isinstance(env, dict) or env.get("APP_ENV") != "development":
        raise DevelopmentRuntimeError("Configured AI Runtime APP_ENV is not development")
    token = env.get("AI_RUNTIME_TOKEN")
    if not isinstance(token, str) or not token:
        raise DevelopmentRuntimeError("Development Compose AI_RUNTIME_TOKEN is missing")
    port = None
    for binding in service.get("ports") or []:
        if (
            isinstance(binding, dict)
            and str(binding.get("target")) == "8090"
            and binding.get("host_ip") in ("127.0.0.1", "::1")
        ):
            port = str(binding.get("published") or "")
            break
    if not port or not port.isdecimal() or port == "8090":
        raise DevelopmentRuntimeError(
            "Development runtime needs an isolated localhost host port (normally 18090), not 8090"
        )
    if not (1 <= int(port) <= 65535):
        raise DevelopmentRuntimeError("Invalid development runtime port")

    state = running.get("State") or {}
    if state.get("Running") is not True:
        raise DevelopmentRuntimeError("Development AI Runtime container is not running")
    active = running.get("Config") or {}
    actual_vars = dict(
        entry.split("=", 1) for entry in active.get("Env") or []
        if isinstance(entry, str) and "=" in entry
    )
    if actual_vars.get("APP_ENV") != "development":
        raise DevelopmentRuntimeError("Running AI Runtime is not in development mode")
    actual_token = actual_vars.get("AI_RUNTIME_TOKEN") or ""
    if not hmac.compare_digest(token, actual_token):
        raise DevelopmentRuntimeError(
            "Configured token differs from running development AI Runtime; no generation attempted"
        )
    bindings = (running.get("NetworkSettings") or {}).get("Ports") or {}
    active_bindings = bindings.get("8090/tcp") or []
    if not any(
        item.get("HostIp") in ("127.0.0.1", "::1")
        and str(item.get("HostPort")) == port
        for item in active_bindings if isinstance(item, dict)
    ):
        raise DevelopmentRuntimeError(
            "Running AI Runtime host-port binding differs from development Compose"
        )
    return f"http://127.0.0.1:{port}", token


def resolve_development_runtime(platform_dir: Path) -> tuple[str, str]:
    """Read existing Compose/container state; never start, stop, or edit services."""
    if not (platform_dir / "docker-compose.yml").is_file():
        raise DevelopmentRuntimeError("Platform repository not found at expected sibling path")

    def docker(*args: str, timeout: float = 15) -> str:
        try:
            result = subprocess.run(
                ["docker", *args], cwd=platform_dir,
                text=True, capture_output=True, check=True, timeout=timeout,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise DevelopmentRuntimeError(
                "Cannot inspect development Compose/runtime; check Docker and the development stack"
            ) from exc
        return result.stdout.strip()

    try:
        compose = json.loads(docker("compose", "config", "--format", "json"))
        # Validate project identity *before* using Compose to inspect the container.
        if compose.get("name") != "agenticforge-platform-dev":
            raise DevelopmentRuntimeError(
                "Platform checkout does not resolve to agenticforge-platform-dev"
            )
        container_id = docker("compose", "ps", "-q", "ai-runtime")
        if not container_id or "\n" in container_id:
            raise DevelopmentRuntimeError("Development ai-runtime has no unique running container")
        running = json.loads(docker("inspect", container_id))
        if not isinstance(running, list) or len(running) != 1:
            raise DevelopmentRuntimeError("Could not identify one development runtime")
        url, token = select_development_runtime(compose, running[0])
    except (ValueError, TypeError, KeyError) as exc:
        if isinstance(exc, DevelopmentRuntimeError):
            raise
        raise DevelopmentRuntimeError("Development runtime metadata is invalid") from exc
    # Readiness is unmetered, unauthenticated, and does not dispatch provider work.
    try:
        with urlopen(f"{url}/health/ready", timeout=5) as response:  # nosec B310 - guarded loopback
            ready = json.load(response)
    except (OSError, ValueError) as exc:
        raise DevelopmentRuntimeError(
            "Development AI Runtime is not ready; no provider call attempted"
        ) from exc
    if ready.get("status") != "ready" or not (ready.get("providers") or {}).get("fal"):
        raise DevelopmentRuntimeError(
            "Development AI Runtime does not report a configured Fal provider"
        )
    return url, token


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        url, token = resolve_development_runtime(args.platform_dir.expanduser().resolve())
    except DevelopmentRuntimeError as exc:
        print(f"[storyboard] development runtime preflight: {exc}", file=sys.stderr)
        return 2
    # Only the launcher consumes this output; never pipe it into a log.
    print(url)
    print(token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
