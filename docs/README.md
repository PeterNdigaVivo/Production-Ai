# docs/

Single source of truth for all handover and improvement reference material.
Project-original architecture docs live in `docs/architecture/` (unchanged);
everything produced while taking the project over lives here alongside it.

## Layout

```
docs/
├── architecture/   <- original project docs (overview, phases, tradeoffs) — untouched
├── audit/          <- code audit + supporting evidence
│   ├── Production-AI_Code_Audit.docx   <- full correctness review (12 findings)
│   └── ghost_track_demo.png            <- Finding 1, shown visually
└── steps/          <- per-step write-ups (what changed, why, how verified)
```

## Where things are

| Item | Location |
|---|---|
| Full code audit (start here) | `docs/audit/Production-AI_Code_Audit.docx` |
| Step 1 — synthetic test harness (code + how-to) | `tools/harness/` (see its README) |
| Step 2 — tracker fix write-up + before/after | `docs/steps/step2_tracker_fix.md`, `docs/steps/step2_before_after.png` |
| Fixed tracker (in service) | `services/tracking-engine/tracking/bytetrack.py` |
| Step 3 — analytics write-up | `docs/steps/step3_analytics.md` |
| Analytics core + endpoint | `services/backend/app/services/analytics.py`, `services/backend/app/api/v1/endpoints/analytics.py` |
| Step 4 — auth claims write-up | `docs/steps/step4_auth_claims.md` |
| Auth claim builder + endpoint | `services/backend/app/services/auth.py`, `services/backend/app/api/v1/endpoints/auth.py` |
| Step 5 — consumer groups write-up | `docs/steps/step5_consumer_groups.md` |
| Stream helper (canonical + tests) | `services/common/streambus/` (vendored into each engine) |
| Step 6 — alerts + roll-up write-up | `docs/steps/step6_alerts_rollup.md` |
| Roll-up + idle service, tasks | `services/backend/app/services/rollup.py`, `services/backend/app/workers/tasks.py` |
| Step 7 — security write-up | `docs/steps/step7_security.md` |
| Refresh-token store | `services/backend/app/services/token_store.py` |
| Step 8 — data flywheel write-up + runbook | `docs/steps/step8_data_flywheel.md`, `docs/steps/step8_runbook.md` |
| Capture + machine-flow (canonical) | `services/common/capture/`, `services/common/machine_flow/` |
| ML training lifecycle | `ml/labeling/export_dataset.py`, `ml/training/registry.py`, `ml/training/train_detection.py` |

## Remediation order (from the audit)

1. **Step 1 — safety net** *(done)* — synthetic harness reproducing bugs against known ground truth. `tools/harness/`.
2. **Step 2 — fix the tracker (Finding 1)** *(done)* — Kalman + correct aging; benchmarked vs supervision (4.6× faster, same accuracy, zero new deps).
3. **Step 3 — analytics: durations + fairness rule + tenant scoping (Findings 2 & 4)** *(done)* — duration KPIs via window function; WAITING_FOR_INPUT excluded; line/tenant-scoped.
4. **Step 4 — auth claims (Finding 3)** *(done)* — tenant_id + roles in the JWT; RBAC and tenant scoping made functional.
5. **Step 5 — consumer groups (Finding 5)** *(done)* — crash-safe, horizontally-scalable stream consumption across detection/tracking/activity.
6. **Step 6 — alert debounce + roll-up (Findings 6 & 8)** *(done)* — periodic de-duplicated idle alerts; real production_records roll-up.
7. **Step 7 — remaining security (Findings 7, 10, 11)** *(done)* — heartbeat auth, WebSocket auth + tenant filter, refresh-token rotation. See `docs/steps/step7_security.md`.
8. **Step 8 — data flywheel + fair activity (Findings 9 & 12 + Phase-2 foundation)** *(done)* — time-weighted FSM, optical-flow machine-running, active-learning capture, training/registry pipeline. See `docs/steps/step8_data_flywheel.md` and `docs/steps/step8_runbook.md`.
9. **Step 9 — piece-counting / cycle-time** *(next, needs labeled footage)*.

## Running the tests

```bash
# Backend (analytics + auth)
cd services/backend && pytest tests/ -q

# Stream consumer groups
cd services && PYTHONPATH=$(pwd) pytest common/streambus/test_consumer.py -q

# Tracker harness
cd tools/harness && pip install -r requirements.txt && pytest -q
```
