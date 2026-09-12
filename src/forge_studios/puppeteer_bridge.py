from __future__ import annotations
import json, shlex, subprocess
from pathlib import Path
from .contracts import EpisodePackage, AssetRecord
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
