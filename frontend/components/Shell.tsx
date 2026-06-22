"use client";
import { usePathname } from "next/navigation";
import { useAuth } from "@/lib/auth";

export default function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const clear = useAuth((s) => s.clear);

  if (path === "/login") return <>{children}</>;

  function signOut() {
    clear();
    window.location.href = "/login";
  }

  return (
    <div className="min-h-screen flex">
      <aside className="w-56 border-r border-slate-800 p-4 flex flex-col">
        <h1 className="text-xl font-bold mb-6">Production-AI</h1>
        <nav className="flex flex-col gap-1 text-sm flex-1">
          <a href="/" className="hover:underline">Executive</a>
          <a href="/lines" className="hover:underline">Production Lines</a>
          <a href="/cameras" className="hover:underline">Cameras</a>
          <a href="/alerts" className="hover:underline">Alerts</a>
        </nav>
        <button onClick={signOut} className="text-xs opacity-60 hover:opacity-100 text-left">Sign out</button>
      </aside>
      <main className="flex-1 p-6">{children}</main>
    </div>
  );
}
