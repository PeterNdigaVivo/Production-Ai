# Phased delivery

## Phase 1 — Foundation (this scaffold)

* RTSP ingest → Redis frame streams
* YOLOv11 detection (COCO person/cell-phone classes)
* ByteTrack persistent IDs per camera
* Polygon workstation zones + seat-zone assignment
* Per-track activity FSM (motion-only) with hysteresis
* Event-engine persistence + naive idle alert
* FastAPI: factories / lines / cameras / workstations / zones / events / alerts / analytics
* JWT auth + RBAC roles
* TimescaleDB hypertables for `worker_events` / `production_events`
* Celery beat: camera heartbeat watchdog + production roll-up stub
* Next.js executive dashboard with live KPI cards + WebSocket event feed
* Docker Compose dev + prod, Helm chart skeleton, GitHub Actions CI

**Trade-offs accepted:**
* COCO-only classes — sewing-machine / tray / bundle classes ship in Phase 3
  after labeling 5–10k bbox samples.
* Activity is motion-only — pose & optical flow land in Phase 2.
* Piece count is not yet computed — `production_events` table is empty until
  Phase 3.

## Phase 2 — Activity intelligence

* YOLO-Pose / RTMPose for joint keypoints
* Optical flow over machine zone → `machine_running` signal
* TCN or temporal transformer on pose sequences for WORKING vs IDLE
* Input-tray emptiness detector → drives WAITING_FOR_INPUT
* Re-ID head for cross-camera track continuity (OSNet)
* Alert cooldown, aggregation, escalation rules

## Phase 3 — Production analytics

* Cycle engine: bundle pickup → sewing → output placement
* Piece counter emits `production_events`
* Roll-up worker into `production_records` (5-min and shift windows)
* PDF/Excel/CSV exports
* Bottleneck analysis (line-level cycle distribution)

## Phase 4 — Integrations + ML lifecycle

* RFID/Barcode/QR readers via MQTT, fused with visual cycle events
* MLflow A/B testing of detection/activity models
* Model drift detection + automated retraining triggers
* Schema-per-tenant migration for strong isolation
