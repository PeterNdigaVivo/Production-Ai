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

  // Marker sizes scale with frame resolution so the overlay reads at both
  // 720p and 1080p. The previous absolute values (r=5, strokeWidth=2) were
  // visually invisible against a downscaled frame in the browser; anchor
  // to a fraction of the shorter frame edge.
  const shortEdge = Math.min(frameW, frameH);
  const dotR = Math.max(12, shortEdge * 0.014);           // ~10px at 720p
  const dotStroke = Math.max(3, shortEdge * 0.004);
  const polyStroke = Math.max(4, shortEdge * 0.005);
  const cutoffStroke = Math.max(4, shortEdge * 0.005);
  const proposalFontPx = Math.max(24, shortEdge * 0.028); // large enough to read after scale-down
  const skippedFontPx = Math.max(22, shortEdge * 0.025);
  const cutoffFontPx = Math.max(24, shortEdge * 0.028);

  // If the endpoint returned data but every list is empty, the operator
  // would see a completely blank overlay and think the toggle broke. Note
  // that condition explicitly (banner-in-svg).
  const nothingToDraw =
    data.proposals.length === 0
    && data.skipped_inside_existing_zone.length === 0
    && !(cutoff > 0);

  return (
    <g style={{ pointerEvents: "none" }}>
      {/* Far cutoff — operators above this line are too small to track. */}
      {cutoff > 0 && (
        <>
          <line
            x1={0} y1={cutoff} x2={frameW} y2={cutoff}
            stroke="rgba(163, 230, 53, 0.9)"
            strokeWidth={cutoffStroke}
            strokeDasharray="16 10"
            vectorEffect="non-scaling-stroke"
          />
          <rect
            x={8} y={cutoff - cutoffFontPx - 10}
            width={cutoffFontPx * 12} height={cutoffFontPx + 8}
            fill="rgba(0, 0, 0, 0.6)" rx={4}
          />
          <text
            x={16} y={cutoff - 8}
            fill="rgba(163, 230, 53, 1)"
            style={{ fontSize: cutoffFontPx, fontWeight: 700 }}
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
              fill="rgba(163, 230, 53, 0.20)"
              stroke="rgba(163, 230, 53, 1)"
              strokeWidth={polyStroke}
              strokeDasharray="14 8"
              vectorEffect="non-scaling-stroke"
            />
            <text
              x={p.center[0]} y={p.center[1]}
              textAnchor="middle" dominantBaseline="middle"
              fill="rgba(163, 230, 53, 1)"
              stroke="black" strokeWidth={2} paintOrder="stroke"
              style={{ fontSize: proposalFontPx, fontWeight: 700 }}
            >
              {p.label} · {Math.round(p.dwell_seconds)}s
            </text>
          </g>
        );
      })}

      {/* Skipped-inside dwell centres — the seats we DIDN'T re-propose
          because they land inside an existing zone. Bigger dot + high-
          contrast label so they're actually readable on a downscaled
          frame. */}
      {data.skipped_inside_existing_zone.map((s, i) => (
        <g key={`skip-in-${i}`}>
          <circle
            cx={s.center[0]} cy={s.center[1]}
            r={dotR}
            fill="rgba(56, 189, 248, 0.95)"
            stroke="white" strokeWidth={dotStroke}
            vectorEffect="non-scaling-stroke"
          />
          <text
            x={s.center[0] + dotR + 6} y={s.center[1] - dotR - 4}
            fill="rgba(56, 189, 248, 1)"
            stroke="black" strokeWidth={2} paintOrder="stroke"
            style={{ fontSize: skippedFontPx, fontWeight: 700 }}
          >
            {Math.round(s.dwell_seconds)}s
          </text>
        </g>
      ))}

      {/* Data present but nothing to draw — surface that instead of
          rendering an empty overlay the operator can't tell apart from a
          broken toggle. */}
      {nothingToDraw && (
        <g>
          <rect
            x={frameW * 0.05} y={frameH * 0.05}
            width={frameW * 0.9} height={proposalFontPx * 2}
            fill="rgba(0, 0, 0, 0.7)" rx={6}
          />
          <text
            x={frameW * 0.5} y={frameH * 0.05 + proposalFontPx * 1.2}
            textAnchor="middle"
            fill="rgba(163, 230, 53, 1)"
            style={{ fontSize: proposalFontPx, fontWeight: 700 }}
          >
            Discovery loaded but it has no proposals, skipped centres, or far cutoff to show.
          </text>
        </g>
      )}
    </g>
  );
}
