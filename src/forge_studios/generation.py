from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class GenerationMode(str, Enum):
    CHEAP = "cheap"
    NORMAL = "normal"


@dataclass(frozen=True)
class ImageGenerationProfile:
    provider: str
    generate_model: str
    edit_model: str
    width: int | None = None
    height: int | None = None


@dataclass(frozen=True)
class VideoGenerationProfile:
    provider: str
    model: str
    width: int | None = None
    height: int | None = None
    resolution: str | None = None


@dataclass(frozen=True)
class StudiosGenerationProfile:
    mode: GenerationMode
    image: ImageGenerationProfile
    video: VideoGenerationProfile
    explicit_overrides: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode.value,
            "image_provider": self.image.provider,
            "image_generate_model": self.image.generate_model,
            "image_edit_model": self.image.edit_model,
            "image_width": self.image.width,
            "image_height": self.image.height,
            "video_provider": self.video.provider,
            "video_model": self.video.model,
            "video_width": self.video.width,
            "video_height": self.video.height,
            "video_resolution": self.video.resolution,
            "explicit_overrides": list(self.explicit_overrides),
        }


_PROFILES = {
    GenerationMode.CHEAP: StudiosGenerationProfile(
        mode=GenerationMode.CHEAP,
        image=ImageGenerationProfile(
            provider="fal",
            generate_model="fal-ai/flux-2/flash",
            edit_model="fal-ai/flux-2/flash/edit",
            width=768,
            height=432,
        ),
        video=VideoGenerationProfile(
            provider="fal",
            model="fal-ai/ltx-2.3-22b/distilled/image-to-video",
            width=768,
            height=432,
        ),
    ),
    GenerationMode.NORMAL: StudiosGenerationProfile(
        mode=GenerationMode.NORMAL,
        image=ImageGenerationProfile(
            provider="fal",
            generate_model="fal-ai/flux-2/flash",
            edit_model="fal-ai/flux-2-pro/edit",
            width=1920,
            height=1080,
        ),
        video=VideoGenerationProfile(
            provider="fal",
            model="fal-ai/ltx-2.3/image-to-video/fast",
            resolution="1080p",
        ),
    ),
}


def parse_generation_mode(value: str | GenerationMode | None) -> GenerationMode:
    if value is None:
        return GenerationMode.NORMAL
    if isinstance(value, GenerationMode):
        return value
    try:
        return GenerationMode(str(value).strip().lower())
    except ValueError as exc:
        allowed = ", ".join(mode.value for mode in GenerationMode)
        raise ValueError(f"invalid generation mode {value!r}; expected one of: {allowed}") from exc


def resolve_generation_profile(
    mode: str | GenerationMode | None = None,
    *,
    image_generate_model: str | None = None,
    image_edit_model: str | None = None,
    video_model: str | None = None,
    image_width: int | None = None,
    image_height: int | None = None,
    video_width: int | None = None,
    video_height: int | None = None,
    video_resolution: str | None = None,
) -> StudiosGenerationProfile:
    selected = parse_generation_mode(mode)
    base = _PROFILES[selected]
    overrides: list[str] = []

    def _model(value: str | None, current: str, label: str) -> str:
        if value is None:
            return current
        resolved = value.strip()
        if not resolved:
            raise ValueError(f"{label} override cannot be blank")
        overrides.append(label)
        return resolved

    resolved_image_width = base.image.width if image_width is None else image_width
    resolved_image_height = base.image.height if image_height is None else image_height
    if (image_width is None) != (image_height is None):
        raise ValueError("image width and height overrides must be supplied together")
    if image_width is not None:
        if image_width <= 0 or image_height is None or image_height <= 0:
            raise ValueError("image dimensions must be positive")
        overrides.append("image_size")

    resolved_video_width = base.video.width if video_width is None else video_width
    resolved_video_height = base.video.height if video_height is None else video_height
    resolved_video_resolution = base.video.resolution if video_resolution is None else video_resolution.strip()
    if (video_width is None) != (video_height is None):
        raise ValueError("video width and height overrides must be supplied together")
    if video_width is not None:
        if video_width <= 0 or video_height is None or video_height <= 0:
            raise ValueError("video dimensions must be positive")
        resolved_video_resolution = None
        overrides.append("video_size")
    if video_resolution is not None:
        if not resolved_video_resolution:
            raise ValueError("video resolution override cannot be blank")
        resolved_video_width = None
        resolved_video_height = None
        overrides.append("video_resolution")

    return StudiosGenerationProfile(
        mode=selected,
        image=ImageGenerationProfile(
            provider="fal",
            generate_model=_model(image_generate_model, base.image.generate_model, "image_generate_model"),
            edit_model=_model(image_edit_model, base.image.edit_model, "image_edit_model"),
            width=resolved_image_width,
            height=resolved_image_height,
        ),
        video=VideoGenerationProfile(
            provider="fal",
            model=_model(video_model, base.video.model, "video_model"),
            width=resolved_video_width,
            height=resolved_video_height,
            resolution=resolved_video_resolution,
        ),
        explicit_overrides=tuple(overrides),
    )
