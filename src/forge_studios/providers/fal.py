from __future__ import annotations
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from .base import MediaRequest, MediaResult
from ..local_config import LocalSecretStore

class FalProvider:
    """fal.ai provider with local-file upload and explicit start/end-frame support.

    Authentication uses the official fal-client `SyncClient(key=...)` when a key is
    stored by `forge-studios keys set fal`. If no Forge key is stored, fal-client can
    still use its normal FAL_KEY / `fal auth login` authentication.
    """
    name='fal'
    def __init__(self, *, image_model: str|None=None, video_model: str|None=None, local_config: LocalSecretStore|None=None):
        self.image_model=image_model or os.getenv('FAL_IMAGE_MODEL')
        self.video_model=video_model or os.getenv('FAL_VIDEO_MODEL','fal-ai/ltx-2.3/image-to-video/fast')
        self.local_config=local_config or LocalSecretStore()

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

    def generate(self, request: MediaRequest) -> list[MediaResult]:
        try:
            import fal_client
        except ImportError as exc:
            raise RuntimeError('Install Forge Studios with [fal] support') from exc
        model=self.image_model if request.kind=='image' else self.video_model
        if not model: raise RuntimeError(f'No fal {request.kind} model configured')
        stored_key=self.local_config.resolve('fal')
        client=fal_client.SyncClient(key=stored_key) if stored_key else fal_client.SyncClient()
        payload: dict[str,Any]={'prompt':request.prompt,**request.options}
        refs=[self._remote_or_upload(value,client) for value in request.reference_assets]
        refs=[value for value in refs if value]
        if request.kind=='image' and refs:
            payload[os.getenv('FAL_IMAGE_REFERENCE_FIELD','image_urls')]=refs
        if request.kind=='video':
            if request.start_frame_asset:
                payload[os.getenv('FAL_VIDEO_START_FRAME_FIELD','image_url')]=self._remote_or_upload(request.start_frame_asset,client)
            if request.end_frame_asset:
                payload[os.getenv('FAL_VIDEO_END_FRAME_FIELD','end_image_url')]=self._remote_or_upload(request.end_frame_asset,client)
            ref_field=os.getenv('FAL_VIDEO_REFERENCE_FIELD')
            if refs and ref_field: payload[ref_field]=refs
        raw=client.subscribe(model,arguments=payload,with_logs=False)
        uris=[]
        if isinstance(raw,dict):
            for keyname in ('url','image_url','video_url'):
                if isinstance(raw.get(keyname),str): uris.append(raw[keyname])
            for keyname in ('images','videos','assets'):
                for item in raw.get(keyname,[]) if isinstance(raw.get(keyname),list) else []:
                    if isinstance(item,str): uris.append(item)
                    elif isinstance(item,dict) and isinstance(item.get('url'),str): uris.append(item['url'])
        if not uris: raise ValueError('fal.ai returned no recognizable media URI')
        return [MediaResult(uri=u,provider=self.name,model=model,metadata={'provider_result':raw}) for u in uris]
