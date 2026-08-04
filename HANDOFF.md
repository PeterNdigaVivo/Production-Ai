# HANDOFF.md — start here

**Read this before touching anything.** Standing document for anyone (human
or Claude session) picking up this project cold. Short by design —
authoritative narrative and change log live in [`ROADMAP.md`](ROADMAP.md);
onboarding steps live in [`docs/runbooks/camera_onboarding.md`](docs/runbooks/camera_onboarding.md).
This file summarises and points.

---

## Goal & true north

Turn CCTV over sewing lines into an operational dashboard for supervisors.

> **True north (from `ROADMAP.md`):** a supervisor sees a stalled workstation
> and fixes it *before the shift ends*. Every feature is judged against that
> sentence. If it doesn't move a supervisor closer to acting on a problem
> during the shift it happened, it waits.

---

## Working model — three roles

1. **Chat Claude (this side)** — writes prompts and the tower-side commands.
   Never touches the repo directly.
2. **Claude Code (in-repo agent)** — makes every code / doc change, runs
   tests it *can* run in-sandbox, commits, pushes. Reports back with
   evidence (diffs, test output, file paths). Honest about what it can't
   run (has no docker daemon, no live DB, no real Redis on this sandbox).
   Every task has explicit **STOP conditions**; if any trip, it reports
   instead of guessing.
3. **Stephen** — the courier. Runs verification on the Windows tower,
   pastes real outputs back. Shell is PowerShell-native. Gates every
   build behind `git status → git pull → tail the log → then build`
   (see Hard-won lessons).

---

## Current state

- **Phase 0 (Prove the pipeline) — complete and verified.** Dashboard
  renders + login works, first camera streaming, `person` detections
  landing in `worker_events` (exit test passed: `select count(*) from
  worker_events` climbing; see [`ROADMAP.md` 3 Aug entry](ROADMAP.md)).
- **Phase 1 (Make it configurable) — in progress.** Camera-onboarding
  loop `discover → review → promote` is live end-to-end. **Six seat
  zones now mapped on Cam-101** (`f524b92c-…`), all empirically backed
  by measured operator dwell — not eyeballed. Stations 1–3 were seeded;
  4 & 5 inserted via the one-off `insert_stations_4_5.py`; Station 6
  promoted via `promote_zones` (maiden run 4 Aug pm).
- **Per-camera resolution correctness.** `--far-cutoff-frac` is
  fractional (default 0.28 of frame height), outputs go to
  `/tmp/discover_zones/<camera_id>/`. Ready for the GD50 at 1920×1080
  without a code change.
- **Promotion audit trail.** Each promotion run writes
  `promotion_log.json` alongside `decisions.json` — camera_id, line,
  frame dims, promoted rows, rejects with reasons.
- **Celery healthchecks green.** Backend Dockerfile's HTTP check was
  inherited by non-HTTP Celery services and reported unhealthy forever
  — fixed in `cc9310f` (`inspect ping` for the worker, `/proc` scan for
  beat).
- **Tracker phantom-track fix verified live.** Kalman-only tracks no
  longer publish; union-of-intervals per station replaces sum-of-tracks
  in rollup + analytics. See `ROADMAP.md` for the failure signatures
  (`y=-4009`, `2796s in a 300s window`).

---

## Key files

| Path | What it is |
| --- | --- |
| [`ROADMAP.md`](ROADMAP.md) | **Authoritative plan.** Phases, exit tests, ground rules, `## Off-roadmap change log` (append-only, newest first). |
| [`docs/runbooks/camera_onboarding.md`](docs/runbooks/camera_onboarding.md) | One-page loop: add camera → discover → review → decisions.json → promote → verify. |
| [`docs/runbooks/staging_deploy.md`](docs/runbooks/staging_deploy.md) | Factory-adjacent GPU box bring-up. |
| `services/backend/app/scripts/discover_zones.py` | Passive dwell clustering. XREAD-only, never joins a consumer group. Per-camera output dir. |
| `services/backend/app/scripts/promote_zones.py` | Decisions-driven zone promotion. Atomic, idempotent, overlap-safe. `load_json_file` tolerates UTF-8 BOM. |
| `services/backend/app/scripts/insert_stations_4_5.py` | **Superseded** by `promote_zones`; kept for the first-camera record. |
| `services/backend/app/services/intervals.py` | Merged (union-of-intervals) state seconds. Shared by rollup + live analytics. |
| `services/backend/app/workers/db.py` | Per-task NullPool engine — fix for asyncpg-across-event-loops in Celery. |
| [`SESSION.md`](SESSION.md) | Pre-Phase-0 audit history (Steps 0–8). Superseded pointer only — see the change log. |

---

## Hard-won lessons

Compact. Longer stories are in the `ROADMAP.md` change log.

1. **Stale-deploy trap.** Before every rebuild: `git status` (uncommitted
   surprises), `git pull`, and tail the last-known-good service log
   until it is showing *your* commit's log line. Skipping this once
   burned an afternoon chasing a "reproduction" of a bug that was already
   fixed on origin.
2. **Transition-only event semantics.** `worker_events` records state
   *transitions*, not samples. Durations reconstruct from
   `LEAD(ts) OVER (partition …)`, and the union-of-intervals CTE in
   `intervals.py` is now the single source; do not re-derive.
3. **UUID PKs don't sort by time.** Every query "give me the latest N"
   needs `ORDER BY ts`, never `ORDER BY id`. The migration puts a
   composite PK `(id, ts)` on `worker_events` for TimescaleDB reasons,
   not for temporal ordering.
4. **UTF-8 BOM.** PowerShell's `Set-Content -Encoding utf8` writes
   `EF BB BF` at the file head; Python's `json` module refuses it.
   Every user-authored JSON reader must use `encoding='utf-8-sig'`
   (see `promote_zones.load_json_file`).
5. **Single-cell aisle blobs.** `discover_zones` cannot tell a seated
   operator from a standing chatter with the same total dwell; the
   reviewer heuristic is **real seats = multi-cell high-dwell clusters;
   single-cell, aisle-adjacent, low-total-dwell = standing person, not
   a seat.** Reject those in `decisions.json`.
6. **Sequence disruptive ops after data collection, never before.**
   Redis `--force-recreate` while consumers were live briefly broke
   the pipeline (recovered on its own; automated test still TODO). If
   in doubt: collect first, restart last.

---

## Next steps (in order)

1. **UI step 1 — read-only zone viewer.** Backend endpoint to serve the
   most recent frame for a camera; frontend component that overlays the
   camera's zones on top. No editing yet, purely visual. Evening work
   because it doesn't need factory hours.
2. **GD50 onboarding via the runbook.** The 1080p dress rehearsal — the
   overlap rule (one seat = one camera) and the fractional far-cutoff
   land under real conditions. Confirms `discover_zones` / `promote_zones`
   generalise beyond Cam-101.
3. **UI step 2 — zone editor (drawing).** Only after step 1 proves the
   viewer works. When this lands, fix the `POST /api/v1/zones`
   `layout_version` auto-bump (see Deferred #2) — the editor is the
   natural moment.

---

## Deferred (flagged, not doing yet)

1. **Cross-window interval under-count** in `intervals.py` — a state
   started before `window_start` and still open at it contributes zero.
   Documented `TODO` in the CTE. **Must be resolved no later than
   Phase 3** (the "numbers you'd defend in a management meeting"
   phase).
2. **`POST /api/v1/zones` layout_version bug.** The endpoint bumps
   *before* insert, so a first-ever zone via the API lands at
   `layout_version=2`, not 1. Scripts sidestep by setting `1`
   explicitly. Fix belongs in the endpoint when the zone editor lands
   (see Next steps #3): only bump if there is already a zone for that
   workstation.
3. **Snapshot-row idea.** Periodically snapshot open FSM states to
   `worker_events` so cross-window intervals stop being lossy. Cheaper
   than the proper cross-window CTE fix, but changes event semantics
   (currently transitions-only). **Discuss before implementing** —
   would touch analytics, rollup, and every downstream consumer.
4. **Redis-bounce reconnect test.** `--force-recreate` recovered fine
   on its own but the reconnect path isn't automated; integration test
   should simulate the bounce and assert every consumer resumes without
   operator help.
5. **Far row + upper-left cluster on Cam-101.** Not yet mapped;
   `discover_zones` is occupancy-dependent, rerun when those seats are
   occupied.

---

> **In doubt?** Read `ROADMAP.md` for plan + history, then this file for
> the working model. Change something? Log it in `## Off-roadmap change
> log` in ROADMAP.md. Update this file only for standing changes to
> role, workflow, or state summary — not per-task notes.
