from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from forge_studios import image_compare
from forge_studios.contracts import AssetRecord, EpisodePackage, Shot

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _test_inputs(tmp_path):
    asset_root = tmp_path / "forge-assets"
    asset_root.mkdir()
    asset1 = asset_root / "forge.png"
    asset2 = asset_root / "ember.png"
    asset1.write_bytes(_PNG)
    asset2.write_bytes(_PNG)
    original = EpisodePackage(
        production_id="p1", show_id="forge-born", episode_id="episode1",
        title="Awakening", arc="Wake", target_duration_seconds=20,
        shots=[Shot(shot_id="awakening", site_id="fire-forge", duration_seconds=20,
                    reference_asset_ids=["forge", "ember"],
                    start_frame_prompt="REFERENCES\n@image1: world.\n@image2: character.\nKeep camera identical.",
                    video_prompt="Dragon wakes.")],
        assets=[
            AssetRecord(asset_id="forge", kind="reference_image", uri="asset://forge",
                        status="canon", authority="locked"),
            AssetRecord(asset_id="ember", kind="reference_image", uri="asset://ember",
                        status="canon", authority="locked"),
        ],
    )
    package_path = tmp_path / "episode.json"
    package_path.write_text(original.model_dump_json())
    manifest_path = tmp_path / "forge-born.yaml"
    manifest_path.write_text(yaml.safe_dump({
        "kind": "asset_manifest",
        "assets": [
            {"asset_id": "forge", "storage_key": "forge.png"},
            {"asset_id": "ember", "storage_key": "ember.png"},
        ],
    }))
    return package_path, manifest_path, asset_root


def test_model_selection_and_payloads_keep_original_prompt():
    prompt = "REFERENCE @image1 and @image2: untouched."
    uris = ["asset://ref_0", "asset://ref_1"]
    assert image_compare.parse_models("openai,nano,klein,qwen") == list(image_compare.DEFAULT_MODELS)
    with pytest.raises(ValueError):
        image_compare.parse_models("openai,openai")
    with pytest.raises(ValueError):
        image_compare.parse_models("unknown")
    for name in image_compare.DEFAULT_MODELS:
        model, args = image_compare.request_for(name, prompt, uris)
        assert model == image_compare.MODEL_SPECS[name]["id"]
        assert args["prompt"] == prompt
        assert args["image_urls"] == uris
        assert args["num_images"] == 1
    assert image_compare.request_for("nano", prompt, uris)[1]["aspect_ratio"] == "16:9"
    assert image_compare.request_for("openai", prompt, uris)[1]["quality"] == "high"


def test_offline_comparison_zip_has_no_provider_calls(tmp_path, monkeypatch):
    pkg, manifest, root = _test_inputs(tmp_path)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("provider was called without --confirm-paid")
    monkeypatch.setattr(image_compare, "_one_generation", forbidden)
    out = tmp_path / "review.zip"
    summary = image_compare.compare(pkg, manifest, root, out, models=["openai", "nano"])
    assert summary["paid_calls_enabled"] is False
    assert summary["attempts"] == []
    with ZipFile(out) as archive:
        assert {"prompt.txt", "index.html", "manifest.json",
                "SHA256SUMS", "references/01.png", "references/02.png"} <= set(archive.namelist())
        assert archive.read("prompt.txt") == b"REFERENCES\n@image1: world.\n@image2: character.\nKeep camera identical."
        assert "planned" in archive.read("index.html").decode()
        for name, data in ((x, archive.read(x)) for x in archive.namelist()
                           if x != "SHA256SUMS"):
            assert hashlib.sha256(data).hexdigest() in archive.read("SHA256SUMS").decode()
    assert json.loads(pkg.read_text())["assets"][0]["uri"] == "asset://forge"


def test_paid_comparison_uses_development_runtime_and_one_request_per_model(tmp_path, monkeypatch):
    pkg, manifest, root = _test_inputs(tmp_path)
    calls = []

    class FakeRuntime:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None
        def generate(self, *, model, arguments, assets, trace, **kwargs):
            calls.append((model, arguments, assets, trace))
            return {"result": {"images": [{"url": "https://v3b.fal.media/files/dummy.png",
                                           "width": 512, "height": 512}]},
                    "provider": "fal", "request_id": "fake-id"}

    monkeypatch.setattr(image_compare, "AIRuntimeMediaClient", FakeRuntime)
    before = pkg.read_bytes()
    out = tmp_path / "results.zip"
    summary = image_compare.compare(pkg, manifest, root, out,
        models=["nano", "openai"], confirm_paid=True, download=lambda _url: _PNG)
    assert len(calls) == 2
    assert {model for model, *_ in calls} == {
        "openai/gpt-image-2/edit", "fal-ai/nano-banana-2/edit"
    }
    for _model, args, uploaded, trace in calls:
        assert args["image_urls"] == ["asset://ref_0", "asset://ref_1"]
        assert list(uploaded) == ["ref_0", "ref_1"]
        assert args["prompt"] == "REFERENCES\n@image1: world.\n@image2: character.\nKeep camera identical."
        assert trace["source_system"] == "forge-studios"
    assert len(summary["attempts"]) == 2
    assert all(x["status"] == "success" for x in summary["attempts"])
    assert pkg.read_bytes() == before
    with ZipFile(out) as archive:
        assert archive.read("images/nano.png") == _PNG
        assert archive.read("images/openai.png") == _PNG
        assert archive.read("manifest.json")


def test_failed_model_still_produces_review_zip_with_other_result(tmp_path, monkeypatch):
    pkg, manifest, root = _test_inputs(tmp_path)
    def fake_generate(name, *_args, **_kwargs):
        if name == "nano":
            return {"label": "nano", "model": image_compare.MODEL_SPECS[name]["id"],
                    "status": "failed", "error": "test failure"}, None
        return {"label": "klein", "model": image_compare.MODEL_SPECS[name]["id"],
                "status": "success", "extension": ".png",
                "output_sha256": hashlib.sha256(_PNG).hexdigest()}, _PNG
    monkeypatch.setattr(image_compare, "_one_generation", fake_generate)
    out = tmp_path / "partial.zip"
    result = image_compare.compare(pkg, manifest, root, out,
                                   models=["nano", "klein"], confirm_paid=True)
    assert len(result["attempts"]) == 2
    with ZipFile(out) as z:
        assert "images/klein.png" in z.namelist()
        assert "images/nano.png" not in z.namelist()
        assert b"test failure" in z.read("index.html")


def test_comparison_runner_requires_explicit_payment_and_existing_inputs():
    path = Path(__file__).resolve().parents[2] / "scripts/run_image_model_comparison.sh"
    script = path.read_text()
    assert "--confirm-paid" in script
    assert "forge_studios.runtime_preflight" in script
    assert "forge_studios.image_compare" in script
    assert "OPENAI_API_KEY" not in script
    assert "GOOGLE_API_KEY" not in script
