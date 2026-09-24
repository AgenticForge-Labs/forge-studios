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

Generate and approve production assets before expensive video. Generated media goes directly under the sibling `forge-assets/forge-born/episodes/<episode-id>/<run-id>/candidates/` when the package is stored in Forge Born; `--asset-root` or `--output-dir` can select another local destination. Final renders default to that run's `masters/` directory, and `--out` can override it:

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

## Cloudflare storage

Forge Born Git holds the final package and checksummed `media-manifest.json`. Studios
writes frames, clips, and default final renders to the sibling `forge-assets/` tree.
After the final capture, back up every inventoried file to private R2:

```bash
forge-studios backup-r2 \
  --manifest ../forge-born/productions/forge-born/EPISODE/RUN/media-manifest.json \
  --asset-root ../forge-assets \
  --receipt ../forge-born/productions/forge-born/EPISODE/RUN/media-backup.json
```

The R2 commands require `pip install 'forge-studios[r2]'` and locally configured
`ASSET_R2_ENDPOINT_URL`, `ASSET_R2_ACCESS_KEY_ID`, and
`ASSET_R2_SECRET_ACCESS_KEY`. Use bucket-scoped credentials; do not commit them.
The backup command never deletes or silently replaces an existing R2 object.

After Worlds creates a reviewed `release.json`, publish only its named website
files and keep the resulting receipt in Forge Born Git:

```bash
forge-studios publish-r2 \
  --release ../forge-born/productions/forge-born/EPISODE/RUN/release.json \
  --asset-root ../forge-assets \
  --receipt ../forge-born/productions/forge-born/EPISODE/RUN/publication.json
```

Public overwrites require the exact key in the release's publication approval.

## Repository boundary

Forge Studios supersedes useful execution behavior from older Director/Animator/
Filmmaker repositories, but those historical package contracts are not supported here.
See `docs/ARCHITECTURE.md` and `AGENTS.md` for the current rules.
