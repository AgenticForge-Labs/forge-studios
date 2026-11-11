"""Exercise assembly with real media whose durations differ from the package."""
import json
import shutil
import subprocess

import pytest

from forge_studios.cli import build_parser
from forge_studios.contracts import AssetRecord, EpisodePackage, Shot
from forge_studios.filmmaker import EndCardRenderSpec, probe_media, render, write_edit_plan
from forge_studios.telemetry import TelemetrySink

pytestmark = pytest.mark.skipif(
    not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='FFmpeg required',
)


def run(*args):
    return subprocess.run(args, check=True, capture_output=True)


@pytest.fixture
def media_package(tmp_path):
    first, second = tmp_path/'first.mp4', tmp_path/'second.mp4'
    # First is longer than authored and has audio ending before its video.
    run('ffmpeg', '-y', '-f', 'lavfi', '-i', 'color=red:s=320x180:r=30:d=1.2',
        '-f', 'lavfi', '-i', 'sine=frequency=440:duration=0.4',
        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(first))
    # Second is shorter than authored, silent, and a different aspect ratio.
    run('ffmpeg', '-y', '-f', 'lavfi', '-i', 'color=blue:s=160x120:r=30:d=0.6',
        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(second))
    return EpisodePackage(
        production_id='production', episode_id='episode', show_id='show',
        title='Assembly', arc='Test', target_duration_seconds=2,
        shots=[Shot(shot_id=name, duration_seconds=1, site_id='site',
                    start_frame_prompt='Still.', video_prompt='Action.',
                    approved_clip_asset_id=name) for name in ('first', 'second')],
        assets=[AssetRecord(asset_id=name, kind='generated_clip', status='approved', uri=str(path))
                for name, path in [('first', first), ('second', second)]],
    )


@pytest.mark.parametrize('resolution,expected', [('auto', (320,180)), ('640x360', (640,360)), ('160x90', (160,90))])
def test_render_full_clips_and_resolution(media_package, tmp_path, resolution, expected):
    out=render(media_package, tmp_path/'out.mp4', resolution=resolution,
               telemetry=TelemetrySink(tmp_path/'events.jsonl'))
    media=probe_media(str(out))
    assert (media['width'], media['height']) == expected
    assert media['has_audio']
    assert media['duration_seconds'] == pytest.approx(1.8, abs=0.08)
    # Sample inside the original final 0.2s of first shot and inside the second.
    # This catches package truncation, order changes, and missing silent clips.
    for time, channel in [('1.1', 0), ('1.5', 2)]:
        pixel=run('ffmpeg', '-v', 'error', '-ss', time, '-i', str(out),
                  '-vf', 'scale=1:1', '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-').stdout
        assert pixel[channel] > 150
    # Original sound survives normalization.
    audio=run('ffmpeg', '-v', 'error', '-i', str(out), '-t', '0.3',
              '-f', 's16le', '-ac', '1', '-').stdout
    assert any(audio)


def test_edit_plan_and_timeline_use_real_durations(media_package, tmp_path):
    plan=json.loads(write_edit_plan(media_package, tmp_path/'plan.json').read_text())['timeline']
    assert [item['duration_seconds'] for item in plan] == pytest.approx([1.2, 0.6])
    assert [item['start_seconds'] for item in plan] == pytest.approx([0, 1.2])
    pytest.importorskip('opentimelineio')
    from forge_studios.timeline_adapter import timeline_from_episode_package
    from forge_studios.timeline import iter_clips
    timeline=timeline_from_episode_package(media_package, require_approved_media=True)
    assert [clip.duration().to_seconds() for clip in iter_clips(timeline)] == pytest.approx([1.2, 0.6])


def test_still_hold_and_end_card_follow_video_resolution(media_package, tmp_path):
    image=tmp_path/'logo.png'
    run('ffmpeg', '-y', '-f', 'lavfi', '-i', 'color=white:s=80x40', '-frames:v', '1', str(image))
    media_package.assets.append(AssetRecord(asset_id='still',kind='start_frame',uri=str(image),status='approved'))
    shot=media_package.shots[0]
    shot.approved_clip_asset_id=None
    shot.approved_start_frame_asset_id='still'
    spec=EndCardRenderSpec(main_logo=image, secondary_logo=image, duration_seconds=0.4, fade_seconds=0.1)
    out=render(media_package, tmp_path/'out.mp4', end_card=spec,
               telemetry=TelemetrySink(tmp_path/'events.jsonl'))
    media=probe_media(str(out))
    assert (media['width'], media['height']) == (160,120)  # first video, not preceding still
    assert media['duration_seconds'] == pytest.approx(2.0, abs=0.12)
    card=probe_media(str(tmp_path/'out-segments'/'end-card.mp4'))
    assert (card['width'], card['height']) == (160,120)


@pytest.mark.parametrize('resolution', ['1921x1080', '0x720', '1080p', '10x20x30'])
def test_cli_rejects_invalid_resolution(resolution):
    with pytest.raises(SystemExit):
        build_parser().parse_args(['render','--package','package.json','--resolution',resolution])


def test_finishing_passes_resolution_to_picture_lock(media_package, tmp_path, monkeypatch):
    from forge_studios import finishing
    profile=finishing.FinishingProfile(path=tmp_path/'finishing.yaml', profile_id='test', end_card=None, music=None)
    calls=[]
    monkeypatch.setattr(finishing, 'render', lambda *args, **kwargs: calls.append(kwargs))
    monkeypatch.setattr(finishing, 'score_video', lambda picture, out, **kwargs: out)
    finishing.finish_episode(media_package, package_path=tmp_path/'package.json', output=tmp_path/'final.mp4',
                             profile=profile, resolution='640x360', music_mode='none',
                             telemetry=TelemetrySink(tmp_path/'events.jsonl'))
    assert calls[0]['resolution'] == '640x360'


def test_timeline_projection_uses_media_timing_without_otio(media_package, monkeypatch):
    from forge_studios import timeline_adapter
    monkeypatch.setattr(timeline_adapter, 'build_timeline', lambda clips, **kwargs: clips)
    clips=timeline_adapter.timeline_from_episode_package(media_package, require_approved_media=True)
    assert [clip.duration_seconds for clip in clips] == pytest.approx([1.2, 0.6])
