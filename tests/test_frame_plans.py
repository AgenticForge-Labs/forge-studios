import pytest

from forge_studios.animator import AnimatorService
from forge_studios.contracts import AssetRecord, EpisodePackage, FramePlan, Scene, Shot
from forge_studios.director import plan_work
from forge_studios.frame_plan import FramePlanError, validate_frame_plans
from forge_studios.package_ops import approve_asset
from forge_studios.providers.base import MediaRequest, MediaResult


class RecordingProvider:
    name = 'recording'

    def __init__(self):
        self.requests = []

    def generate(self, request: MediaRequest):
        self.requests.append(request)
        return [MediaResult(uri=f'/tmp/{request.shot_id}-{request.role}.png', provider=self.name, model='test')]


def chained_package():
    return EpisodePackage(
        production_id='p', episode_id='e', title='Chain',
        scenes=[Scene(scene_id='scene', shots=[
            Shot(shot_id='first', duration_seconds=2, visual='First', render_strategy='still'),
            Shot(shot_id='second', duration_seconds=2, visual='Second', render_strategy='generated_video', frame_plan=FramePlan(mode='chained_start', chain_from_shot_id='first')),
        ])],
    )


def add_approved(package, *, asset_id, shot_id, kind):
    asset = AssetRecord(asset_id=asset_id, kind=f'{kind}_frame', uri=f'/tmp/{asset_id}.png', shot_id=shot_id, status='candidate')
    package.assets.append(asset)
    shot = package.find_shot(shot_id)
    collection = {
        'storyboard': 'storyboard_asset_ids',
        'start': 'start_frame_asset_ids',
        'end': 'end_frame_asset_ids',
    }[kind]
    getattr(shot, collection).append(asset_id)
    approve_asset(package, shot_id, {'start': 'start_frame', 'end': 'end_frame'}.get(kind, kind), asset_id)


def test_chained_successor_waits_for_predecessor_end_frame_and_exposes_dependency():
    package = chained_package()
    add_approved(package, asset_id='first_board', shot_id='first', kind='storyboard')
    add_approved(package, asset_id='second_board', shot_id='second', kind='storyboard')

    work = plan_work(package)
    assert work[0].shot_id == 'first'
    assert work[0].action == 'generate_end_frame'
    assert 'chained successor' in work[0].reason

    endpoint = AssetRecord(asset_id='first_end', kind='end_frame', uri='/tmp/first-end.png', shot_id='first')
    package.assets.append(endpoint)
    package.find_shot('first').end_frame_asset_ids.append(endpoint.asset_id)
    work = plan_work(package)
    assert work[0].action == 'review_end_frame'
    approve_asset(package, 'first', 'end_frame', endpoint.asset_id)

    work = plan_work(package)
    assert work[0].shot_id == 'second'
    assert work[0].action == 'generate_video'
    assert work[0].predecessor_shot_id == 'first'
    assert work[0].required_asset_id == 'first_end'
    assert work[0].depends_on == ('approved_end_frame:first:first_end',)


def test_chained_generation_uses_exact_predecessor_endpoint_not_storyboard():
    package = chained_package()
    add_approved(package, asset_id='first_board', shot_id='first', kind='storyboard')
    add_approved(package, asset_id='first_end', shot_id='first', kind='end')
    provider = RecordingProvider()
    AnimatorService(provider).generate(package, 'second', role='video')
    assert provider.requests[0].start_frame_asset == '/tmp/first_end.png'


def test_chained_generation_never_falls_back_to_storyboard():
    package = chained_package()
    add_approved(package, asset_id='first_board', shot_id='first', kind='storyboard')
    with pytest.raises(FramePlanError, match='approved end-frame') as exc_info:
        AnimatorService(RecordingProvider()).generate(package, 'second', role='video')
    assert exc_info.value.issue.code == 'CHAINED_START_ENDPOINT_MISSING'


@pytest.mark.parametrize('chain_from', [None, 'unknown', 'second'])
def test_invalid_chained_references_are_rejected(chain_from):
    package = chained_package()
    package.find_shot('second').frame_plan.chain_from_shot_id = chain_from
    issues = validate_frame_plans(package)
    assert issues
    assert issues[0].code in {
        'CHAINED_START_MISSING_PREDECESSOR',
        'CHAINED_START_UNKNOWN_PREDECESSOR',
        'CHAINED_START_PREDECESSOR_NOT_EARLIER',
    }


def test_chained_explicit_start_asset_must_match_predecessor_endpoint():
    package = chained_package()
    add_approved(package, asset_id='first_end', shot_id='first', kind='end')
    package.find_shot('second').frame_plan.start_asset_id = 'wrong_start'
    issues = validate_frame_plans(package)
    assert issues[0].code == 'CHAINED_START_ASSET_MISMATCH'


def test_non_chained_start_only_and_start_and_end_keep_their_explicit_inputs():
    package = EpisodePackage(production_id='p', episode_id='e', title='Frames', scenes=[Scene(scene_id='scene', shots=[
        Shot(shot_id='start', duration_seconds=1, visual='Start', render_strategy='generated_video', frame_plan=FramePlan(mode='start_only')),
        Shot(shot_id='both', duration_seconds=1, visual='Both', render_strategy='generated_video', frame_plan=FramePlan(mode='start_and_end')),
    ])])
    add_approved(package, asset_id='start_asset', shot_id='start', kind='start')
    add_approved(package, asset_id='both_start', shot_id='both', kind='start')
    add_approved(package, asset_id='both_end', shot_id='both', kind='end')
    provider = RecordingProvider()
    service = AnimatorService(provider)
    service.generate(package, 'start', role='video')
    service.generate(package, 'both', role='video')
    assert provider.requests[0].start_frame_asset == '/tmp/start_asset.png'
    assert provider.requests[0].end_frame_asset is None
    assert provider.requests[1].start_frame_asset == '/tmp/both_start.png'
    assert provider.requests[1].end_frame_asset == '/tmp/both_end.png'
