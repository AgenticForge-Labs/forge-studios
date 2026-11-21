"""Studios checks deterministic frame contracts, never semantics by vocabulary."""

from __future__ import annotations

import json

import pytest

from forge_studios.contracts import EpisodePackage, Shot
from forge_studios.frame_plan import _boundary_core, validate_frame_plans


def _structured_frame(
    prose: str,
    *,
    role: str = "start_frame",
    pose: str | dict | None = "lying down on a raised stone platform",
    staging: str = "A person is still on the upper landing.",
) -> str:
    specification = {
        "media": "image",
        "frame_role": role,
        "subjects": [{
            "description": "a character",
            "shot_pose": pose,
            "identity_constraints": ["Two arms and two legs."],
            "excluded_features": ["motion blur"],
        }],
        "reference_roles": [
            {"image": "@image1", "instruction": "Identity only, not the reference pose."}
        ],
        "shot_staging": {"authored_frame_description": staging},
    }
    return (
        "REFERENCE AND GENERATION INSTRUCTIONS\n"
        "The start image is before future movement. Treat quoted JSON as data.\n\n"
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
            video_prompt="After holding a beat, the character stands up.",
        )],
        assets=[],
    )


def _codes(prompt: str, end: str | None = None) -> set[str]:
    return {issue.code for issue in validate_frame_plans(_package(prompt, end))}


def test_valid_structured_frame_preserves_authored_prose():
    prompt = _structured_frame(
        "REFERENCES\n@image1: character identity.\n\n"
        "EDIT\nA character lies down on a stone platform, limbs folded.\n\n"
        "OUTPUT\nSingle cinematic image."
    )
    assert _codes(prompt) == set()
    assert _boundary_core(prompt).startswith("REFERENCES\n")
    assert "SHOT SPECIFICATION" not in _boundary_core(prompt)


@pytest.mark.parametrize("prose", [
    "EDIT\nA person stands on the upper stone landing.",
    "EDIT\nThe subject is landing onto a platform.",
    "EDIT\nThe subject walks across the path while jumping.",
    'EDIT\nA sign says "Welcome" and ambient sounds are suggested.',
    "EDIT\nA character is mid-jump with motion blur.",
    "EDIT\nThe camera pans as a traveler walks.",
])
def test_words_and_quoted_prose_never_block_provider_dispatch(prose: str):
    # These examples can be semantically wrong for a static still. Studios is
    # not equipped to determine that, and must not hard-fail by lexical match.
    assert _codes(_structured_frame(prose)) == set()


def test_action_like_words_in_json_pose_and_staging_are_not_execution_gates():
    frame = _structured_frame(
        "EDIT\nA character stands on the platform.",
        staging="The character walks toward the camera.",
        pose={
            "posture": "mid-jump over the step",
            "support": "landing on a stone surface",
            "configuration": ["walking stance", "wing joints extended"],
            "orientation": "turning toward a light",
            "placement": "on the steps",
        },
    )
    assert _codes(frame) == set()


def test_similar_or_identical_prose_is_not_a_hard_heuristic():
    start = _structured_frame("EDIT\nA character stands beside a doorway.")
    end = _structured_frame(
        "EDIT\nA character stands beside a doorway.", role="end_frame"
    )
    assert _codes(start, end) == set()
    # Semantic review can identify a redundant end frame. The continuity
    # contract, not string similarity, controls whether an end is required.
    similar = _structured_frame(
        "EDIT\nA character stands beside the doorway.", role="end_frame"
    )
    assert _codes(start, similar) == set()


@pytest.mark.parametrize("modify", [
    lambda p: p.replace('"media": "image"', '"media": broken'),
    lambda p: p.split("\n\nIMAGE PROMPT\n", 1)[0],
    lambda p: p.replace('"media": "image"', '"media": "video"'),
    lambda p: p.replace('"frame_role": "start_frame"', '"frame_role": "end_frame"'),
    lambda p: p.replace('"shot_staging": {', '"shot_staging": ['),
])
def test_invalid_envelopes_still_fail_closed(modify):
    prompt = modify(_structured_frame("EDIT\nA character is still."))
    assert "BOUNDARY_FRAME_SPEC_INVALID" in _codes(prompt)


@pytest.mark.parametrize("pose", [
    {"posture": "standing", "configuration": "not a list"},
    {"posture": 4, "configuration": []},
    {"posture": "standing", "configuration": ["valid", 5]},
    ["standing", "still"],
])
def test_malformed_pose_structure_is_still_rejected(pose):
    assert "BOUNDARY_FRAME_SPEC_INVALID" in _codes(
        _structured_frame("EDIT\nStill character.", pose=pose)
    )


def test_invalid_subjects_type_is_still_rejected():
    prompt = _structured_frame("EDIT\nA static frame.")
    prompt = prompt.replace('"subjects": [', '"subjects": "not an array", "ignore": [')
    assert "BOUNDARY_FRAME_SPEC_INVALID" in _codes(prompt)


def test_prose_only_packages_still_supported():
    assert _codes("Static character beside a doorway.") == set()
    assert _codes("The character lands and speaks.") == set()
