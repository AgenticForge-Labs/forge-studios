from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field, model_validator

class FramePlan(BaseModel):
    model_config = ConfigDict(extra='allow')
    mode: Literal['still','start_only','start_and_end','chained_start'] = 'still'
    chain_from_shot_id: str | None = None
    start_asset_id: str | None = None
    end_asset_id: str | None = None
    capture_generated_first_frame: bool = False
    capture_generated_last_frame: bool = False

class Shot(BaseModel):
    model_config = ConfigDict(extra='allow')
    shot_id: str
    source_beat_ids: list[str] = Field(default_factory=list)
    duration_seconds: float = Field(gt=0)
    purpose: str = ''
    visual: str
    entity_ids: list[str] = Field(default_factory=list)
    dialogue_ids: list[str] = Field(default_factory=list)
    camera: dict[str, Any] = Field(default_factory=dict)
    visual_constraints: dict[str, Any] = Field(default_factory=dict)
    performance_intent: dict[str, Any] = Field(default_factory=dict)
    edit_intent: dict[str, Any] = Field(default_factory=dict)
    continuity_asset_ids: list[str] = Field(default_factory=list)
    execution_route: Literal['animator','puppeteer','hybrid'] = 'animator'
    render_strategy: Literal['still','still_motion','generated_video','physical','hybrid'] = 'still'
    frame_plan: FramePlan = Field(default_factory=FramePlan)
    image_prompt: str | None = None
    storyboard_prompt: str | None = None
    start_frame_prompt: str | None = None
    end_frame_prompt: str | None = None
    video_prompt: str | None = None
    provider_options: dict[str, Any] = Field(default_factory=dict)
    storyboard_asset_ids: list[str] = Field(default_factory=list)
    approved_storyboard_asset_id: str | None = None
    start_frame_asset_ids: list[str] = Field(default_factory=list)
    approved_start_frame_asset_id: str | None = None
    end_frame_asset_ids: list[str] = Field(default_factory=list)
    approved_end_frame_asset_id: str | None = None
    candidate_clip_asset_ids: list[str] = Field(default_factory=list)
    approved_clip_asset_id: str | None = None
    final_clip_asset_id: str | None = None
    physical_take_ids: list[str] = Field(default_factory=list)
    approved_take_id: str | None = None
    status: str = 'planned'

class Scene(BaseModel):
    model_config = ConfigDict(extra='allow')
    scene_id: str
    source_beat_ids: list[str] = Field(default_factory=list)
    location_id: str | None = None
    summary: str = ''
    shots: list[Shot] = Field(default_factory=list)

class AssetRecord(BaseModel):
    model_config = ConfigDict(extra='allow')
    asset_id: str = Field(default_factory=lambda: f'asset_{uuid4().hex}')
    world_id: str | None = None
    entity_id: str | None = None
    related_entity_ids: list[str] = Field(default_factory=list)
    kind: str
    uri: str
    storage_key: str | None = None
    status: str = 'candidate'
    authority: str = 'generated'
    episode_id: str | None = None
    scene_id: str | None = None
    shot_id: str | None = None
    attempt_id: str | None = None
    source_asset_ids: list[str] = Field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    logical_key: str | None = None
    role: str | None = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

class EpisodePackage(BaseModel):
    model_config = ConfigDict(extra='allow')
    package_version: Literal['episode_package_v1'] = 'episode_package_v1'
    production_id: str
    episode_id: str
    revision: int = 1
    world_id: str | None = None
    series_id: str | None = None
    title: str
    premise: str = ''
    status: str = 'draft'
    target_duration_seconds: float | None = None
    beats: list[dict[str, Any]] = Field(default_factory=list)
    dialogue: list[dict[str, Any]] = Field(default_factory=list)
    scenes: list[Scene] = Field(default_factory=list)
    assets: list[AssetRecord] = Field(default_factory=list)
    trace: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode='after')
    def unique_ids(self) -> 'EpisodePackage':
        shot_ids = [s.shot_id for scene in self.scenes for s in scene.shots]
        if len(shot_ids) != len(set(shot_ids)):
            raise ValueError('shot ids must be unique')
        return self

    def find_shot(self, shot_id: str) -> Shot:
        for scene in self.scenes:
            for shot in scene.shots:
                if shot.shot_id == shot_id:
                    return shot
        raise KeyError(shot_id)

    def find_asset(self, asset_id: str) -> AssetRecord:
        for asset in self.assets:
            if asset.asset_id == asset_id:
                return asset
        raise KeyError(asset_id)

class GenerationAttempt(BaseModel):
    attempt_id: str = Field(default_factory=lambda: f'attempt_{uuid4().hex}')
    production_id: str
    episode_id: str
    scene_id: str | None = None
    shot_id: str
    role: Literal['storyboard','start_frame','end_frame','video','physical_take','final_render']
    provider: str
    model: str | None = None
    prompt: str | None = None
    reference_asset_ids: list[str] = Field(default_factory=list)
    options: dict[str, Any] = Field(default_factory=dict)
    outcome: Literal['started','succeeded','failed','rejected','approved'] = 'started'
    asset_ids: list[str] = Field(default_factory=list)
    error: str | None = None
    cost_usd: float | None = None
    latency_ms: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
