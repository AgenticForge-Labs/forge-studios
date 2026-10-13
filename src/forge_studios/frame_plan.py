from __future__ import annotations

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
            "ENDPOINT_HANDOFF_MISSING_PREDECESSOR",
            f"Shot {shot.shot_id!r} inherits an endpoint but has no chain_from_shot_id.",
            shot.shot_id,
        ))
    shots = ordered_shots(package)
    positions = {item.shot_id: index for index, item in enumerate(shots)}
    if chain_from not in positions:
        raise FramePlanError(FramePlanIssue(
            "ENDPOINT_HANDOFF_UNKNOWN_PREDECESSOR",
            f"Shot {shot.shot_id!r} chains from unknown predecessor {chain_from!r}.",
            shot.shot_id,
            chain_from,
        ))
    if positions[chain_from] >= positions[shot.shot_id]:
        raise FramePlanError(FramePlanIssue(
            "ENDPOINT_HANDOFF_PREDECESSOR_NOT_EARLIER",
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


def _site_by_shot(package: EpisodePackage) -> dict[str, str | None]:
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
    r"walks?|walking|jumps?|jumping|lands?|turns?|turning|"
    # "landing" can be a static architectural surface (stair/upper landing).
    # Reject the action sense, including progressive landing and landing onto
    # a target, without disallowing an otherwise settled composition.
    r"(?:is|are|was|were|starts?|begins?)\s+landing|"
    r"landing\s+(?:on|onto|from|in))\b",
    re.IGNORECASE,
)


def _nonvisual_boundary_evidence(
    sources: list[tuple[str, str]],
) -> str | None:
    """Explain the exact source/phrase without altering provider prompt content."""
    quoted = re.compile(r'["“”][^"“”]+["“”]')
    for location, content in sources:
        matches = [
            (match.start(), match)
            for pattern in (_NONVISUAL_BOUNDARY_PATTERN, quoted)
            if (match := pattern.search(content)) is not None
        ]
        if not matches:
            continue
        _, match = min(matches, key=lambda item: item[0])
        excerpt = " ".join(
            content[max(0, match.start() - 55):match.end() + 55].split()
        )
        if len(excerpt) > 130:
            excerpt = excerpt[:127] + "..."
        return f"{location} matches {match.group()!r} near {excerpt!r}"
    return None


def transient_boundary_markers(prompt: str | None) -> list[str]:
    if not isinstance(prompt, str) or not prompt.strip():
        return []
    return [name for name, pattern in _TRANSIENT_BOUNDARY_PATTERNS if pattern.search(prompt)]


def _normalized_boundary_prompt(prompt: str | None) -> str:
    if not isinstance(prompt, str):
        return ""
    return re.sub(r"[^a-z0-9]+", " ", prompt.casefold()).strip()


_SHOTA_SPEC_HEADER = "\n\nSHOT SPECIFICATION (JSON)\n"
_IMAGE_PROSE_HEADER = "\n\nIMAGE PROMPT\n"


def _structured_boundary_parts(
    prompt: str | None,
) -> tuple[str | None, dict[str, Any] | None, str | None]:
    """Separate provider instructions/JSON from static image prose without rewriting it.

    An ordinary prose-only producer continues unchanged. A structured producer
    must supply valid JSON and a complete IMAGE PROMPT; malformed/partial
    envelopes are rejected rather than hiding their content from validation.
    """
    if not isinstance(prompt, str):
        return None, None, None
    if not prompt.startswith("REFERENCE AND GENERATION INSTRUCTIONS\n"):
        return None, None, None
    if _SHOTA_SPEC_HEADER not in prompt or _IMAGE_PROSE_HEADER not in prompt:
        return None, None, "missing JSON shot specification or IMAGE PROMPT section"
    raw_json_and_prose = prompt.split(_SHOTA_SPEC_HEADER, 1)[1]
    raw_json, prose = raw_json_and_prose.split(_IMAGE_PROSE_HEADER, 1)
    try:
        parsed = json.loads(raw_json)
    except (ValueError, TypeError) as exc:
        return None, None, f"invalid shot specification JSON: {exc}"
    if not isinstance(parsed, dict):
        return None, None, "shot specification must be a JSON object"
    if parsed.get("media") != "image":
        return None, None, "image frame shot specification must have media=image"
    if not prose.strip():
        return None, None, "IMAGE PROMPT section is empty"
    return prose, parsed, None


def _boundary_core(prompt: str | None) -> str:
    if not isinstance(prompt, str):
        return ""
    structured_prose, _spec, _error = _structured_boundary_parts(prompt)
    if structured_prose is not None:
        prompt = structured_prose
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
        structured_prose, spec, structured_error = _structured_boundary_parts(prompt)
        if structured_error:
            issues.append(FramePlanIssue(
                "BOUNDARY_FRAME_SPEC_INVALID",
                f"Shot {shot.shot_id!r} {field_name} has an invalid structured image prompt: "
                f"{structured_error}.",
                shot.shot_id,
            ))
            continue
        if spec is not None and spec.get("frame_role") != (
            "start_frame" if field_name == "start_frame_prompt" else "end_frame"
        ):
            issues.append(FramePlanIssue(
                "BOUNDARY_FRAME_SPEC_INVALID",
                f"Shot {shot.shot_id!r} {field_name} structured frame_role does not match.",
                shot.shot_id,
            ))
            continue
        core = _boundary_core(prompt)
        if not core:
            if require_compositions:
                issues.append(FramePlanIssue(
                    "BOUNDARY_FRAME_COMPOSITION_MISSING",
                    f"Shot {shot.shot_id!r} {field_name} has no shot-specific settled composition.",
                    shot.shot_id,
                ))
            continue
        # The JSON is a provider instruction, not audible dialogue. Validate
        # *renderable* staging alongside the complete image prose, but don't
        # treat explanatory reference policy, JSON punctuation or negative
        # identity metadata as scene motion.
        scan_text = core
        scan_sources = [("image prompt prose", core)]
        if spec is not None:
            staging = spec.get("shot_staging", {})
            if not isinstance(staging, dict):
                issues.append(FramePlanIssue(
                    "BOUNDARY_FRAME_SPEC_INVALID",
                    f"Shot {shot.shot_id!r} {field_name} shot_staging must be an object.",
                    shot.shot_id,
                ))
                continue
            authored_staging = staging.get("authored_frame_description", "")
            if not isinstance(authored_staging, str):
                issues.append(FramePlanIssue(
                    "BOUNDARY_FRAME_SPEC_INVALID",
                    f"Shot {shot.shot_id!r} {field_name} authored frame description must be text.",
                    shot.shot_id,
                ))
                continue
            scan_text += "\n" + authored_staging
            scan_sources.append(("JSON shot_staging", authored_staging))
            # The shot_pose is a settled physical configuration. Reject
            # unmistakably transient poses, but don't treat a walking *stance*
            # as action prose merely because that word appears in the pose.
            subjects = spec.get("subjects", [])
            if isinstance(subjects, list):
                for subject in subjects:
                    if isinstance(subject, dict):
                        pose = subject.get("shot_pose")
                        if isinstance(pose, str):
                            pose_parts = [pose]
                        elif isinstance(pose, dict):
                            # Writer-owned structured pose: arbitrary anatomy is
                            # never required. Scan only authored physical pose
                            # values, not JSON labels or reference metadata.
                            fields = ("posture", "support", "orientation", "placement", "relative_position")
                            pose_parts = [pose.get(key, "") for key in fields]
                            configuration = pose.get("configuration", [])
                            if isinstance(configuration, list):
                                pose_parts.extend(configuration)
                            else:
                                issues.append(FramePlanIssue(
                                    "BOUNDARY_FRAME_SPEC_INVALID",
                                    f"Shot {shot.shot_id!r} {field_name} configuration must be an array.",
                                    shot.shot_id,
                                ))
                                continue
                            if any(not isinstance(part, str) for part in pose_parts):
                                issues.append(FramePlanIssue(
                                    "BOUNDARY_FRAME_SPEC_INVALID",
                                    f"Shot {shot.shot_id!r} {field_name} pose fields must contain text.",
                                    shot.shot_id,
                                ))
                                continue
                        elif pose is None:
                            pose_parts = []
                        else:
                            issues.append(FramePlanIssue(
                                "BOUNDARY_FRAME_SPEC_INVALID",
                                f"Shot {shot.shot_id!r} {field_name} pose must be a string or object.",
                                shot.shot_id,
                            ))
                            continue
                        for part in pose_parts:
                            if transient_boundary_markers(part):
                                scan_text += "\n" + part
        markers = transient_boundary_markers(scan_text)
        if markers:
            issues.append(FramePlanIssue(
                "BOUNDARY_FRAME_TRANSIENT_ACTION",
                f"Shot {shot.shot_id!r} {field_name} describes transient motion ({', '.join(markers)}). "
                "Put intermediate movement in video_prompt instead.",
                shot.shot_id,
            ))
        if _NONVISUAL_BOUNDARY_PATTERN.search(scan_text) or re.search(r"[\"“”][^\"“”]+[\"“”]", scan_text):
            evidence = _nonvisual_boundary_evidence(scan_sources)
            detail = f" Trigger: {evidence}." if evidence else ""
            issues.append(FramePlanIssue(
                "BOUNDARY_FRAME_NONVISUAL_OR_TEMPORAL",
                f"Shot {shot.shot_id!r} {field_name} contains movement, dialogue, audio, or camera motion. "
                f"{detail} Frame prompts must describe one static visible composition.",
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


def validate_frame_plans(
    package: EpisodePackage,
    *,
    require_approved_end_frames: bool = False,
    shot_id: str | None = None,
) -> list[FramePlanIssue]:
    issues: list[FramePlanIssue] = []
    shots = ordered_shots(package)
    selected = [shot for shot in shots if shot_id is None or shot.shot_id == shot_id]
    positions = {item.shot_id: index for index, item in enumerate(shots)}
    locations = _site_by_shot(package)

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

        if shot.frame_plan.mode not in {"start_only", "start_and_end"}:
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

    # Incoming endpoint inheritance is independent of whether this shot also authors
    # an outgoing destination frame. Adjacency alone never implies inheritance.
    for shot in selected:
        transition_mode = _transition_mode(shot)
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
        if not shot.frame_plan.chain_from_shot_id:
            issues.append(FramePlanIssue(
                "INHERIT_ENDPOINT_REQUIRES_CHAIN",
                f"Shot {shot.shot_id!r} declares endpoint inheritance without naming a predecessor.",
                shot.shot_id,
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
        if predecessor.frame_plan.mode != "start_and_end":
            issues.append(FramePlanIssue(
                "ENDPOINT_HANDOFF_PREDECESSOR_HAS_NO_END_FRAME",
                f"Shot {shot.shot_id!r} inherits from predecessor {predecessor.shot_id!r}, "
                "so that predecessor must author an end frame.",
                shot.shot_id,
                predecessor.shot_id,
            ))
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
        inherits_endpoint = bool(shot.frame_plan.chain_from_shot_id)
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
                "ENDPOINT_HANDOFF_ASSET_MISMATCH",
                f"Shot {shot.shot_id!r} explicitly names start asset {shot.frame_plan.start_asset_id!r}, "
                f"but predecessor {predecessor.shot_id!r} ends at approved asset {endpoint!r}.",
                shot.shot_id,
                predecessor.shot_id,
                endpoint,
            ))
        if endpoint and shot.approved_start_frame_asset_id and shot.approved_start_frame_asset_id != endpoint:
            issues.append(FramePlanIssue(
                "ENDPOINT_HANDOFF_APPROVED_ASSET_MISMATCH",
                f"Shot {shot.shot_id!r} approves a start asset different from predecessor {predecessor.shot_id!r}'s endpoint.",
                shot.shot_id,
                predecessor.shot_id,
                endpoint,
            ))
        if require_approved_end_frames and not endpoint:
            issues.append(FramePlanIssue(
                "ENDPOINT_HANDOFF_ENDPOINT_MISSING",
                f"Shot {shot.shot_id!r} cannot generate video until predecessor {predecessor.shot_id!r} has an approved end frame.",
                shot.shot_id,
                predecessor.shot_id,
            ))
    return issues
