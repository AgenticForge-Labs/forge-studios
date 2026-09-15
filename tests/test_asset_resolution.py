import sys
from types import SimpleNamespace

from forge_studios.asset_resolution import bind_missing_references, discover_asset_sources
from forge_studios.contracts import EpisodePackage, Scene, Shot


def test_bind_missing_manifest_reference_into_package(tmp_path, monkeypatch):
    asset_root = tmp_path / 'forge-assets'
    asset_root.mkdir()
    (asset_root / 'place_rune_stone.png').write_bytes(b'png')
    manifest = tmp_path / 'forge-born.yaml'
    manifest.write_text('asset manifest')
    monkeypatch.setitem(sys.modules, 'yaml', SimpleNamespace(safe_load=lambda _: {
        'kind': 'asset_manifest',
        'assets': [{
            'asset_id': 'asset_rune_stone_establishing',
            'kind': 'reference_image',
            'role': 'canonical-establishing',
            'status': 'canon',
            'authority': 'locked',
            'storage_key': 'place_rune_stone.png',
            'uri': 'asset://asset_rune_stone_establishing',
        }],
    }))
    package = EpisodePackage(production_id='p', episode_id='e', title='x', scenes=[Scene(scene_id='s', shots=[
        Shot(shot_id='sh', duration_seconds=1, visual='x', continuity_asset_ids=['asset_rune_stone_establishing'])
    ])])
    bound = bind_missing_references(package, manifest_path=manifest, asset_root=asset_root)
    assert [asset.asset_id for asset in bound] == ['asset_rune_stone_establishing']
    assert bound[0].uri == str((asset_root / 'place_rune_stone.png').resolve())


def test_discover_asset_sources_uses_forge_born_sibling_layout(tmp_path):
    forge_born = tmp_path / 'forge-born'
    package_dir = forge_born / 'episodes'
    (forge_born / 'assets').mkdir(parents=True)
    (forge_born / 'assets' / 'forge-born.yaml').write_text('x')
    (tmp_path / 'forge-assets').mkdir()
    manifest, asset_root = discover_asset_sources(package_dir / 'episode.json')
    assert manifest == forge_born / 'assets' / 'forge-born.yaml'
    assert asset_root == tmp_path / 'forge-assets'
