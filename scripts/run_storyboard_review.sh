#!/usr/bin/env bash
# Foreground EpisodePackage storyboard experiment. No automatic approvals or video.
set -uo pipefail

usage() {
  echo "usage: bash scripts/run_storyboard_review.sh PACKAGE MANIFEST ASSET_ROOT export|smoke|full [--confirm-paid]" >&2
  exit 2
}
[[ $# -ge 4 ]] || usage
PACKAGE=$1
MANIFEST=$2
ASSET_ROOT=$3
RUN=$4
CONFIRM=${5:-}
# Validate each location separately so an unsuccessful operator handoff never
# looks like an inference or storyboard-generation failure.
MISSING=0
if [[ ! -f "$PACKAGE" ]]; then
  echo "ERROR: EpisodePackage JSON is not a file: $PACKAGE" >&2
  MISSING=1
elif [[ "$PACKAGE" == *.zip ]]; then
  echo "ERROR: Pass the validated episode-package.json, not a review ZIP: $PACKAGE" >&2
  MISSING=1
fi
if [[ ! -f "$MANIFEST" ]]; then
  echo "ERROR: Forge Born asset manifest not found: $MANIFEST" >&2
  MISSING=1
fi
if [[ ! -d "$ASSET_ROOT" ]]; then
  echo "ERROR: Canonical media root is not a directory: $ASSET_ROOT" >&2
  MISSING=1
fi
if [[ "$MISSING" -ne 0 ]]; then
  echo "Locate the latest existing package with:" >&2
  echo '  find "$HOME/code/forge-worlds/temp" -type f -name episode-package.json -print 2>/dev/null | tail -n 12' >&2
  echo "Your earlier Worlds review ZIP can also contain episode-package.json; inspect it with unzip -l." >&2
  echo "Do not rerun Worlds or use an old versioned fixture simply to satisfy this check." >&2
  exit 2
fi
[[ "$RUN" == export || "$RUN" == smoke || "$RUN" == full ]] || usage
if [[ "$RUN" == full && "$CONFIRM" != "--confirm-paid" ]]; then
  echo "Full paid generation requires --confirm-paid after budget/first-frame review" >&2
  exit 2
fi
if ! command -v forge-studios >/dev/null 2>&1; then
  echo "forge-studios CLI is not installed on PATH" >&2; exit 2
fi

PACKAGE=$(realpath "$PACKAGE")
MANIFEST=$(realpath "$MANIFEST")
ASSET_ROOT=$(realpath "$ASSET_ROOT")
EPISODE=$(python - "$PACKAGE" <<'PY'
import json, re, sys
v=json.load(open(sys.argv[1]))
print(re.sub(r'[^a-zA-Z0-9_-]', '-', v['episode_id']))
PY
)
TIMESTAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUTPUT_DIR="$HOME/Downloads"
mkdir -p "$OUTPUT_DIR"
OUTPUT="$OUTPUT_DIR/${EPISODE}-storyboard-${TIMESTAMP}.zip"
LOG="$OUTPUT_DIR/${EPISODE}-storyboard-${TIMESTAMP}.log"
TELEMETRY=".agenticforge/telemetry.jsonl"
STATUS=0
STUDIOS_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

# Validate without discarding evidence when a partially generated package is bad.
forge-studios validate "$PACKAGE" > "$LOG" 2>&1 || STATUS=$?
if [[ "$STATUS" -ne 0 && "$RUN" != export ]]; then
  echo "[storyboard] validation failed; paid generation skipped" | tee -a "$LOG"
  RUN=export
fi
# Paid generation is development-only. Verify the running development
# Compose container, its token, environment, and isolated host port first.
# No credentials are written to disk or echoed; no services are changed.
if [[ "$RUN" != export ]]; then
  RUNTIME_SETTINGS=$(python -m forge_studios.runtime_preflight \
    --platform-dir "$STUDIOS_DIR/../agenticforge-platform" 2>>"$LOG") || {
      STATUS=2
      RUN=export
      tail -n 1 "$LOG" >&2
    }
  if [[ "$RUN" != export ]]; then
    mapfile -t RUNTIME_LINES <<< "$RUNTIME_SETTINGS"
    AI_RUNTIME_URL="${RUNTIME_LINES[0]}"
    AI_RUNTIME_TOKEN="${RUNTIME_LINES[1]}"
    export AI_RUNTIME_URL AI_RUNTIME_TOKEN
    echo "[storyboard] isolated development runtime verified at $AI_RUNTIME_URL" | tee -a "$LOG"
    echo "[storyboard] generation profile: normal (Fal charges apply)" | tee -a "$LOG"
    unset RUNTIME_SETTINGS RUNTIME_LINES
  fi
fi
if [[ "$RUN" == smoke ]]; then
  SHOT=$(python - "$PACKAGE" <<'PY'
import json, sys
v=json.load(open(sys.argv[1]))
print(v['shots'][0]['shot_id'])
PY
)
  echo "[storyboard] generating one start frame for $SHOT" | tee -a "$LOG"
  FORGE_STUDIOS_MODE=normal forge-studios generate --package "$PACKAGE" --shot-id "$SHOT" --role start_frame \
    --provider fal --asset-manifest "$MANIFEST" --asset-root "$ASSET_ROOT" --telemetry "$TELEMETRY" \
    2>&1 | tee -a "$LOG"
  STATUS=${PIPESTATUS[0]}
elif [[ "$RUN" == full ]]; then
  echo "[storyboard] full ten-shot normal-profile paid generation explicitly confirmed" | tee -a "$LOG"
  FORGE_STUDIOS_MODE=normal forge-studios storyboard --package "$PACKAGE" \
    --out "$OUTPUT_DIR/${EPISODE}-storyboard-${TIMESTAMP}.html" --generate \
    --provider fal --asset-manifest "$MANIFEST" \
    --asset-root "$ASSET_ROOT" --telemetry "$TELEMETRY" \
    2>&1 | tee -a "$LOG"
  STATUS=${PIPESTATUS[0]}
fi

# Record checked-out repository SHAs as execution context (not story authority).
BORN_DIR=$(dirname "$(dirname "$MANIFEST")")
GIT_ARGS=()
for name_dir in "forge-studios:$STUDIOS_DIR" "forge-born:$BORN_DIR" "forge-worlds:$STUDIOS_DIR/../forge-worlds"; do
  NAME=${name_dir%%:*}
  DIR=${name_dir#*:}
  SHA=$(git -C "$DIR" rev-parse HEAD 2>/dev/null || true)
  if [[ "$SHA" =~ ^[0-9a-f]{40}$ ]]; then
    GIT_ARGS+=(--git-sha "$NAME=$SHA")
  fi
done

# Always export completed frames, references, diagnostics, and a missing-frame ledger.
forge-studios storyboard-export --package "$PACKAGE" --asset-manifest "$MANIFEST" \
  --asset-root "$ASSET_ROOT" --telemetry "$TELEMETRY" --log "$LOG" \
  "${GIT_ARGS[@]}" --out "$OUTPUT" --open-dir || {
    echo "Export failed. Diagnostics retained at $LOG" >&2
    exit 1
  }
echo "[storyboard] review ZIP: $OUTPUT"
echo "[storyboard] execution log: $LOG"
exit "$STATUS"
