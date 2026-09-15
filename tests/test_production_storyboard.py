import json

import pytest

from forge_studios.animator import AnimatorService
from forge_studios.contracts import AssetRecord, EpisodePackage, FramePlan, Scene, Shot
from forge_studios.frame_plan import FramePlanError, validate_frame_plans
from forge_studios.providers.base import MediaResult
from forge_studios.storyboard import build_storyboard


class CaptureProvider:
    name = 'capture'

    def __init__(self):
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        return [MediaResult(uri=f'/tmp/{request.shot_id}-{request.role}.png', provider='capture', model='image-test')]


def _asset(asset_id, kind, uri, *, status='approved', shot_id=None, generation=None):
    metadata = {'generation': generation} if generation else {}
    return AssetRecord(asset_id=asset_id, kind=kind, uri=uri, status=status, shot_id=shot_id, metadata=metadata)


def test_adjacent_generated_video_requires_exact_endpoint_handoff_unless_cut_is_declared():
    package = EpisodePackage(
        production_id='p', episode_id='e', title='Continuity',
        scenes=[
            Scene(scene_id='s1', shots=[Shot(
                shot_id='jump', duration_seconds=8, visual='Ember jumps down.',
                render_strategy='generated_video', frame_plan=FramePlan(mode='start_and_end'),
            )]),
            Scene(scene_id='s2', shots=[Shot(
                shot_id='recover', duration_seconds=8, visual='Ember looks back from the ground.',
                render_strategy='generated_video', frame_plan=FramePlan(mode='start_and_end'),
            )]),
        ],
    )
    issues = validate_frame_plans(package)
    assert issues[0].code == 'CONTINUOUS_VIDEO_ENDPOINT_HANDOFF_REQUIRED'
    package.find_shot('recover').frame_plan.chain_from_shot_id = 'jump'
    assert validate_frame_plans(package) == []
    package.find_shot('recover').frame_plan.chain_from_shot_id = None
    package.find_shot('recover').edit_intent['editorial_boundary'] = 'hard cut to a new camera axis'
    assert validate_frame_plans(package) == []


def test_image_generation_request_is_structured_json_with_indexed_reference_roles():
    shot = Shot(
        shot_id='jump', duration_seconds=8, visual='Ember jumps from the altar and lands on the floor.',
        purpose='Readable first descent', render_strategy='generated_video',
        frame_plan=FramePlan(mode='start_and_end'),
        start_frame_prompt='Ember poised on the bowl altar edge.',
        end_frame_prompt='Ember grounded on all four paws beside the altar.',
        camera={'shot_type': 'side full-body', 'axis': 'altar side'},
        visual_constraints={'must_show': ['bowl altar', 'all four paws'], 'must_not_show': ['doorway']},
        continuity_asset_ids=['ember', 'forge'],
    )
    package = EpisodePackage(
        production_id='p', episode_id='e', title='Jump', scenes=[Scene(scene_id='s', shots=[shot])],
        assets=[
            _asset('ember', 'reference_image', '/tmp/ember.png', status='canon'),
            _asset('forge', 'reference_image', '/tmp/forge.png', status='canon'),
        ],
    )
    provider = CaptureProvider()
    AnimatorService(provider).generate(package, 'jump', role='start_frame')
    payload = json.loads(provider.requests[0].prompt)
    assert payload['task'] == 'generate one production image'
    assert payload['frame_role'] == 'start_frame'
    assert payload['camera']['shot_type'] == 'side full-body'
    assert payload['composition_constraints']['must_show'] == ['bowl altar', 'all four paws']
    assert payload['composition_constraints']['must_not_show'] == ['doorway']
    assert [item['image'] for item in payload['reference_images']] == ['Image 1', 'Image 2']
    assert [item['asset_id'] for item in payload['reference_images']] == ['ember', 'forge']
    assert 'poised on the bowl altar edge' in payload['instruction']


def test_animator_blocks_second_independent_start_before_spending_media_work():
    first = Shot(
        shot_id='jump', duration_seconds=8, visual='Jump.', render_strategy='generated_video',
        frame_plan=FramePlan(mode='start_and_end'),
    )
    second = Shot(
        shot_id='ground', duration_seconds=8, visual='Continue on ground.', render_strategy='generated_video',
        frame_plan=FramePlan(mode='start_and_end'),
    )
    package = EpisodePackage(production_id='p', episode_id='e', title='x', scenes=[Scene(scene_id='s', shots=[first, second])])
    provider = CaptureProvider()
    with pytest.raises(FramePlanError, match='approved end frame|must inherit'):
        AnimatorService(provider).generate(package, 'ground', role='start_frame')
    assert provider.requests == []


def test_production_storyboard_uses_boundary_frames_and_writes_image_guide(tmp_path):
    first = Shot(
        shot_id='jump', duration_seconds=8, visual='Ember jumps down.', purpose='First descent',
        render_strategy='generated_video', frame_plan=FramePlan(mode='start_and_end'),
        start_frame_prompt='On pedestal.', end_frame_prompt='On ground.', video_prompt='Jump from pedestal to ground.',
        start_frame_asset_ids=['jump_start'], approved_start_frame_asset_id='jump_start',
        end_frame_asset_ids=['jump_end'], approved_end_frame_asset_id='jump_end',
    )
    second = Shot(
        shot_id='look', duration_seconds=8, visual='Ember looks around from the ground.', purpose='Continue exploration',
        render_strategy='generated_video', frame_plan=FramePlan(mode='start_and_end', chain_from_shot_id='jump', start_asset_id='jump_end'),
        start_frame_prompt='Exact previous endpoint.', end_frame_prompt='Ember looking south.', video_prompt='Turn head toward the south exit.',
        start_frame_asset_ids=['jump_end'], approved_start_frame_asset_id='jump_end',
        end_frame_asset_ids=['look_end'], approved_end_frame_asset_id='look_end',
    )
    start_generation = {'prompt': '{"frame_role":"start_frame"}', 'reference_asset_ids': ['ember']}
    end_generation = {'prompt': '{"frame_role":"end_frame"}', 'reference_asset_ids': ['ember', 'jump_start']}
    package = EpisodePackage(
        production_id='p', episode_id='e', title='Boundary Comic', premise='A test.',
        scenes=[Scene(scene_id='s1', summary='Jump', shots=[first]), Scene(scene_id='s2', summary='Look', shots=[second])],
        assets=[
            _asset('ember', 'reference_image', '/tmp/ember.png', status='canon'),
            _asset('jump_start', 'start_frame', '/tmp/jump-start.png', shot_id='jump', generation=start_generation),
            _asset('jump_end', 'end_frame', '/tmp/jump-end.png', shot_id='jump', generation=end_generation),
            _asset('look_end', 'end_frame', '/tmp/look-end.png', shot_id='look', generation={'prompt': '{"frame_role":"end_frame"}', 'reference_asset_ids': ['ember', 'jump_end']}),
        ],
    )
    out = build_storyboard(package, tmp_path / 'storyboard.html')
    board = out.read_text()
    guide = (tmp_path / 'storyboard.image-guide.html').read_text()
    assert '/tmp/jump-start.png' in board
    assert board.count('/tmp/jump-end.png') >= 2  # first endpoint and inherited second start
    assert 'Jump from pedestal to ground.' in board
    assert 'inherited from jump end frame' in board
    assert 'exact boundary images supplied to the model' in board
    assert 'Input images, in provider order' in guide
    assert '/tmp/ember.png' in guide
    assert '{&quot;frame_role&quot;:&quot;end_frame&quot;}' in guide
