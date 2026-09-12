from pathlib import Path
from forge_studios.contracts import AssetRecord,EpisodePackage,Scene,Shot
from forge_studios.continuity import extract_boundary_frames
from forge_studios.timeline_adapter import timeline_from_episode_package
from forge_studios.timeline import iter_clips


def package(tmp_path):
    image=tmp_path/'still.png'; image.write_bytes(b'x')
    p=EpisodePackage(production_id='p',episode_id='e',title='Edit',scenes=[Scene(scene_id='sc',shots=[Shot(shot_id='s',duration_seconds=2,visual='x',render_strategy='still_motion',edit_intent={'motion':'slow_push'})])])
    a=AssetRecord(asset_id='a',kind='storyboard_image',uri=str(image),shot_id='s',status='approved'); p.assets.append(a); p.find_shot('s').approved_storyboard_asset_id='a'; return p


def test_otio_adapter_preserves_shot_and_effect(tmp_path):
    p=package(tmp_path); timeline=timeline_from_episode_package(p,require_approved_media=True); clips=list(iter_clips(timeline))
    assert len(clips)==1
    assert clips[0].metadata['shot_id']=='s'
    assert clips[0].effects[0].metadata['service']=='avfilter.zoompan'


def test_boundary_extraction_registers_lineage(tmp_path,monkeypatch):
    p=package(tmp_path); video=tmp_path/'clip.mp4'; video.write_bytes(b'x'); clip=AssetRecord(asset_id='clip',kind='generated_clip',uri=str(video),shot_id='s'); p.assets.append(clip)
    monkeypatch.setattr('forge_studios.continuity.shutil.which',lambda _: '/usr/bin/ffmpeg')
    def fake_run(cmd,check=True):
        Path(cmd[-1]).write_bytes(b'frame')
    monkeypatch.setattr('forge_studios.continuity.subprocess.run',fake_run)
    first,last=extract_boundary_frames(p,'s','clip',output_dir=tmp_path/'frames')
    assert first.source_asset_ids==['clip'] and last.source_asset_ids==['clip']
    assert first.metadata['frame_role']=='first_frame'
    assert last.metadata['frame_role']=='last_frame'
