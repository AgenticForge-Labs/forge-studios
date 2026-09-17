# Agent / Codex instructions

Forge Studios executes `episode_package_v2`. It owns Director, Animator,
production asset/provenance handling, and Filmmaker. It does **not** select an
LLM, invent world canon, or silently rewrite story/prompt intent.

Manual and autonomous production must call the same functions. Do not create a parallel agent-only implementation.

## Operating modes

### Manual mode — default

Use manual mode while learning a workflow, refining production behavior, or whenever the user has not explicitly authorized autonomous execution.

Work **one shot and one primitive at a time**:

```text
inspect shot
→ inspect/bind canonical references
→ edit a start/end/video prompt if needed
→ generate candidate
→ review
→ approve/reject with structured feedback
→ continue to next required primitive
```

Rules:

- Start with `forge-studios plan` and `show-shot`; do not regenerate the whole episode by default.
- Preserve package order as the master narrative order.
- Register stable semantic asset IDs and bind them to shots; do not treat filenames/provider URLs as identity.
- Generate the actual start/end boundary candidates before expensive video.
- Resolve and approve both required boundary images before video.
- Keep candidate history. Regeneration appends a new attempt/asset rather than overwriting prior attempts.
- Require explicit human approval for candidates by default.
- Capture review notes/tags/scores whenever they are useful (`anatomy`, `scale`, `continuity`, `architecture`, `composition`, `motion`, etc.).
- Do not run paid video generation merely to test plumbing if the mock provider is sufficient.
- Every new manual operation that proves useful should become a plain reusable function + CLI command + test before agent orchestration is added.

### Agentic mode

Agentic execution is Director applying an explicit `AutonomyPolicy` to the same primitives.

Rules:

- Conservative defaults are intentional. Director should stop at review/permission boundaries unless policy grants authority.
- Auto-generation and auto-approval are separate permissions.
- Expensive generated video requires `allow_generated_video`/CLI `--allow-video`.
- Frame and clip approvals are separate policy switches.
- Bound action counts/retries. Never allow an unbounded generate/reject/regenerate loop.
- Provider retry/fallback, queue handling, media download, format normalization, and deterministic assembly may be automated.
- Creative rewriting, changing a beat, changing the intended final composition, inventing missing canon, or contradicting locked references must be escalated to the human/Forge Worlds.
- If a provider cannot satisfy an explicitly required capability, return a structured blocked/capability mismatch rather than degrading intent silently.

## Director rule

Director answers **what execution work is required next**, not **what the story should become**.

Derived work plans, storyboard HTML, edit plans, telemetry views, and execution DAGs are projections of EpisodePackage. They are never separate narrative sources of truth.

Execution may later be parallelized or reordered for dependencies/cost, but Filmmaker preserves package narrative order.

## Animator rules

- Animator owns synthetic-media acquisition: exact start/end frames, generated clips, compositing source assets, and boundary extraction.
- Provider-specific request fields belong in adapters.
- Local reference files may be uploaded by the provider adapter at execution time; the package may retain storage-neutral/local URIs.
- Persist generation attempt ID, prompt, references, provider/model/options, source assets, latency, errors, and outputs.
- Treat stochastic generation as a candidate process, never as canon authority.

## Filmmaker rules

- Filmmaker accepts approved assets/takes regardless of synthetic or physical origin.
- Timeline/edit operations must remain deterministic and reproducible from package + selected assets + edit settings.
- Preserve trim, transition, still-motion, audio/music/title/caption/compositing provenance.
- Prefer a simple deterministic backend before adding model-driven editorial decisions.

## Current media boundary

The current v2 contract is generated-video only. Future still or physical
extensions must be versioned explicitly and must not leak route selection back
into the creative LLM context.

## Telemetry and Researcher boundary

Collect operational facts in the public execution layer:

- stable production/episode/scene/shot/attempt/asset/take/render IDs,
- prompts/references/options/model/provider,
- success/failure/retry/latency/cost when available,
- human/automatic reviews and selections,
- final render lineage.

Do not move private audience datasets, learned priors, proprietary experiment results, or optimization logic into this repo. Forge Researcher consumes the public telemetry.

## Migration / legacy transfer

This repository supersedes durable functionality from `robo-director`, `robo-animator`, and `robo-filmmaker`. Physical functionality from `robo-studio`/`robo-puppeteer` belongs in Forge Puppeteer, not here.

When transferring legacy functionality:

- port behavior and tests, not old package boundaries;
- preserve useful provider adapters, continuity helpers, storyboard review, edit/timeline/effects/audio behavior, and Director operational logic;
- replace `ProductionPackage` compatibility at the boundary with `EpisodePackage` while preserving adapters where needed for old fixtures;
- add regression tests before marking a legacy feature transferred;
- update the migration matrix for every completed/declined legacy module;
- do not archive a legacy repo until all useful behavior has a tested destination or is explicitly documented as intentionally retired.

## Secrets

Never commit credentials, tokens, `.env`, provider secrets, or user-local config. Provider secrets must not appear in EpisodePackage or telemetry.
