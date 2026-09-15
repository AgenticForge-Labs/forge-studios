from __future__ import annotations
import html
from pathlib import Path
from typing import Callable
from .contracts import EpisodePackage
from .providers.base import MediaRequest

def generate_storyboard_candidates(package: EpisodePackage, animator, *, skip_existing: bool = True, on_shot_complete=None, progress: Callable[[str], None]|None=None):
    """Generate storyboard candidates for the package's shots in narrative order."""
    generated = []
    shots=[shot for scene in package.scenes for shot in scene.shots]
    total=len(shots)
    for index, shot in enumerate(shots,1):
        if skip_existing and (shot.storyboard_asset_ids or shot.approved_storyboard_asset_id):
            if progress: progress(f'[storyboard] {index}/{total} {shot.shot_id}: existing candidate; skipping')
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
        if progress: progress(f'[storyboard] {index}/{total} {shot.shot_id}: generating')
        assets = animator.generate(package, shot.shot_id, role='storyboard')
        generated.extend(assets)
        if on_shot_complete:
            on_shot_complete(package, shot, assets)
        if progress: progress(f'[storyboard] {index}/{total} {shot.shot_id}: saved {len(assets)} candidate(s)')
    return generated

def build_storyboard(package: EpisodePackage, path: str|Path) -> Path:
    cards=[]
    for scene in package.scenes:
        cards.append(f'<h2>{html.escape(scene.scene_id)} — {html.escape(scene.summary)}</h2>')
        for shot in scene.shots:
            asset_id=shot.approved_storyboard_asset_id or (shot.storyboard_asset_ids[-1] if shot.storyboard_asset_ids else None)
            image=''
            if asset_id:
                try:
                    uri=package.find_asset(asset_id).uri
                    image=f'<img src="{html.escape(uri)}" alt="{html.escape(shot.shot_id)}">'
                except KeyError: pass
            cards.append(f'''<article><h3>{html.escape(shot.shot_id)} · {shot.duration_seconds:g}s</h3>{image}
<p><b>Purpose:</b> {html.escape(shot.purpose)}</p><p><b>Visual:</b> {html.escape(shot.visual)}</p>
<p><b>Route:</b> {shot.execution_route} / {shot.render_strategy} · <b>Frames:</b> {shot.frame_plan.mode} · <b>Status:</b> {shot.status}</p>
<p><b>Beats:</b> {html.escape(', '.join(shot.source_beat_ids))}</p></article>''')
    doc=f'''<!doctype html><meta charset="utf-8"><title>{html.escape(package.title)} storyboard</title>
<style>body{{font:16px system-ui;max-width:1100px;margin:2rem auto;padding:0 1rem}}article{{border:1px solid #bbb;border-radius:12px;padding:1rem;margin:1rem 0}}img{{max-width:100%;max-height:540px;border-radius:8px}}</style>
<h1>{html.escape(package.title)} — Storyboard</h1><p>{html.escape(package.premise)}</p>{''.join(cards)}'''
    out=Path(path); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(doc); return out
