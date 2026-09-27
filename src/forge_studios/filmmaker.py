from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .contracts import EpisodePackage
from .telemetry import TelemetrySink


_VIDEO_FILTER = (
    'scale=1280:720:force_original_aspect_ratio=decrease,'
    'pad=1280:720:(ow-iw)/2:(oh-ih)/2,format=yuv420p'
)
_SILENT_AUDIO = 'anullsrc=channel_layout=stereo:sample_rate=48000'
_IMAGE_KINDS = {'storyboard_image', 'start_frame', 'end_frame', 'generated_image', 'image'}


@dataclass(frozen=True)
class EndCardRenderSpec:
    main_logo: Path
    secondary_logo: Path
    duration_seconds: float = 3.0
    fade_seconds: float = 0.35
    main_width_fraction: float = 0.58
    secondary_width_fraction: float = 0.16
    margin_fraction: float = 0.04
    zoom_start: float = 0.96
    zoom_end: float = 1.06
    background: str = 'black'

    def validate(self) -> None:
        if self.duration_seconds <= 0:
            raise ValueError('end-card duration_seconds must be positive')
        if self.fade_seconds < 0 or self.fade_seconds * 2 > self.duration_seconds:
            raise ValueError('end-card fade_seconds must be non-negative and no more than half the duration')
        for name, value in (
            ('main_width_fraction', self.main_width_fraction),
            ('secondary_width_fraction', self.secondary_width_fraction),
            ('margin_fraction', self.margin_fraction),
        ):
            if value <= 0 or value >= 1:
                raise ValueError(f'end-card {name} must be between 0 and 1')
        if self.zoom_start <= 0 or self.zoom_end <= 0:
            raise ValueError('end-card zoom values must be positive')
        for logo in (self.main_logo, self.secondary_logo):
            if not Path(logo).is_file():
                raise FileNotFoundError(logo)


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
    fade_out_seconds: float = 0.0,
) -> list[str]:
    """Normalize a segment while preserving authored audio and optional final fade."""
    duration_seconds=float(duration)
    if fade_out_seconds < 0 or fade_out_seconds > duration_seconds:
        raise ValueError('fade_out_seconds must be between zero and the segment duration')
    video_filter=_VIDEO_FILTER
    audio_filter: str|None=None
    if fade_out_seconds:
        start=max(0.0,duration_seconds-fade_out_seconds)
        video_filter += f',fade=t=out:st={start:g}:d={fade_out_seconds:g}'
        audio_filter = f'afade=t=out:st={start:g}:d={fade_out_seconds:g}'
    common=[
        '-t',duration,
        '-vf',video_filter,
        '-r','30',
        '-c:v','libx264',
        '-c:a','aac',
        '-ar','48000',
        '-ac','2',
    ]
    if audio_filter:
        common.extend(['-af',audio_filter])
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


def _end_card_command(
    *,
    ffmpeg: str,
    spec: EndCardRenderSpec,
    output: Path,
    width: int = 1280,
    height: int = 720,
    fps: int = 30,
) -> list[str]:
    spec.validate()
    duration=spec.duration_seconds
    main_width=max(1,round(width*spec.main_width_fraction))
    secondary_width=max(1,round(width*spec.secondary_width_fraction))
    margin_x=max(1,round(width*spec.margin_fraction))
    margin_y=max(1,round(height*spec.margin_fraction))
    fade=min(spec.fade_seconds,duration/2)
    fade_out_start=max(0.0,duration-fade)
    zoom_delta=spec.zoom_end-spec.zoom_start
    zoom_expr=f'{spec.zoom_start:g}+({zoom_delta:g})*t/{duration:g}'
    filters=(
        f"[1:v]format=rgba,scale=w='{main_width}*({zoom_expr})':h=-2:eval=frame[main];"
        f'[2:v]format=rgba,scale={secondary_width}:-2[brand];'
        f"[0:v][main]overlay=x='(W-w)/2':y='(H-h)/2':shortest=1[tmp];"
        f"[tmp][brand]overlay=x='W-w-{margin_x}':y='H-h-{margin_y}':shortest=1,"
        f'fade=t=in:st=0:d={fade:g},fade=t=out:st={fade_out_start:g}:d={fade:g}[v]'
    )
    return [
        ffmpeg,'-y',
        '-f','lavfi','-i',f'color=c={spec.background}:s={width}x{height}:r={fps}:d={duration:g}',
        '-loop','1','-framerate',str(fps),'-i',str(spec.main_logo),
        '-loop','1','-framerate',str(fps),'-i',str(spec.secondary_logo),
        '-f','lavfi','-i',_SILENT_AUDIO,
        '-filter_complex',filters,
        '-map','[v]','-map','3:a:0',
        '-t',f'{duration:g}','-r',str(fps),
        '-c:v','libx264','-pix_fmt','yuv420p',
        '-c:a','aac','-ar','48000','-ac','2','-shortest',
        str(output),
    ]


def render(
    package: EpisodePackage,
    output: str|Path,
    *,
    ffmpeg: str='ffmpeg',
    telemetry: TelemetrySink|None=None,
    end_card: EndCardRenderSpec|None=None,
) -> Path:
    """Render approved shots in package order, optionally followed by a branded end card.

    Generated video audio is kept and normalized to stereo AAC. Still-image fallbacks
    and rare silent clips receive a silent stereo track so every concat segment has a
    compatible audio stream. Narrative shots use hard cuts; when an end card is supplied,
    only the final narrative shot fades to black before the card.
    """
    timeline=resolve_final_media(package); output=Path(output); work=output.parent/(output.stem+'-segments'); work.mkdir(parents=True,exist_ok=True)
    segments=[]; ffprobe=_ffprobe_for(ffmpeg)
    final_fade=end_card.fade_seconds if end_card else 0.0
    for i,item in enumerate(timeline):
        uri=item['uri']
        if uri.startswith('http://') or uri.startswith('https://'): raise ValueError('Filmmaker render requires local media paths; download remote assets first')
        src=Path(uri); seg=work/f'{i:04d}-{item["shot_id"]}.mp4'; dur=str(item['duration_seconds'])
        has_audio=False if item['kind'] in _IMAGE_KINDS else _has_audio(src,ffprobe=ffprobe)
        cmd=_segment_command(
            ffmpeg=ffmpeg,src=src,seg=seg,duration=dur,kind=item['kind'],has_audio=has_audio,
            fade_out_seconds=final_fade if i==len(timeline)-1 else 0.0,
        )
        subprocess.run(cmd,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); segments.append(seg)
    extra_duration=0.0
    if end_card:
        card=work/'end-card.mp4'
        subprocess.run(_end_card_command(ffmpeg=ffmpeg,spec=end_card,output=card),check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        segments.append(card); extra_duration=end_card.duration_seconds
    concat=work/'concat.txt'; concat.write_text(''.join(f"file '{p.resolve()}'\n" for p in segments))
    subprocess.run([ffmpeg,'-y','-f','concat','-safe','0','-i',str(concat),'-c','copy',str(output)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    (telemetry or TelemetrySink()).emit(
        'final_render.completed',production_id=package.production_id,episode_id=package.episode_id,
        uri=str(output.resolve()),duration_seconds=sum(x['duration_seconds'] for x in timeline)+extra_duration,
        end_card=bool(end_card),
    )
    return output
