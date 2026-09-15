from __future__ import annotations
from dataclasses import dataclass
from typing import Callable

from .animator import AnimatorService, assert_generation_preflight
from .contracts import EpisodePackage
from .frame_plan import FramePlanError, predecessor_for, validate_frame_plans
from .package_ops import approve_asset, bind_inherited_start_frame
from .telemetry import TelemetrySink

@dataclass
class WorkItem:
    shot_id: str
    action: str
    reason: str
    depends_on: tuple[str,...]=()
    predecessor_shot_id: str|None = None
    required_asset_id: str|None = None

@dataclass
class AutonomyPolicy:
    auto_approve_storyboards: bool = False
    auto_approve_frames: bool = False
    auto_approve_clips: bool = False
    auto_approve_takes: bool = False
    allow_generated_video: bool = False
    allow_physical_execution: bool = False
    max_actions: int = 100

@dataclass
class DirectorResult:
    status: str
    actions_completed: int
    next_work: WorkItem | None = None


def plan_work(package: EpisodePackage) -> list[WorkItem]:
    """Derive executable/review work from the package without changing narrative order."""
    work=[]
    chain_predecessors={}
    for candidate in [shot for scene in package.scenes for shot in scene.shots]:
        if candidate.frame_plan.mode=='chained_start' or (candidate.frame_plan.mode=='start_and_end' and candidate.frame_plan.chain_from_shot_id):
            try:
                predecessor=predecessor_for(package,candidate)
            except FramePlanError:
                continue
            chain_predecessors.setdefault(predecessor.shot_id,[]).append(candidate.shot_id)
    for scene in package.scenes:
        for shot in scene.shots:
            if not shot.approved_storyboard_asset_id:
                if shot.storyboard_asset_ids:
                    work.append(WorkItem(shot.shot_id,'review_storyboard','Storyboard candidates await approval'))
                else:
                    work.append(WorkItem(shot.shot_id,'generate_storyboard','No storyboard candidate exists'))
                continue

            if shot.execution_route in {'animator','hybrid'}:
                chain_predecessor = None
                if shot.frame_plan.mode=='chained_start' or (shot.frame_plan.mode=='start_and_end' and shot.frame_plan.chain_from_shot_id):
                    issues=validate_frame_plans(package,shot_id=shot.shot_id)
                    if issues:
                        work.append(WorkItem(shot.shot_id,'blocked',issues[0].message))
                        continue
                    try:
                        chain_predecessor=predecessor_for(package,shot)
                    except FramePlanError as exc:
                        work.append(WorkItem(shot.shot_id,'blocked',exc.issue.message))
                        continue
                    if not chain_predecessor.approved_end_frame_asset_id:
                        work.append(WorkItem(shot.shot_id,'blocked',f"Waiting for approved end frame from predecessor {chain_predecessor.shot_id!r}",('approved_end_frame:'+chain_predecessor.shot_id,),chain_predecessor.shot_id))
                        continue
                    if shot.frame_plan.mode=='start_and_end' and shot.approved_start_frame_asset_id != chain_predecessor.approved_end_frame_asset_id:
                        work.append(WorkItem(
                            shot.shot_id,'bind_inherited_start_frame',
                            f"Bind approved endpoint of predecessor {chain_predecessor.shot_id!r} as this shot's start frame",
                            (f'approved_end_frame:{chain_predecessor.shot_id}:{chain_predecessor.approved_end_frame_asset_id}',),
                            chain_predecessor.shot_id,chain_predecessor.approved_end_frame_asset_id,
                        ))
                        continue
                if shot.frame_plan.mode in {'start_only','start_and_end'} and not shot.approved_start_frame_asset_id:
                    if shot.start_frame_asset_ids:
                        work.append(WorkItem(shot.shot_id,'review_start_frame','Start-frame candidates await approval'))
                    else:
                        work.append(WorkItem(shot.shot_id,'generate_start_frame','Approved video start frame required'))
                    continue
                if shot.frame_plan.mode=='start_and_end' and not shot.approved_end_frame_asset_id:
                    if shot.end_frame_asset_ids:
                        work.append(WorkItem(shot.shot_id,'review_end_frame','End-frame candidates await approval'))
                    else:
                        work.append(WorkItem(shot.shot_id,'generate_end_frame','Approved destination frame required'))
                    continue
                if shot.shot_id in chain_predecessors and not shot.approved_end_frame_asset_id:
                    if shot.end_frame_asset_ids:
                        work.append(WorkItem(shot.shot_id,'review_end_frame','Approved end frame required by chained successor',('chained_successor_endpoint',)))
                    else:
                        work.append(WorkItem(shot.shot_id,'generate_end_frame','Approved end frame required by chained successor',('chained_successor_endpoint',)))
                    continue
                if shot.render_strategy in {'generated_video','hybrid'} and not shot.approved_clip_asset_id:
                    if shot.candidate_clip_asset_ids:
                        work.append(WorkItem(shot.shot_id,'review_clip','Video candidates await approval'))
                    else:
                        deps=[]
                        predecessor_id=None; required_asset_id=None
                        if chain_predecessor:
                            predecessor_id=chain_predecessor.shot_id
                            required_asset_id=chain_predecessor.approved_end_frame_asset_id
                            deps.append(f'approved_end_frame:{predecessor_id}:{required_asset_id}')
                        if shot.frame_plan.mode in {'start_only','start_and_end'}: deps.append('approved_start_frame')
                        if shot.frame_plan.mode=='start_and_end': deps.append('approved_end_frame')
                        work.append(WorkItem(shot.shot_id,'generate_video','Synthetic motion required',tuple(deps),predecessor_id,required_asset_id))
                    continue

            if shot.execution_route in {'puppeteer','hybrid'} and not shot.approved_take_id:
                if shot.physical_take_ids:
                    work.append(WorkItem(shot.shot_id,'review_take','Physical takes await approval'))
                else:
                    work.append(WorkItem(shot.shot_id,'capture_physical_take','Physical route selected'))
                continue
    return work


class DirectorService:
    """Run the same shot primitives manually or under explicit autonomy policy.

    The Director never invents a replacement story. It may generate/retry/approve only
    where policy explicitly permits it. Otherwise it stops at a review or permission
    boundary and returns the next work item to the human/Codex caller.
    """

    def __init__(
        self,
        animator: AnimatorService,
        *,
        telemetry: TelemetrySink|None=None,
        physical_executor: Callable[[EpisodePackage,str],str]|None=None,
    ):
        self.animator=animator
        self.telemetry=telemetry or TelemetrySink()
        self.physical_executor=physical_executor

    def run_until_blocked(self, package: EpisodePackage, policy: AutonomyPolicy|None=None) -> DirectorResult:
        try:
            assert_generation_preflight(package)
        except ValueError as exc:
            return DirectorResult('blocked',0,WorkItem('', 'blocked', str(exc)))
        policy=policy or AutonomyPolicy()
        completed=0
        while completed < policy.max_actions:
            work=plan_work(package)
            if not work:
                self.telemetry.emit('director.complete',production_id=package.production_id,episode_id=package.episode_id,actions_completed=completed)
                return DirectorResult('complete',completed,None)
            item=work[0]
            shot=package.find_shot(item.shot_id)

            if item.action=='generate_storyboard':
                self.animator.generate(package,item.shot_id,role='storyboard'); completed += 1; continue
            if item.action=='review_storyboard':
                if not policy.auto_approve_storyboards: return DirectorResult('awaiting_review',completed,item)
                approve_asset(package,item.shot_id,'storyboard',shot.storyboard_asset_ids[-1],self.telemetry); completed += 1; continue
            if item.action=='generate_start_frame':
                self.animator.generate(package,item.shot_id,role='start_frame'); completed += 1; continue
            if item.action=='bind_inherited_start_frame':
                bind_inherited_start_frame(package,item.shot_id); completed += 1; continue
            if item.action=='review_start_frame':
                if not policy.auto_approve_frames: return DirectorResult('awaiting_review',completed,item)
                approve_asset(package,item.shot_id,'start_frame',shot.start_frame_asset_ids[-1],self.telemetry); completed += 1; continue
            if item.action=='generate_end_frame':
                self.animator.generate(package,item.shot_id,role='end_frame'); completed += 1; continue
            if item.action=='review_end_frame':
                if not policy.auto_approve_frames: return DirectorResult('awaiting_review',completed,item)
                approve_asset(package,item.shot_id,'end_frame',shot.end_frame_asset_ids[-1],self.telemetry); completed += 1; continue
            if item.action=='generate_video':
                if not policy.allow_generated_video: return DirectorResult('permission_required',completed,item)
                self.animator.generate(package,item.shot_id,role='video'); completed += 1; continue
            if item.action=='review_clip':
                if not policy.auto_approve_clips: return DirectorResult('awaiting_review',completed,item)
                approve_asset(package,item.shot_id,'clip',shot.candidate_clip_asset_ids[-1],self.telemetry); completed += 1; continue
            if item.action=='capture_physical_take':
                if not policy.allow_physical_execution or self.physical_executor is None:
                    return DirectorResult('permission_required',completed,item)
                self.physical_executor(package,item.shot_id); completed += 1; continue
            if item.action=='review_take':
                if not policy.auto_approve_takes: return DirectorResult('awaiting_review',completed,item)
                approve_asset(package,item.shot_id,'take',shot.physical_take_ids[-1],self.telemetry); completed += 1; continue
            return DirectorResult('blocked',completed,item)
        work=plan_work(package)
        return DirectorResult('action_limit',completed,work[0] if work else None)
