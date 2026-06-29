# Step 5 — Consumer groups across the stream engines (Finding 5)

**Status:** done; guarantees verified against a real (fake) Redis, including the shipped vendored copies.
**Files changed:** `services/detection-engine/detection/main.py`, `services/tracking-engine/tracking/main.py`, `services/activity-engine/activity/main.py`.
**Files added:** `services/common/streambus/consumer.py` (canonical helper) + `test_consumer.py` + `sync.sh`; a vendored `streambus/` package inside each engine.
**New runtime dependencies:** none (uses `redis.asyncio` already present).

---

## What was wrong (from the audit)

Only the event-engine used a consumer group. Detection, tracking, and activity read with a plain `XREAD` from `"$"` and kept the last id in memory. Two consequences:

- **Data loss on restart.** `"$"` means "only messages added after I connect." Anything published while a consumer was down or restarting was skipped permanently, and the in-memory cursor was lost on crash — so delivery was not at-least-once.
- **Double-processing on scale-out.** Two replicas each reading `"$"` both processed every message. The README promised horizontal scaling via `XGROUP`/`XREADGROUP`, but the code didn't implement it outside the event-engine.

## What it does now

A small shared helper, `streambus.consume(...)`, gives every engine:

- **At-least-once delivery.** Read with `XREADGROUP ">"`, process, then `XACK`. On startup it first drains this consumer's *pending* (unacked) entries, so frames in flight during a crash are reprocessed.
- **Horizontal scaling.** Replicas join the same group with distinct consumer names (hostname). The group splits messages across them instead of each seeing everything.
- **Responsive shutdown.** The blocking read is raced against a stop event, so a graceful stop (SIGTERM) doesn't hang for the full block window.

### Two backlog policies (live video vs. don't-drop)

- `BacklogPolicy.NEWEST` — used for **frames → detection**. If inference falls behind, ack-and-skip the stale backlog and process the freshest frame, so latency doesn't accumulate on a live feed.
- `BacklogPolicy.ALL` — used for **detections → tracks** and **tracks → activity**. Never drop a detection or track frame; the tracker and FSM need every one.

---

## Two design points worth knowing

### 1. Stateful stages must shard by camera, not share a camera

Tracking and activity hold **per-camera state** (track identities, FSMs). A consumer group is still the right tool — it gives crash-safety and lets you split *different cameras* across replicas — **but two replicas must never consume the same camera's stream**, or each would see half that camera's frames and the state would be wrong.

- Within one process, the discovery loop assigns one task per camera — correct.
- For multi-replica scale-out, shard cameras across replicas by `camera_id` (e.g. hash modulo replica count) so each camera is owned by exactly one replica.

Detection is stateless (each frame independent), so it can share a camera's stream across replicas freely. This distinction is called out in each engine's module docstring.

### 2. Vendoring the helper (a deliberate tradeoff)

Each engine builds from its own Docker context (`context: ./services/<engine>`), so a module in `services/common/` is outside the build and wouldn't be in the image. Two options:

- **(a)** change every build context to `./services` + dockerfile paths — one shared module, but touches the Dockerfile/compose for every service;
- **(b)** vendor the helper into each service package — duplicates ~150 lines but changes nothing about the build.

I chose **(b)** to keep risk low on inherited infra. The canonical copy + tests live in `services/common/streambus/`; `sync.sh` propagates it into each engine. The vendored bodies are byte-identical to canonical (verified). If/when you consolidate build contexts later, the vendored copies can be deleted and the import switched back to `common.streambus` — no logic changes needed.

---

## How it was verified (I ran all of it)

Against `fakeredis` (real consumer-group semantics), on both the canonical helper and the **shipped vendored detection copy**:

- **Scaling** — 2 consumers, 10 messages → split 5/5, zero overlap, each processed exactly once.
- **Crash recovery** — a consumer reads 3 frames but never acks (simulated crash); on restart the same consumer recovers and reprocesses all 3.
- **Newest policy** — 20-frame backlog → only the freshest frame processed, stale ones skipped.

```
cd services
PYTHONPATH=$(pwd) pytest common/streambus/test_consumer.py -q     # 3 passed
```

> Honest caveat: verified with fakeredis, which implements the stream/consumer-group commands faithfully, plus syntax/parse checks on all three refactored mains. A live run against a real Redis with the actual detection/tracking/activity containers is an integration check for the server. The consumer-group logic — the part that was missing — is proven.

---

## Resource note

Consumer groups add negligible overhead (one `XACK` per message) and *reduce*
wasted work at scale by eliminating double-processing. The NEWEST policy actively
saves GPU time under load by not running inference on stale frames. Net effect is
lighter, not heavier — aligned with the goal.

---

## Next

Step 6 — alert debounce + the production roll-up (Findings 6 & 8): stop the idle
alert from firing on every transition, and actually populate `production_records`
(reusing the Step 3 duration logic so live and batch numbers agree).
