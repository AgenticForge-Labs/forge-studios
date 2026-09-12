# Forge Studios

Open execution runtime for AgenticForge productions. Forge Studios consumes a versioned `episode_package_v1` and executes it through synthetic media, physical capture, or both.

**Director** decides what work is required. **Animator** makes storyboard/keyframe/video assets. **Filmmaker** assembles approved media. Physical shots are dispatched to the separately installable **Forge Puppeteer** backend.

The package is the master narrative/production sequence. Storyboard HTML, Director work plans, and edit plans are projections of that package, not separate sources of truth.

## Install

```bash
python -m pip install -e '.[dev]'
# Optional fal.ai support
python -m pip install -e '.[fal,dev]'
```

## Manual-first workflow

The intended development loop is exactly the pipeline used later by Director: inspect one shot, bind references, edit its prompt/frame plan, generate candidates, approve/reject, then continue.

```bash
forge-studios validate episode.json
forge-studios plan --package episode.json
forge-studios show-shot --package episode.json --shot-id s01
```

Register existing canonical images once, then attach the stable IDs to any shot that needs them:

```bash
forge-studios asset-add --package episode.json --asset-id character_ember --uri ~/assets/ember.png
forge-studios asset-add --package episode.json --asset-id place_fire_forge --uri ~/assets/fire-forge.png
forge-studios reference add --package episode.json --shot-id s01 --asset-id character_ember
forge-studios reference add --package episode.json --shot-id s01 --asset-id place_fire_forge
```

Set or refine the shot image prompt without changing the rest of the episode:

```bash
forge-studios set-prompt --package episode.json --shot-id s01 --role image --text 'Ember is asleep on the central altar. Preserve the supplied Ember and Fire Forge exactly; Ember is small relative to the altar and ruin.'
```

Generate a cheap candidate first:

```bash
forge-studios generate --package episode.json --shot-id s01 --role storyboard --provider fal
forge-studios storyboard --package episode.json --out storyboard.html
```

After reviewing the returned candidate asset ID:

```bash
forge-studios approve --package episode.json --shot-id s01 --kind storyboard --asset-id asset_...
# or
forge-studios reject --package episode.json --shot-id s01 --asset-id asset_...
```

Regeneration is simply another `generate`; candidates accumulate with provenance instead of overwriting prior attempts.

For a generated-video shot, set the boundary-frame policy and generate/approve the required stills before spending on video:

```bash
forge-studios set-frame-plan --package episode.json --shot-id s02 --mode start_and_end
forge-studios generate --package episode.json --shot-id s02 --role start_frame --provider fal
forge-studios approve --package episode.json --shot-id s02 --kind start_frame --asset-id asset_...
forge-studios generate --package episode.json --shot-id s02 --role end_frame --provider fal
forge-studios approve --package episode.json --shot-id s02 --kind end_frame --asset-id asset_...
forge-studios set-prompt --package episode.json --shot-id s02 --role video --text 'Ember slowly opens his eyes and slightly lifts his head. No jumping or turning. The Forge remains dormant.'
forge-studios generate --package episode.json --shot-id s02 --role video --provider fal
```

Nothing about this path is special to humans: the CLI calls the same functions Director can call automatically later. `forge-studios plan --package episode.json` shows which primitive Director thinks should happen next.

## fal.ai references and keys

Local reference images and approved start/end frames may stay as local filesystem paths in the EpisodePackage. The fal provider uploads them with `fal_client.upload_file()` only when a fal job needs them.

Never commit keys. You can store the fal key for Forge Studios in the protected per-user AgenticForge configuration:

```bash
forge-studios keys set fal
```

The fal adapter passes that key directly to the official `fal_client.SyncClient`; it is not written into the EpisodePackage or telemetry. If no Forge-local key is stored, normal fal-client authentication (`FAL_KEY` or `fal auth login`) still works. `.env` files are ignored.

Model and endpoint-specific field names stay outside the episode contract. Current environment configuration includes `FAL_IMAGE_MODEL`, `FAL_IMAGE_REFERENCE_FIELD`, `FAL_VIDEO_MODEL`, `FAL_VIDEO_START_FRAME_FIELD`, `FAL_VIDEO_END_FRAME_FIELD`, and optional `FAL_VIDEO_REFERENCE_FIELD`.

## Physical route

Set `execution_route: puppeteer` or `hybrid` in a shot and configure the external executor:

```bash
export FORGE_PUPPETEER_CMD='forge-puppeteer'
forge-studios physical --package episode.json --shot-id physical_01 --out runs/physical_01.json
```

The resulting take is recorded back into the same package, so Filmmaker does not care whether media came from Animator or Puppeteer.

## Filmmaker

Once each shot has approved media:

```bash
forge-studios edit-plan --package episode.json --out edit-plan.json
forge-studios render --package episode.json --out episode.mp4
```

The first renderer is intentionally deterministic and simple. More advanced transitions, audio, MLT integration and music workflows can be migrated from the earlier Filmmaker implementation as real productions require them.

## Data / Researcher

Every generation, approval/rejection, physical take, and final render emits JSONL telemetry to `.agenticforge/telemetry.jsonl` by default. It includes stable production/episode/shot/attempt/asset IDs plus provider/model/options, outcomes, errors, latency and provenance. **Forge Researcher** can ingest this later without exposing its private analysis logic here.

## Legacy mapping

This repo supersedes the active concepts from `robo-director`, `robo-animator`, and `robo-filmmaker`. The legacy repositories remain useful historical references until any provider/editor functionality worth keeping has been ported, after which they can be archived.
