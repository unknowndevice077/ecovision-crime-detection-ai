"use client";
import { useEffect, useState } from "react";
import { getRuntimeConfig } from "../lib/runtime-config";

export function useRuntimeConfig() {
  const [config, setConfig] = useState({
    apiUrl: process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000",
    aiUrl: process.env.NEXT_PUBLIC_AI_URL || "http://localhost:8001",
  });
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let mounted = true;
    getRuntimeConfig().then((cfg) => {
      if (mounted) {
        setConfig(cfg);
        setLoaded(true);
      }
    });
    return () => { mounted = false; };
  }, []);

  return { ...config, loaded };
}