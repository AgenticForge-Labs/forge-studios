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
    duration_seconds: float|None=None
    options: dict[str,Any]=field(default_factory=dict)

@dataclass
class MediaResult:
    uri: str
    provider: str
    model: str|None=None
    metadata: dict[str,Any]=field(default_factory=dict)

class ProviderGenerationError(RuntimeError):
    def __init__(self, message: str, *, provider: str, model: str|None=None, request_id: str|None=None, diagnostics: dict[str,Any]|None=None):
        super().__init__(message)
        self.provider=provider
        self.model=model
        self.request_id=request_id
        self.diagnostics=dict(diagnostics or {})

    def as_dict(self) -> dict[str,Any]:
        return {'provider':self.provider,'model':self.model,'request_id':self.request_id,**self.diagnostics}

class MediaProvider(Protocol):
    name: str
    def generate(self, request: MediaRequest) -> list[MediaResult]: ...
