import sys
from types import SimpleNamespace

from forge_studios.contracts import EpisodePackage, Scene, Shot, FramePlan
from forge_studios.animator import AnimatorService
from forge_studios.providers import MockProvider
from forge_studios.providers.fal import FalProvider
from forge_studios.providers.base import MediaRequest
from forge_studios.package_ops import approve_asset, add_reference, register_asset, set_prompt
from forge_studios.director import plan_work
from forge_studios.storyboard import build_storyboard


def package():
    return EpisodePackage(production_id='ep1',episode_id='ep1',title='Test',scenes=[Scene(scene_id='sc1',shots=[Shot(shot_id='sh1',duration_seconds=2,visual='A dragon wakes',render_strategy='generated_video',frame_plan=FramePlan(mode='start_and_end'))])])


def test_manual_progression(tmp_path):
    p=package(); svc=AnimatorService(MockProvider(tmp_path/'assets'))
    assets=svc.generate(p,'sh1',role='storyboard'); approve_asset(p,'sh1','storyboard',assets[0].asset_id)
    work=plan_work(p); assert [w.action for w in work][:2]==['generate_start_frame','generate_end_frame']
    start=svc.generate(p,'sh1',role='start_frame')[0]; approve_asset(p,'sh1','start_frame',start.asset_id)
    end=svc.generate(p,'sh1',role='end_frame')[0]; approve_asset(p,'sh1','end_frame',end.asset_id)
    assert any(w.action=='generate_video' for w in plan_work(p))
    assert build_storyboard(p,tmp_path/'storyboard.html').exists()


def test_manual_reference_and_prompt(tmp_path):
    p=package(); ref=tmp_path/'ember.png'; ref.write_bytes(b'not-a-real-png')
    register_asset(p,asset_id='character_ember',uri=str(ref),kind='reference_image',status='canon',authority='locked')
    add_reference(p,'sh1','character_ember')
    set_prompt(p,'sh1','image','Ember sleeping on the altar')
    assert p.find_shot('sh1').continuity_asset_ids==['character_ember']
    assert p.find_shot('sh1').image_prompt=='Ember sleeping on the altar'


def test_fal_provider_uses_stored_key_and_uploads_local_reference(tmp_path,monkeypatch):
    seen={}
    class FakeClient:
        def __init__(self,key=None): seen['key']=key
        def upload_file(self,path): seen.setdefault('uploads',[]).append(path); return 'https://uploaded/ref.png'
        def subscribe(self,model,arguments,with_logs=False):
            seen['model']=model; seen['arguments']=arguments
            return {'images':[{'url':'https://result/image.png'}]}
    monkeypatch.setitem(sys.modules,'fal_client',SimpleNamespace(SyncClient=FakeClient))
    ref=tmp_path/'ref.png'; ref.write_bytes(b'x')
    local=SimpleNamespace(resolve=lambda name:'stored-test-value')
    provider=FalProvider(image_model='image-model',local_config=local)
    out=provider.generate(MediaRequest(kind='image',shot_id='s',prompt='test',reference_assets=(str(ref),)))
    assert seen['key']=='stored-test-value'
    assert seen['arguments']['image_urls']==['https://uploaded/ref.png']
    assert out[0].uri=='https://result/image.png'
