# Boundary-frame execution

Forge Worlds owns static composition and temporal story intent. Forge Studios executes
that intent without an LLM.

The current shot contract supports two frame modes:

- `start_only`: generate/approve a static start frame, then generate video from it.
- `start_and_end`: generate/approve both static boundaries, then generate video between
  them when the provider supports the required inputs.

Boundary prompts contain visible static composition only. Camera movement, action,
dialogue, ambience, and synchronized effects belong in `video_prompt`.

Approved references and `reference_uses` remain authoritative when compiling image
requests. An approved start frame used to create an end frame provides continuity
evidence, but should not erase an explicitly authored destination composition.

Endpoint inheritance is explicit in the current contract and is never inferred merely
because shots are adjacent.

Provider-specific request fields remain adapter details.
