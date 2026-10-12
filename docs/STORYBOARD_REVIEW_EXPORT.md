# Portable storyboard review: one ZIP, no parallel production authority

The current `EpisodePackage` from Forge Worlds is the authoritative authored
execution contract. Studios executes existing `start_frame_prompt` and optional
`end_frame_prompt`; it does not invent extra keyframe semantics, modify canon,
generate prompts, or automatically approve candidates.

## Implementation status

`forge-studios storyboard-export` is a no-inference, no-approval ZIP projection.
It reuses the existing `build_storyboard` and candidate asset records.
The foreground runner exports both complete and partial work so a failed frame
does not erase completed candidates. This is separate from video generation.

Review files include:

- `episode-package.json`: valid, portable review copy of the current
  EpisodePackage, with bundled asset URIs. This copy is **not** the production
  file to resume or approve from. Keep the original on your workstation.
- `storyboard.html` and `storyboard.image-guide.html`, with relative local
  image paths that work inside the extracted ZIP.
- `images/`: exact original raster bytes for selected opening/closing frames
  and referenced canonical assets, not compressed or resized substitutes.
- `contact-sheet.svg`: always present, embeds all available frames with
  sequence labels. `contact-sheet.png` is also present when Pillow is
  installed (`python -m pip install Pillow`).
- `manifest.json`: ordered shots, exact authored frame/video prompts,
  reference roles, selected media, unresolved/missing asset ledger, optional
  supplied Git SHAs, and per-file SHA-256 values.
- `SHA256SUMS`: conventional checksum listing for all other ZIP members.
- `generation-events.json` when matching production telemetry exists.
  Full attempt provenance, exact provider options, durations, latency, cost
  and recorded diagnostics are also retained in the review package trace.
- `execution-log.txt` if supplied; known credential-bearing lines and URLs
  with query strings are redacted. Do not supply arbitrary sensitive logs.

Generated reference and candidate media use existing asset IDs and metadata.
The archive is explicitly a **review projection**, not another canon or
approved-asset manifest. Media missing locally or available only at remote URLs
is identified as missing; the export never fetches arbitrary URLs.

## Prepare your current Episode 1 inputs

Use the **latest validated** EpisodePackage from your local production run,
not any old versioned fixtures in `forge-born/episodes`. The
`episode1-review-episode1-20261012-002311.zip` cited in the production handoff
is not stored in the Studios repository; locate the real local package before
attempting inference. Do not re-run Forge Worlds to start storyboarding.

From your cloned workspace:

```bash
cd ~/code/forge-studios
uv sync --extra dev
source .venv/bin/activate

PACKAGE=/absolute/path/to/current/episode-package.json
MANIFEST=~/code/forge-born/assets/forge-born.yaml
ASSET_ROOT=~/code/forge-assets

# Offline only: package existing images (or export missing-image diagnostics).
bash scripts/run_storyboard_review.sh "$PACKAGE" "$MANIFEST" "$ASSET_ROOT" export

# Deliberately paid: generate just one first-shot image through existing fal/AI Runtime.
# Run only after verifying credentials/provider/profile/pricing.
bash scripts/run_storyboard_review.sh "$PACKAGE" "$MANIFEST" "$ASSET_ROOT" smoke
```

The `smoke` operation uses the current `--mode cheap` image provider profile.
Current Studios source defaults use FLUX.2 Flash/Edit via fal/AI Runtime, **not**
Modal credits or an implemented Modal/Vast.ai/Runpod adapter. Before any paid
experiment, confirm the served provider, specific checkpoint license for
commercial use, accepted reference count, dimensions, price, and budget
externally. A $30 Modal credit allowance does not automatically cover fal usage.

### Commercial-weight model choice

The current `--mode cheap` Fal profile uses `fal-ai/flux-2/flash` /
`fal-ai/flux-2/flash/edit`. Fal offers API commercial use, but these are
FLUX.2 **[dev]** models: the downloadable model weights have a
non-commercial license, which is **not** suitable for unrestricted commercial
self-hosted inference. See:
https://fal.ai/models/fal-ai/flux-2/flash/api and
https://help.bfl.ai/articles/9272590838-self-serve-dev-license-overview-pricing .

The official BFL `FLUX.2 [klein] 4B` and `4B Base` weights are Apache-2.0
and allow commercial self-hosted use; 9B and dev weights do not share that
license. See https://github.com/black-forest-labs/flux2 and
https://bfl.ai/models/flux-2-klein . Fal also exposes a
`fal-ai/flux-2/klein/4b/edit` API endpoint
(https://fal.ai/models/fal-ai/flux-2/klein/4b/edit), currently advertised
at approximately $0.01 per megapixel; provider pricing can change.

**Do not automatically substitute Klein into Episode 1 yet.** Verify the
actual model-specific multi-reference cap, image alias handling,
request options/schema and fidelity on a single frozen test shot. The
present Studios Fal profile defaults and adapter behavior were built around
Flash. A model change is an adapter/integration experiment, not a reason
for Studios to reinterpret Worlds' authored prompts. In particular, confirm
whether the shot needs more reference images than Klein's supported capacity.
Modal GPU credits are a separate compute budget and do not pay for Fal API
requests. Report test cost and quality before proposing a default change.

Inspect the initial generated frame for Ember identity, Forge geometry, camera,
pre-action state, and provider cost/latency. Do not pay for nine further shots
until the first sample and its economics are acceptable.

## Full frame production after human approval

```bash
# Makes at most the missing, authored start/end candidates. No auto-approvals.
bash scripts/run_storyboard_review.sh "$PACKAGE" "$MANIFEST" "$ASSET_ROOT" full --confirm-paid
```

This writes a timestamped `*-storyboard-*.zip` to `~/Downloads`, opens that
folder via `xdg-open`, and retains the production package with its new
generated candidate history. It writes the foreground log alongside
the ZIP and embeds a redacted copy. When a provider or validation fails, the
runner still attempts to export accumulated frames and missing-frame
diagnostics; it exits with the generation failure status.

To run the exporter directly:

```bash
forge-studios storyboard-export \
  --package "$PACKAGE" \
  --asset-manifest "$MANIFEST" --asset-root "$ASSET_ROOT" \
  --telemetry .agenticforge/telemetry.jsonl \
  --git-sha forge-studios=<verified-full-commit-sha> \
  --out "$HOME/Downloads/episode1-storyboard-review.zip" --open-dir
```

No arbitrary number of images is imposed. Studios produces one required
opening for `start_only`; an additional closing candidate only for
`start_and_end` authored upstream. Don't manufacture a closing image merely
to fill the contact sheet. Inherited starts reuse an exact predecessor
endpoint according to the frame plan.

## Review and progression to video

Upload the ZIP as one artifact. Compare every original frame to its canonical
references and the story state, annotate significant character/site/scale/
camera/action-continuity defects separately from subjective style choices,
and select accepted candidates. Persistent systematic failures belong in the
owning prompt, reference, frame-plan, or provider boundary, not as edits to
the exported ZIP.

Only after storyboard approval, call the existing Studios `approve` and
`generate --role video` primitives under the current human paid-video gate.
Keep clip provenance and render via the existing Filmmaker timeline. Video
duration segmentation/provider support is a separate validated stage.
No claim is made here that an image-to-video model can produce a complete
20-second shot from these stills without testing.
