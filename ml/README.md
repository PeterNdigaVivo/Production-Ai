# ML pipelines

MLflow tracking server is provisioned in `docker-compose.yml` on port 5000.

## Training

```bash
# Detection — fine-tune YOLOv11 on factory-collected bbox samples
python -m training.train_detection --data data/garment.yaml --epochs 100

# Activity — pose-based temporal classifier (Phase 2)
python -m training.train_activity --data data/activity.parquet
```

Both scripts log params, metrics, and the model artifact to MLflow under the
experiment given by `--experiment`.

## Registry & promotion

The MLflow Model Registry holds three stages: `Staging`, `Production`,
`Archived`. The detection service reads `YOLO_MODEL_PATH` which the K8s
post-deploy hook updates to the latest `Production`-tagged artifact.

## A/B testing (Phase 4)

`detection-engine` runs N replicas, each pinned to a model URI via env. A
weighted ingress in front of the Redis output stream sends a sample of frames
to the candidate; the analytics service compares downstream KPIs.
