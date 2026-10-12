"""Registered, resumable model-specific storyboard refresh, never video or auto-approval."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from forge_studios import cli
from forge_studios.animator import AnimatorService
from forge_studios.contracts import AssetRecord, EpisodePackage, Shot
from forge_studios.io import load_package, save_json
from forge_studios.providers.base import MediaRequest, MediaResult
from forge_studios.providers.fal import FalProvider
from forge_studios.storyboard import generate_boundary_candidates
from forge_studios.telemetry import TelemetrySink

_OPENAI = "openai/gpt-image-2/edit"
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class RecordingProvider:
    name = "fal"
    image_edit_model = _OPENAI

    def __init__(self, root: Path):
        self.root = root
        self.requests: list[MediaRequest] = []

    def generation_settings(self):
        return {"mode": "normal", "image_edit_model": _OPENAI}

    def model_for(self, request: MediaRequest):
        assert request.kind == "image"
        return _OPENAI

    def generate(self, request: MediaRequest):
        self.requests.append(request)
        path = self.root / f"generated-{request.shot_id}-{len(self.requests)}.png"
        path.write_bytes(_PNG)
        return [MediaResult(
            uri=str(path), provider="fal", model=_OPENAI,
            metadata={"provider_settings": {"quality": "high", "num_images": 1}},
        )]


def _package(tmp_path, *, with_gpt=False):
    ref = tmp_path / "canonical.png"
    ref.write_bytes(_PNG)
    old = tmp_path / "previous-flux.png"
    old.write_bytes(_PNG)
    existing = AssetRecord(
        asset_id="old-flux", kind="start_frame", uri=str(old),
        status="candidate", authority="generated", shot_id="awakening",
        model="fal-ai/flux-2-pro/edit", provider="fal",
    )
    shots = [
        Shot(
            shot_id=shot_id, site_id="forge", duration_seconds=20,
            reference_asset_ids=["canon"],
            start_frame_prompt=f"Authored {shot_id} start prompt.",
            video_prompt=f"Authored {shot_id} video prompt.",
        )
        for shot_id in ("awakening", "jump_down", "path_question")
    ]
    shots[0].start_frame_asset_ids.append("old-flux")
    assets = [
        AssetRecord(asset_id="canon", kind="reference_image", uri=str(ref),
                    status="canon", authority="locked"),
        existing,
    ]
    if with_gpt:
        prior = tmp_path / "existing-gpt.png"
        prior.write_bytes(_PNG)
        assets.append(AssetRecord(
            asset_id="prior-gpt", kind="start_frame", uri=str(prior),
            status="candidate", authority="generated", shot_id="path_question",
            provider="fal", model=_OPENAI,
            metadata={"generation": {
                "prompt": shots[2].start_frame_prompt,
                "reference_asset_ids": ["canon"],
                "role": "start_frame",
            }},
        ))
        shots[2].start_frame_asset_ids.append("prior-gpt")
    return EpisodePackage(
        production_id="run-1", episode_id="episode-1", show_id="forge-born",
        title="Episode 1", arc="Awakening", target_duration_seconds=60,
        shots=shots, assets=assets,
    )


def test_refresh_registers_new_candidates_preserving_old_assets_and_attempts(tmp_path):
    p = _package(tmp_path, with_gpt=True)
    path = tmp_path / "episode-package.json"
    save_json(path, p)
    old_sha = p.shots[0].start_frame_asset_ids[:]
    provider = RecordingProvider(tmp_path)
    animator = AnimatorService(provider, TelemetrySink(tmp_path / "telemetry.jsonl"))
    updates = []
    result = generate_boundary_candidates(
        p, animator, refresh_model=_OPENAI,
        on_frame_complete=lambda package, shot, role, assets: (
            save_json(path, package),
            updates.append((shot.shot_id, role, len(assets))),
        ),
    )
    assert [r.kind for r in result] == ["start_frame", "start_frame"]
    assert [r.model for r in result] == [_OPENAI, _OPENAI]
    assert [req.shot_id for req in provider.requests] == ["awakening", "jump_down"]
    assert all(req.kind == "image" and req.role == "start_frame" for req in provider.requests)
    assert len(updates) == 2
    assert p.shots[0].start_frame_asset_ids[:1] == old_sha
    assert p.shots[0].start_frame_asset_ids[-1] != "old-flux"
    assert p.shots[2].start_frame_asset_ids == ["prior-gpt"]
    assert p.find_asset("old-flux").status == "candidate"
    assert all(r.status == "candidate" for r in result)
    assert all(r.metadata["generation"]["prompt"] == p.find_shot(r.shot_id).start_frame_prompt
               for r in result)
    assert all(r.metadata["generation"]["reference_asset_ids"] == ["canon"] for r in result)
    assert all(p.find_shot(sid).approved_start_frame_asset_id is None
               for sid in ("awakening", "jump_down", "path_question"))
    persisted = load_package(path)
    assert persisted.find_shot("awakening").start_frame_asset_ids[-1] == result[0].asset_id
    assert persisted.find_shot("jump_down").start_frame_asset_ids[-1] == result[1].asset_id
    assert persisted.find_shot("path_question").start_frame_asset_ids == ["prior-gpt"]
    # Reruns don't duplicate already registered matching candidates.
    again = generate_boundary_candidates(p, animator, refresh_model=_OPENAI)
    assert again == []
    assert len(provider.requests) == 2


def test_missing_image_or_changed_authored_prompt_requires_fresh_candidate(tmp_path):
    p = _package(tmp_path, with_gpt=True)
    provider = RecordingProvider(tmp_path)
    animator = AnimatorService(provider, TelemetrySink(tmp_path / "events.jsonl"))
    # Existing image is no longer on disk: never treat it as finished.
    Path(p.find_asset("prior-gpt").uri).unlink()
    result = generate_boundary_candidates(p, animator, refresh_model=_OPENAI)
    assert len(result) == 3
    assert p.shots[2].start_frame_asset_ids[-1] != "prior-gpt"


def test_approved_asset_blocks_paid_batch_before_any_generation(tmp_path):
    p = _package(tmp_path)
    p.shots[-1].approved_start_frame_asset_id = "old-flux"
    provider = RecordingProvider(tmp_path)
    with pytest.raises(ValueError, match="human-approved"):
        generate_boundary_candidates(p, AnimatorService(provider), refresh_model=_OPENAI)
    assert provider.requests == []


def test_original_skip_behavior_is_unchanged(tmp_path):
    p = _package(tmp_path)
    provider = RecordingProvider(tmp_path)
    generate_boundary_candidates(p, AnimatorService(provider))
    assert [r.shot_id for r in provider.requests] == ["jump_down", "path_question"]


def test_gpt_fal_edit_settings_match_successful_comparison():
    fal = FalProvider(mode="normal", image_edit_model=_OPENAI)
    req = MediaRequest(
        kind="image", shot_id="awakening", role="start_frame",
        prompt="Exact authored prompt",
        reference_assets=("ref1", "ref2"),
    )
    assert fal.model_for(req) == _OPENAI
    options = {"prompt": req.prompt}
    fal._apply_profile_defaults(req, _OPENAI, options)
    assert options["prompt"] == req.prompt
    assert options["image_size"] == {"width": 1920, "height": 1080}
    assert options["quality"] == "high"
    assert options["num_images"] == 1
    assert fal._image_reference_payload(_OPENAI, ["ref1", "ref2"]) == {
        "image_urls": ["ref1", "ref2"]
    }


def test_cli_rerun_flags_and_no_video(tmp_path, monkeypatch):
    p = _package(tmp_path)
    file = save_json(tmp_path / "episode-package.json", p)
    provider = RecordingProvider(tmp_path)
    chosen = []

    def fake_provider(_name, _output, **kwargs):
        chosen.append(kwargs["image_edit_model"])
        return provider

    monkeypatch.setattr(cli, "_provider", fake_provider)
    out = tmp_path / "storyboard.html"
    cli.main([
        "storyboard", "--package", str(file), "--out", str(out),
        "--generate", "--provider", "fal", "--image-edit-model", _OPENAI,
        "--refresh-model-candidates", "--output-dir", str(tmp_path),
        "--telemetry", str(tmp_path / "events.jsonl"),
    ])
    assert chosen == [_OPENAI]
    assert [r.role for r in provider.requests] == ["start_frame"] * 3
    saved = load_package(file)
    assert len(saved.find_shot("awakening").start_frame_asset_ids) == 2
    assert len(saved.find_shot("jump_down").start_frame_asset_ids) == 1
    assert saved.find_shot("awakening").approved_start_frame_asset_id is None
    assert all(a.kind != "video" for a in saved.assets)
    assert out.is_file()
    cli.main([
        "storyboard", "--package", str(file), "--out", str(out),
        "--generate", "--provider", "fal", "--image-edit-model", _OPENAI,
        "--refresh-model-candidates", "--output-dir", str(tmp_path),
        "--telemetry", str(tmp_path / "events.jsonl"),
    ])
    assert len(provider.requests) == 3


def test_cli_refresh_refuses_missing_references_and_auto_approval(tmp_path):
    p = _package(tmp_path)
    p.shots[1].reference_asset_ids = []
    path = save_json(tmp_path / "package.json", p)
    opts = [
        "storyboard", "--package", str(path), "--out", str(tmp_path / "out.html"),
        "--generate", "--provider", "fal", "--image-edit-model", _OPENAI,
        "--refresh-model-candidates", "--output-dir", str(tmp_path),
    ]
    with pytest.raises(ValueError, match="requires canonical references"):
        cli.main(opts)
    with pytest.raises(ValueError, match="cannot auto-approve"):
        cli.main(opts + ["--auto-approve"])
