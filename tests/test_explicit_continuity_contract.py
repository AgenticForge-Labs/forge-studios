from forge_studios.contracts import EpisodePackage, Scene, Shot
from forge_studios.frame_plan import package_semantic_fingerprint, validate_frame_plans


def _shot(shot_id, *, transition_mode, transition_reason, chain=None, viewpoint="objective", duration=20):
    return Shot(
        shot_id=shot_id,
        duration_seconds=duration,
        visual=shot_id,
        execution_route="animator",
        render_strategy="generated_video",
        frame_plan={"mode": "start_and_end", "chain_from_shot_id": chain},
        edit_intent={
            "transition_mode": transition_mode,
            "transition_reason": transition_reason,
            "viewpoint": viewpoint,
        },
        start_frame_prompt=f"Settled start composition for {shot_id} with stable camera and subject pose.",
        end_frame_prompt=f"Settled end composition for {shot_id} with a visibly changed subject state.",
        video_prompt=f"Temporal change for {shot_id}.",
    )


def _new_package(scenes):
    package = EpisodePackage(
        production_id="p",
        episode_id="e",
        title="test",
        scenes=scenes,
    )
    package.trace["semantic_fingerprint_version"] = "v1"
    package.trace["semantic_fingerprint"] = package_semantic_fingerprint(package)
    return package


def test_adjacent_new_compositions_do_not_need_to_chain():
    package = _new_package([Scene(
        scene_id="scene",
        location_id="forge",
        shots=[
            _shot("a", transition_mode="new_composition", transition_reason="opening"),
            _shot("b", transition_mode="new_composition", transition_reason="camera_cut"),
        ],
    )])
    assert validate_frame_plans(package) == []


def test_explicit_inherit_requires_immediate_predecessor_chain():
    package = _new_package([Scene(
        scene_id="scene",
        location_id="forge",
        shots=[
            _shot("a", transition_mode="new_composition", transition_reason="opening"),
            _shot("b", transition_mode="inherit_endpoint", transition_reason="continuous_action"),
        ],
    )])
    codes = [issue.code for issue in validate_frame_plans(package)]
    assert "EXPLICIT_ENDPOINT_HANDOFF_REQUIRED" in codes


def test_explicit_inherit_allows_exact_immediate_handoff():
    package = _new_package([Scene(
        scene_id="scene",
        location_id="forge",
        shots=[
            _shot("a", transition_mode="new_composition", transition_reason="opening"),
            _shot("b", transition_mode="inherit_endpoint", transition_reason="continuous_action", chain="a"),
        ],
    )])
    assert validate_frame_plans(package) == []


def test_location_and_pov_changes_cannot_inherit():
    package = _new_package([
        Scene(
            scene_id="one",
            location_id="forge",
            shots=[_shot("a", transition_mode="new_composition", transition_reason="opening")],
        ),
        Scene(
            scene_id="two",
            location_id="clearing",
            shots=[_shot(
                "b",
                transition_mode="inherit_endpoint",
                transition_reason="continuous_action",
                chain="a",
                viewpoint="first_person",
            )],
        ),
    ])
    codes = {issue.code for issue in validate_frame_plans(package)}
    assert "LOCATION_CHANGE_CANNOT_INHERIT_ENDPOINT" in codes
    assert "POV_CHANGE_CANNOT_INHERIT_ENDPOINT" in codes


def test_new_contract_requires_explicit_continuity_mode():
    shot = Shot(
        shot_id="a",
        duration_seconds=20,
        visual="a",
        execution_route="animator",
        render_strategy="generated_video",
        frame_plan={"mode": "start_and_end"},
        start_frame_prompt="Settled start composition with a clear subject pose.",
        end_frame_prompt="Settled end composition with a clear changed subject pose.",
        video_prompt="change",
    )
    package = _new_package([Scene(scene_id="scene", location_id="forge", shots=[shot])])
    codes = [issue.code for issue in validate_frame_plans(package)]
    assert "CONTINUITY_MODE_REQUIRED" in codes


def test_generated_video_hard_cap_is_enforced_before_provider_spend():
    package = _new_package([Scene(
        scene_id="scene",
        location_id="forge",
        shots=[_shot(
            "a",
            transition_mode="new_composition",
            transition_reason="opening",
            duration=25,
        )],
    )])
    codes = [issue.code for issue in validate_frame_plans(package)]
    assert "GENERATED_VIDEO_DURATION_EXCEEDED" in codes


def test_semantic_edit_invalidates_stored_worlds_preflight_fingerprint():
    package = _new_package([Scene(
        scene_id="scene",
        location_id="forge",
        shots=[_shot("a", transition_mode="new_composition", transition_reason="opening")],
    )])
    assert validate_frame_plans(package) == []
    package.find_shot("a").end_frame_prompt = "Manually patched to a different ending composition."
    codes = [issue.code for issue in validate_frame_plans(package)]
    assert "STALE_DERIVED_PACKAGE_STATE" in codes
