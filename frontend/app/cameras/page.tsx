"use client";
import useSWR from "swr";
import { api } from "@/lib/api";

type Cam = {
  id: string; name: string; rtsp_url: string; is_active: boolean;
  last_seen_at: string | null; fps_target: number;
};

export default function CamerasPage() {
  const { data, error } = useSWR("/api/v1/cameras", (p) => api<Cam[]>(p), {
    refreshInterval: 5_000,
  });
  if (error) return <div className="text-red-400">Failed to load cameras</div>;
  if (!data) return <div className="opacity-50">Loading…</div>;
  return (
    <div className="space-y-4">
      <h2 className="text-2xl font-semibold">Camera Health</h2>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left opacity-60">
            <th className="py-2">Name</th><th>FPS</th><th>Active</th><th>Last seen</th>
          </tr>
        </thead>
        <tbody>
          {data.map((c) => (
            <tr key={c.id} className="border-t border-slate-800">
              <td className="py-2">{c.name}</td>
              <td>{c.fps_target}</td>
              <td>{c.is_active ? "✓" : "✗"}</td>
              <td>{c.last_seen_at ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
