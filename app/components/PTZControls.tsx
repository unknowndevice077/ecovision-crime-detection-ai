"use client";

import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  ChevronUp, ChevronDown, ChevronLeft, ChevronRight,
  ZoomIn, ZoomOut, Bookmark, Loader2, Square,
} from 'lucide-react';

/* PTZ control pad for an ONVIF camera.
 *
 * Capability-driven on purpose: it asks the backend what the connected
 * camera can actually do and renders only that. A camera with no zoom
 * motors shows no zoom buttons, rather than buttons that quietly fail --
 * an operator who cannot tell "did nothing" from "didn't work" will not
 * trust the panel during an incident.
 *
 * Movement is hold-to-move: press starts a ContinuousMove, release sends
 * Stop. The backend also auto-stops after a short duration, so a lost
 * pointerup (dragged off the button, tab closed, network drop) cannot
 * leave the camera panning until someone power-cycles it.
 */

type Caps = {
  configured: boolean;
  reachable?: boolean;
  reason?: string;
  error?: string;
  pan_tilt: boolean;
  zoom: boolean;
  presets: boolean;
  two_way_audio: boolean;
  two_way_audio_note?: string;
  host?: string;
};

type Preset = { token: string; name: string };

export default function PTZControls({ apiUrl }: { apiUrl: string }) {
  const [caps, setCaps] = useState<Caps | null>(null);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const holding = useRef(false);

  const authHeaders = useCallback((): Record<string, string> => {
    const token = typeof window !== 'undefined' ? localStorage.getItem('ecoToken') : null;
    return token
      ? { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
      : { 'Content-Type': 'application/json' };
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`${apiUrl}/api/ptz/capabilities`, { headers: authHeaders() });
        if (!res.ok) return;
        const data: Caps = await res.json();
        if (cancelled) return;
        setCaps(data);
        if (data.presets) {
          const pr = await fetch(`${apiUrl}/api/ptz/presets`, { headers: authHeaders() });
          if (pr.ok && !cancelled) setPresets((await pr.json()).presets || []);
        }
      } catch {
        /* camera panel is non-critical; a failed probe just leaves it hidden */
      }
    })();
    return () => { cancelled = true; };
  }, [apiUrl, authHeaders]);

  const move = async (pan: number, tilt: number, zoom = 0) => {
    holding.current = true;
    setErr(null);
    try {
      const res = await fetch(`${apiUrl}/api/ptz/move`, {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify({ pan, tilt, zoom, duration: 0.8 }),
      });
      if (!res.ok) setErr((await res.json()).detail || `HTTP ${res.status}`);
    } catch (e: any) {
      setErr(e?.message || 'request failed');
    }
  };

  const stop = async () => {
    if (!holding.current) return;
    holding.current = false;
    try {
      await fetch(`${apiUrl}/api/ptz/stop`, { method: 'POST', headers: authHeaders() });
    } catch { /* backend auto-stop is the backstop */ }
  };

  const gotoPreset = async (token: string) => {
    setBusy(true); setErr(null);
    try {
      const res = await fetch(`${apiUrl}/api/ptz/presets/${encodeURIComponent(token)}/goto`,
        { method: 'POST', headers: authHeaders() });
      if (!res.ok) setErr((await res.json()).detail || `HTTP ${res.status}`);
    } finally { setBusy(false); }
  };

  const savePreset = async () => {
    const name = window.prompt('Name this camera position:');
    if (!name) return;
    setBusy(true); setErr(null);
    try {
      const res = await fetch(`${apiUrl}/api/ptz/presets`, {
        method: 'POST', headers: authHeaders(), body: JSON.stringify({ name }),
      });
      if (res.ok) {
        const pr = await fetch(`${apiUrl}/api/ptz/presets`, { headers: authHeaders() });
        if (pr.ok) setPresets((await pr.json()).presets || []);
      } else {
        setErr((await res.json()).detail || `HTTP ${res.status}`);
      }
    } finally { setBusy(false); }
  };

  if (!caps) return null;

  // Not configured / unreachable: say so, plainly and quietly. Better than a
  // dead control pad that looks functional.
  if (!caps.configured || caps.reachable === false) {
    return (
      <div className="label px-2 py-1 border" style={{ borderColor: 'var(--line)', color: 'var(--text-3)' }}>
        PTZ unavailable — {caps.reason || caps.error || 'camera not reachable'}
      </div>
    );
  }

  const btn = "flex items-center justify-center border transition-colors hover:bg-white/10 active:scale-[0.94] disabled:opacity-30";
  const btnStyle = { borderColor: 'var(--line-2)', color: 'var(--text)' };
  const hold = (pan: number, tilt: number, zoom = 0) => ({
    onPointerDown: () => move(pan, tilt, zoom),
    onPointerUp: stop,
    onPointerLeave: stop,
    onPointerCancel: stop,
  });

  return (
    <div className="flex items-start gap-3">
      {caps.pan_tilt && (
        <div className="grid grid-cols-3 gap-0.5" style={{ width: 96 }}>
          <span />
          <button {...hold(0, 0.6)} className={`${btn} h-7`} style={btnStyle} title="Tilt up" aria-label="Tilt up">
            <ChevronUp size={14} />
          </button>
          <span />
          <button {...hold(-0.6, 0)} className={`${btn} h-7`} style={btnStyle} title="Pan left" aria-label="Pan left">
            <ChevronLeft size={14} />
          </button>
          <button onClick={stop} className={`${btn} h-7`} style={btnStyle} title="Stop" aria-label="Stop movement">
            <Square size={10} />
          </button>
          <button {...hold(0.6, 0)} className={`${btn} h-7`} style={btnStyle} title="Pan right" aria-label="Pan right">
            <ChevronRight size={14} />
          </button>
          <span />
          <button {...hold(0, -0.6)} className={`${btn} h-7`} style={btnStyle} title="Tilt down" aria-label="Tilt down">
            <ChevronDown size={14} />
          </button>
          <span />
        </div>
      )}

      {caps.zoom && (
        <div className="flex flex-col gap-0.5" style={{ width: 32 }}>
          <button {...hold(0, 0, 0.6)} className={`${btn} h-7`} style={btnStyle} title="Zoom in" aria-label="Zoom in">
            <ZoomIn size={14} />
          </button>
          <button {...hold(0, 0, -0.6)} className={`${btn} h-7`} style={btnStyle} title="Zoom out" aria-label="Zoom out">
            <ZoomOut size={14} />
          </button>
        </div>
      )}

      {caps.presets && (
        <div className="flex flex-col gap-1 min-w-0">
          <div className="flex items-center gap-1">
            <select
              onChange={(e) => e.target.value && gotoPreset(e.target.value)}
              defaultValue=""
              disabled={busy}
              className="data text-[10px] bg-transparent border px-1 py-0.5 outline-none"
              style={{ borderColor: 'var(--line-2)', color: 'var(--text)', maxWidth: 130 }}
              aria-label="Go to saved camera position"
            >
              <option value="" style={{ background: 'var(--panel)' }}>Go to preset…</option>
              {presets.map((p) => (
                <option key={p.token} value={p.token} style={{ background: 'var(--panel)' }}>{p.name}</option>
              ))}
            </select>
            <button onClick={savePreset} disabled={busy} className={`${btn} h-6 px-1.5 gap-1`} style={btnStyle} title="Save current position as a preset">
              {busy ? <Loader2 size={11} className="animate-spin" /> : <Bookmark size={11} />}
              <span className="label">Save</span>
            </button>
          </div>
          {err && <span className="label" style={{ color: 'var(--critical)' }}>{err}</span>}
        </div>
      )}

      {!caps.pan_tilt && !caps.zoom && !caps.presets && (
        <span className="label" style={{ color: 'var(--text-3)' }}>
          Camera connected but reports no PTZ capability
        </span>
      )}
    </div>
  );
}
