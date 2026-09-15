import pytest

from forge_studios.animator import AnimatorService
from forge_studios.contracts import EpisodePackage, FramePlan, Scene, Shot
from forge_studios.frame_plan import FramePlanError, transient_boundary_markers, validate_frame_plans
from forge_studios.providers.base import MediaResult


class RecordingProvider:
    name='recording'

    def __init__(self):
        self.requests=[]

    def generate(self,request):
        self.requests.append(request)
        return [MediaResult(uri=f'/tmp/{request.shot_id}-{request.role}.png',provider=self.name,model='test')]


def package_with_boundaries(start_prompt: str, end_prompt: str) -> EpisodePackage:
    shot=Shot(
        shot_id='jump',duration_seconds=8,
        visual='Ember jumps from the raised altar and lands on the floor.',
        render_strategy='generated_video',frame_plan=FramePlan(mode='start_and_end'),
        start_frame_prompt=start_prompt,end_frame_prompt=end_prompt,
        video_prompt='Ember pushes off from the altar, arcs downward, and lands naturally on all four paws.',
    )
    return EpisodePackage(
        production_id='boundary',episode_id='boundary',title='Boundary',
        scenes=[Scene(scene_id='forge',shots=[shot])],
    )


def test_settled_before_and_after_states_are_valid_boundary_prompts():
    package=package_with_boundaries(
        'Ember crouched securely at the altar edge, all four paws supported, ready to jump.',
        'Ember standing securely on all four paws on the floor beside the altar after landing.',
    )
    assert validate_frame_plans(package)==[]
    assert transient_boundary_markers(package.find_shot('jump').start_frame_prompt)==[]
    assert transient_boundary_markers(package.find_shot('jump').end_frame_prompt)==[]


@pytest.mark.parametrize('prompt',[
    'Ember mid-jump between the altar and floor.',
    'Ember while falling toward the floor.',
    'Ember during the descent from the altar.',
    'Ember in the act of leaping off the altar.',
    'Ember halfway down toward the floor.',
    'Ember with motion blur as he descends.',
    'Ember jumps from the altar toward the ground.',
])
def test_transient_action_is_rejected_as_boundary_frame(prompt):
    package=package_with_boundaries(prompt,'Ember standing on all four paws after landing.')
    issues=validate_frame_plans(package)
    assert issues[0].code=='BOUNDARY_FRAME_TRANSIENT_ACTION'
    assert 'video_prompt' in issues[0].message


def test_duplicate_boundary_states_are_rejected_after_normalization():
    package=package_with_boundaries(
        'Ember standing beside the altar.',
        'EMBER standing beside the altar!',
    )
    issues=validate_frame_plans(package)
    assert [issue.code for issue in issues]==['BOUNDARY_FRAME_PROMPTS_DUPLICATE']


def test_animator_blocks_provider_spend_for_action_panel_boundary():
    package=package_with_boundaries(
        'Ember mid-jump between the altar and floor.',
        'Ember standing on all four paws after landing.',
    )
    provider=RecordingProvider()
    with pytest.raises(FramePlanError) as exc_info:
        AnimatorService(provider).generate(package,'jump',role='start_frame')
    assert exc_info.value.issue.code=='BOUNDARY_FRAME_TRANSIENT_ACTION'
    assert provider.requests==[]


def test_story_action_can_remain_dynamic_when_boundaries_are_settled():
    package=package_with_boundaries(
        'Ember crouched at the altar edge with all four paws supported.',
        'Ember grounded on all four paws beside the altar after landing.',
    )
    shot=package.find_shot('jump')
    assert 'jumps' in shot.visual
    assert 'arcs downward' in shot.video_prompt
    assert validate_frame_plans(package)==[]
