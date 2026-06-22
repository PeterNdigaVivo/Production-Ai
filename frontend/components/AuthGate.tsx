"use client";
import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";

export default function AuthGate({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const path = usePathname();
  const token = useAuth((s) => s.accessToken);
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => setHydrated(true), []);

  useEffect(() => {
    if (!hydrated) return;
    if (!token && path !== "/login") router.replace("/login");
  }, [hydrated, token, path, router]);

  if (!hydrated) return null;
  if (path === "/login") return <>{children}</>;
  if (!token) return null;
  return <>{children}</>;
}
