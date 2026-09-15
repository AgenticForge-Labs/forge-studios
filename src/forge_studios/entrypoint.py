from __future__ import annotations

import os
import sys

from . import cli
from .generation import parse_generation_mode
from .providers import FalProvider

_GENERATION_COMMANDS = {"storyboard", "generate", "auto"}


def _extract_generation_mode(argv: list[str]) -> tuple[list[str], str]:
    """Consume generation --mode without colliding with non-generation command modes."""
    if not argv or argv[0] not in _GENERATION_COMMANDS:
        return argv, "normal"
    cleaned: list[str] = []
    value: str | None = None
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--mode":
            if index + 1 >= len(argv):
                raise SystemExit("--mode requires cheap or normal")
            value = argv[index + 1]
            index += 2
            continue
        if arg.startswith("--mode="):
            value = arg.split("=", 1)[1]
            index += 1
            continue
        cleaned.append(arg)
        index += 1
    try:
        mode = parse_generation_mode(value).value
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    return cleaned, mode


def _option_value(argv: list[str], option: str) -> str | None:
    for index, arg in enumerate(argv):
        if arg == option and index + 1 < len(argv):
            return argv[index + 1]
        if arg.startswith(option + "="):
            return arg.split("=", 1)[1]
    return None


def _log_fal_generation_profile(argv: list[str], mode: str) -> None:
    if _option_value(argv, "--provider") != "fal":
        return
    provider = FalProvider(mode=mode)
    profile = provider.profile
    print(f"[generation] mode={profile.mode.value}", file=sys.stderr)
    print("[generation] image_provider=fal", file=sys.stderr)
    print(f"[generation] image_generate_model={profile.image.generate_model}", file=sys.stderr)
    print(f"[generation] image_edit_model={profile.image.edit_model}", file=sys.stderr)
    if profile.image.width and profile.image.height:
        print(f"[generation] image_target_size={profile.image.width}x{profile.image.height}", file=sys.stderr)
    print("[generation] video_provider=fal", file=sys.stderr)
    print(f"[generation] video_model={profile.video.model}", file=sys.stderr)
    if profile.video.width and profile.video.height:
        print(f"[generation] video_target_size={profile.video.width}x{profile.video.height}", file=sys.stderr)
    elif profile.video.resolution:
        print(f"[generation] video_resolution={profile.video.resolution}", file=sys.stderr)


def main(argv=None):
    raw = list(sys.argv[1:] if argv is None else argv)
    cleaned, mode = _extract_generation_mode(raw)
    if cleaned and cleaned[0] in _GENERATION_COMMANDS:
        # Shared semantic mode, propagated without importing Forge Worlds.
        os.environ["FORGE_STUDIOS_MODE"] = mode
        os.environ["FORGE_WORLDS_MODE"] = mode
        _log_fal_generation_profile(cleaned, mode)
    return cli.main(cleaned)


if __name__ == "__main__":
    raise SystemExit(main())
