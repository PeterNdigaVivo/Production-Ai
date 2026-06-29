# SESSION.md — Production-AI Remediation Project: Handoff Briefing

> **Read this first.** This was an **incognito conversation**, so the next
> session starts with zero memory of any of it. This file is the complete
> briefing: the rules we work under, everything built so far, the decisions and
> their reasoning, the current state, and exactly where we pick up next.
> Treat it as authoritative. When in doubt, the per-step docs in
> `docs/steps/` and the code itself are the source of truth.

---

## 0. THE TL;DR (read this even if you read nothing else)

- **Project:** "Production-AI" — a multi-tenant computer-vision platform that
  monitors garment sewing lines via CCTV (Hikvision RTSP). The user, **Stephen**,
  **inherited** this codebase and is taking it over to deploy at a **fabric
  company**: ~**20 sewing lines**, each with ~**6 sewing machines + a supervisor**.
- **Mission:** turn the inherited code into a clean, correct, resource-conscious,
  deployable starting point — then build toward the end-game monitoring goals.
- **End-game goals:** idle time, away time, supervisor movement, **time per piece
  (cycle time)**, **time for a cloth to complete**, and **alerts** when thresholds
  are violated. Camera-only — **no RFID/barcode tags** (user ruled them out).
- **Where we are:** Steps 0–8 are **DONE and verified**. All **12 audit findings
  are CLOSED**. The current deliverable is
  `Production-Ai-claude-happy-hamilton-otnhj9_step8.zip`.
- **What's next:** **Step 9 = piece-counting / cycle-time** (the headline feature).
  It is **gated on the user bringing real footage** from the factory. The user is
  visiting the factory to capture stills/clips and will return with them. A
  field checklist was already produced: `factory_visit_checklist.md`.
- **Immediate next action when user returns:** look at the **camera stills** they
  bring, judge whether the installed cameras can see a "bundle" of fabric well
  enough for piece-counting (placement/resolution/fps), then build Step 9.

---

## 1. GROUND RULES (user-stated, binding — honour these)

1. **Work as equals / a team.** Collaborative tone, no servility.
2. **Share updated files as a ZIP** with correct folder structure that extracts
   directly over the project.
3. **Search the internet** for best solutions when relevant (we used this for the
   SQLAlchemy window-function syntax, supervision library status, etc.).
4. **Discuss ideas before building** — especially for open-ended/ML work where
   there's no single "correct" answer. (We did a full Path-A-vs-B discussion
   before Step 8.)
5. **Main goal:** improve/develop the app to be **faster, lighter, max output,
   and improve the training process** — don't stray into scope creep.

### Working style that has worked well (keep doing this)
- **Claude runs ALL verification** — the user cannot run anything locally (no GPU,
  no cameras connected yet, tower runs Windows + Docker Desktop + WSL2).
- Every step: re-read the real code → discuss/decide → build → **verify in the
  sandbox** (real or fake DB, fakeredis, parse/compile checks, PG-dialect compile)
  → write a `docs/steps/stepN_*.md` working-record → assemble → integrity-check →
  zip → present.
- **Be honest about caveats.** Every step doc has an explicit "honest caveats"
  section (sandbox limits, what's verified vs. needs server integration). Keep
  this discipline — the user values candour over polish.
- **Flag decisions explicitly**, especially design trade-offs, and explain the
  reasoning. The user has pushed back / asked for reasoning before and appreciates
  it.

---

## 2. DELIVERY CONVENTIONS (firm — follow exactly)

- **ZIPs are the COMPLETE project**, not deltas. (Originally I shared deltas; the
  user noticed; since Step 5 every zip is the full ~123 original files + all
  additions.) The user explicitly likes this — it's deployable as-is.
- ZIP root is the project folder `Production-Ai-claude-happy-hamilton-otnhj9/`
  so it extracts directly over the project.
- **Integrity check every time:** verify **zero original files are missing** from
  the assembled tree before zipping. (We diff the original file list against the
  final tree — it has caught real assembly bugs.)
- All docs live under a single root **`docs/`** folder: `docs/audit/`,
  `docs/steps/`, and the original `docs/architecture/` (left untouched).
- Output ZIPs go to **`/mnt/user-data/outputs/`**.
- Clean `__pycache__`, `.pytest_cache`, `*.pyc` before zipping.

### The assembly script
`/home/claude/assemble.sh` rebuilds `final/` from the **complete original** + all
step overlays in order. **This is the reliable way to assemble** — it was written
after an early manual-merge bug. To add Step 9, append its overlay block to this
script (follow the existing pattern), update `/home/claude/final_docs_readme.md`
(the docs index), then run `bash assemble.sh`.

**IMPORTANT for next session:** the sandbox filesystem **resets between
sessions**. The `/home/claude/step*` staging dirs, `final/`, `project/`, and
`assemble.sh` will likely be **GONE**. You will need to **re-extract the latest
zip** (`Production-Ai-claude-happy-hamilton-otnhj9_step8.zip` — ask the user to
re-upload it, or it may be in `/mnt/user-data/uploads/`) to get the current
state, then work forward from there. Don't assume the staging dirs persist.

---

## 3. THE PROJECT — ARCHITECTURE

**Pipeline:** RTSP ingestion → detection (YOLO) → tracking (ByteTrack) → activity
FSM → event-engine → Postgres/TimescaleDB → FastAPI → Next.js dashboard, with
**Redis Streams** as the bus. Each service builds from its **own Docker context**
(`context: ./services/<engine>`) — this matters: shared code must be **vendored**
into each service, not imported across contexts.

**Services** (under `services/`):
- `video-ingestion` — RTSP workers, publish frames to `stream:frames:<cam>`.
- `detection-engine` — YOLO inference; `stream:frames` → `stream:detections`.
- `tracking-engine` — ByteTrack + zone assignment; `stream:detections` →
  `stream:tracks` + `stream:events`.
- `activity-engine` — per-(camera,track) FSM; `stream:tracks` → `stream:events`.
- `event-engine` — persists events to Postgres.
- `backend` — FastAPI: auth, analytics, alerts, websockets, internal routes,
  Celery workers (rollup + alert tasks).
- `common/` — shared (vendored) helpers: `streambus/`, `capture/`, `machine_flow/`.
- `frontend/` — Next.js dashboard.

**Tenancy chain (memorise — used everywhere for scoping):**
`worker_events.workstation_id → workstations.line_id → production_lines.factory_id
→ factories.tenant_id`.

**Worker states:** `WORKING | IDLE | AWAY | WAITING_FOR_INPUT | BREAK | UNKNOWN`.
**The fairness rule (core to the product):** `WAITING_FOR_INPUT` is **excluded
from the productivity denominator** — a worker is never penalised for an empty
input tray. Productivity = `WORKING / (WORKING + IDLE + AWAY)`.

**Key tech/versions:** FastAPI 0.115, SQLAlchemy 2.0.35, asyncpg, Celery 5.4,
python-jose, passlib[bcrypt], redis 5.0.8; numpy 2.1.1, scipy 1.14.1,
opencv-python-headless 4.10, ultralytics 8.3.20, torch 2.4.1.

---

## 4. THE AUDIT (Step 0) — all 12 findings now CLOSED

`docs/audit/Production-AI_Code_Audit.docx` (+ `ghost_track_demo.png`). 12 findings
ranked by data-corruption severity. Status after Step 8:

| # | Finding | Severity | Closed in |
|---|---|---|---|
| 1 | ByteTrack aging broken (ghost tracks) | CRITICAL | Step 2 |
| 2 | Analytics counts events not durations; no fairness rule | CRITICAL | Step 3 |
| 3 | JWT omits tenant_id + roles → RBAC/multitenancy inert | CRITICAL | Step 4 |
| 4 | Analytics no tenant/line filter → cross-tenant leak | HIGH | Step 3 |
| 5 | Stream consumers use XREAD '$' not consumer groups | HIGH | Step 5 |
| 6 | Idle alert fires every transition (spam) | HIGH | Step 6 |
| 7 | Heartbeat endpoint unauthenticated | MED | Step 7 |
| 8 | production_records rollup is a no-op | MED | Step 6 |
| 9 | Dev-admin auto-seeds weak creds | MED | Step 8 |
| 10 | WebSocket unauthenticated + not tenant-filtered | MED | Step 7 |
| 11 | Refresh tokens not rotated/revocable | LOW | Step 7 |
| 12 | Activity FSM vote is frame-count not time-weighted | LOW | Step 8 |

---

## 5. WHAT'S BEEN BUILT — STEP BY STEP

Each step has a full write-up in `docs/steps/`. Summary + key decisions below.

### Step 1 — Synthetic test harness (`tools/harness/`)
Camera-free/GPU-free ground-truth testing. Generates synthetic scenes with known
ground truth, runs the tracker, scores it. Modules: `scene.py`, `detector.py`
(stub + real-YOLO toggle via `HARNESS_DETECTOR` env), `render.py`, `publisher.py`
(Redis frames in real format), `tracker_runner.py`, `overlay.py`, `cli.py`. Has a
README with Windows/WSL2 instructions. Reproduced Finding 1 visually.

### Step 2 — Tracker fix (Finding 1)
Did a **head-to-head benchmark** via the harness: current(buggy) vs fixed
(Kalman + correct aging, numpy/scipy only) vs supervision(oracle). Result: fixed
matches supervision accuracy, **4.6× faster, ZERO new deps**. **Decision: FIX,
not replace** with supervision (which is mid-migration/deprecated and heavy).
Promoted to `services/tracking-engine/tracking/bytetrack.py` (drop-in, same
interface). Regression test pins the old bug.

### Step 3 — Analytics durations + fairness + tenant scoping (Findings 2, 4)
Created `services/backend/app/services/analytics.py` (shared core; uses **LEAD()
window function** to reconstruct state-interval durations; excludes
WAITING_FOR_INPUT = the fairness rule; tenant/line scoped). Rewrote the analytics
endpoint (thin, 404 on cross-tenant). **Decision: logic in a shared service
module** so live API and batch rollup never diverge. Worked example: 75% fair vs
60% naive productivity. Verified vs real SQLite DB + PG compile check.

### Step 4 — Auth claims (Finding 3)
Created `services/backend/app/services/auth.py` (`build_user_claims` loads roles
via UserRole→Role + tenant_id). Login embeds claims; **refresh RE-LOADS from DB**
(so revoked roles take effect). `deps.py` already read these claims — they were
just never populated. Made Step 3's token-level tenant check activate
automatically.

### Step 5 — Consumer groups (Finding 5)
Created shared `streambus/consumer.py` (XREADGROUP, XACK, pending-recovery on
restart, responsive shutdown by racing the blocking read against a stop event).
**Two backlog policies:** `NEWEST` for frames→detection (drop stale frames, keep
freshest — live video), `ALL` for detections→tracks & tracks→activity (never
drop). **Decision: VENDORED into each engine** (each builds from own Docker
context); canonical + `sync.sh` + tests in `services/common/streambus/`.
**KEY CONSTRAINT documented:** stateful stages (tracking/activity) must **shard by
camera** across replicas — never have two replicas consume the same camera's
stream, or per-camera state corrupts.

### Step 6 — Alert debounce + rollup (Findings 6, 8)
Created `services/backend/app/services/rollup.py` (`rollup_window` — per-
workstation window aggregation reusing Step-3 duration logic, **clamps interval to
window end**, **idempotent** delete-then-insert; `workers_idle_too_long` —
ranked-newest query). Rewrote Celery `tasks.py`: real rollup of last completed
5-min window; `check_idle_workers` periodic **de-duplicated** alert. **Decision:
idle alerting is PERIODIC not event-driven** — a worker who stays idle emits no
further transitions, so only a periodic check can catch "still idle." Stripped
idle-spam from the event-engine (now persist-only).

### Step 7 — Security (Findings 7, 10, 11)
- **#7:** moved heartbeat to internal router as `/cameras/{id}/_internal/heartbeat`
  (gated by `require_internal_token`). **CAUGHT BUG:** first naming
  `heartbeat_internal` would NOT match the nginx `/_internal(/|$)` block —
  verified the regex, renamed to `/_internal/heartbeat` so nginx blocks it too.
  Ingestion updated to send `X-Internal-Token`.
- **#10:** WebSocket — token via query param validated **before** accept; tenant
  filter via cached workstation→tenant map; **fail-closed**; superuser sees all.
  Frontend `LiveEventFeed.tsx` updated to send token.
- **#11:** `services/backend/app/services/token_store.py` (Redis jti store,
  rotation + revocation, **no DB migration**). `make_refresh_token` now returns
  `(token, jti)` — **updated the original `test_security.py`** for the new
  signature (a contract I changed). Added `/auth/logout`.

### Step 8 — Data flywheel + fair activity (Findings 9, 12 + Phase-2 foundation)
THE PIVOT from fixing to building toward the end-game. See §6 for the strategy
discussion that preceded it.
- **#9:** `seed_dev_admin` now requires `SEED_DEV_ADMIN=true` AND
  `ENVIRONMENT=development`; warns on weak default. Config flag added.
- **#12 + FSM:** replaced frame-count voting with **TIME-WEIGHTED voting** (each
  sample weighted by seconds-to-next-sample; winner needs >50% of window TIME and
  ≥3s total). Verified: resists 5-frame/0.5s bursts, honours 40s sustained idle.
- **Machine-running detector** (`machine_flow.py`): optical-flow (Farneback) in
  the machine zone → "is the machine running." **Classical CV, no training, works
  day one.** `calibrate_threshold()` derives a per-camera threshold from a clip —
  **THIS NEEDS THE USER'S CALIBRATION FOOTAGE.** Default is conservative until
  calibrated.
- **Wired the full chain:** detection (only engine with raw pixels) computes a
  per-workstation `machine_running` map → tracking forwards it → activity feeds it
  to the FSM. **Behind `MACHINE_FLOW_ENABLED` (off by default); degrades safely
  when off** (no key → defaults False → no crash — verified).
- **Capture pipeline** (`capture/writer.py`): the data flywheel. Saves only
  **uncertain** frames (ambiguous confidence / unstable state / count anomaly /
  cycle-boundary) with `labeled:false` JSON sidecars, rate-limited per camera,
  **OFF by default for privacy** (`CAPTURE_ENABLED`). Wired into detection.
- **Training lifecycle:** `ml/labeling/export_dataset.py` (labelled captures →
  YOLO dataset), `ml/training/registry.py` (MLflow register + Staging→Production
  promote — the missing link). Deliberately **did NOT rewrite** the existing
  `ml/training/train_detection.py` (it's decent) — only extended.
- **Operational runbook:** `docs/steps/step8_runbook.md` — capture→label→export→
  train→promote→deploy loop.

---

## 6. THE END-GAME STRATEGY (critical context for Step 9+)

After a detailed discussion, the user confirmed:
- Footage **can be recorded soon**; **camera-only, NO tags** (RFID/barcode ruled
  out — this is the hardest regime for cycle-timing).
- **"Both equally — no hard deadline"** for monitoring-vs-piece-counting.

**Chosen path: PATH A — deploy-the-measurable-now + capture training data as a
side effect, then train piece-counting later on curated real data.** Reasoning:
- ~**60% of goals** (idle/away/working/supervisor/alerts) are achievable now with
  tuning — this half is built and fair.
- ~**40%** (piece counting, cycle time, end-to-end cloth completion) needs
  **labeled footage that doesn't exist yet**.
- Path A de-risks: each piece delivers value independently, and the capture
  pipeline **solves the labeling bottleneck** by pre-filtering the hard frames —
  instead of hand-labelling hundreds of raw hours. Path B (go straight for
  piece-counting) is the **riskiest quadrant** camera-only, with manual labelling
  as the stall point.

**Reality of the hard goals (be honest with the user about these):**
- "Time per piece" needs a custom **`bundle` detector** (new class — generic
  models have never seen cut-fabric bundles) + a per-station **cycle FSM**.
- "Cloth completion end-to-end" across ~6 stations is the **hardest** camera-only.
  Plan is to **infer it statistically** (cycle time per station + line flow),
  NOT promise perfect per-garment tracking across 6 cameras (bundles look alike,
  pile up, get occluded). This is where a tag would normally help, but tags are
  ruled out.

---

## 7. CURRENT STATE & CURRENT DELIVERABLE

- **Latest zip:** `/mnt/user-data/outputs/Production-Ai-claude-happy-hamilton-otnhj9_step8.zip`
  (complete project, 184 files, all 12 findings closed). This is the **deployable
  base**.
- Other outputs present: `Production-AI_Code_Audit.docx`, `ghost_track_demo.png`,
  `step2_before_after.png`, and **`factory_visit_checklist.md`** (the field guide
  the user is taking to the factory).
- **Test status: 32 tests passing** in sandbox: 17 backend standalone (analytics,
  auth, rollup, security_step7), 3 streambus, 5 harness, 7 step8.
- **Known sandbox limitation (NOT a code bug):** `test_security.py` and
  `test_health.py` can't *collect* in the sandbox because they import the full app
  config chain (needs pydantic/passlib/etc. installed). They work on the server.
  The standalone tests avoid app-config imports on purpose. Also a passlib/bcrypt
  **version clash in the sandbox** prevents password-hashing from running locally
  — verified the JWT functions by stubbing passlib. None of this affects the
  shipped code.

---

## 8. STEP 9 SCOPE — what we agreed to build next (gated on footage)

**Step 9 = piece-counting / cycle-time.** Three parts:
1. **`bundle`/`piece` detector** — new custom YOLO class. **Requires the user's
   training footage.** The Step-8 capture pipeline will grow the dataset over
   time, but the FIRST model needs a deliberately-recorded seed set.
2. **Per-workstation cycle FSM** — bundle at input → machine running (the Step-8
   optical-flow signal) → finished piece to output = one cycle; elapsed = time
   per piece. **Reuses zones + machine-flow + duration machinery already built.**
3. **End-to-end cloth completion** — inferred statistically from per-station cycle
   times + line flow (NOT per-garment vision tracking across 6 cameras).

### What we need from the user (already in `factory_visit_checklist.md`)
- **MOST IMPORTANT first:** a few **stills** of a station with a fabric bundle
  visible, + camera specs (resolution, fps, mounting). This tells us **same-day**
  whether the installed cameras can carry piece-counting.
  - **The bar:** bundle should be **~40–50+ px across**, ≥720p (1080p comfortable),
    ≥5 fps steady, camera looking **down on the work area** (not a wide room shot).
  - User said cameras are **"fairly close to each station"** — promising; a still
    confirms it.
- **(A) Training footage:** 2–5 min clips from **8–10 stations across 2–3 lines**,
  covering fabric/lighting variety, bundles in all states. Variety > volume.
- **(B) Validation footage + GROUND TRUTH:** 3–4 stations, 30–60 min, with
  someone **noting actual piece-completion times for ~20–30 pieces** (phone
  stopwatch + notepad). **This is the easy-to-skip, must-not-skip item** — without
  it we can build a cycle detector but can't certify its accuracy.
- **(Bonus) Calibration clip:** 20–30 min/camera of normal operation with some
  known machine-on/off stretches → calibrates the Step-8 machine-flow threshold.

### Can the user use installed CCTV/NVR recordings?
Yes, and they should try (free, already there). Caveats to check: camera
**placement** (must see the work area, not a wide room shot — the dealbreaker),
**fps** (motion-triggered/erratic complicates timing), **compression** (heavy CCTV
compression smears the fabric texture the machine-flow signal needs), **export
format** (must export real mp4/H.264, not view-only NVR format), **retention**.
Wide cameras still drive idle/away/supervisor fine — only piece-counting is
placement-sensitive; those lines might need one work-area camera each.

---

## 9. IMMEDIATE NEXT ACTIONS (when the user returns with footage)

1. **Re-establish state:** the sandbox reset, so get the latest zip
   (`..._step8.zip`) — check `/mnt/user-data/uploads/` or ask the user to
   re-upload — and extract it to `/home/claude/project/...` to work from.
2. **Look at the stills first.** Judge bundle visibility against the bar in §8.
   Give the user a same-day verdict: installed cameras OK, or need work-area
   cameras on some lines.
3. If footage is usable: **build Step 9** — start with the bundle detector
   (label seed set → train via existing `train_detection.py` → register/promote),
   then the per-station cycle FSM (reuse zones + machine-flow + duration logic).
4. Keep the conventions: re-read real code, discuss before building, verify
   everything, honest caveats, complete-project zip with integrity check, step
   doc, update the docs index, present.
5. If no GPU is available to actually train, build + verify the pipeline logic and
   hand the user a runbook to run training on their tower (as we did in Step 8).

---

## 10. USER CONTEXT / PREFERENCES

- **Name:** Stephen. Treats this as a peer collaboration ("work as equals").
- Cannot run code locally during our sessions (no GPU/cameras wired yet; tower is
  Windows + Docker Desktop + WSL2). **Claude does all verification.**
- Values **honesty about limitations** and **explicit reasoning for decisions**
  over confident polish. Has appreciated when I caught my own bugs (the nginx
  regex gap, the delta-vs-complete-zip issue) and flagged trade-offs.
- Communicates concisely, sometimes informally; is fine with the poll/elicitation
  UI for decisions.
- Is about to **visit the factory** to capture footage and will return with it —
  that visit is the gate for Step 9.

---

*End of handoff. Next session: read this, re-acquire the latest zip, then start
from §9. Good luck.*
