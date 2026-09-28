from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx


_LINEAGE_KEYS = (
    "world_id",
    "show_id",
    "episode_id",
    "production_id",
    "package_id",
    "shot_id",
    "attempt_id",
    "asset_id",
    "take_id",
    "render_id",
    "publication_id",
    "experiment_id",
    "product_id",
    "order_id",
    "conversion_id",
)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class PlatformTelemetryMirror:
    """Best-effort mirror of Studios-owned telemetry into Platform.

    Local JSONL remains the complete Studios event record. The central event is
    intentionally smaller: repeated prompt bodies become content-addressed
    evidence references while stable lineage remains directly queryable.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        token: str | None = None,
        timeout_seconds: float = 15.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = (
            base_url
            or os.getenv("AGENTICFORGE_EVIDENCE_URL", "http://127.0.0.1:8055")
        ).rstrip("/")
        self.token = (
            token
            if token is not None
            else os.getenv("AGENTICFORGE_EVIDENCE_TOKEN", "")
        ).strip()
        self.enabled = bool(self.token)
        self.timeout_seconds = timeout_seconds
        self.client = client

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        if self.client is not None:
            response = self.client.request(
                method,
                f"{self.base_url}/{path.lstrip('/')}",
                headers=self._headers(),
                json=json_body,
                params=params,
            )
        else:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.request(
                    method,
                    f"{self.base_url}/{path.lstrip('/')}",
                    headers=self._headers(),
                    json=json_body,
                    params=params,
                )
        response.raise_for_status()
        value = response.json()
        if isinstance(value, dict) and "data" in value:
            return value["data"]
        return value

    def _persist_prompt(self, prompt: str) -> dict[str, Any]:
        sha256 = _sha256_text(prompt)
        filter_value = json.dumps(
            {
                "_and": [
                    {"owner_system": {"_eq": "forge-studios"}},
                    {"kind": {"_eq": "provider_prompt"}},
                    {"sha256": {"_eq": sha256}},
                ]
            },
            separators=(",", ":"),
        )
        rows = self._request(
            "GET",
            "items/af_evidence",
            params={"filter": filter_value, "limit": 1},
        )
        if isinstance(rows, list) and rows:
            return rows[0]

        payload = {
            "owner_system": "forge-studios",
            "kind": "provider_prompt",
            "schema_version": "1",
            "sha256": sha256,
            "content_type": "text/plain",
            "inline_text": prompt,
            "size_bytes": len(prompt.encode("utf-8")),
            "sensitivity": "private",
            "provenance": {
                "producer": "forge-studios",
                "semantic_owner": "upstream-package",
            },
        }
        try:
            created = self._request(
                "POST",
                "items/af_evidence",
                json_body=payload,
            )
        except httpx.HTTPStatusError:
            rows = self._request(
                "GET",
                "items/af_evidence",
                params={"filter": filter_value, "limit": 1},
            )
            if isinstance(rows, list) and rows:
                return rows[0]
            raise
        if not isinstance(created, dict):
            raise RuntimeError("Platform evidence create returned an unexpected response")
        return created

    def _strip_prompts(
        self,
        value: Any,
        *,
        path: str = "payload",
        refs: list[dict[str, Any]],
    ) -> Any:
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for key, item in value.items():
                child_path = f"{path}.{key}"
                if key == "prompt" and isinstance(item, str):
                    evidence = self._persist_prompt(item)
                    refs.append(
                        {
                            "role": child_path,
                            "evidence_id": str(evidence["id"]),
                            "owner_system": "forge-studios",
                            "kind": "provider_prompt",
                            "sha256": str(evidence["sha256"]),
                        }
                    )
                    continue
                result[key] = self._strip_prompts(
                    item,
                    path=child_path,
                    refs=refs,
                )
            return result
        if isinstance(value, list):
            return [
                self._strip_prompts(
                    item,
                    path=f"{path}[{index}]",
                    refs=refs,
                )
                for index, item in enumerate(value)
            ]
        return value

    @staticmethod
    def _lineage(event: dict[str, Any]) -> dict[str, str]:
        return {
            key: str(event[key])
            for key in _LINEAGE_KEYS
            if event.get(key) not in (None, "")
        }

    @staticmethod
    def _subject(event: dict[str, Any]) -> tuple[str, str]:
        for key in (
            "attempt_id",
            "asset_id",
            "render_id",
            "shot_id",
            "production_id",
            "episode_id",
        ):
            if event.get(key) not in (None, ""):
                return key.removesuffix("_id"), str(event[key])
        return "telemetry_event", str(event["event_id"])

    def mirror(self, event: dict[str, Any]) -> None:
        if not self.enabled:
            return

        event_id = str(event["event_id"])
        filter_value = json.dumps(
            {
                "_and": [
                    {"source": {"_eq": "forge-studios"}},
                    {"idempotency_key": {"_eq": event_id}},
                ]
            },
            separators=(",", ":"),
        )
        existing = self._request(
            "GET",
            "items/af_events",
            params={"filter": filter_value, "limit": 1},
        )
        if isinstance(existing, list) and existing:
            return

        evidence_refs: list[dict[str, Any]] = []
        central_payload = {
            key: value
            for key, value in event.items()
            if key not in {"event_type", "recorded_at", "event_id"}
        }
        central_payload = self._strip_prompts(
            central_payload,
            refs=evidence_refs,
        )
        lineage = self._lineage(event)
        production_id = lineage.get("production_id")
        episode_id = lineage.get("episode_id")
        trace_id = (
            f"production:{production_id}"
            if production_id
            else f"episode:{episode_id}"
            if episode_id
            else f"studios:{event_id}"
        )
        subject_type, subject_id = self._subject(event)
        payload = {
            "source": "forge-studios",
            "event_type": str(event["event_type"]),
            "schema_version": "1",
            "subject_type": subject_type,
            "subject_id": subject_id,
            "trace_id": trace_id,
            "span_id": f"studios:{event_id}",
            "correlation_id": trace_id,
            "idempotency_key": event_id,
            "lineage": lineage,
            "evidence_refs": evidence_refs,
            "payload": central_payload,
            "provenance": {
                "producer": "forge-studios",
                "local_event_id": event_id,
                "local_sink": "jsonl",
            },
            "occurred_at": event["recorded_at"],
        }
        try:
            self._request("POST", "items/af_events", json_body=payload)
        except httpx.HTTPStatusError:
            existing = self._request(
                "GET",
                "items/af_events",
                params={"filter": filter_value, "limit": 1},
            )
            if not (isinstance(existing, list) and existing):
                raise

    def safe_mirror(self, event: dict[str, Any]) -> Exception | None:
        if not self.enabled:
            return None
        try:
            self.mirror(event)
        except Exception as exc:  # noqa: BLE001 - central telemetry is best-effort
            return exc
        return None


class TelemetrySink:
    def __init__(
        self,
        path: str | Path = ".agenticforge/telemetry.jsonl",
        *,
        platform_mirror: PlatformTelemetryMirror | None = None,
    ):
        self.path = Path(path)
        self.platform_mirror = platform_mirror or PlatformTelemetryMirror()

    def emit(self, event_type: str, **payload: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        event = {
            "event_id": f"studios_{uuid4().hex}",
            "event_type": event_type,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            **payload,
        }
        with self.path.open("a") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")

        # Local Studios telemetry is authoritative. Shared Platform storage may be
        # temporarily unavailable without changing production behavior.
        self.platform_mirror.safe_mirror(event)


__all__ = ["PlatformTelemetryMirror", "TelemetrySink"]
