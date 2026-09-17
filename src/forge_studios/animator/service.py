from __future__ import annotations
import json, re, time
from itertools import combinations
from uuid import uuid4
from ..contracts import AssetRecord, EpisodePackage, GenerationAttempt
from ..frame_plan import FramePlanError, FramePlanIssue, predecessor_for, validate_frame_plans
from ..providers.base import MediaProvider, MediaRequest, ProviderGenerationError
from ..telemetry import TelemetrySink

ROLE_KIND={'storyboard':'storyboard_image','start_frame':'start_frame','end_frame':'end_frame','video':'generated_clip'}
ROLE_PROMPT={'storyboard':'storyboard_prompt','start_frame':'start_frame_prompt','end_frame':'end_frame_prompt','video':'video_prompt'}

def boundary_camera(shot, role: str) -> dict:
    """Compile authored static intent; never invent a destination camera pose."""
    camera = dict(shot.camera)
    specific = camera.pop(role, None) if role in {'start_frame','end_frame'} else None
    camera.pop('start_frame', None)
    camera.pop('end_frame', None)
    if role in {'start_frame','end_frame'}:
        camera.pop('movement', None)
        if isinstance(specific, dict):
            # Endpoint objects are complete static intent, not additions to a
            # possibly contradictory shot-wide tracking composition.
            return {**({'viewpoint': camera['viewpoint']} if 'viewpoint' in camera else {}), **specific}
        # Legacy prompts already carry the boundary composition. Repeating the
        # temporal shot composition here can turn a grounded pose into a jump.
        return {key: value for key, value in camera.items() if key in {'viewpoint','axis'}}
    return camera

def _camera_summary(shot, role: str='storyboard') -> str:
    camera = boundary_camera(shot, role)
    return '; '.join(
        f'{key}: {camera[key]}' for key in ('shot_type','framing','distance','axis','composition')
        if isinstance(camera.get(key),str) and camera[key].strip()
    )

def _constraint_values(shot, key: str) -> list[str]:
    constraints=shot.visual_constraints if isinstance(shot.visual_constraints,dict) else {}
    values=constraints.get(key)
    if not isinstance(values,list): return []
    return [str(value).strip() for value in values if str(value).strip()]

def production_prompt_suffix(shot, role: str) -> str:
    """Add deterministic EpisodePackage grounding without creatively rewriting the shot."""
    lines=[]
    camera=_camera_summary(shot, role)
    if camera: lines.append(f'Camera must preserve: {camera}.')
    must_show=_constraint_values(shot,'must_show')
    if must_show: lines.append('Required visible elements: '+'; '.join(must_show)+'.')
    must_not_show=_constraint_values(shot,'must_not_show')
    if must_not_show: lines.append('Forbidden additions or substitutions: '+'; '.join(must_not_show)+'.')
    if shot.continuity_asset_ids:
        lines.append('Treat supplied reusable references according to their explicit production roles; identity/site design references do not dictate the source pose or camera angle.')
    if role=='storyboard':
        lines.append('Depict this shot\'s specific settled state and composition, not a generic character portrait or unrelated establishing view.')
    if not lines: return ''
    return '\n\nProduction constraints:\n'+'\n'.join(f'- {line}' for line in lines)

def storyboard_fallback_prompt(shot) -> str:
    """Legacy planning-still fallback retained for old packages only."""
    ending=getattr(shot,'end_frame_prompt',None)
    blocking=shot.performance_intent.get('blocking') or []
    if not isinstance(blocking,list): blocking=[]
    state=(ending.strip() if isinstance(ending,str) and ending.strip()
           else blocking[-1] if blocking else shot.image_prompt or shot.visual)
    camera=_camera_summary(shot)
    parts=[
        'Create one representative planning still, not a collage or motion-blurred sequence.',
        f'Representative settled state and pose: {state}',
    ]
    if camera: parts.append(f'Camera composition: {camera}')
    parts.append('Use canonical character references for identity, anatomy, costume, materials, and style, not their default pose or camera angle. Repose visible four-legged characters for this shot with grounded paws; preserve established location geometry.')
    prompt='\n\n'.join(parts)
    return re.sub(r'\bunconscious\b','resting peacefully',prompt,flags=re.IGNORECASE)

def assert_generation_preflight(package: EpisodePackage) -> None:
    """Keep every media entrypoint from spending work on a known blocked package."""
    report=package.trace.get('production_preflight') if isinstance(package.trace,dict) else None
    if package.status=='blocked' or (isinstance(report,dict) and report.get('is_valid') is False):
        raise ValueError('EpisodePackage has hard production-preflight errors; fix and revalidate it before media generation.')
    issues=validate_frame_plans(package)
    if issues:
        issue=issues[0]
        raise FramePlanError(issue)


def reference_isolation_plan(reference_asset_ids: list[str]) -> dict:
    ids=list(dict.fromkeys(reference_asset_ids))
    steps=[]
    for asset_id in ids:
        steps.append({'phase':'single_reference','reference_asset_ids':[asset_id]})
    for left,right in combinations(ids,2):
        steps.append({'phase':'pairwise_reference','reference_asset_ids':[left,right]})
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
    for asset_id in shot.continuity_asset_ids:
        try: asset=package.find_asset(asset_id)
        except KeyError as exc: raise ValueError(f'Shot {shot.shot_id!r} continuity reference {asset_id!r} is not present in the package.') from exc
        if asset.kind!='reference_image':
            raise ValueError(f'Shot {shot.shot_id!r} continuity_asset_ids may contain only reusable reference_image assets; {asset_id!r} is {asset.kind!r}. Use approved_storyboard_asset_id for legacy composition guides and frame-plan/approved start/end fields for boundary images.')
        if asset.status not in {'canon','approved'}:
            raise ValueError(f'Shot {shot.shot_id!r} continuity reference {asset_id!r} has status {asset.status!r}; only canon or human-approved reusable references may be sent to a provider.')
        assets.append(asset)
    return assets


def _reference_input(package: EpisodePackage, shot, asset_id: str, *, start_id: str|None) -> dict:
    if asset_id == shot.approved_storyboard_asset_id:
        return {'asset_id':asset_id,'production_role':'storyboard_composition','use':'approved planning/composition guide; use layout only, while reusable identity/site references remain design authority'}
    if start_id and asset_id == start_id:
        inherited=bool(shot.frame_plan.chain_from_shot_id)
        return {'asset_id':asset_id,'production_role':'approved_predecessor_endpoint' if inherited else 'approved_start_frame','use':'boundary continuity evidence for identity, geometry, lighting, props and scale; follow the authored destination state and camera, not the source pose or crop'}
    asset=package.find_asset(asset_id); production_role=_continuity_role(asset) if asset.kind=='reference_image' else f'production_{asset.kind}'; role_suffix=f' ({asset.role})' if asset.role else ''
    uses={
        'canonical_identity':'identity, anatomy, proportions, materials, colors, costume and distinctive design of the authored visible parts only; do not add a face/full body or copy source pose/camera merely because the reference shows them',
        'reusable_character_pose':'approved prior character pose/view useful for continuity; preserve identity but follow current blocking and camera',
        'canonical_site_geometry':'site architecture, geometry, materials, scale and spatial relationships; compose only a view consistent with this geometry',
        'reusable_site_view':'approved prior site view useful for continuity; preserve established geometry and current camera-axis relationships',
        'route_transit':'approved route/transit geography and direction; use only when the route is visible',
        'approved_reusable_reference':'approved reusable design reference','canonical_reference':'canonical reusable design reference',
    }
    return {'asset_id':asset_id,'production_role':production_role,'entity_id':asset.entity_id,'catalog_role':asset.role,'use':uses.get(production_role,'production reference')+role_suffix}


def _reference_inputs(package: EpisodePackage, shot, reference_ids: list[str], *, start_id: str|None) -> list[dict]:
    return [{'image':f'Image {index}',**_reference_input(package,shot,asset_id,start_id=start_id)} for index,asset_id in enumerate(reference_ids,1)]


def _boundary_inputs(shot, *, start_id: str|None, end_id: str|None) -> list[dict]:
    values=[]
    if start_id: values.append({'asset_id':start_id,'production_role':'approved_predecessor_endpoint' if shot.frame_plan.chain_from_shot_id else 'approved_start_frame','provider_role':'video_start_frame'})
    if end_id: values.append({'asset_id':end_id,'production_role':'approved_end_frame','provider_role':'video_end_frame'})
    return values


def structured_image_prompt(package: EpisodePackage, shot, role: str, instruction: str, reference_ids: list[str], *, start_id: str|None=None) -> str:
    """Compile provider input from already-safe production fields, never raw story prose."""
    allow_text=bool((shot.provider_options or {}).get('allow_text'))
    output=('one clean 16:9 frame; no collage, borders, or motion blur' if allow_text else 'one clean 16:9 frame; no collage, labels, captions, subtitles, speech bubbles, readable text, letters, numbers, logos, watermarks, UI, borders, or motion blur')
    payload={
        'task':'generate one production image','frame_role':role,'instruction':instruction,'camera':boundary_camera(shot, role),
        'composition_constraints':{'must_show':_constraint_values(shot,'must_show'),'must_not_show':_constraint_values(shot,'must_not_show')},
        'reference_images':_reference_inputs(package,shot,reference_ids,start_id=start_id),
        'continuity':{'preserve_character_identity':True,'preserve_established_site_geometry':True,'preserve_scale_materials_lighting_and_fixed_props':True,'reference_images_are_design_authority_not_default_pose':True},
        'output':output,
    }
    return json.dumps(payload,ensure_ascii=False,indent=2)


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
    if shot.frame_plan.mode!='start_and_end': return
    if not shot.approved_start_frame_asset_id or not shot.approved_end_frame_asset_id:
        raise ValueError(f'Shot {shot.shot_id!r} needs human-approved start and end boundary frames before video generation.')
    start=package.find_asset(shot.approved_start_frame_asset_id); end=package.find_asset(shot.approved_end_frame_asset_id)
    if start.kind not in {'start_frame','end_frame'}: raise ValueError(f'Shot {shot.shot_id!r} approved start boundary is {start.kind!r}, not a generated boundary frame.')
    if end.kind!='end_frame': raise ValueError(f'Shot {shot.shot_id!r} approved end boundary is {end.kind!r}, not an end_frame.')


class AnimatorService:
    def __init__(self, provider: MediaProvider, telemetry: TelemetrySink|None=None): self.provider=provider; self.telemetry=telemetry or TelemetrySink()
    def generate(self, package: EpisodePackage, shot_id: str, *, role: str='storyboard') -> list[AssetRecord]:
        assert_generation_preflight(package); shot=package.find_shot(shot_id)
        if role not in ROLE_KIND: raise ValueError(f'unknown generation role {role!r}')
        if role=='video' and shot.render_strategy != 'generated_video': raise ValueError('shot is not configured for generated video')
        if role!='storyboard':
            issues=validate_frame_plans(package,shot_id=shot_id)
            if issues: raise FramePlanError(issues[0])
        if role=='video': _require_approved_video_boundaries(package,shot)
        specific=getattr(shot,ROLE_PROMPT[role],None)
        prompt=(specific or storyboard_fallback_prompt(shot)) if role=='storyboard' else (specific or (shot.image_prompt if role!='video' else None) or self._default_prompt(shot,role))
        if role=='storyboard' and specific and isinstance(shot.image_prompt,str) and shot.image_prompt.strip():
            image_anchor=shot.image_prompt.strip()
            if image_anchor not in prompt: prompt += '\n\nVisual design anchor from the EpisodePackage: '+image_anchor
        continuity_assets=_validate_continuity_references(package,shot); reference_ids=[asset.asset_id for asset in continuity_assets]
        start_id=shot.approved_start_frame_asset_id or shot.frame_plan.start_asset_id; end_id=shot.approved_end_frame_asset_id or shot.frame_plan.end_asset_id
        if shot.frame_plan.mode=='chained_start':
            if role=='video':
                issues=validate_frame_plans(package,require_approved_end_frames=True,shot_id=shot_id)
                if issues: raise FramePlanError(issues[0])
            previous=predecessor_for(package,shot); start_id=previous.approved_end_frame_asset_id
        if shot.frame_plan.mode=='start_and_end' and shot.frame_plan.chain_from_shot_id and role!='storyboard':
            if role=='start_frame': raise ValueError(f'Shot {shot_id!r} inherits its start frame from the predecessor; bind that endpoint instead of generating a new start frame.')
            issues=validate_frame_plans(package,require_approved_end_frames=True,shot_id=shot_id)
            if issues: raise FramePlanError(issues[0])
            previous=predecessor_for(package,shot); endpoint=previous.approved_end_frame_asset_id
            if shot.approved_start_frame_asset_id != endpoint:
                raise FramePlanError(FramePlanIssue('START_AND_END_INHERITED_START_NOT_BOUND',f"Shot {shot_id!r} must bind predecessor {previous.shot_id!r}'s approved endpoint as its start frame.",shot_id,previous.shot_id,endpoint))
            start_id=endpoint
        if role=='start_frame' and shot.approved_storyboard_asset_id:
            storyboard_id=shot.approved_storyboard_asset_id
            if storyboard_id not in reference_ids:
                reference_ids.append(storyboard_id)
                prompt += f"\n\nReference image {len(reference_ids)} is this shot's approved storyboard. Keep its composition and spatial layout; reusable identity/site references still fix design."
        if role=='end_frame' and shot.frame_plan.mode=='start_and_end' and not shot.approved_start_frame_asset_id:
            raise ValueError(f'Shot {shot_id!r} needs an approved start frame before generating its end frame.')
        if role=='end_frame' and start_id and start_id not in reference_ids:
            reference_ids.append(start_id)
        # Image requests already carry structured camera, constraints and
        # reference roles. Duplicating them in instruction can overpower the
        # unique endpoint state. Video retains its temporal grounding suffix.
        if role == 'video':
            prompt += production_prompt_suffix(shot,role)
        reference_inputs=_reference_inputs(package,shot,reference_ids,start_id=start_id); boundary_inputs=_boundary_inputs(shot,start_id=start_id,end_id=end_id)
        if role!='video': prompt=structured_image_prompt(package,shot,role,prompt,reference_ids,start_id=start_id)
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
            package.assets.append(asset); assets.append(asset); target={'storyboard':'storyboard_asset_ids','start_frame':'start_frame_asset_ids','end_frame':'end_frame_asset_ids','video':'candidate_clip_asset_ids'}[role]; getattr(shot,target).append(asset.asset_id)
            self.telemetry.emit('asset.generated',production_id=package.production_id,episode_id=package.episode_id,shot_id=shot_id,attempt_id=attempt.attempt_id,asset_id=asset.asset_id,role=role,kind=asset.kind,provider=asset.provider,model=asset.model,source_asset_ids=source_ids,prompt=prompt,options=shot.provider_options,reference_inputs=reference_inputs,boundary_inputs=boundary_inputs,shot_features=shot_features,generation_mode=metadata['generation'].get('mode'),provider_settings=metadata.get('provider_settings'),actual_media=metadata.get('actual_media'))
        attempt.outcome='succeeded'; attempt.asset_ids=[a.asset_id for a in assets]; attempt.model=assets[0].model if assets else None; attempt.latency_ms=(time.perf_counter()-started)*1000
        if assets: attempt.metadata['actual_generation']={'mode':assets[0].metadata.get('generation',{}).get('mode'),'model':assets[0].model,'provider_settings':assets[0].metadata.get('provider_settings'),'actual_media':assets[0].metadata.get('actual_media')}
        _persist_generation_attempt(package,attempt); self.telemetry.emit('generation_attempt.succeeded',**attempt.model_dump(mode='json')); return assets
    @staticmethod
    def _asset_uri(package: EpisodePackage, asset_id: str|None) -> str:
        if not asset_id: raise KeyError('missing asset id')
        return package.find_asset(asset_id).uri
    @staticmethod
    def _default_prompt(shot, role: str) -> str:
        camera='Camera: '+json.dumps(shot.camera,ensure_ascii=False) if shot.camera else ''; constraints='Visual constraints: '+json.dumps(shot.visual_constraints,ensure_ascii=False) if shot.visual_constraints else ''; parts=[value for value in (camera,constraints) if value]
        if role=='video': parts.append('Describe only temporal change and preserve supplied start/end frames and reusable design references.')
        elif role=='start_frame': parts.append('Create the exact settled starting composition for the shot from the structured production constraints and references.')
        elif role=='end_frame': parts.append('Create the exact settled destination composition for the shot from the structured production constraints and references.')
        else: parts.append('Create the requested production planning image from structured references and constraints.')
        return '\n\n'.join(parts)
