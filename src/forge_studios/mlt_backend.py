from __future__ import annotations
from pathlib import Path
from types import ModuleType
from typing import Any,Mapping
from .effects import MLT_EFFECT_NAME
from .timeline import _otio

class MLTUnavailableError(RuntimeError): pass
class UnsupportedTimelineError(ValueError): pass

def _load_mlt()->ModuleType:
    try: import mlt7 as mlt; return mlt
    except ImportError:
        try: import mlt; return mlt
        except ImportError as exc: raise MLTUnavailableError('MLT Python bindings are not installed (normally mlt7)') from exc

def _init_mlt(mlt): mlt.Factory().init(None)

def _frames(time,fps:float)->int: return max(0,round(time.to_seconds()*fps))

def _set_filter_properties(filter_:Any,properties:Mapping[str,object])->None:
    for key,value in properties.items():
        if not isinstance(key,str): raise UnsupportedTimelineError('MLT filter property names must be strings')
        if not isinstance(value,(str,int,float,bool)): raise UnsupportedTimelineError(f'Unsupported MLT property value for {key!r}: {type(value).__name__}')
        filter_.set(key,value)

def _attach_effects(producer,clip,profile,mlt):
    for effect in clip.effects:
        if effect.effect_name!=MLT_EFFECT_NAME: raise UnsupportedTimelineError(f'Unsupported OTIO effect {effect.effect_name!r} on {clip.name!r}')
        service=effect.metadata.get('service'); properties=effect.metadata.get('properties',{})
        if not isinstance(service,str) or not service: raise UnsupportedTimelineError('MLT effect missing service')
        if not isinstance(properties,Mapping): raise UnsupportedTimelineError('MLT effect properties must be a mapping')
        f=mlt.Filter(profile,service)
        if hasattr(f,'is_valid') and not f.is_valid(): raise UnsupportedTimelineError(f'MLT filter service {service!r} is unavailable')
        _set_filter_properties(f,properties); producer.attach(f)

def build_mlt_tractor(timeline,*,profile_name:str='atsc_1080p_30',mlt_module:ModuleType|None=None):
    otio=_otio(); mlt=mlt_module or _load_mlt(); _init_mlt(mlt); profile=mlt.Profile(profile_name); tractor=mlt.Tractor(profile); multitrack=tractor.multitrack(); track_index=0
    for track in timeline.tracks:
        if not isinstance(track,otio.schema.Track) or track.kind!=otio.schema.TrackKind.Video: continue
        playlist=mlt.Playlist(profile)
        for item in track:
            if isinstance(item,otio.schema.Gap):
                duration=_frames(item.source_range.duration,profile.fps())
                if duration: playlist.blank(duration-1)
                continue
            if not isinstance(item,otio.schema.Clip): raise UnsupportedTimelineError(f'Unsupported OTIO item {type(item).__name__}')
            if item.source_range is None: raise UnsupportedTimelineError(f'Clip {item.name!r} needs source_range')
            ref=item.media_reference
            if not isinstance(ref,otio.schema.ExternalReference) or not ref.target_url: raise UnsupportedTimelineError(f'Clip {item.name!r} is not bound to external media')
            producer=mlt.Producer(profile,ref.target_url)
            if not producer.is_valid(): raise FileNotFoundError(ref.target_url)
            _attach_effects(producer,item,profile,mlt)
            start=_frames(item.source_range.start_time,profile.fps()); duration=_frames(item.source_range.duration,profile.fps())
            if duration<=0: raise UnsupportedTimelineError(f'Clip {item.name!r} has zero rendered duration')
            playlist.append(producer,start,start+duration-1)
        multitrack.connect(playlist,track_index); track_index+=1
    if track_index==0: raise UnsupportedTimelineError('Timeline has no video tracks')
    return tractor

def render_timeline(timeline,output_path:str|Path,*,profile_name:str='atsc_1080p_30',consumer_service:str='avformat',video_codec:str='libx264',audio_codec:str='aac',mlt_module:ModuleType|None=None)->Path:
    mlt=mlt_module or _load_mlt(); tractor=build_mlt_tractor(timeline,profile_name=profile_name,mlt_module=mlt); out=Path(output_path); out.parent.mkdir(parents=True,exist_ok=True); profile=mlt.Profile(profile_name); consumer=mlt.Consumer(profile,consumer_service,str(out)); consumer.set('vcodec',video_codec); consumer.set('acodec',audio_codec); consumer.connect(tractor); consumer.run(); return out
