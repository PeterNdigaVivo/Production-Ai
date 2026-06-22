"use client";
import useSWR from "swr";
import { api } from "@/lib/api";

type Line = { id: string; name: string };
type Kpi = {
  line_id: string;
  pieces_completed: number;
  pieces_per_hour: number;
  state_counts: Record<string, number>;
};

export default function KpiGrid() {
  const { data: lines, error: linesError } = useSWR("/api/v1/lines", (p) => api<Line[]>(p));
  const firstLineId = lines && lines.length > 0 ? lines[0].id : undefined;

  const { data, error } = useSWR(
    firstLineId ? `/api/v1/analytics/kpis/line/${firstLineId}?hours=8` : null,
    (p) => api<Kpi>(p),
    { refreshInterval: 10_000 }
  );

  if (linesError) return <div className="text-red-400">Failed to load production lines</div>;
  if (!lines) return <div className="opacity-50">Loading…</div>;

  if (lines.length === 0) {
    return (
      <div className="rounded-xl border border-slate-700 p-6">
        <div className="text-lg font-semibold mb-1">No production lines yet</div>
        <p className="text-sm opacity-70 mb-3">
          Add your first production line to start tracking KPIs.
        </p>
        <a href="/lines" className="inline-block rounded bg-blue-600 hover:bg-blue-500 px-3 py-2 text-sm font-semibold">
          + Add production line
        </a>
      </div>
    );
  }

  if (error) return <div className="text-red-400">KPI fetch failed</div>;
  if (!data) return <div className="opacity-50">Loading KPIs…</div>;

  const cards = [
    { label: "Pieces (8h)", value: data.pieces_completed },
    { label: "Pieces / hour", value: data.pieces_per_hour.toFixed(1) },
    { label: "Working", value: data.state_counts?.WORKING ?? 0 },
    { label: "Idle", value: data.state_counts?.IDLE ?? 0 },
  ];

  return (
    <div className="space-y-2">
      <div className="text-sm opacity-60">Line: {lines[0].name}</div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {cards.map((c) => (
          <div key={c.label} className="rounded-xl border border-slate-700 p-4">
            <div className="text-xs uppercase opacity-60">{c.label}</div>
            <div className="text-3xl font-bold">{c.value}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
