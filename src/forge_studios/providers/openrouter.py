from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import MediaRequest, MediaResult


class OpenRouterImageProvider:
    """Image provider for any runtime implementing `generate_images`; no private repo dependency."""
    name='openrouter'
    def __init__(self,runtime:Any,*,output_dir:str|Path='outputs/generated',model:str|None=None,alias:str='image'):
        self.runtime=runtime; self.output_dir=Path(output_dir); self.model=model; self.alias=alias
    def generate(self,request:MediaRequest)->list[MediaResult]:
        if request.kind!='image': raise ValueError('OpenRouterImageProvider handles image generation only')
        references=list(request.reference_assets)
        if request.start_frame_asset: references.append(request.start_frame_asset)
        options=dict(request.options); n=int(options.pop('n',1)); size=options.pop('size',None); aspect_ratio=options.pop('aspect_ratio',None); background=options.pop('background',None); output_format=options.pop('output_format',None)
        generated=self.runtime.generate_images(role='animator.image',alias=self.alias,prompt=request.prompt,output_dir=self.output_dir,filename_prefix=request.shot_id,model=self.model,n=n,references=references,size=size,aspect_ratio=aspect_ratio,background=background,output_format=output_format,options=options,metadata={'consumer':'forge-studios','shot_id':request.shot_id,'role':request.role})
        out=[]
        for asset in generated:
            path=getattr(asset,'path',None); uri=Path(path).expanduser().resolve().as_uri() if path else str(asset.uri)
            out.append(MediaResult(uri=uri,provider=self.name,model=getattr(asset,'model',self.model),metadata={'sha256':getattr(asset,'sha256',None),'media_type':getattr(asset,'media_type',None),'prompt':getattr(asset,'prompt',request.prompt),**dict(getattr(asset,'metadata',{}) or {})}))
        return out
