from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse
from urllib.request import urlretrieve

from .ai_runtime import AIRuntimeMediaClient, runtime_asset
from .asset_resolution import discover_asset_sources
from .contracts import EpisodePackage
from .filmmaker import EndCardRenderSpec, render
from .local_config import LocalSecretStore
from .telemetry import TelemetrySink


MusicMode = Literal['video', 'style', 'custom', 'none']


@dataclass(frozen=True)
class EndCardProfile:
    enabled: bool
    main_logo_asset_id: str
    secondary_logo_asset_id: str
    duration_seconds: float = 3.0
    fade_seconds: float = 0.35
    main_width_fraction: float = 0.58
    secondary_width_fraction: float = 0.16
    margin_fraction: float = 0.04
    zoom_start: float = 0.96
    zoom_end: float = 1.06
    background: str = 'black'


@dataclass(frozen=True)
class MusicProfile:
    enabled: bool
    provider: str = 'fal'
    model: str = 'sonilo/v1.1/video-to-music'
    default_mode: MusicMode = 'style'
    prompt_library: str | None = None
    prompt_id: str | None = None
    prompt_influence: float = 0.5
    mix_level: float = 0.18
    fade_in_seconds: float = 0.4
    fade_out_seconds: float = 0.8


@dataclass(frozen=True)
class FinishingProfile:
    path: Path
    profile_id: str
    end_card: EndCardProfile | None
    music: MusicProfile | None


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError('Install Forge Studios with YAML support') from exc
    value=yaml.safe_load(path.read_text())
    if not isinstance(value,dict):
        raise ValueError(f'expected YAML mapping: {path}')
    return value


def discover_finishing_profile(package_path: str|Path) -> Path | None:
    """Find a production override or show-owned finishing profile above a package."""
    package=Path(package_path).expanduser().resolve()
    candidates=[package.parent/'finishing.yaml']
    for parent in package.parents:
        candidates.append(parent/'show'/'finishing.yaml')
    seen:set[Path]=set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.is_file():
            return candidate
    return None


def load_finishing_profile(path: str|Path) -> FinishingProfile:
    profile_path=Path(path).expanduser().resolve(); raw=_load_yaml(profile_path)
    if raw.get('kind')!='finishing_profile':
        raise ValueError(f'finishing profile must have kind=finishing_profile: {profile_path}')
    end_raw=raw.get('end_card')
    end_card=None
    if isinstance(end_raw,dict) and bool(end_raw.get('enabled',True)):
        end_card=EndCardProfile(
            enabled=True,
            main_logo_asset_id=str(end_raw['main_logo_asset_id']),
            secondary_logo_asset_id=str(end_raw['secondary_logo_asset_id']),
            duration_seconds=float(end_raw.get('duration_seconds',3.0)),
            fade_seconds=float(end_raw.get('fade_seconds',0.35)),
            main_width_fraction=float(end_raw.get('main_width_fraction',0.58)),
            secondary_width_fraction=float(end_raw.get('secondary_width_fraction',0.16)),
            margin_fraction=float(end_raw.get('margin_fraction',0.04)),
            zoom_start=float(end_raw.get('zoom_start',0.96)),
            zoom_end=float(end_raw.get('zoom_end',1.06)),
            background=str(end_raw.get('background','black')),
        )
    music_raw=raw.get('music')
    music=None
    if isinstance(music_raw,dict) and bool(music_raw.get('enabled',True)):
        mode=str(music_raw.get('default_mode','style'))
        if mode not in {'video','style','custom','none'}:
            raise ValueError(f'unsupported default music mode: {mode!r}')
        music=MusicProfile(
            enabled=True,
            provider=str(music_raw.get('provider','fal')),
            model=str(music_raw.get('model','sonilo/v1.1/video-to-music')),
            default_mode=mode,  # type: ignore[arg-type]
            prompt_library=str(music_raw['prompt_library']) if music_raw.get('prompt_library') else None,
            prompt_id=str(music_raw['prompt_id']) if music_raw.get('prompt_id') else None,
            prompt_influence=float(music_raw.get('prompt_influence',0.5)),
            mix_level=float(music_raw.get('mix_level',0.18)),
            fade_in_seconds=float(music_raw.get('fade_in_seconds',0.4)),
            fade_out_seconds=float(music_raw.get('fade_out_seconds',0.8)),
        )
    return FinishingProfile(path=profile_path,profile_id=str(raw.get('id') or profile_path.stem),end_card=end_card,music=music)


def _manifest_records(manifest_path: Path) -> dict[str, dict[str, Any]]:
    raw=_load_yaml(manifest_path)
    if raw.get('kind')!='asset_manifest':
        raise ValueError(f'asset manifest must have kind=asset_manifest: {manifest_path}')
    return {
        str(item['asset_id']):item
        for item in raw.get('assets',[])
        if isinstance(item,dict) and item.get('asset_id')
    }


def resolve_manifest_asset(asset_id: str, *, manifest_path: str|Path, asset_root: str|Path) -> Path:
    manifest=Path(manifest_path).expanduser().resolve(); root=Path(asset_root).expanduser().resolve()
    record=_manifest_records(manifest).get(asset_id)
    if record is None:
        raise KeyError(f'asset {asset_id!r} is not present in {manifest}')
    storage_key=record.get('storage_key')
    if not isinstance(storage_key,str) or not storage_key:
        raise ValueError(f'asset {asset_id!r} has no storage_key')
    key=Path(storage_key)
    if key.is_absolute() or '..' in key.parts:
        raise ValueError(f'unsafe asset storage_key for {asset_id!r}: {storage_key!r}')
    path=root/key
    if not path.is_file():
        raise FileNotFoundError(f'asset {asset_id!r} resolves to missing file: {path}')
    return path


def resolve_end_card(
    profile: FinishingProfile,
    *,
    manifest_path: str|Path,
    asset_root: str|Path,
) -> EndCardRenderSpec | None:
    spec=profile.end_card
    if not spec or not spec.enabled:
        return None
    return EndCardRenderSpec(
        main_logo=resolve_manifest_asset(spec.main_logo_asset_id,manifest_path=manifest_path,asset_root=asset_root),
        secondary_logo=resolve_manifest_asset(spec.secondary_logo_asset_id,manifest_path=manifest_path,asset_root=asset_root),
        duration_seconds=spec.duration_seconds,
        fade_seconds=spec.fade_seconds,
        main_width_fraction=spec.main_width_fraction,
        secondary_width_fraction=spec.secondary_width_fraction,
        margin_fraction=spec.margin_fraction,
        zoom_start=spec.zoom_start,
        zoom_end=spec.zoom_end,
        background=spec.background,
    )


def load_profile_music_prompt(profile: FinishingProfile) -> str:
    music=profile.music
    if not music or not music.prompt_library or not music.prompt_id:
        raise ValueError(f'finishing profile {profile.profile_id!r} does not define a style prompt')
    library=(profile.path.parent/music.prompt_library).resolve()
    raw=_load_yaml(library)
    for item in raw.get('prompts',[]):
        if isinstance(item,dict) and str(item.get('id'))==music.prompt_id:
            text=str(item.get('template') or '').strip()
            if not text:
                raise ValueError(f'music prompt {music.prompt_id!r} has an empty template')
            return text
    raise KeyError(f'music prompt {music.prompt_id!r} not found in {library}')


def compose_music_prompt(
    profile: FinishingProfile,
    mode: MusicMode,
    *,
    custom_prompt: str|None=None,
    direction: str|None=None,
) -> str | None:
    if mode=='none':
        return None
    if mode=='video':
        if custom_prompt or direction:
            raise ValueError('video music mode does not accept prompt text')
        return None
    if mode=='custom':
        text=(custom_prompt or '').strip()
        if not text:
            raise ValueError('custom music mode requires --music-prompt')
        return text
    prompt=load_profile_music_prompt(profile)
    if custom_prompt:
        raise ValueError('style music mode uses the show prompt; use --music-direction to add episode direction or custom mode to replace it')
    if direction and direction.strip():
        prompt=f'{prompt}\n\nEPISODE DIRECTION\n{direction.strip()}'
    return prompt


def _sonilo_payload(video_url: str, *, prompt: str|None, prompt_influence: float) -> dict[str, Any]:
    payload:dict[str,Any]={'video_url':video_url,'num_samples':1}
    if prompt is not None:
        payload['prompt']=prompt
        payload['prompt_influence']=prompt_influence
    return payload


def generate_sonilo_music(
    video: str|Path,
    output: str|Path,
    *,
    model: str='sonilo/v1.1/video-to-music',
    prompt: str|None=None,
    prompt_influence: float=0.5,
    local_config: LocalSecretStore|None=None,
    telemetry: TelemetrySink|None=None,
    progress=print,
    client_timeout_seconds: float|None=None,
    poll_interval_seconds: float=5.0,
    runtime_client: AIRuntimeMediaClient|None=None,
) -> Path:
    src=Path(video).expanduser().resolve(); out=Path(output).expanduser().resolve()
    if not src.is_file():
        raise FileNotFoundError(src)
    out.parent.mkdir(parents=True,exist_ok=True)
    runtime_assets:dict[str,dict[str,str]]={}
    video_url=runtime_asset(str(src),assets=runtime_assets,asset_id='picture_lock')
    if not video_url:
        raise RuntimeError('could not prepare picture lock for AI Runtime')
    payload=_sonilo_payload(video_url,prompt=prompt,prompt_influence=prompt_influence)
    sink=telemetry or TelemetrySink(); started=time.monotonic()
    sink.emit(
        'music_generation.started',provider='fal',transport='agenticforge-ai-runtime',
        model=model,video=str(src),prompt=prompt,prompt_influence=prompt_influence,
    )
    if progress:
        progress(f'[ai-runtime/fal] music: submitting {model}')
    runtime=runtime_client or AIRuntimeMediaClient(
        timeout_seconds=max((client_timeout_seconds or 600)+30,60),
    )
    owns_runtime=runtime_client is None
    try:
        envelope=runtime.generate(
            model=model,
            arguments=payload,
            assets=runtime_assets,
            metadata={'caller':'forge-studios','role':'music'},
            client_timeout_seconds=client_timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
    except Exception as exc:
        sink.emit(
            'music_generation.failed',provider='fal',transport='agenticforge-ai-runtime',
            model=model,error=str(exc),
        )
        raise
    finally:
        if owns_runtime:
            runtime.close()
    raw=envelope['result']; request_id=envelope.get('request_id')
    audio=raw.get('audio') if isinstance(raw,dict) else None
    if not isinstance(audio,dict) or not isinstance(audio.get('url'),str):
        audios=raw.get('audios') if isinstance(raw,dict) else None
        audio=audios[0] if isinstance(audios,list) and audios and isinstance(audios[0],dict) else None
    if not isinstance(audio,dict) or not isinstance(audio.get('url'),str):
        raise RuntimeError('Sonilo returned no recognizable audio URL')
    remote=audio['url']; parsed=urlparse(remote)
    if parsed.scheme not in {'http','https'}:
        raise RuntimeError(f'Sonilo returned unsupported audio URI: {remote}')
    urlretrieve(remote,out)
    sink.emit(
        'music_generation.completed',provider='fal',transport='agenticforge-ai-runtime',
        model=str(envelope.get('model') or model),request_id=request_id,
        uri=str(out),remote_uri=remote,prompt=prompt,prompt_influence=prompt_influence,
        latency_ms=round((time.monotonic()-started)*1000,1),
    )
    return out

def _ffprobe_for(ffmpeg: str) -> str:
    path=Path(ffmpeg)
    return str(path.with_name('ffprobe')) if path.name!='ffprobe' else str(path)


def _probe_duration(path: Path, *, ffprobe: str) -> float:
    result=subprocess.run(
        [ffprobe,'-v','error','-show_entries','format=duration','-of','default=noprint_wrappers=1:nokey=1',str(path)],
        check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
    )
    return float(result.stdout.strip())


def _mix_music_command(
    *,
    ffmpeg: str,
    video: Path,
    music: Path,
    output: Path,
    level: float,
    duration_seconds: float,
    fade_in_seconds: float,
    fade_out_seconds: float,
) -> list[str]:
    if level < 0:
        raise ValueError('music level cannot be negative')
    fade_in=max(0.0,min(fade_in_seconds,duration_seconds))
    fade_out=max(0.0,min(fade_out_seconds,duration_seconds))
    fade_out_start=max(0.0,duration_seconds-fade_out)
    filters=(
        f'[1:a]volume={level:g},afade=t=in:st=0:d={fade_in:g},'
        f'afade=t=out:st={fade_out_start:g}:d={fade_out:g}[music];'
        '[0:a][music]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,'
        'alimiter=limit=0.98[a]'
    )
    return [
        ffmpeg,'-y','-i',str(video),'-i',str(music),
        '-filter_complex',filters,
        '-map','0:v:0','-map','[a]','-c:v','copy','-c:a','aac','-ar','48000','-ac','2','-shortest',str(output),
    ]


def mix_music(
    video: str|Path,
    music: str|Path,
    output: str|Path,
    *,
    ffmpeg: str='ffmpeg',
    level: float=0.18,
    fade_in_seconds: float=0.4,
    fade_out_seconds: float=0.8,
    telemetry: TelemetrySink|None=None,
) -> Path:
    video_path=Path(video).expanduser().resolve(); music_path=Path(music).expanduser().resolve(); out=Path(output).expanduser().resolve()
    if not video_path.is_file(): raise FileNotFoundError(video_path)
    if not music_path.is_file(): raise FileNotFoundError(music_path)
    out.parent.mkdir(parents=True,exist_ok=True)
    duration=_probe_duration(video_path,ffprobe=_ffprobe_for(ffmpeg))
    cmd=_mix_music_command(
        ffmpeg=ffmpeg,video=video_path,music=music_path,output=out,level=level,duration_seconds=duration,
        fade_in_seconds=fade_in_seconds,fade_out_seconds=fade_out_seconds,
    )
    subprocess.run(cmd,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    (telemetry or TelemetrySink()).emit('final_mix.completed',video=str(video_path),music=str(music_path),uri=str(out),music_level=level)
    return out


def score_video(
    video: str|Path,
    output: str|Path,
    *,
    profile: FinishingProfile,
    music_mode: MusicMode|None=None,
    music_prompt: str|None=None,
    music_direction: str|None=None,
    prompt_influence: float|None=None,
    music_level: float|None=None,
    music_output: str|Path|None=None,
    ffmpeg: str='ffmpeg',
    telemetry: TelemetrySink|None=None,
    progress=print,
    client_timeout_seconds: float|None=None,
    poll_interval_seconds: float=5.0,
) -> Path:
    """Generate a fresh music stem for an existing picture lock and mix it deterministically."""
    source=Path(video).expanduser().resolve(); out=Path(output).expanduser().resolve(); sink=telemetry or TelemetrySink()
    if source==out:
        raise ValueError('music scoring output must differ from the input picture lock')
    music=profile.music
    mode=music_mode or (music.default_mode if music else 'none')
    if mode=='none' or not music or not music.enabled:
        out.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(source,out); return out
    if music.provider!='fal':
        raise ValueError(f'unsupported music provider: {music.provider!r}')
    prompt=compose_music_prompt(profile,mode,custom_prompt=music_prompt,direction=music_direction)
    influence=music.prompt_influence if prompt_influence is None else prompt_influence
    level=music.mix_level if music_level is None else music_level
    music_path=Path(music_output).expanduser().resolve() if music_output else out.with_name(f'{out.stem}-music.m4a')
    generate_sonilo_music(
        source,music_path,model=music.model,prompt=prompt,prompt_influence=influence,
        telemetry=sink,progress=progress,client_timeout_seconds=client_timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )
    return mix_music(
        source,music_path,out,ffmpeg=ffmpeg,level=level,fade_in_seconds=music.fade_in_seconds,
        fade_out_seconds=music.fade_out_seconds,telemetry=sink,
    )


def finish_episode(
    package: EpisodePackage,
    *,
    package_path: str|Path,
    output: str|Path,
    profile: FinishingProfile,
    manifest_path: str|Path|None=None,
    asset_root: str|Path|None=None,
    ffmpeg: str='ffmpeg',
    telemetry: TelemetrySink|None=None,
    music_mode: MusicMode|None=None,
    music_prompt: str|None=None,
    music_direction: str|None=None,
    prompt_influence: float|None=None,
    music_level: float|None=None,
    picture_lock_output: str|Path|None=None,
    music_output: str|Path|None=None,
    progress=print,
    client_timeout_seconds: float|None=None,
    poll_interval_seconds: float=5.0,
) -> Path:
    """Create picture lock, generate one Sonilo stem, then produce the authoritative final mix."""
    out=Path(output).expanduser().resolve(); out.parent.mkdir(parents=True,exist_ok=True); sink=telemetry or TelemetrySink()
    discovered_manifest,discovered_root=discover_asset_sources(package_path)
    manifest=Path(manifest_path).expanduser().resolve() if manifest_path else discovered_manifest
    root=Path(asset_root).expanduser().resolve() if asset_root else discovered_root
    end_card=None
    if profile.end_card:
        if not manifest or not root:
            raise ValueError('end card requires --asset-manifest/--asset-root or discoverable Forge Born asset sources')
        end_card=resolve_end_card(profile,manifest_path=manifest,asset_root=root)
    picture=Path(picture_lock_output).expanduser().resolve() if picture_lock_output else out.with_name(f'{out.stem}-picture-lock{out.suffix}')
    render(package,picture,ffmpeg=ffmpeg,telemetry=sink,end_card=end_card)
    return score_video(
        picture,out,profile=profile,music_mode=music_mode,music_prompt=music_prompt,music_direction=music_direction,
        prompt_influence=prompt_influence,music_level=music_level,music_output=music_output,ffmpeg=ffmpeg,
        telemetry=sink,progress=progress,client_timeout_seconds=client_timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )
