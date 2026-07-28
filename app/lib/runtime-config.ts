// app/lib/runtime-config.ts
//
// Electron (main.js) writes public/runtime-config.json once it knows the
// actual ports backend.py / main.py landed on (they may not be the
// defaults if 8000/8001 were taken). This module fetches it once and
// caches the result so components stop hardcoding "http://localhost:8000".
//
// Falls back to the historical defaults if the file isn't present (e.g.
// running via `npm run dev` outside Electron).

type RuntimeConfig = {
  apiUrl: string;
  aiUrl: string;
};

let cached: RuntimeConfig | null = null;
let inFlight: Promise<RuntimeConfig> | null = null;

const DEFAULTS: RuntimeConfig = {
  apiUrl: process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000",
  aiUrl: process.env.NEXT_PUBLIC_AI_URL || "http://localhost:8001",
};

export async function getRuntimeConfig(): Promise<RuntimeConfig> {
  if (cached) return cached;
  if (inFlight) return inFlight;

  inFlight = (async () => {
    try {
      const res = await fetch("/runtime-config.json", { cache: "no-store" });
      if (!res.ok) throw new Error("runtime-config.json not found");
      const data = await res.json();
      cached = {
        apiUrl: data.apiUrl || DEFAULTS.apiUrl,
        aiUrl: data.aiUrl || DEFAULTS.aiUrl,
      };
    } catch {
      cached = DEFAULTS;
    }
    return cached;
  })();

  return inFlight;
}

// Synchronous accessor for code that already awaited getRuntimeConfig()
// once at app startup (e.g. in a top-level layout effect) and just needs
// the cached value afterward without re-fetching.
export function getRuntimeConfigSync(): RuntimeConfig {
  return cached || DEFAULTS;
}