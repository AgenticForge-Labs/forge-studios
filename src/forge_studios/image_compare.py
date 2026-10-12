"""Experimental image-model comparison; never mutates the production EpisodePackage.

Uses the identical authored start-frame prompt and ordered reference images for
each Fal-hosted model. Platform AI Runtime owns credentials and uploads.
Comparison outputs are review evidence, not production candidate records.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import html
import json
import re
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from .ai_runtime import AIRuntimeMediaClient, runtime_asset, studios_trace
from .asset_resolution import bind_missing_references
from .io import load_package
from .media_types import sniff_image_bytes

# Model-specific request assembly belongs to deterministic provider execution.
# All endpoints below are Fal image-editing APIs (no OpenAI/Google API keys).
MODEL_SPECS = {
    "openai": {
        "id": "openai/gpt-image-2/edit",
        "options": {"image_size": {"width": 1920, "height": 1080},
                    "quality": "high", "num_images": 1},
        "max_references": 16,
    },
    "nano": {
        "id": "fal-ai/nano-banana-2/edit",
        "options": {"aspect_ratio": "16:9", "resolution": "2K", "num_images": 1},
        "max_references": 14,
    },
    "klein": {
        "id": "fal-ai/flux-2/klein/4b/edit",
        "options": {"image_size": "landscape_16_9", "num_images": 1},
        "max_references": 4,
    },
    "qwen": {
        "id": "fal-ai/qwen-image-edit-2511",
        "options": {"image_size": "landscape_16_9", "num_images": 1},
        "max_references": 3,
    },
}
DEFAULT_MODELS = tuple(MODEL_SPECS)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _clean_message(message: str) -> str:
    """Never persist credentials, provider query tokens, or local URLs."""
    value = re.sub(r"(?i)bearer\s+\S+", "Bearer [REDACTED]", message)
    value = re.sub(r"https?://[^\s\"']+", "[provider URL omitted]", value)
    return value[:1500]


def parse_models(value: str) -> list[str]:
    names = [part.strip() for part in value.split(",") if part.strip()]
    if not names or len(names) != len(set(names)) or any(name not in MODEL_SPECS for name in names):
        raise ValueError("models must be a unique comma-separated subset of openai,nano,klein,qwen")
    return names


def request_for(model_name: str, prompt: str, reference_uris: list[str]) -> tuple[str, dict]:
    spec = MODEL_SPECS[model_name]
    if len(reference_uris) > spec["max_references"]:
        raise ValueError(f"{model_name} accepts at most {spec['max_references']} references")
    if not reference_uris:
        raise ValueError("image edit comparison requires canonical references")
    # Exactly the existing package prompt; no auto-generated semantic rewriting.
    return spec["id"], {"prompt": prompt, "image_urls": reference_uris,
                        **spec["options"]}


def _media_download(url: str, *, client: httpx.Client | None = None) -> bytes:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Fal output must be an HTTPS image URL")
    # The provider may return either fal.media or a storage backend URL.
    if not (parsed.hostname.endswith(".fal.media") or parsed.hostname == "fal.media"
            or parsed.hostname == "storage.googleapis.com"):
        raise ValueError("Unexpected image output host; refusing automatic download")
    owned = client is None
    transport = client or httpx.Client(timeout=120.0, follow_redirects=False)
    try:
        with transport.stream("GET", url) as response:
            response.raise_for_status()
            output = bytearray()
            for chunk in response.iter_bytes():
                output.extend(chunk)
                if len(output) > 25 * 1024 * 1024:
                    raise ValueError("Image output exceeds 25 MiB safety limit")
            return bytes(output)
    finally:
        if owned:
            transport.close()


def _selected_baseline(package, shot) -> tuple[bytes, dict] | None:
    for aid in reversed(shot.start_frame_asset_ids):
        try:
            record = package.find_asset(aid)
        except KeyError:
            continue
        if record.status not in {"candidate", "approved", "canon"}:
            continue
        path = Path(record.uri).expanduser()
        if not path.is_file():
            continue
        data = path.read_bytes()
        if not sniff_image_bytes(data):
            continue
        return data, {"asset_id": aid, "provider": record.provider, "model": record.model,
                      "shot_id": shot.shot_id, "sha256": _sha(data)}
    return None


def _resolve_inputs(package_path: Path, manifest_path: Path, asset_root: Path, shot_id: str):
    package = load_package(package_path)
    shot = package.find_shot(shot_id)
    if not shot.start_frame_prompt:
        raise ValueError(f"Shot {shot_id!r} lacks an authored start_frame_prompt")
    # Resolve a *copy* from the loaded original and never save it back.
    bind_missing_references(package, manifest_path=manifest_path, asset_root=asset_root)
    references = []
    for aid in shot.reference_asset_ids:
        record = package.find_asset(aid)
        path = Path(record.uri).expanduser()
        if not path.is_file() or not sniff_image_bytes(path.read_bytes()):
            raise ValueError(f"Canonical reference {aid!r} is not a readable local raster")
        references.append((aid, path, path.read_bytes()))
    if not references:
        raise ValueError("No canonical references for comparison")
    baseline = _selected_baseline(package, shot)
    return package, shot, references, baseline


def _one_generation(name: str, prompt: str, image_references: list[tuple[str, Path, bytes]],
                    package, shot, run_id: str, download=_media_download) -> tuple[dict, bytes | None]:
    start = time.monotonic()
    attempt = uuid4().hex
    evidence = {"label": name, "model": MODEL_SPECS[name]["id"], "attempt_id": attempt,
                "status": "failed", "latency_seconds": None,
                "cost_usd": None, "output_sha256": None, "error": None,
                "request_options": MODEL_SPECS[name]["options"],
                "reference_asset_ids": [aid for aid, _path, _data in image_references],
                "prompt_sha256": _sha(prompt.encode("utf-8"))}
    try:
        uploaded = {}
        uris = []
        for index, (aid, path, _data) in enumerate(image_references):
            # Existing shared upload primitive detects JPEG bytes within .png names.
            uri = runtime_asset(str(path), assets=uploaded, asset_id=f"ref_{index}")
            if not uri or not uri.startswith("asset://"):
                raise ValueError(f"Reference {aid!r} could not be transported")
            uris.append(uri)
        model, arguments = request_for(name, prompt, uris)
        trace = studios_trace(production_id=package.production_id,
                              episode_id=package.episode_id, show_id=package.show_id,
                              shot_id=shot.shot_id, attempt_id=attempt,
                              role="start_frame_model_comparison",
                              purpose=f"experimental {run_id} image comparison")
        with AIRuntimeMediaClient() as runtime:
            envelope = runtime.generate(
                model=model, arguments=arguments, assets=uploaded,
                trace=trace, client_timeout_seconds=600,
            )
        response = envelope.get("result") or {}
        images = response.get("images") or []
        if not images or not isinstance(images[0], dict):
            raise ValueError("Fal returned no image object")
        image_url = images[0].get("url")
        if not isinstance(image_url, str):
            raise TypeError("Fal returned no image URL")
        data = download(image_url)
        signature = sniff_image_bytes(data)
        if not signature:
            raise ValueError("Fal returned unrecognized raster image data")
        evidence.update({
            "status": "success",
            "request_id": envelope.get("request_id"),
            "provider": envelope.get("provider"),
            "result_media": {
                "content_type": signature[1], "bytes": len(data),
                "width": images[0].get("width"),
                "height": images[0].get("height"),
            },
            "output_sha256": _sha(data),
            "extension": signature[0],
        })
        return evidence, data
    except Exception as exc:  # noqa: BLE001 - preserve partial results from individual providers
        evidence["error"] = _clean_message(f"{type(exc).__name__}: {exc}")
        return evidence, None
    finally:
        evidence["latency_seconds"] = round(time.monotonic() - start, 3)


def _write_zip(out: Path, files: dict[str, bytes]) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    # No entries derive directly from user-controlled IDs/pathnames.
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(files):
            archive.writestr(name, files[name])


def _review_html(rows: list[dict], reference_records: list[dict]) -> str:
    content = ['<!doctype html><meta charset="utf-8"><title>Shot image model comparison</title>',
               ('<style>body{font:16px system-ui;max-width:1280px;margin:2rem auto;padding:0 1rem}'
                'section{border:1px solid #bbb;border-radius:12px;padding:1rem;margin:1rem 0}'
                'img{max-width:100%;max-height:720px;object-fit:contain}</style>'),
               "<h1>Awakening image model comparison</h1>",
               "<p>Unapproved experimental images. No video generated; source EpisodePackage unchanged.</p>",
               '<p><a href="prompt.txt">Original authored start-frame prompt</a></p>']
    content.append("<h2>Ordered canonical references</h2>")
    for ref in reference_records:
        content.append(f'<section><h3>{html.escape(ref["asset_id"])}</h3>'
                       f'<img src="{ref["file"]}" alt="{html.escape(ref["asset_id"])}"></section>')
    content.append("<h2>Results</h2>")
    for row in rows:
        content.append(f'<section><h3>{html.escape(row["model"])}</h3>'
                       f'<p>{html.escape(row["status"])}</p>')
        if row.get("file"):
            content.append(f'<img src="{row["file"]}" alt="Generated image">')
        if row.get("error"):
            content.append(f'<p>{html.escape(row["error"])}</p>')
        content.append("</section>")
    return "".join(content)


def compare(package_path: Path, manifest: Path, asset_root: Path, out: Path,
            *, shot_id: str = "awakening", models: list[str] | None = None,
            confirm_paid: bool = False, download=_media_download) -> dict:
    selected = list(models or DEFAULT_MODELS)
    for model in selected:
        if model not in MODEL_SPECS:
            raise ValueError(f"Unknown model: {model}")
    package, shot, refs, baseline = _resolve_inputs(package_path, manifest, asset_root, shot_id)
    run_id = uuid4().hex[:12]
    files: dict[str, bytes] = {"prompt.txt": shot.start_frame_prompt.encode("utf-8")}
    ref_records = []
    for i, (aid, path, data) in enumerate(refs, 1):
        suffix = sniff_image_bytes(data)[0]
        name = f"references/{i:02d}{suffix}"
        files[name] = data
        ref_records.append({"index": i, "asset_id": aid, "sha256": _sha(data), "file": name})
    rows = []
    if baseline:
        data, details = baseline
        ext = sniff_image_bytes(data)[0]
        name = "images/existing_fal_baseline" + ext
        files[name] = data
        rows.append({"model": details["model"] or "Existing candidate", "status": "existing baseline",
                     "file": name, "sha256": _sha(data), "asset_id": details["asset_id"]})
    attempts = []
    if confirm_paid:
        # One call/model, dispatched concurrently; do not retry on errors.
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(selected))) as pool:
            futures = {
                pool.submit(_one_generation, name, shot.start_frame_prompt, refs,
                            package, shot, run_id, download): name
                for name in selected
            }
            for future in concurrent.futures.as_completed(futures):
                name = futures[future]
                attempt, data = future.result()
                attempts.append(attempt)
                row = {"model": attempt["model"], "status": attempt["status"],
                       "error": attempt.get("error"), "file": None}
                if data is not None:
                    file_name = "images/" + name + attempt["extension"]
                    files[file_name] = data
                    row["file"] = file_name
                rows.append(row)
    else:
        rows.extend({"model": MODEL_SPECS[name]["id"], "status": "planned; not generated"}
                    for name in selected)
    attempts.sort(key=lambda item: item["label"])
    rows.sort(key=lambda item: item["model"])
    summary = {
        "run_id": run_id, "shot_id": shot.shot_id, "episode_id": package.episode_id,
        "package_source_sha256": _sha(package_path.read_bytes()),
        "prompt_sha256": _sha(shot.start_frame_prompt.encode()),
        "models": selected, "paid_calls_enabled": confirm_paid,
        "reference_assets": ref_records, "attempts": attempts,
        "results": rows, "cost_usd": "unavailable; inspect Fal billing dashboard",
        "output": str(out.expanduser().resolve()),
    }
    files["manifest.json"] = json.dumps(summary, indent=2, ensure_ascii=False).encode()
    files["index.html"] = _review_html(rows, ref_records).encode()
    files["SHA256SUMS"] = "".join(f"{_sha(data)}  {name}\n"
                                for name, data in sorted(files.items())
                                if name != "SHA256SUMS").encode()
    _write_zip(out.expanduser(), files)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare Fal-hosted image editors on one frozen shot")
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--asset-manifest", type=Path, required=True)
    parser.add_argument("--asset-root", type=Path, required=True)
    parser.add_argument("--shot-id", default="awakening")
    parser.add_argument("--models", default=",".join(DEFAULT_MODELS))
    parser.add_argument("--out", type=Path, help="ZIP output; defaults to ~/Downloads")
    parser.add_argument("--confirm-paid", action="store_true",
                        help="Actually run one metered Fal request per selected model")
    args = parser.parse_args(argv)
    selected = parse_models(args.models)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_shot = re.sub(r"[^A-Za-z0-9_-]", "-", args.shot_id)
    target = args.out or (Path.home() / "Downloads" / f"{safe_shot}-model-compare-{stamp}.zip")
    result = compare(args.package, args.asset_manifest, args.asset_root, target,
                     shot_id=args.shot_id, models=selected, confirm_paid=args.confirm_paid)
    print(json.dumps({"zip": result["output"], "paid": result["paid_calls_enabled"],
                      "models": result["models"],
                      "succeeded": sum(x["status"] == "success" for x in result["attempts"]),
                      "failed": sum(x["status"] == "failed" for x in result["attempts"])}, indent=2))
    return 0 if all(x["status"] == "success" for x in result["attempts"]) else 1


if __name__ == "__main__":
    sys.exit(main())
