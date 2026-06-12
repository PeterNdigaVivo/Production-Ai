import "../styles/globals.css";
import type { ReactNode } from "react";

export const metadata = {
  title: "Production-AI",
  description: "Garment manufacturing intelligence",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body>
        <div className="min-h-screen flex">
          <aside className="w-56 border-r border-slate-800 p-4 space-y-2">
            <h1 className="text-xl font-bold mb-6">Production-AI</h1>
            <nav className="flex flex-col gap-1 text-sm">
              <a href="/" className="hover:underline">Executive</a>
              <a href="/factory" className="hover:underline">Factory</a>
              <a href="/line" className="hover:underline">Production Line</a>
              <a href="/operator" className="hover:underline">Operator</a>
              <a href="/cameras" className="hover:underline">Cameras</a>
              <a href="/alerts" className="hover:underline">Alerts</a>
            </nav>
          </aside>
          <main className="flex-1 p-6">{children}</main>
        </div>
      </body>
    </html>
  );
}
