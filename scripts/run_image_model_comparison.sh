#!/usr/bin/env bash
# Frozen-prompt image comparison; do not modify production package.
set -euo pipefail
if [[ $# -lt 3 ]]; then
  echo 'usage: bash scripts/run_image_model_comparison.sh PACKAGE MANIFEST ASSET_ROOT [--models openai,nano,klein,qwen] [--confirm-paid]' >&2
  exit 2
fi
PACKAGE=$1
MANIFEST=$2
ASSET_ROOT=$3
shift 3
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
for path in "$PACKAGE" "$MANIFEST"; do
  [[ -f "$path" ]] || { echo "[compare] missing file: $path" >&2; exit 2; }
done
[[ -d "$ASSET_ROOT" ]] || { echo "[compare] missing assets: $ASSET_ROOT" >&2; exit 2; }
CONFIRMED=0
for arg in "$@"; do
  [[ "$arg" != "--confirm-paid" ]] || CONFIRMED=1
done
CMD=(python -m forge_studios.image_compare --package "$PACKAGE" --asset-manifest "$MANIFEST" --asset-root "$ASSET_ROOT" "$@")
if [[ "$CONFIRMED" == 1 ]]; then
  echo '[compare] paid Fal image generation: one call/model, parallel; no production edits or video.'
  python -m forge_studios.runtime_preflight --platform-dir "$ROOT/../agenticforge-platform" -- "${CMD[@]}"
else
  echo '[compare] dry-run only. Add --confirm-paid to generate.'
  "${CMD[@]}"
fi
