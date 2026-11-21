from __future__ import annotations

import json
import math
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .contracts import EpisodePackage
from .telemetry import TelemetrySink


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
    episode_label: str | None = None
    episode_title: str | None = None
    copyright_text: str | None = None
    font_file: Path | None = None

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


def resolve_final_media(package: EpisodePackage, *, ffprobe: str = "ffprobe") -> list[dict]:
    timeline=[]; t=0.0
    for shot in package.shots:
        chosen=shot.final_clip_asset_id or shot.approved_clip_asset_id or shot.approved_start_frame_asset_id
        if not chosen: raise ValueError(f'shot {shot.shot_id} has no approved media')
        asset=package.find_asset(chosen)
        media=probe_media(asset.uri, ffprobe=ffprobe, image=asset.kind in _IMAGE_KINDS)
        duration=shot.duration_seconds if asset.kind in _IMAGE_KINDS else media['duration_seconds']
        timeline.append({'shot_id':shot.shot_id,'asset_id':chosen,'uri':asset.uri,'kind':asset.kind,'start_seconds':t,'duration_seconds':duration,'edit_intent':shot.edit_intent, **media})
        t += duration
    return timeline


def write_edit_plan(package: EpisodePackage, path: str|Path) -> Path:
    out=Path(path); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps({'production_id':package.production_id,'timeline':resolve_final_media(package)},indent=2)+'\n'); return out


def _ffprobe_for(ffmpeg: str) -> str:
    path=Path(ffmpeg)
    return str(path.with_name('ffprobe')) if path.name != 'ffprobe' else str(path)


def probe_media(uri: str, *, ffprobe: str = 'ffprobe', image: bool = False) -> dict:
    """Read selected local media; video timing comes from the video stream."""
    if uri.startswith(('http://', 'https://')):
        raise ValueError('Filmmaker requires local media paths; download remote assets first')
    path=Path(uri)
    if not path.is_file():
        raise FileNotFoundError(path)
    result=subprocess.run(
        [ffprobe, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)],
        check=True, capture_output=True, text=True,
    )
    raw=json.loads(result.stdout)
    video=next((s for s in raw.get('streams', []) if s.get('codec_type') == 'video'), None)
    if video is None:
        raise ValueError(f'No video stream in {path}')
    width, height=int(video['width']), int(video['height'])
    rotation=next((float(s['rotation']) for s in video.get('side_data_list', []) if 'rotation' in s), 0)
    if round(rotation) % 180:
        width, height=height, width
    info={'width': width, 'height': height,
          'has_audio': any(s.get('codec_type') == 'audio' for s in raw.get('streams', []))}
    if not image:
        duration=video.get('duration')
        if duration in (None, 'N/A'):
            duration=raw.get('format', {}).get('duration')
        try:
            duration=float(duration)
        except (TypeError, ValueError):
            raise ValueError(f'Cannot determine video duration for {path}') from None
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError(f'Invalid video duration for {path}: {duration}')
        info['duration_seconds']=duration
    return info


def parse_resolution(value: str) -> tuple[int, int] | None:
    """Parse auto or an even WIDTHxHEIGHT canvas supported by H.264/yuv420p."""
    if value == 'auto':
        return None
    try:
        width, height=(int(part) for part in value.lower().split('x'))
    except ValueError:
        raise ValueError('resolution must be auto or WIDTHxHEIGHT (for example 1920x1080)') from None
    if width <= 0 or height <= 0 or width % 2 or height % 2:
        raise ValueError('resolution dimensions must be positive even integers')
    return width, height


def _render_resolution(timeline: list[dict], resolution: str) -> tuple[int, int]:
    explicit=parse_resolution(resolution)
    if explicit:
        return explicit
    source=next((item for item in timeline if item['kind'] not in _IMAGE_KINDS), timeline[0])
    # H.264 4:2:0 requires even dimensions; round an odd source canvas up.
    return (source['width']+1)//2*2, (source['height']+1)//2*2


def _measure_loudnorm_filter(
    *,
    ffmpeg: str,
    src: Path,
    target_lufs: float = -16.0,
    true_peak_db: float = -1.5,
    lra: float = 11.0,
) -> str | None:
    """Measure clip loudness so per-shot audio levels match before concatenation."""
    res = subprocess.run(
        [
            ffmpeg, '-hide_banner', '-nostats', '-i', str(src), '-vn',
            '-af', f'loudnorm=I={target_lufs:g}:TP={true_peak_db:g}:LRA={lra:g}:print_format=json',
            '-f', 'null', '-',
        ],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        return None
    match = re.search(r'\{[^{}]*"input_i"[^{}]*\}', res.stderr, re.DOTALL)
    if not match:
        return None
    try:
        stats = json.loads(match.group(0))
        input_i = float(stats['input_i'])
        input_tp = float(stats['input_tp'])
        input_lra = float(stats['input_lra'])
        input_thresh = float(stats['input_thresh'])
        offset = float(stats['target_offset'])
    except (KeyError, TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in (input_i, input_tp, input_lra, input_thresh, offset)):
        return None
    if input_i <= -70.0:
        return None
    return (
        f'loudnorm=I={target_lufs:g}:TP={true_peak_db:g}:LRA={lra:g}:'
        f'measured_I={input_i:g}:measured_TP={input_tp:g}:measured_LRA={input_lra:g}:'
        f'measured_thresh={input_thresh:g}:offset={offset:g}:linear=true'
    )


def _segment_command(
    *,
    ffmpeg: str,
    src: Path,
    seg: Path,
    duration: str,
    kind: str,
    has_audio: bool,
    fade_out_seconds: float = 0.0,
    width: int = 1280,
    height: int = 720,
    audio_normalize_filter: str | None = None,
) -> list[str]:
    """Normalize a segment while preserving authored audio and optional final fade."""
    duration_seconds=float(duration)
    if fade_out_seconds < 0 or fade_out_seconds > duration_seconds:
        raise ValueError('fade_out_seconds must be between zero and the segment duration')
    video_filter=(
        f'scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,'
        f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p'
    )
    audio_filters: list[str] = []
    if has_audio:
        if audio_normalize_filter:
            audio_filters.append(audio_normalize_filter)
        audio_filters.append('apad')
    if fade_out_seconds:
        start=max(0.0,duration_seconds-fade_out_seconds)
        video_filter += f',fade=t=out:st={start:g}:d={fade_out_seconds:g}'
        audio_filters.append(f'afade=t=out:st={start:g}:d={fade_out_seconds:g}')
    audio_filter = ','.join(audio_filters) if audio_filters else None
    common=[
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
            '-t',duration,*common,'-shortest',str(seg),
        ]
    if has_audio:
        return [
            ffmpeg,'-y','-i',str(src),
            '-map','0:v:0','-map','0:a:0',
            *common,'-shortest',str(seg),
        ]
    return [
        ffmpeg,'-y','-i',str(src),
        '-f','lavfi','-t',duration,'-i',_SILENT_AUDIO,
        '-map','0:v:0','-map','1:a:0',
        *common,'-shortest',str(seg),
    ]


def _escape_drawtext_text(value: str) -> str:
    return (
        value.replace('\\', '\\\\')
        .replace(':', '\\:')
        .replace("'", "\\'")
        .replace('%', '\\%')
    )


def _escape_drawtext_path(path: Path) -> str:
    return str(path).replace('\\', '\\\\').replace(':', '\\:').replace("'", "\\'")


def _resolve_end_card_font(spec_font: Path | None) -> Path | None:
    if spec_font is not None:
        return spec_font if spec_font.is_file() else None
    for candidate in (
        Path('/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf'),
        Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),
        Path('/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf'),
    ):
        if candidate.is_file():
            return candidate
    return None


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
    frames=max(2,round(duration*fps))
    main_width=max(1,round(width*spec.main_width_fraction))
    secondary_width=max(1,round(width*spec.secondary_width_fraction))
    margin_x=max(1,round(width*spec.margin_fraction))
    margin_y=max(1,round(height*spec.margin_fraction))
    fade=min(spec.fade_seconds,duration/2)
    fade_out_start=max(0.0,duration-fade)
    has_episode_text = bool((spec.episode_label or '').strip() or (spec.episode_title or '').strip())
    max_main_height = max(1, round(height * (0.44 if has_episode_text else 0.62)))
    max_secondary_height = max(1, round(height * 0.13))
    main_y = f'{round(height * 0.31)}-h/2' if has_episode_text else '(H-h)/2'
    scale_max = max(spec.zoom_start, spec.zoom_end)
    scale_min = min(spec.zoom_start, spec.zoom_end)
    if abs(scale_max - scale_min) < 1e-6:
        fixed_w = max(1, round(main_width * spec.zoom_start))
        fixed_h = max(1, round(max_main_height * spec.zoom_start))
        main_filter = (
            f"[1:v]format=rgba,scale=w={fixed_w}:h={fixed_h}:"
            f"force_original_aspect_ratio=decrease:flags=lanczos[main];"
        )
        main_input = ['-loop','1','-framerate',str(fps),'-i',str(spec.main_logo)]
    else:
        box_w = max(2, round(main_width * scale_max))
        box_h = max(2, round(max_main_height * scale_max))
        if box_w % 2: box_w += 1
        if box_h % 2: box_h += 1
        supersample = 8
        pad_w, pad_h = box_w * supersample, box_h * supersample
        in_w = max(2, round(main_width * scale_min * supersample))
        in_h = max(2, round(max_main_height * scale_min * supersample))
        out2x_w, out2x_h = box_w * 2, box_h * 2
        z_ratio = scale_max / scale_min
        if spec.zoom_end >= spec.zoom_start:
            z_expr = f"1+({z_ratio - 1:.10f})*on/{frames - 1}"
        else:
            z_expr = f"{z_ratio:.10f}-({z_ratio - 1:.10f})*on/{frames - 1}"
        main_filter = (
            f"[1:v]format=rgba,scale=w={in_w}:h={in_h}:"
            f"force_original_aspect_ratio=decrease:flags=lanczos[lg];"
            f"color=c=black@0.0:s={pad_w}x{pad_h}:r={fps}:d=0.1,format=rgba[zbg];"
            f"[zbg][lg]overlay=x='(W-w)/2':y='(H-h)/2':shortest=1,format=rgba,"
            f"zoompan=z='{z_expr}':x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2':"
            f"d={frames}:s={out2x_w}x{out2x_h}:fps={fps},"
            f"scale=w={box_w}:h={box_h}:flags=lanczos,format=rgba[main];"
        )
        main_input = ['-i',str(spec.main_logo)]
    chain = (
        f"{main_filter}"
        f"[2:v]format=rgba,scale=w={secondary_width}:h={max_secondary_height}:force_original_aspect_ratio=decrease:flags=lanczos[brand];"
        f"[0:v][main]overlay=x='(W-w)/2':y='{main_y}':shortest=1[tmp];"
        f"[tmp][brand]overlay=x='W-w-{margin_x}':y='H-h-{margin_y}':shortest=1"
    )
    font_path = _resolve_end_card_font(spec.font_file)
    bold_font_path = Path('/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf')
    if spec.font_file is not None or not bold_font_path.is_file():
        bold_font_path = font_path
    font_opt = f"fontfile='{_escape_drawtext_path(font_path)}':" if font_path else ''
    bold_opt = f"fontfile='{_escape_drawtext_path(bold_font_path)}':" if bold_font_path else font_opt
    draw_filters: list[str] = []
    label = (spec.episode_label or '').strip()
    title = (spec.episode_title or '').strip()
    copyright_line = (spec.copyright_text or '').strip()
    if label:
        label_size = max(14, round(height * 0.025))
        label_y = round(height * 0.60)
        draw_filters.append(
            f"drawtext={font_opt}text='{_escape_drawtext_text(label)}':"
            f"fontsize={label_size}:fontcolor=0xC8B89A:x='(w-text_w)/2':y={label_y}"
        )
    if title:
        title_size = max(22, round(height * 0.048))
        title_y = round(height * (0.655 if label else 0.61))
        draw_filters.append(
            f"drawtext={bold_opt}text='{_escape_drawtext_text(title)}':"
            f"fontsize={title_size}:fontcolor=0xF5F3EE:x='(w-text_w)/2':y={title_y}"
        )
    if copyright_line:
        copy_size = max(12, round(height * 0.020))
        draw_filters.append(
            f"drawtext={font_opt}text='{_escape_drawtext_text(copyright_line)}':"
            f"fontsize={copy_size}:fontcolor=0x9A9A9A:x={margin_x}:y=h-text_h-{margin_y}"
        )
    text_stage = (',' + ','.join(draw_filters)) if draw_filters else ''
    filters = f"{chain}{text_stage},fade=t=in:st=0:d={fade:g},fade=t=out:st={fade_out_start:g}:d={fade:g}[v]"
    return [
        ffmpeg,'-nostdin','-y',
        '-f','lavfi','-i',f'color=c={spec.background}:s={width}x{height}:r={fps}:d={duration:g}',
        *main_input,
        '-loop','1','-framerate',str(fps),'-i',str(spec.secondary_logo),
        '-f','lavfi','-i',_SILENT_AUDIO,
        '-filter_complex',filters,
        '-map','[v]','-map','3:a:0',
        '-t',f'{duration:g}','-r',str(fps),
        '-c:v','libx264','-crf','16','-pix_fmt','yuv420p',
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
    resolution: str = "auto",
    normalize_audio: bool = True,
    target_lufs: float = -16.0,
) -> Path:
    """Render approved shots in package order, optionally followed by a branded end card.

    Videos play to EOF rather than being trimmed to package durations. The default
    canvas matches the first video; an explicit resolution scales with padding.
    Generated video audio is kept and normalized to stereo AAC. Still-image fallbacks
    and rare silent clips receive a silent stereo track so every concat segment has a
    compatible audio stream. Narrative shots use hard cuts; when an end card is supplied,
    only the final narrative shot fades to black before the card.
    """
    parse_resolution(resolution)
    timeline=resolve_final_media(package, ffprobe=_ffprobe_for(ffmpeg))
    if not timeline:
        raise ValueError('Cannot render an episode with no shots')
    width,height=_render_resolution(timeline,resolution)
    output=Path(output); work=output.parent/(output.stem+'-segments'); work.mkdir(parents=True,exist_ok=True)
    segments=[]
    final_fade=end_card.fade_seconds if end_card else 0.0
    for i,item in enumerate(timeline):
        uri=item['uri']
        if uri.startswith('http://') or uri.startswith('https://'): raise ValueError('Filmmaker render requires local media paths; download remote assets first')
        src=Path(uri); seg=work/f'{i:04d}-{item["shot_id"]}.mp4'; dur=str(item['duration_seconds'])
        has_audio=item['has_audio'] if item['kind'] not in _IMAGE_KINDS else False
        norm_filter = (
            _measure_loudnorm_filter(ffmpeg=ffmpeg, src=src, target_lufs=target_lufs)
            if has_audio and normalize_audio
            else None
        )
        cmd=_segment_command(
            ffmpeg=ffmpeg,src=src,seg=seg,duration=dur,kind=item['kind'],has_audio=has_audio,
            fade_out_seconds=min(final_fade, float(dur)) if i==len(timeline)-1 else 0.0,
            width=width,height=height,
            audio_normalize_filter=norm_filter,
        )
        subprocess.run(cmd,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); segments.append(seg)
    extra_duration=0.0
    if end_card:
        card=work/'end-card.mp4'
        subprocess.run(_end_card_command(ffmpeg=ffmpeg,spec=end_card,output=card,width=width,height=height),check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        segments.append(card); extra_duration=end_card.duration_seconds
    concat=work/'concat.txt'; concat.write_text(''.join(f"file '{p.resolve()}'\n" for p in segments))
    subprocess.run([ffmpeg,'-y','-f','concat','-safe','0','-i',str(concat),'-c','copy',str(output)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    (telemetry or TelemetrySink()).emit(
        'final_render.completed',production_id=package.production_id,episode_id=package.episode_id,
        show_id=package.show_id,
        uri=str(output.resolve()),duration_seconds=sum(x['duration_seconds'] for x in timeline)+extra_duration,
        end_card=bool(end_card),width=width,height=height,
    )
    return output
