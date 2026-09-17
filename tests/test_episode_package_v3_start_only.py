from __future__ import annotations

from forge_studios.contracts import AssetRecord, EpisodePackage
from forge_studios.frame_plan import validate_frame_plans
from forge_studios.storyboard import generate_boundary_candidates


def package() -> EpisodePackage:
    return EpisodePackage.model_validate({
        "package_version": "episode_package_v3",
        "production_id": "test-v3",
        "episode_id": "e1",
        "world_id": "forge-born",
        "show_id": "forge-born",
        "title": "Awakening",
        "premise": "Ember wakes.",
        "arc": "Ember wakes and begins to investigate.",
        "target_duration_seconds": 15,
        "beats": [{
            "beat_id": "wake",
            "site_id": "place_forge",
            "duration_seconds": 15,
            "narrative": "Ember wakes and looks around.",
        }],
        "shots": [{
            "shot_id": "wake",
            "beat_id": "wake",
            "duration_seconds": 15,
            "site_id": "place_forge",
            "character_ids": ["character_ember"],
            "reference_asset_ids": ["ember", "forge"],
            "frame_plan_mode": "start_only",
            "start_frame_prompt": "Wide objective view of Ember resting on the raised stone altar in the open-air Forge at dawn, eyes closed.",
            "video_prompt": "Ember slowly opens his eyes, looks around, and sits upright as quiet forest ambience continues.",
        }],
        "assets": [
            {
                "asset_id": "ember",
                "entity_id": "character_ember",
                "kind": "reference_image",
                "uri": "asset://ember",
                "status": "canon",
                "authority": "locked",
            },
            {
                "asset_id": "forge",
                "entity_id": "place_forge",
                "kind": "reference_image",
                "uri": "asset://forge",
                "status": "canon",
                "authority": "locked",
            },
        ],
    })


def test_v3_start_only_compiles_without_end_frame():
    p = package()
    shot = p.shots[0]
    assert shot.frame_plan.mode == "start_only"
    assert shot.end_frame_prompt is None
    assert shot.edit_intent["transition_mode"] == "new_composition"
    assert validate_frame_plans(p) == []


def test_v2_shape_without_public_mode_infers_start_and_end():
    raw = package().model_dump(mode="json", exclude_none=True, exclude_defaults=True)
    raw["package_version"] = "episode_package_v2"
    shot = raw["shots"][0]
    shot.pop("frame_plan_mode", None)
    shot["end_frame_prompt"] = "Wide objective view of Ember sitting upright on the altar and looking toward the steps."

    parsed = EpisodePackage.model_validate(raw)
    assert parsed.shots[0].frame_plan.mode == "start_and_end"


class FakeAnimator:
    def __init__(self):
        self.roles: list[str] = []

    def generate(self, p: EpisodePackage, shot_id: str, *, role: str):
        self.roles.append(role)
        shot = p.find_shot(shot_id)
        asset = AssetRecord(
            asset_id=f"generated-{role}",
            kind=role,
            uri=f"asset://generated-{role}",
            status="candidate",
            authority="generated",
            shot_id=shot_id,
        )
        p.assets.append(asset)
        if role == "start_frame":
            shot.start_frame_asset_ids.append(asset.asset_id)
        elif role == "end_frame":
            shot.end_frame_asset_ids.append(asset.asset_id)
        return [asset]


def test_start_only_storyboard_generation_creates_only_start_frame():
    p = package()
    animator = FakeAnimator()
    generated = generate_boundary_candidates(p, animator)

    assert animator.roles == ["start_frame"]
    assert [asset.kind for asset in generated] == ["start_frame"]
    assert p.shots[0].start_frame_asset_ids == ["generated-start_frame"]
    assert p.shots[0].end_frame_asset_ids == []
