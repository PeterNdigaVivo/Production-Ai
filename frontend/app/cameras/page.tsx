"use client";
import { useState } from "react";
import useSWR from "swr";
import { api } from "@/lib/api";

type Cam = {
  id: string; name: string; rtsp_url: string; is_active: boolean;
  last_seen_at: string | null; fps_target: number; line_id: string;
};
type Line = { id: string; name: string };

export default function CamerasPage() {
  const { data, error, mutate } = useSWR("/api/v1/cameras", (p) => api<Cam[]>(p), {
    refreshInterval: 5_000,
  });
  const { data: lines } = useSWR("/api/v1/lines", (p) => api<Line[]>(p));
  const [showForm, setShowForm] = useState(false);

  if (error) return <div className="text-red-400">Failed to load cameras</div>;
  if (!data) return <div className="opacity-50">Loading…</div>;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-semibold">Camera Health</h2>
        <button onClick={() => setShowForm((s) => !s)}
                className="rounded bg-blue-600 hover:bg-blue-500 px-3 py-2 text-sm font-semibold">
          {showForm ? "Cancel" : "+ Add camera"}
        </button>
      </div>

      {showForm && (
        <AddCameraForm
          lines={lines ?? []}
          onCreated={() => { setShowForm(false); mutate(); }}
        />
      )}

      {data.length === 0 ? (
        <div className="opacity-60 text-sm">No cameras yet. Click "Add camera" to onboard your first NVR feed.</div>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left opacity-60">
              <th className="py-2">Name</th><th>RTSP</th><th>FPS</th><th>Active</th><th>Last seen</th>
            </tr>
          </thead>
          <tbody>
            {data.map((c) => (
              <tr key={c.id} className="border-t border-slate-800">
                <td className="py-2">{c.name}</td>
                <td className="font-mono text-xs truncate max-w-md">{maskRtsp(c.rtsp_url)}</td>
                <td>{c.fps_target}</td>
                <td>{c.is_active ? "✓" : "✗"}</td>
                <td>{c.last_seen_at ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function maskRtsp(url: string): string {
  return url.replace(/(rtsp:\/\/)([^:]+):([^@]+)@/, "$1$2:****@");
}

function AddCameraForm({ lines, onCreated }: { lines: Line[]; onCreated: () => void }) {
  const [name, setName] = useState("");
  const [rtsp, setRtsp] = useState("");
  const [lineId, setLineId] = useState(lines[0]?.id ?? "");
  const [fps, setFps] = useState(8);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (lines.length === 0) {
    return (
      <div className="rounded border border-amber-700 bg-amber-950/40 p-4 text-sm">
        Create a production line first.{" "}
        <a href="/lines" className="underline">Go to Lines →</a>
      </div>
    );
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      await api("/api/v1/cameras", {
        method: "POST",
        body: JSON.stringify({ line_id: lineId, name, rtsp_url: rtsp, fps_target: fps }),
      });
      onCreated();
    } catch (e: any) {
      setErr(e?.message ?? "Failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="rounded-xl border border-slate-700 p-4 space-y-3">
      <h3 className="font-semibold">Add camera</h3>
      <div className="grid md:grid-cols-2 gap-3">
        <label className="text-sm">
          <span className="opacity-70">Line</span>
          <select value={lineId} onChange={(e) => setLineId(e.target.value)}
                  className="mt-1 w-full rounded bg-slate-800 border border-slate-700 px-3 py-2">
            {lines.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
          </select>
        </label>
        <label className="text-sm">
          <span className="opacity-70">Name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} required
                 placeholder="Cam-101"
                 className="mt-1 w-full rounded bg-slate-800 border border-slate-700 px-3 py-2" />
        </label>
        <label className="text-sm md:col-span-2">
          <span className="opacity-70">RTSP URL</span>
          <input value={rtsp} onChange={(e) => setRtsp(e.target.value)} required
                 placeholder="rtsp://user:pass@nvr-ip:554/Streaming/Channels/101"
                 className="mt-1 w-full rounded bg-slate-800 border border-slate-700 px-3 py-2 font-mono text-xs" />
        </label>
        <label className="text-sm">
          <span className="opacity-70">Target FPS</span>
          <input value={fps} onChange={(e) => setFps(Number(e.target.value))} type="number" min={1} max={30}
                 className="mt-1 w-full rounded bg-slate-800 border border-slate-700 px-3 py-2" />
        </label>
      </div>
      {err && <div className="text-sm text-red-400">{err}</div>}
      <button type="submit" disabled={busy}
              className="rounded bg-blue-600 hover:bg-blue-500 px-3 py-2 text-sm font-semibold disabled:opacity-50">
        {busy ? "Adding…" : "Add camera"}
      </button>
    </form>
  );
}
