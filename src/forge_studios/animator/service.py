from __future__ import annotations
import json, time
from uuid import uuid4
from ..contracts import AssetRecord, EpisodePackage, GenerationAttempt
from ..providers.base import MediaProvider, MediaRequest
from ..telemetry import TelemetrySink

ROLE_KIND={'storyboard':'storyboard_image','start_frame':'start_frame','end_frame':'end_frame','video':'generated_clip'}

class AnimatorService:
    def __init__(self, provider: MediaProvider, telemetry: TelemetrySink|None=None):
        self.provider=provider; self.telemetry=telemetry or TelemetrySink()
    def generate(self, package: EpisodePackage, shot_id: str, *, role: str='storyboard') -> list[AssetRecord]:
        shot=package.find_shot(shot_id)
        if shot.execution_route=='puppeteer' and role!='storyboard':
            raise ValueError('physical-only shot belongs to Forge Puppeteer')
        if role=='video' and shot.render_strategy not in {'generated_video','hybrid'}:
            raise ValueError('shot is not configured for generated video')
        prompt=(shot.video_prompt if role=='video' else shot.image_prompt) or self._default_prompt(shot, role)
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
            asset=AssetRecord(asset_id=f'asset_{uuid4().hex}',kind=ROLE_KIND[role],uri=result.uri,status='candidate',authority='generated',episode_id=package.episode_id,shot_id=shot_id,attempt_id=attempt.attempt_id,provider=result.provider,model=result.model,source_asset_ids=shot.continuity_asset_ids + ([start_id] if start_id else []) + ([end_id] if end_id else []),metadata=result.metadata)
            package.assets.append(asset); assets.append(asset)
            target={'storyboard':'storyboard_asset_ids','start_frame':'start_frame_asset_ids','end_frame':'end_frame_asset_ids','video':'candidate_clip_asset_ids'}[role]
            getattr(shot,target).append(asset.asset_id)
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
        parts.append('Describe only temporal change and preserve supplied frames.' if role=='video' else f'Create the {role.replace("_"," ")} for this shot. Preserve canonical references and scale.')
        return '\n\n'.join(parts)
