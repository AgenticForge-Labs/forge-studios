import pytest

from forge_studios.animator import AnimatorService
from forge_studios.cli import main
from forge_studios.contracts import AssetRecord, EpisodePackage, FramePlan, Scene, Shot
from forge_studios.director import DirectorService, plan_work
from forge_studios.frame_plan import FramePlanError, validate_frame_plans
from forge_studios.io import load_package, save_json
from forge_studios.package_ops import approve_asset, bind_inherited_start_frame
from forge_studios.providers.base import MediaRequest, MediaResult


class RecordingProvider:
    name = 'recording'

    def __init__(self):
        self.requests = []

    def generate(self, request: MediaRequest):
        self.requests.append(request)
        return [MediaResult(uri=f'/tmp/{request.role}.png', provider=self.name, model='test')]


class ContentPolicyProvider:
    name = 'content-policy'

    def generate(self, request: MediaRequest):
        raise RuntimeError('content_policy_violation')


def package_with_approved_board_and_start():
    shot = Shot(
        shot_id='wake', duration_seconds=15, visual='Ember wakes on the altar.',
        render_strategy='generated_video', frame_plan=FramePlan(mode='start_and_end'),
        continuity_asset_ids=['ember', 'forge'],
        start_frame_prompt='Ember eyes closed on the same raised altar.',
        end_frame_prompt='Ember eyes open on the same raised altar.',
        approved_storyboard_asset_id='board', storyboard_asset_ids=['board'],
    )
    package = EpisodePackage(
        production_id='wake-test', episode_id='wake-test', title='Wake',
        scenes=[Scene(scene_id='altar', shots=[shot])],
        assets=[
            AssetRecord(asset_id='ember', kind='reference_image', uri='/tmp/ember.png', status='canon'),
            AssetRecord(asset_id='forge', kind='reference_image', uri='/tmp/forge.png', status='canon'),
            AssetRecord(asset_id='board', kind='storyboard_image', uri='/tmp/board.png', status='approved'),
        ],
    )
    return package


def test_studio_approved_package_asset_remains_approved_after_contract_round_trip(tmp_path):
    package = package_with_approved_board_and_start()
    reloaded = load_package(save_json(tmp_path / 'approved.package.json', package))
    assert reloaded.find_asset('board').status == 'approved'
    assert reloaded.find_asset('board').status != 'canon'


def test_start_frame_uses_approved_storyboard_after_canonical_references():
    package = package_with_approved_board_and_start()
    provider = RecordingProvider()
    asset = AnimatorService(provider).generate(package, 'wake', role='start_frame')[0]
    request = provider.requests[0]
    assert request.reference_assets == ('/tmp/ember.png', '/tmp/forge.png', '/tmp/board.png')
    assert 'Reference image 3 is this shot\'s approved storyboard' in request.prompt
    assert asset.metadata['generation']['reference_asset_ids'] == ['ember', 'forge', 'board']


def test_storyboard_fallback_reposes_identity_reference_to_the_settled_shot_result():
    shot = Shot(
        shot_id='jump', duration_seconds=8, visual='Ember jumps from the altar and lands on four paws.',
        render_strategy='generated_video', frame_plan=FramePlan(mode='start_and_end'),
        image_prompt='Ember sits at the altar edge.',
        end_frame_prompt='Ember grounded on four paws beside the raised altar after landing.',
        continuity_asset_ids=['ember'], camera={'framing': 'full-body side view', 'axis': 'altar side'},
        performance_intent={'blocking': ['Starts at the altar edge', 'lands on all four paws beside the altar']},
    )
    package = EpisodePackage(
        production_id='jump', episode_id='jump', title='Jump',
        scenes=[Scene(scene_id='altar', shots=[shot])],
        assets=[AssetRecord(asset_id='ember', kind='reference_image', uri='/tmp/ember.png', status='canon')],
    )
    provider = RecordingProvider()
    AnimatorService(provider).generate(package, 'jump', role='storyboard')
    request = provider.requests[0]
    assert 'grounded on four paws beside the raised altar after landing' in request.prompt
    assert 'Ember sits at the altar edge' not in request.prompt
    assert 'not their default pose or camera angle' in request.prompt
    assert 'full-body side view' in request.prompt
    assert request.reference_assets == ('/tmp/ember.png',)


def test_end_frame_uses_exact_approved_start_as_a_real_image_reference():
    package = package_with_approved_board_and_start()
    package.assets.append(AssetRecord(asset_id='wake_start', kind='start_frame', uri='/tmp/wake_start.png', status='approved'))
    package.find_shot('wake').approved_start_frame_asset_id = 'wake_start'
    provider = RecordingProvider()
    asset = AnimatorService(provider).generate(package, 'wake', role='end_frame')[0]
    request = provider.requests[0]
    assert request.reference_assets == ('/tmp/ember.png', '/tmp/forge.png', '/tmp/wake_start.png')
    assert request.start_frame_asset == '/tmp/wake_start.png'
    import json
    reference = json.loads(request.prompt)['reference_images'][2]
    assert reference['image'] == 'Image 3'
    assert reference['production_role'] == 'approved_start_frame'
    assert reference['asset_id'] == 'wake_start'
    assert asset.source_asset_ids == ['ember', 'forge', 'wake_start']


def test_end_frame_rejects_missing_approved_start_instead_of_independent_generation():
    package = package_with_approved_board_and_start()
    provider = RecordingProvider()
    with pytest.raises(ValueError, match='approved start frame'):
        AnimatorService(provider).generate(package, 'wake', role='end_frame')
    assert provider.requests == []


@pytest.mark.parametrize('action', ['storyboard', 'generate', 'auto'])
def test_cli_refuses_media_work_for_a_blocked_worlds_package(tmp_path, action):
    package = package_with_approved_board_and_start()
    package.status = 'blocked'
    package.trace = {'production_preflight': {'is_valid': False}}
    package_path = save_json(tmp_path / 'blocked.package.json', package)
    args = {
        'storyboard': ['storyboard', '--package', str(package_path), '--out', str(tmp_path / 'board.html'), '--generate'],
        'generate': ['generate', '--package', str(package_path), '--shot-id', 'wake'],
        'auto': ['auto', '--package', str(package_path)],
    }[action]
    with pytest.raises(ValueError, match='hard production-preflight errors'):
        main(args)


def test_direct_animator_and_director_refuse_known_blocked_package():
    package = package_with_approved_board_and_start()
    package.status = 'blocked'
    package.trace = {'production_preflight': {'is_valid': False}}
    provider = RecordingProvider()
    animator = AnimatorService(provider)
    with pytest.raises(ValueError, match='hard production-preflight errors'):
        animator.generate(package, 'wake', role='start_frame')
    result = DirectorService(animator).run_until_blocked(package)
    assert result.status == 'blocked'
    assert provider.requests == []


def test_content_policy_rejection_is_deterministically_reported_without_a_prompt_rewriter():
    package = package_with_approved_board_and_start()
    with pytest.raises(RuntimeError, match='content_policy_violation') as error:
        AnimatorService(ContentPolicyProvider()).generate(package, 'wake', role='storyboard')
    assert 'Forge Studios does not use an LLM to rewrite prompts' in str(error.value.__notes__)


def continuous_split_package():
    package = EpisodePackage(
        production_id='split', episode_id='split', title='Technical split',
        scenes=[Scene(scene_id='altar', shots=[
            Shot(shot_id='first', duration_seconds=18, visual='Ember wakes.',
                 render_strategy='generated_video', frame_plan=FramePlan(mode='start_and_end')),
            Shot(shot_id='second', duration_seconds=17, visual='Ember continues.',
                 render_strategy='generated_video', frame_plan=FramePlan(mode='start_and_end', chain_from_shot_id='first'),
                 start_frame_prompt='Exact previous endpoint.', end_frame_prompt='Ember at the altar edge.'),
        ])],
    )
    for shot_id in ('first', 'second'):
        package.assets.append(AssetRecord(asset_id=f'{shot_id}_board', kind='storyboard_image',
                                          uri=f'/tmp/{shot_id}_board.png', shot_id=shot_id))
        package.find_shot(shot_id).storyboard_asset_ids.append(f'{shot_id}_board')
        approve_asset(package, shot_id, 'storyboard', f'{shot_id}_board')
    return package


def test_technical_split_waits_then_reuses_exact_endpoint_for_frames_and_video():
    package = continuous_split_package()
    first = package.find_shot('first')
    package.assets.append(AssetRecord(asset_id='first_start', kind='start_frame',
                                      uri='/tmp/first_start.png', shot_id='first'))
    first.start_frame_asset_ids.append('first_start')
    approve_asset(package, 'first', 'start_frame', 'first_start')
    assert plan_work(package)[0].action == 'generate_end_frame'
    assert any(item.shot_id == 'second' and item.action == 'blocked' for item in plan_work(package))

    package.assets.append(AssetRecord(asset_id='first_end', kind='end_frame',
                                      uri='/tmp/first_end.png', shot_id='first'))
    first.end_frame_asset_ids.append('first_end')
    approve_asset(package, 'first', 'end_frame', 'first_end')
    second = package.find_shot('second')
    assert second.approved_start_frame_asset_id == 'first_end'
    assert second.frame_plan.start_asset_id == 'first_end'
    assert second.start_frame_asset_ids == ['first_end']
    assert plan_work(package)[-1].action == 'generate_end_frame'

    provider = RecordingProvider()
    AnimatorService(provider).generate(package, 'second', role='end_frame')
    assert provider.requests[0].reference_assets == ('/tmp/first_end.png',)
    package.assets.append(AssetRecord(asset_id='second_end', kind='end_frame',
                                      uri='/tmp/second_end.png', shot_id='second'))
    second.end_frame_asset_ids.append('second_end')
    approve_asset(package, 'second', 'end_frame', 'second_end')
    AnimatorService(provider).generate(package, 'second', role='video')
    assert provider.requests[1].start_frame_asset == '/tmp/first_end.png'
    assert provider.requests[1].end_frame_asset == '/tmp/second_end.png'


def test_preapproved_endpoint_can_be_bound_without_generating_another_start(tmp_path):
    package = continuous_split_package()
    package.assets.append(AssetRecord(asset_id='first_end', kind='end_frame', uri='/tmp/first_end.png'))
    package.find_shot('first').approved_end_frame_asset_id = 'first_end'
    package.find_shot('first').frame_plan.end_asset_id = 'first_end'
    work = next(item for item in plan_work(package) if item.shot_id == 'second')
    assert work.action == 'bind_inherited_start_frame'
    assert work.required_asset_id == 'first_end'
    assert work.depends_on == ('approved_end_frame:first:first_end',)
    with pytest.raises(FramePlanError, match='bind predecessor'):
        AnimatorService(RecordingProvider()).generate(package, 'second', role='end_frame')
    assert bind_inherited_start_frame(package, 'second') == 'first_end'
    path = save_json(tmp_path / 'split.package.json', package)
    assert main(['inherit-start', '--package', str(path), '--shot-id', 'second']) == 0


def test_start_and_end_chain_rejects_nonadjacent_or_conflicting_predecessor():
    package = continuous_split_package()
    package.scenes[0].shots.insert(1, Shot(shot_id='middle', duration_seconds=2, visual='Other action'))
    issues = validate_frame_plans(package)
    assert issues[0].code == 'START_AND_END_NONADJACENT_PREDECESSOR'
    assert next(item for item in plan_work(package) if item.shot_id == 'second').action == 'blocked'
    package.scenes[0].shots.pop(1)
    package.assets.append(AssetRecord(asset_id='first_end', kind='end_frame', uri='/tmp/first_end.png'))
    package.find_shot('first').approved_end_frame_asset_id = 'first_end'
    package.find_shot('second').frame_plan.start_asset_id = 'wrong'
    issues = validate_frame_plans(package)
    assert issues[0].code == 'CHAINED_START_ASSET_MISMATCH'
