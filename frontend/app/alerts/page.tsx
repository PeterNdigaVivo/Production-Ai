"use client";
import useSWR from "swr";
import { api } from "@/lib/api";

type Alert = {
  id: string; ts: string; severity: string; kind: string;
  title: string; description: string | null; acknowledged: boolean;
};

export default function AlertsPage() {
  const { data, error, mutate } = useSWR("/api/v1/alerts", (p) => api<Alert[]>(p), {
    refreshInterval: 5_000,
  });

  async function ack(id: string) {
    await api(`/api/v1/alerts/${id}/ack`, { method: "POST" });
    mutate();
  }

  if (error) return <div className="text-red-400">Failed</div>;
  if (!data) return <div className="opacity-50">Loading…</div>;
  return (
    <div className="space-y-4">
      <h2 className="text-2xl font-semibold">Alerts</h2>
      <ul className="space-y-2">
        {data.map((a) => (
          <li key={a.id} className="rounded border border-slate-700 p-3 flex justify-between">
            <div>
              <div className="font-semibold">{a.title}</div>
              <div className="text-xs opacity-60">{a.kind} · {a.severity} · {a.ts}</div>
              {a.description && <div className="text-sm opacity-80">{a.description}</div>}
            </div>
            {!a.acknowledged && (
              <button onClick={() => ack(a.id)} className="rounded bg-slate-700 px-2 py-1 text-sm">Ack</button>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
