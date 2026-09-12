from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

@dataclass(frozen=True)
class Crop:
    x:float; y:float; width:float; height:float
    def validate(self):
        if not all(0.0<=v<=1.0 for v in (self.x,self.y,self.width,self.height)): raise ValueError('Crop values must be normalized to [0,1]')
        if self.width<=0 or self.height<=0: raise ValueError('Crop width and height must be positive')
        if self.x+self.width>1.0 or self.y+self.height>1.0: raise ValueError('Crop extends beyond source image')

def interpolate_crop(start:Crop,end:Crop,t:float)->Crop:
    start.validate(); end.validate(); t=min(1.0,max(0.0,t))
    return Crop(x=start.x+(end.x-start.x)*t,y=start.y+(end.y-start.y)*t,width=start.width+(end.width-start.width)*t,height=start.height+(end.height-start.height)*t)

def _cv():
    try:
        import cv2, numpy as np
    except ImportError as exc: raise RuntimeError('Install Forge Studios with [visual] support') from exc
    return cv2,np

def render_frames(image,*,start:Crop,end:Crop,duration_seconds:float,fps:int,output_size:tuple[int,int])->Iterator:
    cv2,_=_cv()
    if duration_seconds<=0 or fps<=0: raise ValueError('duration_seconds and fps must be positive')
    count=max(1,round(duration_seconds*fps)); h,w=image.shape[:2]; out_w,out_h=output_size
    for index in range(count):
        t=0.0 if count==1 else index/(count-1); crop=interpolate_crop(start,end,t)
        x0=round(crop.x*w); y0=round(crop.y*h); x1=round((crop.x+crop.width)*w); y1=round((crop.y+crop.height)*h)
        yield cv2.resize(image[y0:y1,x0:x1],(out_w,out_h),interpolation=cv2.INTER_LANCZOS4)

def pan_zoom_image(input_path:str|Path,output_path:str|Path,*,start:Crop,end:Crop,duration_seconds:float,fps:int=30,output_size:tuple[int,int]=(1920,1080),codec:str='mp4v')->Path:
    cv2,_=_cv(); image=cv2.imread(str(input_path),cv2.IMREAD_COLOR)
    if image is None: raise FileNotFoundError(f'Unable to read image: {input_path}')
    out=Path(output_path); out.parent.mkdir(parents=True,exist_ok=True); writer=cv2.VideoWriter(str(out),cv2.VideoWriter_fourcc(*codec),fps,output_size)
    if not writer.isOpened(): raise RuntimeError(f'Unable to open output video: {out}')
    try:
        for frame in render_frames(image,start=start,end=end,duration_seconds=duration_seconds,fps=fps,output_size=output_size): writer.write(frame)
    finally: writer.release()
    return out
