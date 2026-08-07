import json
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from redis.asyncio import Redis

from app.db.session import get_db
from app.db.models import Camera, Zone, Workstation
from app.schemas.tenancy import CameraCreate, CameraRead
from app.api.deps import current_user
from app.core.config import get_settings
from app.services.zone_queries import latest_zone_ids

# Where discover_zones writes its per-camera proposals. Kept as a module
# constant (not a settings knob) because it must match the script's own
# hard-coded default in app/scripts/discover_zones.py — the two live on
# the same filesystem in the same container.
DISCOVERY_DIR = Path("/tmp/discover_zones")

router = APIRouter(dependencies=[Depends(current_user)])


# Frame-stream client is kept separate from the token-store client because it
# must NOT decode responses (jpeg payloads are binary). Lazy singleton so tests
# can override via app.dependency_overrides.
_frames_redis: Redis | None = None


def get_frames_redis() -> Redis:
    global _frames_redis
    if _frames_redis is None:
        _frames_redis = Redis.from_url(get_settings().redis_url, decode_responses=False)
    return _frames_redis


@router.get("", response_model=list[CameraRead])
async def list_cameras(line_id: str | None = None, db: AsyncSession = Depends(get_db)):
    stmt = select(Camera)
    if line_id:
        stmt = stmt.where(Camera.line_id == line_id)
    res = await db.execute(stmt)
    return list(res.scalars())


@router.post("", response_model=CameraRead, status_code=201)
async def create_camera(body: CameraCreate, db: AsyncSession = Depends(get_db)):
    cam = Camera(
        line_id=body.line_id, name=body.name, rtsp_url=body.rtsp_url,
        fps_target=body.fps_target, resolution=body.resolution,
    )
    db.add(cam)
    await db.commit()
    await db.refresh(cam)
    return cam


@router.get("/{camera_id}/frame")
async def get_camera_frame(
    camera_id: str,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_frames_redis),
) -> Response:
    """Return the latest JPEG frame for a camera from `stream:frames:<id>`.

    The ingestion worker (rtsp_worker.py) publishes each frame as an XADD with
    fields {camera_id, ts, w, h, jpg}. We XREVRANGE COUNT 1 to pull the newest
    and hand back the raw JPEG bytes; native width/height ride along in
    X-Frame-Width / X-Frame-Height response headers so the UI can build an SVG
    viewBox that scales polygons correctly regardless of camera resolution.
    """
    cam = await db.get(Camera, camera_id)
    if not cam:
        raise HTTPException(404, "camera not found")

    key = f"stream:frames:{camera_id}"
    entries = await redis.xrevrange(key, count=1)
    if not entries:
        raise HTTPException(404, "no frames available for this camera")

    _entry_id, fields = entries[0]
    jpg = fields.get(b"jpg")
    if not jpg:
        raise HTTPException(404, "frame payload missing jpg field")

    headers: dict[str, str] = {"Cache-Control": "no-store"}
    w = fields.get(b"w")
    h = fields.get(b"h")
    if w is not None:
        headers["X-Frame-Width"] = w.decode() if isinstance(w, bytes) else str(w)
    if h is not None:
        headers["X-Frame-Height"] = h.decode() if isinstance(h, bytes) else str(h)

    return Response(content=jpg, media_type="image/jpeg", headers=headers)


@router.get("/{camera_id}/zones")
async def list_camera_zones(
    camera_id: str,
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Return every zone attached to any workstation on this camera, with the
    workstation's human-readable name inlined so the viewer can label each
    polygon without a second round-trip. User-facing counterpart to the
    `_internal` variant used by tracking-engine."""
    cam = await db.get(Camera, camera_id)
    if not cam:
        raise HTTPException(404, "camera not found")

    stmt = (
        select(Zone, Workstation.id, Workstation.name)
        .join(Workstation, Workstation.id == Zone.workstation_id)
        .where(Workstation.camera_id == camera_id)
        .where(Zone.id.in_(latest_zone_ids()))
    )
    rows = (await db.execute(stmt)).all()
    return [
        {
            "zone_id": str(z.id),
            "workstation_id": str(ws_id),
            "workstation_name": ws_name,
            "kind": z.kind,
            "polygon": z.polygon,
            "layout_version": z.layout_version,
        }
        for z, ws_id, ws_name in rows
    ]


@router.get("/{camera_id}/tracks")
async def get_camera_tracks(
    camera_id: str,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_frames_redis),
) -> dict:
    """Return the newest per-frame tracking snapshot for a camera.

    The tracking engine publishes each frame's tracks as a JSON-encoded
    string under the `json` field of `stream:tracks:<camera_id>`
    (tracking/main.py:106). We XREVRANGE COUNT 1 to get the newest entry
    and decode it. `age_seconds` is stamped on so the UI can render a
    staleness indicator without needing its own clock alignment with the
    server.

    An empty stream is a normal quiet state (pipeline idle, no detections
    lately) — not an error — so we return a 200 with `stale: true` and
    `tracks: []` rather than a 404. That keeps the live view's polling
    loop happy while making the "nothing to show" reason explicit.

    Read-only: no XADD, no side effects.
    """
    cam = await db.get(Camera, camera_id)
    if not cam:
        raise HTTPException(404, "camera not found")

    key = f"stream:tracks:{camera_id}"
    entries = await redis.xrevrange(key, count=1)
    now = time.time()
    if not entries:
        return {"camera_id": camera_id, "ts": None, "age_seconds": None,
                "tracks": [], "machine_running": {}, "stale": True}

    _entry_id, fields = entries[0]
    raw = fields.get(b"json")
    if raw is None:
        # Stream exists but the newest entry is malformed. Treat as stale so
        # the client shows the same friendly banner it would for empty state.
        return {"camera_id": camera_id, "ts": None, "age_seconds": None,
                "tracks": [], "machine_running": {}, "stale": True}
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"camera_id": camera_id, "ts": None, "age_seconds": None,
                "tracks": [], "machine_running": {}, "stale": True}

    ts = payload.get("ts")
    age = (now - float(ts)) if isinstance(ts, (int, float)) else None
    return {
        "camera_id": payload.get("camera_id", camera_id),
        "ts": ts,
        "age_seconds": age,
        "tracks": payload.get("tracks", []),
        "machine_running": payload.get("machine_running", {}),
        "stale": False,
    }


@router.get("/{camera_id}/discovery")
async def get_camera_discovery(
    camera_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Serve the most recent dwell-discovery proposals for this camera.

    Reads `/tmp/discover_zones/{camera_id}/proposals.json` — the file that
    `app.scripts.discover_zones` writes on each passive-observation run.
    The zone editor overlays these as reference (measured dwell centres,
    proposal polygons, far-cutoff line) so operators can position seats
    against real data rather than eyeballing the raw frame.

    Serves the last run's file only — never triggers a fresh discovery
    (that would tie up the request thread for minutes). 404 when the file
    is absent so the UI can prompt "run discover_zones first".
    """
    cam = await db.get(Camera, camera_id)
    if not cam:
        raise HTTPException(404, "camera not found")

    path = DISCOVERY_DIR / camera_id / "proposals.json"
    if not path.exists():
        raise HTTPException(404, f"no discovery run found for camera {camera_id}")
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        # Corrupt file — surface a 5xx so callers know it's not "just missing".
        raise HTTPException(500, f"discovery file is not valid JSON: {e}")


# NOTE: the camera heartbeat endpoint moved to the internal router
# (app/api/v1/internal.py) so it is gated by X-Internal-Token and blocked
# externally by nginx, matching the other service-to-service routes (Finding 7).
# The ingestion service already holds INTERNAL_SERVICE_TOKEN.
