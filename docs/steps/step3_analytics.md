# Step 3 — Analytics: real durations, the fairness rule, tenant scoping (Findings 2 & 4)

**Status:** done; logic verified against a real database and compiled to valid PostgreSQL.
**Files changed:** `services/backend/app/api/v1/endpoints/analytics.py` (rewritten, thin).
**Files added:** `services/backend/app/services/analytics.py` (the analytics core), `services/backend/tests/test_analytics.py`.
**New runtime dependencies:** none.

---

## What was wrong (from the audit)

- **Finding 2 — counts, not durations.** The endpoint did `SELECT state, count(*) ... GROUP BY state`. `worker_events` stores state *transitions*, so this counted how often a worker flipped into a state, not how long they stayed there. Productivity is a duration measure; the old query could not express it. The **fairness rule was absent** entirely.
- **Finding 4 — cross-tenant leak.** The state aggregation filtered on timestamp only — no line, no tenant. A manager viewing one line saw state data blended across every line in every tenant.

## What it does now

1. **Durations via a window function.** For each `(workstation, track)` ordered by `ts`, `LEAD(ts)` gives the next event's time; the state interval runs from `ts` to the next event (or to *now* for the open interval). Seconds per state are summed in the database — set-based, no pulling rows into Python.

2. **The fairness rule, explicitly.** Productivity = `WORKING / (WORKING + IDLE + AWAY)`. `WAITING_FOR_INPUT` is **excluded from the denominator** — a worker is never penalised for an empty input tray. The waiting time is still reported, just not counted against them.

3. **Tenant + line scoping.** The query is constrained to the workstations of the requested line, and the line must resolve (line → factory → tenant) to the caller's tenant. Unknown or cross-tenant lines return **404** (not 403, so we don't reveal that a line exists in another tenant).

### A worked example (from the tests)

A worker logs 360s WORKING, 120s WAITING_FOR_INPUT, 120s IDLE:

| metric | value |
|---|---|
| Productivity (fair — waiting excluded) | **75.0%** |
| Productivity (naive — waiting as idle) | 60.0% |

The fairness rule rescues **15 percentage points** the worker would otherwise lose for an empty tray. That gap is the entire reason the rule exists, now quantified and enforced.

---

## Design choice: a shared analytics core

The math lives in `app/services/analytics.py`, not inside the endpoint. Reason: the Celery roll-up that populates `production_records` (Finding 8, a later step) must produce the *same* numbers as the live API. One implementation, no drift. The endpoint is now a thin HTTP wrapper that handles auth, scoping, and error mapping.

## Sequencing note (interaction with Finding 3)

Finding 3 (the JWT does not yet carry `tenant_id`) is a later step. Rather than block on it, scoping is enforced at the **data layer**: the line must resolve to a real tenant, and the events aggregated are only those of that line's workstations. This is the more correct design regardless — you never trust only the token. When Finding 3 lands and the token carries `tenant_id`, `_caller_tenant()` automatically adds a second, token-level cross-check (and the data-layer check stays as defence-in-depth). Superusers may cross tenants for support.

---

## How it was verified (I ran all of this)

1. **Logic prototype** in pure Python — duration pairing + fairness math, hand-checked.
2. **Real-database validation** — the *exact same* SQLAlchemy query construction run against an in-memory SQLite DB (window functions supported) with known events, including the hard case of **two different tracks on one workstation** (must be partitioned separately) and an open final interval extending to *now*. Durations matched expectations to the second.
3. **PostgreSQL compile check** — the production query compiled to PostgreSQL dialect to confirm the `WITH intervals AS (… LEAD() OVER (PARTITION BY …) …)` / `EXTRACT(epoch FROM COALESCE(…))` SQL is valid PG.
4. **pytest** — `tests/test_analytics.py`, 3 passing: durations+fairness, per-track partitioning, and workstation-scoping (the leak fix).

```
cd services/backend
pytest tests/test_analytics.py -v     # 3 passed
```

> Honest caveat: the SQLite validation uses `julianday` for the seconds conversion while production uses `EXTRACT(epoch …)` — a dialect difference in that one arithmetic step only. The hard part (window-function interval reconstruction and partitioning) is identical and proven, and the PG SQL is confirmed to compile. Full end-to-end against live TimescaleDB is an integration check to run on the server.

---

## Resource note

The duration query is a single set-based pass with one window function over a
time-bounded, indexed range (`worker_events` is indexed on `(workstation_id, ts)`).
No row-by-row Python. The medium-term plan (Finding 8 / tradeoffs doc) is to
materialise these as TimescaleDB continuous aggregates so the API reads a
pre-rolled table — but the live query is already cheap and correct.

---

## Next

Step 4 — auth claims (Finding 3): put `tenant_id` and `roles` into the JWT at
login and refresh, which upgrades this step's scoping from data-layer-only to
data-layer-plus-token, and makes RBAC functional.
