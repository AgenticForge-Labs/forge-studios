"""Global normal-mode defaults use GPT Image 2 for both Fal image endpoints.

No real Fal calls. The injected Runtime exposes the exact transport requests
and provider results without credentials, side effects or billed inference.
"""

from __future__ import annotations

import base64

from forge_studios.generation import resolve_generation_profile
from forge_studios.providers.base import MediaRequest
from forge_studios.providers.fal import FalProvider


class FakeRuntime:
    def __init__(self):
        self.requests = []

    def generate(self, **kwargs):
        self.requests.append(kwargs)
        return {
            "provider": "fal",
            "model": kwargs["model"],
            "request_id": f"fake-{len(self.requests)}",
            "result": {
                "images": [{"url": "file:///tmp/gpt-image-result.png",
                            "width": 1920, "height": 1080}]
            },
        }

    def close(self):
        raise AssertionError("injected runtime must not be closed")


def test_normal_is_global_gpt_default_but_cheap_remains_opt_in(monkeypatch):
    monkeypatch.delenv("FORGE_STUDIOS_MODE", raising=False)
    monkeypatch.delenv("FAL_IMAGE_GENERATE_MODEL", raising=False)
    monkeypatch.delenv("FAL_IMAGE_EDIT_MODEL", raising=False)
    profile = resolve_generation_profile()
    assert profile.mode.value == "normal"
    assert profile.image.generate_model == "openai/gpt-image-2"
    assert profile.image.edit_model == "openai/gpt-image-2/edit"
    assert (profile.image.width, profile.image.height) == (1920, 1080)
    assert profile.video.model == "fal-ai/ltx-2.3/image-to-video/fast"

    provider = FalProvider()
    assert provider.image_generate_model == profile.image.generate_model
    assert provider.image_edit_model == profile.image.edit_model

    cheap = resolve_generation_profile("cheap")
    assert cheap.image.generate_model == "fal-ai/flux-2/flash"
    assert cheap.image.edit_model == "fal-ai/flux-2/flash/edit"
    assert cheap.image.width == 768


def test_real_fal_transport_contract_for_text_and_reference_generation(
    monkeypatch, tmp_path,
):
    monkeypatch.delenv("FAL_IMAGE_GENERATE_MODEL", raising=False)
    monkeypatch.delenv("FAL_IMAGE_EDIT_MODEL", raising=False)
    runtime = FakeRuntime()
    provider = FalProvider(mode="normal", runtime_client=runtime)
    ref = tmp_path / "reference.png"
    ref.write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    ))

    text_only = MediaRequest(
        kind="image", role="start_frame", shot_id="no-refs",
        prompt="Exact authored no-reference prompt.",
    )
    with_refs = MediaRequest(
        kind="image", role="start_frame", shot_id="with-refs",
        prompt="Exact authored reference prompt: @image1.",
        reference_assets=(str(ref),),
    )
    assert provider.model_for(text_only) == "openai/gpt-image-2"
    assert provider.model_for(with_refs) == "openai/gpt-image-2/edit"
    result1 = provider.generate(text_only)
    result2 = provider.generate(with_refs)

    first, second = runtime.requests
    assert first["model"] == "openai/gpt-image-2"
    assert first["arguments"] == {
        "prompt": text_only.prompt, "image_size": {"width": 1920, "height": 1080},
        "quality": "high", "num_images": 1,
    }
    assert first["assets"] == {}
    assert second["model"] == "openai/gpt-image-2/edit"
    assert second["arguments"] == {
        "prompt": with_refs.prompt, "image_size": {"width": 1920, "height": 1080},
        "quality": "high", "num_images": 1, "image_urls": ["asset://reference_0"],
    }
    assert list(second["assets"]) == ["reference_0"]
    assert result1[0].model == "openai/gpt-image-2"
    assert result2[0].model == "openai/gpt-image-2/edit"
    assert result1[0].metadata["provider_settings"]["quality"] == "high"
    assert result2[0].metadata["provider_settings"]["num_images"] == 1


def test_model_overrides_are_still_independent(monkeypatch):
    monkeypatch.setenv("FAL_IMAGE_GENERATE_MODEL", "test/custom-text-model")
    monkeypatch.setenv("FAL_IMAGE_EDIT_MODEL", "test/custom-reference-model")
    provider = FalProvider(mode="normal")
    assert provider.image_generate_model == "test/custom-text-model"
    assert provider.image_edit_model == "test/custom-reference-model"
    explicit = FalProvider(
        mode="normal", image_generate_model="another/generated",
        image_edit_model="another/edited",
    )
    assert explicit.image_generate_model == "another/generated"
    assert explicit.image_edit_model == "another/edited"
    unchanged = resolve_generation_profile(
        image_generate_model="another/generated",
        image_edit_model="another/edited",
    )
    assert "image_generate_model" in unchanged.explicit_overrides
    assert "image_edit_model" in unchanged.explicit_overrides
