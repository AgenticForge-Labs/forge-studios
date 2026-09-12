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

```bash
forge-studios validate episode.json
forge-studios plan --package episode.json
forge-studios generate --package episode.json --shot-id s01 --role storyboard --provider mock
forge-studios storyboard --package episode.json --out storyboard.html
```

After reviewing a candidate asset, approve it by stable ID:

```bash
forge-studios approve --package episode.json --shot-id s01 --kind storyboard --asset-id asset_...
```

For a generated-video shot, generate and approve explicit boundary frames when its `frame_plan` requests them:

```bash
forge-studios generate --package episode.json --shot-id s01 --role start_frame --provider fal
forge-studios approve --package episode.json --shot-id s01 --kind start_frame --asset-id asset_...
forge-studios generate --package episode.json --shot-id s01 --role end_frame --provider fal
forge-studios approve --package episode.json --shot-id s01 --kind end_frame --asset-id asset_...
forge-studios generate --package episode.json --shot-id s01 --role video --provider fal
```

Nothing about this path is special to humans: the CLI calls the same functions Director can call automatically later.

## Keys and local configuration

Never commit keys. Provider secrets can come from environment variables such as `FAL_KEY`; `forge-studios keys set <name>` also stores a value in the user-only `~/.config/agenticforge/credentials.json` file with restrictive permissions. Provider adapters can progressively use that shared store as they are hardened. `.env` files are ignored.

## Physical route

Set `execution_route: puppeteer` or `hybrid` in a shot and configure the external executor:

```bash
export FORGE_PUPPETEER_CMD='forge-puppeteer'
forge-studios physical --package episode.json --shot-id physical_01 --out runs/physical_01.json
```

The resulting take is recorded back into the same package, so Filmmaker does not care whether media came from Animator or Puppeteer.

## Data / Researcher

Every generation, approval/rejection, physical take, and final render emits JSONL telemetry to `.agenticforge/telemetry.jsonl` by default. It includes stable production/episode/shot/attempt/asset IDs plus provider/model/options, outcomes, errors, latency and provenance. **Forge Researcher** can ingest this later without exposing its private analysis logic here.

## Legacy mapping

This repo supersedes the active concepts from `robo-director`, `robo-animator`, and `robo-filmmaker`. Those repositories can remain historical archives after this consolidation is accepted.
