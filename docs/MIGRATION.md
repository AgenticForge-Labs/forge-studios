# Forge Studios migration matrix

This is the consolidation checklist for `robo-director`, `robo-animator`, and `robo-filmmaker`. Physical Stage/robot behavior moves to Forge Puppeteer.

| Legacy source | Destination | Status | Notes |
|---|---|---|---|
| Director ProductionPackage execution concept | `director.py`, `contracts.py` | transferred/adapted | EpisodePackage is the master sequence and public boundary. |
| Director autonomous episode pipeline | `DirectorService` + `AutonomyPolicy` | transferred/adapted | Progressive per-capability autonomy; stops at review/permission boundaries by default. |
| Director generic action loop | current work-item planner + explicit provider/Puppeteer calls | simplified/replaced | Avoid one generic action abstraction when concrete production primitives are clearer. |
| Director storyboard/human gates | package candidate lists + approval/rejection + storyboard HTML | transferred | Review remains first-class state. |
| Director context/provenance concepts | EpisodePackage trace + telemetry + asset lineage | transferred/adapted | No second context truth. |
| Animator media request/provider abstraction | `providers/base.py`, `animator/service.py` | transferred | Same provider-neutral primitive used manually and by Director. |
| Animator fal provider | `providers/fal.py` | transferred/adapted | Local file upload, explicit start/end fields, protected local credentials. |
| Animator mock provider | `providers/mock.py` | transferred | CI/manual plumbing without spend. |
| Animator OpenRouter image provider | `providers/openrouter.py` | transferred/adapted | Runtime-injected; public Studios does not depend on a private agent repo. |
| Animator continuity extraction | `continuity.py` | transferred/adapted | First/last ffmpeg frames are derived assets with lineage; distinct from pre-render frame plan. |
| Animator lore bridge | stable EpisodePackage asset/reference IDs | replaced | World canon ownership stays outside execution runtime. |
| Animator package adapter | direct EpisodePackage service | retired | Adapter is unnecessary at the new contract boundary. |
| Animator storyboard review | `storyboard.py`, CLI approve/reject | transferred/adapted | Static HTML is a projection; package remains source of truth. |
| Filmmaker deterministic media selection | `filmmaker.py` | transferred | Works for Animator/Puppeteer media. |
| Filmmaker edit-plan projection | `filmmaker.py::write_edit_plan` | transferred | Reproducible package projection. |
| `robo-filmmaker/effects.py` | `effects.py` | transferred | Declarative MLT filter specs. |
| `robo-filmmaker/timeline.py` | `timeline.py` | transferred | Optional OTIO timeline support. |
| package-to-timeline adapter | `timeline_adapter.py` | transferred/adapted | Reads EpisodePackage and current edit intent. |
| `robo-filmmaker/mlt_backend.py` | `mlt_backend.py` | transferred | Optional system MLT backend; fails loudly on unsupported constructs. |
| still pan/zoom renderer | `motion.py` | transferred | Optional visual dependency. |
| credit card renderer | `credits.py` | transferred | Optional visual dependency. |
| transition/audio/music metadata | EpisodePackage edit intent + OTIO metadata | transferred as intent | Execution of richer multi-track audio/transitions is backend capability; unsupported operations must not be silently approximated. |
| old physical Studio execution | Forge Puppeteer | moved | No robot/camera SDK imports in Studios. |

## New behavior added during consolidation

- Separate role-specific storyboard/start/end/video prompts.
- Local canonical reference upload at provider execution time.
- Persistent structured human review notes/tags/scores.
- Generation asset records include prompt/reference/model/options and shot-feature snapshot.
- Director differentiates candidate generation from review/approval.
- Explicit permissions for paid generated video and physical execution.
- `extract-boundaries`, OTIO `timeline`, and optional `render-mlt` CLI paths.

## Acceptance invariants

- `scenes[].shots[]` is the only narrative sequence.
- Manual and agentic paths call the same functions.
- Regeneration appends attempts; it does not erase candidate history.
- Provider/hardware details do not leak into world/story contracts.
- Filmmaker consumes approved media independent of origin.
- Public telemetry exposes operational facts but no private Researcher optimization data.
