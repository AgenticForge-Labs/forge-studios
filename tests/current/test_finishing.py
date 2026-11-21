import shutil

import pytest
from pathlib import Path

from forge_studios.filmmaker import _end_card_command, _segment_command
from forge_studios.finishing import (
    _mix_music_command,
    _sonilo_payload,
    compose_music_prompt,
    load_finishing_profile,
    resolve_end_card,
)


def _profile(tmp_path: Path):
    show=tmp_path/'show'; prompts=tmp_path/'prompts'; root=tmp_path/'assets'; show.mkdir(); prompts.mkdir(); root.mkdir()
    (show/'finishing.yaml').write_text('''\
kind: finishing_profile
id: forge_born_finishing
end_card:
  enabled: true
  main_logo_asset_id: asset_main
  secondary_logo_asset_id: asset_small
music:
  enabled: true
  default_mode: style
  prompt_library: ../prompts/music.yaml
  prompt_id: base
  prompt_influence: 0.5
  mix_level: 0.18
''')
    (prompts/'music.yaml').write_text('''\
kind: prompt_library
prompts:
  - id: base
    template: Warm cinematic score with curiosity and discovery. No vocals.
''')
    (tmp_path/'manifest.yaml').write_text('''\
kind: asset_manifest
assets:
  - asset_id: asset_main
    storage_key: main.png
  - asset_id: asset_small
    storage_key: small.png
''')
    (root/'main.png').write_bytes(b'png'); (root/'small.png').write_bytes(b'png')
    return load_finishing_profile(show/'finishing.yaml'), tmp_path/'manifest.yaml', root


def test_music_modes_keep_style_and_manual_variants_separate(tmp_path):
    profile,_,_=_profile(tmp_path)
    assert compose_music_prompt(profile,'video') is None
    assert compose_music_prompt(profile,'style') == 'Warm cinematic score with curiosity and discovery. No vocals.'
    assert compose_music_prompt(profile,'style',direction='The red dragon approaches the rune stone.') == (
        'Warm cinematic score with curiosity and discovery. No vocals.\n\n'
        'EPISODE DIRECTION\nThe red dragon approaches the rune stone.'
    )
    assert compose_music_prompt(profile,'custom',custom_prompt='Trip-hop instrumental.') == 'Trip-hop instrumental.'


def test_video_only_sonilo_payload_omits_prompt_fields():
    payload=_sonilo_payload('https://example.test/video.mp4',prompt=None,prompt_influence=0.5)
    assert payload == {'video_url':'https://example.test/video.mp4','num_samples':1}
    styled=_sonilo_payload('https://example.test/video.mp4',prompt='house style',prompt_influence=0.65)
    assert styled['prompt']=='house style'
    assert styled['prompt_influence']==0.65


def test_end_card_resolves_stable_asset_ids_and_builds_zoom(tmp_path):
    profile,manifest,root=_profile(tmp_path)
    spec=resolve_end_card(profile,manifest_path=manifest,asset_root=root)
    assert spec is not None
    assert spec.main_logo == root/'main.png'
    command=_end_card_command(ffmpeg='ffmpeg',spec=spec,output=tmp_path/'end.mp4')
    filters=command[command.index('-filter_complex')+1]
    assert 'scale=w=' in filters
    assert 'zoompan=z=' in filters and f'/{3*30 - 1}' in filters
    assert "overlay=x='W-w-51'" in filters
    assert 'fade=t=out' in filters


def test_last_story_segment_can_fade_before_brand_card(tmp_path):
    command=_segment_command(
        ffmpeg='ffmpeg',src=Path('clip.mp4'),seg=tmp_path/'segment.mp4',duration='10',
        kind='generated_clip',has_audio=True,fade_out_seconds=0.35,
    )
    assert 'fade=t=out:st=9.65:d=0.35' in command[command.index('-vf')+1]
    assert command[command.index('-af')+1] == 'apad,afade=t=out:st=9.65:d=0.35'


def test_music_mix_preserves_picture_audio_at_full_gain():
    command=_mix_music_command(
        ffmpeg='ffmpeg',video=Path('picture.mp4'),music=Path('music.m4a'),output=Path('final.mp4'),
        level=0.18,duration_seconds=75,fade_in_seconds=0.4,fade_out_seconds=0.8,
    )
    filters=command[command.index('-filter_complex')+1]
    assert 'volume=0.18' in filters
    assert 'amix=inputs=2:duration=first:dropout_transition=0:normalize=0' in filters
    assert '-c:v' in command and command[command.index('-c:v')+1]=='copy'


def test_finishing_profile_from_package_provides_inline_sonilo_description():
    from forge_studios.contracts import EpisodePackage, Shot
    from forge_studios.finishing import compose_music_prompt, finishing_profile_from_package

    pkg = EpisodePackage(
        production_id='prod', episode_id='ep', show_id='show',
        title='Title', arc='Arc', target_duration_seconds=1.0,
        shots=[Shot(shot_id='s1', duration_seconds=1.0, site_id='site', start_frame_prompt='Still.', video_prompt='Action.')], assets=[],
        finishing={
            'music': {
                'enabled': True,
                'provider': 'fal',
                'model': 'sonilo/v1.1/video-to-music',
                'mode': 'style',
                'description': 'Warm percussive workshop score. No vocals.',
                'prompt_influence': 0.55,
                'mix_level': 0.2,
            }
        },
    )
    profile = finishing_profile_from_package(pkg)
    assert profile is not None
    assert profile.music is not None
    assert profile.music.prompt_influence == 0.55
    assert profile.music.mix_level == 0.2
    assert compose_music_prompt(profile, 'style') == 'Warm percussive workshop score. No vocals.'


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='FFmpeg required')
def test_end_card_renders_episode_metadata_copyright_and_fade_to_black(tmp_path):
    from forge_studios.contracts import EpisodePackage, Shot
    from forge_studios.filmmaker import probe_media, render

    main_logo = tmp_path / 'forge_born.png'
    brand_logo = tmp_path / 'agenticforge.png'
    clip = tmp_path / 'story.mp4'
    subprocess_run = __import__('subprocess').run
    subprocess_run(['ffmpeg', '-y', '-f', 'lavfi', '-i', 'color=red:s=640x360:r=30:d=1', '-frames:v', '1', str(main_logo)], check=True, capture_output=True)
    subprocess_run(['ffmpeg', '-y', '-f', 'lavfi', '-i', 'color=blue:s=320x120:r=30:d=1', '-frames:v', '1', str(brand_logo)], check=True, capture_output=True)
    subprocess_run(['ffmpeg', '-y', '-f', 'lavfi', '-i', 'color=white:s=640x360:r=30:d=1.2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(clip)], check=True, capture_output=True)

    pkg = EpisodePackage(
        production_id='s01e01_awakening', episode_id='s01e01_awakening', show_id='forge_born',
        title='Awakening', arc='Test', target_duration_seconds=1.2,
        shots=[Shot(shot_id='s1', duration_seconds=1.2, site_id='site', start_frame_prompt='Still.', video_prompt='Action.', approved_clip_asset_id='c1')],
        assets=[__import__('forge_studios.contracts', fromlist=['AssetRecord']).AssetRecord(asset_id='c1', kind='generated_clip', status='approved', uri=str(clip))],
    )

    show = tmp_path / 'show'
    prompts = tmp_path / 'prompts'
    assets = tmp_path / 'assets'
    show.mkdir(); prompts.mkdir(); assets.mkdir()
    (show / 'finishing.yaml').write_text('''kind: finishing_profile
id: test_finishing
end_card:
  enabled: true
  main_logo_asset_id: asset_main
  secondary_logo_asset_id: asset_small
  duration_seconds: 2.0
  fade_seconds: 0.4
  copyright_holder: AgenticForge Labs
  copyright_year: 2026
''')
    (tmp_path / 'manifest.yaml').write_text('''kind: asset_manifest
assets:
  - asset_id: asset_main
    storage_key: forge_born.png
  - asset_id: asset_small
    storage_key: agenticforge.png
''')
    (assets / 'forge_born.png').write_bytes(main_logo.read_bytes())
    (assets / 'agenticforge.png').write_bytes(brand_logo.read_bytes())
    profile = load_finishing_profile(show / 'finishing.yaml')
    spec = resolve_end_card(profile, manifest_path=tmp_path / 'manifest.yaml', asset_root=assets, package=pkg)
    assert spec is not None
    assert spec.episode_label == 'SEASON 1 · EPISODE 1'
    assert spec.episode_title == 'Awakening'
    assert spec.copyright_text == '© 2026 AgenticForge Labs. All rights reserved.'

    out = render(pkg, tmp_path / 'with_card.mp4', end_card=spec)
    info = probe_media(str(out))
    assert info['duration_seconds'] >= 3.0

    # Verify fade-to-black boundary at end of story shot (~1.18s)
    boundary = subprocess_run(
        ['ffmpeg', '-v', 'error', '-ss', '1.18', '-i', str(out), '-vf', 'scale=1:1', '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'],
        check=True, capture_output=True,
    ).stdout
    assert max(boundary[:3]) < 30


def test_music_mix_starts_lowering_5s_before_end_and_fades_by_end_card():
    command=_mix_music_command(
        ffmpeg='ffmpeg',video=Path('picture.mp4'),music=Path('music.m4a'),output=Path('final.mp4'),
        level=0.18,duration_seconds=153.35,fade_in_seconds=0.4,fade_out_seconds=2.8,
        fade_out_start_seconds=148.35,
    )
    filters=command[command.index('-filter_complex')+1]
    assert 'afade=t=out:st=148.35:d=2.8' in filters
