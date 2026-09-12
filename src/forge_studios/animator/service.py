from __future__ import annotations
import json, time
from uuid import uuid4
from ..contracts import AssetRecord, EpisodePackage, GenerationAttempt
from ..providers.base import MediaProvider, MediaRequest
from ..telemetry import TelemetrySink

ROLE_KIND={'storyboard':'storyboard_image','start_frame':'start_frame','end_frame':'end_frame','video':'generated_clip'}
ROLE_PROMPT={'storyboard':'storyboard_prompt','start_frame':'start_frame_prompt','end_frame':'end_frame_prompt','video':'video_prompt'}

class AnimatorService:
    def __init__(self, provider: MediaProvider, telemetry: TelemetrySink|None=None):
        self.provider=provider; self.telemetry=telemetry or TelemetrySink()
    def generate(self, package: EpisodePackage, shot_id: str, *, role: str='storyboard') -> list[AssetRecord]:
        shot=package.find_shot(shot_id)
        if role not in ROLE_KIND: raise ValueError(f'unknown generation role {role!r}')
        if shot.execution_route=='puppeteer' and role!='storyboard':
            raise ValueError('physical-only shot belongs to Forge Puppeteer')
        if role=='video' and shot.render_strategy not in {'generated_video','hybrid'}:
            raise ValueError('shot is not configured for generated video')
        specific=getattr(shot,ROLE_PROMPT[role],None)
        prompt=specific or (shot.image_prompt if role!='video' else None) or self._default_prompt(shot, role)
        refs=[self._asset_uri(package,a) for a in shot.continuity_asset_ids]
        start_id=shot.approved_start_frame_asset_id or shot.frame_plan.start_asset_id
        end_id=shot.approved_end_frame_asset_id or shot.frame_plan.end_asset_id
        if shot.frame_plan.mode=='chained_start' and not start_id:
            previous=package.find_shot(shot.frame_plan.chain_from_shot_id or '')
            start_id=previous.approved_end_frame_asset_id or previous.approved_storyboard_asset_id
        request=MediaRequest(kind='video' if role=='video' else 'image',shot_id=shot_id,role=role,prompt=prompt,reference_assets=tuple(refs),start_frame_asset=self._asset_uri(package,start_id) if start_id else None,end_frame_asset=self._asset_uri(package,end_id) if end_id else None,options=shot.provider_options)
        attempt=GenerationAttempt(production_id=package.production_id,episode_id=package.episode_id,shot_id=shot_id,role=role,provider=self.provider.name,prompt=prompt,reference_asset_ids=shot.continuity_asset_ids,options=shot.provider_options)
        self.telemetry.emit('generation_attempt.started',**attempt.model_dump(mode='json'))
        started=time.perf_counter()
        try:
            results=self.provider.generate(request)
        except Exception as exc:
            attempt.outcome='failed'; attempt.error=str(exc); attempt.latency_ms=(time.perf_counter()-started)*1000
            self.telemetry.emit('generation_attempt.failed',**attempt.model_dump(mode='json')); raise
        assets=[]
        for result in results:
            source_ids=shot.continuity_asset_ids + ([start_id] if start_id else []) + ([end_id] if end_id else [])
            metadata=dict(result.metadata)
            metadata['generation']={
                'attempt_id':attempt.attempt_id,
                'role':role,
                'prompt':prompt,
                'provider':result.provider,
                'model':result.model,
                'options':dict(shot.provider_options),
                'reference_asset_ids':list(shot.continuity_asset_ids),
                'start_asset_id':start_id,
                'end_asset_id':end_id,
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
