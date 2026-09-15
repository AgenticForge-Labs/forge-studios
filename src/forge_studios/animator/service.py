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

def _camera_summary(shot) -> str:
    return '; '.join(
        f'{key}: {shot.camera[key]}' for key in ('shot_type','framing','axis','composition')
        if isinstance(shot.camera.get(key),str) and shot.camera[key].strip()
    )

def _constraint_values(shot, key: str) -> list[str]:
    constraints=shot.visual_constraints if isinstance(shot.visual_constraints,dict) else {}
    values=constraints.get(key)
    if not isinstance(values,list): return []
    return [str(value).strip() for value in values if str(value).strip()]

def production_prompt_suffix(shot, role: str) -> str:
    """Add deterministic EpisodePackage grounding without creatively rewriting the shot."""
    lines=[]
    camera=_camera_summary(shot)
    if camera: lines.append(f'Camera must preserve: {camera}.')
    must_show=_constraint_values(shot,'must_show')
    if must_show: lines.append('Required visible elements: '+'; '.join(must_show)+'.')
    must_not_show=_constraint_values(shot,'must_not_show')
    if must_not_show: lines.append('Forbidden additions or substitutions: '+'; '.join(must_not_show)+'.')
    if shot.continuity_asset_ids:
        lines.append('Treat the supplied canonical references as authoritative for character identity and established site geometry; do not replace established architecture with a generic structure.')
    if role=='storyboard':
        lines.append('Depict this shot\'s specific settled state and composition, not a generic character portrait or unrelated establishing view.')
    if not lines: return ''
    return '\n\nProduction constraints:\n'+'\n'.join(f'- {line}' for line in lines)

def storyboard_fallback_prompt(shot) -> str:
    """Use shot action and its settled result when no hand-authored board prompt exists."""
    ending=getattr(shot,'end_frame_prompt',None) if shot.render_strategy in {'generated_video','hybrid'} else None
    blocking=shot.performance_intent.get('blocking') or []
    if not isinstance(blocking,list): blocking=[]
    state=(ending.strip() if isinstance(ending,str) and ending.strip()
           else blocking[-1] if blocking else shot.image_prompt or shot.visual)
    camera=_camera_summary(shot)
    parts=[
        'Create one representative storyboard still, not a collage or motion-blurred sequence. '
        'It may show a stable result of the movement rather than movement itself.',
        f'Representative settled state and pose: {state}',
        f'Shot context: {shot.visual}',
    ]
    if camera: parts.append(f'Camera composition: {camera}')
    parts.append('Use canonical character references for identity, anatomy, costume, materials, and style, '
                 'not their default pose or camera angle. Repose visible four-legged characters for this shot '
                 'with grounded paws; preserve the established location geometry.')
    prompt='\n\n'.join(parts)
    return re.sub(r'\bunconscious\b','resting peacefully with eyes closed',prompt,flags=re.IGNORECASE)

def assert_generation_preflight(package: EpisodePackage) -> None:
    """Keep every media entrypoint from spending work on a known blocked package."""
    report=package.trace.get('production_preflight') if isinstance(package.trace,dict) else None
    if package.status=='blocked' or (isinstance(report,dict) and report.get('is_valid') is False):
        raise ValueError('EpisodePackage has hard production-preflight errors; fix and revalidate it before media generation.')


def reference_isolation_plan(reference_asset_ids: list[str]) -> dict:
    """Build a deterministic single-then-pairwise plan for diagnosing bad reference combinations.

    The plan is diagnostic metadata only; Studios never spends additional provider
    calls automatically. It gives the operator an exact next subset to try while
    keeping the original reference ordering visible.
    """
    ids=list(dict.fromkeys(reference_asset_ids))
    steps=[]
    for asset_id in ids:
        steps.append({'phase':'single_reference','reference_asset_ids':[asset_id]})
    for left,right in combinations(ids,2):
        steps.append({'phase':'pairwise_reference','reference_asset_ids':[left,right]})
    return {
        'strategy':'single_then_pairwise',
        'original_reference_asset_ids':ids,
        'steps':steps,
        'next_isolation_step':steps[0] if steps else None,
    }


def failure_recovery_plan(failure_class: str|None, reference_asset_ids: list[str]) -> dict:
    """Choose the next deterministic recovery action without silently retrying."""
    if failure_class in {'timeout','transport'}:
        return {
            'action':'retry_same_request',
            'reason':'transport/timeout failures should be retried unchanged before changing references',
            'reference_asset_ids':list(reference_asset_ids),
        }
    isolation=reference_isolation_plan(reference_asset_ids)
    if isolation['next_isolation_step']:
        return {
            'action':'isolate_references',
            'reason':'test supplied references individually, then pairwise, to distinguish prompt/image/pair failures',
            **isolation,
        }
    return {
        'action':'inspect_prompt_or_provider_request',
        'reason':'no reference images are available to isolate',
        'reference_asset_ids':[],
    }


def _reference_purpose(package: EpisodePackage, shot, asset_id: str, *, start_id: str|None) -> str:
    if asset_id == shot.approved_storyboard_asset_id:
        return 'approved planning/composition guide for this shot; use layout, not as a replacement for canonical identity or site design'
    if start_id and asset_id == start_id:
        return 'exact approved start frame; preserve its identity, geometry, lighting, props, scale, and camera axis while changing only the intended end-state pose/composition'
    try:
        asset=package.find_asset(asset_id)
    except KeyError:
        return 'production reference'
    role=getattr(asset,'role',None) or asset.metadata.get('role') if isinstance(asset.metadata,dict) else None
    if asset.kind=='reference_image':
        suffix=f' ({role})' if role else ''
        return f'canonical or approved identity/site reference{suffix}; preserve design facts while following this shot-specific pose and camera'
    return f'production reference ({asset.kind})'


def structured_image_prompt(package: EpisodePackage, shot, role: str, instruction: str, reference_ids: list[str], *, start_id: str|None=None) -> str:
    """Serialize image-only production context as structured JSON."""
    references=[]
    for index, asset_id in enumerate(reference_ids,1):
        references.append({
            'image': f'Image {index}',
            'asset_id': asset_id,
            'use': _reference_purpose(package,shot,asset_id,start_id=start_id),
        })
    payload={
        'task':'generate one production image',
        'frame_role':role,
        'instruction':instruction,
        'shot_context':{
            'purpose':shot.purpose,
            'visual_action':shot.visual,
        },
        'camera':shot.camera,
        'composition_constraints':{
            'must_show':_constraint_values(shot,'must_show'),
            'must_not_show':_constraint_values(shot,'must_not_show'),
        },
        'reference_images':references,
        'continuity':{
            'preserve_character_identity':True,
            'preserve_established_site_geometry':True,
            'preserve_scale_materials_lighting_and_fixed_props':True,
            'reference_images_are_design_authority_not_default_pose':True,
        },
        'output':'one clean 16:9 frame; no collage, labels, captions, borders, or motion blur',
    }
    return json.dumps(payload,ensure_ascii=False,indent=2)


class AnimatorService:
    def __init__(self, provider: MediaProvider, telemetry: TelemetrySink|None=None):
        self.provider=provider; self.telemetry=telemetry or TelemetrySink()
    def generate(self, package: EpisodePackage, shot_id: str, *, role: str='storyboard') -> list[AssetRecord]:
        assert_generation_preflight(package)
        shot=package.find_shot(shot_id)
        if role not in ROLE_KIND: raise ValueError(f'unknown generation role {role!r}')
        if shot.execution_route=='puppeteer' and role!='storyboard':
            raise ValueError('physical-only shot belongs to Forge Puppeteer')
        if role=='video' and shot.render_strategy not in {'generated_video','hybrid'}:
            raise ValueError('shot is not configured for generated video')
        if role!='storyboard':
            issues=validate_frame_plans(package,shot_id=shot_id)
            if issues: raise FramePlanError(issues[0])
        specific=getattr(shot,ROLE_PROMPT[role],None)
        prompt=(specific or storyboard_fallback_prompt(shot)) if role=='storyboard' else (
            specific or (shot.image_prompt if role!='video' else None) or self._default_prompt(shot,role)
        )
        if role=='storyboard' and specific and isinstance(shot.image_prompt,str) and shot.image_prompt.strip():
            image_anchor=shot.image_prompt.strip()
            if image_anchor not in prompt:
                prompt += '\n\nVisual design anchor from the EpisodePackage: '+image_anchor
        reference_ids=list(shot.continuity_asset_ids)
        start_id=shot.approved_start_frame_asset_id or shot.frame_plan.start_asset_id
        end_id=shot.approved_end_frame_asset_id or shot.frame_plan.end_asset_id
        if shot.frame_plan.mode=='chained_start':
            if role=='video':
                issues=validate_frame_plans(package,require_approved_end_frames=True,shot_id=shot_id)
                if issues:
                    raise FramePlanError(issues[0])
            previous=predecessor_for(package,shot)
            start_id=previous.approved_end_frame_asset_id
        if shot.frame_plan.mode=='start_and_end' and shot.frame_plan.chain_from_shot_id and role!='storyboard':
            if role=='start_frame':
                raise ValueError(f'Shot {shot_id!r} inherits its start frame from the predecessor; bind that endpoint instead of generating a new start frame.')
            issues=validate_frame_plans(package,require_approved_end_frames=True,shot_id=shot_id)
            if issues: raise FramePlanError(issues[0])
            previous=predecessor_for(package,shot)
            endpoint=previous.approved_end_frame_asset_id
            if shot.approved_start_frame_asset_id != endpoint:
                raise FramePlanError(FramePlanIssue(
                    'START_AND_END_INHERITED_START_NOT_BOUND',
                    f"Shot {shot_id!r} must bind predecessor {previous.shot_id!r}'s approved endpoint as its start frame.",
                    shot_id,previous.shot_id,endpoint,
                ))
            start_id=endpoint
        if role=='start_frame' and shot.approved_storyboard_asset_id:
            storyboard_id=shot.approved_storyboard_asset_id
            if storyboard_id not in reference_ids:
                reference_ids.append(storyboard_id)
                prompt += (f'\n\nReference image {len(reference_ids)} is this shot\'s approved storyboard. '
                           'Keep its composition and spatial layout; canonical references still fix identity and site design.')
        if role=='end_frame' and shot.frame_plan.mode=='start_and_end' and not shot.approved_start_frame_asset_id:
            raise ValueError(f'Shot {shot_id!r} needs an approved start frame before generating its end frame.')
        if role=='end_frame' and start_id and start_id not in reference_ids:
            reference_ids.append(start_id)
            prompt += (f'\n\nReference image {len(reference_ids)} is this shot\'s approved start frame. '
                       'Preserve its character design, architecture, light, props, and camera axis; change only the '
                       'intended pose and ending composition.')
        prompt += production_prompt_suffix(shot,role)
        if role!='video':
            prompt=structured_image_prompt(package,shot,role,prompt,reference_ids,start_id=start_id)
        refs=[self._asset_uri(package,a) for a in reference_ids]
        request=MediaRequest(kind='video' if role=='video' else 'image',shot_id=shot_id,role=role,prompt=prompt,reference_assets=tuple(refs),start_frame_asset=self._asset_uri(package,start_id) if start_id else None,end_frame_asset=self._asset_uri(package,end_id) if end_id else None,options=shot.provider_options)
        shot_features={
            'duration_seconds':shot.duration_seconds,
            'purpose':shot.purpose,
            'visual':shot.visual,
            'entity_ids':shot.entity_ids,
            'camera':shot.camera,
            'visual_constraints':shot.visual_constraints,
            'performance_intent':shot.performance_intent,
            'edit_intent':shot.edit_intent,
            'execution_route':shot.execution_route,
            'render_strategy':shot.render_strategy,
            'frame_plan':shot.frame_plan.model_dump(mode='json'),
            'source_beat_ids':shot.source_beat_ids,
        }
        attempt=GenerationAttempt(production_id=package.production_id,episode_id=package.episode_id,shot_id=shot_id,role=role,provider=self.provider.name,prompt=prompt,reference_asset_ids=reference_ids,options=shot.provider_options,metadata={'shot_features':shot_features})
        self.telemetry.emit('generation_attempt.started',**attempt.model_dump(mode='json'))
        started=time.perf_counter()
        try:
            results=self.provider.generate(request)
        except Exception as exc:
            diagnostics=exc.as_dict() if isinstance(exc,ProviderGenerationError) else {
                'provider':self.provider.name,
                'model':getattr(self.provider,'video_model' if role=='video' else 'image_model',None),
                'request_id':None,
                'failure_class':'provider_error',
            }
            failure_class=diagnostics.get('failure_class')
            recovery=failure_recovery_plan(failure_class,reference_ids)
            diagnostics.update({
                'shot_id':shot_id,
                'role':role,
                'prompt':prompt,
                'reference_asset_ids':list(reference_ids),
                'provider_options':dict(shot.provider_options),
                'recovery':recovery,
                'next_isolation_step':recovery.get('next_isolation_step'),
            })
            attempt.model=diagnostics.get('model')
            attempt.metadata['failure_diagnostics']=diagnostics
            if failure_class=='content_policy' or 'content_policy_violation' in str(exc):
                self.telemetry.emit(
                    'generation_prompt.rejected', production_id=package.production_id, episode_id=package.episode_id,
                    shot_id=shot_id, role=role, provider=self.provider.name,
                    code='PROVIDER_CONTENT_POLICY_REJECTION', request_id=diagnostics.get('request_id'),
                    reference_asset_ids=list(reference_ids), next_isolation_step=recovery.get('next_isolation_step'),
                    guidance='Do not rewrite the story automatically. Diagnose prompt versus reference-image false positives using the recorded isolation plan.',
                )
                exc.add_note('Provider content-policy rejection: use failure_diagnostics.recovery to isolate prompt/reference causes; Forge Studios does not use an LLM to rewrite prompts.')
            attempt.outcome='failed'; attempt.error=str(exc); attempt.latency_ms=(time.perf_counter()-started)*1000
            self.telemetry.emit('generation_attempt.failed',**attempt.model_dump(mode='json')); raise
        assets=[]
        for result in results:
            source_ids=list(dict.fromkeys(reference_ids + ([start_id] if start_id else []) + ([end_id] if end_id else [])))
            metadata=dict(result.metadata)
            metadata['generation']={
                'attempt_id':attempt.attempt_id,
                'role':role,
                'prompt':prompt,
                'provider':result.provider,
                'model':result.model,
                'options':dict(shot.provider_options),
                'reference_asset_ids':list(reference_ids),
                'start_asset_id':start_id,
                'end_asset_id':end_id,
                'shot_features':shot_features,
            }
            asset=AssetRecord(asset_id=f'asset_{uuid4().hex}',kind=ROLE_KIND[role],uri=result.uri,status='candidate',authority='generated',episode_id=package.episode_id,shot_id=shot_id,attempt_id=attempt.attempt_id,provider=result.provider,model=result.model,source_asset_ids=source_ids,metadata=metadata)
            package.assets.append(asset); assets.append(asset)
            target={'storyboard':'storyboard_asset_ids','start_frame':'start_frame_asset_ids','end_frame':'end_frame_asset_ids','video':'candidate_clip_asset_ids'}[role]
            getattr(shot,target).append(asset.asset_id)
            self.telemetry.emit(
                'asset.generated', production_id=package.production_id, episode_id=package.episode_id,
                shot_id=shot_id, attempt_id=attempt.attempt_id, asset_id=asset.asset_id,
                role=role, kind=asset.kind, provider=asset.provider, model=asset.model,
                source_asset_ids=source_ids, prompt=prompt, options=shot.provider_options,
                shot_features=shot_features,
            )
        attempt.outcome='succeeded'; attempt.asset_ids=[a.asset_id for a in assets]; attempt.model=assets[0].model if assets else None; attempt.latency_ms=(time.perf_counter()-started)*1000
        self.telemetry.emit('generation_attempt.succeeded',**attempt.model_dump(mode='json'))
        return assets
    @staticmethod
    def _asset_uri(package: EpisodePackage, asset_id: str|None) -> str:
        if not asset_id: raise KeyError('missing asset id')
        return package.find_asset(asset_id).uri
    @staticmethod
    def _default_prompt(shot, role: str) -> str:
        parts=[shot.visual]
        if shot.purpose: parts.append(f'Narrative purpose: {shot.purpose}')
        if shot.camera: parts.append('Camera: '+json.dumps(shot.camera,ensure_ascii=False))
        if shot.visual_constraints: parts.append('Visual constraints: '+json.dumps(shot.visual_constraints,ensure_ascii=False))
        if shot.performance_intent: parts.append('Performance intent: '+json.dumps(shot.performance_intent,ensure_ascii=False))
        if role=='video':
            parts.append('Describe only temporal change and preserve supplied start/end frames and canonical references.')
        elif role=='start_frame':
            parts.append('Create the exact approved starting composition for the shot. Preserve canonical references, anatomy, architecture, scale, and spatial relationships.')
        elif role=='end_frame':
            parts.append('Create the exact destination composition the shot must reach. Preserve canonical references, anatomy, architecture, scale, and spatial relationships.')
        else:
            parts.append('Create a production storyboard still for this shot. Preserve canonical references, anatomy, architecture, scale, and spatial relationships.')
        return '\n\n'.join(parts)
