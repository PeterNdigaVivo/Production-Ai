# Production-AI — Roadmap

**Owner:** Stephen Nderitu
**Status:** Phase 0 in progress
**Last updated:** 3 August 2026

---

## True north

> A supervisor sees a stalled workstation and fixes it before the shift ends.

Every proposed feature is judged against that sentence. If it does not move a
supervisor closer to acting on a problem during the shift it happened, it waits.

---

## Ground rules

1. **No phase starts before the previous one meets its exit test.** The exit
   tests are written to be verifiable, not subjective.
2. **No UI is designed against imagined data.** Screens are built after the
   data they display exists in the database.
3. **The pipeline is not "AI training".** Detection (does a person exist in
   this frame) and activity (what are they doing) are separate layers. See
   "Detection vs. activity" below — this distinction drives most of the
   sequencing.
4. **The Odoo instance on this host is production and is never touched.**
   All work is scoped to the `production-ai` compose project.

---

## Detection vs. activity — read this before proposing features

These get conflated constantly, and the confusion causes wasted work.

| Layer | What it does | How it improves |
|---|---|---|
| **Detection** (`detection-engine`, YOLO) | Finds objects in a frame. Currently the COCO classes — `person`, `cell phone`, etc. | Custom model training. Needs thousands of labelled frames and a GPU. |
| **Tracking** (`tracking-engine`) | Gives each person a stable ID across frames, maps them to a zone. | Better zone geometry. Threshold tuning. |
| **Activity** (`activity-engine`, FSM) | Turns "person in work zone for 40s" into WORKING / IDLE / AWAY / WAITING_FOR_INPUT. | Tuning thresholds to the line's actual rhythm. No training involved. |

**"Operator missing" is not a trained class.** It is *no person detected in the
work zone for N seconds* — pure zone logic, already implemented. The same is
true of idle and waiting states.

The practical consequence: **accurate zones and well-tuned thresholds will
improve results far more than a custom model, and cost a fraction as much.**
Custom training is deferred to Phase 4 and only happens if Phase 3 proves the
stock model is the bottleneck.

---

## Phase 0 — Prove the pipeline

**Goal:** one real camera, frames moving end to end, detections landing in
Postgres.

- [x] Fix dependency, migration and build blockers
- [x] CPU-only detection engine (GPU kept as a separate build target)
- [x] Resource-fit the stack for 4 cores / 16 GB
- [x] Fix the Celery event-loop bug
- [x] Redis `maxmemory-policy` set to `noeviction`
- [ ] Confirm the dashboard renders and login works
- [ ] First camera configured and streaming
- [ ] `person` detections confirmed in `worker_events`

**Exit test:** `select count(*) from worker_events` climbs while a real camera
is pointed at the floor.

**Not in this phase:** any visual design work, any new screens.

---

## Phase 1 — Make it configurable

**Goal:** onboard a camera and define its workstations without a database
console.

- Camera CRUD in the UI: add, edit, disable
- **Test-connection button** — validates the RTSP URL before saving
- **Zone editor** — pull a still frame from the camera, draw polygons over it,
  label each as a workstation's work area
- Assign workstations to lines; set target pieces/hour
- Basic shift definitions

**Exit test:** a second camera is onboarded start to finish by someone who has
not seen the codebase, using only the UI.

**UI standard for this phase:** functional. Clean and unambiguous, no brand
work. The zone editor is the centrepiece — everything downstream depends on
zone geometry being right, and it is the single highest-value screen in the
system.

---

## Phase 2 — Make it observable

**Goal:** the control room. This is where the Vivo identity lands.

- Live floor view: stations as tiles, colour-coded by state
- Per-station drill-down: current state, time in state, today's cycle count
- Alerts: real-time feed, acknowledge, filter by severity and line
- Shift KPIs: productivity %, pieces completed vs. target, downtime breakdown
- Full visual pass in Vivo brand

**Exit test:** a supervisor opens it unprompted, daily, for a week.

**Design brief:**

- **Identity, not theme.** Vivo logo, wordmark, typeface and one accent colour.
  The retail storefront is built to make customers linger and browse; a factory
  dashboard is read in three seconds from two metres away. Opposing goals —
  borrow the identity, not the layout.
- Colour carries meaning. Green/amber/red mean state, never decoration.
- Legible on a cheap monitor, in a bright room, at distance. Large numerals,
  high contrast, generous spacing.
- The most important number on any screen is the largest thing on it.
- Assumes glances, not sessions.

**Assets required before starting:** logo SVG, brand hex codes, typeface name.
From the marketing brand guide — screenshots of the website are not sufficient.

---

## Phase 3 — Make it trustworthy

**Goal:** numbers you would defend in a management meeting.

- Event review screen: sample recent events, mark correct/incorrect
- Accuracy measurement per camera and per state
- Threshold tuning informed by that data
- Documented known failure modes (occlusion, lighting, two people in one zone)

**Exit test:** measured accuracy on a sample of at least 200 reviewed events,
and the number is good enough to act on.

This phase is the one most likely to be skipped under pressure. Skipping it
means publishing productivity figures about named workers that nobody has
verified. Do not skip it.

---

## Phase 4 — Scale and custom models

**Goal:** the full floor.

- Hardware upgrade (GPU box) — required beyond ~2 cameras
- Remaining cameras onboarded in batches
- Multi-line and multi-factory navigation
- Custom model training, **only if Phase 3 shows the stock model is the
  limiting factor**, and only for garment-specific objects (bundle, tray,
  machine head) — never for actions, which remain zone and FSM logic
- Reporting: export, scheduled summaries, integration with Odoo if useful

**Exit test:** the full line runs unattended for a month and the numbers are
still trusted.

---

## Deliberately out of scope for now

Kept here so they are not re-litigated every week:

- Facial recognition or individual worker identification
- Any model training before Phase 4
- Mobile app (the responsive web dashboard covers it)
- Cloud or VPS hosting — revisit once the LAN deployment is stable
- Kubernetes (a Helm chart exists in the repo; it is aspirational)

---

## Current hardware ceiling

Intel Xeon E5-1607 v3, 4 cores, 16 GB RAM, no usable GPU (the Quadro K-series
card is pre-CUDA-12 and cannot be passed through WSL2).

Realistic capacity: **1 camera at 5–8 fps, or 2 at 3–4 fps**, using yolo11n at
416 px on CPU. This is a pilot machine. Phase 4 is gated on better hardware,
and the GPU build target is already in place for when it arrives.

---

## Off-roadmap change log

This log exists so out-of-band work stays recorded and the roadmap above
remains the single source of truth. Newest first; the roadmap phases stay
narrative, this section stays factual (one entry per landed commit or short
group of commits).

### 3 August 2026 — Redis `noeviction` + adopt ROADMAP.md as authoritative

Phase 0 tick. Redis now runs with `--maxmemory-policy noeviction`; every
stream writer already trims with MAXLEN (`stream:frames:*` 120/camera,
`stream:detections:*` 600, `stream:tracks:*` 600, `stream:events` 10k), so
memory is bounded by design and if the 512M cap is ever hit writes must fail
loudly rather than silently evicting entries or `XGROUP` metadata (which
would corrupt the pipeline). This commit also adopts `ROADMAP.md` at repo
root as the authoritative plan and points the old `README.md` / `docs/
architecture/phases.md` at it, marking the pre-existing four-phase list
superseded.

### 3 August 2026 — Tracker phantom-box + interval double-count fix (`8661da0`)

Two live bugs found against a real camera:

- **Tracker geometry.** `bytetrack.update()` used to return every surviving
  track, including tracks whose last observation was up to `max_age=30`
  frames ago. Those tracks were Kalman-only predictions and extrapolated
  unboundedly (observed: `y1=-4009`, `y2=2151`, 0.001-pixel boxes). Fix:
  1-pixel floor in `_xyxy_to_z` / `_x_to_xyxy`; `update()` now returns only
  tracks with `misses == 0 AND hits >= min_hits`; `tracking/main.py` adds a
  publish-boundary sanity filter (drop degenerate, `<8×16px`, or off-frame
  boxes; clamp survivors) so the guarantee holds regardless of which
  tracker is configured.
- **Rollup + analytics double-count.** Both `rollup_window()` and
  `compute_line_kpis()` partitioned intervals by `(workstation_id,
  worker_track_id)` and summed them. Two concurrent tracks (real or
  phantom) reported 2× the wall-clock time; live data showed
  `effective_working_s=2796` for a 300s window. Fix: new
  `services/backend/app/services/intervals.merged_state_seconds` — a
  gaps-and-islands CTE computing the UNION of intervals per (workstation,
  state). Both callers go through the shared helper so live KPIs and the
  batch roll-up cannot drift again.

**Deferred:** the same CTE has a documented cross-window under-count — a
state that started before `window_start` and is still open at
`window_start` contributes zero to the current window. Comment left in
`services/backend/app/services/intervals.py` at the CTE. **Must be resolved
no later than Phase 3** (the "numbers you would defend in a management
meeting" phase — under-counting cross-window intervals is not a defensible
gap).

### 3 August 2026 — Bring-up and boot fixes (grouped)

Everything needed to make `docker compose up -d` succeed cleanly on
Stephen's tower and produce a login-able dashboard on first boot:

- **`cc9310f` real Celery healthchecks.** Backend Dockerfile's HTTP check
  was inherited by the two Celery services, which are not HTTP servers and
  so reported unhealthy forever. Overridden in compose: worker uses
  `celery inspect ping -d celery@$HOSTNAME`; beat scans `/proc` in Python
  (no `procps` in `python:3.12-slim`) for a process whose cmdline contains
  both `celery` and `beat`.
- **`ac7e34e` per-task NullPool asyncpg engine.** Every scheduled Celery
  task was failing with `Task ... got Future attached to a different loop`.
  Cause: the module-level asyncpg pool in `app/db/session.py` binds
  connections to the first event loop that touches them, and each Celery
  task's `asyncio.run(...)` opens a fresh loop. Fix: `app/workers/db.py`
  exposes `task_session()`, which builds a private
  `create_async_engine(..., poolclass=NullPool)` per invocation and
  disposes it in a `finally`. NullPool is a *structural* guarantee (open
  on checkout, close on checkin), not one that depends on `dispose()`
  being reached — important because Postgres shares this host with Odoo.
  FastAPI's session handling is unchanged.
- **`0faa739` CRLF-mangled entrypoint.** `.gitattributes` at the repo
  root normalizes `*.sh` and `Dockerfile` to LF regardless of the
  developer's `core.autocrlf`; `sed -i 's/\r$//'` added to the
  detection-engine Dockerfile as belt-and-braces for working copies
  already CRLF-mangled by Windows checkouts.
- **`4a7406c` frontend runs one consistent way.** Removed the `dev` stage
  from `frontend/Dockerfile`; compose now builds with `target: prod`,
  drops the source bind mount and the anonymous volumes that masked
  `/app/.next` and `/app/node_modules`, forwards `NEXT_PUBLIC_*` as build
  args so they actually bake into the client bundle. The previous
  configuration silently ran `next dev` against a masked `.next`, broke
  PostCSS auto-discovery, and let raw `@tailwind` directives reach
  webpack.
- **`1db0b29` eslint downgraded 9→8.57.1.** `eslint-config-next@14.2.13`
  only supports ESLint 7/8; npm 10's strict ERESOLVE blocked `npm install`
  and the frontend image never built.
- **`73c0aed` detection default is CPU.** Restructured
  `services/detection-engine/Dockerfile` into two targets, `cpu` (default,
  `python:3.12-slim`, torch CPU wheels installed in a separate cached
  layer, yolo11n baked into `/opt/models` at build time, an entrypoint
  script seeds `YOLO_MODEL_PATH` on first run) and `gpu` (preserved,
  fixed a latent python3.11-vs-python3.10 pip mismatch, reads
  `requirements-gpu.txt`).
- **`6d947e1` resource-fit for 4-core / 16 GB.** Explicit `deploy.
  resources.limits` per service; `celery-worker` concurrency 4→1;
  observability (`prometheus`, `grafana`, `loki`, `mlflow`) and `nginx`
  moved behind compose profiles so `docker compose up` starts only the
  core pipeline (`make up-core` / `make up-full`).
- **`73a32ae` TimescaleDB opportunistic, sidecar dropped.** `CREATE
  EXTENSION timescaledb` wrapped in a `SAVEPOINT` (via
  `conn.begin_nested()`); both `create_hypertable()` calls guarded by
  `if _timescale:`. Composite PK `(id, ts)` on `worker_events` and
  `production_events` — Timescale requires the partitioning column in
  every unique index. Unused `timescaledb` compose service, `ts_data`
  volume, and `TIMESCALE_URL` env removed.
- **`a16adf3` config + dep unblocks.** `email-validator`, `bcrypt==4.2.1`,
  `fakeredis`, `aiosqlite` pinned in the backend requirements;
  `httpx` added to tracking/detection where the code already imports it;
  `opencv-python-headless` to activity; `frontend/public/.gitkeep` so
  the Dockerfile's `COPY /app/public` succeeds; `.env.example` updates
  including `SEED_DEV_ADMIN=true` and `admin@local` → `admin@local.dev`
  (the old value was rejected by `EmailStr` on the login route).
- **`f359672` first Phase-1 boot bugs.** Added the `/api/v1/cameras/
  _internal` and `/api/v1/zones/_internal` endpoints the ingestion and
  tracking services expect (gated by `X-Internal-Token`, nginx blocks
  `/_internal` from outside). Frontend gained a login page, Zustand
  token store, 401-redirect fetch wrapper, and the Add Camera / Add
  Line / Add Factory forms that made real onboarding possible from the
  UI. Idempotent dev-admin bootstrap runs from the FastAPI lifespan.
- **`7ac776f` User dataclass field order.** Required fields (`email`,
  `hashed_password`) moved before defaulted ones so
  `MappedAsDataclass`'s generated `__init__` doesn't throw
  "non-default argument follows default argument" at import time.

### 3 August 2026 — Single-tenant `/tenants` scope tightening (`f7880ad`)

Production-AI is single-tenant. The `GET /api/v1/tenants` endpoint used to
accept any authenticated caller — a viewer or supervisor could list every
tenant's `name` and `slug`. Restricted to `super_admin` (the seeded
`admin@local.dev` still passes via `require_roles`' existing `superuser`
bypass). Seven new tests, including a regression pin asserting
`tenants.router.dependencies` does not contain the raw `current_user`
callable. Documented in `docs/steps/step9_tenants_scoping.md`.

### 3 August 2026 — Staging runbook (`12e2fc8`)

`docker-compose.staging.yml` override adds GPU device reservations
without altering the base compose (which stays CPU-friendly for laptops
and CI). `docs/runbooks/staging_deploy.md` walks a fresh factory-adjacent
GPU box from `nvidia-smi` through to seeing real frames in `worker_events`
— including the explicit "do NOT run `make seed`" note (its
`nvr.local` Cam-101 would poison a real deployment) and the correct
onboarding flow via the `/lines` and `/cameras` dashboard forms.

### 3 August 2026 — Step 0–8 audit remediation (pre-Phase-0 baseline)

Before this branch's Phase-0 work started, an incognito audit-and-fix
session closed all twelve findings from the inherited-code audit (Steps
0–8; per-step working records in `docs/steps/step2_*.md` through
`step8_*.md`, plus `SESSION.md` for the full narrative and
`docs/audit/Production-AI_Code_Audit.docx` for the original findings).
Highlights: correct ByteTrack aging + Kalman prediction (Step 2), duration
+ fairness analytics with tenant scoping (Step 3), tenant/role claims in
JWTs (Step 4), Redis consumer groups on every stream (Step 5), debounced
de-duplicated idle alerting + production_records roll-up (Step 6),
security hardening (heartbeat authenticated, WebSocket tenant filter,
refresh-token rotation — Step 7), and the data-flywheel Step 8 (opt-in
flagged-frame capture, optical-flow machine-running signal fused into
the FSM). Everything above 3-Aug is *this branch's* work on top of that
baseline.
