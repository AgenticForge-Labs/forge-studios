from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FramePlan(BaseModel):
    """Internal boundary state compiled from the public package.

    mode controls whether this shot authors an outgoing destination frame.
    chain_from_shot_id independently controls whether its incoming start is the
    exact approved endpoint of the preceding shot.
    """

    model_config = ConfigDict(extra="forbid")
    mode: Literal["start_only", "start_and_end"] = "start_only"
    chain_from_shot_id: str | None = None
    start_asset_id: str | None = None
    end_asset_id: str | None = None
    capture_generated_first_frame: bool = False
    capture_generated_last_frame: bool = False


class VocabularySelection(BaseModel):
    """Stable semantic vocabulary choice preserved from Forge Worlds."""

    model_config = ConfigDict(extra="forbid")

    vocabulary_id: str
    term_id: str
    purpose: str = ""


class Shot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shot_id: str
    duration_seconds: float = Field(gt=0, le=20)
    site_id: str
    site_area_id: str | None = None
    character_ids: list[str] = Field(default_factory=list)
    visible_entity_ids: list[str] = Field(default_factory=list)
    cinematic_choices: list[VocabularySelection] = Field(default_factory=list)
    reference_asset_ids: list[str] = Field(default_factory=list)
    reference_uses: dict[str, str] = Field(default_factory=dict)
    frame_plan_mode: Literal["start_only", "start_and_end"] | None = None
    inherits_start_from_shot_id: str | None = None
    start_frame_prompt: str
    video_prompt: str
    end_frame_prompt: str | None = None
    start_frame_asset_ids: list[str] = Field(default_factory=list)
    approved_start_frame_asset_id: str | None = None
    end_frame_asset_ids: list[str] = Field(default_factory=list)
    approved_end_frame_asset_id: str | None = None
    candidate_clip_asset_ids: list[str] = Field(default_factory=list)
    approved_clip_asset_id: str | None = None
    final_clip_asset_id: str | None = None
    status: str = "prompt_ready"

    # Internal adapters for deterministic Studios services. They are excluded from
    # EpisodePackage serialization and are not creative LLM fields.
    source_requirement_ids: list[str] = Field(default_factory=list, exclude=True)
    purpose: str = Field(default="", exclude=True)
    visual: str = Field(default="", exclude=True)
    entity_ids: list[str] = Field(default_factory=list, exclude=True)
    dialogue_ids: list[str] = Field(default_factory=list, exclude=True)
    camera: dict[str, Any] = Field(default_factory=dict, exclude=True)
    visual_constraints: dict[str, Any] = Field(default_factory=dict)
    performance_intent: dict[str, Any] = Field(default_factory=dict, exclude=True)
    edit_intent: dict[str, Any] = Field(default_factory=dict, exclude=True)
    continuity_asset_ids: list[str] = Field(default_factory=list, exclude=True)
    execution_route: Literal["animator"] = Field(default="animator", exclude=True)
    render_strategy: Literal["generated_video"] = Field(default="generated_video", exclude=True)
    frame_plan: FramePlan = Field(default_factory=FramePlan, exclude=True)
    provider_options: dict[str, Any] = Field(default_factory=dict, exclude=True)

    @model_validator(mode="after")
    def compile_internal_video_state(self) -> Shot:
        unknown_reference_uses = set(self.reference_uses) - set(self.reference_asset_ids)
        if unknown_reference_uses:
            raise ValueError(
                f"reference_uses names assets not present in reference_asset_ids: {sorted(unknown_reference_uses)}"
            )
        mode = self.frame_plan_mode or (
            "start_and_end" if (self.end_frame_prompt or "").strip() else "start_only"
        )
        self.frame_plan_mode = mode
        self.source_requirement_ids = [self.shot_id]
        self.visual = self.end_frame_prompt or self.start_frame_prompt
        self.entity_ids = list(dict.fromkeys([
            *self.character_ids,
            *self.visible_entity_ids,
            self.site_id,
        ]))
        self.continuity_asset_ids = list(self.reference_asset_ids)
        self.frame_plan.mode = mode
        self.frame_plan.chain_from_shot_id = self.inherits_start_from_shot_id
        self.edit_intent = {
            "transition_mode": "inherit_endpoint" if self.inherits_start_from_shot_id else "new_composition"
        }
        if mode == "start_and_end" and not (self.end_frame_prompt or "").strip():
            raise ValueError("start_and_end shot requires end_frame_prompt")
        if mode == "start_only" and self.end_frame_prompt is not None:
            raise ValueError("start_only shot must not carry end_frame_prompt")
        return self


class AssetRecord(BaseModel):
    model_config = ConfigDict(extra="allow")
    asset_id: str = Field(default_factory=lambda: f"asset_{uuid4().hex}")
    world_id: str | None = None
    entity_id: str | None = None
    related_entity_ids: list[str] = Field(default_factory=list)
    kind: str
    uri: str
    storage_key: str | None = None
    status: str = "candidate"
    authority: str = "generated"
    episode_id: str | None = None
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
    model_config = ConfigDict(extra="forbid")

    package_version: Literal["episode_package"] = "episode_package"
    production_id: str
    episode_id: str
    revision: int = 1
    world_id: str | None = None
    show_id: str
    title: str
    premise: str = ""
    arc: str
    tone: str = ""
    themes: list[str] = Field(default_factory=list)
    status: str = "prompt_ready"
    target_duration_seconds: float
    shots: list[Shot] = Field(default_factory=list)
    assets: list[AssetRecord] = Field(default_factory=list)
    trace: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_ids(self) -> EpisodePackage:
        shot_ids = [shot.shot_id for shot in self.shots]
        if len(shot_ids) != len(set(shot_ids)):
            raise ValueError("shot ids must be unique")
        asset_ids = [asset.asset_id for asset in self.assets]
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("asset ids must be unique")
        known_assets = set(asset_ids)
        for shot in self.shots:
            missing_assets = set(shot.reference_asset_ids) - known_assets
            if missing_assets:
                raise ValueError(
                    f"shot {shot.shot_id!r} references unknown assets: {sorted(missing_assets)}"
                )
        total = sum(shot.duration_seconds for shot in self.shots)
        if abs(total - self.target_duration_seconds) > 0.5:
            raise ValueError(
                f"package duration is {total:g}s but target is {self.target_duration_seconds:g}s"
            )
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
    attempt_id: str = Field(default_factory=lambda: f"attempt_{uuid4().hex}")
    production_id: str
    episode_id: str
    shot_id: str
    role: Literal["start_frame", "end_frame", "video", "physical_take", "final_render"]
    provider: str
    model: str | None = None
    prompt: str | None = None
    reference_asset_ids: list[str] = Field(default_factory=list)
    options: dict[str, Any] = Field(default_factory=dict)
    outcome: Literal["started", "succeeded", "failed", "rejected", "approved"] = "started"
    asset_ids: list[str] = Field(default_factory=list)
    error: str | None = None
    cost_usd: float | None = None
    latency_ms: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
