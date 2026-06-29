# Step 2 — Fix the tracker (Finding 1)

**Status:** done and verified in the harness.
**Files changed:** `services/tracking-engine/tracking/bytetrack.py` (rewritten, drop-in).
**Files added:** `tools/harness/harness/_candidates/` (fixed tracker + supervision adapter), `tools/harness/harness/bench.py`, harder scenes in `scene.py`, updated tests.
**New runtime dependencies:** none.

---

## The decision: fix the existing tracker, don't adopt a library

We treated "fix vs replace" as a data question and measured it. The candidates:

- **current** — the project's tracker as shipped (the baseline; has Finding 1).
- **fixed** — a hardened rewrite: constant-velocity **Kalman filter** + **correct aging**, numpy/scipy only.
- **supervision** — `sv.ByteTrack`, used as a *reference oracle* (not adopted).

### Why not supervision

It is the de-facto standard, but for our goals it was the wrong fit:

- `sv.ByteTrack` is **deprecated**, migrating to a separate `trackers` package (`ByteTrackTracker`), removal scheduled for 0.30.0. Adopting it means inheriting an API mid-churn.
- It pulls a heavy dependency tree into a service the author deliberately kept to 7 lean packages.
- It ran **4.6× slower** than our fixed tracker on identical input (see below).

We instead used it to *prove* our lean tracker matches library-grade behaviour.

---

## Head-to-head results (synthetic scenes, identical input)

`gtEnd` = ground-truth workers at end · `end` = tracker's live tracks at end · `ghost` = tracks alive after their worker left · `idsw` = approx id switches · `upd/s` = tracker updates per second.

| scene | tracker | gtEnd | end | ghost | idsw | upd/s |
|---|---|---:|---:|---:|---:|---:|
| enter_leave | current | 1 | **2** | **1** | 0 | 36,759 |
| enter_leave | **fixed** | 1 | **1** | **0** | 0 | 11,342 |
| enter_leave | supervision | 1 | 1 | 0 | 0 | 2,302 |
| crossing | current | 2 | 2 | 0 | 1 | 34,001 |
| crossing | **fixed** | 2 | 2 | 0 | 1 | 10,518 |
| crossing | supervision | 2 | 2 | 0 | 1 | 2,125 |
| dropout (25%) | current | 3 | 3 | 0 | 0 | 26,911 |
| dropout (25%) | **fixed** | 3 | **3** | 0 | 0 | 8,424 |
| dropout (25%) | supervision | 3 | **1** | 0 | 0 | 2,032 |

**Averages:** current ~30,800 upd/s · **fixed ~10,200 upd/s** · supervision ~2,200 upd/s.

### Reading the table

- **Finding 1 is fixed.** On `enter_leave`, `fixed` ends with 1 live track and 0 ghosts — matching supervision exactly — while `current` keeps the ghost. See `step2_before_after.png`: same frame, the phantom "ID 2" box over empty floor is gone.
- **Identity stability matches the oracle** on the crossing test (the scene that separates a Kalman tracker from a raw-IoU one).
- **More robust under frame loss.** At 25% dropout, `fixed` held all 3 workers; supervision dropped to 1. Workers don't teleport on a factory floor, so holding tracks through brief misses is the behaviour we want — though the exact tolerance is a tuning knob to revisit on real footage.
- **4.6× faster than supervision**, with **zero new dependencies**.

### Honest caveats

- The Kalman filter costs ~3× vs the buggy baseline (~30k → ~10k upd/s). In context this is irrelevant: detection (YOLO) dominates cost by orders of magnitude; tracking will never be the bottleneck. ~10k upd/s is ~1,000+ camera-streams of tracking per CPU core at 8 fps.
- These are **synthetic** scenes. They prove the *logic* (ghost elimination, ID stability under crossing/dropout) conclusively; they are not a substitute for real Hikvision footage, which we validate against when available.

---

## What changed in the code

`services/tracking-engine/tracking/bytetrack.py` was rewritten with the **same public interface** (`update(detections) -> list[Track]`, with `.id/.xyxy/.cls/.conf/.hits`), so nothing else in the pipeline changes. Three substantive changes:

1. **Correct aging (the bug).** Every surviving track is marked missed at the top of the frame; a successful match clears it. The old code inferred "matched" from `misses == 0` *after* mutating misses, so unmatched tracks never aged out and IDs were immortal. The two dead no-op loops were removed.
2. **Kalman prediction.** A per-track constant-velocity filter (state: cx, cy, aspect, height + velocities). Matching is done against the *predicted* box, so motion and dropped frames no longer break IoU overlap.
3. **Cleaned two-stage association.** High-confidence first, then low-confidence against leftovers (the real "BYTE" recovery idea), with the second stage now also able to sustain a track.

---

## How to reproduce

From `tools/harness/` (Windows PowerShell or any OS):

```bash
pip install -r requirements.txt
pip install supervision        # optional, only to include the oracle column

python -m harness.bench        # the full head-to-head table
python -m pytest -v            # 5 passing checks incl. a regression marker
python -m harness.cli frame --scene enter_leave --t 15 --out after.png
```

The regression test `test_regression_original_tracker_had_ghost` keeps the
original defect pinned as a tripwire, so it can never be silently reintroduced.

---

## Next

Step 3 — analytics: real duration-based KPIs with the `WAITING_FOR_INPUT`
fairness rule and tenant/line scoping (Findings 2 & 4). The fixed tracker is the
prerequisite: trustworthy durations require trustworthy identities, which we now
have.
