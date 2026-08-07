/** Shared coordinate helpers for the zone viewer/editor.
 *
 * All zone polygons are stored in the camera's *native* pixel space so that
 * an edit at 1280×720 remains meaningful when the camera is later upgraded
 * to 1920×1080. The SVG's viewBox is set to those native dimensions and
 * `preserveAspectRatio="xMidYMid meet"` handles the letterboxing that lets
 * the frame scale responsively without deforming.
 *
 * `screenToNative` uses the browser's own SVG CTM (current transformation
 * matrix) to map a pointer's client-space coordinate into native pixel
 * space. Doing it through the CTM (rather than `getBoundingClientRect` +
 * hand-rolled math) means preserveAspectRatio letterboxing, `zoom`, and
 * page transforms are all handled correctly by the browser. */
export function screenToNative(
  svg: SVGSVGElement,
  clientX: number,
  clientY: number,
): { x: number; y: number } | null {
  const pt = svg.createSVGPoint();
  pt.x = clientX;
  pt.y = clientY;
  const ctm = svg.getScreenCTM();
  if (!ctm) return null;
  const p = pt.matrixTransform(ctm.inverse());
  return { x: p.x, y: p.y };
}

export type Point = [number, number];
export type Polygon = Point[];

/** Return the axis-aligned bounding rectangle of a polygon. */
export function bbox(poly: Polygon): { x1: number; y1: number; x2: number; y2: number } {
  const xs = poly.map((p) => p[0]);
  const ys = poly.map((p) => p[1]);
  return {
    x1: Math.min(...xs),
    y1: Math.min(...ys),
    x2: Math.max(...xs),
    y2: Math.max(...ys),
  };
}

/** Build a 4-point axis-aligned quad polygon from a bounding rectangle,
 * winding TL → TR → BR → BL (same order the discovery script and the
 * existing zones use, so read-side callers don't see a shape change). */
export function polygonFromBBox(x1: number, y1: number, x2: number, y2: number): Polygon {
  return [
    [x1, y1],
    [x2, y1],
    [x2, y2],
    [x1, y2],
  ];
}

export const MIN_BOX_PX = 24;

/** Clamp a bbox to the frame + enforce the minimum edge. When the caller was
 * dragging a corner and hit an edge, we hold that edge in place and let the
 * opposite side move — this is what feels natural to a user resizing a box
 * against the frame boundary. */
export function clampBBox(
  x1: number, y1: number, x2: number, y2: number,
  frameW: number, frameH: number,
): { x1: number; y1: number; x2: number; y2: number } {
  let nx1 = Math.max(0, Math.min(frameW, x1));
  let ny1 = Math.max(0, Math.min(frameH, y1));
  let nx2 = Math.max(0, Math.min(frameW, x2));
  let ny2 = Math.max(0, Math.min(frameH, y2));
  // Normalise: ensure x1<x2, y1<y2.
  if (nx1 > nx2) [nx1, nx2] = [nx2, nx1];
  if (ny1 > ny2) [ny1, ny2] = [ny2, ny1];
  // Enforce minimum edge by expanding the side that has room.
  if (nx2 - nx1 < MIN_BOX_PX) {
    if (nx1 + MIN_BOX_PX <= frameW) nx2 = nx1 + MIN_BOX_PX;
    else nx1 = nx2 - MIN_BOX_PX;
  }
  if (ny2 - ny1 < MIN_BOX_PX) {
    if (ny1 + MIN_BOX_PX <= frameH) ny2 = ny1 + MIN_BOX_PX;
    else ny1 = ny2 - MIN_BOX_PX;
  }
  return { x1: nx1, y1: ny1, x2: nx2, y2: ny2 };
}
