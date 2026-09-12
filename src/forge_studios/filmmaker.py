from __future__ import annotations
import json, subprocess
from pathlib import Path
from .contracts import EpisodePackage
from .telemetry import TelemetrySink

def resolve_final_media(package: EpisodePackage) -> list[dict]:
    timeline=[]; t=0.0
    for scene in package.scenes:
        for shot in scene.shots:
            chosen=shot.final_clip_asset_id or shot.approved_clip_asset_id or shot.approved_take_id or shot.approved_storyboard_asset_id or shot.approved_start_frame_asset_id
            if not chosen: raise ValueError(f'shot {shot.shot_id} has no approved media')
            asset=package.find_asset(chosen)
            timeline.append({'shot_id':shot.shot_id,'asset_id':chosen,'uri':asset.uri,'kind':asset.kind,'start_seconds':t,'duration_seconds':shot.duration_seconds,'edit_intent':shot.edit_intent})
            t += shot.duration_seconds
    return timeline

def write_edit_plan(package: EpisodePackage, path: str|Path) -> Path:
    out=Path(path); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps({'production_id':package.production_id,'timeline':resolve_final_media(package)},indent=2)+'\n'); return out

def render(package: EpisodePackage, output: str|Path, *, ffmpeg: str='ffmpeg', telemetry: TelemetrySink|None=None) -> Path:
    timeline=resolve_final_media(package); output=Path(output); work=output.parent/(output.stem+'-segments'); work.mkdir(parents=True,exist_ok=True)
    segments=[]
    for i,item in enumerate(timeline):
        uri=item['uri']
        if uri.startswith('http://') or uri.startswith('https://'): raise ValueError('Filmmaker render requires local media paths; download remote assets first')
        src=Path(uri); seg=work/f'{i:04d}-{item["shot_id"]}.mp4'; dur=str(item['duration_seconds'])
        if item['kind'] in {'storyboard_image','start_frame','end_frame','generated_image','image'}:
            cmd=[ffmpeg,'-y','-loop','1','-i',str(src),'-t',dur,'-vf','scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,format=yuv420p','-r','30','-an',str(seg)]
        else:
            cmd=[ffmpeg,'-y','-i',str(src),'-t',dur,'-vf','scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,format=yuv420p','-r','30','-an',str(seg)]
        subprocess.run(cmd,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); segments.append(seg)
    concat=work/'concat.txt'; concat.write_text(''.join(f"file '{p.resolve()}'\n" for p in segments))
    subprocess.run([ffmpeg,'-y','-f','concat','-safe','0','-i',str(concat),'-c','copy',str(output)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    (telemetry or TelemetrySink()).emit('final_render.completed',production_id=package.production_id,episode_id=package.episode_id,uri=str(output.resolve()),duration_seconds=sum(x['duration_seconds'] for x in timeline))
    return output
