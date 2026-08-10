# Production-AI — Roadmap

**Owner:** Stephen Nderitu
**Status:** Phase 0 complete — Phase 1 next
**Last updated:** 4 August 2026 (pm)

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
- [x] Confirm the dashboard renders and login works
- [x] First camera configured and streaming
- [x] `person` detections confirmed in `worker_events`

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

### 10 August 2026 — Activity FSM re-keyed to workstation; AWAY reachable

**Bug (Phase 3 blocker sitting inside Phase 1).** `productivity = WORKING / (WORKING + IDLE + AWAY)` returned the same number for a station worked continuously and a station empty for three hours, because AWAY events were never emitted. `ActivitySignals.present` defaulted to `True` and the activity handler iterated the frame's `payload["tracks"]` — a vanished operator's track simply stopped arriving, `fsm.update()` was never called for it, and the FSM never confirmed AWAY. Absence is a property of the **seat**, not of the track.

**Fix.**
- **Tracking engine** (`services/tracking-engine/tracking/main.py`) now publishes a `workstations` roster alongside each frame's tracks on `stream:tracks:<camera_id>`. The roster is the set of distinct `workstation_id` values from `cam_zones` — computed in the same block that feeds `assign_workstation`, with a structural invariant comment so the two cannot drift. Roster is any-kind (not seat-only) because `POST /api/v1/zones` accepts any kind for any workstation and the zone editor will use it; a machine zone drawn before a seat zone would open a silent-drop hole under a seat-only roster.
- **Activity engine** (`services/activity-engine/activity/main.py`) FSM registry is now `dict[str, WorkerActivityFSM]` keyed by `workstation_id`. It iterates the ROSTER, sets `present=True` if a track is assigned there this frame (highest-conf wins under multi-track occlusion, logged at debug), else `present=False`. The existing 60s time-weighted debounce handles absence exactly as it handles every other state.
- **Pipeline-outage guard.** If the gap between successive tracks payloads exceeds `3 × (1 / DETECTION_TARGET_FPS)`, the handler holds state and logs a warning. Recording an operator as AWAY because ffmpeg dropped is exactly the kind of number that destroys dashboard trust.
- **Aisle walkers.** Tracks with `workstation_id=None` never drive any FSM. A person walking the aisle is not an operator.
- **Registry bound.** Workstations no longer on the roster (seat deleted, seat re-zoned) get their FSM and centroid evicted. This also closes the old unbounded-growth issue that track-keyed dicts had under track-ID churn.
- **`worker_track_id` becomes a sentinel.** The FSM is now workstation-keyed, so there is no owning track for AWAY (the absence of a track IS the event); Option B was not viable — LEAD partitioned by `(workstation_id, worker_track_id)` would leave the WORKING interval unterminated while AWAY started a fresh partition, double-counting at exactly the transitions this fix creates.
  - `intervals.py`: LEAD now partitions on `workstation_id` alone.
  - `rollup.workers_idle_too_long`: ranks per workstation (a workstation belongs to exactly one camera per the non-nullable FK, so camera_id was redundant too).
  - `workers/tasks.check_idle_workers`: dedup key changed to `"{camera_id}:{workstation_id}"` — per-seat is the correct semantics, not a migration artefact.
  - `worker_events.worker_track_id` retained as audit-only metadata; docstring updated on the model.
- **Historical rows.** Not backfilled. Historical windows will still show zero AWAY. The brief originally said "existing worker_events rows are unaffected" — that was wrong. Repartitioning LEAD by workstation_id alone changes how pre-fix rows re-summarize: roughly three weeks of existing data will show shifted WORKING/IDLE figures on any per-workstation query, because chains that previously ran per-track now run per-seat. Expect it, do not read it as a regression. The union-of-intervals safety net stays for that and for accidental same-ts double writes.

**Tests (7 new + 4 updated).**
- New `services/activity-engine/tests/test_state_machine.py` — first tests the FSM has ever had. Covers: sustained absence → AWAY; brief absence < debounce → no flip; AWAY→WORKING on return; `machine_running=True` + `present=False` still resolves to AWAY (the `_candidate` present-check wins); time-weighted vote ignores a 30fps AWAY burst against a 50s WORKING baseline; `total >= 3.0` evidence floor guard; stable WORKING happy path.
- Updated: `test_intervals_overlap.py`, `test_rollup.py`, `test_analytics.py` — LEAD partition switched to `workstation_id` alone; two assertions that encoded the old per-track chaining were re-derived under the new partition (WAITING slice terminates at the next event; two-track chains no longer skip over an interleaved AWAY).

**Reporting scope — what actually works today.** AWAY is now emitted for absences **shorter than the query window**. Two known holes remain, and productivity should NOT be described as reliable until both are closed:

1. **Long absences.** Their single AWAY event falls outside the queried window and the intervals CTE only sees `ts >= window_start`. The longer a seat is empty, the more likely its lone AWAY event has already fallen out of any short recent window — the true-north case (a three-hour empty seat over a 10-minute recent window) is exactly the shape this misses.
2. **Pipeline outages.** During an outage no `worker_state_changed` events are written, and `intervals.py` closes each interval with `LEAD(ts)` — so the entire gap gets attributed to whichever state was last recorded. A 30-minute ffmpeg drop on a seat that was WORKING reads as 30 minutes of WORKING. The outage guard in the activity engine protects the FSM's debounce vote; it does nothing to the duration query.

Both are the same failure family: the interval math has no way to say what an *absence of events* means, and neither hole is fixable in the activity engine. **Fold both into the next task**, framed as *"what does a gap in worker_events mean?"* rather than only *"states open before window_start"*. When the fix lands, do it as the CTE modification, not the Deferred #3 snapshot-row idea: snapshot rows touch every downstream consumer and break the transitions-only semantics lesson #2 rests on; the CTE fix is contained in one function. **Do not attempt this without a design proposal first** — the obvious move (synthesising a gap marker into `worker_events`) is Deferred #3 in disguise.

**Free accuracy evidence for later, do NOT build here.** AWAY co-occurring with `machine_running=True` is a strong false-AWAY signal (operator present but undetected) — worth surfacing on the Phase 3 event-review screen. Logged; not implemented.

**Known items filed alongside this fix.**
- Activity-engine `amain()` never cancels stale camera tasks (mirror of the same shape in tracking). Not folded in here because distinguishing "camera task genuinely gone" from "camera task holding state through a pipeline outage" — the exact semantics the outage guard depends on — is its own design question.

**Migration.** None. Code-only.

**Deploy notes.**
1. Acknowledge any open `worker_idle` alerts before deploying — old payloads carry the track-shaped dedup key and would collide with the new shape until they auto-close.
2. **Deploy order is not optional: tracking-engine must be updated at or before activity-engine.** If activity-engine restarts first, it sees `stream:tracks` payloads with no `workstations` key and holds state (with a rate-limited warning) — the engine emits nothing until tracking-engine also rolls. Handler holds state rather than wiping it, and empty rosters do NOT evict; both are guarded and tested. See HANDOFF hard-won lesson on this.
3. **Expected observation, not a regression.** Historical `worker_events` rows will re-summarize under the new LEAD partition (workstation-alone). Any per-workstation WORKING/IDLE figure on the last ~3 weeks of data will shift — sometimes significantly, where two tracks were previously chained separately at one seat. Do not read the shift as a regression.

**Follow-up commit** (`fix(activity): outage-guard threshold; empty-roster guard; handler tests`): review of the initial commit surfaced two blockers before deploy. (1) Outage threshold was derived from `DETECTION_TARGET_FPS` (0.75s at 4fps) which would trip on any pipeline running below its target rate, silently disabling the engine — replaced with an absolute 10s and, on trip, the handler processes the frame normally after pruning FSM windows so the gap contributes no vote weight (was: skipping the frame, which left the last pre-outage sample crediting the full gap for gaps shorter than the debounce). (2) Empty `workstations` in a legacy or partial-deploy payload used to enter the eviction loop and wipe every FSM without a log; now legacy-key-missing holds state with a rate-limited warning, and even an explicitly-empty roster does not evict. Handler-level tests (10 cases) added covering both blockers + roster iteration + multi-track selection + low-frame-rate resilience.

Commits: `feat(activity): key FSM to workstation, make AWAY reachable`, `fix(activity): outage-guard threshold; empty-roster guard; handler tests`.

### 4 August 2026 (pm) — Promotion pipeline live end-to-end

Onboarding loop `discover → review → promote` proved out for the first
time against real data. What landed:

- **`promote_zones` maiden run.** Discovery run #3's `S7` (114 dwell-s,
  polygon `[[566,182],[666,182],[666,282],[566,282]]`) approved and
  promoted as **Station 6**. Atomic single-transaction insert, overlap-
  checked against every existing zone on the camera and between new
  polygons.
- **False-positive caught in review.** A second `S<n>` blob from the
  same run — one hot cell, 156 dwell-s, aisle-adjacent — was rejected
  as "post-lunch standing chatter". Reviewer heuristic recorded so it
  survives the next onboarding: **real seats = multi-cell high-dwell
  clusters. Single-cell, aisle-adjacent, low-total-dwell = standing
  person, not a seat.** discover_zones's structural filters (min box
  size, existing-zone dedup, far-cutoff) don't catch this — it needs
  the reviewer.
- **Audit trail:** each promotion run writes `promotion_log.json`
  alongside `decisions.json` — camera_id, line, frame dims, promoted
  rows (workstation_id, zone_id, layout_version, `[created]`/`[reused]`
  tags, polygon), and rejects with their `reason`. Full paper trail
  per run.
- **UTF-8 BOM bug caught by the same smoke test.** Windows PowerShell's
  `Set-Content -Encoding utf8` writes a BOM (`EF BB BF`) that Python's
  built-in `json` module refuses. Fixed in `ebc495f` — `load_json_file`
  helper reads with `encoding='utf-8-sig'` (strips a leading BOM if
  present, no-op otherwise), both file loads centralised, malformed
  JSON now surfaces as a clean `STOPPED: <path>: <reason>` line and
  exit 2 instead of a raw traceback. +4 unit tests
  (`test_promote_zones.py` → 24 total, backend suite 54 pass / 1
  pre-existing bcrypt env quirk).
- **Per-camera resolution correctness landed with `promote_zones`
  (`fd103e1`).** `discover_zones.py` audited: `--far-cutoff-frac 0.28`
  replaces the fixed 200 px cutoff (720p → 200 px, **1080p → 302 px**);
  outputs now go to `/tmp/discover_zones/<camera_id>/` so parallel or
  successive runs on different cameras never overwrite each other;
  frame dims already come from the payload. **Ready for the GD50 at
  1920×1080** without a code change.
- **Runbook:** `docs/runbooks/camera_onboarding.md` — the one-page
  loop (add camera → discover during working hours → review overlay
  in chat → write `decisions.json` → promote → verify with the
  `worker_events` join). Includes the one-station-one-camera rule for
  overlapping views.
- **`insert_stations_4_5.py`** carries a SUPERSEDED banner pointing at
  `promote_zones`; kept for the historical record of the first
  camera, not to be extended.

### 4 August 2026 — Row 1 near-field mapped: Stations 4 & 5 inserted from dwell data

Discovery run #2 (30 min, mid-morning, **20,422 samples / 123 distinct
tracks**) both validated and extended the seat map:

- **Station 1** accumulated **1,477 dwell-seconds inside its polygon** —
  disproving yesterday's "polygon too high" hypothesis. The zone is
  positioned correctly; yesterday's near-zero hit count was a traffic
  question, not a geometry one.
- **Station 2** re-validated on the same run (dwell falls squarely inside
  its polygon).
- **Stations 4 and 5** inserted from the run's two new dwell clusters via
  the idempotent script (commit `072b869`). Polygons match the measured
  clusters. **Station 5 merges two blobs** that `discover_zones` split
  because the seat sits at the frame bottom (y=720 = frame height) and
  the operator's foot point straddled the clamp; the polygon runs to
  y=720 to reunite the seat.
- **All five seat zones on Row 1 near-field are now empirically backed**
  by measured operator dwell, not eyeballing.

Still unmapped, deferred to future discovery passes:

- The right row and the upper-left cluster. Rerunning `discover_zones`
  costs nothing (passive tail, no writes) so we do it when those seats
  are occupied — the tool is occupancy-dependent, not clock-dependent.

Known quirk to resolve when the zone editor lands (Phase 1):

- `POST /api/v1/zones` auto-bumps `Workstation.layout_version` before
  insert, so the *first* zone created for a fresh workstation via the API
  lands at `layout_version=2`, not 1. The one-off script sidesteps this
  by setting the field explicitly to match Stations 1–3. The proper fix
  is in the endpoint: only bump when there is already a zone for that
  workstation (i.e. when this insert is genuinely a new revision).

### 3 August 2026 — Phase 0 exit test passed (bring-up findings)

The three remaining Phase 0 checkboxes ticked, verified live on the factory
tower today:

- **Dashboard renders and login works.** `POST /api/v1/auth/login` returned
  an access+refresh pair for the seeded admin; the dashboard rendered under
  the AuthGate.
- **First camera configured and streaming.** Camera
  `f524b92c-f155-4792-9ca0-908ddb7c1dc4`; frames stream sitting at its
  MAXLEN cap (steady state, no unbounded growth); ingestion heartbeats
  landing on `POST /cameras/{id}/_internal/heartbeat`.
- **`person` detections confirmed in `worker_events` (exit test).**
  `select count(*) from worker_events` climbed 1028 → 1102 in ~8 min with
  `max(ts)` seconds old at query time — the test the roadmap defines for
  Phase 0.

Three findings surfaced during verification, each recorded here so nothing
gets lost in the transition to Phase 1:

- **All `worker_events` rows have `workstation_id = NULL`.** No zones are
  defined for the seeded camera yet, so `tracking-engine`'s
  `assign_workstation()` returns `None` for every track and analytics /
  per-station KPIs stay empty. This is not a bug — it is the next work
  item, exactly the reason Phase 1's zone editor is the highest-value
  screen in the system. **Fix in Phase 1** (zone editor + `POST /zones`
  through the UI).
- **`redis --force-recreate` at ~12:15 UTC broke streambus consumers.**
  Consumer services logged connection errors after Redis restarted; the
  pipeline resumed on its own and `event-engine` lag returned to 0 within
  a couple of poll cycles. Recovery worked, but the reconnect path isn't
  covered by an automated test today — **candidate hardening item**:
  simulate a Redis bounce in a service integration test and assert every
  consumer (`detection-engine`, `tracking-engine`, `activity-engine`,
  `event-engine`) resumes without operator intervention.
- **Celery healthchecks now green** after rebuild to `cc9310f`
  (`inspect ping -d celery@$HOSTNAME` for the worker, `/proc` scan for
  beat). Confirmed via `docker compose ps` — previously both services
  reported unhealthy indefinitely because the backend Dockerfile's HTTP
  healthcheck was inherited unchanged.

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
