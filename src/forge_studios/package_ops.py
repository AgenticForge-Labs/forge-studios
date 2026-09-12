from __future__ import annotations
from .contracts import EpisodePackage
from .telemetry import TelemetrySink

APPROVAL_FIELD={'storyboard':'approved_storyboard_asset_id','start_frame':'approved_start_frame_asset_id','end_frame':'approved_end_frame_asset_id','clip':'approved_clip_asset_id','take':'approved_take_id'}

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
