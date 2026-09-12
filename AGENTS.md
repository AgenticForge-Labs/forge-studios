# Codex guidance

Forge Studios executes `episode_package_v1`; it does not invent world canon or silently rewrite story intent.

- Package order is the master narrative order. Execution may be parallel or dependency-driven.
- Keep functions usable directly and through the CLI; agent automation must invoke the same primitives.
- Generated video is downstream of approved storyboard/keyframes when the shot requests them.
- Respect `execution_route`: animator, puppeteer, or hybrid.
- Routine retry/provider selection may be automated; creative rewriting should be escalated to a human or Forge Worlds.
- Persist attempt, asset, approval, cost/latency and provenance telemetry from the beginning.
- Never commit credentials or `.env` files. Use environment variables or the user-only local config store.
