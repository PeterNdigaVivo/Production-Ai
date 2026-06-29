"""Export labeled captures into a YOLO-format training dataset.

The capture pipeline (services/common/capture) writes flagged frames + JSON
sidecars while the system runs. A human labels them (sets `labeled: true` and
fills `annotations`). This tool turns the labeled subset into a YOLO dataset
(images/ + labels/ + data.yaml) ready for ml/training/train_detection.py.

This is the second half of the data flywheel: capture -> label -> export ->
train -> deploy -> capture better. It deliberately ignores unlabeled captures,
so you train only on reviewed data.

Sidecar annotation format expected (added by the labeling step):
  {
    ...,
    "labeled": true,
    "image_wh": [W, H],
    "annotations": [
      {"class_id": 0, "xyxy": [x1,y1,x2,y2]},   # pixel coords
      ...
    ]
  }
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
from pathlib import Path


def _xyxy_to_yolo(xyxy, w, h):
    x1, y1, x2, y2 = xyxy
    cx = (x1 + x2) / 2.0 / w
    cy = (y1 + y2) / 2.0 / h
    bw = (x2 - x1) / w
    bh = (y2 - y1) / h
    return cx, cy, bw, bh


def export(capture_dir: str, out_dir: str, classes: list[str],
           val_split: float = 0.2, seed: int = 42) -> dict:
    cap = Path(capture_dir)
    out = Path(out_dir)
    rng = random.Random(seed)

    # gather labeled sidecars
    items = []
    for meta_path in cap.rglob("*.json"):
        try:
            meta = json.loads(meta_path.read_text())
        except Exception:
            continue
        if not meta.get("labeled") or not meta.get("annotations"):
            continue
        jpg = Path(meta.get("jpg", str(meta_path.with_suffix(".jpg"))))
        if not jpg.exists():
            continue
        items.append((jpg, meta))

    if not items:
        return {"images": 0, "note": "no labeled captures found"}

    rng.shuffle(items)
    n_val = int(len(items) * val_split)
    splits = {"val": items[:n_val], "train": items[n_val:]}

    for split, rows in splits.items():
        (out / "images" / split).mkdir(parents=True, exist_ok=True)
        (out / "labels" / split).mkdir(parents=True, exist_ok=True)
        for jpg, meta in rows:
            stem = jpg.stem
            shutil.copy(jpg, out / "images" / split / jpg.name)
            w, h = meta.get("image_wh", [None, None])
            lines = []
            for a in meta["annotations"]:
                if w and h:
                    cx, cy, bw, bh = _xyxy_to_yolo(a["xyxy"], w, h)
                    lines.append(f"{a['class_id']} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")
            (out / "labels" / split / f"{stem}.txt").write_text("\n".join(lines))

    data_yaml = out / "data.yaml"
    data_yaml.write_text(
        f"path: {out.resolve()}\n"
        f"train: images/train\n"
        f"val: images/val\n"
        f"nc: {len(classes)}\n"
        f"names: {classes}\n"
    )
    return {
        "images": len(items),
        "train": len(splits["train"]),
        "val": len(splits["val"]),
        "data_yaml": str(data_yaml),
    }


def main():
    ap = argparse.ArgumentParser(description="Export labeled captures to a YOLO dataset")
    ap.add_argument("--capture-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--classes", nargs="+", default=["person", "sewing_machine", "bundle"])
    ap.add_argument("--val-split", type=float, default=0.2)
    args = ap.parse_args()
    result = export(args.capture_dir, args.out_dir, args.classes, args.val_split)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
