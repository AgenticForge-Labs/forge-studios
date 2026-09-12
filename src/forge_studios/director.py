from __future__ import annotations
from dataclasses import dataclass
from .contracts import EpisodePackage

@dataclass
class WorkItem:
    shot_id: str
    action: str
    reason: str
    depends_on: tuple[str,...]=()

def plan_work(package: EpisodePackage) -> list[WorkItem]:
    work=[]
    for scene in package.scenes:
        for shot in scene.shots:
            if not shot.approved_storyboard_asset_id:
                work.append(WorkItem(shot.shot_id,'generate_storyboard','No approved storyboard still')); continue
            if shot.execution_route in {'animator','hybrid'}:
                if shot.frame_plan.mode in {'start_only','start_and_end'} and not shot.approved_start_frame_asset_id:
                    work.append(WorkItem(shot.shot_id,'generate_start_frame','Video boundary frame required'))
                if shot.frame_plan.mode=='start_and_end' and not shot.approved_end_frame_asset_id:
                    work.append(WorkItem(shot.shot_id,'generate_end_frame','Destination composition matters'))
                if shot.render_strategy in {'generated_video','hybrid'} and not shot.approved_clip_asset_id:
                    deps=[]
                    if shot.frame_plan.mode in {'start_only','start_and_end'}: deps.append('approved_start_frame')
                    if shot.frame_plan.mode=='start_and_end': deps.append('approved_end_frame')
                    work.append(WorkItem(shot.shot_id,'generate_video','Synthetic motion required',tuple(deps)))
            if shot.execution_route in {'puppeteer','hybrid'} and not shot.approved_take_id:
                work.append(WorkItem(shot.shot_id,'capture_physical_take','Physical route selected'))
    return work
