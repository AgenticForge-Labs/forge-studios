from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .contracts import EpisodePackage
from .telemetry import TelemetrySink


class ShortShotReframe(BaseModel):
    model_config = ConfigDict(extra="allow")
    shot_id: str
    crop_anchor_x: float = Field(default=0.5, ge=0, le=1)
    crop_anchor_y: float = Field(default=0.5, ge=0, le=1)
    safe_region_notes: list[str] = Field(default_factory=list)


class ShortFormUnit(BaseModel):
    model_config = ConfigDict(extra="allow")
    short_id: str
    title: str
    source_beat_ids: list[str] = Field(default_factory=list)
    source_scene_ids: list[str] = Field(default_factory=list)
    source_shot_ids: list[str] = Field(default_factory=list)
    target_platforms: list[str] = Field(default_factory=list)
    target_duration_seconds: float = Field(default=25.0, gt=0, le=90)
    hook: str
    payoff: str = ""
    standalone: bool = True
    priority: str = "medium"
    reframes: list[ShortShotReframe] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    def reframe_for(self, shot_id: str) -> ShortShotReframe:
        for item in self.reframes:
            if item.shot_id == shot_id:
                return item
        return ShortShotReframe(shot_id=shot_id)


def short_form_units(package: EpisodePackage) -> list[ShortFormUnit]:
    raw = getattr(package, "short_form_units", []) or []
    return [ShortFormUnit.model_validate(item) for item in raw]


def find_short(package: EpisodePackage, short_id: str) -> ShortFormUnit:
    for unit in short_form_units(package):
        if unit.short_id == short_id:
            return unit
    raise KeyError(short_id)


def _chosen_asset_id(shot) -> str:
    chosen = (
        shot.final_clip_asset_id
        or shot.approved_clip_asset_id
        or shot.approved_start_frame_asset_id
    )
    if not chosen:
        raise ValueError(f"shot {shot.shot_id} has no approved media")
    return chosen


def resolve_short_media(package: EpisodePackage, short_id: str) -> list[dict[str, Any]]:
    unit = find_short(package, short_id)
    timeline: list[dict[str, Any]] = []
    t = 0.0
    for shot_id in unit.source_shot_ids:
        shot = package.find_shot(shot_id)
        asset_id = _chosen_asset_id(shot)
        asset = package.find_asset(asset_id)
        reframe = unit.reframe_for(shot_id)
        timeline.append({
            "shot_id": shot_id,
            "asset_id": asset_id,
            "uri": asset.uri,
            "kind": asset.kind,
            "start_seconds": t,
            "duration_seconds": shot.duration_seconds,
            "crop_anchor_x": reframe.crop_anchor_x,
            "crop_anchor_y": reframe.crop_anchor_y,
            "safe_region_notes": reframe.safe_region_notes,
        })
        t += shot.duration_seconds
    return timeline


def write_short_edit_plan(package: EpisodePackage, short_id: str, path: str | Path) -> Path:
    unit = find_short(package, short_id)
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "production_id": package.production_id,
        "episode_id": package.episode_id,
        "short": unit.model_dump(mode="json"),
        "timeline": resolve_short_media(package, short_id),
    }
    out.write_text(json.dumps(payload, indent=2) + "\n")
    return out


def vertical_filter(width: int, height: int, anchor_x: float, anchor_y: float) -> str:
    """Scale to fill then crop around a normalized anchor.

    This deliberately keeps v1 deterministic. Later subject/face tracking can
    provide time-varying anchors without changing the short-form contract.
    """
    x = min(1.0, max(0.0, anchor_x))
    y = min(1.0, max(0.0, anchor_y))
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height}:(iw-ow)*{x:.4f}:(ih-oh)*{y:.4f},format=yuv420p"
    )


def render_short(
    package: EpisodePackage,
    short_id: str,
    output: str | Path,
    *,
    ffmpeg: str = "ffmpeg",
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
    telemetry: TelemetrySink | None = None,
) -> Path:
    timeline = resolve_short_media(package, short_id)
    output = Path(output)
    work = output.parent / f"{output.stem}-segments"
    work.mkdir(parents=True, exist_ok=True)
    segments: list[Path] = []

    for i, item in enumerate(timeline):
        uri = item["uri"]
        if uri.startswith("http://") or uri.startswith("https://"):
            raise ValueError("Short renderer requires local media paths; download remote assets first")
        src = Path(uri)
        seg = work / f"{i:04d}-{item['shot_id']}.mp4"
        duration = str(item["duration_seconds"])
        vf = vertical_filter(
            width,
            height,
            item["crop_anchor_x"],
            item["crop_anchor_y"],
        )
        if item["kind"] in {"storyboard_image", "start_frame", "end_frame", "generated_image", "image"}:
            cmd = [ffmpeg, "-y", "-loop", "1", "-i", str(src), "-t", duration, "-vf", vf, "-r", str(fps), "-an", str(seg)]
        else:
            cmd = [ffmpeg, "-y", "-i", str(src), "-t", duration, "-vf", vf, "-r", str(fps), "-an", str(seg)]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        segments.append(seg)

    concat = work / "concat.txt"
    concat.write_text("".join(f"file '{p.resolve()}'\n" for p in segments))
    subprocess.run(
        [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", str(output)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    (telemetry or TelemetrySink()).emit(
        "short_render.completed",
        production_id=package.production_id,
        episode_id=package.episode_id,
        short_id=short_id,
        uri=str(output.resolve()),
        duration_seconds=sum(item["duration_seconds"] for item in timeline),
        width=width,
        height=height,
    )
    return output
