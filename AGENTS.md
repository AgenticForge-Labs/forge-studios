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
├── beats[]
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
- Keep candidate/attempt history.
- Require explicit approval by default.
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

## Frame policy

The current contract supports:

- `start_only`: independent shot with one authored start frame;
- `start_and_end`: authored start and destination frames.

Endpoint inheritance is explicit when present. Do not infer chaining from adjacency.

## Filmmaker

Filmmaker consumes approved media and assembles deterministically. Editorial projections
must remain reproducible from package + selected assets + edit settings.

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

## Telemetry / Researcher boundary

Collect operational facts only: production/episode/shot/attempt/asset IDs, prompts,
references, provider/model/options, reviews, outputs, latency/cost, and render lineage.
Private optimization logic and audience datasets belong in Forge Researcher.

## Runtime notes

Use `--mode cheap` for routine boundary generation unless the user explicitly chooses
the expensive profile. Current cheap defaults are `fal-ai/flux-2/flash` for images and
`fal-ai/ltx-2.3-22b/distilled` for video.

The CLI flag is authoritative; do not assume an exported environment variable selected
cheap mode. Confirm the run log before spending credits.

Never commit credentials or local secret files.
