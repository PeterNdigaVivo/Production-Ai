# Step 8 — Data flywheel + fair activity (Finding 9, Finding 12, Phase-2 foundation)

**Status:** done; all logic verified without GPU/cameras/Redis. Tuning of the
machine-flow threshold is deferred to your calibration footage (by design).
**New runtime dependencies:** none (optical flow uses opencv, already present).

This is the step where the project turns from *fixing the inherited code* to
*building toward the end-game* you described: monitor idle/away/supervisor now,
and lay the foundation to measure time-per-piece later — camera-only, no tags.

---

## Strategy (agreed): Path A — deploy the measurable, capture data as you go

You confirmed: footage can be recorded soon, camera-only (no RFID/barcode), and
no hard deadline. So instead of hand-labelling hundreds of raw hours to train a
piece detector up front (slow, high-risk, the classic camera-only stall), we:

1. Make the **measurable-now** signals (idle / away / working / supervisor) fair
   and defensible.
2. Turn the **running deployment into a data collector** — it saves only the
   frames it's unsure about, pre-curating exactly the data piece-counting will
   need.
3. Ship the **training pipeline** that consumes that data, ready for when it
   accumulates.

Piece-counting / cycle-time becomes the next step, trained on real curated data
rather than guesses.

---

## What shipped

### Finding 9 — dev-admin auto-seed hardening (audit closed)
The seeder now refuses to run unless **both** `ENVIRONMENT=development` **and**
`SEED_DEV_ADMIN=true`, and warns on the weak default password. A misconfigured
`ENVIRONMENT` can no longer create a well-known superuser. This closes the last
open audit finding.

### Finding 12 — fair activity FSM (time-weighted)
The FSM confirmed states by **counting frames** in the debounce window. Under the
variable frame rates the ingestion layer produces (reconnects, fps spikes), a
short burst of frames could swing the state. It now weights each sample by the
**seconds it represents**, so the debounce is a true *majority-of-time* rule. A
transition requires the winner to hold >50% of the window's time and at least
~3s of evidence.
- Verified: a 5-frame (0.5s) idle burst does NOT flip a long-working worker;
  40s of sustained idle does.

### Machine-running signal (optical flow — no training)
The FSM had a `machine_running` slot hardwired to `False`, so "is this person
actually sewing" was guessed from body motion (a sewing worker sits still and
read as idle — unfair). New `MachineRunningDetector` computes dense optical-flow
magnitude inside each **machine zone**; sustained motion there = machine running.
Classical CV, works on day one.
- Wired through the pipeline: **detection** (the only engine with raw pixels)
  computes a per-workstation `machine_running` map → **tracking** forwards it →
  **activity** feeds it into the FSM. Entirely behind `MACHINE_FLOW_ENABLED`
  (off by default); when off, the chain degrades gracefully (no key, defaults
  False, no crashes — verified).
- **Needs calibration:** the flow threshold depends on resolution/fps/machine.
  `calibrate_threshold()` derives a per-camera value from a clip with known
  running/idle segments — this is what your calibration footage is for. Ships
  with a conservative default until then.

### Capture pipeline — the data flywheel
New `CaptureWriter` saves only **uncertain** frames (ambiguous detection
confidence, unstable state, detection-count anomalies, future cycle-boundary
hints) with a JSON sidecar (`labeled: false`), rate-limited per camera, **off by
default for privacy** (`CAPTURE_ENABLED`). Wired into the detection engine.
- Verified: flags the right frames, ignores confident ones, rate-limits, writes
  correct sidecars, captures nothing when disabled.

### Training lifecycle (consumes the flywheel)
- `ml/labeling/export_dataset.py` — turns labelled captures into a YOLO dataset
  (images/labels/data.yaml), ignoring unlabelled ones.
- `ml/training/registry.py` — register a trained run in MLflow's Model Registry
  and promote Staging→Production (the missing link the docs described), so the
  detection service can pull the current Production model. Extends the existing
  `train_detection.py` rather than replacing it.

See `docs/steps/step8_runbook.md` for the end-to-end operational flow.

---

## How it was verified (I ran all of it)

`tests/test_step8.py` — 7 passing: FSM burst-resistance + sustained-idle,
capture flagging/sidecar/rate-limit/off-by-default, optical-flow
moving-vs-static, calibration separation. Plus an end-to-end simulation of the
`detection → tracking → activity` machine_running hand-off, including the
feature-off safety path.

```
pytest tests/test_step8.py -v          # 7 passed
```

> Honest caveats:
> - **Machine-flow threshold is uncalibrated** until your footage arrives — the
>   detector is correct (moving ≫ static, verified), but the running/idle cut
>   point is factory-specific. First task with the calibration clip: run
>   `calibrate_threshold()` and set per-camera values.
> - The optical-flow signal assumes a **`machine` zone** is defined per
>   workstation (the same zone system the tracker uses). Stations without a
>   machine zone simply get no machine signal and fall back to motion.
> - End-to-end across live Redis + GPU is a server-side integration check;
>   the logic and the cross-engine contract are proven here.

---

## What's next (Step 9+): piece-counting / cycle-time

With the flywheel running and a calibration clip in hand, the path is:
1. Define a `bundle`/`piece` class and label a first batch from captures.
2. Train the detector (pipeline ready), register + promote.
3. Build a per-workstation **cycle FSM** (bundle present → sewing → output) to
   measure time-per-piece, then aggregate per garment across stations.
This is the harder, camera-only part — now de-risked because it trains on real,
pre-curated data instead of raw footage.
