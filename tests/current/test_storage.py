import hashlib
import json
from pathlib import Path

import pytest

from forge_studios.storage import (
    PRIVATE_BUCKET,
    PUBLIC_BUCKET,
    backup_media,
    publish_release,
)


class FakeR2:
    def __init__(self):
        self.objects = {}

    def head_object(self, *, Bucket, Key):
        item = self.objects.get((Bucket, Key))
        if item is None:
            error = RuntimeError("not found")
            error.response = {"Error": {"Code": "404"}}
            raise error
        return {
            "ContentLength": item["size"],
            "Metadata": dict(item["metadata"]),
        }

    def upload_file(self, filename, bucket, key, ExtraArgs):
        path = Path(filename)
        self.objects[(bucket, key)] = {
            "size": path.stat().st_size,
            "metadata": dict(ExtraArgs.get("Metadata") or {}),
            "content": path.read_bytes(),
        }


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest(tmp_path: Path, *, unresolved=False):
    asset_root = tmp_path / "assets"
    source = asset_root / "forge-born/episodes/e1/run-1/candidates/frame.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"frame")
    item = {
        "asset_id": "frame-1",
        "kind": "start_frame",
        "status": "approved",
    }
    if unresolved:
        item.update({
            "remote_uri": "https://provider.example/frame.png",
            "backup_status": "unresolved",
        })
    else:
        key = source.relative_to(asset_root).as_posix()
        item.update({
            "storage_key": key,
            "private_r2_key": key,
            "sha256": _digest(source),
            "size": source.stat().st_size,
        })
    manifest = {
        "record_type": "production_run",
        "private_bucket": PRIVATE_BUCKET,
        "show_id": "forge-born",
        "episode_id": "e1",
        "run_id": "run-1",
        "package_sha256": "pkg",
        "media": [item],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    return manifest_path, asset_root, source


def test_backup_uploads_and_verifies_manifest_bytes(tmp_path):
    manifest, root, source = _manifest(tmp_path)
    client = FakeR2()
    receipt_path = tmp_path / "receipt.json"

    receipt = backup_media(manifest, root, receipt_path, client=client)

    key = source.relative_to(root).as_posix()
    assert receipt["objects"] == [{
        "storage_key": key,
        "sha256": _digest(source),
        "size": source.stat().st_size,
        "verified": True,
    }]
    assert client.objects[(PRIVATE_BUCKET, key)]["metadata"]["sha256"] == _digest(source)
    assert json.loads(receipt_path.read_text()) == receipt


def test_backup_rejects_unresolved_provider_media(tmp_path):
    manifest, root, _ = _manifest(tmp_path, unresolved=True)
    with pytest.raises(ValueError, match="localize remote-only"):
        backup_media(manifest, root, tmp_path / "receipt.json", client=FakeR2())


def test_backup_rejects_local_media_changed_after_capture(tmp_path):
    manifest, root, source = _manifest(tmp_path)
    source.write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed since capture"):
        backup_media(manifest, root, tmp_path / "receipt.json", client=FakeR2())


def _release(tmp_path: Path, *, replace_existing=False, include_approval_hash=True):
    root = tmp_path / "assets"
    source = root / "website/forgeborn/video.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"video")
    release = {
        "record_type": "episode_release",
        "publication_status": "approved",
        "episode_id": "e1",
        "run_id": "run-1",
        "package_sha256": "pkg",
        "private_backup_receipt": "media-backup.json",
        "reconciliation": {"batch_id": "batch-1"},
        "publication_approval": {
            "decision": "approve",
            "reviewer": "human",
            "episode_id": "e1",
            "run_id": "run-1",
        },
        "public_files": [{
            "storage_key": source.relative_to(root).as_posix(),
            "public_key": "website/forgeborn/releases/e1.mp4",
            "sha256": _digest(source),
            "replace_existing": replace_existing,
        }],
    }
    if include_approval_hash:
        release["publication_approval_sha256"] = "approval-hash"
    path = tmp_path / "release.json"
    path.write_text(json.dumps(release))
    return path, root, source


def test_publish_receipt_hashes_release_and_links_approval(tmp_path):
    release, root, _ = _release(tmp_path)
    receipt_path = tmp_path / "publication.json"
    receipt = publish_release(release, root, receipt_path, client=FakeR2())

    assert receipt["release_sha256"] == _digest(release)
    assert receipt["publication_approval_sha256"] == "approval-hash"
    assert receipt["objects"][0]["public_key"] == "website/forgeborn/releases/e1.mp4"


def test_publish_requires_publication_approval_hash(tmp_path):
    release, root, _ = _release(tmp_path, include_approval_hash=False)
    with pytest.raises(ValueError, match="approval hash"):
        publish_release(release, root, tmp_path / "publication.json", client=FakeR2())


def test_publish_refuses_unapproved_replacement(tmp_path):
    release, root, _ = _release(tmp_path, replace_existing=False)
    client = FakeR2()
    client.objects[(PUBLIC_BUCKET, "website/forgeborn/releases/e1.mp4")] = {
        "size": 3,
        "metadata": {"sha256": "different"},
        "content": b"old",
    }
    with pytest.raises(ValueError, match="exists with different"):
        publish_release(release, root, tmp_path / "publication.json", client=client)


def test_publish_allows_explicitly_approved_replacement(tmp_path):
    release, root, source = _release(tmp_path, replace_existing=True)
    client = FakeR2()
    key = "website/forgeborn/releases/e1.mp4"
    client.objects[(PUBLIC_BUCKET, key)] = {
        "size": 3,
        "metadata": {"sha256": "different"},
        "content": b"old",
    }
    receipt = publish_release(release, root, tmp_path / "publication.json", client=client)

    assert receipt["objects"][0]["result"] == "replaced"
    assert client.objects[(PUBLIC_BUCKET, key)]["content"] == source.read_bytes()


def test_publish_rejects_path_traversal_key(tmp_path):
    release, root, _ = _release(tmp_path)
    data = json.loads(release.read_text())
    data["public_files"][0]["public_key"] = "website/forgeborn/../secret.mp4"
    release.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="unsafe storage key"):
        publish_release(release, root, tmp_path / "publication.json", client=FakeR2())
