"use client";
// app/context/WebSocketContext.tsx
//
// Replaces the per-view 5-8s setInterval polling in HistoryView, RecordsView,
// AdminUsersView, DevteamView etc. with ONE shared socket connection.
// Any view can subscribe to a channel ("incidents", "records", ...) and
// gets notified the moment the backend broadcasts something relevant,
// instead of waiting up to 8s and re-fetching on a timer regardless of
// whether anything changed.
//
// Wrap the app with <WebSocketProvider> once in layout.tsx / a client
// wrapper, then any component calls useLiveChannel("incidents", refetchFn).

import React, { createContext, useContext, useEffect, useRef, useCallback, useState } from 'react';

const WS_URL = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/^http/, "ws") + "/ws";

type AlertMessage = {
  status: string;          // "CRITICAL"
  id: string;
  type: string;
  location: string;
  conf: number;
  cameraLinkId: string;
};

type Listener = (msg: AlertMessage) => void;

type WebSocketContextValue = {
  connected: boolean;
  latestAlert: AlertMessage | null;
  subscribe: (channel: string, fn: Listener) => () => void; // returns unsubscribe
};

const WebSocketContext = createContext<WebSocketContextValue | null>(null);

export function WebSocketProvider({ children }: { children: React.ReactNode }) {
  const [connected, setConnected] = useState(false);
  const [latestAlert, setLatestAlert] = useState<AlertMessage | null>(null);
  const listenersRef = useRef<Map<string, Set<Listener>>>(new Map());
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const connect = useCallback(() => {
    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;

    ws.onopen = () => setConnected(true);

    ws.onmessage = (event) => {
      try {
        const msg: AlertMessage = JSON.parse(event.data);
        setLatestAlert(msg);
        // Every incident-shaped broadcast should also nudge any view
        // subscribed to "incidents" (Map, History) to refetch --
        // cheap correctness win over trying to fully sync state client-side.
        listenersRef.current.get("incidents")?.forEach((fn) => fn(msg));
        listenersRef.current.get("*")?.forEach((fn) => fn(msg));
      } catch {
        // non-JSON / unrecognized payload, ignore
      }
    };

    ws.onclose = () => {
      setConnected(false);
      // simple backoff reconnect -- a dropped wifi link on a smartpole
      // shouldn't require a manual page refresh to recover live updates
      reconnectTimer.current = setTimeout(connect, 3000);
    };

    ws.onerror = () => ws.close();
  }, []);

  useEffect(() => {
    connect();
    return () => {
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      wsRef.current?.close();
    };
  }, [connect]);

  const subscribe = useCallback((channel: string, fn: Listener) => {
    if (!listenersRef.current.has(channel)) listenersRef.current.set(channel, new Set());
    listenersRef.current.get(channel)!.add(fn);
    return () => listenersRef.current.get(channel)?.delete(fn);
  }, []);

  return (
    <WebSocketContext.Provider value={{ connected, latestAlert, subscribe }}>
      {children}
    </WebSocketContext.Provider>
  );
}

export function useWebSocketContext() {
  const ctx = useContext(WebSocketContext);
  if (!ctx) throw new Error("useWebSocketContext must be used inside <WebSocketProvider>");
  return ctx;
}

/**
 * Drop-in replacement for a polling useEffect. Calls `onEvent` whenever the
 * given channel fires, AND still does one initial fetch on mount.
 *
 * Before:
 *   useEffect(() => {
 *     fetchStuff();
 *     const interval = setInterval(fetchStuff, 8000);
 *     return () => clearInterval(interval);
 *   }, []);
 *
 * After:
 *   useLiveChannel("incidents", fetchStuff);
 */
export function useLiveChannel(channel: string, onEvent: () => void) {
  const { subscribe } = useWebSocketContext();

  useEffect(() => {
    onEvent(); // initial load
    const unsubscribe = subscribe(channel, () => onEvent());
    // Belt-and-suspenders: still poll, but slowly (60s) as a fallback in
    // case a broadcast gets missed during a reconnect window -- this is
    // NOT the primary refresh mechanism anymore, just a safety net.
    const fallback = setInterval(onEvent, 60000);
    return () => {
      unsubscribe();
      clearInterval(fallback);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [channel]);
}