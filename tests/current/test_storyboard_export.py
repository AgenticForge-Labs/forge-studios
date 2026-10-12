from __future__ import annotations

import base64
import hashlib
import json
from zipfile import ZipFile

import yaml

from forge_studios.cli import build_parser
from forge_studios.contracts import AssetRecord, EpisodePackage, Shot
from forge_studios.storyboard_export import export_storyboard_review

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _package(tmp_path):
    root = tmp_path / "forge-assets"
    (root / "canon").mkdir(parents=True)
    (root / "canon" / "ember.png").write_bytes(_PNG)
    frame = tmp_path / "frame.png"
    frame.write_bytes(_PNG)
    p = EpisodePackage(
        production_id="production-1", episode_id="s01e01", show_id="forge-born",
        title="Awakening", arc="Awakening and discovery", target_duration_seconds=20,
        shots=[Shot(
            shot_id="awakening", site_id="forge", duration_seconds=20,
            reference_asset_ids=["ember"],
            start_frame_prompt="An objective static wide shot.", video_prompt="Ember wakes.",
            start_frame_asset_ids=["frame"],
        )],
        assets=[
            AssetRecord(asset_id="ember", kind="reference_image", uri="asset://ember",
                        status="canon", authority="locked"),
            AssetRecord(asset_id="frame", kind="start_frame", uri=str(frame),
                        shot_id="awakening", metadata={"generation": {
                            "prompt": "An objective static wide shot.", "model": "mock-model",
                            "provider_settings": {"seed": 42}}}),
        ],
        trace={"generation_attempts": [{"role": "start_frame", "cost": 0.002}],
               "access_token": "do-not-leak"},
    )
    manifest = tmp_path / "assets.yaml"
    manifest.write_text(yaml.safe_dump({"kind": "asset_manifest", "assets": [
        {"asset_id": "ember", "storage_key": "canon/ember.png"}]}))
    path = tmp_path / "episode.json"
    path.write_text(p.model_dump_json())
    return p, path, root, manifest


def test_export_contains_actual_frames_canon_manifest_and_portable_html(tmp_path):
    p, path, root, assets = _package(tmp_path)
    out = tmp_path / "review.zip"
    result = export_storyboard_review(
        p, package_path=path, output_path=out,
        asset_root=root, asset_manifest=assets, git_shas={"forge-studios": "a" * 40})
    assert result["frames"] == 1
    assert result["complete"]
    assert p.find_asset("frame").uri == str(tmp_path / "frame.png")
    assert p.find_asset("ember").uri == "asset://ember"
    with ZipFile(out) as z:
        files = set(z.namelist())
        assert {"episode-package.json", "storyboard.html",
                "storyboard.image-guide.html", "contact-sheet.svg",
                "manifest.json", "SHA256SUMS"} <= files
        data = json.loads(z.read("manifest.json"))
        assert data["shot_count"] == 1
        assert data["frame_count"] == 1
        assert data["git_shas"]["forge-studios"] == "a" * 40
        assert len([f for f in files if f.startswith("images/")]) == 2
        for name, check in data["files"].items():
            assert hashlib.sha256(z.read(name)).hexdigest() == check["sha256"]
        assert "data:image/png;base64," in z.read("contact-sheet.svg").decode()
        assert "images/" in z.read("storyboard.html").decode()
        projection = json.loads(z.read("episode-package.json"))
        assert projection["assets"][0]["uri"].startswith("images/")
        assert projection["trace"]["access_token"] == "[REDACTED]"
        assert "do-not-leak" not in z.read("episode-package.json").decode()


def test_incomplete_export_preserves_diagnostics_without_generating(tmp_path):
    p, path, root, assets = _package(tmp_path)
    p.shots[0].start_frame_asset_ids = []
    p.trace["production_preflight"] = {"is_valid": False, "errors": ["missing"]}
    out = tmp_path / "partial.zip"
    result = export_storyboard_review(
        p, package_path=path, output_path=out, asset_root=root, asset_manifest=assets)
    assert result["frames"] == 0
    assert result["complete"] is False
    with ZipFile(out) as z:
        manifest = json.loads(z.read("manifest.json"))
        assert manifest["missing"][0]["reason"] == "no candidate generated"
        projection = json.loads(z.read("episode-package.json"))
        assert projection["trace"]["production_preflight"]["is_valid"] is False
        assert z.read("contact-sheet.svg").startswith(b"<svg")


def test_export_does_not_traverse_asset_root(tmp_path):
    p, path, root, assets = _package(tmp_path)
    p.assets[0].storage_key = "../outside.png"
    out = tmp_path / "unsafe.zip"
    result = export_storyboard_review(
        p, package_path=path, output_path=out, asset_root=root)
    assert not result["complete"]
    with ZipFile(out) as z:
        assert json.loads(z.read("manifest.json"))["missing"][0]["asset_id"] == "ember"


def test_cli_export_has_no_generate_or_auto_approval_switches():
    parser = build_parser()
    opts = parser.parse_args(["storyboard-export", "--package", "episode.json",
                              "--out", "review.zip", "--open-dir"])
    assert opts.cmd == "storyboard-export"
    assert opts.open_dir
    assert not hasattr(opts, "generate")
    assert not hasattr(opts, "auto_approve")


def test_export_redacts_sensitive_execution_log_and_provenance(tmp_path):
    p, path, root, assets = _package(tmp_path)
    p.trace["provider_uri"] = "https://example.org/image.png?signature=private"
    p.trace["authorization"] = "Bearer very-secret-key"
    log = tmp_path / "run.log"
    log.write_text("started\nAuthorization: Bearer secret\nhttps://test.invalid/image.png?token=secret\nfinished\n")
    out = tmp_path / "safe.zip"
    export_storyboard_review(
        p, package_path=path, output_path=out,
        asset_root=root, asset_manifest=assets, log_path=log,
    )
    with ZipFile(out) as z:
        text = z.read("execution-log.txt").decode()
        assert "finished" in text
        assert "Bearer secret" not in text
        assert "token=secret" not in text
        projection = z.read("episode-package.json").decode()
        assert "very-secret-key" not in projection
        assert "signature=private" not in projection
        assert "[REDACTED]" in projection
