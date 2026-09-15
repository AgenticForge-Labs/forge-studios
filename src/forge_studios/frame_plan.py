from __future__ import annotations

from dataclasses import asdict, dataclass

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
    """Return the explicit reason this shot starts on a new visual setup, if any."""
    intent = shot.edit_intent if isinstance(shot.edit_intent, dict) else {}
    value = intent.get('editorial_boundary')
    return str(value).strip() if value is not None else ''


def validate_frame_plans(package: EpisodePackage, *, require_approved_end_frames: bool = False, shot_id: str | None = None) -> list[FramePlanIssue]:
    issues: list[FramePlanIssue] = []
    shots = ordered_shots(package)
    selected = [shot for shot in shots if shot_id is None or shot.shot_id == shot_id]
    positions = {item.shot_id: index for index, item in enumerate(shots)}

    # Forge Worlds' current generated-video contract uses start_and_end. When two
    # such units are adjacent and the later unit does not declare a real editorial
    # boundary, it must reuse the predecessor's exact endpoint. Keep older
    # start_only/chained_start packages valid for compatibility; this rule tightens
    # the current production path without rewriting legacy contracts.
    for shot in selected:
        if shot.render_strategy != 'generated_video' or shot.frame_plan.mode != 'start_and_end':
            continue
        index = positions[shot.shot_id]
        if index == 0:
            continue
        predecessor = shots[index - 1]
        if predecessor.render_strategy != 'generated_video':
            continue
        if _editorial_boundary(shot):
            continue
        if shot.frame_plan.chain_from_shot_id != predecessor.shot_id:
            issues.append(FramePlanIssue(
                'CONTINUOUS_VIDEO_ENDPOINT_HANDOFF_REQUIRED',
                f"Shot {shot.shot_id!r} follows generated-video shot {predecessor.shot_id!r} without an "
                "editorial_boundary, so its start frame must inherit that predecessor's approved end frame. "
                "Either set frame_plan.chain_from_shot_id to the immediate predecessor or declare the actual cut/setup change.",
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
