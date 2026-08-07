"use client";

import { useEffect } from "react";
import useSWR from "swr";
import { ApiError, api } from "@/lib/api";

type Proposal = {
  label: string;
  polygon: [number, number][];
  bbox: number[];
  center: [number, number];
  dwell_seconds: number;
};

type Skipped = {
  center: [number, number];
  dwell_seconds: number;
};

type DiscoveryDoc = {
  generated_at: string;
  camera_id: string;
  sampling: {
    frame: { w: number; h: number };
    far_cutoff_px: number;
    actual_seconds: number;
  };
  proposals: Proposal[];
  skipped_too_far: Skipped[];
  skipped_inside_existing_zone: Skipped[];
};

/** Non-interactive reference overlay for the editor.
 *
 * Draws the last discover_zones run's dwell measurements on top of the
 * current frame so operators can position seats against empirical evidence
 * (measured operator dwell) rather than eyeballing the raw JPEG.
 *
 * Every element renders with `pointer-events: none` so the underlying
 * editable seats remain fully draggable through the overlay. */
export function DiscoveryOverlay({
  cameraId,
  frameW,
  frameH,
  onStatus,
}: {
  cameraId: string;
  frameW: number;
  frameH: number;
  onStatus: (status: { kind: "loading" } | { kind: "ok"; doc: DiscoveryDoc }
                    | { kind: "missing" } | { kind: "error"; message: string }) => void;
}) {
  const { data, error, isLoading } = useSWR<DiscoveryDoc>(
    `/api/v1/cameras/${cameraId}/discovery`,
    (p: string) => api<DiscoveryDoc>(p),
    { revalidateOnFocus: false, shouldRetryOnError: false },
  );

  // Push status upward so the page can render a friendly hint when the file
  // isn't there. Effect (not render-side) so the parent's state updates
  // don't feed back into another render of this component.
  useEffect(() => {
    if (isLoading) onStatus({ kind: "loading" });
    else if (error) {
      if (error instanceof ApiError && error.status === 404) onStatus({ kind: "missing" });
      else {
        const msg = error instanceof ApiError ? `${error.status} — ${error.detail}` :
                    String((error as Error)?.message ?? error);
        onStatus({ kind: "error", message: msg });
      }
    } else if (data) onStatus({ kind: "ok", doc: data });
  }, [data, error, isLoading, onStatus]);

  if (!data) return null;

  const cutoff = data.sampling.far_cutoff_px;
  return (
    <g style={{ pointerEvents: "none" }}>
      {/* Far cutoff — operators above this line are too small to track. */}
      {cutoff > 0 && (
        <>
          <line
            x1={0} y1={cutoff} x2={frameW} y2={cutoff}
            stroke="rgba(163, 230, 53, 0.75)"
            strokeWidth={2}
            strokeDasharray="8 6"
            vectorEffect="non-scaling-stroke"
          />
          <text
            x={12} y={cutoff - 6}
            fill="rgba(163, 230, 53, 0.95)"
            stroke="black" strokeWidth={0.4} paintOrder="stroke"
            style={{ fontSize: 13, fontWeight: 600 }}
          >
            far cutoff · {cutoff}px
          </text>
        </>
      )}

      {/* Proposal polygons — dashed so they don't compete with saved zones. */}
      {data.proposals.map((p) => {
        const pts = p.polygon.map(([x, y]) => `${x},${y}`).join(" ");
        return (
          <g key={p.label}>
            <polygon
              points={pts}
              fill="rgba(163, 230, 53, 0.12)"
              stroke="rgba(163, 230, 53, 0.85)"
              strokeWidth={2}
              strokeDasharray="6 4"
              vectorEffect="non-scaling-stroke"
            />
            <text
              x={p.center[0]} y={p.center[1]}
              textAnchor="middle" dominantBaseline="middle"
              fill="rgba(163, 230, 53, 1)"
              stroke="black" strokeWidth={0.5} paintOrder="stroke"
              style={{ fontSize: 14, fontWeight: 600 }}
            >
              {p.label} · {Math.round(p.dwell_seconds)}s
            </text>
          </g>
        );
      })}

      {/* Skipped-inside dwell centres — small dot + dwell label. These are
          the seats we DIDN'T re-propose because they land inside an
          existing zone; they still deserve visual confirmation. */}
      {data.skipped_inside_existing_zone.map((s, i) => (
        <g key={`skip-in-${i}`}>
          <circle
            cx={s.center[0]} cy={s.center[1]}
            r={5}
            fill="rgba(56, 189, 248, 0.9)"
            stroke="white" strokeWidth={1}
            vectorEffect="non-scaling-stroke"
          />
          <text
            x={s.center[0] + 8} y={s.center[1] - 8}
            fill="rgba(56, 189, 248, 1)"
            stroke="black" strokeWidth={0.4} paintOrder="stroke"
            style={{ fontSize: 12, fontWeight: 500 }}
          >
            {Math.round(s.dwell_seconds)}s
          </text>
        </g>
      ))}
    </g>
  );
}
