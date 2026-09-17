from pathlib import Path

from forge_studios.filmmaker import _segment_command


def _pairs(command: list[str]) -> set[tuple[str, str]]:
    return set(zip(command, command[1:]))


def test_generated_video_segment_preserves_audio():
    command = _segment_command(
        ffmpeg="ffmpeg",
        src=Path("clip.mp4"),
        seg=Path("segment.mp4"),
        duration="20",
        kind="generated_clip",
        has_audio=True,
    )

    assert "-an" not in command
    assert ("-map", "0:v:0") in _pairs(command)
    assert ("-map", "0:a:0") in _pairs(command)
    assert ("-c:a", "aac") in _pairs(command)
    assert ("-ar", "48000") in _pairs(command)
    assert ("-ac", "2") in _pairs(command)


def test_silent_media_gets_compatible_silent_audio_track():
    command = _segment_command(
        ffmpeg="ffmpeg",
        src=Path("frame.png"),
        seg=Path("segment.mp4"),
        duration="20",
        kind="start_frame",
        has_audio=False,
    )

    assert "-an" not in command
    assert "anullsrc=channel_layout=stereo:sample_rate=48000" in command
    assert ("-map", "1:a:0") in _pairs(command)
    assert ("-c:a", "aac") in _pairs(command)
