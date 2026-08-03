# Production-AI — Garment Manufacturing Intelligence Platform

AI-powered productivity intelligence for garment sewing lines using Hikvision
NVR RTSP feeds. Detects workers, tracks workstations, infers activity states,
counts pieces, and surfaces KPIs to a real-time dashboard.

> **Status:** Phase 1 scaffold. Core ingestion → detection → tracking → zone
> pipeline is wired end-to-end; activity/cycle/piece engines have working
> skeletons with documented extension points; analytics, alerting, MLOps,
> Helm, and CI are scaffolded.

## Architecture (high level)

```
[Hikvision NVR] --RTSP--> ingestion -> Redis Stream:frames
                                    \
                                     +-> detection -> Redis Stream:detections
                                                  \
                                                   +-> tracking -> Redis Stream:tracks
                                                                \
                                                                 +-> activity -> events
                                                                              \
                                                                               +-> FastAPI + Postgres/TimescaleDB
                                                                                                |
                                                                                                v
                                                                                       Next.js Dashboard
```

See [`docs/architecture/overview.md`](docs/architecture/overview.md) for
detailed diagrams (Mermaid).

## Quick start (development)

```bash
cp .env.example .env
# edit .env with your RTSP URLs and DB credentials
make up
# Backend:   http://localhost:8000/docs
# Dashboard: http://localhost:3000
# Grafana:   http://localhost:3001
# MLflow:    http://localhost:5000
```

## Repository layout

| Path | Purpose |
| --- | --- |
| `services/video-ingestion`  | FFmpeg-based RTSP ingest, frame publisher |
| `services/detection-engine` | YOLOv11 + TensorRT object detection |
| `services/tracking-engine`  | ByteTrack/BoT-SORT multi-object tracking |
| `services/activity-engine`  | Pose + temporal activity recognition |
| `services/machine-activity` | Visual sewing-machine state inference |
| `services/event-engine`     | Redis Streams event router, alerting |
| `services/backend`          | FastAPI REST + WebSocket API |
| `services/celery-worker`    | Background jobs, reports, ETL |
| `frontend`                  | Next.js 14 + React + ECharts dashboard |
| `infrastructure`            | Docker, Nginx, Prometheus, Grafana, Loki, Helm |
| `ml`                        | MLflow, training & evaluation pipelines |
| `docs`                      | Architecture, API, runbooks |

## Phases

The authoritative plan is [`ROADMAP.md`](ROADMAP.md). Read it before
proposing features — it also carries the ground rules and the "detection vs.
activity" distinction that drives sequencing.

The four-phase list originally in this section (and still in
[`docs/architecture/phases.md`](docs/architecture/phases.md) for historical
context) is **superseded**. Two differences worth calling out here:

- **Camera-only.** RFID / barcode / QR readers are ruled out — the original
  Phase 4 assumed they'd be added.
- **Custom model training is deferred to Phase 4 and gated on Phase 3's
  accuracy measurement.** The stock YOLO model plus tight zones and
  well-tuned thresholds is expected to carry us until Phase 3 proves
  otherwise; training a custom head before that is speculative work.
