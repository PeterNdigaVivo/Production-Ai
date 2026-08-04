"""Decisions-driven zone promotion.

Third stage of the camera-onboarding pipeline:

    discover_zones.py  →  human review in chat  →  promote_zones.py
    (proposals.json)      (decisions.json)          (workstations + zones)

Reads a `proposals.json` produced by `discover_zones.py` and a companion
`decisions.json` written by the human reviewer. Every proposal MUST have a
decision — silence must never promote. Approvals become workstations +
seat zones; merges union their bounding boxes into the target's polygon;
rejections are recorded and discarded.

Same rails as the earlier one-off `insert_stations_4_5.py`
(idempotent by name, explicit `layout_version=1`, atomic single-
transaction insert, overlap-safe, never mutates existing rows) — this
tool supersedes that one-off.

Usage
-----

    docker compose exec backend python -m app.scripts.promote_zones \
        --proposals /tmp/discover_zones/<camera_id>/proposals.json \
        --decisions /tmp/discover_zones/<camera_id>/decisions.json

decisions.json schema
---------------------

    {
      "camera_id": "<uuid>",           // MUST match proposals.json's
      "line":      "Line A",           // line NAME; must exist — never auto-created
      "decisions": [
        {"label": "S4", "action": "approve", "name": "Station 6"},
        {"label": "S5", "action": "merge",   "into": "S4"},
        {"label": "S6", "action": "reject",  "reason": "tea corner"}
      ]
    }

Actions:
  * approve — insert as its own workstation. `name` optional; if absent,
    auto-assigned as the next free "Station N" on the target line.
  * merge   — union this proposal's bbox with the target's, becoming part
    of the target's polygon. Target MUST be an `approve` decision.
    Chained merges are disallowed.
  * reject  — discard; `reason` is recorded in output but ignored otherwise.

Stop conditions (any → exit 2, nothing inserted):
  * proposals ↔ decisions camera_id mismatch
  * unreviewed proposal (some label with no decision)
  * decision references a label not in proposals
  * duplicate decision labels
  * merge target is not an `approve`
  * unknown action string
  * `line` name not found in the database
  * any promoted polygon escapes the frame recorded in proposals.json
  * any promoted polygon overlaps an EXISTING zone on this camera (unless
    that zone is on the workstation we'd be reusing idempotently — see
    the [idem] path)
  * any two promoted polygons overlap each other
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.db.models import ProductionLine, Workstation, Zone


VALID_ACTIONS = ("approve", "merge", "reject")


# ---------------------------------------------------------------------------- #
# Pure helpers (unit-tested; no I/O)
# ---------------------------------------------------------------------------- #
def _bbox_from_polygon(polygon: list[list[float]]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    return min(xs), min(ys), max(xs), max(ys)


def _bbox_union(*bboxes: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    return (
        min(b[0] for b in bboxes), min(b[1] for b in bboxes),
        max(b[2] for b in bboxes), max(b[3] for b in bboxes),
    )


def _polygon_from_bbox(bbox: tuple[float, float, float, float]) -> list[list[float]]:
    x1, y1, x2, y2 = bbox
    return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]


def _bboxes_overlap(a, b) -> bool:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    return not (ax2 <= bx1 or bx2 <= ax1 or ay2 <= by1 or by2 <= ay1)


def _polygon_in_frame(polygon: list[list[float]], frame_w: int, frame_h: int) -> bool:
    return all(0 <= x <= frame_w and 0 <= y <= frame_h for x, y in polygon)


def _next_station_number(existing_names: set[str]) -> int:
    """Highest 'Station <n>' + 1. Case-insensitive on 'Station'."""
    highest = 0
    for name in existing_names:
        parts = name.strip().split()
        if len(parts) == 2 and parts[0].lower() == "station" and parts[1].isdigit():
            highest = max(highest, int(parts[1]))
    return highest + 1


def validate_decisions(proposals: dict, decisions: dict) -> None:
    """Raise ValueError on any misuse. Called before any DB work."""
    if not isinstance(proposals.get("proposals"), list):
        raise ValueError("proposals.json missing 'proposals' array")
    if not isinstance(decisions.get("decisions"), list):
        raise ValueError("decisions.json missing 'decisions' array")

    p_cam = proposals.get("camera_id")
    d_cam = decisions.get("camera_id")
    if p_cam != d_cam:
        raise ValueError(
            f"camera_id mismatch: proposals={p_cam!r} decisions={d_cam!r}"
        )
    if not decisions.get("line"):
        raise ValueError("decisions.json missing 'line' name")

    p_labels = {p["label"] for p in proposals["proposals"]}
    d_labels = [d.get("label") for d in decisions["decisions"]]

    dupes = sorted({lbl for lbl in d_labels if d_labels.count(lbl) > 1})
    if dupes:
        raise ValueError(f"duplicate decision labels: {dupes}")

    unknown = sorted(set(d_labels) - p_labels)
    if unknown:
        raise ValueError(f"decisions reference labels not in proposals: {unknown}")

    unreviewed = sorted(p_labels - set(d_labels))
    if unreviewed:
        raise ValueError(
            f"unreviewed proposals: {unreviewed} — every proposal must have "
            f"a decision (silence must never promote)"
        )

    action_by_label = {d["label"]: d["action"] for d in decisions["decisions"]}
    for d in decisions["decisions"]:
        action = d.get("action")
        if action not in VALID_ACTIONS:
            raise ValueError(
                f"decision {d.get('label')!r}: unknown action {action!r} "
                f"(valid: {VALID_ACTIONS})"
            )
        if action == "merge":
            target = d.get("into")
            if not target:
                raise ValueError(f"merge {d['label']!r} missing 'into'")
            if target == d["label"]:
                raise ValueError(f"merge {d['label']!r} → itself")
            if target not in action_by_label:
                raise ValueError(
                    f"merge {d['label']!r} → {target!r} but {target!r} has no decision"
                )
            if action_by_label[target] != "approve":
                raise ValueError(
                    f"merge {d['label']!r} → {target!r} but {target!r} is "
                    f"{action_by_label[target]!r} (must be 'approve'; "
                    f"chained merges are disallowed)"
                )


def resolve_promotions(proposals: dict, decisions: dict) -> list[dict]:
    """Post-validation: build the concrete promotion list.

    Returns list of dicts, one per approve decision:
      {"labels": ["S4", "S5", ...], "polygon": [[..]..], "explicit_name": str|None}

    Order preserves decisions.json order (deterministic for auto-numbering).
    """
    p_by_label = {p["label"]: p for p in proposals["proposals"]}
    merges_into: dict[str, list[str]] = defaultdict(list)
    for d in decisions["decisions"]:
        if d["action"] == "merge":
            merges_into[d["into"]].append(d["label"])

    promotions: list[dict] = []
    for d in decisions["decisions"]:
        if d["action"] != "approve":
            continue
        labels = [d["label"]] + merges_into.get(d["label"], [])
        bboxes = [_bbox_from_polygon(p_by_label[lbl]["polygon"]) for lbl in labels]
        polygon = _polygon_from_bbox(_bbox_union(*bboxes))
        promotions.append({
            "labels": labels,
            "polygon": polygon,
            "explicit_name": d.get("name"),
        })
    return promotions


def assign_names(promotions: list[dict], existing_workstation_names: set[str]) -> None:
    """Mutates each promotion to add `assigned_name`.

    Explicit names claim their slot first; auto-numbered promotions fill in
    around them starting from the next free 'Station N'.
    """
    taken = {n.lower() for n in existing_workstation_names}
    # Pass 1: explicit names (claim slots).
    for p in promotions:
        if p["explicit_name"]:
            p["assigned_name"] = p["explicit_name"]
            taken.add(p["explicit_name"].lower())
    # Pass 2: auto-number the rest, skipping any taken slots.
    for p in promotions:
        if p["explicit_name"]:
            continue
        n = _next_station_number(taken)
        while f"station {n}" in taken:
            n += 1
        p["assigned_name"] = f"Station {n}"
        taken.add(p["assigned_name"].lower())


# ---------------------------------------------------------------------------- #
# I/O
# ---------------------------------------------------------------------------- #
def load_json_file(path: str) -> dict:
    """Read a user-authored JSON file, tolerating a UTF-8 BOM.

    Windows PowerShell's `Set-Content -Encoding utf8` writes a BOM by
    default; the built-in `json` module cannot parse that. `utf-8-sig`
    strips a leading BOM if present and is a no-op otherwise, so this
    is safe on POSIX-written files too.

    Raises ValueError with a message shaped for the STOPPED report on any
    IO or parse failure, so the caller can emit one clean stderr line
    instead of a raw traceback.
    """
    p = Path(path)
    try:
        raw = p.read_text(encoding="utf-8-sig")
    except OSError as e:
        raise ValueError(f"{path}: cannot read ({e.__class__.__name__}: {e})") from e
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"{path}: not valid JSON ({e.msg} at line {e.lineno} col {e.colno})") from e


async def _existing_zones_for_camera(db, camera_id: str) -> list[dict]:
    stmt = (
        select(Zone, Workstation.name, Workstation.id)
        .join(Workstation, Workstation.id == Zone.workstation_id)
        .where(Workstation.camera_id == uuid.UUID(camera_id))
    )
    return [
        {"polygon": z.polygon, "kind": z.kind, "workstation_name": name,
         "workstation_id": ws_id, "layout_version": z.layout_version}
        for z, name, ws_id in (await db.execute(stmt)).all()
    ]


# ---------------------------------------------------------------------------- #
# Main
# ---------------------------------------------------------------------------- #
async def amain(args) -> int:
    # ---- 0) File loads (tolerate UTF-8 BOM; malformed JSON is a clean stop) - #
    try:
        proposals = load_json_file(args.proposals)
        decisions = load_json_file(args.decisions)
    except ValueError as e:
        print(f"STOPPED: {e}", file=sys.stderr)
        return 2

    # ---- 1) Pure validation (no DB yet) ------------------------------------ #
    try:
        validate_decisions(proposals, decisions)
    except ValueError as e:
        print(f"STOPPED: {e}", file=sys.stderr)
        return 2

    camera_id = proposals["camera_id"]
    line_name = decisions["line"]
    frame_w = int(proposals["sampling"]["frame"]["w"])
    frame_h = int(proposals["sampling"]["frame"]["h"])

    promotions = resolve_promotions(proposals, decisions)
    rejections = [d for d in decisions["decisions"] if d["action"] == "reject"]

    print(f"[init] camera={camera_id}")
    print(f"[init] line={line_name}")
    print(f"[init] frame={frame_w}x{frame_h} "
          f"(from proposals.json — per-camera, not hard-coded)")
    print(f"[init] approvals={len(promotions)}  "
          f"merges={sum(len(p['labels']) - 1 for p in promotions)}  "
          f"rejections={len(rejections)}")

    # ---- 2) Bounds check (against per-camera frame dims) ------------------- #
    for p in promotions:
        if not _polygon_in_frame(p["polygon"], frame_w, frame_h):
            print(
                f"STOPPED: promoted polygon for labels {p['labels']} escapes "
                f"frame {frame_w}x{frame_h}: {p['polygon']}",
                file=sys.stderr,
            )
            return 2

    # ---- 3) DB work ------------------------------------------------------- #
    settings = get_settings()
    engine = create_async_engine(settings.database_url, echo=False)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as db:
            line = (await db.execute(
                select(ProductionLine).where(ProductionLine.name == line_name)
            )).scalar_one_or_none()
            if line is None:
                print(f"STOPPED: no ProductionLine named {line_name!r} "
                      f"(never auto-created)", file=sys.stderr)
                return 2

            existing_zones = await _existing_zones_for_camera(db, camera_id)
            existing_ws_names = {
                z["workstation_name"] for z in existing_zones
            }
            # also names of workstations on this line without zones yet
            line_ws = (await db.execute(
                select(Workstation.name).where(Workstation.line_id == line.id)
            )).all()
            existing_ws_names.update(n for (n,) in line_ws)

            print(f"[init] existing zones on this camera: {len(existing_zones)}")

            assign_names(promotions, existing_ws_names)

            # ---- 4) Overlap check ------------------------------------------ #
            # Reuse path: if we'll idempotently reuse an existing workstation,
            # its own existing zone is NOT counted as a colliding zone (we won't
            # insert a second one — see the [idem] path below).
            reuse_names = {p["assigned_name"] for p in promotions
                           if p["assigned_name"] in existing_ws_names}
            colliding_existing = [
                z for z in existing_zones if z["workstation_name"] not in reuse_names
            ]
            for p in promotions:
                if p["assigned_name"] in reuse_names:
                    continue
                nb = _bbox_from_polygon(p["polygon"])
                for z in colliding_existing:
                    if _bboxes_overlap(nb, _bbox_from_polygon(z["polygon"])):
                        print(
                            f"STOPPED: promoted polygon for "
                            f"{p['assigned_name']} ({p['labels']}) overlaps "
                            f"existing zone on workstation "
                            f"{z['workstation_name']} (kind={z['kind']}, "
                            f"v={z['layout_version']}) polygon={z['polygon']}",
                            file=sys.stderr,
                        )
                        return 2

            # between new promotions
            for i, p in enumerate(promotions):
                for j in range(i + 1, len(promotions)):
                    q = promotions[j]
                    if _bboxes_overlap(_bbox_from_polygon(p["polygon"]),
                                       _bbox_from_polygon(q["polygon"])):
                        print(
                            f"STOPPED: promoted polygons {p['assigned_name']} "
                            f"and {q['assigned_name']} overlap each other",
                            file=sys.stderr,
                        )
                        return 2

            print("[check] no overlap with existing or between promotions")

            # ---- 5) Atomic insert ----------------------------------------- #
            created = []
            for p in promotions:
                name = p["assigned_name"]
                ws = (await db.execute(
                    select(Workstation).where(
                        Workstation.line_id == line.id,
                        Workstation.name == name,
                    )
                )).scalar_one_or_none()

                if ws is None:
                    ws = Workstation(
                        line_id=line.id,
                        camera_id=uuid.UUID(camera_id),
                        code=name.replace(" ", ""),
                        name=name,
                    )
                    db.add(ws)
                    await db.flush()
                    ws_created = True
                else:
                    ws_created = False
                    print(f"[idem] {name} already exists ({ws.code}); reusing")

                z_existing = (await db.execute(
                    select(Zone).where(
                        Zone.workstation_id == ws.id,
                        Zone.kind == "seat",
                    )
                )).scalar_one_or_none()

                if z_existing is not None:
                    zone_id = z_existing.id
                    zone_v = z_existing.layout_version
                    zone_created = False
                    print(f"[idem] {name} already has a seat zone "
                          f"({zone_id}, v={zone_v}); skipping zone insert")
                else:
                    z = Zone(
                        workstation_id=ws.id, kind="seat",
                        polygon=p["polygon"], layout_version=1,
                    )
                    db.add(z)
                    await db.flush()
                    zone_id = z.id
                    zone_v = z.layout_version
                    zone_created = True

                created.append({
                    "assigned_name": name,
                    "labels": p["labels"],
                    "workstation_id": str(ws.id),
                    "workstation_created": ws_created,
                    "zone_id": str(zone_id),
                    "zone_layout_version": zone_v,
                    "zone_created": zone_created,
                    "polygon": p["polygon"],
                })

            await db.commit()

            # ---- 6) Report ------------------------------------------------ #
            print("\n[done] promotions:")
            for c in created:
                ws_tag = "created" if c["workstation_created"] else "reused"
                z_tag = "created" if c["zone_created"] else "reused"
                merged = " ← " + "+".join(c["labels"]) if len(c["labels"]) > 1 else ""
                print(f"  {c['assigned_name']}{merged}  "
                      f"workstation_id={c['workstation_id']} [{ws_tag}]")
                print(f"    zone_id={c['zone_id']} v={c['zone_layout_version']} "
                      f"kind=seat [{z_tag}]  polygon={c['polygon']}")
            if rejections:
                print("\n[done] rejections:")
                for r in rejections:
                    print(f"  {r['label']}: {r.get('reason', '(no reason)')}")

            # write a machine-readable log next to decisions.json
            log_path = Path(args.decisions).with_name("promotion_log.json")
            log_path.write_text(json.dumps({
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "camera_id": camera_id,
                "line": line_name,
                "frame": {"w": frame_w, "h": frame_h},
                "promoted": created,
                "rejected": [
                    {"label": r["label"], "reason": r.get("reason")}
                    for r in rejections
                ],
            }, indent=2, default=str))
            print(f"\n[log] {log_path}")

            print("\n[verify] select w.name, z.polygon from zones z "
                  "join workstations w on w.id=z.workstation_id "
                  "order by w.name:")
            verify_stmt = (
                select(Workstation.name, Zone.polygon)
                .join(Zone, Zone.workstation_id == Workstation.id)
                .order_by(Workstation.name)
            )
            for name, polygon in (await db.execute(verify_stmt)).all():
                print(f"  {name}  {polygon}")
    finally:
        await engine.dispose()

    return 0


def _parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Promote reviewed dwell-discovery proposals into "
                    "workstations + seat zones. Atomic, idempotent, "
                    "overlap-safe. Reads two JSON files; writes DB.")
    p.add_argument("--proposals", required=True,
                   help="path to proposals.json (from discover_zones.py)")
    p.add_argument("--decisions", required=True,
                   help="path to decisions.json (see module docstring)")
    return p.parse_args(argv)


if __name__ == "__main__":
    sys.exit(asyncio.run(amain(_parse_args())))
