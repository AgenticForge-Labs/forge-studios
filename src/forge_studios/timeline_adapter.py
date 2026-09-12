from __future__ import annotations
from collections.abc import Mapping
from .contracts import EpisodePackage
from .effects import EffectSpec,mlt_filter,zoom_pan
from .timeline import TimelineClip,build_timeline


def _effects(intent:Mapping,*,duration:float,rate:float)->tuple[EffectSpec,...]:
    effects=[]; motion=str(intent.get('motion') or '').strip().lower()
    if motion in {'slow_push','push_in','zoom_in','ken_burns'}:
        effects.append(zoom_pan(duration_frames=max(1,round(duration*rate)),fps=rate))
    for item in intent.get('effects') or []:
        if not isinstance(item,Mapping): continue
        service=item.get('service')
        if not isinstance(service,str) or not service.strip(): raise ValueError('edit_intent effects require a non-empty MLT service')
        props=item.get('properties') or {}
        if not isinstance(props,Mapping): raise ValueError('edit_intent effect properties must be a mapping')
        effects.append(mlt_filter(service,name=item.get('name'),**dict(props)))
    return tuple(effects)


def _selected_asset_id(shot):
    return shot.final_clip_asset_id or shot.approved_clip_asset_id or shot.approved_take_id or shot.approved_storyboard_asset_id or shot.approved_start_frame_asset_id


def timeline_from_episode_package(package:EpisodePackage,*,rate:float=30.0,require_approved_media:bool=False):
    clips=[]
    for scene in package.scenes:
        for shot in scene.shots:
            asset_id=_selected_asset_id(shot); asset=package.find_asset(asset_id) if asset_id else None
            if require_approved_media and not asset: raise ValueError(f'shot {shot.shot_id!r} has no approved media')
            intent=shot.edit_intent if isinstance(shot.edit_intent,dict) else shot.edit_intent.model_dump(mode='json')
            metadata={
                'production_id':package.production_id,'episode_id':package.episode_id,'revision':package.revision,
                'scene_id':scene.scene_id,'shot_id':shot.shot_id,'asset_id':asset_id,'dialogue_ids':list(shot.dialogue_ids),
                'edit_intent':dict(intent),'transition_in':intent.get('transition_in'),'transition_out':intent.get('transition_out'),
                'hold_after_seconds':float(intent.get('hold_after_seconds') or 0),'music_cue':intent.get('music_cue'),
                'sound_effects':list(intent.get('sound_effects') or []),'caption_dialogue':bool(intent.get('caption_dialogue',True)),
            }
            clips.append(TimelineClip(name=shot.shot_id,duration_seconds=shot.duration_seconds,media_url=asset.uri if asset else None,metadata=metadata,effects=_effects(intent,duration=shot.duration_seconds,rate=rate)))
    return build_timeline(clips,name=package.title,rate=rate,metadata={'production_id':package.production_id,'episode_id':package.episode_id,'revision':package.revision})
