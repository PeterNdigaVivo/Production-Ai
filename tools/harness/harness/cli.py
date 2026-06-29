"""Command-line entry point for the Step-1 harness.

Examples (run from tools/harness/):

  # 1. Pure-logic tracker report against ground truth (no Redis, no GPU):
  python -m harness.cli report --scene enter_leave

  # 2. Render an annotated video you can watch (shows the ghost track):
  python -m harness.cli overlay --scene enter_leave --out ghost_demo.mp4

  # 3. Render a single annotated PNG at a given time:
  python -m harness.cli frame --scene enter_leave --t 15 --out frame.png

  # 4. Publish synthetic frames into Redis for the REAL pipeline to consume:
  python -m harness.cli publish --scene enter_leave --camera 11111111-1111-1111-1111-111111111111 \
      --redis redis://localhost:6379/0

The detector mode for report/overlay/frame is the stub (perfect detection) so
the TRACKER is isolated. Set HARNESS_DETECTOR=yolo only for publish+real pipeline.
"""
from __future__ import annotations

import argparse
import sys

from .scene import scene_steady_two, scene_enter_leave, scene_leave_return
from .tracker_runner import run_tracker_over_scene, make_current_tracker

SCENES = {
    "steady_two": scene_steady_two,
    "enter_leave": scene_enter_leave,
    "leave_return": scene_leave_return,
}


def _get_scene(name: str):
    if name not in SCENES:
        print(f"unknown scene '{name}'. choices: {', '.join(SCENES)}", file=sys.stderr)
        sys.exit(2)
    return SCENES[name]()


def cmd_report(args):
    scene = _get_scene(args.scene)
    tracker = make_current_tracker()
    m = run_tracker_over_scene(tracker, scene)
    print("=" * 60)
    print(f"SCENE: {args.scene}   tracker: CURRENT (vendored)")
    print("=" * 60)
    print(m.summary())
    if m.ghost_tracks > 0:
        print("\n>>> GHOST TRACKS PRESENT -- this is Finding 1 (tracker aging bug).")
    else:
        print("\n>>> No ghosts on this scene.")


def cmd_overlay(args):
    from .overlay import render_overlay_video
    scene = _get_scene(args.scene)
    render_overlay_video(make_current_tracker(), scene, args.out)
    print(f"wrote {args.out} -- open it and watch the track IDs.")


def cmd_frame(args):
    from .overlay import render_overlay_frame_png
    scene = _get_scene(args.scene)
    render_overlay_frame_png(make_current_tracker(), scene, args.t, args.out)
    print(f"wrote {args.out}")


def cmd_publish(args):
    from .publisher import publish_scene
    scene = _get_scene(args.scene)
    publish_scene(scene, camera_id=args.camera, redis_url=args.redis,
                  realtime=not args.fast, loop=args.loop)


def build_parser():
    p = argparse.ArgumentParser(prog="harness", description="Production-AI Step-1 synthetic harness")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("report", help="ground-truth tracker metrics (no Redis)")
    r.add_argument("--scene", default="enter_leave")
    r.set_defaults(func=cmd_report)

    o = sub.add_parser("overlay", help="write annotated .mp4")
    o.add_argument("--scene", default="enter_leave")
    o.add_argument("--out", default="overlay.mp4")
    o.set_defaults(func=cmd_overlay)

    f = sub.add_parser("frame", help="write a single annotated .png")
    f.add_argument("--scene", default="enter_leave")
    f.add_argument("--t", type=float, default=15.0)
    f.add_argument("--out", default="frame.png")
    f.set_defaults(func=cmd_frame)

    pub = sub.add_parser("publish", help="publish frames to Redis for the real pipeline")
    pub.add_argument("--scene", default="enter_leave")
    pub.add_argument("--camera", required=True, help="camera UUID (matches a row in cameras)")
    pub.add_argument("--redis", default="redis://localhost:6379/0")
    pub.add_argument("--fast", action="store_true", help="blast frames (ignore fps pacing)")
    pub.add_argument("--loop", action="store_true", help="repeat the scene forever")
    pub.set_defaults(func=cmd_publish)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
