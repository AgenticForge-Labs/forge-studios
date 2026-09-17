from __future__ import annotations

from pathlib import Path
from typing import Any

from .contracts import AssetRecord, EpisodePackage


def discover_asset_sources(package_path: str | Path) -> tuple[Path | None, Path | None]:
    """Discover the sibling Forge Born manifest and Forge Assets root."""
    package = Path(package_path).expanduser().resolve()
    forge_born = package.parent.parent
    manifest = forge_born / 'assets' / 'forge-born.yaml'
    asset_root = forge_born.parent / 'forge-assets'
    return (manifest if manifest.is_file() else None, asset_root if asset_root.is_dir() else None)


def bind_missing_references(package: EpisodePackage, *, manifest_path: str | Path, asset_root: str | Path) -> list[AssetRecord]:
    """Bind missing shot references from the stable manifest into the package."""
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError('Install Forge Studios with YAML support to resolve asset manifests') from exc
    raw: Any = yaml.safe_load(Path(manifest_path).read_text())
    if not isinstance(raw, dict) or raw.get('kind') != 'asset_manifest':
        raise ValueError(f'asset manifest must have kind=asset_manifest: {manifest_path}')
    records = {str(item.get('asset_id')): item for item in raw.get('assets', []) if isinstance(item, dict) and item.get('asset_id')}
    referenced = {asset_id for shot in package.shots for asset_id in shot.reference_asset_ids}
    existing = {asset.asset_id for asset in package.assets}
    bound: list[AssetRecord] = []
    root = Path(asset_root).expanduser().resolve()
    for asset_id in sorted(referenced - existing):
        record = records.get(asset_id)
        if record is None:
            continue
        storage_key = record.get('storage_key')
        if not isinstance(storage_key, str):
            raise ValueError(f'asset manifest record {asset_id!r} has no storage_key')
        key = Path(storage_key)
        if key.is_absolute() or '..' in key.parts:
            raise ValueError(f'unsafe asset storage_key for {asset_id!r}: {storage_key!r}')
        local_path = root / key
        if not local_path.is_file():
            raise FileNotFoundError(f'asset {asset_id!r} resolves to missing file: {local_path}')
        values = dict(record)
        values['uri'] = str(local_path)
        values.setdefault('metadata', {})
        values['metadata'] = {**values['metadata'], 'manifest_path': str(Path(manifest_path).resolve()), 'storage_key': storage_key}
        asset = AssetRecord.model_validate(values)
        package.assets.append(asset)
        bound.append(asset)
    return bound
