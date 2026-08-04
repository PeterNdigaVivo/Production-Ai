import { getAccessToken, useAuth } from "@/lib/auth";

export const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
export const WS_URL = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000/api/v1/ws/events";

/** Error thrown by api() / apiBlob() for non-OK HTTP responses. Carries the
 * status code and the parsed `detail` string (FastAPI convention) so callers
 * can branch on failure kind — e.g. 404-empty-stream vs 404-unknown-resource. */
export class ApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string, message?: string) {
    super(message ?? `${status}: ${detail}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function _readDetail(res: Response): Promise<string> {
  const text = await res.text().catch(() => "");
  if (!text) return "";
  try {
    const j = JSON.parse(text);
    return typeof j?.detail === "string" ? j.detail : text;
  } catch {
    return text;
  }
}

function _handle401() {
  if (typeof window !== "undefined") {
    useAuth.getState().clear();
    if (!window.location.pathname.startsWith("/login")) {
      window.location.href = "/login";
    }
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getAccessToken();
  const headers = new Headers(init?.headers || {});
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init?.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");

  const res = await fetch(`${API_URL}${path}`, { cache: "no-store", ...init, headers });

  if (res.status === 401) {
    _handle401();
    throw new ApiError(401, "unauthorized");
  }
  if (!res.ok) {
    const detail = await _readDetail(res);
    throw new ApiError(res.status, detail, `${res.status} ${res.statusText}${detail ? `: ${detail}` : ""}`);
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
    _handle401();
    throw new ApiError(401, "unauthorized");
  }
  if (!res.ok) {
    const detail = await _readDetail(res);
    throw new ApiError(res.status, detail, `${res.status} ${res.statusText}${detail ? `: ${detail}` : ""}`);
  }
  return { blob: await res.blob(), headers: res.headers };
}
