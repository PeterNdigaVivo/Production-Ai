# Camera onboarding

Adding a camera to Production-AI is a four-step loop; the whole thing runs
during working hours because the AI part is measuring where operators
actually sit, and empty chairs produce nothing.

    add camera  →  discover_zones  →  review overlay  →  decisions.json  →  promote_zones

The last two steps replace ad-hoc SQL and eyeballed polygons with
decisions-driven promotion: silence never promotes, no proposal skips
review, and every insert is atomic. The one-off
`services/backend/app/scripts/insert_stations_4_5.py` is superseded and
kept only for the historical record of the first camera.

## 1. Add the camera

Any of:

- **UI** — `/cameras` page → *Add camera* form (line dropdown, RTSP URL,
  target FPS). This is the normal path.
- **API** — `POST /api/v1/cameras` with `{line_id, name, rtsp_url, fps_target}`.
  See `docs/runbooks/staging_deploy.md` for the scripted onboarding curl.
- **Seed** — only for the fake `Cam-101` on a dev box. Not for factory
  cameras; the staging runbook explicitly warns against `make seed` there.

The ingestion service picks the new row up on its next `/cameras/_internal`
poll (≤ 30 s) and starts streaming.

> **One camera per workstation.** If two cameras see the same seat, register
> the seat under exactly one of them. Zones are camera-scoped; a person in
> the overlap gets attributed to both otherwise, and analytics double-counts.

## 2. Discover — measure where operators actually sit

Passive read-only tail of `stream:tracks:<camera>`; accumulates operator
foot-point dwell on a grid, finds hot blobs, proposes bounding boxes.
Never writes to the DB.

```bash
docker compose exec backend python -m app.scripts.discover_zones \
    --camera <camera_uuid> --minutes 20
```

Runs in the container, output lands at `/tmp/discover_zones/<camera_id>/`
(per-camera subdir so parallel runs on different cameras never overwrite
each other):

- `proposals.json` — machine-readable proposals with dwell seconds, frame
  dims, cutoff, sampling stats
- `overlay.jpg` — a current frame with existing zones in red, proposals
  in green, dwell heatmap faint underneath

Frame width/height and the far-field cutoff (fraction of frame height,
default 0.28) both come from the payload, so the same command works for
720p and 1080p cameras. Stop conditions built in: no traffic in the first
60 s, or fewer than ~500 samples across the whole window, exits 2 with a
clear message.

**Run this only during working hours.** Empty chairs generate no dwell.

## 3. Review — look at the overlay in chat

```bash
docker compose cp backend:/tmp/discover_zones/<camera_id>/overlay.jpg ./overlay.jpg
docker compose cp backend:/tmp/discover_zones/<camera_id>/proposals.json ./proposals.json
```

Paste the overlay into chat. For each proposal, decide:

- **approve** — seat is real; use this polygon
- **merge into another** — two blobs are one seat (common at frame edges
  where the operator's foot point straddles the clamp)
- **reject** — dwell without a seat (tea corner, chatty spot, supervisor
  desk)

## 4. Write `decisions.json` and promote

Write the review as a companion JSON next to `proposals.json`:

```json
{
  "camera_id": "<same uuid as proposals.json>",
  "line": "Line A",
  "decisions": [
    {"label": "S4", "action": "approve", "name": "Station 6"},
    {"label": "S5", "action": "merge",   "into": "S4"},
    {"label": "S6", "action": "reject",  "reason": "tea corner"}
  ]
}
```

Rules the tool enforces:

- Every proposal label needs a decision — silence is not skip, it is an
  error (exit 2). This is the guarantee that keeps ambiguous seats from
  being promoted by accident.
- `camera_id` must match; `line` must exist (never auto-created).
- `approve` — optionally name the station. If omitted, auto-numbered as
  the next free `Station N` on that line.
- `merge` — union the source polygon into the target's bounding box.
  Target must be an `approve` (chained merges disallowed).
- `reject` — dropped from the DB; `reason` recorded in the promotion log.

Copy the file into the container and run the promoter:

```bash
docker compose cp ./decisions.json backend:/tmp/discover_zones/<camera_id>/decisions.json
docker compose exec backend python -m app.scripts.promote_zones \
    --proposals /tmp/discover_zones/<camera_id>/proposals.json \
    --decisions /tmp/discover_zones/<camera_id>/decisions.json
```

Atomic single transaction, idempotent by workstation name, overlap-checked
against every existing zone on the camera *and* between the new
polygons. If anything fails validation it exits 2 with nothing inserted;
otherwise it prints the created row IDs and a
`select w.name, z.polygon …` verification block. A machine-readable
`promotion_log.json` is written alongside `decisions.json` for the audit
trail.

## 5. Verify

After promotion, watch `worker_events` land with the new
`workstation_id`s populated (previously they were all `NULL` because
`assign_workstation` had nowhere to map to):

```sql
select ws.name, count(*)
from worker_events we
join workstations ws on ws.id = we.workstation_id
where we.ts > now() - interval '10 minutes'
group by ws.name
order by ws.name;
```

Counts should climb for the newly-mapped stations. If a mapped station
stays at zero, either its polygon doesn't cover where the operator
actually sits (rerun `discover_zones` and compare), or that seat is
genuinely empty during the check.