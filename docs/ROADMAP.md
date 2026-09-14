# Forge Studios Roadmap

This roadmap prioritizes the smallest high-value production primitives first. The goal is not to reproduce a large interactive video application immediately; it is to make every production more useful than the last while preserving a clean path toward stronger continuity, automated review, and interactive tooling later.

## Guiding principle

**Collect the evidence before building the intelligence.**

Forge Studios should preserve enough structured data that the same storyboard or shot can be rendered through different models, providers, prompts, reference strategies, or settings and compared fairly. Human choices and production outcomes then become training/evaluation data for later routing, criticism, and optimization systems.

The `EpisodePackage` remains the production source of truth. Experimental variants should share the same shot/storyboard intent rather than silently changing narrative requirements between model comparisons.

## Now — simple, high-value primitives

### 1. Production experiment identity and complete telemetry

Extend the existing telemetry/provenance path so every generation attempt can be grouped into a controlled comparison.

Record at minimum:

- `experiment_id` — groups alternatives intended to answer the same production question.
- `variant_id` — identifies one candidate within the experiment.
- episode, scene, shot, and source storyboard/frame-plan IDs.
- provider and model.
- model/version identifier when available.
- prompt and role (`storyboard`, `start_frame`, `end_frame`, `video`, etc.).
- all reference asset IDs and their roles.
- generation parameters/options, including seed when available.
- parent/source asset lineage.
- output asset ID.
- latency, cost/usage when available, retries, and failure state.
- human approval/rejection decision, tags, rubric scores, notes, and selected winner.
- downstream outcome when known, such as whether a frame was actually used to produce an approved clip or final episode.

Do not require a new database or complex experiment service initially. The existing package records plus append-only JSONL telemetry are sufficient as long as stable IDs make runs joinable later.

### 2. Same-shot multi-model / multi-setting comparison

Add a small comparison primitive that can take one approved storyboard or shot definition and generate multiple candidates while holding creative intent constant.

Examples:

- same storyboard prompt, different image models;
- same approved start/end frames, different video models;
- same model, different reference-image sets;
- same model, different prompt strategies or provider options.

The first implementation only needs to:

1. create one `experiment_id`;
2. generate named variants from the same source shot/storyboard state;
3. preserve every candidate and its provenance;
4. allow a human to choose/rank candidates and record why;
5. mark the chosen asset without deleting the alternatives.

Prefer pairwise comparison or simple ranking over an elaborate scoring system at first. The important product is the dataset.

### 3. Deterministic reference resolution before an agentic resolver

Use information already present in `EpisodePackage` before adding an LLM-based reference-selection agent.

A simple resolver should gather and rank:

1. explicitly bound shot references;
2. approved canonical character/place/object assets for `entity_ids`;
3. `continuity_asset_ids`;
4. an approved predecessor frame when `frame_plan` chains from another shot;
5. manually supplied overrides.

Record exactly which references were supplied to each generation attempt. This creates the data needed to learn whether richer automatic reference selection is actually valuable.

## Next — use the collected data

### 4. Continuity graph

Generalize existing `frame_plan`, shot order, entity IDs, and continuity references into an explicit dependency/continuity graph.

Potential edge types include:

- temporal predecessor;
- frame-chain predecessor;
- camera/spatial parent;
- character continuity;
- place/background continuity;
- prop/object continuity.

The graph should primarily improve scheduling and reference selection. It should not become a second source of narrative truth.

### 5. Visual Critic / comparison assistant

Add automated evaluation only after human comparison data exists.

Start with the same dimensions already useful in human review:

- prompt/shot compliance;
- character identity;
- anatomy/physical constraints;
- character/object scale;
- location geometry;
- blocking and composition;
- cross-shot continuity;
- obvious generation defects.

The critic should initially **rank and explain**, not silently approve. Store critic scores and preferences beside human preferences so agreement, disagreement, and model-specific bias can be measured.

### 6. Dependency-aware parallel execution

Represent generation work as a small execution DAG so independent shots/variants can run concurrently while dependent video tasks wait for required approved frames/references.

This should reuse the same Director/Animator primitives rather than creating a separate pipeline.

## Later — learned production intelligence

### 7. Learned model/provider routing

Use accumulated experiments to predict which model/provider/settings are best for a particular shot type or objective, rather than globally declaring one model best.

Useful conditioning variables may include:

- still vs motion;
- number and type of characters;
- character interaction;
- camera motion;
- need for start/end frame adherence;
- reference count/type;
- scene complexity;
- target style;
- historical human preference;
- cost and latency constraints.

The desired result is a policy such as: *for this kind of shot, under this budget, try model A first; use model B for a second candidate when uncertainty is high.*

### 8. Preference/evaluation models and active experimentation

Once enough comparisons exist, Forge Researcher can test learned critics, preference models, and experiment-selection policies. The system should deliberately request informative comparisons where the current routing/critic is uncertain instead of generating redundant variants forever.

### 9. Interactive production workspace

A richer interactive workspace can later expose:

- agent conversation;
- storyboard and shot inspection;
- candidate comparison;
- approval/rejection;
- render progress;
- experiment history;
- model/provider performance summaries.

This is valuable productization, but it should sit on top of the same package, telemetry, experiment, and review primitives rather than becoming a second architecture.

## Not yet

Avoid prematurely building:

- a large autonomous reference-selection agent;
- an automatic final-approval agent;
- a complex learned router before comparison data exists;
- a separate experiment database when stable package/telemetry IDs suffice;
- a monolithic idea-to-video application that bypasses Forge Worlds or `EpisodePackage`.

The near-term objective is simpler: **make controlled variants easy, preserve everything needed to compare them, capture human preference, and keep the production contracts stable.**
