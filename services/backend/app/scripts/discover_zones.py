"""Dwell-clustering zone discovery for camera onboarding.

Passive read-only observer over `stream:tracks:<camera>`: accumulates
per-track foot-point dwell time on a coarse grid, finds hot blobs, and
proposes seat-zone bounding boxes for human approval. Does NOT write to the
database — the output is one JSON file of proposals and one JPG overlay for
Stephen to eyeball before we insert anything.

Usage
-----

    docker compose exec backend python -m app.scripts.discover_zones \
        --camera <uuid> --minutes 20

Contract (must not break)
-------------------------

* Uses plain XREAD (never XREADGROUP, never joins the tracking or activity
  consumer groups). Passive tail-only, cannot steal messages.
* Reads Workstation + Zone as SELECT only. Never writes.
* Skips proposals whose centre lies inside an existing zone polygon for
  this camera (so we don't propose re-drawing what's already there).
* Skips proposals whose centre y < far-cutoff (default 28% of frame
  height; --far-cutoff-frac). Operators that far back render <~40 px
  tall on 720p and would produce noisy zones; logs them as "observed
  but skipped: too far". The cutoff is fractional so it scales across
  camera resolutions — 720p → 200 px, 1080p → 302 px.

Behaviour
---------

* Weight each track sample by the elapsed seconds since that track's
  previous sample (capped at 2s so a track that briefly disappears cannot
  dump a huge weight into the wrong cell on reappearance). A seated
  operator's foot point stays in one cell and accumulates minutes; a
  person walking the aisle leaves a smear of tiny weights.
* Grid cell = 16px (--cell). Frame default 1280x720 but each sample uses
  the payload's own w/h if present.
* Dwell threshold = 90s (--dwell-threshold-seconds). Real seats blow past
  this in a working shift; walkways don't.
* Blob = 4-connected cells above the threshold. Fit axis-aligned bbox,
  pad ±20px, enforce ≥100x100px.
* Bail early with clear message if `stream:tracks` shows no entries in the
  first 60s, or if fewer than ~500 samples arrive across the window.
* SIGINT (Ctrl-C) writes partial results from whatever was collected.
* Progress line every 60s: samples, distinct tracks, hot cells.
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import signal
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.db.models import Workstation, Zone


# ---------------------------------------------------------------------------- #
# Defaults
# ---------------------------------------------------------------------------- #
FRAME_W_DEFAULT = 1280
FRAME_H_DEFAULT = 720
CELL_PX = 16
DWELL_THRESHOLD_S = 90.0
PADDING_PX = 20
MIN_BOX_PX = 100
FAR_CUTOFF_FRAC = 0.28   # 28% of frame height — 720p → 200px, 1080p → 302px
MIN_SAMPLES = 500
MAX_PER_SAMPLE_WEIGHT_S = 2.0
IDLE_STREAM_TIMEOUT_S = 60.0
PROGRESS_INTERVAL_S = 60.0


# ---------------------------------------------------------------------------- #
# Pure helpers (unit-testable, no I/O)
# ---------------------------------------------------------------------------- #
def _point_in_polygon(x: float, y: float, poly: list[list[float]]) -> bool:
    """Ray-cast; avoids a shapely dependency (not in backend requirements)."""
    n = len(poly)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i][0], poly[i][1]
        xj, yj = poly[j][0], poly[j][1]
        if (yi > y) != (yj > y):
            x_cross = (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def _connected_blobs(hot_cells: set[tuple[int, int]]) -> list[list[tuple[int, int]]]:
    """4-connected connected components over the set of hot grid cells."""
    remaining = set(hot_cells)
    blobs: list[list[tuple[int, int]]] = []
    while remaining:
        seed = remaining.pop()
        blob = [seed]
        stack = [seed]
        while stack:
            cx, cy = stack.pop()
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                n = (cx + dx, cy + dy)
                if n in remaining:
                    remaining.remove(n)
                    blob.append(n)
                    stack.append(n)
        blobs.append(blob)
    return blobs


def _blob_bbox(cells: list[tuple[int, int]], cell_px: int) -> tuple[int, int, int, int]:
    xs = [c[0] for c in cells]
    ys = [c[1] for c in cells]
    return (min(xs) * cell_px, min(ys) * cell_px,
            (max(xs) + 1) * cell_px, (max(ys) + 1) * cell_px)


def _blob_center(cells: list[tuple[int, int]],
                 grid: dict[tuple[int, int], float],
                 cell_px: int) -> tuple[float, float]:
    total = sum(grid[c] for c in cells)
    if total <= 0:
        # fall back to geometric centre if nothing weighted (shouldn't happen)
        cx = sum(c[0] for c in cells) / len(cells)
        cy = sum(c[1] for c in cells) / len(cells)
        return (cx + 0.5) * cell_px, (cy + 0.5) * cell_px
    sx = sum(grid[c] * (c[0] + 0.5) * cell_px for c in cells) / total
    sy = sum(grid[c] * (c[1] + 0.5) * cell_px for c in cells) / total
    return sx, sy


def _finalize_box(bbox: tuple[int, int, int, int], padding: int,
                  min_wh: int, frame_w: int, frame_h: int) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = bbox
    x1 -= padding; y1 -= padding
    x2 += padding; y2 += padding
    if (x2 - x1) < min_wh:
        pad = (min_wh - (x2 - x1)) / 2
        x1 -= pad; x2 += pad
    if (y2 - y1) < min_wh:
        pad = (min_wh - (y2 - y1)) / 2
        y1 -= pad; y2 += pad
    x1 = max(0, int(x1)); y1 = max(0, int(y1))
    x2 = min(frame_w, int(x2)); y2 = min(frame_h, int(y2))
    return x1, y1, x2, y2


# ---------------------------------------------------------------------------- #
# I/O
# ---------------------------------------------------------------------------- #
async def _existing_zones(session, camera_id: str) -> list[dict]:
    stmt = (
        select(Zone, Workstation.code)
        .join(Workstation, Workstation.id == Zone.workstation_id)
        .where(Workstation.camera_id == camera_id)
    )
    return [
        {"kind": z.kind, "polygon": z.polygon, "workstation_code": code}
        for z, code in (await session.execute(stmt)).all()
    ]


async def _fetch_latest_frame(redis: Redis, camera_id: str) -> bytes | None:
    key = f"stream:frames:{camera_id}"
    entries = await redis.xrevrange(key, count=1)
    if not entries:
        return None
    _entry_id, fields = entries[0]
    return fields.get(b"jpg")


async def _sample_tracks(redis: Redis, camera_id: str, minutes: float,
                         cell_px: int, stop_event: asyncio.Event
                         ) -> tuple[dict, int, set, float, int, int]:
    """Tail stream:tracks:<camera_id> and accumulate dwell.

    Returns: (grid, samples_total, distinct_tracks, actual_seconds,
              frame_w, frame_h).
    Frame dims come from the last observed payload (falls back to defaults).
    """
    key = f"stream:tracks:{camera_id}"
    last_id = "$"  # only NEW entries — passive tail
    grid: dict[tuple[int, int], float] = defaultdict(float)
    last_track_ts: dict[int, float] = {}
    samples_total = 0
    distinct: set[int] = set()
    frame_w = FRAME_W_DEFAULT
    frame_h = FRAME_H_DEFAULT

    start = time.time()
    end_deadline = start + minutes * 60.0
    first_data_deadline = start + IDLE_STREAM_TIMEOUT_S
    last_progress = start
    saw_any = False

    while not stop_event.is_set() and time.time() < end_deadline:
        remaining = end_deadline - time.time()
        block_ms = max(200, min(2000, int(remaining * 1000)))
        try:
            resp = await redis.xread({key: last_id}, block=block_ms, count=100)
        except Exception as e:
            print(f"[warn] redis xread error: {e}; retrying", file=sys.stderr)
            await asyncio.sleep(1.0)
            continue

        if not resp:
            if not saw_any and time.time() > first_data_deadline:
                raise RuntimeError(
                    f"no entries in stream:tracks:{camera_id} for the first "
                    f"{int(IDLE_STREAM_TIMEOUT_S)}s. Is tracking-engine running "
                    f"and receiving detections for this camera?"
                )
        else:
            for _stream, entries in resp:
                for entry_id, fields in entries:
                    last_id = entry_id
                    saw_any = True
                    try:
                        payload = json.loads(fields[b"json"])
                    except Exception:
                        continue
                    ts = float(payload.get("ts", time.time()))
                    if payload.get("w"):
                        frame_w = int(payload["w"])
                    if payload.get("h"):
                        frame_h = int(payload["h"])
                    for tr in payload.get("tracks", []):
                        try:
                            tid = int(tr["track_id"])
                            x1, y1, x2, y2 = tr["xyxy"]
                        except (KeyError, ValueError, TypeError):
                            continue
                        fx = (x1 + x2) / 2.0
                        fy = float(y2)
                        if not (0 <= fx < frame_w and 0 <= fy < frame_h):
                            continue
                        prev = last_track_ts.get(tid)
                        last_track_ts[tid] = ts
                        weight = 0.0 if prev is None else max(0.0, min(MAX_PER_SAMPLE_WEIGHT_S, ts - prev))
                        col = int(fx // cell_px)
                        row = int(fy // cell_px)
                        grid[(col, row)] += weight
                        samples_total += 1
                        distinct.add(tid)

        now = time.time()
        if now - last_progress >= PROGRESS_INTERVAL_S:
            last_progress = now
            hot = sum(1 for v in grid.values() if v >= DWELL_THRESHOLD_S / 3)
            print(f"[{int(now - start):>4d}s] samples={samples_total} "
                  f"tracks={len(distinct)} hot_cells={hot}", flush=True)

    actual = time.time() - start
    return dict(grid), samples_total, distinct, actual, frame_w, frame_h


# ---------------------------------------------------------------------------- #
# Overlay rendering (Pillow, installed on demand)
# ---------------------------------------------------------------------------- #
def _ensure_pillow():
    try:
        from PIL import Image  # noqa: F401
        return
    except ImportError:
        pass
    print("[info] Pillow not present in backend image — installing it into "
          "this container just for this run (not persisted, not in "
          "requirements.txt).", flush=True)
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "pillow"])


def _render_overlay(jpg_bytes: bytes, grid: dict, cell_px: int,
                    existing: list[dict], proposals: list[dict],
                    out_path: Path) -> None:
    from PIL import Image, ImageDraw
    base = Image.open(io.BytesIO(jpg_bytes)).convert("RGBA")

    # Heat overlay
    heat = Image.new("RGBA", base.size, (0, 0, 0, 0))
    hdraw = ImageDraw.Draw(heat)
    if grid:
        gmax = max(grid.values())
        cutoff = max(1.0, gmax * 0.2)
        for (col, row), v in grid.items():
            if v < cutoff:
                continue
            alpha = int(160 * min(1.0, v / gmax))
            x = col * cell_px; y = row * cell_px
            hdraw.rectangle([x, y, x + cell_px, y + cell_px],
                            fill=(255, 40, 40, alpha))
    img = Image.alpha_composite(base, heat)
    draw = ImageDraw.Draw(img)

    # Existing zones — red outline
    for z in existing:
        pts = [(int(p[0]), int(p[1])) for p in z["polygon"]]
        if len(pts) >= 3:
            draw.polygon(pts, outline=(255, 60, 60, 255))
            cx = sum(p[0] for p in pts) // len(pts)
            cy = sum(p[1] for p in pts) // len(pts)
            label = f"{z.get('workstation_code', '?')} ({z.get('kind', '?')})"
            draw.text((cx - 20, cy - 6), label, fill=(255, 200, 200, 255))

    # Proposed zones — green rect + label
    for p in proposals:
        x1, y1, x2, y2 = p["bbox"]
        draw.rectangle([x1, y1, x2, y2], outline=(40, 220, 40, 255), width=3)
        draw.text((x1 + 6, y1 + 4),
                  f"{p['label']}  dwell={int(p['dwell_seconds'])}s",
                  fill=(40, 220, 40, 255))

    img.convert("RGB").save(str(out_path), "JPEG", quality=88)


# ---------------------------------------------------------------------------- #
# Main
# ---------------------------------------------------------------------------- #
def _next_label_index(existing: list[dict]) -> int:
    """S1/S2/... — start after the highest existing S<n> code we can parse."""
    highest = 0
    for z in existing:
        code = z.get("workstation_code") or ""
        # tolerate WS-01 and S1 alike
        for token in code.replace("-", " ").replace("S", " ").split():
            if token.isdigit():
                highest = max(highest, int(token))
    return highest + 1


async def amain(args) -> int:
    settings = get_settings()
    redis = Redis.from_url(settings.redis_url)
    engine = create_async_engine(settings.database_url, echo=False)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    # 1) Fetch existing zones (READ ONLY). Stop if this fails.
    try:
        async with Session() as db:
            existing = await _existing_zones(db, args.camera)
    except Exception as e:
        print(f"STOPPED: fetching existing zones failed: {e}", file=sys.stderr)
        return 2
    finally:
        await engine.dispose()

    print(f"[init] camera={args.camera} minutes={args.minutes} "
          f"cell={args.cell}px dwell_threshold={args.dwell_threshold_seconds}s")
    print(f"[init] existing zones on this camera: {len(existing)}")

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _on_sigint():
        print("\n[sigint] finishing early; writing whatever was collected",
              flush=True)
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _on_sigint)
        except NotImplementedError:  # pragma: no cover
            pass

    # 2) Sample.
    try:
        grid, samples, tracks, actual_seconds, frame_w, frame_h = await _sample_tracks(
            redis, args.camera, args.minutes, args.cell, stop_event,
        )
    except RuntimeError as e:
        print(f"STOPPED: {e}", file=sys.stderr)
        await redis.aclose()
        return 2

    print(f"[done] samples={samples} tracks={len(tracks)} "
          f"actual_seconds={int(actual_seconds)} frame={frame_w}x{frame_h}")

    interrupted = stop_event.is_set()
    if not interrupted and samples < MIN_SAMPLES:
        print(f"STOPPED: only {samples} samples in {int(actual_seconds)}s "
              f"(need ~{MIN_SAMPLES}). Not enough traffic to propose zones "
              f"reliably. Try a longer --minutes during working hours.",
              file=sys.stderr)
        await redis.aclose()
        return 2

    # 3) Blob detection + finalisation.
    hot = {c for c, v in grid.items() if v >= args.dwell_threshold_seconds}
    blobs = _connected_blobs(hot)
    print(f"[blobs] {len(blobs)} candidate blob(s) above {args.dwell_threshold_seconds}s dwell")

    # Fractional far-cutoff scales with resolution (720p→~200, 1080p→~302).
    far_cutoff_px = int(frame_h * args.far_cutoff_frac)
    print(f"[far-cutoff] y < {far_cutoff_px}px "
          f"(= {args.far_cutoff_frac:.2f} * {frame_h}px frame height)")

    label_i = _next_label_index(existing)
    proposals: list[dict] = []
    skipped_far: list[dict] = []
    skipped_inside: list[dict] = []

    for cells in sorted(blobs, key=lambda b: -sum(grid[c] for c in b)):
        cx, cy = _blob_center(cells, grid, args.cell)
        dwell = sum(grid[c] for c in cells)
        peak = max(cells, key=lambda c: grid[c])

        if any(_point_in_polygon(cx, cy, z["polygon"]) for z in existing):
            skipped_inside.append({
                "center": [int(cx), int(cy)],
                "dwell_seconds": round(dwell, 1),
            })
            continue
        if cy < far_cutoff_px:
            skipped_far.append({
                "center": [int(cx), int(cy)],
                "dwell_seconds": round(dwell, 1),
            })
            continue

        bbox = _finalize_box(
            _blob_bbox(cells, args.cell), args.padding, args.min_size,
            frame_w, frame_h,
        )
        x1, y1, x2, y2 = bbox
        polygon = [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]
        proposals.append({
            "label": f"S{label_i}",
            "polygon": polygon,
            "bbox": bbox,
            "center": [round(cx, 1), round(cy, 1)],
            "dwell_seconds": round(dwell, 1),
            "peak_cell": {
                "col": peak[0], "row": peak[1],
                "seconds": round(grid[peak], 1),
                "px": [peak[0] * args.cell, peak[1] * args.cell],
            },
        })
        label_i += 1

    # 4) Write outputs. Default is /tmp/discover_zones/<camera_id>/ so runs
    #    for different cameras never clobber each other. --out-dir overrides.
    out_dir = Path(args.out_dir) if args.out_dir else Path("/tmp/discover_zones") / args.camera
    out_dir.mkdir(parents=True, exist_ok=True)

    proposals_doc = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "camera_id": args.camera,
        "sampling": {
            "requested_minutes": args.minutes,
            "actual_seconds": int(actual_seconds),
            "interrupted": interrupted,
            "samples": samples,
            "distinct_tracks": len(tracks),
            "frame": {"w": frame_w, "h": frame_h},
            "grid_cell_px": args.cell,
            "dwell_threshold_s": args.dwell_threshold_seconds,
            "far_cutoff_frac": args.far_cutoff_frac,
            "far_cutoff_px": far_cutoff_px,
        },
        "existing_zones_count": len(existing),
        "proposals": proposals,
        "skipped_too_far": skipped_far,
        "skipped_inside_existing_zone": skipped_inside,
    }
    proposals_path = out_dir / "proposals.json"
    proposals_path.write_text(json.dumps(proposals_doc, indent=2))

    # 5) Overlay.
    overlay_path = out_dir / "overlay.jpg"
    try:
        jpg = await _fetch_latest_frame(redis, args.camera)
    except Exception as e:
        print(f"[warn] frame fetch failed: {e}", file=sys.stderr)
        jpg = None
    if jpg:
        _ensure_pillow()
        _render_overlay(jpg, grid, args.cell, existing, proposals, overlay_path)
    else:
        print("[warn] no frame in stream:frames — overlay skipped", file=sys.stderr)

    await redis.aclose()

    # 6) Summary.
    print("")
    print(f"proposals: {proposals_path}")
    if jpg:
        print(f"overlay:   {overlay_path}")
    print(f"proposed:  {len(proposals)}")
    print(f"skipped (inside existing): {len(skipped_inside)}")
    print(f"skipped (too far, y<{far_cutoff_px}px = "
          f"{args.far_cutoff_frac:.2f}·{frame_h}): {len(skipped_far)}")
    for s in skipped_far:
        print(f"  too-far centre {tuple(s['center'])}  dwell={s['dwell_seconds']}s")
    print("")
    print("To copy the overlay off the container:")
    print(f"  docker compose cp backend:{overlay_path} ./overlay.jpg")
    print("  docker compose cp backend:%s ./proposals.json" % proposals_path)
    return 0


def _parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Passive dwell-clustering zone discovery. "
                    "Read-only against Redis and Postgres — never writes zones.")
    p.add_argument("--camera", required=True, help="camera_id (UUID)")
    p.add_argument("--minutes", type=float, default=20.0,
                   help="sampling window in minutes (default 20)")
    p.add_argument("--cell", type=int, default=CELL_PX,
                   help="grid cell size in px (default 16)")
    p.add_argument("--dwell-threshold-seconds", type=float,
                   default=DWELL_THRESHOLD_S,
                   help=f"dwell in seconds to count a cell as hot (default {DWELL_THRESHOLD_S})")
    p.add_argument("--padding", type=int, default=PADDING_PX,
                   help=f"px padding around blob bbox (default {PADDING_PX})")
    p.add_argument("--min-size", type=int, default=MIN_BOX_PX,
                   help=f"minimum zone width/height in px (default {MIN_BOX_PX})")
    p.add_argument("--far-cutoff-frac", type=float, default=FAR_CUTOFF_FRAC,
                   help=f"skip blobs whose centre y is above this fraction of "
                        f"frame height (default {FAR_CUTOFF_FRAC}). Scales "
                        f"correctly across camera resolutions.")
    p.add_argument("--out-dir", default=None,
                   help="output directory (default /tmp/discover_zones/<camera_id>)")
    return p.parse_args(argv)


if __name__ == "__main__":
    sys.exit(asyncio.run(amain(_parse_args())))
