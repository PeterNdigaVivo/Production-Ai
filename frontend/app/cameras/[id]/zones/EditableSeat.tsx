"use client";

import { useRef } from "react";
import {
  bbox,
  clampBBox,
  MIN_BOX_PX,
  Polygon,
  polygonFromBBox,
  screenToNative,
} from "./coords";

type Props = {
  svgRef: React.RefObject<SVGSVGElement>;
  workstationId: string;
  workstationName: string;
  polygon: Polygon;
  selected: boolean;
  frameW: number;
  frameH: number;
  onSelect: (workstationId: string) => void;
  onChange: (workstationId: string, next: Polygon) => void;
};

type DragMode = { kind: "body"; startPointer: { x: number; y: number }; startBBox: ReturnType<typeof bbox> }
              | { kind: "corner"; idx: 0 | 1 | 2 | 3; anchor: { x: number; y: number } }
              | null;

/** One interactive seat zone. Renders the polygon body + 4 corner handles,
 * and translates pointer drags into new bounding-box coordinates in the
 * frame's native pixel space via `screenToNative`.
 *
 * Only body-translate and corner-resize are supported here (stage 2 spec).
 * Add-new, delete, and freeform polygon editing are stage 3. */
export function EditableSeat(props: Props) {
  const { svgRef, workstationId, workstationName, polygon, selected,
          frameW, frameH, onSelect, onChange } = props;

  const dragRef = useRef<DragMode>(null);
  const box = bbox(polygon);

  function toNative(e: React.PointerEvent<SVGElement>) {
    const svg = svgRef.current;
    if (!svg) return null;
    return screenToNative(svg, e.clientX, e.clientY);
  }

  function beginBodyDrag(e: React.PointerEvent<SVGPolygonElement>) {
    e.stopPropagation();
    const p = toNative(e);
    if (!p) return;
    onSelect(workstationId);
    (e.currentTarget as Element).setPointerCapture(e.pointerId);
    dragRef.current = { kind: "body", startPointer: p, startBBox: bbox(polygon) };
  }

  function beginCornerDrag(idx: 0 | 1 | 2 | 3) {
    return (e: React.PointerEvent<SVGCircleElement>) => {
      e.stopPropagation();
      const p = toNative(e);
      if (!p) return;
      onSelect(workstationId);
      (e.currentTarget as Element).setPointerCapture(e.pointerId);
      // Anchor is the corner diagonally opposite; we hold it fixed and
      // move the dragged corner freely.
      const b = bbox(polygon);
      const anchor =
        idx === 0 ? { x: b.x2, y: b.y2 } :
        idx === 1 ? { x: b.x1, y: b.y2 } :
        idx === 2 ? { x: b.x1, y: b.y1 } :
                    { x: b.x2, y: b.y1 };
      dragRef.current = { kind: "corner", idx, anchor };
    };
  }

  function onPointerMove(e: React.PointerEvent<SVGElement>) {
    const drag = dragRef.current;
    if (!drag) return;
    const p = toNative(e);
    if (!p) return;

    if (drag.kind === "body") {
      const dx = p.x - drag.startPointer.x;
      const dy = p.y - drag.startPointer.y;
      const w = drag.startBBox.x2 - drag.startBBox.x1;
      const h = drag.startBBox.y2 - drag.startBBox.y1;
      // Clamp the top-left so the whole rectangle stays on-frame.
      const nx1 = Math.max(0, Math.min(frameW - w, drag.startBBox.x1 + dx));
      const ny1 = Math.max(0, Math.min(frameH - h, drag.startBBox.y1 + dy));
      onChange(workstationId, polygonFromBBox(nx1, ny1, nx1 + w, ny1 + h));
      return;
    }

    // corner: rebuild the bbox from the fixed anchor and the pointer.
    const clamped = clampBBox(
      Math.min(drag.anchor.x, p.x),
      Math.min(drag.anchor.y, p.y),
      Math.max(drag.anchor.x, p.x),
      Math.max(drag.anchor.y, p.y),
      frameW, frameH,
    );
    onChange(workstationId, polygonFromBBox(clamped.x1, clamped.y1, clamped.x2, clamped.y2));
  }

  function onPointerUp(e: React.PointerEvent<SVGElement>) {
    dragRef.current = null;
    try { (e.currentTarget as Element).releasePointerCapture(e.pointerId); } catch { /* ignore */ }
  }

  const points = polygon.map(([x, y]) => `${x},${y}`).join(" ");
  const centre = {
    x: (box.x1 + box.x2) / 2,
    y: (box.y1 + box.y2) / 2,
  };
  const cornerRadius = Math.max(6, Math.min(frameW, frameH) * 0.012);
  const strokeSelected = "#38bdf8";
  const strokeIdle = "rgba(56, 189, 248, 0.55)";
  const fill = selected ? "rgba(56, 189, 248, 0.30)" : "rgba(56, 189, 248, 0.15)";

  return (
    <g onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={onPointerUp}>
      <polygon
        points={points}
        fill={fill}
        stroke={selected ? strokeSelected : strokeIdle}
        strokeWidth={selected ? 4 : 3}
        vectorEffect="non-scaling-stroke"
        style={{ cursor: "move" }}
        onPointerDown={beginBodyDrag}
        onClick={(e) => { e.stopPropagation(); onSelect(workstationId); }}
      />
      <text
        x={centre.x}
        y={centre.y}
        textAnchor="middle"
        dominantBaseline="middle"
        fill="white"
        stroke="black"
        strokeWidth={0.5}
        paintOrder="stroke"
        style={{ fontSize: 18, fontWeight: 600, pointerEvents: "none" }}
      >
        {workstationName}
      </text>
      {selected && (
        <>
          {/* Corner handles are visually large + click targets even larger,
              since dragging exact SVG circles at cursor precision on a
              downscaled frame is finicky. */}
          {([[box.x1, box.y1, 0], [box.x2, box.y1, 1],
             [box.x2, box.y2, 2], [box.x1, box.y2, 3]] as const).map(([cx, cy, idx]) => (
            <circle
              key={idx}
              cx={cx}
              cy={cy}
              r={cornerRadius}
              fill="white"
              stroke={strokeSelected}
              strokeWidth={2}
              vectorEffect="non-scaling-stroke"
              style={{ cursor: cornerCursor(idx as 0 | 1 | 2 | 3) }}
              onPointerDown={beginCornerDrag(idx as 0 | 1 | 2 | 3)}
            />
          ))}
        </>
      )}
    </g>
  );
}

function cornerCursor(idx: 0 | 1 | 2 | 3): string {
  return idx === 0 || idx === 2 ? "nwse-resize" : "nesw-resize";
}
