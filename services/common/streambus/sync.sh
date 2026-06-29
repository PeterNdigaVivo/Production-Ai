#!/usr/bin/env bash
# Propagate the canonical streambus consumer into each service's vendored copy.
# Run from the repo root:  bash services/common/streambus/sync.sh
set -euo pipefail
SRC="services/common/streambus/consumer.py"
for dst in services/detection-engine/detection/streambus \
           services/tracking-engine/tracking/streambus \
           services/activity-engine/activity/streambus; do
  mkdir -p "$dst"
  {
    echo '"""VENDORED from services/common/streambus/consumer.py — keep in sync.'
    echo ""
    echo "Each engine builds from its own Docker context, so this shared helper is"
    echo "copied into each service package. Canonical copy + tests live in"
    echo "services/common/streambus/. After editing canonical, run"
    echo "  bash services/common/streambus/sync.sh"
    tail -n +2 "$SRC"
  } > "$dst/consumer.py"
  touch "$dst/__init__.py"
  echo "synced -> $dst/consumer.py"
done
