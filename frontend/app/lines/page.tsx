"use client";
import { useState } from "react";
import useSWR from "swr";
import { api } from "@/lib/api";

type Factory = { id: string; name: string };
type Line = { id: string; factory_id: string; name: string; target_pieces_per_hour: number };

export default function LinesPage() {
  const { data: lines, error, mutate } = useSWR("/api/v1/lines", (p) => api<Line[]>(p), {
    refreshInterval: 10_000,
  });
  const { data: factories } = useSWR("/api/v1/factories", (p) => api<Factory[]>(p));
  const [showLineForm, setShowLineForm] = useState(false);
  const [showFactoryForm, setShowFactoryForm] = useState(false);

  if (error) return <div className="text-red-400">Failed to load lines</div>;
  if (!lines) return <div className="opacity-50">Loading…</div>;

  const hasFactory = (factories?.length ?? 0) > 0;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-semibold">Production Lines</h2>
        <div className="flex gap-2">
          <button onClick={() => setShowFactoryForm((s) => !s)}
                  className="rounded bg-slate-700 hover:bg-slate-600 px-3 py-2 text-sm">
            {showFactoryForm ? "Cancel" : "+ Factory"}
          </button>
          <button onClick={() => setShowLineForm((s) => !s)}
                  disabled={!hasFactory}
                  className="rounded bg-blue-600 hover:bg-blue-500 px-3 py-2 text-sm font-semibold disabled:opacity-40">
            {showLineForm ? "Cancel" : "+ Add line"}
          </button>
        </div>
      </div>

      {showFactoryForm && (
        <AddFactoryForm onCreated={() => { setShowFactoryForm(false); mutate(); }} />
      )}
      {showLineForm && hasFactory && (
        <AddLineForm factories={factories ?? []}
                     onCreated={() => { setShowLineForm(false); mutate(); }} />
      )}

      {!hasFactory && (
        <div className="rounded border border-amber-700 bg-amber-950/40 p-4 text-sm">
          Create a factory first — every line belongs to one.
        </div>
      )}

      {lines.length === 0 ? (
        <div className="opacity-60 text-sm">No production lines yet.</div>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left opacity-60">
              <th className="py-2">Name</th><th>Factory</th><th>Target pieces/hr</th>
            </tr>
          </thead>
          <tbody>
            {lines.map((l) => (
              <tr key={l.id} className="border-t border-slate-800">
                <td className="py-2">{l.name}</td>
                <td className="opacity-70 font-mono text-xs">{l.factory_id.slice(0, 8)}</td>
                <td>{l.target_pieces_per_hour}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

type Tenant = { id: string; name: string; slug: string };

function AddFactoryForm({ onCreated }: { onCreated: () => void }) {
  const { data: tenants } = useSWR("/api/v1/tenants", (p) => api<Tenant[]>(p));
  const [tenantId, setTenantId] = useState("");
  const [name, setName] = useState("");
  const [tz, setTz] = useState("UTC");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Pick the first tenant once the list loads.
  if (tenants && tenants.length > 0 && !tenantId) setTenantId(tenants[0].id);

  if (tenants && tenants.length === 0) {
    return (
      <div className="rounded border border-amber-700 bg-amber-950/40 p-4 text-sm">
        No tenants exist. The dev startup hook should have created a "Default" tenant —
        restart the backend container, or POST <code>/api/v1/tenants</code>.
      </div>
    );
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null); setBusy(true);
    try {
      await api("/api/v1/factories", {
        method: "POST",
        body: JSON.stringify({ tenant_id: tenantId, name, timezone: tz }),
      });
      onCreated();
    } catch (e: any) { setErr(e?.message ?? "Failed"); }
    finally { setBusy(false); }
  }

  return (
    <form onSubmit={submit} className="rounded-xl border border-slate-700 p-4 space-y-3">
      <h3 className="font-semibold">Add factory</h3>
      <div className="grid md:grid-cols-3 gap-3">
        <label className="text-sm">
          <span className="opacity-70">Tenant</span>
          <select value={tenantId} onChange={(e) => setTenantId(e.target.value)}
                  className="mt-1 w-full rounded bg-slate-800 border border-slate-700 px-3 py-2">
            {(tenants ?? []).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
        </label>
        <label className="text-sm">
          <span className="opacity-70">Name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} required
                 className="mt-1 w-full rounded bg-slate-800 border border-slate-700 px-3 py-2" />
        </label>
        <label className="text-sm">
          <span className="opacity-70">Timezone</span>
          <input value={tz} onChange={(e) => setTz(e.target.value)}
                 className="mt-1 w-full rounded bg-slate-800 border border-slate-700 px-3 py-2" />
        </label>
      </div>
      {err && <div className="text-sm text-red-400">{err}</div>}
      <button type="submit" disabled={busy}
              className="rounded bg-blue-600 hover:bg-blue-500 px-3 py-2 text-sm font-semibold disabled:opacity-50">
        {busy ? "Adding…" : "Add factory"}
      </button>
    </form>
  );
}

function AddLineForm({ factories, onCreated }: { factories: Factory[]; onCreated: () => void }) {
  const [factoryId, setFactoryId] = useState(factories[0]?.id ?? "");
  const [name, setName] = useState("");
  const [target, setTarget] = useState(80);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null); setBusy(true);
    try {
      await api("/api/v1/lines", {
        method: "POST",
        body: JSON.stringify({
          factory_id: factoryId, name, target_pieces_per_hour: target,
        }),
      });
      onCreated();
    } catch (e: any) { setErr(e?.message ?? "Failed"); }
    finally { setBusy(false); }
  }

  return (
    <form onSubmit={submit} className="rounded-xl border border-slate-700 p-4 space-y-3">
      <h3 className="font-semibold">Add production line</h3>
      <div className="grid md:grid-cols-3 gap-3">
        <label className="text-sm">
          <span className="opacity-70">Factory</span>
          <select value={factoryId} onChange={(e) => setFactoryId(e.target.value)}
                  className="mt-1 w-full rounded bg-slate-800 border border-slate-700 px-3 py-2">
            {factories.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
          </select>
        </label>
        <label className="text-sm">
          <span className="opacity-70">Name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} required
                 placeholder="Line A"
                 className="mt-1 w-full rounded bg-slate-800 border border-slate-700 px-3 py-2" />
        </label>
        <label className="text-sm">
          <span className="opacity-70">Target pieces / hr</span>
          <input value={target} onChange={(e) => setTarget(Number(e.target.value))} type="number" min={0}
                 className="mt-1 w-full rounded bg-slate-800 border border-slate-700 px-3 py-2" />
        </label>
      </div>
      {err && <div className="text-sm text-red-400">{err}</div>}
      <button type="submit" disabled={busy}
              className="rounded bg-blue-600 hover:bg-blue-500 px-3 py-2 text-sm font-semibold disabled:opacity-50">
        {busy ? "Adding…" : "Add line"}
      </button>
    </form>
  );
}
