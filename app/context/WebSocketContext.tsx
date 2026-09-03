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
        const msg: AlertMessage & { channel?: string } = JSON.parse(event.data);
        setLatestAlert(msg);
        // BUG FOUND 2026-09-02: this used to hardcode `.get("incidents")` --
        // every broadcast, regardless of its own `channel` field, only ever
        // reached listeners subscribed to "incidents" (plus "*"). The backend
        // genuinely broadcasts distinct channels ("users", "camera_models",
        // "optimize_weights", "records", ...) and AdminUsersView/AiModelsPanel
        // subscribe to exactly those names expecting a live push -- but
        // nothing ever routed a message to them, so a new user, a permission
        // change, a detection-model toggle, and (worst of all) a live
        // optimize_weights progress update all sat frozen for up to the 60s
        // fallback poll instead of updating instantly. "incidents" and "*"
        // subscribers were never affected, which is exactly why this went
        // unnoticed: those are the two channels this repo's manual testing
        // happened to exercise most.
        if (msg.channel) {
          listenersRef.current.get(msg.channel)?.forEach((fn) => fn(msg));
        }
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

  // BUG FOUND 2026-09-03 (found via manual multi-tab testing): 'ecoToken'/
  // 'ecoUser' live in plain localStorage, which every tab on this origin
  // shares, and every fetch across the app independently re-reads the token
  // at call time rather than binding to whichever session first rendered
  // the page. Log in as a second account in another tab and the FIRST tab
  // keeps its own UI chrome (header still says the original user/role) but
  // every subsequent request it makes now carries the second account's
  // token -- observed concretely as a Barangay Admin's own "Personnel" list
  // silently turning into every account in the system (DevTeam's view)
  // because DevTeam happened to log in elsewhere. Nothing here stops two
  // logins sharing one browser profile (that's a deliberate, documented
  // choice elsewhere -- shared front-desk PCs are a real deployment shape
  // for this app), but a tab silently acting under an identity its own
  // header never updated to show is a genuine confused-deputy risk, not
  // just a stale-UI cosmetic issue. The 'storage' event only fires in
  // tabs OTHER than the one that made the write, so this can't loop on the
  // tab's own login/logout -- reloading is the simplest way to make every
  // open tab immediately and visibly consistent with whichever session is
  // actually live in this browser, rather than silently drifting.
  useEffect(() => {
    function handleStorage(e: StorageEvent) {
      if (e.key === 'ecoToken' && e.newValue !== e.oldValue) {
        window.location.reload();
      }
    }
    window.addEventListener('storage', handleStorage);
    return () => window.removeEventListener('storage', handleStorage);
  }, []);

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
export function useLiveChannel(channel: string, onEvent: () => void, ready: boolean = true) {
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

  // BUG FOUND 2026-09-03: the ref above fixed the *closure* staleness, but
  // not a second problem sitting right next to it -- this effect's single
  // guaranteed initial call still fires at mount, which for page.tsx is
  // BEFORE its own hydrate-currentUser-from-localStorage effect has
  // committed. fetchStats/fetchActiveAlertCache's `if (!currentUser) return`
  // guard was correctly reading null at that instant and skipping -- but
  // nothing re-ran them once currentUser actually landed a render later.
  // Confirmed live: the Incident Queue sat on its loading skeleton for the
  // full 60s fallback period after every fresh login, not populating until
  // that fallback tick finally fired. `ready` lets a caller hold this
  // effect off (and everything it does -- initial call, subscribe, fallback
  // poll) until the value its callback actually needs exists; flipping
  // false -> true re-runs the effect exactly once, at which point the
  // initial call finally sees a real currentUser. Defaults to true so every
  // other call site (mounted only after currentUser already exists, inside
  // the dashboard's own `if (!currentUser) return` guard) is unaffected.
  useEffect(() => {
    if (!ready) return;
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
  }, [channel, subscribe, ready]);
}