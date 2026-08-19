"use client";

import React, { useState } from 'react';
import { Brain, Gauge, Undo2, AlertTriangle } from 'lucide-react';
import { useRuntimeConfig } from '../hooks/useRuntimeConfig';
import { useLiveChannel } from '../context/WebSocketContext';

/* Read-only AI model status + "optimize for this machine" control.
 *
 * Barangay-admin scoped, not DevTeam's full AI Models tab: this shows what's
 * running and lets the machine be sped up (a TensorRT engine build that
 * refuses to install unless it agrees with the .pt weights on real input --
 * see optimize_weights.py), but never lets a barangay account turn a
 * detector on or off. That stays DevTeam-only -- see backend.py's
 * MODEL_VIEW_ROLES comment for why the split sits exactly there: the
 * barangay owns the hardware this runs on (same reasoning as manage_cameras
 * being barangay-only), but "should this camera see less" is a different,
 * higher-stakes call than "how fast is the box under the camera."
 */

function authHeaders() {
  const token = typeof window !== "undefined" ? localStorage.getItem("ecoToken") : null;
  return { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) };
}

type ModelMetricStat = { label: string; value: number; unit: string; note?: string; good?: boolean };
type DetectionModel = {
  name: string;
  display_name: string;
  enabled: boolean;
  experimental: boolean;
  threshold: number;
  weights_present: boolean;
  metrics?: { headline?: { label: string; value: number; unit: string }; stats?: ModelMetricStat[] };
};

type OptimizeStep = {
  label: string;
  state: 'start' | 'building' | 'done' | 'skipped' | 'failed';
  before_ms?: number;
  after_ms?: number;
  speedup?: number;
  reason?: string;
  error?: string;
};

type OptimizeSummary =
  | { kind: 'summary'; results: unknown[]; combined: number | null }
  | { kind: 'reverted'; files: string[] };

type OptimizeState = {
  running: boolean;
  steps: OptimizeStep[];
  summary: OptimizeSummary | null;
  preconditions: { ok: boolean; detail: string } | null;
};

export default function AiModelsPanel() {
  const { apiUrl: API_URL } = useRuntimeConfig();
  const [models, setModels] = useState<DetectionModel[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [optimizeState, setOptimizeState] = useState<OptimizeState | null>(null);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState('');

  const flash = (msg: string) => { setToast(msg); setTimeout(() => setToast(''), 3000); };

  const fetchModels = async () => {
    try {
      const res = await fetch(`${API_URL}/api/devteam/detection-models`, { headers: authHeaders() });
      if (res.ok) setModels((await res.json()).models || []);
    } catch { /* leave the previous list up */ }
    finally { setLoaded(true); }
  };

  const fetchOptimizeStatus = async () => {
    try {
      const res = await fetch(`${API_URL}/api/devteam/optimize_weights/status`, { headers: authHeaders() });
      if (res.ok) setOptimizeState(await res.json());
    } catch { /* leave the previous panel up */ }
  };

  React.useEffect(() => { fetchModels(); fetchOptimizeStatus(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useLiveChannel("optimize_weights", fetchOptimizeStatus);

  const startOptimize = async (revert: boolean) => {
    setBusy(true);
    try {
      const res = await fetch(`${API_URL}/api/devteam/optimize_weights${revert ? '/revert' : ''}`, {
        method: 'POST', headers: authHeaders(),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) { flash(d.detail || 'Could not start'); return; }
      await fetchOptimizeStatus();
      flash(revert ? 'Reverting to standard weights…' : 'Optimizing for this machine…');
    } catch {
      flash('Could not reach the server');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3">
      {toast && (
        <div className="border px-3 py-2 text-[10.5px]" style={{ background: 'var(--panel)', borderColor: 'var(--accent)' }}>
          {toast}
        </div>
      )}

      {/* Model status -- read only */}
      <div className="border p-3" style={{ background: 'var(--panel)', borderColor: 'var(--line)' }}>
        <div className="flex items-center gap-2 mb-3">
          <Brain size={14} style={{ color: 'var(--text-3)' }} />
          <span className="label">AI Models</span>
        </div>
        {!loaded ? (
          <p className="text-[10.5px]" style={{ color: 'var(--text-2)' }}>Loading…</p>
        ) : models.length === 0 ? (
          <p className="text-[10.5px]" style={{ color: 'var(--text-2)' }}>No detection models configured</p>
        ) : (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            {models.map(m => (
              <div key={m.name} className="border p-2.5" style={{ borderColor: 'var(--line)', background: 'var(--bg)' }}>
                <div className="flex items-center justify-between mb-1">
                  <span className="text-[9.5px] uppercase tracking-wide truncate" style={{ color: 'var(--text-2)' }}>
                    {m.display_name}
                  </span>
                  <span
                    className="text-[8px] font-bold uppercase px-1 py-0.5 border shrink-0"
                    style={{
                      color: m.enabled ? 'var(--ok)' : 'var(--text-3)',
                      borderColor: m.enabled ? 'var(--ok)' : 'var(--line-2)',
                    }}
                  >
                    {m.enabled ? 'Active' : 'Off'}
                  </span>
                </div>
                {m.metrics?.headline ? (
                  <div className="data text-base font-bold text-white leading-none">
                    {m.metrics.headline.value}{m.metrics.headline.unit}
                    <span className="text-[9px] font-normal ml-1" style={{ color: 'var(--text-3)' }}>{m.metrics.headline.label}</span>
                  </div>
                ) : (
                  <div className="text-[9.5px]" style={{ color: 'var(--text-3)' }}>—</div>
                )}
              </div>
            ))}
          </div>
        )}
        <p className="text-[9.5px] leading-relaxed mt-3" style={{ color: 'var(--text-3)' }}>
          Turning a model on or off is a DevTeam action. This view is read-only.
        </p>
      </div>

      {/* Optimize for this machine */}
      <div className="border p-3" style={{ background: 'var(--panel)', borderColor: 'var(--line)' }}>
        <div className="flex items-center justify-between gap-3 mb-2">
          <div className="flex items-center gap-2 min-w-0">
            <Gauge size={14} style={{ color: 'var(--text-3)' }} />
            <span className="label">Optimize for this machine</span>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button
              onClick={() => startOptimize(true)}
              disabled={busy || !!optimizeState?.running}
              title="Delete every optimized engine, returning to standard weights"
              className="flex items-center gap-1.5 px-2.5 py-1.5 text-[9.5px] uppercase tracking-wide border disabled:opacity-40 disabled:cursor-not-allowed"
              style={{ borderColor: 'var(--line-2)', color: 'var(--text-2)' }}
            >
              <Undo2 size={11} /> Revert
            </button>
            <button
              onClick={() => startOptimize(false)}
              disabled={busy || !!optimizeState?.running}
              className="flex items-center gap-1.5 px-2.5 py-1.5 text-[9.5px] uppercase tracking-wide border disabled:opacity-40 disabled:cursor-not-allowed"
              style={{ borderColor: 'var(--accent)', color: 'var(--accent)' }}
            >
              <Gauge size={11} /> {optimizeState?.running ? 'Running…' : 'Optimize'}
            </button>
          </div>
        </div>

        <p className="text-[9.5px] leading-relaxed" style={{ color: 'var(--text-2)' }}>
          Speeds up detection on this specific PC. Takes a few minutes; safe to leave running.
          Never changes what a model decides -- a faster result is refused unless it agrees with
          the original on real input.
        </p>

        {optimizeState?.preconditions && !optimizeState.preconditions.ok && (
          <div className="flex items-start gap-2 mt-2.5 pt-2.5 border-t" style={{ borderColor: 'var(--line)' }}>
            <AlertTriangle size={11} style={{ color: 'var(--warn)' }} className="mt-0.5 shrink-0" />
            <p className="text-[9.5px] leading-relaxed" style={{ color: 'var(--text-2)' }}>
              <span style={{ color: 'var(--warn)', fontWeight: 700 }}>Not available on this machine: </span>
              {optimizeState.preconditions.detail}
            </p>
          </div>
        )}

        {(optimizeState?.running || (optimizeState?.steps?.length ?? 0) > 0) && (
          <div className="mt-2.5 pt-2.5 border-t space-y-1" style={{ borderColor: 'var(--line)' }}>
            {optimizeState!.steps.map(s => (
              <div key={s.label} className="flex items-center justify-between text-[9.5px] font-mono">
                <span style={{ color: 'var(--text-2)' }}>{s.label}</span>
                {s.state === 'done' ? (
                  <span style={{ color: 'var(--ok)' }}>
                    {s.before_ms?.toFixed(1)}ms → {s.after_ms?.toFixed(1)}ms
                    <span className="text-white"> ({s.speedup?.toFixed(2)}x)</span>
                  </span>
                ) : s.state === 'failed' ? (
                  <span style={{ color: 'var(--critical)' }} className="truncate max-w-[50%]" title={s.error}>failed</span>
                ) : s.state === 'skipped' ? (
                  <span style={{ color: 'var(--text-3)' }}>skipped</span>
                ) : s.state === 'building' ? (
                  <span style={{ color: 'var(--warn)' }}>building…</span>
                ) : (
                  <span style={{ color: 'var(--text-3)' }}>starting…</span>
                )}
              </div>
            ))}
            {optimizeState?.summary?.kind === 'summary' && optimizeState.summary.combined != null && (
              <div className="flex items-center justify-between pt-1.5 mt-1.5 border-t" style={{ borderColor: 'var(--line)' }}>
                <span className="text-[9.5px] uppercase tracking-wide" style={{ color: 'var(--text-2)' }}>Combined</span>
                <span className="text-[12px] font-mono" style={{ color: 'var(--ok)' }}>{optimizeState.summary.combined.toFixed(2)}x faster</span>
              </div>
            )}
            {optimizeState?.summary?.kind === 'reverted' && (
              <p className="text-[9.5px]" style={{ color: 'var(--text-2)' }}>
                Removed {optimizeState.summary.files.length} engine file(s). Back to standard weights.
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
