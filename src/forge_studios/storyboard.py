from __future__ import annotations

import html
from pathlib import Path
from typing import Callable

from .contracts import EpisodePackage
from .providers.base import MediaRequest


def generate_storyboard_candidates(package: EpisodePackage, animator, *, skip_existing: bool = True, on_shot_complete=None, progress: Callable[[str], None] | None = None):
    """Generate planning-image candidates for the package's shots in narrative order.

    These candidates are composition aids for frame generation.  The production
    storyboard itself is built from the actual start/end boundary frames used by
    video generation, not from these representative planning stills.
    """
    generated = []
    shots = [shot for scene in package.scenes for shot in scene.shots]
    total = len(shots)
    for index, shot in enumerate(shots, 1):
        if skip_existing and (shot.storyboard_asset_ids or shot.approved_storyboard_asset_id):
            if progress:
                progress(f'[storyboard] {index}/{total} {shot.shot_id}: existing candidate; skipping')
            localize = getattr(getattr(animator, 'provider', None), 'localize', None)
            if localize:
                for asset_index, asset_id in enumerate(shot.storyboard_asset_ids):
                    asset = package.find_asset(asset_id)
                    if asset.uri.startswith(('http://', 'https://')):
                        asset.uri, remote_uri = localize(
                            asset.uri,
                            MediaRequest(kind='image', shot_id=shot.shot_id, role='storyboard', prompt=''),
                            asset_index,
                        )
                        if remote_uri:
                            asset.metadata.setdefault('remote_uri', remote_uri)
            continue
        if progress:
            progress(f'[storyboard] {index}/{total} {shot.shot_id}: generating planning image')
        assets = animator.generate(package, shot.shot_id, role='storyboard')
        generated.extend(assets)
        if on_shot_complete:
            on_shot_complete(package, shot, assets)
        if progress:
            progress(f'[storyboard] {index}/{total} {shot.shot_id}: saved {len(assets)} planning candidate(s)')
    return generated


def _asset_id_for_frame(shot, role: str) -> str | None:
    if role == 'start_frame':
        return shot.approved_start_frame_asset_id or (shot.start_frame_asset_ids[-1] if shot.start_frame_asset_ids else None)
    if role == 'end_frame':
        return shot.approved_end_frame_asset_id or (shot.end_frame_asset_ids[-1] if shot.end_frame_asset_ids else None)
    if role == 'storyboard':
        return shot.approved_storyboard_asset_id or (shot.storyboard_asset_ids[-1] if shot.storyboard_asset_ids else None)
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
.frame-grid{display:grid;grid-template-columns:1fr 1fr;gap:1rem}.frame-panel{min-width:0}.frame-heading{display:flex;gap:.7rem;align-items:center;margin:.2rem 0 .5rem}.handoff{font-size:.82rem;color:#5d5d5d}
img{display:block;width:100%;max-height:500px;object-fit:contain;background:#eee;border-radius:8px}.missing{height:260px;display:grid;place-items:center;border:2px dashed #bbb;border-radius:8px;color:#777;background:#fafafa}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f4f4;border-radius:8px;padding:.75rem;font:13px ui-monospace,SFMono-Regular,Menlo,monospace}.video-prompt{background:#111;color:#f5f5f5}
.asset-id{font-size:.78rem;color:#666;margin-top:.4rem}.muted{color:#888}.explain{padding:.9rem 1rem;border-left:4px solid #777;background:white}.planning-note{font-size:.9rem;color:#555}
@media(max-width:760px){.frame-grid{grid-template-columns:1fr}}
'''


def build_storyboard(package: EpisodePackage, path: str | Path) -> Path:
    """Render the production storyboard as the exact video boundary-frame sequence.

    For a generated-video unit the two large panels are the actual start and end
    images supplied to the video model.  Motion belongs in video_prompt.  When the
    next unit is continuous, its start panel intentionally reuses the predecessor's
    approved end-frame asset, making the handoff visually explicit.
    """
    out = Path(path)
    guide_path = out.with_name(f'{out.stem}.image-guide{out.suffix or ".html"}')
    cards = []
    for scene in package.scenes:
        cards.append(f'<h2>{html.escape(scene.scene_id)} — {html.escape(scene.summary)}</h2>')
        for shot in scene.shots:
            if shot.render_strategy == 'generated_video':
                visual = (
                    '<div class="frame-grid">'
                    + _frame_panel(package, shot, 'start_frame', shot.start_frame_prompt)
                    + _frame_panel(package, shot, 'end_frame', shot.end_frame_prompt)
                    + '</div>'
                )
                motion = f'<h4>VIDEO MOTION PROMPT</h4><pre class="video-prompt">{html.escape(shot.video_prompt or "(no video prompt)")}</pre>'
            else:
                still_id = _asset_id_for_frame(shot, 'start_frame') or _asset_id_for_frame(shot, 'storyboard')
                visual = _asset_image(package, still_id, alt=shot.shot_id)
                motion = f'<pre>{html.escape(shot.video_prompt or shot.image_prompt or shot.visual)}</pre>'
            cards.append(f'''<article>
<h3>{html.escape(shot.shot_id)} · {shot.duration_seconds:g}s</h3>
{visual}
{motion}
<p><b>Purpose:</b> {html.escape(shot.purpose)}</p>
<p><b>Shot action:</b> {html.escape(shot.visual)}</p>
<p class="planning-note"><b>Route:</b> {html.escape(shot.execution_route)} / {html.escape(shot.render_strategy)} · <b>Frames:</b> {html.escape(shot.frame_plan.mode)} · <b>Status:</b> {html.escape(shot.status)}</p>
<p class="planning-note"><b>Beats:</b> {html.escape(', '.join(shot.source_beat_ids))}</p>
</article>''')
    doc = f'''<!doctype html><meta charset="utf-8"><title>{html.escape(package.title)} production storyboard</title>
<style>{_storyboard_styles()}</style>
<h1>{html.escape(package.title)} — Production Storyboard</h1>
<p>{html.escape(package.premise)}</p>
<div class="explain"><b>This is the video-generation sequence.</b> Each generated-video unit shows the exact boundary images supplied to the model. The action itself is described by the video motion prompt and occurs between these images. A continuous successor reuses the previous end image as its start image. <a href="{html.escape(guide_path.name)}">Open the image-generation guide →</a></div>
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
        ('planning storyboard image', shot.storyboard_asset_ids, shot.storyboard_prompt),
        ('start frame', shot.start_frame_asset_ids, shot.start_frame_prompt),
        ('end frame', shot.end_frame_asset_ids, shot.end_frame_prompt),
    ):
        for asset_id in ids:
            result.append((role, asset_id, fallback))
    return result


def build_image_guide(package: EpisodePackage, path: str | Path, *, storyboard_name: str | None = None) -> Path:
    """Render provenance for every generated production image.

    The guide is deliberately mechanical: output image, ordered input images, exact
    provider prompt, provider/model and approval state.  It is useful both for human
    review and for learning which reusable assets/prompts work across episodes.
    """
    cards = []
    for scene in package.scenes:
        for shot in scene.shots:
            for role, asset_id, fallback in _guide_role_assets(shot):
                try:
                    asset = package.find_asset(asset_id)
                except KeyError:
                    continue
                refs = _generation_refs(asset)
                inputs = []
                for index, ref_id in enumerate(refs, 1):
                    try:
                        ref = package.find_asset(ref_id)
                        thumb = f'<img src="{html.escape(ref.uri)}" alt="input image {index}">'
                    except KeyError:
                        thumb = '<div class="missing">missing input</div>'
                    inputs.append(f'<div class="input"><b>Image {index}</b><code>{html.escape(ref_id)}</code>{thumb}</div>')
                reusable = ' · reusable reference' if asset.metadata.get('reusable_reference') else ''
                cards.append(f'''<article>
<h2>{html.escape(shot.shot_id)} — {html.escape(role)}</h2>
<div class="output"><b>OUTPUT</b>{_asset_image(package, asset_id, alt=f'{shot.shot_id} {role}')}</div>
<h3>Input images, in provider order</h3>
<div class="inputs">{''.join(inputs) if inputs else '<span class="muted">No input images</span>'}</div>
<h3>Exact image-generation prompt</h3>
<pre>{html.escape(_generation_prompt(asset, fallback))}</pre>
<p><b>Asset:</b> <code>{html.escape(asset_id)}</code> · <b>Status:</b> {html.escape(asset.status)}{html.escape(reusable)} · <b>Provider/model:</b> {html.escape(asset.provider or 'unknown')} / {html.escape(asset.model or 'unknown')}</p>
</article>''')
    back = f'<p><a href="{html.escape(storyboard_name)}">← Production storyboard</a></p>' if storyboard_name else ''
    doc = f'''<!doctype html><meta charset="utf-8"><title>{html.escape(package.title)} image guide</title>
<style>{_storyboard_styles()}.inputs{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:.7rem}}.input{{background:#fff;border:1px solid #ccc;border-radius:8px;padding:.5rem}}.input code{{display:block;font-size:.72rem;color:#666;margin:.2rem 0}}.input img{{height:180px;object-fit:contain}}.output{{max-width:850px}}</style>
<h1>{html.escape(package.title)} — Image Generation Guide</h1>
<div class="explain">For each generated image this page shows the exact output, the ordered input/reference images, and the exact prompt sent to the image provider. This is separate from the production storyboard because a planning/reference image is not necessarily a video boundary frame.</div>
{back}{''.join(cards) if cards else '<p>No generated image assets are recorded yet.</p>'}'''
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc)
    return out
