from __future__ import annotations
from dataclasses import dataclass
from typing import Mapping

MLT_EFFECT_NAME='AgenticForge.MLTFilter'

@dataclass(frozen=True)
class EffectSpec:
    service: str
    properties: Mapping[str,object]|None=None
    name: str|None=None
    def validate(self)->None:
        if not self.service or not self.service.strip(): raise ValueError('MLT effect service must be non-empty')

def mlt_filter(service:str,*,name:str|None=None,**properties:object)->EffectSpec:
    spec=EffectSpec(service=service,properties=properties,name=name); spec.validate(); return spec

def brightness(level:float)->EffectSpec:
    if level<0: raise ValueError('brightness level cannot be negative')
    return mlt_filter('brightness',level=level,name='Brightness')

def volume(level:float|str)->EffectSpec: return mlt_filter('volume',level=level,name='Volume')

def affine(**properties:object)->EffectSpec:
    normalized={k if k.startswith('transition.') else f'transition.{k}':v for k,v in properties.items()}
    return EffectSpec(service='affine',properties=normalized,name='Transform')

def zoom_pan(*,zoom:str='min(zoom+0.0015,1.5)',x:str='iw/2-(iw/zoom/2)',y:str='ih/2-(ih/zoom/2)',duration_frames:int|None=None,size:str|None=None,fps:float|None=None)->EffectSpec:
    if duration_frames is not None and duration_frames<=0: raise ValueError('duration_frames must be positive')
    props:dict[str,object]={'z':zoom,'x':x,'y':y}
    if duration_frames is not None: props['d']=duration_frames
    if size is not None: props['s']=size
    if fps is not None: props['fps']=fps
    return EffectSpec(service='avfilter.zoompan',properties=props,name='Zoom/Pan')
