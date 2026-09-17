from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from .contracts import AssetRecord, EpisodePackage, FramePlan
from .frame_plan import FramePlanError, FramePlanIssue, predecessor_for, validate_frame_plans
from .telemetry import TelemetrySink

APPROVAL_FIELD={'start_frame':'approved_start_frame_asset_id','end_frame':'approved_end_frame_asset_id','clip':'approved_clip_asset_id'}
PROMPT_FIELD={'image':'image_prompt','storyboard':'storyboard_prompt','start_frame':'start_frame_prompt','end_frame':'end_frame_prompt','video':'video_prompt'}

def _record_review(asset: AssetRecord, *, decision: str, note: str|None=None, tags: list[str]|None=None, scores: dict[str,float]|None=None) -> dict[str,Any]:
    review={'decision':decision,'reviewed_at':datetime.now(timezone.utc).isoformat(),'note':note,'tags':tags or [],'scores':scores or {}}
    reviews=asset.metadata.setdefault('reviews',[])
    if isinstance(reviews,list): reviews.append(review)
    else: asset.metadata['reviews']=[review]
    return review

def approve_asset(package: EpisodePackage, shot_id: str, kind: str, asset_id: str, telemetry: TelemetrySink|None=None, *, note: str|None=None, tags: list[str]|None=None, scores: dict[str,float]|None=None) -> None:
    shot=package.find_shot(shot_id); asset=package.find_asset(asset_id)
    if asset.shot_id and asset.shot_id != shot_id: raise ValueError('asset belongs to another shot')
    inheriting=[]
    if kind=='end_frame':
        inheriting=[successor for successor in package.shots
                    if successor.frame_plan.mode=='start_and_end' and successor.frame_plan.chain_from_shot_id==shot_id]
        for successor in inheriting:
            issues=validate_frame_plans(package,shot_id=successor.shot_id)
            if issues: raise FramePlanError(issues[0])
            if any(value and value != asset_id for value in
                   (successor.frame_plan.start_asset_id, successor.approved_start_frame_asset_id)):
                raise ValueError(f'successor {successor.shot_id!r} names a different start frame')
    setattr(shot,APPROVAL_FIELD[kind],asset_id); asset.status='approved'; asset.authority='seeded'
    for successor in inheriting:
        bind_inherited_start_frame(package, successor.shot_id)
    if kind=='storyboard': shot.status='storyboard_ready'
    review=_record_review(asset,decision='approved',note=note,tags=tags,scores=scores)
    (telemetry or TelemetrySink()).emit('asset.approved',production_id=package.production_id,episode_id=package.episode_id,shot_id=shot_id,asset_id=asset_id,role=kind,review=review,attempt_id=asset.attempt_id,provider=asset.provider,model=asset.model)

def bind_inherited_start_frame(package: EpisodePackage, shot_id: str) -> str:
    """Reuse an approved immediate predecessor endpoint as this shot's start."""
    shot=package.find_shot(shot_id)
    if shot.frame_plan.mode!='start_and_end' or not shot.frame_plan.chain_from_shot_id:
        raise ValueError(f'shot {shot_id!r} has no start_and_end predecessor handoff')
    predecessor=predecessor_for(package,shot)
    issues=validate_frame_plans(package,shot_id=shot_id)
    if issues: raise FramePlanError(issues[0])
    endpoint=predecessor.approved_end_frame_asset_id
    if not endpoint:
        raise FramePlanError(FramePlanIssue(
            'CHAINED_START_ENDPOINT_MISSING',
            f"Shot {shot_id!r} waits for predecessor {predecessor.shot_id!r}'s approved end frame.",
            shot_id,predecessor.shot_id,
        ))
    package.find_asset(endpoint)
    if any(value and value != endpoint for value in
           (shot.frame_plan.start_asset_id,shot.approved_start_frame_asset_id)):
        raise ValueError(f'shot {shot_id!r} names a start frame different from predecessor endpoint {endpoint!r}')
    shot.approved_start_frame_asset_id=endpoint
    shot.frame_plan.start_asset_id=endpoint
    if endpoint not in shot.start_frame_asset_ids: shot.start_frame_asset_ids.append(endpoint)
    return endpoint

def reject_asset(package: EpisodePackage, shot_id: str, asset_id: str, telemetry: TelemetrySink|None=None, *, reason: str|None=None, tags: list[str]|None=None, scores: dict[str,float]|None=None) -> None:
    asset=package.find_asset(asset_id)
    if asset.shot_id and asset.shot_id != shot_id: raise ValueError('asset belongs to another shot')
    asset.status='rejected'
    review=_record_review(asset,decision='rejected',note=reason,tags=tags,scores=scores)
    (telemetry or TelemetrySink()).emit('asset.rejected',production_id=package.production_id,episode_id=package.episode_id,shot_id=shot_id,asset_id=asset_id,review=review,attempt_id=asset.attempt_id,provider=asset.provider,model=asset.model)

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
