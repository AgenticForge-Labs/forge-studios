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
from .base import MediaRequest, MediaResult


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
    # The ordinary Kontext endpoint is single-image editing.
    "fal-ai/flux-pro/kontext": FalImageModelProfile("image_url", "single", 1),
    # Fal documents this endpoint as accepting image_urls for multi-image editing.
    "fal-ai/flux-pro/kontext/max/multi": FalImageModelProfile("image_urls", "list"),
    "fal-ai/flux-2-pro/edit": FalImageModelProfile("image_urls", "list"),
    # Kling accepts up to ten image_urls and addresses them as @Image1, @Image2, etc.
    "fal-ai/kling-image/o3/image-to-image": FalImageModelProfile("image_urls", "list", 10, True),
    "openai/gpt-image-2/edit": FalImageModelProfile("image_urls", "list", 16),
}


def image_model_profile(model: str) -> FalImageModelProfile:
    """Return known Fal reference rules, defaulting to the common image_urls list."""
    return _IMAGE_MODEL_PROFILES.get(model, _DEFAULT_IMAGE_PROFILE)


def provider_safe_image_prompt(prompt: str) -> str:
    """Apply a final conservative wording pass before sending text to fal.

    Story fields can retain narrative language. This adapter only receives media
    prompts and avoids resting-state and anatomy-failure vocabulary that has
    repeatedly tripped image-provider checks.
    """
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


class FalProvider:
    """fal.ai provider with local-file upload and explicit start/end-frame support.

    Authentication uses the official fal-client `SyncClient(key=...)` when a key is
    stored by `forge-studios keys set fal`. If no Forge key is stored, fal-client can
    still use its normal FAL_KEY / `fal auth login` authentication.
    """
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
        """Download provider output so package media remains usable after CDN expiry."""
        parsed=urlparse(uri)
        if parsed.scheme not in {'http','https'}:
            return uri
        suffix=Path(parsed.path).suffix or ('.jpg' if request.kind=='image' else '.bin')
        self.output_dir.mkdir(parents=True,exist_ok=True)
        target=self.output_dir / f'{request.shot_id}-{request.role}-{uuid4().hex[:12]}-{index}{suffix}'
        urlretrieve(uri,target)
        return str(target.resolve())

    def localize(self, uri: str, request: MediaRequest, index: int = 0) -> tuple[str, str | None]:
        """Return a durable local URI and the original provider URI."""
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
        """Map storage-neutral references to the selected Fal image endpoint schema."""
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
            # Match the primary 16:9 delivery format. A shot may override this
            # through provider_options when a different canvas is intentional.
            payload.setdefault('image_size', dict(DEFAULT_IMAGE_SIZE))
            # FLUX.2 Pro Edit defaults to tolerance 2, which has rejected benign
            # stylized-character storyboards in this production. Keep Fal's checker
            # enabled, but use its documented most-permissive API tolerance unless a
            # shot explicitly asks for a stricter value. This is provider execution
            # configuration, not world or story metadata.
            if model.startswith('fal-ai/flux-2'):
                payload.setdefault('safety_tolerance', os.getenv('FAL_IMAGE_SAFETY_TOLERANCE','5'))
                payload.setdefault('enable_safety_checker', True)
        refs=[self._remote_or_upload(value,client) for value in request.reference_assets]
        refs=[value for value in refs if value]
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
            # Fal reports queued/in-progress repeatedly. Keep the terminal readable,
            # but emit a heartbeat at the configured polling cadence.
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
            if request_id:
                exc.add_note(f'fal request id: {request_id}')
            if 'Timeout' in type(exc).__name__ or 'timeout' in str(exc).lower():
                exc.add_note(
                    f'Fal request timed out after {self.client_timeout_seconds:g}s. The client attempted cancellation; '
                    'rerun safely resumes from already-saved storyboard shots.'
                )
            raise
        uris=[]
        if isinstance(raw,dict):
            for keyname in ('url','image_url','video_url'):
                if isinstance(raw.get(keyname),str): uris.append(raw[keyname])
            for keyname in ('images','videos','assets'):
                for item in raw.get(keyname,[]) if isinstance(raw.get(keyname),list) else []:
                    if isinstance(item,str): uris.append(item)
                    elif isinstance(item,dict) and isinstance(item.get('url'),str): uris.append(item['url'])
        if not uris: raise ValueError('fal.ai returned no recognizable media URI')
        results=[]
        for index, uri in enumerate(uris):
            results.append(MediaResult(
                uri=self._download_output(uri,request,index),
                provider=self.name,
                model=model,
                metadata={'provider_result':raw,'remote_uri':uri},
            ))
        return results
