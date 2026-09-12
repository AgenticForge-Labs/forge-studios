from forge_studios.contracts import EpisodePackage, Scene, Shot, FramePlan
from forge_studios.animator import AnimatorService
from forge_studios.providers import MockProvider
from forge_studios.package_ops import approve_asset
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
