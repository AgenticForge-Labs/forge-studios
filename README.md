# Forge Studios

Open execution runtime for AgenticForge productions. Forge Studios consumes a versioned `episode_package_v1` and executes it through synthetic media, physical capture, or both.

**Director** decides what work is required. **Animator** makes storyboard/keyframe/video assets. **Filmmaker** assembles approved media. Physical shots are dispatched to the separately installable **Forge Puppeteer** backend.

The package is the master narrative/production sequence. Storyboard HTML, Director work plans, timeline projections, and edit plans are derived from that package rather than becoming separate sources of truth.

See [`docs/ROADMAP.md`](docs/ROADMAP.md) for the staged production roadmap, including controlled multi-model comparisons, data collection, continuity, automated criticism, and later learned routing.

## Install

```bash
python -m pip install -e '.[dev]'
python -m pip install -e '.[fal,dev]'       # fal.ai generation
python -m pip install -e '.[timeline,dev]'  # OpenTimelineIO projection
python -m pip install -e '.[full,dev]'      # all optional Python integrations
```

MLT rendering additionally requires a compatible system MLT installation. FFmpeg is used for deterministic rendering/boundary extraction where applicable.

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

Set/refine role-specific prompts without changing the rest of the episode:

```bash
forge-studios set-prompt --package episode.json --shot-id s01 --role storyboard --text 'Ember is asleep on the central altar. Preserve the supplied Ember and Fire Forge exactly.'
forge-studios set-prompt --package episode.json --shot-id s01 --role start_frame --text 'Exact starting composition...'
forge-studios set-prompt --package episode.json --shot-id s01 --role end_frame --text 'Exact destination composition...'
forge-studios set-prompt --package episode.json --shot-id s01 --role video --text 'Only the intended temporal change...'
```

Generate a cheap storyboard candidate first:

```bash
forge-studios generate --package episode.json --shot-id s01 --role storyboard --provider fal
forge-studios storyboard --package episode.json --out storyboard.html
```

After review, approve or reject while preserving structured review data:

```bash
forge-studios approve --package episode.json --shot-id s01 --kind storyboard --asset-id asset_... \
  --note 'Good scale and architecture' --tag continuity --score composition=5

forge-studios reject --package episode.json --shot-id s01 --asset-id asset_... \
  --reason 'Wrong anatomy' --tag anatomy --score anatomy=1
```

Regeneration is another `generate`; attempts accumulate with provenance rather than overwriting history.

For generated video, explicitly define the boundary policy and approve required keyframes before spending on motion generation:

```bash
forge-studios set-frame-plan --package episode.json --shot-id s02 --mode start_and_end
forge-studios generate --package episode.json --shot-id s02 --role start_frame --provider fal
forge-studios approve --package episode.json --shot-id s02 --kind start_frame --asset-id asset_...
forge-studios generate --package episode.json --shot-id s02 --role end_frame --provider fal
forge-studios approve --package episode.json --shot-id s02 --kind end_frame --asset-id asset_...
forge-studios generate --package episode.json --shot-id s02 --role video --provider fal
```

Post-generation boundary extraction is available separately and does not replace pre-approved boundary frames:

```bash
forge-studios extract-boundaries --package episode.json --shot-id s02 --asset-id asset_clip --out-dir outputs/boundaries
```

## Progressive agentic execution

Manual and agentic modes call the same primitives. By default Director is conservative and stops at review/permission boundaries:

```bash
forge-studios auto --package episode.json --provider fal
```

Capabilities are granted independently rather than through one unsafe global switch:

```bash
forge-studios auto --package episode.json --provider fal \
  --auto-approve-storyboards \
  --auto-approve-frames \
  --allow-video \
  --auto-approve-clips
```

Physical execution remains separately permissioned with `--allow-physical` and a configured Puppeteer command. See `AGENTS.md` for the manual/assisted/agentic policy Codex and other agents should follow.

## Media providers and keys

### fal.ai

Local reference images and approved start/end frames can remain filesystem paths in EpisodePackage. The fal adapter uploads a local file only when a fal request needs a provider-accessible URL.

Never commit keys:

```bash
forge-studios keys set fal
```

The adapter passes the stored key directly to the official fal client. Standard fal-client authentication (`FAL_KEY` or `fal auth login`) also remains compatible. `.env` files are ignored.

Provider/model-specific request field names stay outside the episode contract. Current fal configuration includes `FAL_IMAGE_MODEL`, `FAL_IMAGE_REFERENCE_FIELD`, `FAL_VIDEO_MODEL`, `FAL_VIDEO_START_FRAME_FIELD`, `FAL_VIDEO_END_FRAME_FIELD`, and optional `FAL_VIDEO_REFERENCE_FIELD`.

### OpenRouter image adapter

`OpenRouterImageProvider` is available as a library adapter for a runtime implementing `generate_images`. It is intentionally runtime-injected rather than coupling public Forge Studios to private Forge Worlds/business-runtime code. The CLI currently exposes the built-in mock and fal providers; a caller/Codex integration can instantiate the OpenRouter adapter directly.

## Physical route

Set `execution_route: puppeteer` or `hybrid` in a shot and configure the external executor:

```bash
export FORGE_PUPPETEER_CMD='forge-puppeteer'
forge-studios physical --package episode.json --shot-id physical_01 --out runs/physical_01.json
```

The resulting take is recorded back into the same package, so Filmmaker can consume approved media without caring whether it originated from Animator or Puppeteer.

## Filmmaker and timelines

Create a deterministic edit-plan projection or basic FFmpeg render:

```bash
forge-studios edit-plan --package episode.json --out edit-plan.json
forge-studios render --package episode.json --out episode.mp4
```

Project the EpisodePackage into an OpenTimelineIO timeline:

```bash
forge-studios timeline --package episode.json --out episode.otio --require-media
```

When MLT is installed, render the package-derived timeline through the transferred MLT backend:

```bash
forge-studios render-mlt --package episode.json --out episode.mp4 --profile atsc_1080p_30
```

Transferred Filmmaker modules also include declarative effects, still pan/zoom motion, credits, timeline conversion, and strict handling of unsupported backend constructs. Richer multi-track audio/transitions can continue to evolve without creating a second edit truth.

## Data / Forge Researcher

Every generation, approval/rejection, physical take, and final render emits JSONL telemetry to `.agenticforge/telemetry.jsonl` by default. Generation assets preserve prompt/reference/model/options plus relevant shot-design features and lineage. **Forge Researcher** can ingest those events and later combine them with publication outcomes and experiments.

## Legacy mapping

This repository supersedes the durable production behavior from `robo-director`, `robo-animator`, and `robo-filmmaker`. Physical Stage/robot behavior moved to Forge Puppeteer. See `docs/MIGRATION.md` for the transfer matrix and explicit disposition of adapted/retired behavior.
