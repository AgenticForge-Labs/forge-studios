from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse
from urllib.request import urlretrieve
from uuid import uuid4

from ..local_config import LocalSecretStore
from .base import MediaRequest, MediaResult, ProviderGenerationError


@dataclass(frozen=True)
class FalImageModelProfile:
    """Provider-specific reference-image rules, kept outside EpisodePackage."""

    reference_field: str = "image_urls"
    reference_shape: str = "list"
    max_references: int | None = None
    numbered_prompt_references: bool = False


_DEFAULT_IMAGE_PROFILE = FalImageModelProfile()
DEFAULT_IMAGE_SIZE = {'width': 1920, 'height': 1080}
_IMAGE_MODEL_PROFILES = {
    "fal-ai/flux-pro/kontext": FalImageModelProfile("image_url", "single", 1),
    "fal-ai/flux-pro/kontext/max/multi": FalImageModelProfile("image_urls", "list"),
    "fal-ai/flux-2-pro/edit": FalImageModelProfile("image_urls", "list"),
    "fal-ai/kling-image/o3/image-to-image": FalImageModelProfile("image_urls", "list", 10, True),
    "openai/gpt-image-2/edit": FalImageModelProfile("image_urls", "list", 16),
}


def image_model_profile(model: str) -> FalImageModelProfile:
    """Return known Fal reference rules, defaulting to the common image_urls list."""
    return _IMAGE_MODEL_PROFILES.get(model, _DEFAULT_IMAGE_PROFILE)


def provider_safe_image_prompt(prompt: str) -> str:
    """Apply a final conservative wording pass before sending text to fal."""
    replacements = (
        (r"\bawake but still lying on\b", "awake and settled on"),
        (r"\blying on\b", "resting on"),
        (r"\blies on\b", "rests on"),
        (r"\bunconscious\b", "in a calm dormant state"),
        (r"\bmalformed anatomy\b", "an inconsistent silhouette"),
        (r"\bbipedal or (?:an )?malformed\b", "inconsistent"),
    )
    result = prompt
    for pattern, replacement in replacements:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
    result = re.sub(
        r"\b(?:do not|never)\s+(?:let|make|allow)\s+(?:video\s+)?generation\s+infer\s+[^.]+\.",
        "Keep the established four-legged silhouette.",
        result,
        flags=re.IGNORECASE,
    )
    return result


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
    """fal.ai provider with local-file upload and explicit start/end-frame support."""
    name='fal'
    def __init__(self, *, image_model: str|None=None, video_model: str|None=None, output_dir: str|Path='outputs/fal', local_config: LocalSecretStore|None=None, progress: Callable[[str], None]|None=None, client_timeout_seconds: float|None=None, poll_interval_seconds: float|None=None):
        self.image_model=image_model or os.getenv('FAL_IMAGE_MODEL')
        self.video_model=video_model or os.getenv('FAL_VIDEO_MODEL','fal-ai/ltx-2.3/image-to-video/fast')
        self.output_dir=Path(output_dir)
        self.local_config=local_config or LocalSecretStore()
        self.progress=progress
        self.client_timeout_seconds=client_timeout_seconds if client_timeout_seconds is not None else float(os.getenv('FAL_CLIENT_TIMEOUT_SECONDS','300'))
        self.poll_interval_seconds=poll_interval_seconds if poll_interval_seconds is not None else float(os.getenv('FAL_POLL_INTERVAL_SECONDS','5'))

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
    def _image_reference_payload(model: str, references: list[str]) -> tuple[dict[str, Any], str]:
        if not references:
            return {}, ""
        profile = image_model_profile(model)
        if profile.max_references is not None and len(references) > profile.max_references:
            raise ValueError(
                f"Fal model {model!r} accepts at most {profile.max_references} reference image(s); "
                f"received {len(references)}"
            )
        field = os.getenv("FAL_IMAGE_REFERENCE_FIELD") or profile.reference_field
        value: str | list[str] = references[0] if profile.reference_shape == "single" else references
        prompt_suffix = ""
        if profile.numbered_prompt_references:
            names = ", ".join(f"@Image{index}" for index in range(1, len(references) + 1))
            prompt_suffix = f"\n\nReference images are supplied in order as {names}."
        return {field: value}, prompt_suffix

    def _diagnostic_settings(self, *, request: MediaRequest, model: str, payload: dict[str,Any], reference_count: int) -> dict[str,Any]:
        settings: dict[str,Any]={
            'kind':request.kind,
            'shot_id':request.shot_id,
            'role':request.role,
            'reference_count':reference_count,
            'client_timeout_seconds':self.client_timeout_seconds,
            'poll_interval_seconds':self.poll_interval_seconds,
        }
        if request.kind=='image':
            profile=image_model_profile(model)
            settings.update({
                'reference_field':os.getenv('FAL_IMAGE_REFERENCE_FIELD') or profile.reference_field,
                'reference_shape':profile.reference_shape,
                'max_references':profile.max_references,
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
            })
        return settings

    def generate(self, request: MediaRequest) -> list[MediaResult]:
        try:
            import fal_client
        except ImportError as exc:
            raise RuntimeError('Install Forge Studios with [fal] support') from exc
        model=self.image_model if request.kind=='image' else self.video_model
        if not model: raise RuntimeError(f'No fal {request.kind} model configured')
        stored_key=self.local_config.resolve('fal')
        client=fal_client.SyncClient(key=stored_key) if stored_key else fal_client.SyncClient()
        payload: dict[str,Any]={'prompt':provider_safe_image_prompt(request.prompt) if request.kind=='image' else request.prompt,**request.options}
        if request.kind=='image':
            payload.setdefault('image_size', dict(DEFAULT_IMAGE_SIZE))
            if model.startswith('fal-ai/flux-2'):
                payload.setdefault('safety_tolerance', os.getenv('FAL_IMAGE_SAFETY_TOLERANCE','5'))
                payload.setdefault('enable_safety_checker', True)
        refs=[self._remote_or_upload(value,client) for value in request.reference_assets]
        refs=[value for value in refs if value]
        try:
            if request.kind=='image' and refs:
                reference_payload, prompt_suffix = self._image_reference_payload(model, refs)
                payload.update(reference_payload)
                payload['prompt'] += prompt_suffix
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
        if request.kind=='image':
            self._progress(
                f'[fal] {request.shot_id} {request.role}: sending '
                f'safety_tolerance={payload.get("safety_tolerance", "provider-default")}, '
                f'enable_safety_checker={payload.get("enable_safety_checker", "provider-default")}'
            )
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
                    'provider_settings':self._diagnostic_settings(request=request,model=model,payload=payload,reference_count=len(refs)),
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
        uris=[]
        if isinstance(raw,dict):
            for keyname in ('url','image_url','video_url'):
                if isinstance(raw.get(keyname),str): uris.append(raw[keyname])
            for keyname in ('images','videos','assets'):
                for item in raw.get(keyname,[]) if isinstance(raw.get(keyname),list) else []:
                    if isinstance(item,str): uris.append(item)
                    elif isinstance(item,dict) and isinstance(item.get('url'),str): uris.append(item['url'])
        if not uris:
            raise ProviderGenerationError(
                'fal.ai returned no recognizable media URI', provider=self.name, model=model, request_id=request_id,
                diagnostics={
                    'failure_class':'malformed_response',
                    'response_keys':sorted(raw.keys()) if isinstance(raw,dict) else [],
                    'provider_settings':self._diagnostic_settings(request=request,model=model,payload=payload,reference_count=len(refs)),
                },
            )
        results=[]
        for index, uri in enumerate(uris):
            results.append(MediaResult(
                uri=self._download_output(uri,request,index),
                provider=self.name,
                model=model,
                metadata={'provider_result':raw,'remote_uri':uri,'request_id':request_id},
            ))
        return results
