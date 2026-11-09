"""Deterministic MIME identification for locally held raster bytes.

Provider transport and review projections must not trust historic file suffixes:
a legacy canonical *.png may contain valid JPEG-encoded pixels.
"""

from __future__ import annotations


def sniff_image_bytes(data: bytes) -> tuple[str, str] | None:
    """Return canonical (extension, MIME) for supported raster formats."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png", "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg", "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif", "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp", "image/webp"
    return None
