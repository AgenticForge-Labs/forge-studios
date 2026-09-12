# Forge Studios architecture

Forge Studios is the public execution side of the AgenticForge production system. It consumes `episode_package_v1` and executes the ordered shot sequence without owning the fictional world or silently rewriting creative intent.

## Components

```text
EpisodePackage
      ↓
   Director
      ↓
 ┌───────────────┬──────────────────┐
 │ Animator      │ Forge Puppeteer  │
 │ synthetic     │ physical         │
 └───────────────┴──────────────────┘
      ↓
   Filmmaker
      ↓
 final production
```

Director owns execution planning, dependencies, routine retries, approvals according to explicit policy, and escalation. Animator owns synthetic still/keyframe/video execution. Forge Puppeteer is a separately installable physical backend. Filmmaker assembles approved media from either path.

## One package, many projections

The ordered `scenes[].shots[]` list is the master production/narrative sequence. Director's work list, storyboard HTML and Filmmaker edit plan are derived views of that package. They are not additional authored sources of truth.

Director is free to generate independent shots in another order later, but it must preserve package sequence for narrative assembly.

## Manual and automatic execution share primitives

The core operations are intentionally callable as Python functions and CLI commands:

- inspect/edit a shot
- register/bind a canonical reference
- set a role-specific prompt
- generate storyboard candidate(s)
- approve/reject and attach structured review data
- generate explicit start/end frames
- generate video
- dispatch a physical shot
- render a storyboard or final edit

Director calls the same functions. `auto` simply applies an `AutonomyPolicy` to those primitives.

Default Director behavior is conservative: it can create a cheap storyboard candidate and then stops at human review. Expensive video or physical execution requires explicit permission. Auto-approval is opt-in separately for storyboards, frames, clips and physical takes.

## Shot execution routes

`execution_route`:
- `animator`
- `puppeteer`
- `hybrid`

`render_strategy`:
- `still`
- `still_motion`
- `generated_video`
- `physical`
- `hybrid`

An episode can mix Animator and physical shots freely. A true same-shot hybrid/composite can collect both synthetic and physical assets, but advanced compositing remains a Filmmaker extension; the first renderer is deliberately deterministic and simple.

## Boundary-frame control

`frame_plan` is semantic intent:
- `still`
- `start_only`
- `start_and_end`
- `chained_start`

Start/end frames are approved assets *before* expensive video when the plan requires them. This differs from extracting first/last frames *after* a clip. Provider field names remain adapter configuration rather than EpisodePackage fields.

The fal adapter supports local canonical references: a local path remains in the package until a fal request needs it, then fal-client uploads the file and supplies the provider URL. This keeps storage concerns out of story contracts.

## Review data is product data

Every generated asset keeps its generation provenance: attempt ID, role, prompt, provider/model, provider options, reference IDs, start/end IDs and a snapshot of relevant shot design features.

Approvals/rejections may also capture:
- free-text note/reason
- structured tags such as `scale`, `anatomy`, `continuity`, `composition`
- numeric scores such as `continuity=5`

These reviews are persisted on the asset and emitted to telemetry. They are useful both for production debugging and later Forge Researcher learning.

## Telemetry boundary

Public Forge Studios records operational facts, not proprietary optimization logic. JSONL events provide enough lineage for private Researcher ingestion:

- generation start/success/failure
- generated asset lineage
- provider/model/options
- shot design snapshot
- human/automatic approval or rejection
- physical take
- final render
- latency/cost when available

Audience metrics, experiments, causal analysis and learned policy remain in Forge Researcher.

## Secrets

Secrets never belong in EpisodePackage, telemetry or Git. Forge Studios can store a fal key in the user-only AgenticForge credential file and passes it directly to `fal_client.SyncClient(key=...)`. Standard fal-client `FAL_KEY` or `fal auth login` also remains compatible.

## Physical boundary

Forge Studios does not import robot SDKs. It emits a semantic `forge_puppeteer_request_v1` to the external Forge Puppeteer command/service and receives a `forge_puppeteer_take_v1` result. Hardware calibration, cameras, motors and safety enforcement belong behind the Puppeteer/Stage boundary.
