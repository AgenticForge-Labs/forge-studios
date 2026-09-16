import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from forge_studios.animator import AnimatorService
from forge_studios.contracts import EpisodePackage, FramePlan, Scene, Shot
from forge_studios.director import AutonomyPolicy, DirectorService, plan_work
from forge_studios.package_ops import (
    add_reference,
    approve_asset,
    register_asset,
    set_prompt,
)
from forge_studios.providers import MockProvider
from forge_studios.providers.base import MediaRequest, MediaResult
from forge_studios.providers.fal import (
    FalProvider,
    image_model_profile,
    provider_safe_image_prompt,
)
from forge_studios.storyboard import build_storyboard, generate_storyboard_candidates


def package():
    return EpisodePackage(production_id='ep1',episode_id='ep1',title='Test',scenes=[Scene(scene_id='sc1',shots=[Shot(shot_id='sh1',duration_seconds=2,visual='A dragon wakes',render_strategy='generated_video',frame_plan=FramePlan(mode='start_and_end'))])])


def test_manual_progression(tmp_path):
    p=package(); svc=AnimatorService(MockProvider(tmp_path/'assets'))
    assets=svc.generate(p,'sh1',role='storyboard')
    assert plan_work(p)[0].action=='review_storyboard'
    approve_asset(p,'sh1','storyboard',assets[0].asset_id)
    assert plan_work(p)[0].action=='generate_start_frame'
    start=svc.generate(p,'sh1',role='start_frame')[0]
    assert plan_work(p)[0].action=='review_start_frame'
    approve_asset(p,'sh1','start_frame',start.asset_id)
    assert plan_work(p)[0].action=='generate_end_frame'
    end=svc.generate(p,'sh1',role='end_frame')[0]
    approve_asset(p,'sh1','end_frame',end.asset_id)
    assert plan_work(p)[0].action=='generate_video'
    assert build_storyboard(p,tmp_path/'storyboard.html').exists()


def test_director_stops_at_human_review_by_default(tmp_path):
    p=package(); svc=AnimatorService(MockProvider(tmp_path/'assets'))
    result=DirectorService(svc).run_until_blocked(p)
    assert result.status=='awaiting_review'
    assert result.next_work.action=='review_storyboard'
    assert len(p.find_shot('sh1').storyboard_asset_ids)==1


def test_batch_storyboard_generation_skips_existing_candidates(tmp_path):
    p=EpisodePackage(production_id='ep1',episode_id='ep1',title='Test',scenes=[Scene(scene_id='sc1',shots=[
        Shot(shot_id='sh1',duration_seconds=1,visual='first'),
        Shot(shot_id='sh2',duration_seconds=1,visual='second'),
    ])])
    svc=AnimatorService(MockProvider(tmp_path/'assets'))
    first=svc.generate(p,'sh1',role='storyboard')[0]
    generated=generate_storyboard_candidates(p,svc)
    assert [asset.shot_id for asset in generated]==['sh2']
    assert p.find_shot('sh1').storyboard_asset_ids==[first.asset_id]


def test_storyboard_generation_keeps_design_anchor_and_locked_constraints():
    class CaptureProvider:
        name='capture'
        def __init__(self): self.request=None
        def generate(self,request):
            self.request=request
            return [MediaResult(uri='asset://storyboard',provider=self.name,model='test-image')]

    p=package(); shot=p.find_shot('sh1')
    shot.storyboard_prompt='Settled view of Ember just beyond the open southern gap.'
    shot.image_prompt='Use the canonical dormant south-facing Fire Forge view. Keep the open gap and broad steps. No glow or fire.'
    shot.camera={
        'shot_type':'rear three-quarter follow view',
        'axis':'southward exit axis',
        'composition':'Ember, open gap, broad steps, and nearby path remain readable',
    }
    shot.visual_constraints={
        'must_show':['open gap between freestanding monoliths','broad steps','dirt path'],
        'must_not_show':['doorway','fireplace','fire'],
    }
    register_asset(p,asset_id='forge_south',uri='asset://forge-south',kind='reference_image',status='canon',authority='locked')
    add_reference(p,'sh1','forge_south')
    provider=CaptureProvider()

    AnimatorService(provider).generate(p,'sh1',role='storyboard')

    prompt=provider.request.prompt
    assert 'Visual design anchor from the EpisodePackage' in prompt
    assert 'canonical dormant south-facing Fire Forge view' in prompt
    import json
    compiled = json.loads(prompt)
    assert compiled['camera']['shot_type'] == 'rear three-quarter follow view'
    assert compiled['composition_constraints']['must_show'] == ['open gap between freestanding monoliths', 'broad steps', 'dirt path']
    assert compiled['composition_constraints']['must_not_show'] == ['doorway', 'fireplace', 'fire']
    assert '"production_role": "canonical_reference"' in prompt


def test_director_can_fully_auto_run_when_explicitly_allowed(tmp_path):
    p=package(); svc=AnimatorService(MockProvider(tmp_path/'assets'))
    policy=AutonomyPolicy(auto_approve_storyboards=True,auto_approve_frames=True,auto_approve_clips=True,allow_generated_video=True)
    result=DirectorService(svc).run_until_blocked(p,policy)
    assert result.status=='complete'
    shot=p.find_shot('sh1')
    assert shot.approved_storyboard_asset_id
    assert shot.approved_start_frame_asset_id
    assert shot.approved_end_frame_asset_id
    assert shot.approved_clip_asset_id


def test_manual_reference_and_prompt(tmp_path):
    p=package(); ref=tmp_path/'ember.png'; ref.write_bytes(b'not-a-real-png')
    register_asset(p,asset_id='character_ember',uri=str(ref),kind='reference_image',status='canon',authority='locked')
    add_reference(p,'sh1','character_ember')
    set_prompt(p,'sh1','start_frame','Ember sleeping on the altar')
    assert p.find_shot('sh1').continuity_asset_ids==['character_ember']
    assert p.find_shot('sh1').start_frame_prompt=='Ember sleeping on the altar'


def test_fal_provider_uses_stored_key_and_uploads_local_reference(tmp_path,monkeypatch):
    seen={}
    class FakeClient:
        def __init__(self,key=None): seen['key']=key
        def upload_file(self,path): seen.setdefault('uploads',[]).append(path); return 'https://uploaded/ref.png'
        def subscribe(self,model,arguments,with_logs=False,**kwargs):
            seen['model']=model; seen['arguments']=arguments
            seen['subscribe_kwargs']=kwargs
            return {'images':[{'url':'https://result/image.png'}]}
    monkeypatch.setitem(sys.modules,'fal_client',SimpleNamespace(SyncClient=FakeClient))
    def fake_download(uri,target):
        seen['download']=(uri,str(target)); target.write_bytes(b'downloaded')
    monkeypatch.setattr('forge_studios.providers.fal.urlretrieve',fake_download)
    ref=tmp_path/'ref.png'; ref.write_bytes(b'x')
    local=SimpleNamespace(resolve=lambda name:'stored-test-value')
    provider=FalProvider(image_edit_model='fal-ai/flux-2-pro/edit',output_dir=tmp_path/'generated',local_config=local)
    out=provider.generate(MediaRequest(kind='image',shot_id='s',prompt='test',reference_assets=(str(ref),)))
    assert seen['key']=='stored-test-value'
    assert seen['arguments']['image_urls']==['https://uploaded/ref.png']
    assert seen['arguments']['image_size']=={'width':1920,'height':1080}
    assert seen['arguments']['safety_tolerance']=='5'
    assert seen['arguments']['enable_safety_checker'] is True
    assert seen['subscribe_kwargs']['client_timeout']==300
    assert seen['subscribe_kwargs']['interval']==5
    assert Path(out[0].uri).read_bytes()==b'downloaded'
    assert seen['download'][0]=='https://result/image.png'
    assert out[0].metadata['remote_uri']=='https://result/image.png'


def test_fal_image_model_profiles_map_reference_shapes_and_limits():
    assert image_model_profile('fal-ai/flux-pro/kontext').reference_shape == 'single'
    assert FalProvider._image_reference_payload(
        'fal-ai/flux-pro/kontext', ['https://uploaded/ember.png']
    )[0] == {'image_url': 'https://uploaded/ember.png'}
    assert FalProvider._image_reference_payload(
        'fal-ai/flux-pro/kontext/max/multi', ['https://uploaded/ember.png', 'https://uploaded/forge.png']
    )[0] == {'image_urls': ['https://uploaded/ember.png', 'https://uploaded/forge.png']}


def test_fal_provider_safety_pass_removes_risky_resting_and_failure_vocabulary():
    prompt = (
        "Ember awake but still lying on the altar. Do not let video generation infer malformed anatomy or a bipedal shape."
    )
    safe = provider_safe_image_prompt(prompt)
    assert "lying" not in safe
    assert "malformed" not in safe
    assert "bipedal" not in safe
    assert "settled on the altar" in safe
    assert "four-legged silhouette" in safe
    assert FalProvider._image_reference_payload(
        'fal-ai/flux-2-pro/edit', ['https://uploaded/ember.png', 'https://uploaded/forge.png']
    )[0] == {'image_urls': ['https://uploaded/ember.png', 'https://uploaded/forge.png']}


def test_fal_image_model_profiles_reject_too_many_single_references():
    with pytest.raises(ValueError, match='at most 1'):
        FalProvider._image_reference_payload(
            'fal-ai/flux-pro/kontext', ['https://uploaded/ember.png', 'https://uploaded/forge.png']
        )
