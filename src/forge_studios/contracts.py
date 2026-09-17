from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field, model_validator

class FramePlan(BaseModel):
    """Internal Studios boundary state compiled from the v2 package."""
    model_config = ConfigDict(extra='forbid')
    mode: Literal['start_and_end'] = 'start_and_end'
    chain_from_shot_id: str | None = None
    start_asset_id: str | None = None
    end_asset_id: str | None = None
    capture_generated_first_frame: bool = False
    capture_generated_last_frame: bool = False

class Shot(BaseModel):
    model_config = ConfigDict(extra='forbid')
    shot_id: str
    beat_id: str
    duration_seconds: float = Field(gt=0, le=20)
    site_id: str
    character_ids: list[str] = Field(default_factory=list)
    visible_entity_ids: list[str] = Field(default_factory=list)
    reference_asset_ids: list[str] = Field(default_factory=list)
    inherits_start_from_shot_id: str | None = None
    start_frame_prompt: str
    end_frame_prompt: str
    video_prompt: str
    start_frame_asset_ids: list[str] = Field(default_factory=list)
    approved_start_frame_asset_id: str | None = None
    end_frame_asset_ids: list[str] = Field(default_factory=list)
    approved_end_frame_asset_id: str | None = None
    candidate_clip_asset_ids: list[str] = Field(default_factory=list)
    approved_clip_asset_id: str | None = None
    final_clip_asset_id: str | None = None
    status: str = 'prompt_ready'

    # Internal adapters for existing deterministic Studios services. They are
    # never serialized into episode_package_v2.
    source_beat_ids: list[str] = Field(default_factory=list, exclude=True)
    purpose: str = Field(default='', exclude=True)
    visual: str = Field(default='', exclude=True)
    entity_ids: list[str] = Field(default_factory=list, exclude=True)
    dialogue_ids: list[str] = Field(default_factory=list, exclude=True)
    camera: dict[str, Any] = Field(default_factory=dict, exclude=True)
    visual_constraints: dict[str, Any] = Field(default_factory=dict, exclude=True)
    performance_intent: dict[str, Any] = Field(default_factory=dict, exclude=True)
    edit_intent: dict[str, Any] = Field(default_factory=dict, exclude=True)
    continuity_asset_ids: list[str] = Field(default_factory=list, exclude=True)
    execution_route: Literal['animator'] = Field(default='animator', exclude=True)
    render_strategy: Literal['generated_video'] = Field(default='generated_video', exclude=True)
    frame_plan: FramePlan = Field(default_factory=FramePlan, exclude=True)
    image_prompt: str | None = Field(default=None, exclude=True)
    storyboard_prompt: str | None = Field(default=None, exclude=True)
    provider_options: dict[str, Any] = Field(default_factory=dict, exclude=True)
    storyboard_asset_ids: list[str] = Field(default_factory=list, exclude=True)
    approved_storyboard_asset_id: str | None = Field(default=None, exclude=True)

    @model_validator(mode='after')
    def compile_internal_video_state(self) -> 'Shot':
        self.source_beat_ids=[self.beat_id]
        self.visual=self.end_frame_prompt
        self.entity_ids=list(dict.fromkeys([*self.character_ids,*self.visible_entity_ids,self.site_id]))
        self.continuity_asset_ids=list(self.reference_asset_ids)
        self.frame_plan.mode='start_and_end'
        self.frame_plan.chain_from_shot_id=self.inherits_start_from_shot_id
        self.edit_intent={
            'transition_mode':'inherit_endpoint' if self.inherits_start_from_shot_id else 'new_composition'
        }
        return self

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
    model_config = ConfigDict(extra='forbid')
    package_version: Literal['episode_package_v2']
    production_id: str
    episode_id: str
    revision: int = 1
    world_id: str | None = None
    show_id: str
    title: str
    premise: str = ''
    arc: str
    themes: list[str] = Field(default_factory=list)
    status: str = 'prompt_ready'
    target_duration_seconds: float
    beats: list[dict[str, Any]] = Field(default_factory=list)
    shots: list[Shot] = Field(default_factory=list)
    assets: list[AssetRecord] = Field(default_factory=list)
    trace: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode='after')
    def unique_ids(self) -> 'EpisodePackage':
        shot_ids = [s.shot_id for s in self.shots]
        if len(shot_ids) != len(set(shot_ids)):
            raise ValueError('shot ids must be unique')
        asset_ids = [asset.asset_id for asset in self.assets]
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError('asset ids must be unique')
        known = set(shot_ids)
        known_beats={str(beat.get('beat_id')) for beat in self.beats if isinstance(beat,dict) and beat.get('beat_id')}
        known_assets=set(asset_ids)
        covered=set()
        for index, shot in enumerate(self.shots):
            if shot.beat_id not in known_beats:
                raise ValueError(f'shot {shot.shot_id!r} references unknown beat {shot.beat_id!r}')
            covered.add(shot.beat_id)
            missing_assets=set(shot.reference_asset_ids)-known_assets
            if missing_assets:
                raise ValueError(f'shot {shot.shot_id!r} references unknown assets: {sorted(missing_assets)}')
            predecessor=shot.inherits_start_from_shot_id
            if predecessor and (index == 0 or self.shots[index-1].shot_id != predecessor):
                raise ValueError(f'shot {shot.shot_id!r} must inherit from its immediate predecessor')
            if predecessor not in known and predecessor is not None:
                raise ValueError(f'shot {shot.shot_id!r} inherits from unknown shot {predecessor!r}')
            if predecessor:
                previous=self.shots[index-1]
                if shot.site_id != previous.site_id:
                    raise ValueError(f'shot {shot.shot_id!r} cannot inherit across a site change')
                if shot.start_frame_prompt.strip() != previous.end_frame_prompt.strip():
                    raise ValueError(f'shot {shot.shot_id!r} inherited start prompt must equal its predecessor end prompt')
        missing_beats=known_beats-covered
        if missing_beats:
            raise ValueError(f'planned beats are not covered by package shots: {sorted(missing_beats)}')
        total=sum(shot.duration_seconds for shot in self.shots)
        if abs(total-self.target_duration_seconds)>0.5:
            raise ValueError(f'package duration is {total:g}s but target is {self.target_duration_seconds:g}s')
        return self

    def find_shot(self, shot_id: str) -> Shot:
        for shot in self.shots:
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
