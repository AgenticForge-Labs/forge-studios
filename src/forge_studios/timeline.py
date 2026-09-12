from __future__ import annotations
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable,Mapping
from .effects import EffectSpec,MLT_EFFECT_NAME


def _otio():
    try: import opentimelineio as otio
    except ImportError as exc: raise RuntimeError('Install Forge Studios with [timeline] support') from exc
    return otio

@dataclass(frozen=True)
class TimelineClip:
    name:str; duration_seconds:float; media_url:str|None=None; source_start_seconds:float=0.0
    metadata:Mapping[str,object]|None=None; effects:tuple[EffectSpec,...]=()
    def validate(self):
        if self.duration_seconds<=0: raise ValueError('duration_seconds must be positive')
        if self.source_start_seconds<0: raise ValueError('source_start_seconds cannot be negative')
        for e in self.effects: e.validate()

def build_timeline(clips:Iterable[TimelineClip],*,name:str='AgenticForge Edit',rate:float=30.0,metadata:Mapping[str,object]|None=None):
    otio=_otio()
    if rate<=0: raise ValueError('rate must be positive')
    timeline=otio.schema.Timeline(name=name,metadata=dict(metadata or {})); track=otio.schema.Track(name='V1',kind=otio.schema.TrackKind.Video); timeline.tracks.append(track)
    for spec in clips:
        spec.validate(); duration=otio.opentime.RationalTime(spec.duration_seconds*rate,rate); start=otio.opentime.RationalTime(spec.source_start_seconds*rate,rate); rng=otio.opentime.TimeRange(start,duration)
        ref=otio.schema.ExternalReference(target_url=spec.media_url,available_range=rng) if spec.media_url else otio.schema.MissingReference(available_range=rng)
        clip=otio.schema.Clip(name=spec.name,media_reference=ref,source_range=rng,metadata=dict(spec.metadata or {}))
        for effect in spec.effects:
            clip.effects.append(otio.schema.Effect(name=effect.name or effect.service,effect_name=MLT_EFFECT_NAME,metadata={'service':effect.service,'properties':dict(effect.properties or {})}))
        track.append(clip)
    return timeline

def _walk(node:object)->Iterator:
    otio=_otio()
    if isinstance(node,otio.schema.Clip): yield node; return
    try: children=iter(node)
    except TypeError: return
    for child in children: yield from _walk(child)

def iter_clips(timeline)->Iterator: yield from _walk(timeline.tracks)

def bind_media(timeline,assets_by_shot_id:Mapping[str,str]):
    otio=_otio(); resolved=otio.adapters.read_from_string(otio.adapters.write_to_string(timeline,adapter_name='otio_json'),adapter_name='otio_json')
    for clip in iter_clips(resolved):
        shot_id=clip.metadata.get('shot_id')
        if isinstance(shot_id,str) and assets_by_shot_id.get(shot_id):
            clip.media_reference=otio.schema.ExternalReference(target_url=assets_by_shot_id[shot_id],available_range=clip.source_range,metadata={'resolved_from_shot_id':shot_id})
    return resolved

def save_timeline(timeline,path:str|Path)->Path:
    otio=_otio(); out=Path(path); out.parent.mkdir(parents=True,exist_ok=True); otio.adapters.write_to_file(timeline,str(out)); return out

def load_timeline(path:str|Path):
    otio=_otio(); timeline=otio.adapters.read_from_file(str(path))
    if not isinstance(timeline,otio.schema.Timeline): raise TypeError(f'Expected OTIO Timeline, got {type(timeline).__name__}')
    return timeline
