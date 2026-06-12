# Trade-offs and recommended optimizations

## What the scaffold deliberately does *not* do yet

| Concern | Status | Why |
|---|---|---|
| Re-ID across cameras | Not wired | Adds 200–500ms/frame; only matters once we cross camera boundaries inside one line. |
| Custom YOLO classes (sewing machine, tray) | Defer | Needs 5–10k labeled samples per class. Use COCO + zone polygons in Phase 1 to avoid blocking. |
| Frame storage | Not done | Privacy risk + 100s of GB/day. Only crops on alert in Phase 2. |
| Schema-per-tenant | Defer | Row-level isolation + JWT claim is enough for the first 10 tenants. |

## High-impact optimizations (when you hit scale)

1. **Batch detection.** Today we infer one frame at a time. Group N frames
   from M cameras into a single GPU batch — ~3× throughput on a T4 with
   negligible latency cost (one frame interval).
2. **Frame down-sampling at ingest.** `target_fps=4` is enough for activity
   classification once the FSM has a 60s window. Halves all downstream cost.
3. **Redis stream sharding.** When a single Redis node tops 30k ops/s, shard
   by camera_id hash across 4 Redis instances behind a thin facade.
4. **Continuous aggregates (TimescaleDB).** Materialize `production_records`
   from `worker_events` with CAGGs — saves the Celery roll-up entirely and
   makes dashboards 100× faster.
5. **Substream over mainstream.** Hikvision channel 102 is typically
   704×576 — perfectly adequate for person detection and 4× cheaper than 101.
6. **TensorRT INT8** with calibration on factory-specific frames is good for
   another ~30% latency reduction over FP16 with minimal mAP loss.

## Cost ballpark

A single 24G RTX 6000 / A30 / L4 runs ~80 cameras at 8 fps with YOLOv11n +
TensorRT FP16. Bottleneck is JPEG decode/encode on CPU — 1 vCPU per ~30
cameras when not using NVDEC.

## Security posture

* No frames are persisted by default; only event-level metadata.
* JWT access tokens are 15-min TTL, refresh tokens 30 days. Rotate
  `JWT_SECRET` every quarter via the secrets manager.
* RTSP credentials live in env vars, never logged. Move to Vault / KMS for
  prod.
* All inter-service traffic stays on the `paimnet` Docker network or the K8s
  cluster network — never exposed outside.
