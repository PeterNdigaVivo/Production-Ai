"use client";
import { create } from "zustand";
import { persist } from "zustand/middleware";

type AuthState = {
  accessToken: string | null;
  refreshToken: string | null;
  setTokens: (a: string, r: string) => void;
  clear: () => void;
};

export const useAuth = create<AuthState>()(
  persist(
    (set) => ({
      accessToken: null,
      refreshToken: null,
      setTokens: (a, r) => set({ accessToken: a, refreshToken: r }),
      clear: () => set({ accessToken: null, refreshToken: null }),
    }),
    { name: "production-ai-auth" }
  )
);

export function getAccessToken(): string | null {
  if (typeof window === "undefined") return null;
  return useAuth.getState().accessToken;
}
