"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import useSWR, { useSWRConfig } from "swr";
import Link from "next/link";
import { ApiError, api, apiBlob } from "@/lib/api";
import { EditableSeat } from "./EditableSeat";
import { DiscoveryOverlay } from "./DiscoveryOverlay";
import type { Polygon } from "./coords";

type ZoneRow = {
  zone_id: string;
  workstation_id: string;
  workstation_name: string;
  kind: string;
  polygon: Polygon;
  layout_version: number;
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

type SaveState =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "saved"; newLayoutVersion: number }
  | { kind: "error"; message: string };

type DiscoveryStatus =
  | { kind: "loading" }
  | { kind: "ok"; doc: { sampling: { actual_seconds: number } } }
  | { kind: "missing" }
  | { kind: "error"; message: string };

const KIND_STYLE: Record<string, { stroke: string; fill: string }> = {
  seat: { stroke: "#38bdf8", fill: "rgba(56, 189, 248, 0.20)" },
  machine: { stroke: "#f59e0b", fill: "rgba(245, 158, 11, 0.20)" },
  input_tray: { stroke: "#a3e635", fill: "rgba(163, 230, 53, 0.20)" },
  output_tray: { stroke: "#f472b6", fill: "rgba(244, 114, 182, 0.20)" },
};
const DEFAULT_STYLE = { stroke: "#cbd5e1", fill: "rgba(203, 213, 225, 0.20)" };

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
  const { mutate } = useSWRConfig();
  const zonesKey = `/api/v1/cameras/${cameraId}/zones`;

  const [frame, setFrame] = useState<FrameData | null>(null);
  const [frameError, setFrameError] = useState<FrameError | null>(null);
  const [frameLoading, setFrameLoading] = useState(false);
  const [refreshTick, setRefreshTick] = useState(0);
  const currentObjectUrl = useRef<string | null>(null);

  // ---- Edit-mode state (all UI-only until Save fires an actual POST) --------
  const [editMode, setEditMode] = useState(false);
  const [showDiscovery, setShowDiscovery] = useState(false);
  const [discoveryStatus, setDiscoveryStatus] = useState<DiscoveryStatus | null>(null);
  const [selectedWs, setSelectedWs] = useState<string | null>(null);
  // Working polygons keyed by workstation_id. Presence means "unsaved edit"
  // (we clear on save/cancel so the SWR-fetched polygon takes over).
  const [drafts, setDrafts] = useState<Record<string, Polygon>>({});
  const [saveStates, setSaveStates] = useState<Record<string, SaveState>>({});

  const { data: zones, error: zonesError, isLoading: zonesLoading } =
    useSWR<ZoneRow[]>(zonesKey, (p: string) => api<ZoneRow[]>(p));

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

  useEffect(() => { loadFrame(); }, [loadFrame, refreshTick]);
  useEffect(() => () => {
    if (currentObjectUrl.current) URL.revokeObjectURL(currentObjectUrl.current);
  }, []);

  // Leaving edit mode drops any unsaved drafts so the next re-entry starts
  // clean. Saved edits already come back via SWR, so nothing is lost.
  function exitEditMode() {
    setEditMode(false);
    setShowDiscovery(false);
    setSelectedWs(null);
    setDrafts({});
    setSaveStates({});
  }

  function setDraft(wsId: string, poly: Polygon) {
    setDrafts((d) => ({ ...d, [wsId]: poly }));
    setSaveStates((s) => ({ ...s, [wsId]: { kind: "idle" } }));
  }

  function revertDraft(wsId: string) {
    setDrafts((d) => { const n = { ...d }; delete n[wsId]; return n; });
    setSaveStates((s) => ({ ...s, [wsId]: { kind: "idle" } }));
  }

  async function saveDraft(zone: ZoneRow) {
    const polygon = drafts[zone.workstation_id];
    if (!polygon) return;
    setSaveStates((s) => ({ ...s, [zone.workstation_id]: { kind: "saving" } }));
    try {
      // Kind is pinned to "seat" for stage 2 (per spec). POST bumps
      // layout_version + inserts new row; latest_zone_ids() ensures the
      // old row stops appearing in reads.
      const created = await api<{ id: string; workstation_id: string; kind: string; polygon: Polygon; layout_version: number }>(
        "/api/v1/zones",
        { method: "POST", body: JSON.stringify({ workstation_id: zone.workstation_id, kind: "seat", polygon }) },
      );
      await mutate(zonesKey);   // revalidate so the UI shows the new row.
      setSaveStates((s) => ({ ...s, [zone.workstation_id]: { kind: "saved", newLayoutVersion: created.layout_version } }));
      setDrafts((d) => { const n = { ...d }; delete n[zone.workstation_id]; return n; });
    } catch (e: unknown) {
      const msg = e instanceof ApiError ? `${e.status} — ${e.detail}` :
                  e instanceof Error ? e.message : String(e);
      setSaveStates((s) => ({ ...s, [zone.workstation_id]: { kind: "error", message: msg } }));
    }
  }

  const selectedZone = useMemo(
    () => (zones ?? []).find((z) => z.workstation_id === selectedWs) ?? null,
    [zones, selectedWs],
  );

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="space-y-1">
          <div className="text-xs uppercase tracking-wide opacity-60">
            <Link href="/cameras" className="underline">Cameras</Link>{" "}
            <span aria-hidden>›</span> Zones
          </div>
          <h2 className="text-2xl font-semibold">Camera zones</h2>
          <div className="font-mono text-xs opacity-60">{cameraId}</div>
        </div>
        <div className="flex items-center gap-2">
          <Link
            href={`/cameras/${cameraId}/live`}
            className="rounded bg-emerald-700 hover:bg-emerald-600 px-3 py-2 text-sm font-semibold"
          >
            ▶ Live view
          </Link>
          <button
            onClick={() => setRefreshTick((t) => t + 1)}
            disabled={frameLoading}
            className="rounded bg-slate-700 hover:bg-slate-600 px-3 py-2 text-sm font-semibold disabled:opacity-50"
          >
            {frameLoading ? "Refreshing…" : "↻ Refresh frame"}
          </button>
          {editMode ? (
            <button
              onClick={exitEditMode}
              className="rounded bg-slate-800 border border-slate-600 hover:bg-slate-700 px-3 py-2 text-sm font-semibold"
            >
              Exit edit mode
            </button>
          ) : (
            <button
              onClick={() => setEditMode(true)}
              className="rounded bg-blue-600 hover:bg-blue-500 px-3 py-2 text-sm font-semibold"
            >
              ✎ Edit zones
            </button>
          )}
        </div>
      </div>

      {editMode && (
        <div className="rounded border border-slate-700 bg-slate-900/60 p-3 text-sm flex items-center gap-4 flex-wrap">
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={showDiscovery}
              onChange={(e) => setShowDiscovery(e.target.checked)}
              className="w-4 h-4"
            />
            <span>Show measured dwell (reference only)</span>
          </label>
          {showDiscovery && discoveryStatus?.kind === "missing" && (
            <span className="text-xs opacity-70">
              No discovery run found for this camera — run <code>discover_zones</code> first.
            </span>
          )}
          {showDiscovery && discoveryStatus?.kind === "error" && (
            <span className="text-xs text-red-300">Discovery failed: {discoveryStatus.message}</span>
          )}
          {showDiscovery && discoveryStatus?.kind === "loading" && (
            <span className="text-xs opacity-70">Loading discovery…</span>
          )}
          <span className="text-xs opacity-60 ml-auto">
            {selectedZone
              ? `Selected: ${selectedZone.workstation_name}`
              : "Click a seat to select · drag body to move · drag corners to resize"}
          </span>
        </div>
      )}

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
        cameraId={cameraId}
        frame={frame}
        frameError={frameError}
        zones={zones ?? []}
        zonesLoading={zonesLoading}
        frameLoading={frameLoading}
        editMode={editMode}
        showDiscovery={showDiscovery}
        onDiscoveryStatus={setDiscoveryStatus}
        drafts={drafts}
        selectedWs={selectedWs}
        onSelect={setSelectedWs}
        onChangeDraft={setDraft}
      />

      {editMode && selectedZone && (
        <SelectedSeatPanel
          zone={selectedZone}
          hasDraft={selectedZone.workstation_id in drafts}
          saveState={saveStates[selectedZone.workstation_id] ?? { kind: "idle" }}
          onSave={() => saveDraft(selectedZone)}
          onRevert={() => revertDraft(selectedZone.workstation_id)}
        />
      )}

      <ZoneLegend zones={zones ?? []} />
    </div>
  );
}

// -------------------------------------------------------------------------- //
// Read-only + editable overlay
// -------------------------------------------------------------------------- //
function ZoneOverlay(props: {
  cameraId: string;
  frame: FrameData | null;
  frameError: FrameError | null;
  zones: ZoneRow[];
  zonesLoading: boolean;
  frameLoading: boolean;
  editMode: boolean;
  showDiscovery: boolean;
  onDiscoveryStatus: (s: DiscoveryStatus) => void;
  drafts: Record<string, Polygon>;
  selectedWs: string | null;
  onSelect: (ws: string | null) => void;
  onChangeDraft: (ws: string, poly: Polygon) => void;
}) {
  const {
    cameraId, frame, frameError, zones, zonesLoading, frameLoading,
    editMode, showDiscovery, onDiscoveryStatus, drafts, selectedWs, onSelect, onChangeDraft,
  } = props;

  const svgRef = useRef<SVGSVGElement>(null);

  if (!frame && frameLoading) return <div className="opacity-60 text-sm">Loading frame…</div>;
  if (!frame) {
    if (frameError) return null;
    return <div className="opacity-60 text-sm">No frame loaded.</div>;
  }
  const { objectUrl, width: W, height: H } = frame;

  return (
    <div className="rounded-xl border border-slate-700 overflow-hidden bg-black">
      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="xMidYMid meet"
        className="w-full h-auto block"
        role="img"
        aria-label={`Camera frame ${W}×${H} with ${zones.length} zone overlays`}
        // Click on empty canvas deselects the active seat when in edit mode.
        onClick={editMode ? () => onSelect(null) : undefined}
      >
        <image href={objectUrl} x={0} y={0} width={W} height={H} />

        {/* Non-editable zones (machine/tray) are ALWAYS read-only. Only
            seats become interactive in edit mode, per stage-2 spec. */}
        {zones.filter((z) => !(editMode && z.kind === "seat")).map((z) => (
          <ReadOnlyZonePolygon key={z.zone_id} zone={z} />
        ))}

        {editMode && zones.filter((z) => z.kind === "seat").map((z) => (
          <EditableSeat
            key={z.zone_id}
            svgRef={svgRef}
            workstationId={z.workstation_id}
            workstationName={z.workstation_name}
            polygon={drafts[z.workstation_id] ?? z.polygon}
            selected={z.workstation_id === selectedWs}
            frameW={W}
            frameH={H}
            onSelect={onSelect}
            onChange={onChangeDraft}
          />
        ))}

        {editMode && showDiscovery && (
          <DiscoveryOverlay cameraId={cameraId} frameW={W} frameH={H} onStatus={onDiscoveryStatus} />
        )}
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

function ReadOnlyZonePolygon({ zone }: { zone: ZoneRow }) {
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
        x={cx} y={cy}
        textAnchor="middle" dominantBaseline="middle"
        fill="white" stroke="black" strokeWidth={0.5} paintOrder="stroke"
        style={{ fontSize: 18, fontWeight: 600 }}
      >
        {zone.workstation_name}
      </text>
    </g>
  );
}

// -------------------------------------------------------------------------- //
// Selected-seat action panel
// -------------------------------------------------------------------------- //
function SelectedSeatPanel({
  zone, hasDraft, saveState, onSave, onRevert,
}: {
  zone: ZoneRow;
  hasDraft: boolean;
  saveState: SaveState;
  onSave: () => void;
  onRevert: () => void;
}) {
  const busy = saveState.kind === "saving";
  return (
    <div className="rounded border border-slate-700 bg-slate-900/60 p-3 text-sm flex items-center gap-3 flex-wrap">
      <div>
        <div className="font-semibold">{zone.workstation_name}</div>
        <div className="text-xs opacity-60">
          {zone.kind} · v{zone.layout_version} · {zone.workstation_id}
        </div>
      </div>
      <div className="ml-auto flex items-center gap-2">
        <button
          onClick={onRevert}
          disabled={!hasDraft || busy}
          className="rounded bg-slate-800 border border-slate-600 hover:bg-slate-700 disabled:opacity-40 px-3 py-2 text-sm"
        >
          Revert
        </button>
        <button
          onClick={onSave}
          disabled={!hasDraft || busy}
          className="rounded bg-blue-600 hover:bg-blue-500 disabled:opacity-40 px-3 py-2 text-sm font-semibold"
        >
          {busy ? "Saving…" : "Save polygon"}
        </button>
      </div>
      {saveState.kind === "saved" && (
        <div className="w-full text-xs text-emerald-300">
          Saved as layout version v{saveState.newLayoutVersion}.
        </div>
      )}
      {saveState.kind === "error" && (
        <div className="w-full text-xs text-red-300">Save failed: {saveState.message}</div>
      )}
    </div>
  );
}

// -------------------------------------------------------------------------- //
// Existing helpers (unchanged behavior)
// -------------------------------------------------------------------------- //
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
