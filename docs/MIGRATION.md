# Legacy consolidation

Forge Studios contains the useful execution behavior consolidated from older Director,
Animator, and Filmmaker repositories. That history does not define an alternate package
contract.

## Current rule

Only the unversioned `EpisodePackage` in `src/forge_studios/contracts.py` is supported.
It contains ordered `beats[]`, `shots[]`, and `assets[]`.

Historical `ProductionPackage`, scene-based EpisodePackage, numbered EpisodePackage
generations, and their compatibility tests/adapters are retired. Old generated files are
regenerated upstream rather than migrated inside Studios.

## Durable capabilities retained

- deterministic Director work planning;
- provider-neutral media requests;
- fal and mock provider adapters;
- approved reference handling;
- start/end boundary generation;
- candidate review and provenance;
- deterministic Filmmaker/edit/timeline output;
- telemetry for later Forge Researcher ingestion.

Physical robot/stage behavior remains outside this repository in Forge Puppeteer.

## Acceptance invariants

- `package.shots` is the only active narrative sequence;
- manual and agentic execution call the same primitives;
- regeneration appends attempts;
- provider details do not leak upstream into world/story contracts;
- Filmmaker consumes approved media independent of provider;
- no compatibility path silently accepts retired scene-package schemas.
