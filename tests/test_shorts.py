from forge_studios.contracts import AssetRecord, EpisodePackage, Scene, Shot
from forge_studios.shorts import find_short, resolve_short_media, vertical_filter


def _package():
    return EpisodePackage(
        production_id="ep1",
        episode_id="ep1",
        title="Episode 1",
        scenes=[
            Scene(
                scene_id="scene_1",
                shots=[
                    Shot(
                        shot_id="shot_1",
                        duration_seconds=3,
                        visual="Ember reacts",
                        approved_clip_asset_id="asset_1",
                    ),
                    Shot(
                        shot_id="shot_2",
                        duration_seconds=2,
                        visual="Smolder deadpans",
                        approved_clip_asset_id="asset_2",
                    ),
                ],
            )
        ],
        assets=[
            AssetRecord(asset_id="asset_1", kind="generated_video", uri="one.mp4"),
            AssetRecord(asset_id="asset_2", kind="generated_video", uri="two.mp4"),
        ],
        short_form_units=[
            {
                "short_id": "short_ep1_01",
                "title": "The reaction",
                "source_shot_ids": ["shot_1", "shot_2"],
                "hook": "Ember thinks he has it figured out.",
                "payoff": "Smolder does not agree.",
                "reframes": [
                    {"shot_id": "shot_1", "crop_anchor_x": 0.4},
                    {"shot_id": "shot_2", "crop_anchor_x": 0.6},
                ],
            }
        ],
    )


def test_resolve_short_uses_planned_order_and_reframes():
    package = _package()
    assert find_short(package, "short_ep1_01").title == "The reaction"
    timeline = resolve_short_media(package, "short_ep1_01")
    assert [item["shot_id"] for item in timeline] == ["shot_1", "shot_2"]
    assert timeline[0]["crop_anchor_x"] == 0.4
    assert timeline[1]["start_seconds"] == 3.0


def test_vertical_filter_scales_to_fill_then_crops():
    vf = vertical_filter(1080, 1920, 0.5, 0.5)
    assert "force_original_aspect_ratio=increase" in vf
    assert "crop=1080:1920" in vf
