from __future__ import annotations

import json

from forge_studios.animator import AnimatorService
from forge_studios.contracts import AssetRecord, EpisodePackage, Shot
from forge_studios.director import plan_work
from forge_studios.frame_plan import validate_frame_plans
from forge_studios.io import load_package, save_json
from forge_studios.package_ops import approve_asset, bind_inherited_start_frame
from forge_studios.providers import MockProvider
from forge_studios.storyboard import build_storyboard, generate_boundary_candidates
from forge_studios.telemetry import TelemetrySink


def make_package(*, inherited: bool = True) -> EpisodePackage:
    return EpisodePackage(
        package_version="episode_package",
        production_id="p",
        episode_id="e",
        world_id="w",
        show_id="s",
        title="Episode",
        premise="Ember wakes and explores.",
        arc="Confusion becomes curiosity.",
        themes=["identity"],
        target_duration_seconds=40,
        beats=[{"beat_id": "wake"}, {"beat_id": "leave"}],
        shots=[
            Shot(
                shot_id="wake", beat_id="wake", duration_seconds=20,
                site_id="forge", character_ids=["ember"],
                reference_asset_ids=["ember_ref", "forge_ref"],
                frame_plan_mode="start_and_end",
                start_frame_prompt="Wide objective view of Ember on the altar with eyes closed.",
                end_frame_prompt="Medium objective view of Ember standing at the altar edge, looking south.",
                video_prompt="Ember blinks three times, rises, and asks \"Where am I?\" in his soft sincere established voice.",
            ),
            Shot(
                shot_id="leave", beat_id="leave", duration_seconds=20,
                site_id="forge", character_ids=["ember"],
                reference_asset_ids=["ember_ref", "forge_ref"],
                frame_plan_mode="start_and_end",
                inherits_start_from_shot_id="wake" if inherited else None,
                start_frame_prompt="Medium objective view of Ember standing at the altar edge, looking south.",
                end_frame_prompt="Wide objective view of Ember at the southern opening, facing the path.",
                video_prompt="Ember walks toward the southern opening as the objective camera tracks beside him and forest ambience grows clearer.",
            ),
        ],
        assets=[
            AssetRecord(asset_id="ember_ref", entity_id="ember", kind="reference_image", uri="/tmp/ember.png", status="canon"),
            AssetRecord(asset_id="forge_ref", entity_id="forge", kind="reference_image", uri="/tmp/forge.png", status="canon"),
        ],
    )


def test_public_json_remains_current_contract(tmp_path):
    path = save_json(tmp_path / "package.json", make_package())
    raw = json.loads(path.read_text())
    assert raw["package_version"] == "episode_package"
    assert "scenes" not in raw
    serialized = path.read_text()
    for forbidden in ("execution_route", "render_strategy", '"frame_plan":', "physical_take"):
        assert forbidden not in serialized
    loaded = load_package(path)
    assert loaded.shots[1].frame_plan.chain_from_shot_id == "wake"
    assert loaded.shots[0].frame_plan.mode == "start_and_end"


def test_validation_keeps_boundary_checks_without_provider_word_policing():
    package = make_package()
    assert validate_frame_plans(package) == []

    package.shots[0].end_frame_prompt = package.shots[0].start_frame_prompt
    assert any(issue.code == "BOUNDARY_FRAME_PROMPTS_DUPLICATE" for issue in validate_frame_plans(package))

    package = make_package()
    package.shots[0].end_frame_prompt = "Ember is mid-jump above the altar."
    assert any(issue.code == "BOUNDARY_FRAME_TRANSIENT_ACTION" for issue in validate_frame_plans(package))

    package = make_package()
    package.shots[0].end_frame_prompt = "The camera tracks Ember as he speaks over an audible hum."
    assert any(issue.code == "BOUNDARY_FRAME_NONVISUAL_OR_TEMPORAL" for issue in validate_frame_plans(package))

    package = make_package()
    package.shots[0].video_prompt += " An intimate view holds on Ember."
    assert not any(issue.code == "PROVIDER_SENSITIVE_PROMPT_WORDING" for issue in validate_frame_plans(package))


def test_boundary_generation_reuses_exact_predecessor_candidate(tmp_path):
    package = make_package()
    service = AnimatorService(MockProvider(tmp_path / "media"), TelemetrySink(tmp_path / "telemetry.jsonl"))
    generated = generate_boundary_candidates(package, service)
    assert len(generated) == 3
    assert package.shots[1].start_frame_asset_ids[-1] == package.shots[0].end_frame_asset_ids[-1]
    assert package.shots[0].start_frame_asset_ids
    assert package.shots[0].end_frame_asset_ids
    assert package.shots[1].end_frame_asset_ids


def test_approval_binds_inherited_start_and_director_waits_for_review(tmp_path):
    package = make_package()
    sink = TelemetrySink(tmp_path / "telemetry.jsonl")
    service = AnimatorService(MockProvider(tmp_path / "media"), sink)
    generate_boundary_candidates(package, service)

    first = package.shots[0]
    approve_asset(package, "wake", "end_frame", first.end_frame_asset_ids[-1], sink)
    inherited = bind_inherited_start_frame(package, "leave")
    assert inherited == first.approved_end_frame_asset_id
    assert package.shots[1].approved_start_frame_asset_id == inherited
    assert plan_work(package)[0].action == "review_start_frame"


def test_storyboard_is_built_from_boundary_pairs_only(tmp_path):
    package = make_package(inherited=False)
    path = build_storyboard(package, tmp_path / "storyboard.html")
    text = path.read_text()
    assert "START FRAME" in text
    assert "END FRAME" in text
    assert "VIDEO MOTION / PERFORMANCE PROMPT" in text
    assert "Route:" not in text
    assert "Purpose:" not in text
