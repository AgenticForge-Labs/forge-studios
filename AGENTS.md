# Agent / Codex instructions

Forge Studios executes one current, unversioned `EpisodePackage`. It owns Director,
Animator, production asset/provenance handling, and Filmmaker. It does not choose an LLM,
invent world canon, or silently rewrite story/prompt intent.

Historical scene-based packages and numbered EpisodePackage generations are retired.
Do not add compatibility adapters for them. If an old generated package fails to load,
regenerate it with current Forge Worlds.

## Current contract

```text
EpisodePackage
├── shots[]
└── assets[]
```

`package.shots` is the master narrative/production order. There is no active
`scenes[]` layer.

The current public package marker is exactly:

```text
episode_package
```

## Manual mode — default

Work one production shot at a time:

```text
inspect shot
→ inspect references/reference_uses
→ generate required boundary candidate
→ review
→ approve/reject
→ generate video when allowed
→ continue
```

Rules:

- Start with `forge-studios plan` and `show-shot`.
- Preserve package order and shot intent.
- Stable asset IDs are identity; filenames/provider URLs are transport.
- Generate required boundary images before paid video.
- Keep candidate/attempt history while it remains useful for review.
- Treat storyboard projections and unapproved candidates as draft-local evidence, not
  accepted production or required private-backup material.
- Require explicit approval by default. Approved package media and final masters become
  accepted production and are backup-required.
- Use mock/cheap paths when testing plumbing.
- Never use an LLM to "fix" a prompt inside Studios.

## Director

Director answers what execution work is required next, not what the story should become.
Storyboard HTML, work plans, timelines, and edit plans are derived projections only.

Execution may be reordered for dependency/cost reasons, but final assembly preserves
`package.shots` narrative order.

## Animator

Animator owns synthetic-media acquisition and provider compilation.

- Provider-specific fields stay in adapters.
- Preserve the shot's authored start/end/video prompts.
- Preserve `visual_constraints`.
- Preserve ordered reference assets and `reference_uses`.
- A supporting-site reference is secondary evidence; never promote it to primary
  composition merely because it is canonical.
- Persist attempt, provider/model/options, input references, outputs, errors, latency,
  and cost when available.
- Generated media are candidates until approved.

## Package execution boundary

Forge Studios does not compose, rewrite, expand, summarize, or semantically augment
shot prompts. The `EpisodePackage` is already the authored execution contract.

For every media role:

- send `start_frame_prompt`, `end_frame_prompt`, or `video_prompt` exactly as supplied;
- preserve `reference_asset_ids` order;
- resolve asset IDs to provider-accessible files/URLs;
- map approved start/end boundary assets into provider transport fields;
- apply explicit provider/model/options settings;
- record attempts, provenance, diagnostics, cost, and outputs.

Do not append camera prose, visual constraints, reference-role explanations, audio
policy, fallback prompt text, or any other semantic content inside Studios. If a
required package prompt is missing, fail loudly and send the correction upstream to
Forge Worlds rather than inventing one.

Provider adapters may translate transport/schema details required by an API, including
field names, uploaded asset URLs, or syntax-only reference aliases. They must not rewrite,
sanitize, paraphrase, append, or otherwise change creative meaning. Provider-specific
content-policy wording must be fixed upstream in Forge Worlds, not silently rewritten in
Studios.

Current Worlds frame prompts use ordered lowercase `@imageN` aliases. Preserve their
ordering and meaning. A provider adapter may translate only the documented alias syntax,
such as lowercase FLUX `@imageN` to Kling `@ImageN`, while preserving the rest of the
prompt exactly. Provider profiles may
enforce hard API limits such as maximum reference count or prompt characters before a
paid request, but must fail rather than truncate or rewrite the authored prompt.


## Portable storyboard review

`storyboard-export` is an offline, non-authoritative projection of the
existing package plus frame candidates, canonical references, exact prompts,
provider evidence, checksums and partial-failure diagnostics. It must never
generate, auto-approve, rewrite canon, or become the resume/approval source.
See `docs/STORYBOARD_REVIEW_EXPORT.md` for the guarded foreground runner,
one-shot paid smoke, and ZIP review workflow.

## Registered image-only storyboard refresh

The Episode 1 foreground production runner selects `openai/gpt-image-2/edit`
via the existing Fal-backed AI Runtime. Only the explicit
`forge-studios storyboard --generate --provider fal --image-edit-model
openai/gpt-image-2/edit --refresh-model-candidates` path refreshes existing
non-approved frames by model while reusing already registered matching local
candidates. Never delete earlier FLUX attempts, silently alter authored
prompts or auto-approve the new results. It checkpoints each new candidate in
the source package and retains it for human storyboard review. This is
not video generation, and the development Runtime preflight and explicit
`full --confirm-paid` budget gate are mandatory in the foreground runner.
The normal-mode global default is now GPT Image 2 for both text-only and reference-conditioned generation; the explicit `cheap` mode remains FLUX Flash.
See `docs/STORYBOARD_REVIEW_EXPORT.md` for operator commands.

## Experimental image-provider comparisons

`scripts/run_image_model_comparison.sh` and `image_compare.py` compare
the **identical, authored** shot prompt and ordered canonical references
across Fal-hosted image-edit models. They use Platform AI Runtime through the
existing isolated-dev runtime preflight. No separate OpenAI/Google keys, no
system pip installation, no video generation, no auto-approval, and no writes
back to the production EpisodePackage. One paid call per selected model
requires `--confirm-paid`; an offline dry-run exports the intended request
matrix and original references. Review outputs are non-authoritative ZIPs.
See `docs/STORYBOARD_REVIEW_EXPORT.md` for usage and licensing/budget notes.

## Frame policy

The current contract treats incoming continuity and outgoing frame constraints as
independent axes:

- `start_only`: this shot does not author its own destination frame;
- `start_and_end`: this shot authors a destination frame;
- `inherits_start_from_shot_id`: when present, this shot begins from the exact approved
  end frame of its immediate predecessor, regardless of its own frame-plan mode.

An inheriting shot therefore may be `start_only`: the predecessor supplies its exact
start, while the shot remains free at its own end. The predecessor must be
`start_and_end` so an authored endpoint exists. Endpoint inheritance is always explicit;
never infer chaining from adjacency.

Public `cinematic_choices` are semantic vocabulary selections authored upstream. Studios
preserves them for inspection/provenance but executes the concrete frame-plan and prompt
fields; it does not reinterpret cinematic vocabulary.

## Filmmaker

Filmmaker consumes approved media and assembles deterministically. Editorial projections
must remain reproducible from package + selected assets + edit settings.

Show-specific finishing intent stays outside generic Studios code. When a show-owned
`show/finishing.yaml` is present, Filmmaker may execute its deterministic end-card,
music-provider, and mix settings after picture lock. Logo references use stable asset IDs
resolved through the existing asset manifest/root; never hard-code a show's filenames in
Studios.

The default configured music route is style-only video-to-music: send the whole
picture-locked video plus the show's constant prompt. Manual reruns must use the same
primitive and may choose video-only inference, the show style plus optional episode
direction, or an exact custom prompt. Studios does not invent episode emotion or rewrite
music prompts. Sonilo/fal produces a music stem; Studios owns the final mix with the
generated dialogue/ambience already present in the picture lock.

Narrative shots remain hard-cut by default. A finishing profile may request a fade from
the final narrative shot into a branded end card. Do not introduce automatic dissolves
between ordinary shots unless they are explicitly authored later.

## Agentic mode

Agentic execution applies an explicit autonomy policy to the same primitives used
manually.

- Generation permission and approval permission are separate.
- Paid video requires explicit permission.
- Retries are bounded.
- Capability mismatch returns blocked state rather than silently degrading intent.
- Creative changes are escalated upstream to the human/Forge Worlds.

## Repository handoff discipline

This repository is frequently handed between ChatGPT review, Hermes local execution,
and human work. Before editing:

- read this file and README;
- inspect the active branch;
- treat `src/forge_studios/contracts.py` as the single current package schema;
- do not resurrect `Scene`, scene-package iteration, or old package markers from
  historical tests/docs;
- validate only current package fixtures.

Current contract tests live under `tests/current/`. Historical scene-package tests
should not be restored.

## Asset storage — where the bytes live

The manifest is `assets/forge-born.yaml` in the sibling `forge-born` repo. The actual
media bytes live in the sibling `forge-assets/` directory (the local Directus
stand-in), NOT inside `forge-born/assets/`:

- Canonical references remain under `<repo-root>/forge-assets/<storage_key>`.
  New generated frames and clips go directly under
  `<repo-root>/forge-assets/forge-born/episodes/<episode-id>/<run-id>/candidates/`.
  Historical generated files under `forge-born/generated/` remain historical media.
  Bytes are not Git-versioned.

Every CLI call that resolves or generates media must pass:

- `--asset-manifest <forge-born>/assets/forge-born.yaml`
- `--asset-root <repo-root>/forge-assets` — the directory that actually contains the
  `storage_key` files. Pointing this at `forge-born/assets/` will fail providers with
  `file_download_error` (FAL can't fetch a path that doesn't resolve), so double-check
  it before any paid generation. (See `forge-born/assets/README.md`.)

Never derive asset identity from a filename or provider URL; `logical_key` and
`asset_id` are the stable keys. Generated media are candidates until approved. Production
capture may inventory draft candidates with `retention: draft_local` and
`backup_required: false`; `backup-r2` must skip those while preserving the skip in its
receipt. Explicitly approved package assets and final masters are
`accepted_production` and backup-required.

## Shared evidence/event boundary

Forge Studios remains authoritative for generation attempts, review decisions, generated
asset lineage, render/edit events, and deterministic provider request construction.

The local `.agenticforge/telemetry.jsonl` remains the complete Studios event record.
When `AGENTICFORGE_EVIDENCE_TOKEN` is configured, the same event is mirrored
best-effort into Platform `af_events` with stable production lineage. Repeated prompt
strings are stored once as content-addressed `forge-studios/provider_prompt` evidence
and central events carry pointers instead of duplicated prompt bodies.

AI Runtime receives the same production/episode/shot/attempt lineage for fal execution,
so its provider request/response, usage, latency, request IDs, and cost can be joined to
Studios events without making Runtime the owner of production semantics.

Platform persistence failure must never invalidate a local Studios event or successful
provider execution. Forge Researcher consumes the shared records and may create derived
datasets/models, but does not rewrite Studios production truth.

## Telemetry / Researcher boundary

Collect operational facts only: production/episode/shot/attempt/asset IDs, prompts,
references, provider/model/options, reviews, outputs, latency/cost, and render lineage.
Private optimization logic and audience datasets belong in Forge Researcher.

## Shared AI Runtime boundary

All fal.ai execution, including Animator image/video generation and Filmmaker Sonilo
music generation, goes through AgenticForge AI Runtime by default.

- Studios owns provider-specific request fields because those fields encode deterministic
  production intent.
- Runtime owns fal credentials, authenticated provider transport, and temporary upload of
  explicitly supplied local assets.
- Studios sends exact authored prompts; Runtime must not rewrite or semantically augment
  them.
- Do not add a second direct `fal_client` path in Studios when the Runtime media contract
  covers the operation.
- Preserve resolved provider/model, request ID, raw provider result, and Studios
  production metadata in telemetry/provenance.
- General API Runtime callers may default to port 8090; **the Episode 1
  paid storyboard runner may not**. Its normal-profile paid test uses the
  isolated running `agenticforge-platform-dev` runtime (typically port
  18090) with verified effective development token, obtained in memory only.
  Never borrow the production runtime's token or edit live Platform secrets.

## Runtime notes

Provider generation mode is read from `FORGE_STUDIOS_MODE` by `FalProvider`.
The standalone `forge-studios generate` and `storyboard` subcommands do
**not** accept a `--mode` flag. `FORGE_STUDIOS_MODE=normal` is the global
default; it now selects `openai/gpt-image-2` for text-to-image and
`openai/gpt-image-2/edit` for reference-conditioned generation through Fal.
Both use `quality=high`, one output image, and target 1920×1080. The
explicit `cheap` mode still uses FLUX.2 Flash by design. Existing
`FAL_IMAGE_GENERATE_MODEL` and `FAL_IMAGE_EDIT_MODEL` overrides remain
independent and authoritative over profile defaults. Previous FLUX candidates
remain as provenance. The full Episode 1 runner may keep the explicit GPT
edit-model selection for a reproducible operator handoff. Video model
configuration is unchanged.
Before paid generation, `runtime_preflight` verifies the actual development
Platform container and its credentials without emitting tokens or modifying
services. Confirm model, image size, provider and cost in the attempt trace.

Never commit credentials or local secret files.
