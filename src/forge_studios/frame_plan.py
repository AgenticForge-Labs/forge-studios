from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
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
    return [shot for scene in package.scenes for shot in scene.shots]


def predecessor_for(package: EpisodePackage, shot: Shot) -> Shot:
    chain_from = shot.frame_plan.chain_from_shot_id
    if not chain_from:
        raise FramePlanError(FramePlanIssue(
            'CHAINED_START_MISSING_PREDECESSOR',
            f"Shot {shot.shot_id!r} uses chained_start but has no chain_from_shot_id.",
            shot.shot_id,
        ))
    shots = ordered_shots(package)
    positions = {item.shot_id: index for index, item in enumerate(shots)}
    if chain_from not in positions:
        raise FramePlanError(FramePlanIssue(
            'CHAINED_START_UNKNOWN_PREDECESSOR',
            f"Shot {shot.shot_id!r} chains from unknown predecessor {chain_from!r}.",
            shot.shot_id,
            chain_from,
        ))
    if positions[chain_from] >= positions[shot.shot_id]:
        raise FramePlanError(FramePlanIssue(
            'CHAINED_START_PREDECESSOR_NOT_EARLIER',
            f"Shot {shot.shot_id!r} must chain from an earlier shot, not {chain_from!r}.",
            shot.shot_id,
            chain_from,
        ))
    return shots[positions[chain_from]]


def _editorial_boundary(shot: Shot) -> str:
    intent = shot.edit_intent if isinstance(shot.edit_intent, dict) else {}
    value = intent.get('editorial_boundary')
    return str(value).strip() if value is not None else ''


def _transition_mode(shot: Shot) -> str:
    intent = shot.edit_intent if isinstance(shot.edit_intent, dict) else {}
    value = intent.get('transition_mode')
    return str(value).strip() if value is not None else ''


def _transition_reason(shot: Shot) -> str:
    intent = shot.edit_intent if isinstance(shot.edit_intent, dict) else {}
    value = intent.get('transition_reason')
    return str(value).strip() if value is not None else ''


def _viewpoint(shot: Shot) -> str:
    intent = shot.edit_intent if isinstance(shot.edit_intent, dict) else {}
    explicit = str(intent.get('viewpoint') or '').strip()
    if explicit:
        return explicit
    camera = shot.camera if isinstance(shot.camera, dict) else {}
    text = ' '.join(str(camera.get(key) or '') for key in ('shot_type', 'framing', 'distance', 'composition', 'axis')).casefold()
    return 'first_person' if any(marker in text for marker in ('first-person', 'first person', 'point of view', 'pov')) else 'objective'


def _scene_location_by_shot(package: EpisodePackage) -> dict[str, str | None]:
    return {
        shot.shot_id: scene.location_id
        for scene in package.scenes
        for shot in scene.shots
    }


_TRANSIENT_BOUNDARY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ('mid-motion', re.compile(
        r'\bmid[- ]?(?:jump|leap|fall|descent|landing|turn|stride|step|run|walk|flight)\b',
        re.IGNORECASE,
    )),
    ('while-moving', re.compile(
        r'\bwhile\s+(?:jumping|leaping|falling|landing|descending|turning|walking|running|flying)\b',
        re.IGNORECASE,
    )),
    ('during-motion', re.compile(
        r'\bduring\s+(?:the\s+)?(?:jump|leap|fall|descent|landing|turn|walk|run|flight)\b',
        re.IGNORECASE,
    )),
    ('in-the-act-of-motion', re.compile(
        r'\bin\s+the\s+act\s+of\s+(?:jumping|leaping|falling|landing|descending|turning|walking|running|flying)\b',
        re.IGNORECASE,
    )),
    ('halfway-through-motion', re.compile(
        r'\bhalfway\s+(?:through|down|up|across)\b',
        re.IGNORECASE,
    )),
    ('motion-blur', re.compile(r'\bmotion[- ]blur(?:red)?\b', re.IGNORECASE)),
    ('active-jump-or-fall', re.compile(
        r'\b(?:jumps|leaps|falls)\s+(?:from|off|down|toward|towards|to|across|over)\b',
        re.IGNORECASE,
    )),
)


def transient_boundary_markers(prompt: str | None) -> list[str]:
    if not isinstance(prompt, str) or not prompt.strip():
        return []
    return [name for name, pattern in _TRANSIENT_BOUNDARY_PATTERNS if pattern.search(prompt)]


def _normalized_boundary_prompt(prompt: str | None) -> str:
    if not isinstance(prompt, str):
        return ''
    return re.sub(r'[^a-z0-9]+', ' ', prompt.casefold()).strip()


def _boundary_core(prompt: str | None) -> str:
    if not isinstance(prompt, str):
        return ''
    for marker in ('\n\nCanonical visual anchors', '\n\nNON-NEGOTIABLE VISUAL RULES', '\n\nProduction constraints'):
        prompt = prompt.split(marker, 1)[0]
    return prompt.strip()


def _boundary_prompt_issues(shot: Shot, *, require_compositions: bool = False) -> list[FramePlanIssue]:
    if shot.render_strategy != 'generated_video' or shot.frame_plan.mode != 'start_and_end':
        return []
    issues: list[FramePlanIssue] = []
    for field_name in ('start_frame_prompt', 'end_frame_prompt'):
        prompt = getattr(shot, field_name, None)
        core = _boundary_core(prompt)
        if not core:
            if require_compositions:
                issues.append(FramePlanIssue(
                    'BOUNDARY_FRAME_COMPOSITION_MISSING',
                    f"Shot {shot.shot_id!r} {field_name} has no shot-specific settled composition. Shared guardrail/reference text is not a boundary design.",
                    shot.shot_id,
                ))
            continue
        markers = transient_boundary_markers(prompt)
        if markers:
            issues.append(FramePlanIssue(
                'BOUNDARY_FRAME_TRANSIENT_ACTION',
                f"Shot {shot.shot_id!r} {field_name} describes transient motion ({', '.join(markers)}). "
                'Boundary images must be settled endpoint states; put the jump, fall, turn, walk, or other '
                'intermediate motion in video_prompt instead.',
                shot.shot_id,
            ))
    start = _normalized_boundary_prompt(_boundary_core(getattr(shot, 'start_frame_prompt', None)))
    end = _normalized_boundary_prompt(_boundary_core(getattr(shot, 'end_frame_prompt', None)))
    if start and end and start == end:
        issues.append(FramePlanIssue(
            'BOUNDARY_FRAME_PROMPTS_DUPLICATE',
            f"Shot {shot.shot_id!r} uses the same settled composition for its start and end frames. "
            'Describe the distinct state before the action and the distinct state after it; keep the transition in video_prompt.',
            shot.shot_id,
        ))
    return issues


def _semantic_shot_payload(shot: Shot) -> dict[str, Any]:
    return {
        'shot_id': shot.shot_id,
        'source_beat_ids': list(shot.source_beat_ids),
        'duration_seconds': shot.duration_seconds,
        'purpose': shot.purpose,
        'visual': shot.visual,
        'entity_ids': list(shot.entity_ids),
        'dialogue_ids': list(shot.dialogue_ids),
        'camera': dict(shot.camera),
        'visual_constraints': dict(shot.visual_constraints),
        'performance_intent': dict(shot.performance_intent),
        'edit_intent': dict(shot.edit_intent),
        'continuity_asset_ids': list(shot.continuity_asset_ids),
        'execution_route': shot.execution_route,
        'render_strategy': shot.render_strategy,
        'frame_plan': {
            'mode': shot.frame_plan.mode,
            'chain_from_shot_id': shot.frame_plan.chain_from_shot_id,
        },
        'image_prompt': shot.image_prompt,
        'start_frame_prompt': shot.start_frame_prompt,
        'end_frame_prompt': shot.end_frame_prompt,
        'video_prompt': shot.video_prompt,
        'provider_options': dict(shot.provider_options),
    }


def package_semantic_fingerprint(package: EpisodePackage) -> str:
    payload = {
        'package_version': package.package_version,
        'production_id': package.production_id,
        'episode_id': package.episode_id,
        'revision': package.revision,
        'world_id': package.world_id,
        'target_duration_seconds': package.target_duration_seconds,
        'beats': package.beats,
        'dialogue': package.dialogue,
        'scenes': [
            {
                'scene_id': scene.scene_id,
                'source_beat_ids': list(scene.source_beat_ids),
                'location_id': scene.location_id,
                'summary': scene.summary,
                'dramatic_goal': getattr(scene, 'dramatic_goal', ''),
                'character_ids': list(getattr(scene, 'character_ids', []) or []),
                'shots': [_semantic_shot_payload(shot) for shot in scene.shots],
            }
            for scene in package.scenes
        ],
    }
    raw = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _stale_package_issue(package: EpisodePackage) -> FramePlanIssue | None:
    trace = package.trace if isinstance(package.trace, dict) else {}
    expected = trace.get('semantic_fingerprint')
    version = trace.get('semantic_fingerprint_version')
    if version != 'v1' or not isinstance(expected, str) or not expected:
        return None
    actual = package_semantic_fingerprint(package)
    if actual == expected:
        return None
    return FramePlanIssue(
        'STALE_DERIVED_PACKAGE_STATE',
        'EpisodePackage semantic production intent changed after Forge Worlds derived its preflight/status metadata. '
        'Re-run deterministic Worlds finalization before any provider spend; do not trust stale preflight trace.',
        '__package__',
    )


def validate_frame_plans(package: EpisodePackage, *, require_approved_end_frames: bool = False, shot_id: str | None = None) -> list[FramePlanIssue]:
    issues: list[FramePlanIssue] = []
    stale = _stale_package_issue(package)
    if stale is not None:
        issues.append(stale)

    shots = ordered_shots(package)
    selected = [shot for shot in shots if shot_id is None or shot.shot_id == shot_id]
    positions = {item.shot_id: index for index, item in enumerate(shots)}
    locations = _scene_location_by_shot(package)
    require_explicit_continuity = (package.trace or {}).get('semantic_fingerprint_version') == 'v1'

    for shot in selected:
        for role in ('start_frame', 'end_frame'):
            camera = shot.camera.get(role)
            if camera is not None and not isinstance(camera, dict):
                issues.append(FramePlanIssue('INVALID_BOUNDARY_CAMERA', f'camera.{role} must be a static camera object.', shot.shot_id))
            elif isinstance(camera, dict) and camera.get('movement') not in (None, '', 'none', 'static', 'locked'):
                issues.append(FramePlanIssue('TEMPORAL_BOUNDARY_CAMERA', f'camera.{role} must not contain temporal movement.', shot.shot_id))
        issues.extend(_boundary_prompt_issues(shot, require_compositions=require_explicit_continuity))
        if shot.render_strategy == 'generated_video' and shot.duration_seconds > 20 + 1e-6:
            issues.append(FramePlanIssue(
                'GENERATED_VIDEO_DURATION_EXCEEDED',
                f"Shot {shot.shot_id!r} is {shot.duration_seconds:g}s; generated video is capped at 20s.",
                shot.shot_id,
            ))
        if shot.render_strategy == 'generated_video' and shot.frame_plan.mode != 'start_and_end' and require_explicit_continuity:
            issues.append(FramePlanIssue(
                'ANIMATOR_VIDEO_REQUIRES_START_AND_END',
                f"Shot {shot.shot_id!r} must use start_and_end under the current animator production contract.",
                shot.shot_id,
            ))

    for shot in selected:
        if shot.render_strategy != 'generated_video' or shot.frame_plan.mode != 'start_and_end':
            continue
        transition_mode = _transition_mode(shot)
        if require_explicit_continuity and transition_mode not in {'new_composition', 'inherit_endpoint'}:
            issues.append(FramePlanIssue(
                'CONTINUITY_MODE_REQUIRED',
                f"Shot {shot.shot_id!r} lacks explicit transition_mode from Forge Worlds.",
                shot.shot_id,
            ))
            continue
        if transition_mode == 'new_composition':
            if shot.frame_plan.chain_from_shot_id:
                issues.append(FramePlanIssue(
                    'NEW_COMPOSITION_MUST_NOT_CHAIN',
                    f"Shot {shot.shot_id!r} declares a new composition but still chains from {shot.frame_plan.chain_from_shot_id!r}.",
                    shot.shot_id,
                    shot.frame_plan.chain_from_shot_id,
                ))
            continue
        if transition_mode != 'inherit_endpoint':
            continue

        index = positions[shot.shot_id]
        if index == 0:
            issues.append(FramePlanIssue(
                'FIRST_SHOT_CANNOT_INHERIT_ENDPOINT',
                f"Shot {shot.shot_id!r} is first and cannot inherit an endpoint.",
                shot.shot_id,
            ))
            continue
        predecessor = shots[index - 1]
        if shot.frame_plan.chain_from_shot_id != predecessor.shot_id:
            issues.append(FramePlanIssue(
                'EXPLICIT_ENDPOINT_HANDOFF_REQUIRED',
                f"Shot {shot.shot_id!r} explicitly inherits and must chain from immediate predecessor {predecessor.shot_id!r}.",
                shot.shot_id,
                predecessor.shot_id,
            ))
        if _editorial_boundary(shot):
            issues.append(FramePlanIssue(
                'INHERITED_ENDPOINT_HAS_EDITORIAL_BOUNDARY',
                f"Shot {shot.shot_id!r} cannot both inherit the exact endpoint and declare an editorial boundary.",
                shot.shot_id,
                predecessor.shot_id,
            ))
        if locations.get(predecessor.shot_id) != locations.get(shot.shot_id):
            issues.append(FramePlanIssue(
                'LOCATION_CHANGE_CANNOT_INHERIT_ENDPOINT',
                f"Shot {shot.shot_id!r} changes location and must start from a new composition.",
                shot.shot_id,
                predecessor.shot_id,
            ))
        if _viewpoint(predecessor) != _viewpoint(shot):
            issues.append(FramePlanIssue(
                'POV_CHANGE_CANNOT_INHERIT_ENDPOINT',
                f"Shot {shot.shot_id!r} changes viewpoint and must start from a new composition.",
                shot.shot_id,
                predecessor.shot_id,
            ))
        reason = _transition_reason(shot)
        if reason and reason not in {'continuous_action', 'technical_duration_split'}:
            issues.append(FramePlanIssue(
                'INVALID_ENDPOINT_HANDOFF_REASON',
                f"Shot {shot.shot_id!r} inherits for non-continuous reason {reason!r}.",
                shot.shot_id,
                predecessor.shot_id,
            ))

    for shot in selected:
        inherits_endpoint = (
            shot.frame_plan.mode == 'chained_start'
            or (shot.frame_plan.mode == 'start_and_end' and bool(shot.frame_plan.chain_from_shot_id))
        )
        if not inherits_endpoint:
            continue
        try:
            predecessor = predecessor_for(package, shot)
        except FramePlanError as exc:
            issues.append(exc.issue)
            continue
        endpoint = predecessor.approved_end_frame_asset_id
        if shot.frame_plan.mode == 'start_and_end':
            if positions[predecessor.shot_id] != positions[shot.shot_id] - 1:
                issues.append(FramePlanIssue(
                    'START_AND_END_NONADJACENT_PREDECESSOR',
                    f"Shot {shot.shot_id!r} must inherit the immediately preceding end frame.",
                    shot.shot_id, predecessor.shot_id,
                ))
        if endpoint and shot.frame_plan.start_asset_id and shot.frame_plan.start_asset_id != endpoint:
            issues.append(FramePlanIssue(
                'CHAINED_START_ASSET_MISMATCH',
                f"Shot {shot.shot_id!r} explicitly names start asset {shot.frame_plan.start_asset_id!r}, "
                f"but predecessor {predecessor.shot_id!r} ends at approved asset {endpoint!r}.",
                shot.shot_id,
                predecessor.shot_id,
                endpoint,
            ))
        if endpoint and shot.approved_start_frame_asset_id and shot.approved_start_frame_asset_id != endpoint:
            issues.append(FramePlanIssue(
                'CHAINED_START_APPROVED_ASSET_MISMATCH',
                f"Shot {shot.shot_id!r} approves a start asset different from predecessor {predecessor.shot_id!r}'s endpoint.",
                shot.shot_id, predecessor.shot_id, endpoint,
            ))
        if require_approved_end_frames and not endpoint:
            issues.append(FramePlanIssue(
                'CHAINED_START_ENDPOINT_MISSING',
                f"Shot {shot.shot_id!r} cannot generate video until predecessor {predecessor.shot_id!r} "
                'has an approved end-frame asset.',
                shot.shot_id,
                predecessor.shot_id,
            ))
    return issues
