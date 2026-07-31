"use client";
import { useState } from "react";
import { API_URL } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export default function LoginPage() {
  const [email, setEmail] = useState("admin@local.dev");
  const [password, setPassword] = useState("admin");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const setTokens = useAuth((s) => s.setTokens);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const res = await fetch(`${API_URL}/api/v1/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      if (!res.ok) {
        setError(res.status === 401 ? "Invalid credentials" : `Error ${res.status}`);
        return;
      }
      const data = await res.json();
      setTokens(data.access_token, data.refresh_token);
      window.location.href = "/";
    } catch (e: any) {
      setError(e?.message ?? "Network error");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4 rounded-xl border border-slate-700 p-6">
        <h1 className="text-2xl font-bold">Production-AI</h1>
        <p className="text-sm opacity-70">Sign in to continue</p>
        <label className="block text-sm">
          <span className="opacity-70">Email</span>
          <input value={email} onChange={(e) => setEmail(e.target.value)} type="email" required
                 className="mt-1 w-full rounded bg-slate-800 border border-slate-700 px-3 py-2" />
        </label>
        <label className="block text-sm">
          <span className="opacity-70">Password</span>
          <input value={password} onChange={(e) => setPassword(e.target.value)} type="password" required
                 className="mt-1 w-full rounded bg-slate-800 border border-slate-700 px-3 py-2" />
        </label>
        {error && <div className="text-sm text-red-400">{error}</div>}
        <button type="submit" disabled={loading}
                className="w-full rounded bg-blue-600 hover:bg-blue-500 py-2 font-semibold disabled:opacity-50">
          {loading ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
