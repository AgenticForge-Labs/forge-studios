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
    assert '*t/3' in filters
    assert "overlay=x='W-w-51'" in filters
    assert 'fade=t=out' in filters


def test_last_story_segment_can_fade_before_brand_card(tmp_path):
    command=_segment_command(
        ffmpeg='ffmpeg',src=Path('clip.mp4'),seg=tmp_path/'segment.mp4',duration='10',
        kind='generated_clip',has_audio=True,fade_out_seconds=0.35,
    )
    assert 'fade=t=out:st=9.65:d=0.35' in command[command.index('-vf')+1]
    assert command[command.index('-af')+1] == 'afade=t=out:st=9.65:d=0.35'


def test_music_mix_preserves_picture_audio_at_full_gain():
    command=_mix_music_command(
        ffmpeg='ffmpeg',video=Path('picture.mp4'),music=Path('music.m4a'),output=Path('final.mp4'),
        level=0.18,duration_seconds=75,fade_in_seconds=0.4,fade_out_seconds=0.8,
    )
    filters=command[command.index('-filter_complex')+1]
    assert 'volume=0.18' in filters
    assert 'amix=inputs=2:duration=first:dropout_transition=0:normalize=0' in filters
    assert '-c:v' in command and command[command.index('-c:v')+1]=='copy'
