from forge_studios.contracts import AssetRecord, EpisodePackage, Scene, Shot
from forge_studios.package_ops import approve_asset, reject_asset


def base_package():
    p=EpisodePackage(production_id='p',episode_id='e',title='x',scenes=[Scene(scene_id='sc',shots=[Shot(shot_id='s',duration_seconds=1,visual='x')])])
    a=AssetRecord(asset_id='a1',kind='storyboard_image',uri='x.png',shot_id='s',attempt_id='try1',provider='mock',model='m')
    p.assets.append(a); p.find_shot('s').storyboard_asset_ids.append('a1')
    return p


def test_approval_records_review():
    p=base_package()
    approve_asset(p,'s','storyboard','a1',note='good scale',tags=['scale'],scores={'continuity':5})
    review=p.find_asset('a1').metadata['reviews'][-1]
    assert review['decision']=='approved'
    assert review['scores']['continuity']==5


def test_rejection_records_reason():
    p=base_package()
    reject_asset(p,'s','a1',reason='wrong anatomy',tags=['anatomy'])
    review=p.find_asset('a1').metadata['reviews'][-1]
    assert review['decision']=='rejected'
    assert review['note']=='wrong anatomy'
