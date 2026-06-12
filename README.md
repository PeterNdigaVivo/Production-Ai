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

1. **Phase 1 (this scaffold)** — RTSP ingestion, detection, tracking,
   workstation zones, presence analytics, dashboard skeleton.
2. **Phase 2** — Activity recognition, idle intelligence, machine activity.
3. **Phase 3** — Piece counting, cycle time, production analytics.
4. **Phase 4** — RFID/barcode integration, MLflow A/B, advanced optimization.

See [`docs/architecture/phases.md`](docs/architecture/phases.md).
