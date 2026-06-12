# machine-activity (Phase 2)

Inferring sewing-machine state (RUNNING / STOPPED / UNKNOWN) purely from video.

## Approach

1. Crop the `machine` zone polygon per workstation.
2. Compute temporal variance / optical flow magnitude in the crop.
3. Smooth with EMA; threshold to {RUNNING, STOPPED}.
4. Publish `machine_state_changed` events on transitions (with debounce).

## Stub

A working stub will land in Phase 2; the activity-engine FSM already accepts a
`machine_running` boolean which this service will provide.
