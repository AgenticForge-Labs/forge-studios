from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlretrieve
from uuid import uuid4

from .contracts import AssetRecord, EpisodePackage
from .telemetry import TelemetrySink


def _local_media(uri: str, target_dir: Path) -> Path:
    parsed=urlparse(uri)
    if parsed.scheme=='file': return Path(parsed.path)
    if parsed.scheme in {'http','https'}:
        target=target_dir/f'download_{uuid4().hex}{Path(parsed.path).suffix or ".mp4"}'
        urlretrieve(uri,target)
        return target
    return Path(uri).expanduser()


def extract_boundary_frames(
    package: EpisodePackage,
    shot_id: str,
    clip_asset_id: str,
    *,
    output_dir: str|Path,
    ffmpeg: str='ffmpeg',
    telemetry: TelemetrySink|None=None,
) -> tuple[AssetRecord,AssetRecord]:
    """Extract first/last frames from a generated/physical clip with provenance.

    This is post-generation continuity capture. It is intentionally distinct from
    a shot's pre-generation `frame_plan` and approved start/end composition.
    """
    shot=package.find_shot(shot_id); source=package.find_asset(clip_asset_id)
    if source.shot_id and source.shot_id!=shot_id: raise ValueError('clip belongs to another shot')
    target=Path(output_dir); target.mkdir(parents=True,exist_ok=True)
    source_path=_local_media(source.uri,target)
    if not source_path.exists(): raise FileNotFoundError(source_path)
    exe=shutil.which(ffmpeg) if Path(ffmpeg).name==ffmpeg else ffmpeg
    if not exe: raise RuntimeError('ffmpeg is required to extract boundary frames')
    first=target/f'{clip_asset_id}_first.png'; last=target/f'{clip_asset_id}_last.png'
    subprocess.run([exe,'-hide_banner','-loglevel','error','-y','-i',str(source_path),'-vf','select=eq(n\\,0)','-frames:v','1',str(first)],check=True)
    subprocess.run([exe,'-hide_banner','-loglevel','error','-y','-sseof','-1','-i',str(source_path),'-update','1','-q:v','2',str(last)],check=True)
    first_asset=AssetRecord(
        kind='frame_image',uri=str(first.resolve()),status='candidate',authority='generated',
        episode_id=package.episode_id,shot_id=shot_id,source_asset_ids=[clip_asset_id],provider='derived',model='ffmpeg',
        metadata={'frame_role':'first_frame','source_video_uri':source.uri},
    )
    last_asset=AssetRecord(
        kind='frame_image',uri=str(last.resolve()),status='candidate',authority='generated',
        episode_id=package.episode_id,shot_id=shot_id,source_asset_ids=[clip_asset_id],provider='derived',model='ffmpeg',
        metadata={'frame_role':'last_frame','source_video_uri':source.uri},
    )
    package.assets.extend([first_asset,last_asset])
    sink=telemetry or TelemetrySink()
    for asset,role in ((first_asset,'first_frame'),(last_asset,'last_frame')):
        sink.emit('boundary_frame.extracted',production_id=package.production_id,episode_id=package.episode_id,shot_id=shot_id,source_asset_id=clip_asset_id,asset_id=asset.asset_id,frame_role=role,uri=asset.uri)
    # Captured frames are available for explicit chaining by asset ID; do not silently
    # overwrite the shot's approved pre-generation start/end frames.
    return first_asset,last_asset
