import json

import pytest

from forge_studios.animator.service import AnimatorService
from forge_studios.contracts import AssetRecord, EpisodePackage, FramePlan, Scene, Shot
from forge_studios.package_ops import approve_asset
from forge_studios.providers.base import MediaResult


class RecordingProvider:
    name = 'recording'

    def __init__(self):
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        return [MediaResult(uri=f'/tmp/{request.shot_id}-{request.role}.png', provider=self.name, model='test')]


def test_structured_prompt_separates_identity_site_and_reusable_pose_roles():
    shot = Shot(
        shot_id='pose', duration_seconds=8, visual='Ember stands beside the altar.',
        continuity_asset_ids=['ember_canon', 'forge_canon', 'ember_pose'],
        storyboard_prompt='Ember standing beside the altar in a new grounded pose.',
    )
    package = EpisodePackage(
        production_id='roles', episode_id='roles', title='Roles',
        scenes=[Scene(scene_id='forge', shots=[shot])],
        assets=[
            AssetRecord(asset_id='ember_canon', entity_id='character_ember', kind='reference_image', uri='/tmp/ember.png', status='canon', authority='locked', role='canonical-reference', tags=['character']),
            AssetRecord(asset_id='forge_canon', entity_id='place_fire_forge', kind='reference_image', uri='/tmp/forge.png', status='canon', authority='locked', role='altar-view', tags=['place']),
            AssetRecord(asset_id='ember_pose', entity_id='character_ember', kind='reference_image', uri='/tmp/pose.png', status='approved', authority='generated', role='grounded-floor-pose', tags=['character', 'reusable']),
        ],
    )
    provider = RecordingProvider()
    asset = AnimatorService(provider).generate(package, 'pose', role='storyboard')[0]
    prompt = json.loads(provider.requests[0].prompt)
    roles = [item['production_role'] for item in prompt['reference_images']]
    assert roles == ['canonical_identity', 'canonical_site_geometry', 'reusable_character_pose']
    assert prompt['reference_images'][0]['entity_id'] == 'character_ember'
    assert asset.metadata['generation']['reference_inputs'][2]['production_role'] == 'reusable_character_pose'


def test_start_and_end_generation_give_storyboard_and_boundary_images_distinct_roles():
    shot = Shot(
        shot_id='jump', duration_seconds=8, visual='Ember moves from altar to floor.',
        render_strategy='generated_video', frame_plan=FramePlan(mode='start_and_end'),
        continuity_asset_ids=['ember'],
        start_frame_prompt='Ember settled at the altar edge.',
        end_frame_prompt='Ember grounded on the floor beside the altar.',
        storyboard_prompt='Ember grounded beside the altar.',
    )
    package = EpisodePackage(
        production_id='jump', episode_id='jump', title='Jump',
        scenes=[Scene(scene_id='forge', shots=[shot])],
        assets=[
            AssetRecord(asset_id='ember', entity_id='character_ember', kind='reference_image', uri='/tmp/ember.png', status='canon', authority='locked', tags=['character']),
            AssetRecord(asset_id='board', kind='storyboard_image', uri='/tmp/board.png', status='approved', shot_id='jump'),
        ],
    )
    shot.storyboard_asset_ids=['board']
    shot.approved_storyboard_asset_id='board'
    provider=RecordingProvider()
    start_asset=AnimatorService(provider).generate(package,'jump',role='start_frame')[0]
    start_prompt=json.loads(provider.requests[0].prompt)
    assert [item['production_role'] for item in start_prompt['reference_images']] == [
        'canonical_identity','storyboard_composition'
    ]

    start_asset.status='approved'
    shot.start_frame_asset_ids.append(start_asset.asset_id)
    shot.approved_start_frame_asset_id=start_asset.asset_id
    shot.frame_plan.start_asset_id=start_asset.asset_id
    end_asset=AnimatorService(provider).generate(package,'jump',role='end_frame')[0]
    end_prompt=json.loads(provider.requests[1].prompt)
    assert [item['production_role'] for item in end_prompt['reference_images']] == [
        'canonical_identity','approved_start_frame'
    ]
    assert end_asset.metadata['generation']['boundary_inputs'][0]['production_role'] == 'approved_start_frame'


def test_chained_video_boundary_metadata_marks_predecessor_endpoint():
    first=Shot(shot_id='first',duration_seconds=8,visual='First',render_strategy='generated_video',frame_plan=FramePlan(mode='start_and_end'))
    second=Shot(shot_id='second',duration_seconds=8,visual='Second',render_strategy='generated_video',frame_plan=FramePlan(mode='start_and_end',chain_from_shot_id='first'),video_prompt='Ember continues forward.')
    package=EpisodePackage(production_id='chain',episode_id='chain',title='Chain',scenes=[Scene(scene_id='s',shots=[first,second])])
    endpoint=AssetRecord(asset_id='first_end',kind='end_frame',uri='/tmp/first_end.png',status='approved',shot_id='first')
    second_end=AssetRecord(asset_id='second_end',kind='end_frame',uri='/tmp/second_end.png',status='approved',shot_id='second')
    package.assets.extend([endpoint,second_end])
    first.end_frame_asset_ids=['first_end']; first.approved_end_frame_asset_id='first_end'; first.frame_plan.end_asset_id='first_end'
    second.start_frame_asset_ids=['first_end']; second.approved_start_frame_asset_id='first_end'; second.frame_plan.start_asset_id='first_end'
    second.end_frame_asset_ids=['second_end']; second.approved_end_frame_asset_id='second_end'; second.frame_plan.end_asset_id='second_end'
    provider=RecordingProvider()
    asset=AnimatorService(provider).generate(package,'second',role='video')[0]
    boundaries=asset.metadata['generation']['boundary_inputs']
    assert boundaries == [
        {'asset_id':'first_end','production_role':'approved_predecessor_endpoint','provider_role':'video_start_frame'},
        {'asset_id':'second_end','production_role':'approved_end_frame','provider_role':'video_end_frame'},
    ]


def test_continuity_asset_ids_reject_storyboards_frames_and_unapproved_candidates_before_provider_call():
    provider=RecordingProvider()
    for bad_asset in (
        AssetRecord(asset_id='board',kind='storyboard_image',uri='/tmp/board.png',status='approved'),
        AssetRecord(asset_id='candidate',kind='reference_image',uri='/tmp/candidate.png',status='candidate'),
    ):
        package=EpisodePackage(
            production_id='bad',episode_id='bad',title='Bad',
            scenes=[Scene(scene_id='s',shots=[Shot(shot_id='bad',duration_seconds=1,visual='Bad',continuity_asset_ids=[bad_asset.asset_id],storyboard_prompt='Bad')])],
            assets=[bad_asset],
        )
        with pytest.raises(ValueError):
            AnimatorService(provider).generate(package,'bad',role='storyboard')
    assert provider.requests == []
