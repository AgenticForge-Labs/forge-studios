from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .contracts import EpisodePackage
from .telemetry import TelemetrySink


_VIDEO_FILTER = (
    'scale=1280:720:force_original_aspect_ratio=decrease,'
    'pad=1280:720:(ow-iw)/2:(oh-ih)/2,format=yuv420p'
)
_SILENT_AUDIO = 'anullsrc=channel_layout=stereo:sample_rate=48000'
_IMAGE_KINDS = {'storyboard_image', 'start_frame', 'end_frame', 'generated_image', 'image'}


def resolve_final_media(package: EpisodePackage) -> list[dict]:
    timeline=[]; t=0.0
    for shot in package.shots:
        chosen=shot.final_clip_asset_id or shot.approved_clip_asset_id or shot.approved_start_frame_asset_id
        if not chosen: raise ValueError(f'shot {shot.shot_id} has no approved media')
        asset=package.find_asset(chosen)
        timeline.append({'shot_id':shot.shot_id,'asset_id':chosen,'uri':asset.uri,'kind':asset.kind,'start_seconds':t,'duration_seconds':shot.duration_seconds,'edit_intent':shot.edit_intent})
        t += shot.duration_seconds
    return timeline


def write_edit_plan(package: EpisodePackage, path: str|Path) -> Path:
    out=Path(path); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps({'production_id':package.production_id,'timeline':resolve_final_media(package)},indent=2)+'\n'); return out


def _ffprobe_for(ffmpeg: str) -> str:
    path=Path(ffmpeg)
    return str(path.with_name('ffprobe')) if path.name != 'ffprobe' else str(path)


def _has_audio(path: Path, *, ffprobe: str) -> bool:
    """Return whether a media file contains at least one audio stream."""
    result=subprocess.run(
        [
            ffprobe,'-v','error','-select_streams','a:0',
            '-show_entries','stream=index','-of','csv=p=0',str(path),
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def _segment_command(
    *,
    ffmpeg: str,
    src: Path,
    seg: Path,
    duration: str,
    kind: str,
    has_audio: bool,
) -> list[str]:
    """Normalize every segment to H.264 + stereo AAC while preserving authored audio."""
    common=[
        '-t',duration,
        '-vf',_VIDEO_FILTER,
        '-r','30',
        '-c:v','libx264',
        '-c:a','aac',
        '-ar','48000',
        '-ac','2',
    ]
    if kind in _IMAGE_KINDS:
        return [
            ffmpeg,'-y','-loop','1','-i',str(src),
            '-f','lavfi','-i',_SILENT_AUDIO,
            '-map','0:v:0','-map','1:a:0',
            *common,'-shortest',str(seg),
        ]
    if has_audio:
        return [
            ffmpeg,'-y','-i',str(src),
            '-map','0:v:0','-map','0:a:0',
            *common,str(seg),
        ]
    return [
        ffmpeg,'-y','-i',str(src),
        '-f','lavfi','-i',_SILENT_AUDIO,
        '-map','0:v:0','-map','1:a:0',
        *common,'-shortest',str(seg),
    ]


def render(package: EpisodePackage, output: str|Path, *, ffmpeg: str='ffmpeg', telemetry: TelemetrySink|None=None) -> Path:
    """Render the approved episode without discarding generated dialogue or sound effects.

    Generated video audio is kept and normalized to stereo AAC. Still-image fallbacks
    and rare silent clips receive a silent stereo track so every concat segment has a
    compatible audio stream.
    """
    timeline=resolve_final_media(package); output=Path(output); work=output.parent/(output.stem+'-segments'); work.mkdir(parents=True,exist_ok=True)
    segments=[]; ffprobe=_ffprobe_for(ffmpeg)
    for i,item in enumerate(timeline):
        uri=item['uri']
        if uri.startswith('http://') or uri.startswith('https://'): raise ValueError('Filmmaker render requires local media paths; download remote assets first')
        src=Path(uri); seg=work/f'{i:04d}-{item["shot_id"]}.mp4'; dur=str(item['duration_seconds'])
        has_audio=False if item['kind'] in _IMAGE_KINDS else _has_audio(src,ffprobe=ffprobe)
        cmd=_segment_command(ffmpeg=ffmpeg,src=src,seg=seg,duration=dur,kind=item['kind'],has_audio=has_audio)
        subprocess.run(cmd,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); segments.append(seg)
    concat=work/'concat.txt'; concat.write_text(''.join(f"file '{p.resolve()}'\n" for p in segments))
    subprocess.run([ffmpeg,'-y','-f','concat','-safe','0','-i',str(concat),'-c','copy',str(output)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    (telemetry or TelemetrySink()).emit('final_render.completed',production_id=package.production_id,episode_id=package.episode_id,uri=str(output.resolve()),duration_seconds=sum(x['duration_seconds'] for x in timeline))
    return output
