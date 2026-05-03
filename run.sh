#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

uv run main.py \
  --audio-device "Aggregate Device" \
  --screen-interval 5.0 \
  --audio-chunk-duration 15.0 \
  --segment-duration 300 \
  --output-dir ./output \
  --translate \
  "$@"
