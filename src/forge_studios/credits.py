from __future__ import annotations
from collections.abc import Sequence

def render_credit_card(lines:Sequence[str],*,size:tuple[int,int]=(1920,1080),background:int=0,foreground:int=255):
    try:
        import cv2, numpy as np
    except ImportError as exc: raise RuntimeError('Install Forge Studios with [visual] support') from exc
    width,height=size; frame=np.full((height,width,3),background,dtype=np.uint8); font=cv2.FONT_HERSHEY_SIMPLEX; scale=max(0.7,width/1800); thickness=max(1,round(scale*2)); gap=round(70*scale); total=max(1,len(lines))*gap; y=max(gap,(height-total)//2+gap); color=(foreground,foreground,foreground)
    for line in lines:
        (tw,_),_=cv2.getTextSize(line,font,scale,thickness); x=max(20,(width-tw)//2); cv2.putText(frame,line,(x,y),font,scale,color,thickness,cv2.LINE_AA); y+=gap
    return frame
