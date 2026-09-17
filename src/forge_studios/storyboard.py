from __future__ import annotations

import html
from pathlib import Path
from typing import Callable

from .contracts import EpisodePackage
from .frame_plan import predecessor_for
from .providers.base import MediaRequest


def generate_storyboard_candidates(package: EpisodePackage, animator, *, skip_existing: bool = True, on_shot_complete=None, progress: Callable[[str], None] | None = None):
    """Legacy planning-still generator retained only for old callers/tests."""
    generated = []
    shots = package.shots
    total = len(shots)
    for index, shot in enumerate(shots, 1):
        if skip_existing and (shot.storyboard_asset_ids or shot.approved_storyboard_asset_id):
            if progress:
                progress(f'[legacy-storyboard] {index}/{total} {shot.shot_id}: existing candidate; skipping')
            continue
        if progress:
            progress(f'[legacy-storyboard] {index}/{total} {shot.shot_id}: generating planning image')
        assets = animator.generate(package, shot.shot_id, role='storyboard')
        generated.extend(assets)
        if on_shot_complete:
            on_shot_complete(package, shot, assets)
    return generated


def _latest_boundary_id(shot, role: str) -> str | None:
    if role == 'start_frame':
        return shot.approved_start_frame_asset_id or (shot.start_frame_asset_ids[-1] if shot.start_frame_asset_ids else None)
    if role == 'end_frame':
        return shot.approved_end_frame_asset_id or (shot.end_frame_asset_ids[-1] if shot.end_frame_asset_ids else None)
    raise ValueError(role)


def _localize_existing(package: EpisodePackage, animator, shot, role: str) -> None:
    localize = getattr(getattr(animator, 'provider', None), 'localize', None)
    if not localize:
        return
    ids = shot.start_frame_asset_ids if role == 'start_frame' else shot.end_frame_asset_ids
    for asset_index, asset_id in enumerate(ids):
        asset = package.find_asset(asset_id)
        if asset.uri.startswith(('http://', 'https://')):
            asset.uri, remote_uri = localize(
                asset.uri,
                MediaRequest(kind='image', shot_id=shot.shot_id, role=role, prompt=''),
                asset_index,
            )
            if remote_uri:
                asset.metadata.setdefault('remote_uri', remote_uri)


def _candidate_pair_context(package: EpisodePackage, shot, start_id: str):
    """Temporarily make a candidate start usable while creating an optional end."""
    class _Context:
        def __enter__(self_inner):
            self_inner.original_start = shot.approved_start_frame_asset_id
            self_inner.original_plan_start = shot.frame_plan.start_asset_id
            self_inner.previous = None
            self_inner.original_previous_end = None
            shot.approved_start_frame_asset_id = start_id
            shot.frame_plan.start_asset_id = start_id
            if shot.frame_plan.chain_from_shot_id:
                self_inner.previous = predecessor_for(package, shot)
                self_inner.original_previous_end = self_inner.previous.approved_end_frame_asset_id
                self_inner.previous.approved_end_frame_asset_id = start_id
            return self_inner

        def __exit__(self_inner, exc_type, exc, tb):
            shot.approved_start_frame_asset_id = self_inner.original_start
            shot.frame_plan.start_asset_id = self_inner.original_plan_start
            if self_inner.previous is not None:
                self_inner.previous.approved_end_frame_asset_id = self_inner.original_previous_end
            return False

    return _Context()


def generate_boundary_candidates(
    package: EpisodePackage,
    animator,
    *,
    skip_existing: bool = True,
    on_frame_complete=None,
    progress: Callable[[str], None] | None = None,
):
    """Generate the actual frame candidates requested by each v3 shot.

    `start_only` generates only the visual start anchor. `start_and_end` additionally
    generates the authored destination frame. The current Forge Born workflow uses
    independent start-only beats; endpoint chaining remains a future/optional mode.
    """
    generated = []
    shots = package.shots
    total = len(shots)
    for index, shot in enumerate(shots, 1):
        if shot.render_strategy != 'generated_video':
            raise ValueError(
                f'Shot {shot.shot_id!r} is {shot.render_strategy!r}; this production path expects generated_video.'
            )
        if shot.frame_plan.mode not in {'start_only', 'start_and_end'}:
            raise ValueError(
                f'Shot {shot.shot_id!r} uses unsupported frame plan {shot.frame_plan.mode!r} for frame candidate generation.'
            )

        if progress:
            progress(f'[frames] {index}/{total} {shot.shot_id}: preparing {shot.frame_plan.mode}')

        if shot.frame_plan.chain_from_shot_id:
            previous = predecessor_for(package, shot)
            start_id = _latest_boundary_id(previous, 'end_frame')
            if not start_id:
                raise ValueError(
                    f'Shot {shot.shot_id!r} inherits {previous.shot_id!r} end frame, but that predecessor has no end-frame candidate yet.'
                )
            if start_id not in shot.start_frame_asset_ids:
                shot.start_frame_asset_ids.append(start_id)
            if progress:
                progress(f'[frames] {shot.shot_id}: reusing {previous.shot_id} end candidate as exact start')
        else:
            start_id = _latest_boundary_id(shot, 'start_frame')
            if not (skip_existing and start_id):
                assets = animator.generate(package, shot.shot_id, role='start_frame')
                generated.extend(assets)
                start_id = assets[-1].asset_id
                if on_frame_complete:
                    on_frame_complete(package, shot, 'start_frame', assets)
                if progress:
                    progress(f'[frames] {shot.shot_id}: generated start candidate {start_id}')
            else:
                _localize_existing(package, animator, shot, 'start_frame')

        if not start_id:
            raise ValueError(f'Shot {shot.shot_id!r} has no start-frame candidate.')

        if shot.frame_plan.mode == 'start_and_end':
            end_id = _latest_boundary_id(shot, 'end_frame')
            if not (skip_existing and end_id):
                with _candidate_pair_context(package, shot, start_id):
                    assets = animator.generate(package, shot.shot_id, role='end_frame')
                generated.extend(assets)
                end_id = assets[-1].asset_id
                if on_frame_complete:
                    on_frame_complete(package, shot, 'end_frame', assets)
                if progress:
                    progress(f'[frames] {shot.shot_id}: generated optional end candidate {end_id}')
            else:
                _localize_existing(package, animator, shot, 'end_frame')

        if progress:
            label = 'start ready' if shot.frame_plan.mode == 'start_only' else 'start/end pair ready'
            progress(f'[frames] {index}/{total} {shot.shot_id}: {label} for human review')
    return generated


def _asset_id_for_frame(shot, role: str) -> str | None:
    if role == 'start_frame':
        return shot.approved_start_frame_asset_id or (shot.start_frame_asset_ids[-1] if shot.start_frame_asset_ids else None)
    if role == 'end_frame':
        return shot.approved_end_frame_asset_id or (shot.end_frame_asset_ids[-1] if shot.end_frame_asset_ids else None)
    raise ValueError(role)


def _asset_image(package: EpisodePackage, asset_id: str | None, *, alt: str, css_class: str = '') -> str:
    if not asset_id:
        return '<div class="missing">Not generated yet</div>'
    try:
        asset = package.find_asset(asset_id)
    except KeyError:
        return f'<div class="missing">Missing asset {html.escape(asset_id)}</div>'
    classes = f' class="{html.escape(css_class)}"' if css_class else ''
    return f'<img{classes} src="{html.escape(asset.uri)}" alt="{html.escape(alt)}">'


def _frame_panel(package: EpisodePackage, shot, role: str, prompt: str | None) -> str:
    asset_id = _asset_id_for_frame(shot, role)
    label = 'START FRAME' if role == 'start_frame' else 'END FRAME'
    inherited = ''
    if role == 'start_frame' and shot.frame_plan.chain_from_shot_id:
        inherited = f'<div class="handoff">↳ inherited from {html.escape(shot.frame_plan.chain_from_shot_id)} end frame</div>'
    asset_label = f'<code>{html.escape(asset_id)}</code>' if asset_id else '<span class="muted">pending</span>'
    return f'''<section class="frame-panel">
<div class="frame-heading"><b>{label}</b>{inherited}</div>
{_asset_image(package, asset_id, alt=f'{shot.shot_id} {label}')}
<div class="asset-id">{asset_label}</div>
<pre>{html.escape(prompt or '(no frame prompt)')}</pre>
</section>'''


def _storyboard_styles() -> str:
    return '''
body{font:16px system-ui;max-width:1280px;margin:2rem auto;padding:0 1rem;background:#f7f7f5;color:#161616}
a{color:inherit}article{background:white;border:1px solid #bbb;border-radius:14px;padding:1rem;margin:1.2rem 0;box-shadow:0 2px 8px #0000000a}
.frame-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:1rem}.frame-panel{min-width:0}.frame-heading{display:flex;gap:.7rem;align-items:center;margin:.2rem 0 .5rem}.handoff{font-size:.82rem;color:#5d5d5d}
img{display:block;width:100%;max-height:500px;object-fit:contain;background:#eee;border-radius:8px}.missing{height:260px;display:grid;place-items:center;border:2px dashed #bbb;border-radius:8px;color:#777;background:#fafafa}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f4f4;border-radius:8px;padding:.75rem;font:13px ui-monospace,SFMono-Regular,Menlo,monospace}.video-prompt{background:#111;color:#f5f5f5}
.asset-id{font-size:.78rem;color:#666;margin-top:.4rem}.muted{color:#888}.explain{padding:.9rem 1rem;border-left:4px solid #777;background:white}.planning-note{font-size:.9rem;color:#555}
'''


def build_storyboard(package: EpisodePackage, path: str | Path) -> Path:
    """Render the human production storyboard from the actual requested frame anchors."""
    out = Path(path)
    guide_path = out.with_name(f'{out.stem}.image-guide{out.suffix or ".html"}')
    cards = []
    for shot in package.shots:
        panels = [_frame_panel(package, shot, 'start_frame', shot.start_frame_prompt)]
        if shot.frame_plan.mode == 'start_and_end':
            panels.append(_frame_panel(package, shot, 'end_frame', shot.end_frame_prompt))
        visual = '<div class="frame-grid">' + ''.join(panels) + '</div>'
        motion = f'<h4>VIDEO MOTION / PERFORMANCE PROMPT</h4><pre class="video-prompt">{html.escape(shot.video_prompt or "(no video prompt)")}</pre>'
        cards.append(f'''<article>
<h3>{html.escape(shot.shot_id)} · {shot.duration_seconds:g}s · {html.escape(shot.frame_plan.mode)}</h3>
{visual}
{motion}
<p class="planning-note"><b>Beat:</b> {html.escape(shot.beat_id)} · <b>Site:</b> {html.escape(shot.site_id)} · <b>Status:</b> {html.escape(shot.status)}</p>
</article>''')
    doc = f'''<!doctype html><meta charset="utf-8"><title>{html.escape(package.title)} production storyboard</title>
<style>{_storyboard_styles()}</style>
<h1>{html.escape(package.title)} — Production Storyboard</h1>
<p>{html.escape(package.premise)}</p>
<div class="explain"><b>This is the final human pre-video review artifact.</b> Start-only units show the actual START frame anchor plus the complete video prompt. An END frame appears only for a unit explicitly authored as start_and_end. <a href="{html.escape(guide_path.name)}">Open the image-generation guide →</a></div>
{''.join(cards)}'''
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc)
    build_image_guide(package, guide_path, storyboard_name=out.name)
    return out


def _generation_prompt(asset, fallback: str | None = None) -> str:
    generation = asset.metadata.get('generation') if isinstance(asset.metadata, dict) else None
    if isinstance(generation, dict) and isinstance(generation.get('prompt'), str):
        return generation['prompt']
    return fallback or '(generation prompt unavailable)'


def _generation_refs(asset) -> list[str]:
    generation = asset.metadata.get('generation') if isinstance(asset.metadata, dict) else None
    refs = generation.get('reference_asset_ids') if isinstance(generation, dict) else None
    if isinstance(refs, list):
        return [str(value) for value in refs]
    return list(asset.source_asset_ids)


def _guide_role_assets(shot) -> list[tuple[str, str, str | None]]:
    result: list[tuple[str, str, str | None]] = []
    for role, ids, fallback in (
        ('start frame', shot.start_frame_asset_ids, shot.start_frame_prompt),
        ('end frame', shot.end_frame_asset_ids, shot.end_frame_prompt),
    ):
        for asset_id in ids:
            result.append((role, asset_id, fallback))
    return result


def _guide_card(package: EpisodePackage, *, title: str, asset_id: str, fallback: str | None = None) -> str:
    try:
        asset = package.find_asset(asset_id)
    except KeyError:
        return ''
    refs = _generation_refs(asset)
    inputs = []
    for index, ref_id in enumerate(refs, 1):
        try:
            ref = package.find_asset(ref_id)
            thumb = f'<img src="{html.escape(ref.uri)}" alt="input image {index}">'
        except KeyError:
            thumb = '<div class="missing">missing input</div>'
        inputs.append(f'<div class="input"><b>INPUT {index}</b><code>{html.escape(ref_id)}</code>{thumb}</div>')
    reusable = ' · reusable reference' if asset.kind == 'reference_image' or asset.metadata.get('reusable_reference') else ''
    return f'''<article>
<h2>{html.escape(title)}</h2>
<div class="equation"><b>{' + '.join(f'INPUT {index}' for index in range(1, len(inputs)+1)) if inputs else 'TEXT-ONLY INPUT'} + PROMPT → OUTPUT</b></div>
<h3>Input images, in provider order</h3>
<div class="inputs">{''.join(inputs) if inputs else '<span class="muted">No input images</span>'}</div>
<h3>Exact prompt</h3>
<pre>{html.escape(_generation_prompt(asset, fallback))}</pre>
<div class="output"><b>OUTPUT</b>{_asset_image(package, asset_id, alt=title)}</div>
<p><b>Asset:</b> <code>{html.escape(asset_id)}</code> · <b>Status:</b> {html.escape(asset.status)}{html.escape(reusable)} · <b>Provider/model:</b> {html.escape(asset.provider or 'unknown')} / {html.escape(asset.model or 'unknown')}</p>
</article>'''


def build_image_guide(package: EpisodePackage, path: str | Path, *, storyboard_name: str | None = None) -> Path:
    """Show how each generated frame/reference image was made: inputs + prompt -> output."""
    cards = []
    seen: set[str] = set()
    for shot in package.shots:
        for role, asset_id, fallback in _guide_role_assets(shot):
            if asset_id in seen:
                continue
            seen.add(asset_id)
            card = _guide_card(package, title=f'{shot.shot_id} — {role}', asset_id=asset_id, fallback=fallback)
            if card:
                cards.append(card)
    for asset in package.assets:
        if asset.asset_id in seen:
            continue
        if asset.kind == 'reference_image' and asset.authority == 'generated':
            card = _guide_card(package, title=f'reusable reference — {asset.asset_id}', asset_id=asset.asset_id)
            if card:
                cards.append(card)
                seen.add(asset.asset_id)
    back = f'<p><a href="{html.escape(storyboard_name)}">← Production storyboard</a></p>' if storyboard_name else ''
    doc = f'''<!doctype html><meta charset="utf-8"><title>{html.escape(package.title)} image guide</title>
<style>{_storyboard_styles()}.inputs{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:.7rem}}.input{{background:#fff;border:1px solid #ccc;border-radius:8px;padding:.5rem}}.input code{{display:block;font-size:.72rem;color:#666;margin:.2rem 0}}.input img{{height:180px;object-fit:contain}}.output{{max-width:850px;margin-top:1rem}}.equation{{padding:.6rem;background:#eef;border-radius:6px;margin:.4rem 0}}</style>
<h1>{html.escape(package.title)} — Image Generation Guide</h1>
<div class="explain">For every generated start frame, optional end frame, or reusable reference image, this page shows the ordered image inputs, exact provider prompt, and resulting output.</div>
{back}{''.join(cards) if cards else '<p>No generated frame/reference images are recorded yet.</p>'}'''
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc)
    return out
