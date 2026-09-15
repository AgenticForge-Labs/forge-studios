import pytest

from forge_studios.cli import _run_with_failure_checkpoint
from forge_studios.contracts import EpisodePackage, Scene, Shot
from forge_studios.io import load_package, save_json


def package():
    return EpisodePackage(
        production_id='checkpoint',
        episode_id='checkpoint',
        title='Checkpoint',
        scenes=[Scene(scene_id='s', shots=[Shot(shot_id='sh1', duration_seconds=1, visual='test')])],
    )


def test_failure_checkpoint_persists_in_memory_attempt_state_before_reraising(tmp_path):
    path = tmp_path / 'episode.json'
    p = package()
    save_json(path, p)

    def fail_after_mutation():
        p.trace['generation_attempts'] = [{
            'attempt_id': 'attempt_failed',
            'shot_id': 'sh1',
            'role': 'storyboard',
            'outcome': 'failed',
        }]
        p.trace['last_generation_failure'] = p.trace['generation_attempts'][0]
        raise RuntimeError('provider failed')

    with pytest.raises(RuntimeError, match='provider failed'):
        _run_with_failure_checkpoint(path, p, fail_after_mutation)

    reloaded = load_package(path)
    assert reloaded.trace['generation_attempts'][0]['attempt_id'] == 'attempt_failed'
    assert reloaded.trace['last_generation_failure']['outcome'] == 'failed'


def test_successful_operation_does_not_checkpoint_until_caller_decides(tmp_path):
    path = tmp_path / 'episode.json'
    p = package()
    save_json(path, p)

    def succeed_after_mutation():
        p.trace['temporary'] = 'in-memory'
        return 42

    assert _run_with_failure_checkpoint(path, p, succeed_after_mutation) == 42
    assert 'temporary' not in load_package(path).trace
