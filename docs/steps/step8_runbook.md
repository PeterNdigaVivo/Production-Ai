# Runbook — the data flywheel: capture → label → export → train → promote → deploy

This is the practical loop that takes the system from "monitors what's
measurable" to "measures time-per-piece," using data your live deployment
collects. Camera-only; no tags required.

## 0. One-time: record a calibration clip (do this first)

Record **20–30 minutes per camera angle** of normal line operation, covering
both busy and quiet periods, and ideally a few obvious machine-running and
machine-idle stretches you can note the timestamps of.

Use it to:
- **Validate activity detection** against reality (not synthetic boxes).
- **Calibrate the machine-flow threshold** per camera:
  1. Run the clip through the detector, collecting `mean_flow` values during
     known running vs idle segments.
  2. `from activity.machine_flow import calibrate_threshold`
     `thr = calibrate_threshold(running_flows, idle_flows)`
  3. Set that per-camera threshold in config.

## 1. Turn on capture (in the factory)

Capture is **off by default** (privacy). Enable on the detection engine:

```
CAPTURE_ENABLED=true
CAPTURE_DIR=/data/capture          # mount a real volume
MACHINE_FLOW_ENABLED=true          # once thresholds are calibrated
```

The system now saves only **uncertain** frames (ambiguous confidence, unstable
state, count anomalies) with `labeled: false` sidecars, rate-limited per camera.
Let it run. You accumulate a curated set of the hard cases — not raw hours.

## 2. Label the captures

Each capture is `<id>.jpg` + `<id>.json`. Labelling = drawing boxes and setting
in the sidecar:

```json
{
  "...": "...",
  "labeled": true,
  "image_wh": [1920, 1080],
  "annotations": [
    {"class_id": 0, "xyxy": [x1, y1, x2, y2]},   // person
    {"class_id": 1, "xyxy": [x1, y1, x2, y2]},   // sewing_machine
    {"class_id": 2, "xyxy": [x1, y1, x2, y2]}    // bundle  (for piece-counting)
  ]
}
```

Any labelling tool that can read/write these sidecars works (CVAT, Label Studio,
or a simple internal UI). Only `labeled: true` captures are used downstream.

## 3. Export to a training dataset

```
python ml/labeling/export_dataset.py \
  --capture-dir /data/capture \
  --out-dir /data/datasets/run-001 \
  --classes person sewing_machine bundle
```

Produces a YOLO dataset (`images/`, `labels/`, `data.yaml`) with a train/val
split. Unlabelled captures are ignored.

## 4. Train

```
python ml/training/train_detection.py \
  --data /data/datasets/run-001/data.yaml \
  --model yolo11n.pt \
  --epochs 100 \
  --experiment garment-detection
```

Logs params, metrics, and artifacts to MLflow.

## 5. Register + promote

```
# register the run's best weights
python ml/training/registry.py register \
  --run-id <MLFLOW_RUN_ID> --artifact-path weights/best.pt --name garment-detector

# promote to Production (archives the previous Production version)
python ml/training/registry.py promote \
  --name garment-detector --version <N> --stage Production

# what's live now?
python ml/training/registry.py current --name garment-detector
```

## 6. Deploy the new model

Point the detection engine at the promoted model (`YOLO_MODEL_PATH`, or pull the
current Production URI from the registry) and restart the detection workers.
The improved model now produces better detections → fewer uncertain frames on
the easy cases, sharper captures on the genuinely hard ones → the next loop is
better. That's the flywheel.

## 7. Toward time-per-piece (Step 9+)

Once the `bundle` class detects reliably:
- Add a per-workstation **cycle FSM**: bundle in input zone → machine running
  (sewing) → bundle leaves to output zone = one piece; the elapsed time is the
  cycle time.
- Aggregate cycle times per station, and chain stations for end-to-end garment
  completion time.
This reuses the zone system, the machine-flow signal, and the duration/analytics
machinery already built.
