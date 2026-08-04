import { getAccessToken, useAuth } from "@/lib/auth";

export const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
export const WS_URL = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000/api/v1/ws/events";

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getAccessToken();
  const headers = new Headers(init?.headers || {});
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init?.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");

  const res = await fetch(`${API_URL}${path}`, { cache: "no-store", ...init, headers });

  if (res.status === 401) {
    if (typeof window !== "undefined") {
      useAuth.getState().clear();
      if (!window.location.pathname.startsWith("/login")) {
        window.location.href = "/login";
      }
    }
    throw new Error("unauthorized");
  }
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}${text ? `: ${text}` : ""}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

/** Fetch a binary response (e.g. image/jpeg) with the same auth handling as
 * `api()`. Returns the raw Blob plus the response Headers, so callers can
 * both display the payload and read metadata (e.g. X-Frame-Width/Height). */
export async function apiBlob(
  path: string,
  init?: RequestInit,
): Promise<{ blob: Blob; headers: Headers }> {
  const token = getAccessToken();
  const headers = new Headers(init?.headers || {});
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const res = await fetch(`${API_URL}${path}`, { cache: "no-store", ...init, headers });

  if (res.status === 401) {
    if (typeof window !== "undefined") {
      useAuth.getState().clear();
      if (!window.location.pathname.startsWith("/login")) {
        window.location.href = "/login";
      }
    }
    throw new Error("unauthorized");
  }
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}${text ? `: ${text}` : ""}`);
  }
  return { blob: await res.blob(), headers: res.headers };
}
