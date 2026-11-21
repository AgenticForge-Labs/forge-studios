from __future__ import annotations

import asyncio
import json
import shlex
import subprocess
from pathlib import Path

from .contracts import AssetRecord, EpisodePackage
from .telemetry import TelemetrySink


def request_for_shot(package: EpisodePackage, shot_id: str) -> dict:
    shot=package.find_shot(shot_id)
    return {'contract_version':'forge_puppeteer_request_v1','production_id':package.production_id,'episode_id':package.episode_id,'shot_id':shot_id,'duration_seconds':shot.duration_seconds,'visual':shot.visual,'camera':shot.camera,'performance_intent':shot.performance_intent,'edit_intent':shot.edit_intent,'entity_ids':shot.entity_ids}

def dispatch(package: EpisodePackage, shot_id: str, *, command: str, output: str|Path, telemetry: TelemetrySink|None=None) -> AssetRecord:
    req=Path(str(output)+'.request.json'); req.parent.mkdir(parents=True,exist_ok=True); req.write_text(json.dumps(request_for_shot(package,shot_id),indent=2)+'\n')
    subprocess.run(shlex.split(command)+['execute','--request',str(req),'--out',str(output)],check=True)
    result=json.loads(Path(output).read_text())
    asset=AssetRecord(kind='physical_take',uri=result['uri'],status='candidate',episode_id=package.episode_id,shot_id=shot_id,provider='forge-puppeteer',metadata=result)
    package.assets.append(asset); package.find_shot(shot_id).physical_take_ids.append(asset.asset_id)
    (telemetry or TelemetrySink()).emit('physical_take.completed',production_id=package.production_id,episode_id=package.episode_id,shot_id=shot_id,asset_id=asset.asset_id,executor='forge-puppeteer')
    return asset


def dispatch_behavior_pack(
    package: EpisodePackage,
    shot_id: str,
    *,
    server_name: str,
    server_config: dict,
    behavior_packs_path: str | Path,
    behavior_pack_id: str,
    behavior_pack_revision: str,
    take_id: str,
    telemetry: TelemetrySink | None = None,
) -> AssetRecord:
    """Execute an operator-resolved, revision-pinned Puppeteer behavior pack.

    The pack ID, revision, server name and MCP subprocess configuration must
    come from operator-authored integration configuration. Shot prose is never
    compiled into device actions. MCPClient receives argv as structured data
    and owns the configured MCP lifecycle; no shell is involved.
    """
    try:
        from forge_puppeteer.mcp.client import MCPClient, ServerConfig
    except ImportError as exc:
        raise RuntimeError(
            "This opt-in route requires Forge Puppeteer with MCP support. Install "
            "'forge-puppeteer[mcp]' in the same Python environment as Forge Studios."
        ) from exc

    if not server_name or not behavior_pack_id or not behavior_pack_revision or not take_id:
        raise ValueError("server, behavior pack ID/revision, and take ID are required")
    config = ServerConfig.model_validate(server_config)
    client = MCPClient(
        {server_name: config}, behavior_packs_path=behavior_packs_path
    )
    plan = client.compile_behavior_pack(
        behavior_pack_id, behavior_pack_revision, take_id, server=server_name
    )
    try:
        results = asyncio.run(client.execute(plan))
    except RuntimeError as exc:
        # MCPClient itself imports the optional `mcp` runtime lazily, so a base
        # forge-puppeteer install can import and compile packs but fail here.
        if "Install forge-puppeteer[mcp]" in str(exc):
            raise RuntimeError(
                "Forge Puppeteer MCP support is missing. Install "
                "'forge-puppeteer[mcp]' in the same Python environment as Forge Studios."
            ) from exc
        raise
    result = {
        "contract_version": "forge_puppeteer_take_v1",
        "take_id": take_id,
        "server": server_name,
        "behavior_pack_id": behavior_pack_id,
        "behavior_pack_revision": behavior_pack_revision,
        "results": list(results),
    }
    asset = AssetRecord(
        kind="physical_take",
        uri=f"forge-puppeteer://take/{take_id}",
        status="candidate",
        episode_id=package.episode_id,
        shot_id=shot_id,
        provider="forge-puppeteer-mcp",
        metadata=result,
    )
    package.assets.append(asset)
    (telemetry or TelemetrySink()).emit(
        "physical_take.completed",
        production_id=package.production_id,
        episode_id=package.episode_id,
        shot_id=shot_id,
        asset_id=asset.asset_id,
        executor="forge-puppeteer-mcp",
        take_id=take_id,
        behavior_pack_id=behavior_pack_id,
        behavior_pack_revision=behavior_pack_revision,
    )
    return asset
