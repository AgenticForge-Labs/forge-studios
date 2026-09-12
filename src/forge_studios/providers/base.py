from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Protocol

@dataclass
class MediaRequest:
    kind: str
    shot_id: str
    prompt: str
    role: str='storyboard'
    reference_assets: tuple[str,...]=()
    start_frame_asset: str|None=None
    end_frame_asset: str|None=None
    options: dict[str,Any]=field(default_factory=dict)

@dataclass
class MediaResult:
    uri: str
    provider: str
    model: str|None=None
    metadata: dict[str,Any]=field(default_factory=dict)

class MediaProvider(Protocol):
    name: str
    def generate(self, request: MediaRequest) -> list[MediaResult]: ...
