#!/bin/sh
# Seed YOLO_MODEL_PATH from the baked /opt/models copy on first run so the
# /models volume (or any other empty mount) becomes usable without a manual
# `ultralytics YOLO(...)` fetch inside the container.
set -eu

BAKED="/opt/models/yolo11n.pt"
TARGET="${YOLO_MODEL_PATH:-/models/yolo11n.pt}"

if [ ! -f "$TARGET" ] && [ -f "$BAKED" ]; then
    mkdir -p "$(dirname "$TARGET")"
    cp "$BAKED" "$TARGET"
fi

exec "$@"
