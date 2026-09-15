import json

import pytest

from forge_studios.animator.service import AnimatorService, failure_recovery_plan, reference_isolation_plan
from forge_studios.contracts import AssetRecord, EpisodePackage, Scene, Shot
from forge_studios.providers.base import ProviderGenerationError
from forge_studios.providers.fal import _classify_fal_failure
from forge_studios.telemetry import TelemetrySink


class RejectingProvider:
    name = 'fal'
    image_model = 'fal-ai/flux-2-pro/edit'
    video_model = 'fal-ai/ltx-2.3/image-to-video/fast'

    def generate(self, request):
        raise ProviderGenerationError(
            'content_policy_violation',
            provider='fal',
            model=self.image_model,
            request_id='req_test_123',
            diagnostics={
                'failure_class': 'content_policy',
                'provider_settings': {
                    'safety_tolerance': '5',
                    'enable_safety_checker': True,
                    'reference_count': len(request.reference_assets),
                },
            },
        )


def diagnostic_package():
    shot = Shot(
        shot_id='jump',
        duration_seconds=8,
        visual='Ember prepares to move from the altar to the floor.',
        continuity_asset_ids=['ember_ref', 'forge_ref'],
        storyboard_prompt='Ember settled at the altar edge in the Fire Forge.',
    )
    return EpisodePackage(
        production_id='diag',
        episode_id='diag',
        title='Diagnostics',
        scenes=[Scene(scene_id='forge', shots=[shot])],
        assets=[
            AssetRecord(asset_id='ember_ref', kind='reference_image', uri='/tmp/ember.png', status='canon'),
            AssetRecord(asset_id='forge_ref', kind='reference_image', uri='/tmp/forge.png', status='canon'),
        ],
    )


def test_reference_isolation_is_single_then_pairwise_in_stable_order():
    plan = reference_isolation_plan(['ember', 'forge', 'ember'])
    assert plan['original_reference_asset_ids'] == ['ember', 'forge']
    assert plan['steps'] == [
        {'phase': 'single_reference', 'reference_asset_ids': ['ember']},
        {'phase': 'single_reference', 'reference_asset_ids': ['forge']},
        {'phase': 'pairwise_reference', 'reference_asset_ids': ['ember', 'forge']},
    ]
    assert plan['next_isolation_step'] == plan['steps'][0]


def test_transport_failure_retries_same_request_before_changing_references():
    recovery = failure_recovery_plan('transport', ['ember', 'forge'])
    assert recovery['action'] == 'retry_same_request'
    assert recovery['reference_asset_ids'] == ['ember', 'forge']


def test_content_policy_failure_records_complete_reproducible_diagnostics(tmp_path):
    telemetry_path = tmp_path / 'telemetry.jsonl'
    package = diagnostic_package()
    with pytest.raises(ProviderGenerationError):
        AnimatorService(RejectingProvider(), TelemetrySink(telemetry_path)).generate(package, 'jump', role='storyboard')

    events = [json.loads(line) for line in telemetry_path.read_text().splitlines()]
    failed = next(event for event in events if event['event_type'] == 'generation_attempt.failed')
    assert failed['shot_id'] == 'jump'
    assert failed['role'] == 'storyboard'
    assert failed['provider'] == 'fal'
    assert failed['model'] == 'fal-ai/flux-2-pro/edit'
    assert failed['prompt']
    assert failed['reference_asset_ids'] == ['ember_ref', 'forge_ref']
    diagnostics = failed['metadata']['failure_diagnostics']
    assert diagnostics['request_id'] == 'req_test_123'
    assert diagnostics['provider_settings']['safety_tolerance'] == '5'
    assert diagnostics['provider_options'] == {}
    assert diagnostics['recovery']['action'] == 'isolate_references'
    assert diagnostics['next_isolation_step'] == {
        'phase': 'single_reference',
        'reference_asset_ids': ['ember_ref'],
    }

    rejected = next(event for event in events if event['event_type'] == 'generation_prompt.rejected')
    assert rejected['request_id'] == 'req_test_123'
    assert rejected['reference_asset_ids'] == ['ember_ref', 'forge_ref']
    assert rejected['next_isolation_step']['reference_asset_ids'] == ['ember_ref']


def test_fal_failure_classification_separates_policy_timeout_and_transport():
    assert _classify_fal_failure(RuntimeError('content_policy_violation')) == 'content_policy'
    assert _classify_fal_failure(TimeoutError('timed out')) == 'timeout'
    assert _classify_fal_failure(RuntimeError('RemoteProtocolError: peer disconnected')) == 'transport'
