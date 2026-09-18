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
        assets=[AssetRecord(asset_id="ref", kind="reference_image", uri="/tmp/ref.png", status="canon")],
    )


def test_v2_package_validates_without_scene_or_route_fields(tmp_path):
    value = package()
    assert validate_frame_plans(value) == []
    path = save_json(tmp_path / "package.json", value)
    text = path.read_text()
    assert '"scenes"' not in text
    assert '"execution_route"' not in text
    assert '"frame_plan"' not in text
