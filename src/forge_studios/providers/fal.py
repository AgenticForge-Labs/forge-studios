from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse
from urllib.request import urlretrieve
from uuid import uuid4

from ..generation import GenerationMode, StudiosGenerationProfile, resolve_generation_profile
from ..local_config import LocalSecretStore
from .base import MediaRequest, MediaResult, ProviderGenerationError


@dataclass(frozen=True)
class FalImageModelProfile:
    """Provider-specific reference-image rules, kept outside EpisodePackage."""

    reference_field: str = "image_urls"
    reference_shape: str = "list"
    max_references: int | None = None
    min_dimension: int | None = None
    max_prompt_chars: int | None = None


_DEFAULT_IMAGE_PROFILE = FalImageModelProfile()
_IMAGE_MODEL_PROFILES = {
    "fal-ai/flux-pro/kontext": FalImageModelProfile("image_url", "single", 1),
    "fal-ai/flux-pro/kontext/max/multi": FalImageModelProfile("image_urls", "list"),
    "fal-ai/flux-2/flash": FalImageModelProfile("image_urls", "list", min_dimension=512),
    "fal-ai/flux-2/flash/edit": FalImageModelProfile("image_urls", "list", 4, min_dimension=512),
    "fal-ai/flux-2-pro/edit": FalImageModelProfile("image_urls", "list"),
    "fal-ai/kling-image/o3/image-to-image": FalImageModelProfile(
        "image_urls", "list", 10, max_prompt_chars=2500
    ),
    "openai/gpt-image-2/edit": FalImageModelProfile("image_urls", "list", 16),
}
_FAST_VIDEO_DURATIONS = {6, 8, 10, 12, 14, 16, 18, 20}


def image_model_profile(model: str) -> FalImageModelProfile:
    """Return known Fal reference rules, defaulting to the common image_urls list."""
    return _IMAGE_MODEL_PROFILES.get(model, _DEFAULT_IMAGE_PROFILE)


def resolve_supported_image_size(model: str, width: int, height: int) -> tuple[int, int]:
    """Resolve a mode target to the nearest documented practical 16:9 size.

    FLUX.2 Flash/Flash Edit document a 512px minimum for each custom dimension,
    so the semantic cheap target 768x432 cannot be sent literally. We scale to
    the smallest exact integer 16:9 dimensions satisfying that endpoint limit.
    Explicit per-request ``image_size`` still bypasses this mode-default resolver.
    """
    profile=image_model_profile(model)
    minimum=profile.min_dimension
    if minimum is None or (width >= minimum and height >= minimum):
        return width,height
    unit=max(
        math.ceil(width/16),
        math.ceil(height/9),
        math.ceil(minimum/16),
        math.ceil(minimum/9),
    )
    return 16*unit,9*unit


def _classify_fal_failure(exc: Exception) -> str:
    text=f'{type(exc).__name__}: {exc}'.casefold()
    if any(term in text for term in ('content_policy', 'content policy', 'safety checker', 'safety violation', 'nsfw')):
        return 'content_policy'
    if 'timeout' in text or 'timed out' in text:
        return 'timeout'
    if any(term in text for term in ('remoteprotocolerror', 'connectionerror', 'connecterror', 'network', 'connection reset')):
        return 'transport'
    if any(term in text for term in ('bad request', 'validation', '422', '400')):
        return 'request_validation'
    return 'provider_error'


class FalProvider:
    """fal.ai provider with generation-mode profiles and explicit start/end-frame support."""

    name='fal'

    def __init__(
        self,
        *,
        mode: str | GenerationMode | None = None,
        image_generate_model: str | None = None,
        image_edit_model: str | None = None,
        video_model: str | None = None,
        image_width: int | None = None,
        image_height: int | None = None,
        video_width: int | None = None,
        video_height: int | None = None,
        video_resolution: str | None = None,
        output_dir: str|Path='outputs/fal',
        local_config: LocalSecretStore|None=None,
        progress: Callable[[str], None]|None=None,
        client_timeout_seconds: float|None=None,
        poll_interval_seconds: float|None=None,
    ):
        env_mode = mode if mode is not None else os.getenv('FORGE_STUDIOS_MODE')
        self.profile: StudiosGenerationProfile = resolve_generation_profile(
            env_mode,
            image_generate_model=image_generate_model or os.getenv('FAL_IMAGE_GENERATE_MODEL'),
            image_edit_model=image_edit_model or os.getenv('FAL_IMAGE_EDIT_MODEL'),
            video_model=video_model or os.getenv('FAL_VIDEO_MODEL'),
            image_width=image_width,
            image_height=image_height,
            video_width=video_width,
            video_height=video_height,
            video_resolution=video_resolution,
        )
        self.image_generate_model=self.profile.image.generate_model
        self.image_edit_model=self.profile.image.edit_model
        self.video_model=self.profile.video.model
        self.output_dir=Path(output_dir)
        self.local_config=local_config or LocalSecretStore()
        self.progress=progress
        self.client_timeout_seconds=client_timeout_seconds if client_timeout_seconds is not None else float(os.getenv('FAL_CLIENT_TIMEOUT_SECONDS','300'))
        self.poll_interval_seconds=poll_interval_seconds if poll_interval_seconds is not None else float(os.getenv('FAL_POLL_INTERVAL_SECONDS','5'))

    @property
    def generation_mode(self) -> str:
        return self.profile.mode.value

    def generation_settings(self) -> dict[str, Any]:
        return self.profile.as_dict()

    def model_for(self, request: MediaRequest) -> str:
        if request.kind == 'video':
            return self.video_model
        if request.kind != 'image':
            raise ValueError(f'unsupported media kind {request.kind!r}')
        has_composition_inputs = bool(request.reference_assets or request.start_frame_asset or request.end_frame_asset)
        return self.image_edit_model if has_composition_inputs else self.image_generate_model

    def _progress(self, message: str) -> None:
        if self.progress:
            self.progress(message)

    def _download_output(self, uri: str, request: MediaRequest, index: int) -> str:
        parsed=urlparse(uri)
        if parsed.scheme not in {'http','https'}:
            return uri
        suffix=Path(parsed.path).suffix or ('.jpg' if request.kind=='image' else '.bin')
        self.output_dir.mkdir(parents=True,exist_ok=True)
        target=self.output_dir / f'{request.shot_id}-{request.role}-{uuid4().hex[:12]}-{index}{suffix}'
        urlretrieve(uri,target)
        return str(target.resolve())

    def localize(self, uri: str, request: MediaRequest, index: int = 0) -> tuple[str, str | None]:
        parsed=urlparse(uri)
        if parsed.scheme not in {'http','https'}:
            return uri, None
        return self._download_output(uri,request,index), uri

    @staticmethod
    def _remote_or_upload(value: str|None, client) -> str|None:
        if not value:
            return None
        parsed=urlparse(value)
        if parsed.scheme in {'http','https','data'}:
            return value
        path=Path(value).expanduser()
        if path.exists() and path.is_file():
            return client.upload_file(str(path.resolve()))
        return value

    @staticmethod
    def _image_reference_payload(model: str, references: list[str]) -> dict[str, Any]:
        if not references:
            return {}
        profile = image_model_profile(model)
        if profile.max_references is not None and len(references) > profile.max_references:
            raise ValueError(
                f"Fal model {model!r} accepts at most {profile.max_references} reference image(s); "
                f"received {len(references)}"
            )
        field = os.getenv("FAL_IMAGE_REFERENCE_FIELD") or profile.reference_field
        value: str | list[str] = references[0] if profile.reference_shape == "single" else references
        return {field: value}

    def _apply_profile_defaults(self, request: MediaRequest, model: str, payload: dict[str, Any]) -> None:
        if request.kind == 'image':
            image_profile = image_model_profile(model)
            prompt = str(payload.get('prompt') or '')
            if image_profile.max_prompt_chars is not None and len(prompt) > image_profile.max_prompt_chars:
                raise ValueError(
                    f'Fal model {model!r} accepts prompts up to {image_profile.max_prompt_chars} characters; '
                    f'received {len(prompt)}. Shorten the authored prompt upstream in Forge Worlds.'
                )
            if 'image_size' not in payload and self.profile.image.width and self.profile.image.height:
                width,height=resolve_supported_image_size(model,self.profile.image.width,self.profile.image.height)
                payload['image_size']={'width':width,'height':height}
            if model.startswith('fal-ai/flux-2'):
                payload.setdefault('safety_tolerance', os.getenv('FAL_IMAGE_SAFETY_TOLERANCE','5'))
                payload.setdefault('enable_safety_checker', True)
            return

        if 'video_size' not in payload and 'resolution' not in payload:
            if self.profile.video.width and self.profile.video.height:
                payload['video_size']={'width':self.profile.video.width,'height':self.profile.video.height}
            elif self.profile.video.resolution:
                payload['resolution']=self.profile.video.resolution

        if request.duration_seconds is None or 'duration' in payload or 'num_frames' in payload:
            return
        duration=float(request.duration_seconds)
        if duration <= 0:
            raise ValueError('requested video duration must be positive')
        if '/distilled/' in model:
            fps=float(payload.get('fps',24))
            if fps <= 0:
                raise ValueError('video fps must be positive')
            payload['num_frames']=max(1,round(duration*fps))
        else:
            rounded=round(duration)
            if abs(duration-rounded) > 1e-6 or rounded not in _FAST_VIDEO_DURATIONS:
                allowed=', '.join(str(value) for value in sorted(_FAST_VIDEO_DURATIONS))
                raise ValueError(
                    f'Fal LTX fast video supports durations {allowed} seconds; requested {duration:g}. '
                    'Forge Studios will not silently shorten the shot.'
                )
            payload['duration']=str(rounded)

    def _diagnostic_settings(self, *, request: MediaRequest, model: str, payload: dict[str,Any], reference_count: int) -> dict[str,Any]:
        settings: dict[str,Any]={
            'generation_mode':self.generation_mode,
            'kind':request.kind,
            'shot_id':request.shot_id,
            'role':request.role,
            'model':model,
            'reference_count':reference_count,
            'requested_duration_seconds':request.duration_seconds,
            'client_timeout_seconds':self.client_timeout_seconds,
            'poll_interval_seconds':self.poll_interval_seconds,
            'explicit_profile_overrides':list(self.profile.explicit_overrides),
        }
        if request.kind=='image':
            profile=image_model_profile(model)
            settings.update({
                'reference_field':os.getenv('FAL_IMAGE_REFERENCE_FIELD') or profile.reference_field,
                'reference_shape':profile.reference_shape,
                'max_references':profile.max_references,
                'max_prompt_chars':profile.max_prompt_chars,
                'prompt_chars':len(str(payload.get('prompt') or '')),
                'image_target_size':{'width':self.profile.image.width,'height':self.profile.image.height} if self.profile.image.width and self.profile.image.height else None,
                'image_size':payload.get('image_size'),
                'safety_tolerance':payload.get('safety_tolerance','provider-default'),
                'enable_safety_checker':payload.get('enable_safety_checker','provider-default'),
            })
        else:
            settings.update({
                'start_frame_field':os.getenv('FAL_VIDEO_START_FRAME_FIELD','image_url'),
                'end_frame_field':os.getenv('FAL_VIDEO_END_FRAME_FIELD','end_image_url'),
                'reference_field':os.getenv('FAL_VIDEO_REFERENCE_FIELD'),
                'has_start_frame':bool(request.start_frame_asset),
                'has_end_frame':bool(request.end_frame_asset),
                'video_size':payload.get('video_size'),
                'resolution':payload.get('resolution'),
                'duration':payload.get('duration'),
                'num_frames':payload.get('num_frames'),
                'fps':payload.get('fps'),
            })
        return settings

    @staticmethod
    def _media_items(raw: Any) -> list[tuple[str, dict[str, Any]]]:
        items: list[tuple[str, dict[str, Any]]] = []
        if not isinstance(raw,dict):
            return items
        for keyname in ('url','image_url','video_url'):
            if isinstance(raw.get(keyname),str):
                items.append((raw[keyname],{}))
        for keyname in ('image','video'):
            item=raw.get(keyname)
            if isinstance(item,str):
                items.append((item,{}))
            elif isinstance(item,dict) and isinstance(item.get('url'),str):
                items.append((item['url'],dict(item)))
        for keyname in ('images','videos','assets'):
            values=raw.get(keyname,[]) if isinstance(raw.get(keyname),list) else []
            for item in values:
                if isinstance(item,str):
                    items.append((item,{}))
                elif isinstance(item,dict) and isinstance(item.get('url'),str):
                    items.append((item['url'],dict(item)))
        return items

    def generate(self, request: MediaRequest) -> list[MediaResult]:
        try:
            import fal_client
        except ImportError as exc:
            raise RuntimeError('Install Forge Studios with [fal] support') from exc
        model=self.model_for(request)
        stored_key=self.local_config.resolve('fal')
        client=fal_client.SyncClient(key=stored_key) if stored_key else fal_client.SyncClient()
        payload: dict[str,Any]={'prompt':request.prompt,**request.options}
        try:
            self._apply_profile_defaults(request,model,payload)
        except Exception as exc:
            raise ProviderGenerationError(
                str(exc), provider=self.name, model=model,
                diagnostics={
                    'failure_class':'request_validation',
                    'provider_settings':self._diagnostic_settings(request=request,model=model,payload=payload,reference_count=len(request.reference_assets)),
                },
            ) from exc
        refs=[self._remote_or_upload(value,client) for value in request.reference_assets]
        refs=[value for value in refs if value]
        try:
            if request.kind=='image' and refs:
                payload.update(self._image_reference_payload(model, refs))
            if request.kind=='video':
                if request.start_frame_asset:
                    payload[os.getenv('FAL_VIDEO_START_FRAME_FIELD','image_url')]=self._remote_or_upload(request.start_frame_asset,client)
                if request.end_frame_asset:
                    payload[os.getenv('FAL_VIDEO_END_FRAME_FIELD','end_image_url')]=self._remote_or_upload(request.end_frame_asset,client)
                ref_field=os.getenv('FAL_VIDEO_REFERENCE_FIELD')
                if refs and ref_field: payload[ref_field]=refs
        except Exception as exc:
            raise ProviderGenerationError(
                str(exc), provider=self.name, model=model,
                diagnostics={
                    'failure_class':'request_validation',
                    'provider_settings':self._diagnostic_settings(request=request,model=model,payload=payload,reference_count=len(refs)),
                },
            ) from exc
        settings=self._diagnostic_settings(request=request,model=model,payload=payload,reference_count=len(refs))
        if request.kind=='image':
            self._progress(
                f'[fal] {request.shot_id} {request.role}: mode={self.generation_mode} model={model} '
                f'image_target={settings.get("image_target_size")} image_size={payload.get("image_size", "provider-default")} '
                f'safety_tolerance={payload.get("safety_tolerance", "provider-default")}, '
                f'enable_safety_checker={payload.get("enable_safety_checker", "provider-default")}'
            )
        else:
            size=payload.get('video_size') or payload.get('resolution','provider-default')
            self._progress(f'[fal] {request.shot_id} {request.role}: mode={self.generation_mode} model={model} video_size={size}')
        started=time.monotonic()
        request_id: str|None=None
        last_status: str|None=None

        def on_enqueue(value: str) -> None:
            nonlocal request_id
            request_id=value
            self._progress(f'[fal] {request.shot_id} {request.role}: submitted request {value}; waiting for {model}')

        def on_queue_update(status: Any) -> None:
            nonlocal last_status
            name=type(status).__name__
            detail=[]
            for field in ('position','queue_position'):
                value=getattr(status,field,None)
                if value is not None:
                    detail.append(f'{field}={value}')
            summary=f'{name.lower()}{" (" + ", ".join(detail) + ")" if detail else ""}'
            elapsed=int(time.monotonic()-started)
            if summary != last_status or elapsed % max(1,int(self.poll_interval_seconds)) == 0:
                self._progress(f'[fal] {request.shot_id} {request.role}: {summary}; {elapsed}s elapsed')
                last_status=summary

        try:
            raw=client.subscribe(
                model,arguments=payload,with_logs=False,
                interval=self.poll_interval_seconds,
                on_enqueue=on_enqueue,
                on_queue_update=on_queue_update,
                client_timeout=self.client_timeout_seconds,
            )
        except Exception as exc:
            failure_class=_classify_fal_failure(exc)
            error=ProviderGenerationError(
                str(exc), provider=self.name, model=model, request_id=request_id,
                diagnostics={
                    'failure_class':failure_class,
                    'provider_settings':settings,
                },
            )
            if request_id:
                error.add_note(f'fal request id: {request_id}')
            if failure_class=='timeout':
                error.add_note(
                    f'Fal request timed out after {self.client_timeout_seconds:g}s. The client attempted cancellation; '
                    'rerun safely resumes from already-saved storyboard shots.'
                )
            raise error from exc
        media_items=self._media_items(raw)
        if not media_items:
            raise ProviderGenerationError(
                'fal.ai returned no recognizable media URI', provider=self.name, model=model, request_id=request_id,
                diagnostics={
                    'failure_class':'malformed_response',
                    'response_keys':sorted(raw.keys()) if isinstance(raw,dict) else [],
                    'provider_settings':settings,
                },
            )
        results=[]
        for index,(uri,media) in enumerate(media_items):
            actual={key:media.get(key) for key in ('width','height','fps','duration','num_frames') if media.get(key) is not None}
            results.append(MediaResult(
                uri=self._download_output(uri,request,index),
                provider=self.name,
                model=model,
                metadata={
                    'provider_result':raw,
                    'remote_uri':uri,
                    'request_id':request_id,
                    'generation_mode':self.generation_mode,
                    'provider_settings':settings,
                    'actual_media':actual,
                },
            ))
        return results
