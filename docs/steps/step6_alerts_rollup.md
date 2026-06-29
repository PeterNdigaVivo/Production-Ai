# Step 6 — Alert debounce + production roll-up (Findings 6 & 8)

**Status:** done; logic verified against a real database and compiled to valid PostgreSQL.
**Files changed:** `services/event-engine/events/main.py`, `services/backend/app/workers/tasks.py`, `services/backend/app/workers/celery_app.py`.
**Files added:** `services/backend/app/services/rollup.py`, `services/backend/tests/test_rollup.py`.
**New runtime dependencies:** none.

---

## Finding 6 — idle alert fired on every transition (spam)

The event-engine inserted an alert on *every* transition into IDLE, with no
duration threshold and no de-duplication. Brief, normal pauses (repositioning
fabric, reaching for thread) each spawned an alert; the table flooded and
operators would learn to ignore it.

### Fix: a periodic, debounced, de-duplicated check

Idle alerting moved out of the event-engine into a Celery beat task,
`check_idle_workers`, running every minute:

- It finds workers whose **current** state (latest event per camera/workstation/track) is `IDLE` and has persisted **>= 180s**.
- A worker back to `WORKING` or in `WAITING_FOR_INPUT` is not flagged — the fairness rule holds (no alert for an empty tray).
- **De-duplicated:** if an unacknowledged `worker_idle` alert already exists for that worker (`payload.worker_key`), no second alert is raised.

Why periodic rather than event-driven: a worker who goes idle and *stays* idle
emits no further transitions, so there is no event for the event-engine to react
to. Only a periodic check can detect "still idle, nothing new." The event-engine
now just persists events (and the camera-heartbeat watchdog was likewise made
de-duplicating so it stops re-alerting an already-flagged offline camera).

## Finding 8 — production_records roll-up was a no-op

`rollup_production_records` only logged start/done, so `production_records` was
never populated and historical/shift dashboards had no source.

### Fix: real windowed aggregation, reusing the Step-3 duration logic

`rollup.rollup_window(start, end)` reconstructs per-(workstation) state durations
with the same `LEAD()` interval method as the live analytics, **clamps the last
interval to the window end** (so a closed window is final, not open to "now"),
counts pieces, and writes one `production_records` row per workstation. The
Celery task rolls up the most recently completed 5-minute window each run.

- **Idempotent:** rows for a window are deleted before re-insert, so re-running a
  window (retry, backfill) overwrites rather than duplicates.
- **Consistent with live KPIs:** same interval logic as `app/services/analytics.py`,
  so rolled-up numbers and on-demand KPIs agree.

---

## How it was verified (I ran all of it)

Against in-memory SQLite (window functions) with known events, plus a
PostgreSQL compile check of the production queries (`func.least`, `EXTRACT(epoch)`,
`row_number()`):

- **Roll-up durations** — WORKING/IDLE seconds within a window computed correctly.
- **Interval clamping** — an interval whose next event falls *after* the window end is capped at the window (60s, not 360s out to the next event).
- **Idle debounce** — of three workers (200s idle, 60s idle, working), only the 200s one alerts.
- **Idle resolution** — a worker whose latest event is WORKING does not alert even if previously idle.

```
cd services/backend
pytest tests/test_rollup.py -v      # 4 passed
```

> Honest caveat: SQLite for the seconds arithmetic (`julianday` vs Postgres
> `EXTRACT(epoch)`) and `func.min` standing in for `func.least`; the
> interval-reconstruction and ranking logic is identical and the production SQL
> is confirmed to compile for PostgreSQL. A live run with Celery beat + Postgres
> is a server-side integration check. De-duplication is enforced in Python
> against current unacked alerts; under heavy concurrency a DB unique constraint
> on `(kind, worker_key)` for open alerts would be the belt-and-braces version
> (a small future migration).

---

## Resource note

The roll-up is one set-based pass per 5-minute window over an indexed time range,
and replaces what would otherwise be repeated expensive live aggregation — this
is the path toward TimescaleDB continuous aggregates noted in the tradeoffs doc.
The idle check is one ranked query per minute. Both are cheap and bounded.

---

## Next

Step 7 — remaining security hardening (Findings 7, 10, 11): authenticate the
heartbeat endpoint, authenticate + tenant-filter the WebSocket feed, and add
refresh-token rotation.
