# Forge Studios

Forge Studios is the deterministic media-execution runtime for AgenticForge. It consumes
one current, unversioned `EpisodePackage` from Forge Worlds (or an equivalent
hand-authored producer) and executes its ordered generated-video shots without rewriting
story intent.

## Current package boundary

```text
EpisodePackage
├── beats[]
├── shots[]
└── assets[]
```

The package marker is:

```text
package_version = "episode_package"
```

There is no supported `scenes[]` package hierarchy and no compatibility path for
numbered EpisodePackage generations. Old generated packages should be regenerated
upstream.

Director uses `package.shots` as the master narrative/production order. Storyboard
HTML, work plans, timelines, and edit plans are derived projections, not alternate
sources of truth.

## Components

- **Director** determines what execution work is needed next.
- **Animator** creates start/end frame candidates and generated video using provider
  adapters.
- **Filmmaker** assembles approved media deterministically.
- **Telemetry** records attempts, provenance, review decisions, latency, and cost when
  available.

Forge Studios does not choose an LLM, invent canon, add story events, or repair creative
intent.

## Install

```bash
python -m pip install -e '.[dev]'
python -m pip install -e '.[fal,dev]'
python -m pip install -e '.[timeline,dev]'
python -m pip install -e '.[full,dev]'
```

## Validate and inspect

```bash
forge-studios validate episode-package.json
forge-studios plan --package episode-package.json
forge-studios show-shot --package episode-package.json --shot-id <shot_id>
```

The public shot contains the authored production intent: beat/site IDs, references,
reference-use instructions, frame policy, static boundary prompt(s), video prompt,
visual constraints, and media lifecycle bindings.

## Storyboard and media generation

Generate boundary candidates and a review page:

```bash
forge-studios storyboard   --package episode-package.json   --out storyboard.html   --generate   --provider fal   --mode cheap
```

The current default Worlds workflow is `start_only`: one static start frame grounds
the shot and the video prompt carries the full audiovisual performance. A shot may use
`start_and_end` when an explicit destination frame is useful.

Generate and approve production assets before expensive video:

```bash
forge-studios generate --package episode-package.json --shot-id <shot> --role start_frame --provider fal
forge-studios approve --package episode-package.json --shot-id <shot> --kind start_frame --asset-id <asset>
forge-studios generate --package episode-package.json --shot-id <shot> --role video --provider fal
```

For `start_and_end`, generate/approve the end frame before video as well.

Regeneration appends new attempts/assets; it does not erase candidate history.

## Reference semantics

Forge Worlds resolves world/view IDs to stable reference assets before handoff.
`reference_uses` tells Studios how each input should be interpreted. For example, a
supporting site view may be evidence for a distant landmark and must not replace the
primary scene composition.

Studios preserves these instructions when compiling provider requests. It does not infer
a new world view or reinterpret the selected site.

## Progressive autonomy

Manual and agentic execution call the same primitives. Director defaults to conservative
review gates; automated generation and automated approval are separate permissions.
Paid video remains explicitly gated.

```bash
forge-studios auto --package episode-package.json --provider fal
```

## Provider configuration

The built-in fal adapter supports local approved references and uploads them only when a
provider-accessible URL is required. Provider/model-specific field names stay inside
adapters rather than the EpisodePackage.

Use `--mode cheap` for routine iteration. Current cheap defaults are documented in
`AGENTS.md`.

Never commit provider keys, credentials, or local secret files.

## Filmmaker

```bash
forge-studios edit-plan --package episode-package.json --out edit-plan.json
forge-studios render --package episode-package.json --out episode.mp4
forge-studios timeline --package episode-package.json --out episode.otio --require-media
```

Timeline/edit outputs are reproducible projections of the package and selected approved
assets.

## Repository boundary

Forge Studios supersedes useful execution behavior from older Director/Animator/
Filmmaker repositories, but those historical package contracts are not supported here.
See `docs/ARCHITECTURE.md` and `AGENTS.md` for the current rules.
