from __future__ import annotations

import base64
from pathlib import Path

import httpx

from forge_studios.ai_runtime import AIRuntimeMediaClient
from forge_studios.finishing import generate_sonilo_music
from forge_studios.providers.base import MediaRequest
from forge_studios.providers.fal import FalProvider


class _FakeRuntime:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return self.result

    def close(self):
        raise AssertionError("injected runtime client must not be closed")


def test_runtime_client_posts_to_shared_media_endpoint():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["authorization"] = request.headers["authorization"]
        seen["body"] = __import__("json").loads(request.content)
        return httpx.Response(
            200,
            json={
                "provider": "fal",
                "model": "fal-ai/test",
                "request_id": "req-1",
                "result": {"images": [{"url": "file:///tmp/result.png"}]},
                "latency_seconds": 0.1,
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    runtime = AIRuntimeMediaClient(
        base_url="http://runtime.test",
        token="runtime-token",
        client=client,
    )
    value = runtime.generate(
        model="fal-ai/test",
        arguments={"prompt": "exact"},
        trace={
            "source_system": "forge-studios",
            "trace_id": "production:run-1",
            "role": "video",
            "lineage": {"production_id": "run-1"},
        },
    )
    client.close()

    assert seen["path"] == "/v1/media/generate"
    assert seen["authorization"] == "Bearer runtime-token"
    assert seen["body"]["trace"]["trace_id"] == "production:run-1"
    assert seen["body"]["arguments"]["prompt"] == "exact"
    assert value["request_id"] == "req-1"


def test_fal_provider_keeps_prompt_semantics_and_sends_local_refs_through_runtime(tmp_path):
    reference = tmp_path / "reference.png"
    reference.write_bytes(b"reference-bytes")
    runtime = _FakeRuntime(
        {
            "provider": "fal",
            "model": "fal-ai/flux-2/flash/edit",
            "request_id": "fal-123",
            "result": {
                "images": [
                    {
                        "url": "file:///tmp/generated.png",
                        "width": 768,
                        "height": 512,
                    }
                ]
            },
            "latency_seconds": 1.0,
        }
    )
    provider = FalProvider(mode="cheap", runtime_client=runtime)

    results = provider.generate(
        MediaRequest(
            kind="image",
            shot_id="shot-1",
            role="start_frame",
            prompt="Exact authored prompt with @image1 unchanged.",
            production_id="run-1",
            episode_id="ep-1",
            show_id="forge-born",
            attempt_id="attempt-1",
            reference_assets=(str(reference),),
        )
    )

    call = runtime.calls[0]
    assert call["arguments"]["prompt"] == "Exact authored prompt with @image1 unchanged."
    assert call["arguments"]["image_urls"] == ["asset://reference_0"]
    assert base64.b64decode(call["assets"]["reference_0"]["data_base64"]) == b"reference-bytes"
    assert call["metadata"]["caller"] == "forge-studios"
    assert call["trace"] == {
        "source_system": "forge-studios",
        "trace_id": "production:run-1",
        "role": "start_frame",
        "purpose": "media_generation",
        "lineage": {
            "show_id": "forge-born",
            "episode_id": "ep-1",
            "production_id": "run-1",
            "shot_id": "shot-1",
            "attempt_id": "attempt-1",
        },
    }
    assert results[0].model == "fal-ai/flux-2/flash/edit"
    assert results[0].metadata["transport"] == "agenticforge-ai-runtime"
    assert results[0].metadata["request_id"] == "fal-123"


def test_sonilo_scoring_uses_runtime_for_local_picture_lock(tmp_path, monkeypatch):
    video = tmp_path / "picture-lock.mp4"
    video.write_bytes(b"video-bytes")
    output = tmp_path / "music.m4a"
    runtime = _FakeRuntime(
        {
            "provider": "fal",
            "model": "sonilo/v1.1/video-to-music",
            "request_id": "music-1",
            "result": {"audio": {"url": "https://media.test/music.m4a"}},
            "latency_seconds": 2.0,
        }
    )

    def fake_download(_url, target):
        Path(target).write_bytes(b"music-bytes")

    monkeypatch.setattr("forge_studios.finishing.urlretrieve", fake_download)
    result = generate_sonilo_music(
        video,
        output,
        prompt="Show style",
        runtime_client=runtime,
        progress=None,
        production_id="run-1",
        episode_id="ep-1",
        show_id="forge-born",
    )

    call = runtime.calls[0]
    assert call["arguments"]["video_url"] == "asset://picture_lock"
    assert call["arguments"]["prompt"] == "Show style"
    assert call["trace"] == {
        "source_system": "forge-studios",
        "trace_id": "production:run-1",
        "role": "music",
        "purpose": "music_generation",
        "lineage": {
            "show_id": "forge-born",
            "episode_id": "ep-1",
            "production_id": "run-1",
        },
    }
    assert base64.b64decode(call["assets"]["picture_lock"]["data_base64"]) == b"video-bytes"
    assert result.read_bytes() == b"music-bytes"
