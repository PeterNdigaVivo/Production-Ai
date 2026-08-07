"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import useSWR from "swr";
import Link from "next/link";
import { ApiError, api, apiBlob } from "@/lib/api";

// Reuse the read-side ZoneRow shape from the zones page — we only need
// workstation_id → workstation_name for the box labels, so redefining it
// locally (a tiny shape) is cleaner than reaching across pages.
type ZoneRow = {
  zone_id: string;
  workstation_id: string;
  workstation_name: string;
  kind: string;
  polygon: [number, number][];
  layout_version: number;
};

type TrackRecord = {
  track_id: number;
  xyxy: [number, number, number, number];
  conf: number;
  workstation_id: string | null;
  hits: number;
};

type TracksPayload = {
  camera_id: string;
  ts: number | null;
  age_seconds: number | null;
  tracks: TrackRecord[];
  machine_running: Record<string, boolean>;
  stale: boolean;
};

type FrameData = {
  objectUrl: string;
  width: number;
  height: number;
};

type FrameError =
  | { kind: "empty_stream"; message: string }
  | { kind: "camera_unknown"; message: string }
  | { kind: "unauthorized"; message: string }
  | { kind: "http"; status: number; message: string }
  | { kind: "network"; message: string }
  | { kind: "decode"; message: string };

const FRAME_REFRESH_MS = 1_000;
const TRACKS_REFRESH_MS = 1_000;
const STALE_TRACK_AGE_S = 5;

const ATTRIBUTED_STROKE = "#4ade80";
const ATTRIBUTED_FILL = "rgba(74, 222, 128, 0.15)";
const UNATTRIBUTED_STROKE = "#fbbf24";
const UNATTRIBUTED_FILL = "rgba(251, 191, 36, 0.10)";

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

export default function CameraLiveViewPage({ params }: { params: { id: string } }) {
  const cameraId = params.id;

  const [frame, setFrame] = useState<FrameData | null>(null);
  const [frameError, setFrameError] = useState<FrameError | null>(null);
  const [frameLoading, setFrameLoading] = useState(false);
  const currentObjectUrl = useRef<string | null>(null);

  // Workstation name lookup — fetched once and cached. Tracks payload only
  // carries workstation_id, so we resolve names on the client.
  const { data: zones } = useSWR<ZoneRow[]>(
    `/api/v1/cameras/${cameraId}/zones`,
    (p: string) => api<ZoneRow[]>(p),
    { revalidateOnFocus: false },
  );
  const nameById = new Map<string, string>();
  (zones ?? []).forEach((z) => nameById.set(z.workstation_id, z.workstation_name));

  const { data: tracks, error: tracksError } = useSWR<TracksPayload>(
    `/api/v1/cameras/${cameraId}/tracks`,
    (p: string) => api<TracksPayload>(p),
    { refreshInterval: TRACKS_REFRESH_MS, revalidateOnFocus: false },
  );

  // Frame poll — we fetch a fresh JPEG on the same cadence as tracks so
  // the overlay lines up within one poll interval. Boxes may lag the image
  // by a frame; per spec, that's acceptable for a confidence check.
  const loadFrame = useCallback(async () => {
    setFrameLoading(true);
    try {
      const { blob } = await apiBlob(`/api/v1/cameras/${cameraId}/frame`);
      const measured = await measureBlob(blob);
      setFrameError(null);
      setFrame((prev) => {
        if (prev) URL.revokeObjectURL(prev.objectUrl);
        currentObjectUrl.current = measured.objectUrl;
        return measured;
      });
    } catch (e: unknown) {
      if (e instanceof ApiError) {
        if (e.status === 401) setFrameError({ kind: "unauthorized", message: e.detail || "session expired" });
        else if (e.status === 404 && /no frames/i.test(e.detail)) setFrameError({ kind: "empty_stream", message: e.detail });
        else if (e.status === 404) setFrameError({ kind: "camera_unknown", message: e.detail || "camera not found" });
        else setFrameError({ kind: "http", status: e.status, message: e.detail || e.message });
      } else if (e instanceof Error && /decode|dimensions/i.test(e.message)) {
        setFrameError({ kind: "decode", message: e.message });
      } else {
        setFrameError({ kind: "network", message: e instanceof Error ? e.message : String(e) });
      }
    } finally {
      setFrameLoading(false);
    }
  }, [cameraId]);

  useEffect(() => { loadFrame(); }, [loadFrame]);
  useEffect(() => {
    const t = setInterval(loadFrame, FRAME_REFRESH_MS);
    return () => clearInterval(t);
  }, [loadFrame]);
  useEffect(() => () => {
    if (currentObjectUrl.current) URL.revokeObjectURL(currentObjectUrl.current);
  }, []);

  const isTracksStale = !tracks
    || tracks.stale
    || (tracks.age_seconds !== null && tracks.age_seconds > STALE_TRACK_AGE_S);
  const staleReason = getStaleReason(tracks, tracksError);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="space-y-1">
          <div className="text-xs uppercase tracking-wide opacity-60">
            <Link href="/cameras" className="underline">Cameras</Link>{" "}
            <span aria-hidden>›</span> Live view
          </div>
          <h2 className="text-2xl font-semibold">What the AI sees</h2>
          <div className="font-mono text-xs opacity-60">{cameraId}</div>
        </div>
        <div className="flex items-center gap-2">
          <Link
            href={`/cameras/${cameraId}/zones`}
            className="rounded bg-slate-800 border border-slate-600 hover:bg-slate-700 px-3 py-2 text-sm font-semibold"
          >
            ← Back to zones (edit)
          </Link>
        </div>
      </div>

      <FrameErrorPanel err={frameError} />

      {isTracksStale && (
        <StaleBanner reason={staleReason} />
      )}

      <LiveOverlay
        frame={frame}
        frameError={frameError}
        frameLoading={frameLoading}
        tracks={tracks?.tracks ?? []}
        stale={isTracksStale}
        nameById={nameById}
      />

      <Legend />

      <div className="text-xs opacity-60 flex items-center gap-4 flex-wrap">
        <span>
          Poll: frame {FRAME_REFRESH_MS}ms · tracks {TRACKS_REFRESH_MS}ms
        </span>
        {tracks && tracks.age_seconds !== null && (
          <span>Tracks age: {tracks.age_seconds.toFixed(1)}s</span>
        )}
        {tracks && (
          <span>
            {tracks.tracks.length} track{tracks.tracks.length === 1 ? "" : "s"} in frame ·{" "}
            {tracks.tracks.filter((t) => t.workstation_id).length} attributed
          </span>
        )}
      </div>
    </div>
  );
}

// -------------------------------------------------------------------------- //
// Overlay
// -------------------------------------------------------------------------- //
function LiveOverlay({
  frame, frameError, frameLoading, tracks, stale, nameById,
}: {
  frame: FrameData | null;
  frameError: FrameError | null;
  frameLoading: boolean;
  tracks: TrackRecord[];
  stale: boolean;
  nameById: Map<string, string>;
}) {
  if (!frame && frameLoading) return <div className="opacity-60 text-sm">Loading frame…</div>;
  if (!frame) {
    if (frameError) return null;
    return <div className="opacity-60 text-sm">No frame loaded.</div>;
  }
  const { objectUrl, width: W, height: H } = frame;

  // Render boxes translucent while stale so the operator can see the last
  // known state but knows it isn't live.
  const boxOpacity = stale ? 0.35 : 1;

  return (
    <div className="rounded-xl border border-slate-700 overflow-hidden bg-black">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="xMidYMid meet"
        className="w-full h-auto block"
        role="img"
        aria-label={`Live camera frame ${W}×${H} with ${tracks.length} track overlays`}
      >
        <image href={objectUrl} x={0} y={0} width={W} height={H} />
        <g style={{ opacity: boxOpacity }}>
          {tracks.map((t) => (
            <TrackBox key={t.track_id} track={t} nameById={nameById} />
          ))}
        </g>
      </svg>
      <div className="px-3 py-2 text-xs opacity-60 flex justify-between">
        <span>Native {W}×{H}</span>
        <span>
          {stale ? "detections stale" : "live"}
        </span>
      </div>
    </div>
  );
}

function TrackBox({ track, nameById }: { track: TrackRecord; nameById: Map<string, string> }) {
  const [x1, y1, x2, y2] = track.xyxy;
  const attributed = track.workstation_id != null;
  const stroke = attributed ? ATTRIBUTED_STROKE : UNATTRIBUTED_STROKE;
  const fill = attributed ? ATTRIBUTED_FILL : UNATTRIBUTED_FILL;
  const label = attributed
    ? (nameById.get(track.workstation_id!) ?? "unknown workstation")
    : "unassigned";

  return (
    <g>
      <rect
        x={x1}
        y={y1}
        width={Math.max(0, x2 - x1)}
        height={Math.max(0, y2 - y1)}
        fill={fill}
        stroke={stroke}
        strokeWidth={3}
        vectorEffect="non-scaling-stroke"
      />
      <text
        x={x1 + 4}
        y={y1 + 20}
        fill="white"
        stroke="black"
        strokeWidth={0.5}
        paintOrder="stroke"
        style={{ fontSize: 16, fontWeight: 600 }}
      >
        {label} · #{track.track_id} · {(track.conf * 100).toFixed(0)}%
      </text>
    </g>
  );
}

// -------------------------------------------------------------------------- //
// Banners + legend
// -------------------------------------------------------------------------- //
function getStaleReason(tracks: TracksPayload | undefined, err: unknown): string {
  if (err instanceof ApiError) return `${err.status} — ${err.detail}`;
  if (err) return String((err as Error).message ?? err);
  if (!tracks) return "Waiting for first tracks response…";
  if (tracks.stale) return "No entries in stream:tracks yet — pipeline may be quiet or stopped.";
  if (tracks.age_seconds !== null && tracks.age_seconds > STALE_TRACK_AGE_S) {
    return `Last detection was ${tracks.age_seconds.toFixed(1)}s ago — tracking may be stopped.`;
  }
  return "No live detections.";
}

function StaleBanner({ reason }: { reason: string }) {
  return (
    <div className="rounded border border-amber-700 bg-amber-950/40 p-3 text-sm text-amber-100">
      <div className="font-semibold">No live detections</div>
      <div className="opacity-90">{reason}</div>
    </div>
  );
}

function Legend() {
  return (
    <div className="flex flex-wrap gap-4 text-xs opacity-80">
      <span className="flex items-center gap-2">
        <span
          className="inline-block w-3 h-3 rounded-sm border"
          style={{ borderColor: ATTRIBUTED_STROKE, backgroundColor: ATTRIBUTED_FILL }}
        />
        attributed to a workstation
      </span>
      <span className="flex items-center gap-2">
        <span
          className="inline-block w-3 h-3 rounded-sm border"
          style={{ borderColor: UNATTRIBUTED_STROKE, backgroundColor: UNATTRIBUTED_FILL }}
        />
        unassigned (no polygon match)
      </span>
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
