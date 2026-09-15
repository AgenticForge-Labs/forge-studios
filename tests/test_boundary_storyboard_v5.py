from pathlib import Path

import pytest

from forge_studios.animator import AnimatorService
from forge_studios.contracts import AssetRecord, EpisodePackage, Scene, Shot
from forge_studios.director import plan_work
from forge_studios.package_ops import approve_asset
from forge_studios.providers import MockProvider
from forge_studios.storyboard import build_storyboard, generate_boundary_candidates
from forge_studios.telemetry import TelemetrySink


def _package(*, chained=False):
    first = Shot(
        shot_id="shot_1",
        duration_seconds=10,
        purpose="Ember reacts",
        visual="Close reaction shot",
        entity_ids=["character_ember", "place_forge"],
        camera={"framing": "close"},
        continuity_asset_ids=["ember", "forge"],
        execution_route="animator",
        render_strategy="generated_video",
        frame_plan={"mode": "start_and_end"},
        image_prompt="Ember in the Forge",
        start_frame_prompt="Ember settled before reacting. No readable text.",
        end_frame_prompt="Ember holds an awed expression after reacting. No readable text.",
        video_prompt="Ember's eyes widen slightly and he speaks; keep supplied boundaries exact.",
    )
    shots = [first]
    if chained:
        shots.append(Shot(
            shot_id="shot_2",
            duration_seconds=10,
            purpose="Continue reaction",
            visual="Continuous close reaction",
            entity_ids=["character_ember", "place_forge"],
            camera={"framing": "close"},
            continuity_asset_ids=["ember", "forge"],
            execution_route="animator",
            render_strategy="generated_video",
            frame_plan={"mode": "start_and_end", "chain_from_shot_id": "shot_1"},
            image_prompt="Continue Ember reaction",
            start_frame_prompt="Exact settled endpoint of the previous shot.",
            end_frame_prompt="Ember settles into a quieter uncertain expression. No readable text.",
            video_prompt="Continue from the exact previous endpoint with subtle breathing and eye movement.",
        ))
    return EpisodePackage(
        production_id="ep1",
        episode_id="ep1",
        title="test",
        status="storyboard_ready",
        scenes=[Scene(scene_id="scene_1", summary="test", shots=shots)],
        assets=[
            AssetRecord(asset_id="ember", entity_id="character_ember", kind="reference_image", uri="ember.png", status="canon", authority="locked", role="canonical-reference", tags=["character"]),
            AssetRecord(asset_id="forge", entity_id="place_forge", kind="reference_image", uri="forge.png", status="canon", authority="locked", role="canonical-establishing", tags=["place"]),
        ],
        trace={
            "production_contract": {
                "planning_storyboard_images": False,
                "final_render_strategy": "generated_video",
                "frame_plan_mode": "start_and_end",
            },
            "production_preflight": {"is_valid": True},
        },
    )


def test_boundary_generation_creates_start_and_end_without_storyboard_asset(tmp_path):
    package = _package()
    service = AnimatorService(MockProvider(tmp_path / "mock"))
    generated = generate_boundary_candidates(package, service)

    shot = package.find_shot("shot_1")
    assert len(generated) == 2
    assert len(shot.start_frame_asset_ids) == 1
    assert len(shot.end_frame_asset_ids) == 1
    assert shot.storyboard_asset_ids == []
    assert package.find_asset(shot.start_frame_asset_ids[-1]).kind == "start_frame"
    assert package.find_asset(shot.end_frame_asset_ids[-1]).kind == "end_frame"
    # Candidate generation must not pretend a human approved either frame.
    assert shot.approved_start_frame_asset_id is None
    assert shot.approved_end_frame_asset_id is None


def test_chained_shot_reuses_exact_predecessor_end_candidate(tmp_path):
    package = _package(chained=True)
    service = AnimatorService(MockProvider(tmp_path / "mock"))
    generate_boundary_candidates(package, service)

    first = package.find_shot("shot_1")
    second = package.find_shot("shot_2")
    assert first.end_frame_asset_ids
    assert second.start_frame_asset_ids[-1] == first.end_frame_asset_ids[-1]
    assert len(second.end_frame_asset_ids) == 1


def test_human_storyboard_contains_boundaries_and_no_storyboard_still_section(tmp_path):
    package = _package()
    service = AnimatorService(MockProvider(tmp_path / "mock"))
    generate_boundary_candidates(package, service)
    out = build_storyboard(package, tmp_path / "board.html")
    text = out.read_text()
    assert "START FRAME" in text
    assert "END FRAME" in text
    assert "VIDEO MOTION / PERFORMANCE PROMPT" in text
    assert "There are no separate storyboard stills" in text
    assert "planning storyboard image" not in text

    guide = tmp_path / "board.image-guide.html"
    guide_text = guide.read_text()
    assert "INPUT 1" in guide_text
    assert "PROMPT → OUTPUT" in guide_text
    assert "Separate representative storyboard stills are intentionally excluded" in guide_text


def test_boundary_first_director_skips_legacy_storyboard_gate():
    package = _package()
    work = plan_work(package)
    assert work[0].action == "generate_start_frame"
    assert all(item.action != "generate_storyboard" for item in work)


def test_video_generation_requires_human_approved_boundary_assets(tmp_path):
    package = _package()
    service = AnimatorService(MockProvider(tmp_path / "mock"))
    generate_boundary_candidates(package, service)

    with pytest.raises(ValueError, match="human-approved start and end boundary frames"):
        service.generate(package, "shot_1", role="video")

    shot = package.find_shot("shot_1")
    sink = TelemetrySink(tmp_path / "telemetry.jsonl")
    approve_asset(package, "shot_1", "start_frame", shot.start_frame_asset_ids[-1], sink)
    approve_asset(package, "shot_1", "end_frame", shot.end_frame_asset_ids[-1], sink)
    assets = service.generate(package, "shot_1", role="video")
    assert assets[0].kind == "generated_clip"


def test_structured_boundary_prompt_does_not_reinject_raw_story_prose(tmp_path):
    class CaptureProvider(MockProvider):
        def generate(self, request):
            self.last_request = request
            return super().generate(request)

    package = _package()
    shot = package.find_shot("shot_1")
    shot.purpose = "RAW PURPOSE SHOULD NOT LEAK"
    shot.visual = "RAW VISUAL SHOULD NOT LEAK"
    provider = CaptureProvider(tmp_path / "mock")
    service = AnimatorService(provider)
    service.generate(package, "shot_1", role="start_frame")
    prompt = provider.last_request.prompt
    assert "RAW PURPOSE SHOULD NOT LEAK" not in prompt
    assert "RAW VISUAL SHOULD NOT LEAK" not in prompt
    assert "No readable text" in prompt
