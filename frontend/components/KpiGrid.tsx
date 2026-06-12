"use client";
import useSWR from "swr";
import { api } from "@/lib/api";

type Kpi = {
  line_id: string;
  pieces_completed: number;
  pieces_per_hour: number;
  state_counts: Record<string, number>;
};

const fetcher = (p: string) => api<Kpi>(p);

export default function KpiGrid() {
  const { data, error } = useSWR("/api/v1/analytics/kpis/line/demo?hours=8", fetcher, {
    refreshInterval: 10_000,
  });

  if (error) return <div className="text-red-400">KPI fetch failed</div>;
  if (!data) return <div className="opacity-50">Loading KPIs…</div>;

  const cards = [
    { label: "Pieces (8h)", value: data.pieces_completed },
    { label: "Pieces / hour", value: data.pieces_per_hour.toFixed(1) },
    { label: "Working", value: data.state_counts?.WORKING ?? 0 },
    { label: "Idle", value: data.state_counts?.IDLE ?? 0 },
  ];

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
      {cards.map((c) => (
        <div key={c.label} className="rounded-xl border border-slate-700 p-4">
          <div className="text-xs uppercase opacity-60">{c.label}</div>
          <div className="text-3xl font-bold">{c.value}</div>
        </div>
      ))}
    </div>
  );
}
