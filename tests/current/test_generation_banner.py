"""Startup banner reflects the effective Fal override, not an unrelated default."""

from forge_studios.entrypoint import _log_fal_generation_profile


def test_generation_banner_reports_effective_storyboard_edit_model(capsys, monkeypatch):
    monkeypatch.delenv("FAL_IMAGE_EDIT_MODEL", raising=False)
    _log_fal_generation_profile(
        ["storyboard", "--generate", "--provider", "fal",
         "--image-edit-model", "openai/gpt-image-2/edit"],
        "normal",
    )
    captured = capsys.readouterr()
    assert "[generation] mode=normal" in captured.err
    assert "[generation] image_edit_model=openai/gpt-image-2/edit" in captured.err
    assert "fal-ai/flux-2-pro/edit" not in captured.err


def test_generation_banner_honors_env_override_for_single_image(capsys, monkeypatch):
    monkeypatch.setenv("FAL_IMAGE_EDIT_MODEL", "openai/gpt-image-2/edit")
    _log_fal_generation_profile(
        ["generate", "--provider", "fal", "--shot-id", "awakening"],
        "normal",
    )
    assert "image_edit_model=openai/gpt-image-2/edit" in capsys.readouterr().err
