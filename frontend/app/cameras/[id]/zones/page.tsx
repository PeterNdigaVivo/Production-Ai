"use client";

import { useCallback, useEffect, useState } from "react";
import useSWR from "swr";
import Link from "next/link";
import { api, apiBlob } from "@/lib/api";

type ZoneRow = {
  zone_id: string;
  workstation_id: string;
  workstation_name: string;
  kind: string;
  polygon: [number, number][];
  layout_version: number;
};

type FrameData = {
  objectUrl: string;
  width: number;
  height: number;
};

// Palette per zone kind. Read-only viewer, so we lean toward legibility over
// brand consistency; strokes are strong, fills are semi-transparent so the
// underlying frame still shows through.
const KIND_STYLE: Record<string, { stroke: string; fill: string }> = {
  seat: { stroke: "#38bdf8", fill: "rgba(56, 189, 248, 0.20)" },
  machine: { stroke: "#f59e0b", fill: "rgba(245, 158, 11, 0.20)" },
  input_tray: { stroke: "#a3e635", fill: "rgba(163, 230, 53, 0.20)" },
  output_tray: { stroke: "#f472b6", fill: "rgba(244, 114, 182, 0.20)" },
};
const DEFAULT_STYLE = { stroke: "#cbd5e1", fill: "rgba(203, 213, 225, 0.20)" };

export default function CameraZoneViewerPage({ params }: { params: { id: string } }) {
  const cameraId = params.id;
  const [frame, setFrame] = useState<FrameData | null>(null);
  const [frameError, setFrameError] = useState<string | null>(null);
  const [frameLoading, setFrameLoading] = useState(false);
  const [refreshTick, setRefreshTick] = useState(0);

  const {
    data: zones,
    error: zonesError,
    isLoading: zonesLoading,
  } = useSWR(`/api/v1/cameras/${cameraId}/zones`, (p) => api<ZoneRow[]>(p));

  const loadFrame = useCallback(async () => {
    setFrameLoading(true);
    setFrameError(null);
    try {
      const { blob, headers } = await apiBlob(`/api/v1/cameras/${cameraId}/frame`);
      // Actual frame dims from the payload — never hard-coded. Falls back to
      // the blob's intrinsic size only if headers are missing (they shouldn't).
      const w = Number(headers.get("X-Frame-Width")) || 0;
      const h = Number(headers.get("X-Frame-Height")) || 0;
      const objectUrl = URL.createObjectURL(blob);
      setFrame((prev) => {
        if (prev) URL.revokeObjectURL(prev.objectUrl);
        return { objectUrl, width: w, height: h };
      });
    } catch (e: any) {
      setFrameError(e?.message ?? "Failed to load frame");
    } finally {
      setFrameLoading(false);
    }
  }, [cameraId]);

  useEffect(() => {
    loadFrame();
  }, [loadFrame, refreshTick]);

  // Free the object URL on unmount so the browser doesn't leak the frame.
  useEffect(() => {
    return () => {
      if (frame) URL.revokeObjectURL(frame.objectUrl);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <div className="space-y-1">
          <div className="text-xs uppercase tracking-wide opacity-60">
            <Link href="/cameras" className="underline">Cameras</Link> <span aria-hidden>›</span> Zones
          </div>
          <h2 className="text-2xl font-semibold">Camera zones</h2>
          <div className="font-mono text-xs opacity-60">{cameraId}</div>
        </div>
        <button
          onClick={() => setRefreshTick((t) => t + 1)}
          disabled={frameLoading}
          className="rounded bg-slate-700 hover:bg-slate-600 px-3 py-2 text-sm font-semibold disabled:opacity-50"
        >
          {frameLoading ? "Refreshing…" : "↻ Refresh frame"}
        </button>
      </div>

      {frameError && (
        <div className="rounded border border-red-700 bg-red-950/40 p-3 text-sm text-red-200">
          {frameError}
        </div>
      )}
      {zonesError && (
        <div className="rounded border border-red-700 bg-red-950/40 p-3 text-sm text-red-200">
          Failed to load zones: {String(zonesError.message ?? zonesError)}
        </div>
      )}

      <ZoneOverlay
        frame={frame}
        zones={zones ?? []}
        zonesLoading={zonesLoading}
        frameLoading={frameLoading}
      />

      <ZoneLegend zones={zones ?? []} />
    </div>
  );
}

function ZoneOverlay({
  frame,
  zones,
  zonesLoading,
  frameLoading,
}: {
  frame: FrameData | null;
  zones: ZoneRow[];
  zonesLoading: boolean;
  frameLoading: boolean;
}) {
  if (!frame && frameLoading) {
    return <div className="opacity-60 text-sm">Loading frame…</div>;
  }
  if (!frame || !frame.width || !frame.height) {
    return (
      <div className="rounded border border-slate-700 bg-slate-900/40 p-6 text-sm opacity-70">
        No frame available yet. The ingestion worker must be running and
        publishing to <code>stream:frames:{"{camera_id}"}</code> for this camera.
      </div>
    );
  }

  const { objectUrl, width: W, height: H } = frame;

  return (
    <div className="rounded-xl border border-slate-700 overflow-hidden bg-black">
      {/* viewBox uses ACTUAL frame dims, so polygons in pixel coords land
          without any client-side scaling. preserveAspectRatio keeps the
          camera's native ratio (1280x720 today, 1920x1080 for GD50). */}
      <svg
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="xMidYMid meet"
        className="w-full h-auto block"
        role="img"
        aria-label={`Camera frame ${W}×${H} with ${zones.length} zone overlays`}
      >
        <image href={objectUrl} x={0} y={0} width={W} height={H} />
        {zones.map((z) => (
          <ZonePolygon key={z.zone_id} zone={z} />
        ))}
      </svg>
      <div className="px-3 py-2 text-xs opacity-60 flex justify-between">
        <span>Native {W}×{H}</span>
        <span>
          {zonesLoading ? "loading zones…" : `${zones.length} zone${zones.length === 1 ? "" : "s"}`}
        </span>
      </div>
    </div>
  );
}

function ZonePolygon({ zone }: { zone: ZoneRow }) {
  const style = KIND_STYLE[zone.kind] ?? DEFAULT_STYLE;
  const points = (zone.polygon || []).map(([x, y]) => `${x},${y}`).join(" ");
  if (!points) return null;

  // Label at the polygon centroid so it doesn't jump when polygons overlap.
  const cx = zone.polygon.reduce((s, [x]) => s + x, 0) / zone.polygon.length;
  const cy = zone.polygon.reduce((s, [, y]) => s + y, 0) / zone.polygon.length;

  return (
    <g>
      <polygon
        points={points}
        fill={style.fill}
        stroke={style.stroke}
        strokeWidth={3}
        vectorEffect="non-scaling-stroke"
      />
      <text
        x={cx}
        y={cy}
        textAnchor="middle"
        dominantBaseline="middle"
        fill="white"
        stroke="black"
        strokeWidth={0.5}
        paintOrder="stroke"
        style={{ fontSize: 18, fontWeight: 600 }}
      >
        {zone.workstation_name}
      </text>
    </g>
  );
}

function ZoneLegend({ zones }: { zones: ZoneRow[] }) {
  if (zones.length === 0) return null;
  const kinds = Array.from(new Set(zones.map((z) => z.kind)));
  return (
    <div className="flex flex-wrap gap-3 text-xs opacity-80">
      {kinds.map((k) => {
        const style = KIND_STYLE[k] ?? DEFAULT_STYLE;
        return (
          <span key={k} className="flex items-center gap-2">
            <span
              className="inline-block w-3 h-3 rounded-sm border"
              style={{ borderColor: style.stroke, backgroundColor: style.fill }}
            />
            {k}
          </span>
        );
      })}
    </div>
  );
}
