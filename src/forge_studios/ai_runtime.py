from __future__ import annotations

import base64
import mimetypes
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx


class AIRuntimeError(RuntimeError):
    """Shared AI Runtime request failed."""


class AIRuntimeMediaClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        token: str | None = None,
        timeout_seconds: float | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = (
            base_url or os.getenv("AI_RUNTIME_URL", "http://127.0.0.1:8090")
        ).rstrip("/")
        self.token = token or os.getenv("AI_RUNTIME_TOKEN", "local-development-token")
        if not self.base_url:
            raise ValueError("AI_RUNTIME_URL cannot be blank")
        if not self.token:
            raise ValueError("AI_RUNTIME_TOKEN cannot be blank")
        timeout = timeout_seconds or float(os.getenv("AI_RUNTIME_REQUEST_TIMEOUT_SECONDS", "900"))
        self._owns_client = client is None
        self.client = client or httpx.Client(timeout=httpx.Timeout(timeout, connect=10.0))

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> "AIRuntimeMediaClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def generate(
        self,
        *,
        model: str,
        arguments: dict[str, Any],
        assets: dict[str, dict[str, str]] | None = None,
        metadata: dict[str, Any] | None = None,
        client_timeout_seconds: float | None = None,
        poll_interval_seconds: float | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "provider": "fal",
            "model": model,
            "arguments": arguments,
            "assets": assets or {},
            "metadata": metadata or {},
        }
        if client_timeout_seconds is not None:
            body["client_timeout_seconds"] = client_timeout_seconds
        if poll_interval_seconds is not None:
            body["poll_interval_seconds"] = poll_interval_seconds
        try:
            response = self.client.post(
                f"{self.base_url}/v1/media/generate",
                headers={"Authorization": f"Bearer {self.token}"},
                json=body,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:4000]
            raise AIRuntimeError(
                f"AI Runtime returned HTTP {exc.response.status_code}: {detail}"
            ) from exc
        except httpx.HTTPError as exc:
            raise AIRuntimeError(f"AI Runtime request failed: {exc}") from exc
        try:
            value = response.json()
        except ValueError as exc:
            raise AIRuntimeError("AI Runtime returned non-JSON media response") from exc
        if not isinstance(value, dict) or not isinstance(value.get("result"), dict):
            raise AIRuntimeError("AI Runtime media response omitted provider result")
        return value


def runtime_asset(
    value: str | None,
    *,
    assets: dict[str, dict[str, str]],
    asset_id: str,
) -> str | None:
    """Convert one local path to an explicit Runtime asset placeholder."""
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https", "data"}:
        return value
    path = Path(value).expanduser()
    if not path.exists() or not path.is_file():
        return value
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    assets[asset_id] = {
        "filename": path.name,
        "content_type": content_type,
        "data_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
    }
    return f"asset://{asset_id}"
