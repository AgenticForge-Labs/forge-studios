import os
import sys
from types import SimpleNamespace

import pytest

from forge_studios import entrypoint
from forge_studios.animator import AnimatorService
from forge_studios.contracts import AssetRecord, EpisodePackage, FramePlan, Scene, Shot
from forge_studios.generation import GenerationMode, parse_generation_mode, resolve_generation_profile
from forge_studios.providers.base import MediaRequest, MediaResult
from forge_studios.providers.fal import FalProvider, image_model_profile, resolve_supported_image_size


class _LocalConfig:
    def resolve(self, _name):
        return "test-key"


def _install_fake_fal(monkeypatch, response):
    seen = {}

    class FakeClient:
        def __init__(self, key=None):
            seen["key"] = key

        def upload_file(self, path):
            return f"file://{path}"

        def subscribe(self, model, arguments, **kwargs):
            seen["model"] = model
            seen["arguments"] = arguments
            seen["kwargs"] = kwargs
            return response

    monkeypatch.setitem(sys.modules, "fal_client", SimpleNamespace(SyncClient=FakeClient))
    return seen


def test_omitted_mode_resolves_to_normal():
    profile = resolve_generation_profile()
    assert profile.mode is GenerationMode.NORMAL
    assert profile.image.generate_model == "fal-ai/flux-2/flash"
    assert profile.image.edit_model == "fal-ai/flux-2-pro/edit"
    assert profile.video.model == "fal-ai/ltx-2.3/image-to-video/fast"
    assert profile.video.resolution == "1080p"


def test_cheap_profile_keeps_semantic_768_by_432_target():
    profile = resolve_generation_profile("cheap")
    assert profile.image.generate_model == "fal-ai/flux-2/flash"
    assert profile.image.edit_model == "fal-ai/flux-2/flash/edit"
    assert (profile.image.width, profile.image.height) == (768, 432)
    assert profile.video.model == "fal-ai/ltx-2.3-22b/distilled/image-to-video"
    assert (profile.video.width, profile.video.height) == (768, 432)
    assert profile.video.resolution is None


def test_invalid_mode_fails_clearly():
    with pytest.raises(ValueError, match="invalid generation mode"):
        parse_generation_mode("draft")


def test_explicit_profile_overrides_win_over_mode():
    profile = resolve_generation_profile(
        "cheap",
        image_edit_model="fal-ai/flux-2-pro/edit",
        image_width=960,
        image_height=540,
        video_model="vendor/video",
        video_resolution="1080p",
    )
    assert profile.image.edit_model == "fal-ai/flux-2-pro/edit"
    assert (profile.image.width, profile.image.height) == (960, 540)
    assert profile.video.model == "vendor/video"
    assert profile.video.resolution == "1080p"
    assert profile.video.width is None and profile.video.height is None
    assert set(profile.explicit_overrides) == {
        "image_edit_model", "image_size", "video_model", "video_resolution"
    }


def test_provider_selects_t2i_without_refs_and_edit_model_with_refs():
    provider = FalProvider(mode="cheap", local_config=_LocalConfig())
    pure = MediaRequest(kind="image", shot_id="pure", prompt="a forge")
    edit = MediaRequest(kind="image", shot_id="edit", prompt="ember in forge", reference_assets=("ref.png",))
    assert provider.model_for(pure) == "fal-ai/flux-2/flash"
    assert provider.model_for(edit) == "fal-ai/flux-2/flash/edit"


def test_flux_flash_cheap_target_resolves_to_smallest_supported_exact_16_by_9():
    assert resolve_supported_image_size("fal-ai/flux-2/flash", 768, 432) == (912, 513)
    assert resolve_supported_image_size("fal-ai/flux-2/flash/edit", 768, 432) == (912, 513)
    assert resolve_supported_image_size("fal-ai/flux-2/flash", 1024, 576) == (1024, 576)


def test_cheap_image_edit_uses_supported_fallback_and_records_target_and_actual(monkeypatch):
    seen = _install_fake_fal(monkeypatch, {"images": [{"url": "/tmp/frame.png", "width": 912, "height": 513}]})
    provider = FalProvider(mode="cheap", local_config=_LocalConfig())
    result = provider.generate(MediaRequest(
        kind="image", shot_id="s", prompt="compose", reference_assets=("/tmp/ref.png",)
    ))[0]
    assert seen["model"] == "fal-ai/flux-2/flash/edit"
    assert seen["arguments"]["image_size"] == {"width": 912, "height": 513}
    assert result.metadata["generation_mode"] == "cheap"
    assert result.metadata["provider_settings"]["image_target_size"] == {"width": 768, "height": 432}
    assert result.metadata["actual_media"] == {"width": 912, "height": 513}


def test_flash_edit_rejects_more_than_four_references_instead_of_silently_dropping_them():
    assert image_model_profile("fal-ai/flux-2/flash/edit").max_references == 4
    with pytest.raises(ValueError, match="at most 4"):
        FalProvider._image_reference_payload(
            "fal-ai/flux-2/flash/edit",
            [f"https://example.test/{index}.png" for index in range(5)],
        )


def test_explicit_image_size_overrides_cheap_default(monkeypatch):
    seen = _install_fake_fal(monkeypatch, {"images": [{"url": "/tmp/frame.png"}]})
    provider = FalProvider(mode="cheap", local_config=_LocalConfig())
    provider.generate(MediaRequest(
        kind="image", shot_id="s", prompt="compose",
        options={"image_size": {"width": 1024, "height": 576}},
    ))
    assert seen["arguments"]["image_size"] == {"width": 1024, "height": 576}


def test_cheap_video_requests_768_by_432_and_preserves_duration_as_frames(monkeypatch):
    seen = _install_fake_fal(monkeypatch, {
        "video": {"url": "/tmp/video.mp4", "width": 768, "height": 432, "fps": 24, "duration": 20.0, "num_frames": 480}
    })
    provider = FalProvider(mode="cheap", local_config=_LocalConfig())
    result = provider.generate(MediaRequest(
        kind="video", shot_id="v", prompt="move", start_frame_asset="/tmp/start.png", duration_seconds=20,
    ))[0]
    assert seen["model"] == "fal-ai/ltx-2.3-22b/distilled/image-to-video"
    assert seen["arguments"]["video_size"] == {"width": 768, "height": 432}
    assert seen["arguments"]["num_frames"] == 480
    assert "duration" not in seen["arguments"]
    assert result.metadata["actual_media"]["duration"] == 20.0
    assert result.metadata["actual_media"]["width"] == 768


def test_normal_video_requests_fast_1080p_and_preserves_supported_duration(monkeypatch):
    seen = _install_fake_fal(monkeypatch, {
        "video": {"url": "/tmp/video.mp4", "width": 1920, "height": 1080, "fps": 25, "duration": 20.0}
    })
    provider = FalProvider(mode="normal", local_config=_LocalConfig())
    provider.generate(MediaRequest(
        kind="video", shot_id="v", prompt="move", start_frame_asset="/tmp/start.png", duration_seconds=20,
    ))
    assert seen["model"] == "fal-ai/ltx-2.3/image-to-video/fast"
    assert seen["arguments"]["resolution"] == "1080p"
    assert seen["arguments"]["duration"] == "20"


def test_normal_video_refuses_to_silently_shorten_unsupported_duration(monkeypatch):
    _install_fake_fal(monkeypatch, {"video": {"url": "/tmp/video.mp4"}})
    provider = FalProvider(mode="normal", local_config=_LocalConfig())
    with pytest.raises(Exception, match="will not silently shorten"):
        provider.generate(MediaRequest(
            kind="video", shot_id="v", prompt="move", start_frame_asset="/tmp/start.png", duration_seconds=15,
        ))


def test_explicit_video_size_overrides_cheap_mode(monkeypatch):
    seen = _install_fake_fal(monkeypatch, {"video": {"url": "/tmp/video.mp4"}})
    provider = FalProvider(mode="cheap", local_config=_LocalConfig())
    provider.generate(MediaRequest(
        kind="video", shot_id="v", prompt="move", start_frame_asset="/tmp/start.png",
        options={"video_size": {"width": 960, "height": 540}, "num_frames": 240},
    ))
    assert seen["arguments"]["video_size"] == {"width": 960, "height": 540}


def test_animator_passes_shot_duration_to_video_provider_and_persists_generation_provenance():
    class CaptureProvider:
        name = "capture"
        def __init__(self):
            self.request = None
        def generation_settings(self):
            return {"mode": "cheap", "video_model": "test-video", "explicit_overrides": []}
        def model_for(self, _request):
            return "test-video"
        def generate(self, request):
            self.request = request
            return [MediaResult(
                uri="/tmp/video.mp4", provider=self.name, model="test-video",
                metadata={
                    "generation_mode": "cheap",
                    "provider_settings": {"video_size": {"width": 768, "height": 432}},
                    "actual_media": {"width": 768, "height": 432, "duration": 20.0, "fps": 24},
                },
            )]

    shot = Shot(
        shot_id="v", duration_seconds=20, visual="Ember walks forward.", render_strategy="generated_video",
        frame_plan=FramePlan(mode="start_only", start_asset_id="start"),
        approved_start_frame_asset_id="start",
    )
    package = EpisodePackage(
        production_id="ep", episode_id="ep", title="test",
        scenes=[Scene(scene_id="scene", shots=[shot])],
        assets=[AssetRecord(asset_id="start", kind="start_frame", uri="/tmp/start.png", status="approved")],
    )
    provider = CaptureProvider()
    asset = AnimatorService(provider).generate(package, "v", role="video")[0]
    assert provider.request.duration_seconds == 20
    assert asset.metadata["generation"]["mode"] == "cheap"
    assert asset.metadata["generation"]["model"] == "test-video"
    assert asset.metadata["generation"]["provider_settings"]["video_size"] == {"width": 768, "height": 432}
    assert asset.metadata["generation"]["actual_media"]["duration"] == 20.0
    assert package.trace["generation_attempts"][-1]["metadata"]["requested_duration_seconds"] == 20


def test_studio_cli_consumes_mode_and_propagates_semantic_mode_to_worlds(monkeypatch):
    captured = {}
    monkeypatch.setattr(entrypoint.cli, "main", lambda argv: captured.setdefault("argv", argv) or 0)
    monkeypatch.delenv("FORGE_STUDIOS_MODE", raising=False)
    monkeypatch.delenv("FORGE_WORLDS_MODE", raising=False)
    result = entrypoint.main([
        "generate", "--package", "episode.json", "--shot-id", "s", "--provider", "mock", "--mode", "cheap"
    ])
    assert result == captured["argv"]
    assert "--mode" not in captured["argv"]
    assert os.environ["FORGE_STUDIOS_MODE"] == "cheap"
    assert os.environ["FORGE_WORLDS_MODE"] == "cheap"


def test_non_generation_mode_argument_is_left_for_existing_commands():
    cleaned, mode = entrypoint._extract_generation_mode([
        "set-frame-plan", "--package", "episode.json", "--shot-id", "s", "--mode", "start_and_end"
    ])
    assert mode == "normal"
    assert cleaned[-2:] == ["--mode", "start_and_end"]


def test_studios_provider_exports_do_not_include_openrouter():
    from forge_studios import providers
    assert "OpenRouterImageProvider" not in providers.__all__
