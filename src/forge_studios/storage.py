"""Verified R2 backup and explicit public publication for Forge Born media."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
from pathlib import Path
from typing import Any
from uuid import uuid4


PRIVATE_BUCKET = 'agenticforge-media-backup'
PUBLIC_BUCKET = 'agenticforge-public-assets'


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _key(value: str, *, prefix: str | None = None) -> str:
    path = Path(value)
    if not value or path.is_absolute() or any(part in {'.', '..'} for part in path.parts) or '\\' in value:
        raise ValueError(f'unsafe storage key: {value!r}')
    key = path.as_posix()
    if prefix and not key.startswith(prefix):
        raise ValueError(f'{key!r} must begin with {prefix!r}')
    return key


def _source(root: Path, key: str) -> Path:
    path = (root / _key(key)).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise FileNotFoundError(path)
    return path


def _write_json_atomic(path: str | Path, value: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f'.{target.name}.{uuid4().hex}.tmp')
    try:
        temporary.write_text(json.dumps(value, indent=2) + '\n')
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def r2_client(client: Any | None = None) -> Any:
    if client is not None:
        return client
    endpoint = os.environ.get('ASSET_R2_ENDPOINT_URL') or os.environ.get('R2_ENDPOINT_URL')
    access = os.environ.get('ASSET_R2_ACCESS_KEY_ID') or os.environ.get('R2_ACCESS_KEY_ID')
    secret = os.environ.get('ASSET_R2_SECRET_ACCESS_KEY') or os.environ.get('R2_SECRET_ACCESS_KEY')
    if not endpoint or not access or not secret:
        raise RuntimeError('set ASSET_R2_ENDPOINT_URL, ASSET_R2_ACCESS_KEY_ID, and ASSET_R2_SECRET_ACCESS_KEY')
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError("install R2 support: pip install 'forge-studios[r2]'") from exc
    return boto3.client('s3', endpoint_url=endpoint, aws_access_key_id=access,
                        aws_secret_access_key=secret, region_name='auto')


def _head(client: Any, bucket: str, key: str) -> dict | None:
    try:
        return client.head_object(Bucket=bucket, Key=key)
    except Exception as exc:
        response = getattr(exc, 'response', {})
        code = str(response.get('Error', {}).get('Code', ''))
        if code in {'404', 'NoSuchKey', 'NotFound'}:
            return None
        raise


def _upload_verified(client: Any, bucket: str, key: str, source: Path, digest: str,
                     *, replace_existing: bool = False) -> str:
    size = source.stat().st_size
    existing = _head(client, bucket, key)
    if existing is not None:
        if existing.get('ContentLength') == size and existing.get('Metadata', {}).get('sha256') == digest:
            return 'already_verified'
        if not replace_existing:
            raise ValueError(f'R2 key exists with different or unverified content: {bucket}/{key}')
    content_type = mimetypes.guess_type(source.name)[0] or 'application/octet-stream'
    client.upload_file(str(source), bucket, key,
                       ExtraArgs={'ContentType': content_type, 'Metadata': {'sha256': digest}})
    remote = _head(client, bucket, key)
    if remote is None or remote.get('ContentLength') != size or remote.get('Metadata', {}).get('sha256') != digest:
        raise RuntimeError(f'R2 verification failed: {bucket}/{key}')
    return 'replaced' if existing is not None else 'uploaded'


def backup_media(manifest_path: str | Path, asset_root: str | Path, receipt_path: str | Path,
                 *, client: Any | None = None) -> dict:
    manifest = json.loads(Path(manifest_path).read_text())
    if manifest.get('record_type') != 'production_run' or manifest.get('private_bucket') != PRIVATE_BUCKET:
        raise ValueError('expected a production_run manifest for the private R2 bucket')
    media = manifest.get('media')
    if not isinstance(media, list):
        raise ValueError('manifest media must be a list')
    if any(item.get('backup_status') == 'unresolved' for item in media):
        raise ValueError('localize remote-only provider media before backup')
    root = Path(asset_root).resolve()
    client = r2_client(client)
    objects = []
    seen: dict[str, str] = {}
    for item in media:
        key = _key(item['storage_key'])
        remote_key = _key(item['private_r2_key'])
        if key != remote_key:
            raise ValueError(f'private R2 key must match local storage key: {key}')
        source = _source(root, key)
        digest = sha256_file(source)
        if digest != item['sha256'] or source.stat().st_size != item['size']:
            raise ValueError(f'local media changed since capture: {key}')
        if key in seen:
            if seen[key] != digest:
                raise ValueError(f'conflicting digest for {key}')
            continue
        seen[key] = digest
        _upload_verified(client, PRIVATE_BUCKET, key, source, digest)
        objects.append({'storage_key': key, 'sha256': digest, 'size': item['size'], 'verified': True})
    receipt = {'record_type': 'media_backup_receipt', 'bucket': PRIVATE_BUCKET,
               'show_id': manifest['show_id'], 'episode_id': manifest['episode_id'],
               'run_id': manifest['run_id'], 'package_sha256': manifest['package_sha256'],
               'objects': objects}
    _write_json_atomic(receipt_path, receipt)
    return receipt


def publish_release(release_path: str | Path, asset_root: str | Path, receipt_path: str | Path,
                    *, client: Any | None = None) -> dict:
    """Publish only explicitly approved website files; never publish the package."""
    release = json.loads(Path(release_path).read_text())
    if release.get('record_type') != 'episode_release' or release.get('publication_status') != 'approved':
        raise ValueError('release must have publication_status=approved')
    approval = release.get('publication_approval') or {}
    if approval.get('decision') != 'approve' or not approval.get('reviewer'):
        raise ValueError('release lacks explicit publication approval')
    if approval.get('episode_id') != release.get('episode_id') or approval.get('run_id') != release.get('run_id'):
        raise ValueError('publication approval does not match release')
    if not release.get('package_sha256') or not release.get('private_backup_receipt') or not release.get('reconciliation'):
        raise ValueError('release lacks package, backup, or reconciliation evidence')
    if not release.get('publication_approval_sha256'):
        raise ValueError('release lacks publication approval hash')
    files = release.get('public_files')
    if not isinstance(files, list) or not files:
        raise ValueError('approved release needs public_files')
    root = Path(asset_root).resolve()
    client = r2_client(client)
    objects = []
    seen_public_keys: set[str] = set()
    for item in files:
        source_key = _key(item['storage_key'])
        public_key = _key(item['public_key'], prefix='website/forgeborn/')
        if public_key in seen_public_keys:
            raise ValueError(f'duplicate public R2 key: {public_key}')
        seen_public_keys.add(public_key)
        source = _source(root, source_key)
        digest = sha256_file(source)
        if digest != item['sha256']:
            raise ValueError(f'public file changed since release approval: {source_key}')
        result = _upload_verified(client, PUBLIC_BUCKET, public_key, source, digest,
                                  replace_existing=item.get('replace_existing') is True)
        objects.append({'public_key': public_key, 'sha256': digest, 'size': source.stat().st_size,
                        'result': result, 'url': f'https://assets.agenticforgelabs.com/{public_key}'})
    receipt = {'record_type': 'publication_receipt', 'bucket': PUBLIC_BUCKET,
               'episode_id': release['episode_id'], 'run_id': release['run_id'],
               'release_sha256': sha256_file(Path(release_path)),
               'publication_approval_sha256': release['publication_approval_sha256'],
               'objects': objects}
    _write_json_atomic(receipt_path, receipt)
    return receipt
