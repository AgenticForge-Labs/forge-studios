"""Cross-repository regression: JSON-plus-prose frames remain statically checked."""

from __future__ import annotations

import json

from forge_studios.contracts import EpisodePackage, Shot
from forge_studios.frame_plan import (
    _boundary_core,
    validate_frame_plans,
)


def _structured_frame(
    prose: str,
    *,
    role: str = "start_frame",
    pose: str = "lying down on the central stone altar, forelegs tucked",
    staging: str = "A small red dragon lies on the altar, belly touching stone.",
) -> str:
    specification = {
        "media": "image",
        "frame_role": role,
        "subjects": [{
            "description": "a small red four-legged dragon",
            "shot_pose": pose,
            "identity_constraints": [
                "Exactly two small wings, symmetrically attached to the upper back.",
                "Two silver horns, four feet, and a silver collar.",
            ],
            "excluded_features": ["walking", "missing wings"],
        }],
        "reference_roles": [
            {"image": "@image1", "instruction": "Identity only, not the reference pose."}
        ],
        "shot_staging": {"authored_frame_description": staging},
    }
    return (
        "REFERENCE AND GENERATION INSTRUCTIONS\n"
        "The current shot pose overrides the pose shown in reference images; "
        "for video the first movement occurs only after this image.\n\n"
        "SHOT SPECIFICATION (JSON)\n"
        + json.dumps(specification, indent=2)
        + "\n\nIMAGE PROMPT\n"
        + prose
    )


def _package(start: str, end: str | None = None) -> EpisodePackage:
    return EpisodePackage(
        package_version="episode_package",
        production_id="structured-test",
        episode_id="episode",
        world_id="world",
        show_id="show",
        title="Structure test",
        arc="Test boundary stills.",
        target_duration_seconds=10,
        shots=[Shot(
            shot_id="shot_one",
            duration_seconds=10,
            site_id="place",
            start_frame_prompt=start,
            end_frame_prompt=end,
            video_prompt="After holding a beat, the dragon stands up.",
        )],
        assets=[],
    )


def test_structured_frame_validates_without_interpreting_json_quotes_as_dialogue():
    prompt = _structured_frame(
        "REFERENCES\n@image1: character identity; EDIT controls pose.\n\n"
        "EDIT\nA small red dragon lies down on a stone altar, all paws tucked.\n\n"
        "OUTPUT\nClean cinematic 16:9 frame.",
    )
    assert validate_frame_plans(_package(prompt)) == []
    assert _boundary_core(prompt).startswith("REFERENCES\n")
    assert "SHOT SPECIFICATION" not in _boundary_core(prompt)


def test_actual_motion_in_image_prose_is_still_rejected():
    prompt = _structured_frame("EDIT\nThe dragon walks toward the camera.")
    codes = {x.code for x in validate_frame_plans(_package(prompt))}
    assert "BOUNDARY_FRAME_NONVISUAL_OR_TEMPORAL" in codes


def test_transient_action_in_json_staging_is_still_rejected():
    prompt = _structured_frame(
        "EDIT\nA small red dragon holds a settled stance on a stone altar.",
        staging="A small red dragon is mid-jump over the altar.",
    )
    codes = {x.code for x in validate_frame_plans(_package(prompt))}
    assert "BOUNDARY_FRAME_TRANSIENT_ACTION" in codes


def test_transient_shot_pose_is_still_rejected():
    prompt = _structured_frame(
        "EDIT\nA small red dragon holds a settled stance on a stone altar.",
        pose="mid-jump from the raised altar",
    )
    codes = {x.code for x in validate_frame_plans(_package(prompt))}
    assert "BOUNDARY_FRAME_TRANSIENT_ACTION" in codes


def test_malformed_json_never_bypasses_static_frame_validation():
    prompt = _structured_frame("EDIT\nA settled red dragon.")
    malformed = prompt.replace('"media": "image"', '"media": broken')
    codes = {x.code for x in validate_frame_plans(_package(malformed))}
    assert "BOUNDARY_FRAME_SPEC_INVALID" in codes
    missing_prose = prompt.split("\n\nIMAGE PROMPT\n", 1)[0]
    codes = {x.code for x in validate_frame_plans(_package(missing_prose))}
    assert "BOUNDARY_FRAME_SPEC_INVALID" in codes


def test_similarity_comparison_uses_frame_compositions_not_shared_json_policy():
    start = _structured_frame("EDIT\nSmall dragon lies resting on stone altar.")
    end = _structured_frame(
        "END EDIT\nSmall dragon stands beside a distant woodland path.",
        role="end_frame",
        staging="Small dragon stands beside a distant woodland path.",
    )
    assert validate_frame_plans(_package(start, end)) == []
    identical = _structured_frame(
        "EDIT\nSmall dragon lies resting on stone altar.",
        role="end_frame",
    )
    codes = {x.code for x in validate_frame_plans(_package(start, identical))}
    assert "BOUNDARY_FRAME_PROMPTS_DUPLICATE" in codes
