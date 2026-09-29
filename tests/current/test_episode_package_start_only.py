from __future__ import annotations

from forge_studios.contracts import AssetRecord, EpisodePackage
from forge_studios.frame_plan import validate_frame_plans
from forge_studios.storyboard import generate_boundary_candidates


def package() -> EpisodePackage:
    return EpisodePackage.model_validate({
        "package_version": "episode_package",
        "production_id": "test-current",
        "episode_id": "e1",
        "world_id": "forge-born",
        "show_id": "forge-born",
        "title": "Awakening",
        "premise": "Ember wakes.",
        "arc": "Ember wakes and begins to investigate.",
        "target_duration_seconds": 15,
        "shots": [{
            "shot_id": "wake",
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


def test_start_only_compiles_without_end_frame():
    p = package()
    shot = p.shots[0]
    assert shot.frame_plan.mode == "start_only"
    assert shot.end_frame_prompt is None
    assert shot.edit_intent["transition_mode"] == "new_composition"
    assert validate_frame_plans(p) == []


def test_package_without_public_mode_infers_start_and_end():
    raw = package().model_dump(mode="json", exclude_none=True, exclude_defaults=True)
    raw["package_version"] = "episode_package"
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


def inherited_start_only_package() -> EpisodePackage:
    raw = {
        "package_version": "episode_package",
        "production_id": "test-handoff",
        "episode_id": "e2",
        "world_id": "forge-born",
        "show_id": "forge-born",
        "title": "Continuous action",
        "premise": "A reveal flows directly into Ember's next action.",
        "arc": "The camera reveals scale, then Ember jumps.",
        "target_duration_seconds": 30,
        "shots": [
            {
                "shot_id": "reveal",
                "duration_seconds": 15,
                "site_id": "place_forge",
                "frame_plan_mode": "start_and_end",
                "start_frame_prompt": "Tight settled view of Ember at the altar edge.",
                "end_frame_prompt": "Wide settled view revealing the Forge around Ember at the altar edge.",
                "video_prompt": "The camera pulls back to reveal the scale of the Forge.",
            },
            {
                "shot_id": "jump",
                "duration_seconds": 15,
                "site_id": "place_forge",
                "frame_plan_mode": "start_only",
                "inherits_start_from_shot_id": "reveal",
                "start_frame_prompt": "Wide settled view revealing the Forge around Ember at the altar edge.",
                "video_prompt": "Ember jumps down from the altar and lands below.",
                "cinematic_choices": [
                    {
                        "vocabulary_id": "cinematic_boundary_from_previous",
                        "term_id": "exact_continuity",
                        "purpose": "Begin from the exact reveal endpoint.",
                    }
                ],
            },
        ],
        "assets": [],
    }
    return EpisodePackage.model_validate(raw)


def test_start_only_may_inherit_exact_predecessor_endpoint():
    p = inherited_start_only_package()
    successor = p.shots[1]

    assert successor.frame_plan.mode == "start_only"
    assert successor.frame_plan.chain_from_shot_id == "reveal"
    assert successor.end_frame_prompt is None
    assert successor.cinematic_choices[0].term_id == "exact_continuity"
    assert validate_frame_plans(p) == []


def test_inherited_start_only_generates_no_duplicate_start_or_end():
    p = inherited_start_only_package()
    animator = FakeAnimator()
    generated = generate_boundary_candidates(p, animator)

    assert animator.roles == ["start_frame", "end_frame"]
    assert [asset.kind for asset in generated] == ["start_frame", "end_frame"]
    assert p.shots[1].start_frame_asset_ids[-1] == p.shots[0].end_frame_asset_ids[-1]
    assert p.shots[1].end_frame_asset_ids == []
