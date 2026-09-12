from __future__ import annotations
import base64
from pathlib import Path
from .base import MediaRequest, MediaResult

_PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')

class MockProvider:
    name='mock'
    def __init__(self, output_dir: str|Path='outputs/mock'):
        self.output_dir=Path(output_dir)
    def generate(self, request: MediaRequest) -> list[MediaResult]:
        self.output_dir.mkdir(parents=True,exist_ok=True)
        if request.kind=='image':
            path=self.output_dir/f'{request.shot_id}-{request.role}.png'; path.write_bytes(_PNG)
        else:
            path=self.output_dir/f'{request.shot_id}-{request.role}.mock-video.json'; path.write_text('{}\n')
        return [MediaResult(uri=str(path.resolve()),provider=self.name,model='mock')]
