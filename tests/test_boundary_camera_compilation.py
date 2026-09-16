import json

from forge_studios.animator.service import boundary_camera, structured_image_prompt
from forge_studios.contracts import EpisodePackage, Scene, Shot
from forge_studios.frame_plan import validate_frame_plans


def test_compiler_uses_distinct_authored_static_camera_without_temporal_leakage():
    shot = Shot(shot_id="move", duration_seconds=15, visual="Movement",
                camera={"viewpoint": "objective", "movement": "track the jump",
                        "composition": "during jump",
                        "start_frame": {"composition": "wide view, feet on ledge", "distance": "wide"},
                        "end_frame": {"composition": "close view, feet on floor", "distance": "close"}})
    package = EpisodePackage(production_id="p", episode_id="e", title="Test")
    start = json.loads(structured_image_prompt(package, shot, "start_frame", "On ledge.", []))
    end = json.loads(structured_image_prompt(package, shot, "end_frame", "On floor.", []))
    assert start["camera"]["distance"] == "wide"
    assert end["camera"]["distance"] == "close"
    assert "during jump" not in json.dumps(start)
    assert "track the jump" not in json.dumps(end)
    assert boundary_camera(shot, "video")["movement"] == "track the jump"


def test_legacy_boundary_prompt_is_authoritative_without_invented_camera():
    shot = Shot(shot_id="move", duration_seconds=15, visual="Jump",
                camera={"composition": "tracking jump", "movement": "pan", "axis": "north"})
    assert boundary_camera(shot, "end_frame") == {"axis": "north"}
    assert shot.camera["movement"] == "pan"  # compilation does not mutate the shot


def test_malformed_or_temporal_boundary_camera_is_rejected_before_spend():
    shot = Shot(shot_id="s", duration_seconds=15, visual="View",
                camera={"start_frame": "tracking", "end_frame": {"movement": "pan"}})
    package = EpisodePackage(production_id="p", episode_id="e", title="Test", scenes=[Scene(scene_id="sc", shots=[shot])])
    assert {f.code for f in validate_frame_plans(package)} >= {"INVALID_BOUNDARY_CAMERA", "TEMPORAL_BOUNDARY_CAMERA"}
