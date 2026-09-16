# Static boundary camera contract (episode_package_v1 extension)

Worlds owns static composition and motion intent. Studios compiles it without an LLM.

`shot.camera.start_frame` and `shot.camera.end_frame` optionally hold complete
static camera objects (composition, angle, distance, focus, axis). Shot-wide
`camera.movement` describes motion for video, not either image. The existing
extensible camera schema and semantic fingerprint include these optional objects.

When supplied, select the requested static object and the shared viewpoint. Do
not merge a shot-wide tracking/jump composition into it. Without static objects,
use the endpoint prompt and shared viewpoint/axis only; never invent a destination.
Both validators reject a non-object boundary camera or temporal movement within it.

Image requests carry camera, constraints and ordered reference roles once. An end
image's start reference establishes identity/geometry/state continuity but must
not freeze the source pose/crop against an explicitly authored destination.
This does not relax exact inherited video-start binding or human approval gates.

These changes were motivated by the actual Forge Born v9 guide: an ostensibly
grounded start was supplied with “capturing the jump and landing in full motion”
as its shared camera. Another POV request required eyes while forbidding the
owner's face. The latter is a Worlds design contradiction, not a reason to add
creative rewriting to Studios. See Worlds `docs/V9_PIPELINE_REVIEW.md` for evidence.

No paid generation was used to test this compiler change. Provider-free tests
cover static selection, temporal exclusion, legacy fallback, rejection of invalid
boundary cameras, actual reference ordering and unchanged video approval gates.
