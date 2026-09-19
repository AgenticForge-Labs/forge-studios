from __future__ import annotations
import time
from itertools import combinations
from uuid import uuid4
from ..contracts import AssetRecord, EpisodePackage, GenerationAttempt
from ..frame_plan import FramePlanError, FramePlanIssue, predecessor_for, validate_frame_plans
from ..providers.base import MediaProvider, MediaRequest, ProviderGenerationError
from ..telemetry import TelemetrySink

ROLE_KIND={'start_frame':'start_frame','end_frame':'end_frame','video':'generated_clip'}
ROLE_PROMPT={'start_frame':'start_frame_prompt','end_frame':'end_frame_prompt','video':'video_prompt'}

def assert_generation_preflight(package: EpisodePackage) -> None:
    """Keep every media entrypoint from spending work on a known blocked package."""
    report=package.trace.get('production_preflight') if isinstance(package.trace,dict) else None
    if package.status=='blocked' or (isinstance(report,dict) and report.get('is_valid') is False):
        raise ValueError('EpisodePackage has hard production-preflight errors; fix and revalidate it before media generation.')
    issues=validate_frame_plans(package)
    if issues:
        raise FramePlanError(issues[0])


def reference_isolation_plan(reference_asset_ids: list[str]) -> dict:
    ids=list(dict.fromkeys(reference_asset_ids)); steps=[]
    for asset_id in ids: steps.append({'phase':'single_reference','reference_asset_ids':[asset_id]})
    for left,right in combinations(ids,2): steps.append({'phase':'pairwise_reference','reference_asset_ids':[left,right]})
    return {'strategy':'single_then_pairwise','original_reference_asset_ids':ids,'steps':steps,'next_isolation_step':steps[0] if steps else None}


def failure_recovery_plan(failure_class: str|None, reference_asset_ids: list[str]) -> dict:
    if failure_class in {'timeout','transport'}:
        return {'action':'retry_same_request','reason':'transport/timeout failures should be retried unchanged before changing references','reference_asset_ids':list(reference_asset_ids)}
    isolation=reference_isolation_plan(reference_asset_ids)
    if isolation['next_isolation_step']:
        return {'action':'isolate_references','reason':'test supplied references individually, then pairwise, to distinguish prompt/image/pair failures',**isolation}
    return {'action':'inspect_prompt_or_provider_request','reason':'no reference images are available to isolate','reference_asset_ids':[]}


def _persist_generation_attempt(package: EpisodePackage, attempt: GenerationAttempt) -> None:
    if not isinstance(package.trace,dict): package.trace={}
    attempts=package.trace.setdefault('generation_attempts',[])
    if not isinstance(attempts,list): attempts=[]; package.trace['generation_attempts']=attempts
    record=attempt.model_dump(mode='json')
    for index in range(len(attempts)-1,-1,-1):
        prior=attempts[index]
        if isinstance(prior,dict) and prior.get('attempt_id')==attempt.attempt_id:
            attempts[index]=record; break
    else: attempts.append(record)
    package.trace['last_generation_attempt_id']=attempt.attempt_id
    if attempt.outcome=='failed': package.trace['last_generation_failure']=record


def _continuity_role(asset: AssetRecord) -> str:
    role=(asset.role or '').casefold(); tags={tag.casefold() for tag in asset.tags}; entity=(asset.entity_id or '').casefold(); generated=asset.authority=='generated' or asset.status=='approved'
    if 'route' in role or 'transit' in role or 'route' in tags or 'transit' in tags: return 'route_transit'
    if entity.startswith('character_') or 'character' in tags: return 'reusable_character_pose' if generated and asset.status!='canon' else 'canonical_identity'
    if entity.startswith('place_') or 'place' in tags: return 'reusable_site_view' if generated and asset.status!='canon' else 'canonical_site_geometry'
    return 'approved_reusable_reference' if asset.status=='approved' else 'canonical_reference'


def _validate_continuity_references(package: EpisodePackage, shot) -> list[AssetRecord]:
    assets=[]
    for asset_id in shot.reference_asset_ids:
        try: asset=package.find_asset(asset_id)
        except KeyError as exc: raise ValueError(f'Shot {shot.shot_id!r} reference {asset_id!r} is not present in the package.') from exc
        if asset.kind!='reference_image':
            raise ValueError(f'Shot {shot.shot_id!r} reference_asset_ids may contain only reusable reference_image assets; {asset_id!r} is {asset.kind!r}.')
        if asset.status not in {'canon','approved'}:
            raise ValueError(f'Shot {shot.shot_id!r} continuity reference {asset_id!r} has status {asset.status!r}; only canon or human-approved reusable references may be sent to a provider.')
        assets.append(asset)
    return assets


def _reference_input(package: EpisodePackage, shot, asset_id: str, *, start_id: str|None) -> dict:
    if start_id and asset_id == start_id:
        inherited=bool(shot.frame_plan.chain_from_shot_id)
        return {'asset_id':asset_id,'production_role':'approved_predecessor_endpoint' if inherited else 'approved_start_frame','use':'visual start anchor for identity, geometry, lighting, props and scale; preserve this starting composition while following the authored temporal prompt'}
    asset=package.find_asset(asset_id); production_role=_continuity_role(asset) if asset.kind=='reference_image' else f'production_{asset.kind}'; role_suffix=f' ({asset.role})' if asset.role else ''
    uses={
        'canonical_identity':'identity, anatomy, proportions, materials, colors, costume and distinctive design of the authored visible parts only; do not copy source pose/camera merely because the reference shows them',
        'reusable_character_pose':'approved prior character pose/view useful for continuity; preserve identity but follow current blocking and camera',
        'canonical_site_geometry':'site architecture, geometry, materials, scale and spatial relationships; compose only a view consistent with this geometry',
        'reusable_site_view':'approved prior site view useful for continuity; preserve established geometry and current camera-axis relationships',
        'route_transit':'approved route/transit geography and direction; use only when the route is visible',
        'approved_reusable_reference':'approved reusable design reference','canonical_reference':'canonical reusable design reference',
    }
    directed_use=(shot.reference_uses or {}).get(asset_id)
    use=(directed_use.strip() if isinstance(directed_use,str) and directed_use.strip() else uses.get(production_role,'production reference'))
    return {'asset_id':asset_id,'production_role':production_role,'entity_id':asset.entity_id,'catalog_role':asset.role,'use':use+role_suffix}


def _reference_inputs(package: EpisodePackage, shot, reference_ids: list[str], *, start_id: str|None) -> list[dict]:
    return [{'image':f'Image {index}',**_reference_input(package,shot,asset_id,start_id=start_id)} for index,asset_id in enumerate(reference_ids,1)]


def _boundary_inputs(shot, *, start_id: str|None, end_id: str|None) -> list[dict]:
    values=[]
    if start_id: values.append({'asset_id':start_id,'production_role':'approved_predecessor_endpoint' if shot.frame_plan.chain_from_shot_id else 'approved_start_frame','provider_role':'video_start_frame'})
    if end_id: values.append({'asset_id':end_id,'production_role':'approved_end_frame','provider_role':'video_end_frame'})
    return values


def _provider_generation_settings(provider) -> dict:
    getter=getattr(provider,'generation_settings',None)
    if not callable(getter): return {}
    value=getter(); return dict(value) if isinstance(value,dict) else {}


def _provider_model_for_request(provider, request: MediaRequest, role: str):
    resolver=getattr(provider,'model_for',None)
    if callable(resolver):
        try: return resolver(request)
        except Exception: pass
    return getattr(provider,'video_model' if role=='video' else 'image_model',None)


def _require_approved_video_boundaries(package: EpisodePackage, shot) -> None:
    if not shot.approved_start_frame_asset_id:
        raise ValueError(f'Shot {shot.shot_id!r} needs a human-approved start frame before video generation.')
    start=package.find_asset(shot.approved_start_frame_asset_id)
    if start.kind!='start_frame':
        raise ValueError(f'Shot {shot.shot_id!r} approved start boundary is {start.kind!r}, not a start_frame.')
    if shot.frame_plan.mode=='start_only':
        return
    if shot.frame_plan.mode=='start_and_end':
        if not shot.approved_end_frame_asset_id:
            raise ValueError(f'Shot {shot.shot_id!r} uses start_and_end and needs an approved end frame before video generation.')
        end=package.find_asset(shot.approved_end_frame_asset_id)
        if end.kind!='end_frame':
            raise ValueError(f'Shot {shot.shot_id!r} approved end boundary is {end.kind!r}, not an end_frame.')
        return
    raise ValueError(f'unsupported frame plan mode {shot.frame_plan.mode!r}')


class AnimatorService:
    def __init__(self, provider: MediaProvider, telemetry: TelemetrySink|None=None): self.provider=provider; self.telemetry=telemetry or TelemetrySink()
    def generate(self, package: EpisodePackage, shot_id: str, *, role: str='start_frame') -> list[AssetRecord]:
        assert_generation_preflight(package); shot=package.find_shot(shot_id)
        if role not in ROLE_KIND: raise ValueError(f'unknown generation role {role!r}')
        if role=='video' and shot.render_strategy != 'generated_video': raise ValueError('shot is not configured for generated video')
        if role=='end_frame' and shot.frame_plan.mode=='start_only':
            raise ValueError(f'Shot {shot_id!r} uses start_only; no end frame is requested for this production unit.')
        issues=validate_frame_plans(package,shot_id=shot_id)
        if issues: raise FramePlanError(issues[0])
        if role=='video': _require_approved_video_boundaries(package,shot)
        specific=getattr(shot,ROLE_PROMPT[role],None)
        if not isinstance(specific,str) or not specific.strip():
            raise ValueError(f'Shot {shot_id!r} has no authored {ROLE_PROMPT[role]}; Forge Studios executes package prompts and does not synthesize fallbacks.')
        prompt=specific.strip()
        continuity_assets=_validate_continuity_references(package,shot); reference_ids=[asset.asset_id for asset in continuity_assets]
        start_id=shot.approved_start_frame_asset_id or shot.frame_plan.start_asset_id; end_id=shot.approved_end_frame_asset_id or shot.frame_plan.end_asset_id
        if shot.frame_plan.mode=='start_and_end' and shot.frame_plan.chain_from_shot_id:
            if role=='start_frame': raise ValueError(f'Shot {shot_id!r} inherits its start frame from the predecessor; bind that endpoint instead of generating a new start frame.')
            issues=validate_frame_plans(package,require_approved_end_frames=True,shot_id=shot_id)
            if issues: raise FramePlanError(issues[0])
            previous=predecessor_for(package,shot); endpoint=previous.approved_end_frame_asset_id
            if shot.approved_start_frame_asset_id != endpoint:
                raise FramePlanError(FramePlanIssue('START_AND_END_INHERITED_START_NOT_BOUND',f"Shot {shot_id!r} must bind predecessor {previous.shot_id!r}'s approved endpoint as its start frame.",shot_id,previous.shot_id,endpoint))
            start_id=endpoint
        if role=='end_frame' and not shot.approved_start_frame_asset_id:
            raise ValueError(f'Shot {shot_id!r} needs an approved start frame before generating its end frame.')
        if role=='end_frame' and start_id and start_id not in reference_ids:
            reference_ids.append(start_id)
        reference_inputs=_reference_inputs(package,shot,reference_ids,start_id=start_id); boundary_inputs=_boundary_inputs(shot,start_id=start_id,end_id=end_id)
        refs=[self._asset_uri(package,a) for a in reference_ids]
        request=MediaRequest(kind='video' if role=='video' else 'image',shot_id=shot_id,role=role,prompt=prompt,reference_assets=tuple(refs),start_frame_asset=self._asset_uri(package,start_id) if start_id else None,end_frame_asset=self._asset_uri(package,end_id) if end_id else None,duration_seconds=shot.duration_seconds if role=='video' else None,options=dict(shot.provider_options))
        provider_generation=_provider_generation_settings(self.provider)
        shot_features={'duration_seconds':shot.duration_seconds,'purpose':shot.purpose,'visual':shot.visual,'entity_ids':shot.entity_ids,'camera':shot.camera,'visual_constraints':shot.visual_constraints,'performance_intent':shot.performance_intent,'edit_intent':shot.edit_intent,'execution_route':shot.execution_route,'render_strategy':shot.render_strategy,'frame_plan':shot.frame_plan.model_dump(mode='json'),'source_beat_ids':shot.source_beat_ids}
        attempt=GenerationAttempt(production_id=package.production_id,episode_id=package.episode_id,shot_id=shot_id,role=role,provider=self.provider.name,prompt=prompt,reference_asset_ids=reference_ids,options=shot.provider_options,metadata={'shot_features':shot_features,'reference_inputs':reference_inputs,'boundary_inputs':boundary_inputs,'provider_generation':provider_generation,'requested_duration_seconds':request.duration_seconds})
        _persist_generation_attempt(package,attempt); self.telemetry.emit('generation_attempt.started',**attempt.model_dump(mode='json')); started=time.perf_counter()
        try: results=self.provider.generate(request)
        except Exception as exc:
            diagnostics=exc.as_dict() if isinstance(exc,ProviderGenerationError) else {'provider':self.provider.name,'model':_provider_model_for_request(self.provider,request,role),'request_id':None,'failure_class':'provider_error'}
            failure_class=diagnostics.get('failure_class'); recovery=failure_recovery_plan(failure_class,reference_ids)
            diagnostics.update({'shot_id':shot_id,'role':role,'prompt':prompt,'reference_asset_ids':list(reference_ids),'reference_inputs':reference_inputs,'boundary_inputs':boundary_inputs,'provider_options':dict(shot.provider_options),'provider_generation':provider_generation,'requested_duration_seconds':request.duration_seconds,'recovery':recovery,'next_isolation_step':recovery.get('next_isolation_step')})
            attempt.model=diagnostics.get('model'); attempt.metadata['failure_diagnostics']=diagnostics
            if failure_class=='content_policy' or 'content_policy_violation' in str(exc):
                self.telemetry.emit('generation_prompt.rejected',production_id=package.production_id,episode_id=package.episode_id,shot_id=shot_id,role=role,provider=self.provider.name,code='PROVIDER_CONTENT_POLICY_REJECTION',request_id=diagnostics.get('request_id'),reference_asset_ids=list(reference_ids),reference_inputs=reference_inputs,next_isolation_step=recovery.get('next_isolation_step'),guidance='Do not rewrite the story automatically. Diagnose prompt versus reference-image false positives using the recorded isolation plan.')
                exc.add_note('Provider content-policy rejection: use failure_diagnostics.recovery to isolate prompt/reference causes; Forge Studios does not use an LLM to rewrite prompts.')
            attempt.outcome='failed'; attempt.error=str(exc); attempt.latency_ms=(time.perf_counter()-started)*1000; _persist_generation_attempt(package,attempt); self.telemetry.emit('generation_attempt.failed',**attempt.model_dump(mode='json')); raise
        assets=[]
        for result in results:
            source_ids=list(dict.fromkeys(reference_ids + ([start_id] if start_id else []) + ([end_id] if end_id else []))); metadata=dict(result.metadata)
            metadata['generation']={'attempt_id':attempt.attempt_id,'role':role,'prompt':prompt,'provider':result.provider,'model':result.model,'mode':metadata.get('generation_mode') or provider_generation.get('mode'),'options':dict(shot.provider_options),'provider_profile':provider_generation,'provider_settings':metadata.get('provider_settings'),'requested_duration_seconds':request.duration_seconds,'actual_media':metadata.get('actual_media'),'reference_asset_ids':list(reference_ids),'reference_inputs':reference_inputs,'boundary_inputs':boundary_inputs,'start_asset_id':start_id,'end_asset_id':end_id,'shot_features':shot_features}
            asset=AssetRecord(asset_id=f'asset_{uuid4().hex}',kind=ROLE_KIND[role],uri=result.uri,status='candidate',authority='generated',episode_id=package.episode_id,shot_id=shot_id,attempt_id=attempt.attempt_id,provider=result.provider,model=result.model,source_asset_ids=source_ids,metadata=metadata)
            package.assets.append(asset); assets.append(asset); target={'start_frame':'start_frame_asset_ids','end_frame':'end_frame_asset_ids','video':'candidate_clip_asset_ids'}[role]; getattr(shot,target).append(asset.asset_id)
            self.telemetry.emit('asset.generated',production_id=package.production_id,episode_id=package.episode_id,shot_id=shot_id,attempt_id=attempt.attempt_id,asset_id=asset.asset_id,role=role,kind=asset.kind,provider=asset.provider,model=asset.model,source_asset_ids=source_ids,prompt=prompt,options=shot.provider_options,reference_inputs=reference_inputs,boundary_inputs=boundary_inputs,shot_features=shot_features,generation_mode=metadata['generation'].get('mode'),provider_settings=metadata.get('provider_settings'),actual_media=metadata.get('actual_media'))
        attempt.outcome='succeeded'; attempt.asset_ids=[a.asset_id for a in assets]; attempt.model=assets[0].model if assets else None; attempt.latency_ms=(time.perf_counter()-started)*1000
        if assets: attempt.metadata['actual_generation']={'mode':assets[0].metadata.get('generation',{}).get('mode'),'model':assets[0].model,'provider_settings':assets[0].metadata.get('provider_settings'),'actual_media':assets[0].metadata.get('actual_media')}
        _persist_generation_attempt(package,attempt); self.telemetry.emit('generation_attempt.succeeded',**attempt.model_dump(mode='json')); return assets
    @staticmethod
    def _asset_uri(package: EpisodePackage, asset_id: str|None) -> str:
        if not asset_id: raise KeyError('missing asset id')
        return package.find_asset(asset_id).uri
