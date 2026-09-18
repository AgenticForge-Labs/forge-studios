from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from typing import Any

from .contracts import EpisodePackage, Shot


@dataclass(frozen=True)
class FramePlanIssue:
    code: str
    message: str
    shot_id: str
    predecessor_shot_id: str | None = None
    required_asset_id: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


class FramePlanError(ValueError):
    def __init__(self, issue: FramePlanIssue):
        self.issue = issue
        super().__init__(issue.message)


def ordered_shots(package: EpisodePackage) -> list[Shot]:
    return list(package.shots)


def predecessor_for(package: EpisodePackage, shot: Shot) -> Shot:
    chain_from = shot.frame_plan.chain_from_shot_id
    if not chain_from:
        raise FramePlanError(FramePlanIssue(
            "CHAINED_START_MISSING_PREDECESSOR",
            f"Shot {shot.shot_id!r} uses chained_start but has no chain_from_shot_id.",
            shot.shot_id,
        ))
    shots = ordered_shots(package)
    positions = {item.shot_id: index for index, item in enumerate(shots)}
    if chain_from not in positions:
        raise FramePlanError(FramePlanIssue(
            "CHAINED_START_UNKNOWN_PREDECESSOR",
            f"Shot {shot.shot_id!r} chains from unknown predecessor {chain_from!r}.",
            shot.shot_id,
            chain_from,
        ))
    if positions[chain_from] >= positions[shot.shot_id]:
        raise FramePlanError(FramePlanIssue(
            "CHAINED_START_PREDECESSOR_NOT_EARLIER",
            f"Shot {shot.shot_id!r} must chain from an earlier shot, not {chain_from!r}.",
            shot.shot_id,
            chain_from,
        ))
    return shots[positions[chain_from]]


def _editorial_boundary(shot: Shot) -> str:
    intent = shot.edit_intent if isinstance(shot.edit_intent, dict) else {}
    value = intent.get("editorial_boundary")
    return str(value).strip() if value is not None else ""


def _transition_mode(shot: Shot) -> str:
    intent = shot.edit_intent if isinstance(shot.edit_intent, dict) else {}
    value = intent.get("transition_mode")
    return str(value).strip() if value is not None else ""


def _transition_reason(shot: Shot) -> str:
    intent = shot.edit_intent if isinstance(shot.edit_intent, dict) else {}
    value = intent.get("transition_reason")
    return str(value).strip() if value is not None else ""


def _viewpoint(shot: Shot) -> str:
    intent = shot.edit_intent if isinstance(shot.edit_intent, dict) else {}
    explicit = str(intent.get("viewpoint") or "").strip()
    if explicit:
        return explicit
    camera = shot.camera if isinstance(shot.camera, dict) else {}
    text = " ".join(
        str(camera.get(key) or "")
        for key in ("shot_type", "framing", "distance", "composition", "axis")
    ).casefold()
    return "first_person" if any(
        marker in text for marker in ("first-person", "first person", "point of view", "pov")
    ) else "objective"


def _scene_location_by_shot(package: EpisodePackage) -> dict[str, str | None]:
    return {shot.shot_id: shot.site_id for shot in package.shots}


_TRANSIENT_BOUNDARY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("mid-motion", re.compile(
        r"\bmid[- ]?(?:jump|leap|fall|descent|landing|turn|stride|step|run|walk|flight)\b",
        re.IGNORECASE,
    )),
    ("while-moving", re.compile(
        r"\bwhile\s+(?:jumping|leaping|falling|landing|descending|turning|walking|running|flying)\b",
        re.IGNORECASE,
    )),
    ("during-motion", re.compile(
        r"\bduring\s+(?:the\s+)?(?:jump|leap|fall|descent|landing|turn|walk|run|flight)\b",
        re.IGNORECASE,
    )),
    ("motion-blur", re.compile(r"\bmotion[- ]blur(?:red)?\b", re.IGNORECASE)),
)

_NONVISUAL_BOUNDARY_PATTERN = re.compile(
    r"\b(?:camera\s+(?:moves|glides|pans|pushes|follows|zooms)|rack\s+focus|"
    r"ambient\s+(?:sound|audio)|sound\s+of|audible|voice|speaks?|says?|"
    r"walks?|walking|jumps?|jumping|lands?|landing|turns?|turning)\b",
    re.IGNORECASE,
)


def transient_boundary_markers(prompt: str | None) -> list[str]:
    if not isinstance(prompt, str) or not prompt.strip():
        return []
    return [name for name, pattern in _TRANSIENT_BOUNDARY_PATTERNS if pattern.search(prompt)]


def _normalized_boundary_prompt(prompt: str | None) -> str:
    if not isinstance(prompt, str):
        return ""
    return re.sub(r"[^a-z0-9]+", " ", prompt.casefold()).strip()


def _boundary_core(prompt: str | None) -> str:
    if not isinstance(prompt, str):
        return ""
    for marker in (
        "\n\nCanonical visual anchors",
        "\n\nNON-NEGOTIABLE VISUAL RULES",
        "\n\nProduction constraints",
    ):
        prompt = prompt.split(marker, 1)[0]
    return prompt.strip()


def _boundary_prompt_issues(shot: Shot, *, require_compositions: bool = False) -> list[FramePlanIssue]:
    if shot.render_strategy != "generated_video":
        return []
    if shot.frame_plan.mode not in {"start_only", "start_and_end"}:
        return []

    fields = ["start_frame_prompt"]
    if shot.frame_plan.mode == "start_and_end":
        fields.append("end_frame_prompt")

    issues: list[FramePlanIssue] = []
    for field_name in fields:
        prompt = getattr(shot, field_name, None)
        core = _boundary_core(prompt)
        if not core:
            if require_compositions:
                issues.append(FramePlanIssue(
                    "BOUNDARY_FRAME_COMPOSITION_MISSING",
                    f"Shot {shot.shot_id!r} {field_name} has no shot-specific settled composition.",
                    shot.shot_id,
                ))
            continue
        markers = transient_boundary_markers(prompt)
        if markers:
            issues.append(FramePlanIssue(
                "BOUNDARY_FRAME_TRANSIENT_ACTION",
                f"Shot {shot.shot_id!r} {field_name} describes transient motion ({', '.join(markers)}). "
                "Put intermediate movement in video_prompt instead.",
                shot.shot_id,
            ))
        if _NONVISUAL_BOUNDARY_PATTERN.search(core) or re.search(r"[\"“”][^\"“”]+[\"“”]", core):
            issues.append(FramePlanIssue(
                "BOUNDARY_FRAME_NONVISUAL_OR_TEMPORAL",
                f"Shot {shot.shot_id!r} {field_name} contains movement, dialogue, audio, or camera motion. "
                "Frame prompts must describe one static visible composition.",
                shot.shot_id,
            ))

    if shot.frame_plan.mode == "start_and_end":
        start = _normalized_boundary_prompt(_boundary_core(shot.start_frame_prompt))
        end = _normalized_boundary_prompt(_boundary_core(shot.end_frame_prompt))
        if start and end and start == end:
            issues.append(FramePlanIssue(
                "BOUNDARY_FRAME_PROMPTS_DUPLICATE",
                f"Shot {shot.shot_id!r} uses the same composition for start and end frames.",
                shot.shot_id,
            ))
        elif start and end and SequenceMatcher(None, start, end).ratio() > 0.88:
            issues.append(FramePlanIssue(
                "BOUNDARY_FRAME_PROMPTS_TOO_SIMILAR",
                f"Shot {shot.shot_id!r} start and end prompts are too similar to justify an authored end frame.",
                shot.shot_id,
            ))
    return issues


def _semantic_shot_payload(shot: Shot) -> dict[str, Any]:
    return {
        "shot_id": shot.shot_id,
        "beat_id": shot.beat_id,
        "duration_seconds": shot.duration_seconds,
        "site_id": shot.site_id,
        "site_area_id": shot.site_area_id,
        "character_ids": list(shot.character_ids),
        "visible_entity_ids": list(shot.visible_entity_ids),
        "reference_asset_ids": list(shot.reference_asset_ids),
        "reference_uses": dict(shot.reference_uses),
        "visual_constraints": dict(shot.visual_constraints),
        "frame_plan_mode": shot.frame_plan_mode,
        "inherits_start_from_shot_id": shot.inherits_start_from_shot_id,
        "start_frame_prompt": shot.start_frame_prompt,
        "end_frame_prompt": shot.end_frame_prompt,
        "video_prompt": shot.video_prompt,
    }


def package_semantic_fingerprint(package: EpisodePackage) -> str:
    payload = {
        "package_version": package.package_version,
        "production_id": package.production_id,
        "episode_id": package.episode_id,
        "revision": package.revision,
        "world_id": package.world_id,
        "target_duration_seconds": package.target_duration_seconds,
        "beats": package.beats,
        "shots": [_semantic_shot_payload(shot) for shot in package.shots],
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _stale_package_issue(package: EpisodePackage) -> FramePlanIssue | None:
    trace = package.trace if isinstance(package.trace, dict) else {}
    expected = trace.get("semantic_fingerprint")
    version = trace.get("semantic_fingerprint_version")
    if version not in {"v2", "v3"} or not isinstance(expected, str) or not expected:
        return None
    actual = package_semantic_fingerprint(package)
    if actual == expected:
        return None
    return FramePlanIssue(
        "STALE_DERIVED_PACKAGE_STATE",
        "EpisodePackage semantic production intent changed after derived production state was recorded. "
        "Re-run deterministic finalization before provider spend.",
        "__package__",
    )


def validate_frame_plans(
    package: EpisodePackage,
    *,
    require_approved_end_frames: bool = False,
    shot_id: str | None = None,
) -> list[FramePlanIssue]:
    issues: list[FramePlanIssue] = []
    stale = _stale_package_issue(package)
    if stale is not None:
        issues.append(stale)

    shots = ordered_shots(package)
    selected = [shot for shot in shots if shot_id is None or shot.shot_id == shot_id]
    positions = {item.shot_id: index for index, item in enumerate(shots)}
    locations = _scene_location_by_shot(package)

    for shot in selected:
        for role in ("start_frame", "end_frame"):
            camera = shot.camera.get(role)
            if camera is not None and not isinstance(camera, dict):
                issues.append(FramePlanIssue(
                    "INVALID_BOUNDARY_CAMERA",
                    f"camera.{role} must be a static camera object.",
                    shot.shot_id,
                ))
            elif isinstance(camera, dict) and camera.get("movement") not in (None, "", "none", "static", "locked"):
                issues.append(FramePlanIssue(
                    "TEMPORAL_BOUNDARY_CAMERA",
                    f"camera.{role} must not contain temporal movement.",
                    shot.shot_id,
                ))

        if shot.frame_plan.mode not in {"start_only", "start_and_end", "chained_start"}:
            issues.append(FramePlanIssue(
                "UNSUPPORTED_FRAME_PLAN_MODE",
                f"Shot {shot.shot_id!r} uses unsupported frame plan mode {shot.frame_plan.mode!r}.",
                shot.shot_id,
            ))
            continue
        issues.extend(_boundary_prompt_issues(shot, require_compositions=True))
        if shot.render_strategy == "generated_video" and shot.duration_seconds > 20 + 1e-6:
            issues.append(FramePlanIssue(
                "GENERATED_VIDEO_DURATION_EXCEEDED",
                f"Shot {shot.shot_id!r} is {shot.duration_seconds:g}s; generated video is capped at 20s.",
                shot.shot_id,
            ))
        if shot.frame_plan.mode == "start_only" and shot.frame_plan.chain_from_shot_id:
            issues.append(FramePlanIssue(
                "START_ONLY_MUST_NOT_CHAIN",
                f"Shot {shot.shot_id!r} is start_only and must begin from its own approved start frame.",
                shot.shot_id,
            ))

    # Endpoint inheritance remains available for future start_and_end/chained_start
    # experiments, but the current Forge Born v3 package does not use it.
    for shot in selected:
        transition_mode = _transition_mode(shot)
        if shot.frame_plan.mode == "start_only":
            if transition_mode not in {"", "new_composition"}:
                issues.append(FramePlanIssue(
                    "START_ONLY_REQUIRES_NEW_COMPOSITION",
                    f"Shot {shot.shot_id!r} is start_only and cannot declare endpoint inheritance.",
                    shot.shot_id,
                ))
            continue
        if shot.frame_plan.mode != "start_and_end":
            continue
        if transition_mode not in {"", "new_composition", "inherit_endpoint"}:
            issues.append(FramePlanIssue(
                "INVALID_CONTINUITY_MODE",
                f"Shot {shot.shot_id!r} has invalid transition_mode {transition_mode!r}.",
                shot.shot_id,
            ))
            continue
        if transition_mode in {"", "new_composition"}:
            if shot.frame_plan.chain_from_shot_id:
                issues.append(FramePlanIssue(
                    "NEW_COMPOSITION_MUST_NOT_CHAIN",
                    f"Shot {shot.shot_id!r} declares a new composition but chains from {shot.frame_plan.chain_from_shot_id!r}.",
                    shot.shot_id,
                    shot.frame_plan.chain_from_shot_id,
                ))
            continue

        index = positions[shot.shot_id]
        if index == 0:
            issues.append(FramePlanIssue(
                "FIRST_SHOT_CANNOT_INHERIT_ENDPOINT",
                f"Shot {shot.shot_id!r} is first and cannot inherit an endpoint.",
                shot.shot_id,
            ))
            continue
        predecessor = shots[index - 1]
        if shot.frame_plan.chain_from_shot_id != predecessor.shot_id:
            issues.append(FramePlanIssue(
                "EXPLICIT_ENDPOINT_HANDOFF_REQUIRED",
                f"Shot {shot.shot_id!r} explicitly inherits and must chain from immediate predecessor {predecessor.shot_id!r}.",
                shot.shot_id,
                predecessor.shot_id,
            ))
        if _editorial_boundary(shot):
            issues.append(FramePlanIssue(
                "INHERITED_ENDPOINT_HAS_EDITORIAL_BOUNDARY",
                f"Shot {shot.shot_id!r} cannot both inherit an exact endpoint and declare an editorial boundary.",
                shot.shot_id,
                predecessor.shot_id,
            ))
        if locations.get(predecessor.shot_id) != locations.get(shot.shot_id):
            issues.append(FramePlanIssue(
                "LOCATION_CHANGE_CANNOT_INHERIT_ENDPOINT",
                f"Shot {shot.shot_id!r} changes location and must start from a new composition.",
                shot.shot_id,
                predecessor.shot_id,
            ))
        if _viewpoint(predecessor) != _viewpoint(shot):
            issues.append(FramePlanIssue(
                "POV_CHANGE_CANNOT_INHERIT_ENDPOINT",
                f"Shot {shot.shot_id!r} changes viewpoint and must start from a new composition.",
                shot.shot_id,
                predecessor.shot_id,
            ))
        reason = _transition_reason(shot)
        if reason and reason not in {"continuous_action", "technical_duration_split"}:
            issues.append(FramePlanIssue(
                "INVALID_ENDPOINT_HANDOFF_REASON",
                f"Shot {shot.shot_id!r} inherits for non-continuous reason {reason!r}.",
                shot.shot_id,
                predecessor.shot_id,
            ))

    for shot in selected:
        inherits_endpoint = (
            shot.frame_plan.mode == "chained_start"
            or (shot.frame_plan.mode == "start_and_end" and bool(shot.frame_plan.chain_from_shot_id))
        )
        if not inherits_endpoint:
            continue
        try:
            predecessor = predecessor_for(package, shot)
        except FramePlanError as exc:
            issues.append(exc.issue)
            continue
        endpoint = predecessor.approved_end_frame_asset_id
        if endpoint and shot.frame_plan.start_asset_id and shot.frame_plan.start_asset_id != endpoint:
            issues.append(FramePlanIssue(
                "CHAINED_START_ASSET_MISMATCH",
                f"Shot {shot.shot_id!r} explicitly names start asset {shot.frame_plan.start_asset_id!r}, "
                f"but predecessor {predecessor.shot_id!r} ends at approved asset {endpoint!r}.",
                shot.shot_id,
                predecessor.shot_id,
                endpoint,
            ))
        if endpoint and shot.approved_start_frame_asset_id and shot.approved_start_frame_asset_id != endpoint:
            issues.append(FramePlanIssue(
                "CHAINED_START_APPROVED_ASSET_MISMATCH",
                f"Shot {shot.shot_id!r} approves a start asset different from predecessor {predecessor.shot_id!r}'s endpoint.",
                shot.shot_id,
                predecessor.shot_id,
                endpoint,
            ))
        if require_approved_end_frames and not endpoint:
            issues.append(FramePlanIssue(
                "CHAINED_START_ENDPOINT_MISSING",
                f"Shot {shot.shot_id!r} cannot generate video until predecessor {predecessor.shot_id!r} has an approved end frame.",
                shot.shot_id,
                predecessor.shot_id,
            ))
    return issues
