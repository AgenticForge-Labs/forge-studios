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

## Shared evidence and research lineage

Studios always writes its complete local JSONL telemetry. If
`AGENTICFORGE_EVIDENCE_TOKEN` is configured, it also mirrors those authoritative
production events to AgenticForge Platform. Central events retain production/episode/
shot/attempt/asset lineage but replace repeated prompt bodies with SHA-256-addressed
evidence references.

fal image/video calls and episode-finishing Sonilo calls carry the same production trace
through AI Runtime. Runtime therefore records provider execution facts while Studios
continues to own the deterministic media request and production outcome.

Evidence mirroring is best-effort and is not a production gate. Default Platform rights
permit internal research while training/redistribution/resale remain review-required
until explicitly approved.

## Install

```bash
python -m pip install -e '.[dev]'
python -m pip install -e '.[timeline,dev]'
python -m pip install -e '.[full,dev]'
```

## Validate and inspect

```bash
forge-studios validate episode-package.json
forge-studios plan --package episode-package.json
forge-studios show-shot --package episode-package.json --shot-id <shot_id>
```

The public shot contains the authored production intent: shot/site IDs, references,
reference-use instructions, frame policy, static boundary prompt(s), video prompt,
visual constraints, and media lifecycle bindings.

## Storyboard and media generation

Generate boundary candidates and a review page:

```bash
forge-studios storyboard   --package episode-package.json   --out storyboard.html   --generate   --provider fal   --mode cheap
```

The current default Worlds workflow is `start_only`: one static start frame grounds
the shot and the video prompt carries the full audiovisual performance. A shot may use
`start_and_end` when an explicit destination frame is useful. Incoming continuity is
separate: `inherits_start_from_shot_id` reuses the immediate predecessor's exact approved
end frame as this shot's start. The inheriting shot may still be `start_only` when its
own destination should remain unconstrained.

Shots may also carry upstream `cinematic_choices` vocabulary selections for human review
and provenance. Studios does not reinterpret those semantic choices; it executes their
compiled frame-plan and prompts.

Generate and approve production assets before expensive video. Generated media goes directly under the sibling `forge-assets/forge-born/episodes/<episode-id>/<run-id>/candidates/` when the package is stored in Forge Born; `--asset-root` or `--output-dir` can select another local destination. Final renders default to that run's `masters/` directory, and `--out` can override it:

```bash
forge-studios generate --package episode-package.json --shot-id <shot> --role start_frame --provider fal
forge-studios approve --package episode-package.json --shot-id <shot> --kind start_frame --asset-id <asset>
forge-studios generate --package episode-package.json --shot-id <shot> --role video --provider fal
```

For `start_and_end`, generate/approve the end frame before video as well.

Regeneration appends new attempts/assets; it does not erase candidate history during
active work. Storyboard HTML/review projections and unapproved candidate frames/clips are
draft review evidence, not accepted production. They may be kept locally while useful
and are not required for private R2 backup. Once a frame/clip is explicitly approved
into the production package, it becomes accepted production media and is backup-required.

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

Forge Studios uses the shared AgenticForge AI Runtime as its default provider transport.
Start Platform first:

```bash
cd ../agenticforge-platform
make restart
```

Studios defaults to `AI_RUNTIME_URL=http://127.0.0.1:8090` and
`AI_RUNTIME_TOKEN=local-development-token`. The Platform Runtime owns the fal.ai
credential. Studios owns exact provider-field construction, model/profile selection,
prompt fidelity, reference ordering, boundary-media binding, result localization, and
production provenance.

Local approved references and picture-lock videos are sent to Runtime as explicit local
asset inputs; Runtime uploads them to fal.ai and substitutes only those transport
references. Runtime must not rewrite prompts or infer production intent.

Use `--mode cheap` for routine iteration. Current cheap defaults are documented in
`AGENTS.md`.

Never commit Runtime tokens, provider keys, credentials, or local secret files.

## Filmmaker

```bash
forge-studios edit-plan --package episode-package.json --out edit-plan.json
forge-studios render --package episode-package.json --out episode.mp4
forge-studios timeline --package episode-package.json --out episode.otio --require-media
```

`render` always preserves `package.shots` order and generated clip audio. When it can
auto-discover a show-owned `show/finishing.yaml`, it also renders that profile's branded
end card, saves an unscored `episode-picture-lock.mp4`, generates a Sonilo music stem
through fal, and creates the final deterministic audio mix. A show can therefore keep its
logos, score prompt, and mix defaults outside the generic Studios runtime.

The music profile default is normally `style`: send the complete picture-locked video
plus the show's constant style prompt. The same picture lock can be rerun without changing
the edit:

```bash
# Let Sonilo infer the music from the video with no text prompt.
forge-studios score-music --package episode-package.json \
  --video episode-picture-lock.mp4 --out episode-video-only.mp4 --music-mode video

# Reuse the show style and add light episode-level semantic direction.
forge-studios score-music --package episode-package.json \
  --video episode-picture-lock.mp4 --out episode-directed.mp4 --music-mode style \
  --music-direction "The red dragon approaches the rune stone mysteriously; placing the stone becomes wondrous and larger."

# Replace the show style with an exact manual prompt.
forge-studios score-music --package episode-package.json \
  --video episode-picture-lock.mp4 --out episode-custom.mp4 --music-mode custom \
  --music-prompt "..."
```

Use `--music-mode none` on `render` to stop at picture lock without making a paid music
request. Sonilo returns a music stem; Studios, not the provider, remains authoritative for
the final dialogue/ambience + music mix. Timeline/edit outputs remain reproducible
projections of the package and selected approved assets.

## Cloudflare storage

Forge Born Git holds the final package and checksummed `media-manifest.json`. Studios
writes frames, clips, and default final renders to the sibling `forge-assets/` tree.
After the final capture, back up every inventoried item marked
`backup_required: true` to private R2. Draft storyboard/candidate media are retained in
the manifest for provenance but skipped:

```bash
forge-studios backup-r2 \
  --manifest ../forge-born/productions/forge-born/EPISODE/RUN/media-manifest.json \
  --asset-root ../forge-assets \
  --receipt ../forge-born/productions/forge-born/EPISODE/RUN/media-backup.json
```

The R2 commands require `pip install 'forge-studios[r2]'` and locally configured
`ASSET_R2_ENDPOINT_URL`, `ASSET_R2_ACCESS_KEY_ID`, and
`ASSET_R2_SECRET_ACCESS_KEY`. Use bucket-scoped credentials; do not commit them.
The backup receipt records both verified uploaded objects and draft items intentionally
skipped because `backup_required` is false. The backup command never deletes or
silently replaces an existing R2 object.

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
