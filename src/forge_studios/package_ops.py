from __future__ import annotations
from pathlib import Path
from .contracts import AssetRecord, EpisodePackage, FramePlan
from .telemetry import TelemetrySink

APPROVAL_FIELD={'storyboard':'approved_storyboard_asset_id','start_frame':'approved_start_frame_asset_id','end_frame':'approved_end_frame_asset_id','clip':'approved_clip_asset_id','take':'approved_take_id'}
PROMPT_FIELD={'image':'image_prompt','storyboard':'storyboard_prompt','start_frame':'start_frame_prompt','end_frame':'end_frame_prompt','video':'video_prompt'}

def approve_asset(package: EpisodePackage, shot_id: str, kind: str, asset_id: str, telemetry: TelemetrySink|None=None) -> None:
    shot=package.find_shot(shot_id); asset=package.find_asset(asset_id)
    if asset.shot_id and asset.shot_id != shot_id: raise ValueError('asset belongs to another shot')
    setattr(shot,APPROVAL_FIELD[kind],asset_id); asset.status='approved'; asset.authority='seeded'
    if kind=='storyboard': shot.status='storyboard_ready'
    (telemetry or TelemetrySink()).emit('asset.approved',production_id=package.production_id,episode_id=package.episode_id,shot_id=shot_id,asset_id=asset_id,role=kind)

def reject_asset(package: EpisodePackage, shot_id: str, asset_id: str, telemetry: TelemetrySink|None=None) -> None:
    asset=package.find_asset(asset_id)
    if asset.shot_id and asset.shot_id != shot_id: raise ValueError('asset belongs to another shot')
    asset.status='rejected'; (telemetry or TelemetrySink()).emit('asset.rejected',production_id=package.production_id,episode_id=package.episode_id,shot_id=shot_id,asset_id=asset_id)

def register_asset(package: EpisodePackage, *, asset_id: str, uri: str, kind: str='reference_image', status: str='canon', authority: str='locked', metadata: dict|None=None) -> AssetRecord:
    """Register an existing local/remote asset under a stable semantic ID."""
    try:
        return package.find_asset(asset_id)
    except KeyError:
        pass
    normalized=uri
    path=Path(uri).expanduser()
    if path.exists(): normalized=str(path.resolve())
    asset=AssetRecord(asset_id=asset_id,kind=kind,uri=normalized,status=status,authority=authority,metadata=metadata or {})
    package.assets.append(asset)
    return asset

def add_reference(package: EpisodePackage, shot_id: str, asset_id: str) -> None:
    package.find_asset(asset_id)
    shot=package.find_shot(shot_id)
    if asset_id not in shot.continuity_asset_ids:
        shot.continuity_asset_ids.append(asset_id)

def remove_reference(package: EpisodePackage, shot_id: str, asset_id: str) -> None:
    shot=package.find_shot(shot_id)
    shot.continuity_asset_ids=[value for value in shot.continuity_asset_ids if value != asset_id]

def set_prompt(package: EpisodePackage, shot_id: str, role: str, text: str|None) -> None:
    shot=package.find_shot(shot_id)
    try:
        field=PROMPT_FIELD[role]
    except KeyError as exc:
        raise ValueError(f'unknown prompt role {role!r}') from exc
    setattr(shot,field,text)

def set_frame_plan(package: EpisodePackage, shot_id: str, mode: str, *, chain_from_shot_id: str|None=None, start_asset_id: str|None=None, end_asset_id: str|None=None) -> None:
    shot=package.find_shot(shot_id)
    shot.frame_plan=FramePlan(mode=mode,chain_from_shot_id=chain_from_shot_id,start_asset_id=start_asset_id,end_asset_id=end_asset_id)
