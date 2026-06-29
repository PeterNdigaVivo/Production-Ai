# Step 1 — Synthetic Test Harness

This is the **safety net** from the audit: a camera-free, GPU-free way to run the
Production-AI pipeline against **known ground truth**, so we can prove a bug is
present and later prove it is fixed — with data, not opinion.

It is **additive only**. Nothing in the existing `services/` tree is modified.
Everything lives under `tools/harness/`.

---

## What it gives you

| Tool | What it does | Needs Redis? | Needs GPU? |
|---|---|---|---|
| `report` | Runs the **current tracker** over a scripted scene and prints metrics vs ground truth | No | No |
| `frame` | Renders one annotated PNG (boxes + track IDs) so you can *see* the bug | No | No |
| `overlay` | Renders an annotated **.mp4** of a whole run | No | No |
| `publish` | Streams synthetic frames into **Redis** so the *real* detection/tracking services consume them | Yes | No (GPU optional) |

The first three need **only Python**. They are how we validate the tracker fix
in Step 2. `publish` is for when you want the whole Dockerized pipeline running
end-to-end on the tower.

---

## The bug this harness proves (Finding 1)

In the `enter_leave` scene, a second worker leaves at t=8s and never returns.
Ground truth therefore ends with **1** worker. The current tracker reports:

```
ground-truth final workers  = 1
tracker final live tracks   = 2     <-- one too many
ghost tracks (alive post-exit) = 1  <-- a track with no detection under it
```

That ghost is why worker counts and every per-worker KPI are unreliable. The
`frame` command draws it: you'll see an "ID 2" box on the right with **no body
inside it** and the HUD reading `detections: 1`.

---

## Running it on Windows

You have two options. **Option A (plain Python)** is enough for everything
except `publish`. **Option B (Docker)** is for the full pipeline.

### Prerequisites

- **Python 3.11+** for Windows — https://www.python.org/downloads/windows/
  (during install, tick **"Add python.exe to PATH"**).
- For `publish` only: **Docker Desktop** with the **WSL2 backend** —
  https://docs.docker.com/desktop/install/windows-install/

Check Python is available (PowerShell):

```powershell
python --version
```

### Option A — pure Python (report / frame / overlay)

From a PowerShell prompt, in the `tools\harness` folder:

```powershell
# 1. Create and activate a virtual environment (keeps deps isolated)
python -m venv .venv
.\.venv\Scripts\Activate.ps1
# If activation is blocked, run once:  Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

# 2. Install dependencies
pip install -r requirements.txt

# 3. Print the tracker report for the bug scene
python -m harness.cli report --scene enter_leave

# 4. Render the annotated frame that SHOWS the ghost track
python -m harness.cli frame --scene enter_leave --t 15 --out ghost.png
#    -> open ghost.png; note "ID 2" box with no body, HUD says detections: 1

# 5. (optional) Render the whole run as a video
python -m harness.cli overlay --scene enter_leave --out ghost.mp4

# 6. Run the automated checks
python -m pytest -v
#    Expect: 2 passed, 1 xfailed.
#    The xfailed test is the bug; it FLIPS TO PASS once Step 2 fixes the tracker.
```

Try the clean scene too, to see the harness does **not** cry wolf:

```powershell
python -m harness.cli report --scene steady_two
#    -> ghost tracks = 0  (two workers stay the whole time, two stable IDs)
```

### Option B — full pipeline via Docker (the `publish` path)

This streams synthetic frames into Redis so the **real** detection and tracking
engines process them — useful once we want to watch the live dashboard react.

```powershell
# 1. Start a Redis the harness can publish to (from anywhere)
docker run -d --name pai-redis -p 6379:6379 redis:7-alpine

# 2. From tools\harness (venv active, deps installed), publish a looping scene.
#    The camera UUID must match a row in your `cameras` table once the backend
#    is up; for a pure ingestion->detection smoke test any UUID works.
python -m harness.cli publish `
  --scene enter_leave `
  --camera 11111111-1111-1111-1111-111111111111 `
  --redis redis://localhost:6379/0 --loop
```

When you bring up the rest of the stack (`docker compose up`), point the services
at the same Redis and they'll consume these frames exactly as if they came from a
Hikvision NVR. (Full end-to-end wiring is its own step — we'll do that together
when we get there.)

---

## Scenes available

| Scene | Story | What it tests |
|---|---|---|
| `steady_two` | 2 workers, present throughout | Baseline — must report exactly 2, no ghosts |
| `enter_leave` | worker 2 leaves at 8s, never returns | **Finding 1** — departed worker must age out |
| `leave_return` | worker 2 leaves at 6s, returns at 12s | Re-entry handling |

New scenes are a few lines in `harness/scene.py` — we'll add factory-realistic
ones (variable fps, occlusion, crossing paths) as we need them.

---

## Detector modes

By default the detector is a **stub** that emits the scene's known boxes — so the
**tracker is the only thing under test**. To exercise real detection instead:

```powershell
$env:HARNESS_DETECTOR = "yolo"   # then uncomment ultralytics/torch in requirements.txt
```

Use real YOLO only when you specifically want to test detection; it is slower and
adds a second variable. For proving/​fixing the tracker, stub is correct.

---

## How this maps to the plan

- **Now (Step 1):** harness exists; it reproduces Finding 1 with ground truth and
  has the `frame`/`overlay` microscope. The failing (`xfail`) test is the bug.
- **Step 2:** fix the tracker (or swap in `supervision` — we'll decide using this
  harness to compare both on identical input). Success = the xfail test passes and
  `enter_leave` reports `ghost tracks = 0`.

---

## File map

```
tools/harness/
├── README.md                      <- this file
├── requirements.txt
├── harness/
│   ├── scene.py                   <- ground-truth scene definitions
│   ├── detector.py                <- stub detector + real-YOLO toggle
│   ├── render.py                  <- draws scenes to BGR/JPEG
│   ├── publisher.py               <- publishes frames to Redis (real format)
│   ├── tracker_runner.py          <- runs a tracker vs ground truth -> metrics
│   ├── overlay.py                 <- annotated PNG/MP4 "microscope"
│   ├── cli.py                     <- command-line entry point
│   └── _vendor/
│       └── bytetrack_current.py   <- COPY of the project's tracker, tested as-is
└── tests/
    └── test_tracker_findings.py   <- audit findings as automated assertions
```
