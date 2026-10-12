"""Portable, deterministic storyboard-review export; no provider calls or approvals."""
from __future__ import annotations

import base64
import hashlib
import html
import io
import json
import math
import mimetypes
import re
from pathlib import Path
from urllib.parse import urlsplit
from zipfile import ZIP_DEFLATED, ZipFile

from .contracts import EpisodePackage
from .storyboard import build_storyboard

_IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}
_SECRET_KEY = re.compile(r"(^|_)(token|secret|password|api_key|authorization|credential|cookie)(_|$)", re.I)
_SECRET_VALUE = re.compile(r"(?i)(bearer\\s+\\S+|sk-[a-z0-9_-]{16,})")


def _scrub(value):
    """Remove obvious credentials and signed/query URLs from exported evidence."""
    if isinstance(value, dict):
        return {key: "[REDACTED]" if _SECRET_KEY.search(str(key)) else _scrub(item)
                for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    if isinstance(value, str):
        value = _SECRET_VALUE.sub("[REDACTED]", value)
        if value.startswith(("https://", "http://")) and "?" in value:
            return value.split("?", 1)[0] + "?[REDACTED]"
        return value
    return value


def _source_path(uri: str, *, package_dir: Path) -> Path | None:
    """Never fetch arbitrary URLs; resolve only local file and manifest-backed media."""
    if uri.startswith(("https://", "http://", "asset://", "data:")):
        return None
    if uri.startswith("file://"):
        from urllib.parse import unquote
        parts = urlsplit(uri)
        if parts.netloc not in ("", "localhost"):
            return None
        return Path(unquote(parts.path))
    if "://" in uri:
        return None
    path = Path(uri).expanduser()
    return path if path.is_absolute() else package_dir / path


def _resolve_media(asset, package_dir: Path, asset_root: Path | None, storage_keys: dict[str, str]) -> Path | None:
    candidate = _source_path(asset.uri, package_dir=package_dir)
    if candidate is not None and candidate.is_file():
        return candidate.resolve()
    if asset_root is not None and (asset.storage_key or storage_keys.get(asset.asset_id)):
        key = Path(asset.storage_key or storage_keys[asset.asset_id])
        if key.is_absolute() or ".." in key.parts:
            return None
        candidate = (asset_root / key).resolve()
        if candidate.is_relative_to(asset_root.resolve()) and candidate.is_file():
            return candidate
    return None


def _json(value) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True, default=str) + "\n").encode("utf-8")


def _sheet_svg(panels: list[tuple[str, bytes, str]]) -> bytes:
    """Self-contained, labeled contact sheet with actual embedded raster bytes."""
    cols, cell_w, image_h, header_h = 2, 600, 338, 64
    rows = max(1, math.ceil(len(panels) / cols))
    cell_h = image_h + header_h
    pieces = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{cols*cell_w}" height="{rows*cell_h}" viewBox="0 0 {cols*cell_w} {rows*cell_h}">',
              '<rect width="100%" height="100%" fill="#e8e8e8"/>']
    for i, (label, data, mime) in enumerate(panels):
        x, y = (i % cols) * cell_w, (i // cols) * cell_h
        encoded = base64.b64encode(data).decode("ascii")
        pieces.extend([
            f'<rect x="{x+4}" y="{y+4}" width="{cell_w-8}" height="{cell_h-8}" rx="8" fill="white"/>',
            f'<text x="{x+16}" y="{y+37}" font-family="sans-serif" font-size="19" fill="#111">{html.escape(label)}</text>',
            f'<image x="{x+10}" y="{y+header_h}" width="{cell_w-20}" height="{image_h-10}" preserveAspectRatio="xMidYMid meet" href="data:{mime};base64,{encoded}"/>',
        ])
    if not panels:
        pieces.append('<text x="20" y="40" font-family="sans-serif" font-size="22">No local storyboard frames available</text>')
    pieces.append("</svg>")
    return "".join(pieces).encode("utf-8")


def export_storyboard_review(
    package: EpisodePackage,
    *,
    package_path: str | Path,
    output_path: str | Path,
    asset_root: str | Path | None = None,
    asset_manifest: str | Path | None = None,
    telemetry_path: str | Path | None = None,
    log_path: str | Path | None = None,
    git_shas: dict[str, str] | None = None,
) -> dict:
    """Export a self-contained review ZIP even when only some frames exist.

    The original package is never changed. The included EpisodePackage is a review
    projection whose URIs are portable, not a replacement production authority.
    """
    source = Path(package_path).expanduser().resolve()
    target = Path(output_path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    root = Path(asset_root).expanduser().resolve() if asset_root else None
    storage_keys: dict[str, str] = {}
    if asset_manifest:
        import yaml
        raw = yaml.safe_load(Path(asset_manifest).expanduser().read_text())
        if not isinstance(raw, dict) or raw.get("kind") != "asset_manifest":
            raise ValueError("asset manifest must have kind=asset_manifest")
        storage_keys = {str(item["asset_id"]): str(item["storage_key"])
                        for item in raw.get("assets", [])
                        if isinstance(item, dict) and "asset_id" in item and "storage_key" in item}
    review = package.model_copy(deep=True)
    payloads: dict[str, bytes] = {}
    image_locations: dict[str, str] = {}
    unresolved: set[str] = set()
    missing: list[dict] = []
    panels: list[tuple[str, bytes, str]] = []
    shots: list[dict] = []

    def include(asset_id: str, *, label: str) -> str | None:
        if asset_id in image_locations:
            return image_locations[asset_id]
        if asset_id in unresolved:
            return None
        asset = review.find_asset(asset_id)
        path = _resolve_media(asset, source.parent, root, storage_keys)
        suffix = path.suffix.lower() if path else ""
        if path is None or suffix not in _IMAGE_TYPES:
            unresolved.add(asset_id)
            missing.append({"asset_id": asset_id, "label": label, "reason": "local supported raster image unavailable"})
            asset.uri = f"asset://unbundled/{asset_id}"
            return None
        data = path.read_bytes()
        # Paths in ZIP never include user-controlled shot IDs or filesystem names.
        inside = f"images/{len(image_locations)+1:03d}{suffix}"
        payloads[inside] = data
        image_locations[asset_id] = inside
        asset.uri = inside
        return inside

    for index, shot in enumerate(review.shots, 1):
        frames = []
        for role, ids, approved in (
            ("start_frame", shot.start_frame_asset_ids, shot.approved_start_frame_asset_id),
            ("end_frame", shot.end_frame_asset_ids, shot.approved_end_frame_asset_id),
        ):
            if role == "end_frame" and shot.frame_plan.mode != "start_and_end":
                continue
            selected = approved or (ids[-1] if ids else None)
            entry = {"role": role, "asset_id": selected, "path": None,
                     "prompt": shot.start_frame_prompt if role == "start_frame" else shot.end_frame_prompt}
            if selected:
                entry["path"] = include(selected, label=f"{shot.shot_id}:{role}")
                if entry["path"]:
                    data = payloads[entry["path"]]
                    panels.append((f"{index:02d} {shot.shot_id} / {role}", data, _IMAGE_TYPES[Path(entry["path"]).suffix]))
            else:
                missing.append({"shot_id": shot.shot_id, "role": role, "reason": "no candidate generated"})
            frames.append(entry)
        for ref_id in shot.reference_asset_ids:
            include(ref_id, label=f"{shot.shot_id}:reference")
        shots.append({"index": index, "shot_id": shot.shot_id,
                      "site_id": shot.site_id, "duration_seconds": shot.duration_seconds,
                      "frame_plan_mode": shot.frame_plan.mode,
                      "reference_asset_ids": shot.reference_asset_ids,
                      "reference_uses": shot.reference_uses,
                      "frames": frames, "video_prompt": shot.video_prompt})

    # Package may retain unused media; don't publish workstation paths or signed URLs.
    for asset in review.assets:
        if asset.asset_id not in image_locations:
            asset.uri = f"asset://unbundled/{asset.asset_id}"
    package_dict = _scrub(review.model_dump(mode="json", exclude_none=True, exclude_defaults=True))
    package_dict["package_version"] = review.package_version
    payloads["episode-package.json"] = _json(package_dict)

    # Reuse the existing Studios storyboard renderer with only portable review URIs.
    from tempfile import TemporaryDirectory
    with TemporaryDirectory() as temp:
        html_path = build_storyboard(review, Path(temp) / "storyboard.html")
        payloads["storyboard.html"] = html_path.read_bytes()
        payloads["storyboard.image-guide.html"] = html_path.with_name("storyboard.image-guide.html").read_bytes()

    payloads["contact-sheet.svg"] = _sheet_svg(panels)
    png_available = False
    try:
        from PIL import Image, ImageDraw
        width, cell_w, cell_h = 1200, 600, 405
        sheet = Image.new("RGB", (width, max(1, math.ceil(len(panels)/2))*cell_h), "#eeeeee")
        painter = ImageDraw.Draw(sheet)
        for i, (label, data, _mime) in enumerate(panels):
            x, y = i % 2 * cell_w, i // 2 * cell_h
            with Image.open(io.BytesIO(data)) as opened:
                img = opened.convert("RGB")
                img.thumbnail((580, 333))
                sheet.paste(img, (x + (cell_w-img.width)//2, y+55+(333-img.height)//2))
            painter.text((x+12,y+14),label,fill="#111111")
        data_out=io.BytesIO()
        sheet.save(data_out,format="PNG")
        payloads["contact-sheet.png"] = data_out.getvalue()
        png_available = True
    except ImportError:
        pass  # SVG contact sheet is always generated without optional Pillow.

    if log_path:
        log = Path(log_path).expanduser()
        if log.is_file():
            clean_lines = []
            for line in log.read_text(errors="replace")[-1_000_000:].splitlines():
                if re.search(r"(?i)\\b(token|secret|password|api[_-]?key|authorization|credential|cookie)\\b", line):
                    clean_lines.append("[REDACTED LOG LINE]")
                else:
                    line = re.sub(r"https?://\\S+\\?\\S+", "[REDACTED URL]", line)
                    clean_lines.append(_SECRET_VALUE.sub("[REDACTED]", line))
            payloads["execution-log.txt"] = ("\\n".join(clean_lines) + "\\n").encode("utf-8")

    if telemetry_path:
        telemetry = Path(telemetry_path).expanduser()
        if telemetry.is_file():
            # Export only records for this production; sanitize metadata and never
            # add arbitrary log files containing credentials.
            events = []
            for line in telemetry.read_text().splitlines():
                try:
                    record = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if isinstance(record, dict) and record.get("production_id") == package.production_id:
                    events.append(_scrub(record))
            payloads["generation-events.json"] = _json(events)

    manifest = {"kind": "forge_studios_storyboard_review", "schema_version": 1,
                "episode_id": package.episode_id, "production_id": package.production_id,
                "shot_count": len(shots), "frame_count": len(panels),
                "complete": not missing, "missing": missing, "shots": shots,
                "canonical_references": [
                    {"asset_id": a.asset_id, "path": image_locations.get(a.asset_id),
                     "status": a.status, "authority": a.authority}
                    for a in review.assets if a.kind == "reference_image"
                ],
                "git_shas": git_shas or {}, "contact_sheet_png": png_available,
                "files": {}}
    for name, data in payloads.items():
        manifest["files"][name] = {"sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}
    payloads["manifest.json"] = _json(_scrub(manifest))
    payloads["SHA256SUMS"] = ("".join(
        f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in sorted(payloads.items())
    )).encode("utf-8")
    with ZipFile(target, "w", compression=ZIP_DEFLATED) as archive:
        for name, data in sorted(payloads.items()):
            archive.writestr(name, data)
    return {"zip": str(target.resolve()), "shots": len(shots),
            "frames": len(panels), "missing": len(missing), "complete": not missing}
