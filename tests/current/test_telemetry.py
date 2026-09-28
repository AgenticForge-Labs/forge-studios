from __future__ import annotations

import json
from typing import Any

import httpx

from forge_studios.telemetry import PlatformTelemetryMirror, TelemetrySink


def test_local_telemetry_keeps_prompt_while_platform_uses_evidence_pointer(tmp_path) -> None:
    evidence_by_sha: dict[str, dict[str, Any]] = {}
    events: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/items/af_evidence" and request.method == "GET":
            filter_value = json.loads(request.url.params["filter"])
            sha = next(
                item["sha256"]["_eq"]
                for item in filter_value["_and"]
                if "sha256" in item
            )
            row = evidence_by_sha.get(sha)
            return httpx.Response(200, json={"data": [row] if row else []})
        if request.url.path == "/items/af_events" and request.method == "GET":
            return httpx.Response(200, json={"data": []})

        payload = json.loads(request.content)
        if request.url.path == "/items/af_evidence":
            row = {"id": f"evidence-{len(evidence_by_sha) + 1}", **payload}
            evidence_by_sha[payload["sha256"]] = row
            return httpx.Response(200, json={"data": row})
        if request.url.path == "/items/af_events":
            events.append(payload)
            return httpx.Response(200, json={"data": {"id": "event-1", **payload}})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    mirror = PlatformTelemetryMirror(
        base_url="https://directus.test",
        token="evidence-token",
        client=client,
    )
    path = tmp_path / "telemetry.jsonl"
    sink = TelemetrySink(path, platform_mirror=mirror)

    sink.emit(
        "generation_attempt.failed",
        show_id="forge-born",
        production_id="run-1",
        episode_id="ep-1",
        shot_id="shot-1",
        attempt_id="attempt-1",
        prompt="Exact authored provider prompt.",
        metadata={
            "failure_diagnostics": {
                "prompt": "Exact authored provider prompt.",
                "failure_class": "content_policy",
            }
        },
    )
    client.close()

    local = json.loads(path.read_text().strip())
    assert local["prompt"] == "Exact authored provider prompt."
    assert local["metadata"]["failure_diagnostics"]["prompt"] == (
        "Exact authored provider prompt."
    )
    assert local["event_id"].startswith("studios_")

    assert len(evidence_by_sha) == 1
    assert len(events) == 1
    central = events[0]
    assert central["trace_id"] == "production:run-1"
    assert central["subject_type"] == "attempt"
    assert central["subject_id"] == "attempt-1"
    assert central["lineage"]["show_id"] == "forge-born"
    assert "prompt" not in central["payload"]
    assert "prompt" not in central["payload"]["metadata"]["failure_diagnostics"]
    assert len(central["evidence_refs"]) == 2
    assert {
        ref["evidence_id"] for ref in central["evidence_refs"]
    } == {"evidence-1"}


def test_platform_failure_does_not_break_local_telemetry(tmp_path) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    sink = TelemetrySink(
        tmp_path / "telemetry.jsonl",
        platform_mirror=PlatformTelemetryMirror(
            base_url="https://directus.test",
            token="evidence-token",
            client=client,
        ),
    )

    sink.emit(
        "generation_attempt.started",
        production_id="run-1",
        episode_id="ep-1",
        prompt="Still written locally.",
    )
    client.close()

    local = json.loads((tmp_path / "telemetry.jsonl").read_text().strip())
    assert local["prompt"] == "Still written locally."


def test_platform_mirror_is_disabled_without_token(tmp_path) -> None:
    sink = TelemetrySink(
        tmp_path / "telemetry.jsonl",
        platform_mirror=PlatformTelemetryMirror(token=""),
    )
    sink.emit("final_render.completed", production_id="run-1")
    assert (tmp_path / "telemetry.jsonl").is_file()
