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
[[ -f "$PACKAGE" && -f "$MANIFEST" && -d "$ASSET_ROOT" ]] || {
  echo "Package, manifest and asset root must exist" >&2; exit 2;
}
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

# Validate without discarding evidence when a partially generated package is bad.
forge-studios validate "$PACKAGE" > "$LOG" 2>&1 || STATUS=$?
if [[ "$RUN" == smoke ]]; then
  SHOT=$(python - "$PACKAGE" <<'PY'
import json, sys
v=json.load(open(sys.argv[1]))
print(v['shots'][0]['shot_id'])
PY
)
  echo "[storyboard] generating one start frame for $SHOT" | tee -a "$LOG"
  forge-studios generate --package "$PACKAGE" --shot-id "$SHOT" --role start_frame \
    --provider fal --mode cheap --asset-root "$ASSET_ROOT" --telemetry "$TELEMETRY" \
    2>&1 | tee -a "$LOG"
  STATUS=${PIPESTATUS[0]}
elif [[ "$RUN" == full ]]; then
  echo "[storyboard] full ten-shot paid generation explicitly confirmed" | tee -a "$LOG"
  forge-studios storyboard --package "$PACKAGE" \
    --out "$OUTPUT_DIR/${EPISODE}-storyboard-${TIMESTAMP}.html" --generate \
    --provider fal --mode cheap --asset-manifest "$MANIFEST" \
    --asset-root "$ASSET_ROOT" --telemetry "$TELEMETRY" \
    2>&1 | tee -a "$LOG"
  STATUS=${PIPESTATUS[0]}
fi

# Record checked-out repository SHAs as execution context (not story authority).
STUDIOS_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
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
