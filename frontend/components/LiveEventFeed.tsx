"use client";
import { useEffect, useState } from "react";
import { WS_URL } from "@/lib/api";

type Event = { id: string; data: Record<string, string> };

export default function LiveEventFeed() {
  const [events, setEvents] = useState<Event[]>([]);

  useEffect(() => {
    const ws = new WebSocket(WS_URL);
    ws.onmessage = (m) => {
      try {
        const payload = JSON.parse(m.data);
        if (payload.type !== "event") return;
        setEvents((prev) => [{ id: payload.id, data: payload.data }, ...prev].slice(0, 50));
      } catch {}
    };
    return () => ws.close();
  }, []);

  return (
    <div className="rounded-xl border border-slate-700 p-4">
      <h3 className="font-semibold mb-2">Live events</h3>
      <ul className="text-sm space-y-1 max-h-96 overflow-auto">
        {events.length === 0 && <li className="opacity-50">Waiting for events…</li>}
        {events.map((e) => (
          <li key={e.id} className="font-mono">
            <span className="opacity-50">{e.data.type ?? "event"}</span>{" "}
            cam={e.data.camera_id?.slice(0, 8)} state={e.data.state ?? ""} trk={e.data.track_id ?? ""}
          </li>
        ))}
      </ul>
    </div>
  );
}
