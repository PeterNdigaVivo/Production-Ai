"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import useSWR from "swr";
import Link from "next/link";
import { ApiError, api, apiBlob } from "@/lib/api";

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

// Distinguishable failure kinds so the UI can never again hide a 200-with-body
// behind "no frame available".
type FrameError =
  | { kind: "empty_stream"; message: string }
  | { kind: "camera_unknown"; message: string }
  | { kind: "unauthorized"; message: string }
  | { kind: "http"; status: number; message: string }
  | { kind: "network"; message: string }
  | { kind: "decode"; message: string };

const KIND_STYLE: Record<string, { stroke: string; fill: string }> = {
  seat: { stroke: "#38bdf8", fill: "rgba(56, 189, 248, 0.20)" },
  machine: { stroke: "#f59e0b", fill: "rgba(245, 158, 11, 0.20)" },
  input_tray: { stroke: "#a3e635", fill: "rgba(163, 230, 53, 0.20)" },
  output_tray: { stroke: "#f472b6", fill: "rgba(244, 114, 182, 0.20)" },
};
const DEFAULT_STYLE = { stroke: "#cbd5e1", fill: "rgba(203, 213, 225, 0.20)" };

/** Decode a JPEG blob into its native pixel dimensions via a hidden <img>.
 * This is CORS-immune: no response-header contract needed, works at any
 * camera resolution the browser can decode. */
function measureBlob(blob: Blob): Promise<{ objectUrl: string; width: number; height: number }> {
  return new Promise((resolve, reject) => {
    const objectUrl = URL.createObjectURL(blob);
    const img = new Image();
    img.onload = () => {
      const w = img.naturalWidth;
      const h = img.naturalHeight;
      if (!w || !h) {
        URL.revokeObjectURL(objectUrl);
        reject(new Error("image decoded with zero dimensions"));
        return;
      }
      resolve({ objectUrl, width: w, height: h });
    };
    img.onerror = () => {
      URL.revokeObjectURL(objectUrl);
      reject(new Error("browser could not decode the frame as an image"));
    };
    img.src = objectUrl;
  });
}

export default function CameraZoneViewerPage({ params }: { params: { id: string } }) {
  const cameraId = params.id;
  const [frame, setFrame] = useState<FrameData | null>(null);
  const [frameError, setFrameError] = useState<FrameError | null>(null);
  const [frameLoading, setFrameLoading] = useState(false);
  const [refreshTick, setRefreshTick] = useState(0);
  const currentObjectUrl = useRef<string | null>(null);

  const {
    data: zones,
    error: zonesError,
    isLoading: zonesLoading,
  } = useSWR(`/api/v1/cameras/${cameraId}/zones`, (p) => api<ZoneRow[]>(p));

  const loadFrame = useCallback(async () => {
    setFrameLoading(true);
    setFrameError(null);
    try {
      const { blob } = await apiBlob(`/api/v1/cameras/${cameraId}/frame`);
      const measured = await measureBlob(blob);
      setFrame((prev) => {
        if (prev) URL.revokeObjectURL(prev.objectUrl);
        currentObjectUrl.current = measured.objectUrl;
        return measured;
      });
    } catch (e: unknown) {
      // Classify so the UI shows the right message — never silently degrade
      // a real HTTP body into "no frame available".
      if (e instanceof ApiError) {
        if (e.status === 401) {
          setFrameError({ kind: "unauthorized", message: e.detail || "session expired" });
        } else if (e.status === 404 && /no frames/i.test(e.detail)) {
          setFrameError({ kind: "empty_stream", message: e.detail });
        } else if (e.status === 404) {
          setFrameError({ kind: "camera_unknown", message: e.detail || "camera not found" });
        } else {
          setFrameError({ kind: "http", status: e.status, message: e.detail || e.message });
        }
      } else if (e instanceof Error && /decode|dimensions/i.test(e.message)) {
        setFrameError({ kind: "decode", message: e.message });
      } else {
        setFrameError({ kind: "network", message: e instanceof Error ? e.message : String(e) });
      }
    } finally {
      setFrameLoading(false);
    }
  }, [cameraId]);

  useEffect(() => {
    loadFrame();
  }, [loadFrame, refreshTick]);

  useEffect(() => {
    return () => {
      if (currentObjectUrl.current) URL.revokeObjectURL(currentObjectUrl.current);
    };
  }, []);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <div className="space-y-1">
          <div className="text-xs uppercase tracking-wide opacity-60">
            <Link href="/cameras" className="underline">Cameras</Link>{" "}
            <span aria-hidden>›</span> Zones
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

      <FrameErrorPanel err={frameError} />
      {zonesError && (
        <div className="rounded border border-red-700 bg-red-950/40 p-3 text-sm text-red-200">
          Failed to load zones:{" "}
          {zonesError instanceof ApiError
            ? `${zonesError.status} — ${zonesError.detail}`
            : String((zonesError as Error)?.message ?? zonesError)}
        </div>
      )}

      <ZoneOverlay
        frame={frame}
        frameError={frameError}
        zones={zones ?? []}
        zonesLoading={zonesLoading}
        frameLoading={frameLoading}
      />

      <ZoneLegend zones={zones ?? []} />
    </div>
  );
}

function FrameErrorPanel({ err }: { err: FrameError | null }) {
  if (!err) return null;
  const isEmpty = err.kind === "empty_stream";
  const style = isEmpty
    ? "border-amber-700 bg-amber-950/40 text-amber-100"
    : "border-red-700 bg-red-950/40 text-red-200";
  const heading =
    err.kind === "empty_stream" ? "No frame in the stream yet"
    : err.kind === "camera_unknown" ? "Camera not found"
    : err.kind === "unauthorized" ? "Not authorised"
    : err.kind === "decode" ? "Frame received but could not be rendered"
    : err.kind === "http" ? `HTTP ${err.status}`
    : "Network error";
  return (
    <div className={`rounded border p-3 text-sm ${style}`}>
      <div className="font-semibold">{heading}</div>
      <div className="opacity-90">{err.message}</div>
    </div>
  );
}

function ZoneOverlay({
  frame,
  frameError,
  zones,
  zonesLoading,
  frameLoading,
}: {
  frame: FrameData | null;
  frameError: FrameError | null;
  zones: ZoneRow[];
  zonesLoading: boolean;
  frameLoading: boolean;
}) {
  if (!frame && frameLoading) {
    return <div className="opacity-60 text-sm">Loading frame…</div>;
  }
  if (!frame) {
    // Error panel above already shows the reason; keep this quiet.
    if (frameError) return null;
    return <div className="opacity-60 text-sm">No frame loaded.</div>;
  }

  const { objectUrl, width: W, height: H } = frame;

  return (
    <div className="rounded-xl border border-slate-700 overflow-hidden bg-black">
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
