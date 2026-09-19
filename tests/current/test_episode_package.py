import json

from forge_studios.animator.service import _reference_input, structured_image_prompt
from forge_studios.contracts import AssetRecord, EpisodePackage, Shot
from forge_studios.frame_plan import validate_frame_plans
from forge_studios.io import save_json


def package():
    return EpisodePackage(
        package_version="episode_package",
        production_id="p",
        episode_id="e",
        world_id="w",
        show_id="s",
        title="Episode",
        arc="A small discovery.",
        target_duration_seconds=40,
        beats=[{"beat_id": "one"}, {"beat_id": "two"}],
        shots=[
            Shot(
                shot_id="one", beat_id="one", duration_seconds=20,
                site_id="place", reference_asset_ids=["ref"],
                start_frame_prompt="Ember rests on the altar with closed eyes in a wide objective view.",
                end_frame_prompt="Ember stands alert at the altar edge in a medium objective view.",
                video_prompt="Ember opens his eyes, rises, speaks in his established voice, and finishes standing at the edge.",
            ),
            Shot(
                shot_id="two", beat_id="two", duration_seconds=20,
                site_id="place", reference_asset_ids=["ref"],
                inherits_start_from_shot_id="one",
                start_frame_prompt="Ember stands alert at the altar edge in a medium objective view.",
                end_frame_prompt="Ember stands at the southern opening looking toward the path in a wide objective view.",
                video_prompt="Ember walks from the altar to the southern opening while the objective camera tracks beside him.",
            ),
        ],
        assets=[AssetRecord(asset_id="ref", entity_id="place", kind="reference_image", uri="/tmp/ref.png", status="canon")],
    )


def test_current_package_validates_without_scene_or_route_fields(tmp_path):
    value = package()
    assert validate_frame_plans(value) == []
    path = save_json(tmp_path / "package.json", value)
    text = path.read_text()
    assert '"scenes"' not in text
    assert '"execution_route"' not in text
    assert '"frame_plan"' not in text


def test_visual_constraints_are_part_of_the_public_handoff():
    value = package()
    value.shots[0].visual_constraints = {
        "must_show": ["raised circular altar"],
        "must_not_show": ["arch", "glowing rune grooves"],
    }

    payload = value.model_dump(mode="json", exclude_none=True)

    assert payload["package_version"] == "episode_package"
    assert payload["shots"][0]["visual_constraints"]["must_show"] == ["raised circular altar"]
    assert payload["shots"][0]["visual_constraints"]["must_not_show"] == [
        "arch",
        "glowing rune grooves",
    ]


def test_reference_uses_are_public_and_override_catalog_default_for_provider():
    value = package()
    value.shots[0].reference_uses = {
        "ref": (
            "Supporting site reference only: distant background landmark visible beyond "
            "the path. Do not use it as the primary scene composition."
        )
    }

    payload = value.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
    assert payload["shots"][0]["reference_uses"]["ref"].startswith(
        "Supporting site reference only"
    )

    reference = _reference_input(
        value,
        value.shots[0],
        "ref",
        start_id=None,
    )
    assert reference["use"].startswith("Supporting site reference only")
    assert "primary scene composition" in reference["use"]


def test_reference_uses_must_target_bound_reference_assets():
    import pytest

    value = package()
    with pytest.raises(ValueError, match="reference_uses names assets"):
        value.shots[0].__class__(
            shot_id="bad",
            beat_id="one",
            duration_seconds=20,
            site_id="place",
            reference_asset_ids=["ref"],
            reference_uses={"not_bound": "supporting landmark"},
            start_frame_prompt="A settled frame.",
            video_prompt="A short action.",
        )


def test_primary_site_reference_uses_preserve_mode_without_geometry_restatement():
    value = package()
    value.shots[0].reference_uses = {
        "ref": "Primary environment/base composition authority. Preserve reference pixels for fixed site geometry."
    }

    payload = json.loads(structured_image_prompt(
        value,
        value.shots[0],
        "start_frame",
        "REFERENCE AUTHORITY\nPreserve the supplied primary site image.\n\nSTART FRAME DELTA\nAdd one character on the existing surface.",
        ["ref"],
    ))

    assert payload["render_mode"] == "preserve_reference"
    assert payload["primary_environment_reference"] == "ref"
    assert "camera" not in payload
    assert "composition_constraints" not in payload
    assert payload["instruction"].startswith("REFERENCE AUTHORITY")
