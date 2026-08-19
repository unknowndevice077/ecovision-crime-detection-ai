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
import { useRuntimeConfig } from '../hooks/useRuntimeConfig';

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
  const { apiUrl } = useRuntimeConfig();
  const [connected, setConnected] = useState(false);
  const [latestAlert, setLatestAlert] = useState<AlertMessage | null>(null);
  const listenersRef = useRef<Map<string, Set<Listener>>>(new Map());
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Fixed 3s-forever reconnect would hammer a genuinely-down backend
  // indefinitely (a real 24/7-monitoring failure mode: backend crash,
  // extended brownout). Back off exponentially instead, capped at 30s, and
  // reset back to the fast 3s delay the moment a connection actually opens.
  const reconnectDelay = useRef(3000);
  const RECONNECT_MIN_MS = 3000;
  const RECONNECT_MAX_MS = 30000;

  const connect = useCallback(() => {
    // apiUrl starts at the historical default and updates once
    // runtime-config.json loads (see useRuntimeConfig) -- this effect
    // re-runs and reconnects to the real URL when that happens, so a
    // fallback port (backend didn't land on 8000) doesn't leave this
    // socket permanently pointed at the wrong address.
    const wsUrl = apiUrl.replace(/^http/, "ws") + "/ws";
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      reconnectDelay.current = RECONNECT_MIN_MS; // connection succeeded, reset backoff
    };

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
      // Exponential backoff reconnect -- a dropped wifi link on a smartpole
      // shouldn't require a manual page refresh to recover live updates, but
      // an extended backend outage shouldn't be hammered at a fixed 3s pace
      // forever either.
      reconnectTimer.current = setTimeout(connect, reconnectDelay.current);
      reconnectDelay.current = Math.min(reconnectDelay.current * 2, RECONNECT_MAX_MS);
    };

    ws.onerror = () => ws.close();
  }, [apiUrl]);

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

  // BUG FOUND 2026-08-19: `onEvent` was called directly inside this effect,
  // whose dependency array is just [channel] -- a constant string literal
  // that never changes, so this effect runs exactly ONCE, on mount, and
  // every subsequent invocation (every WS broadcast, every 60s fallback
  // tick) calls the SAME `onEvent` closure captured at that first render.
  // For a callback like CrimeReportsView's fetchIncidents, which reads
  // nothing but localStorage fresh on every call, that staleness is
  // invisible. For page.tsx's fetchStats/fetchActiveAlertCache -- both
  // guarded by `if (!currentUser) return;` -- it was fatal: currentUser is
  // still null on the very first render (its own hydration effect hasn't
  // committed yet), so the closure captured HERE, permanently, was the one
  // that always no-ops. Every later broadcast and every 60s poll kept
  // calling that same frozen closure for the rest of the session -- the
  // Incident Queue (accept/decline) and nav badge counts could never
  // populate, ever, no matter how many real incidents landed, because
  // this hook never called a version of the function that could see them.
  // A ref sidesteps it: the ref's .current is reassigned every render (so
  // it always holds the latest closure), while the effect itself still
  // only needs to run once for the subscribe/unsubscribe lifecycle.
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  useEffect(() => {
    onEventRef.current(); // initial load
    const unsubscribe = subscribe(channel, () => onEventRef.current());
    // Belt-and-suspenders: still poll, but slowly (60s) as a fallback in
    // case a broadcast gets missed during a reconnect window -- this is
    // NOT the primary refresh mechanism anymore, just a safety net.
    const fallback = setInterval(() => onEventRef.current(), 60000);
    return () => {
      unsubscribe();
      clearInterval(fallback);
    };
  }, [channel, subscribe]);
}