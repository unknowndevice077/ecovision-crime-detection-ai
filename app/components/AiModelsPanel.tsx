"use client";

import React, { useState } from 'react';
import { Brain, Gauge, Undo2, AlertTriangle } from 'lucide-react';
import { useRuntimeConfig } from '../hooks/useRuntimeConfig';
import { useLiveChannel } from '../context/WebSocketContext';

/* AI model status, on/off, + "optimize for this machine" control.
 *
 * Barangay-admin scoped, not DevTeam's full AI Models tab: no threshold
 * editing (that number is what the model's reported accuracy was measured
 * at -- DevTeam-only, enforced server-side in set_detection_model, not just
 * hidden here) and no per-model metrics breakdown. But turning a detector
 * on or off IS a barangay-admin action, same as manage_cameras -- the
 * barangay owns the camera and the hardware this actually runs on, and
 * "should this camera see less" is exactly the kind of call that sits with
 * whoever is accountable for that camera. (Was DevTeam-only until
 * 2026-08-23; see backend.py's set_detection_model comment for why that
 * changed.)
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
  metrics?: {
    status?: string;
    headline?: { label: string; value: number; unit: string };
    stats?: ModelMetricStat[];
    caveat?: string;
  };
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

// Mirrors backend.py's DETECTION_CLASSES tuple exactly -- order and keys
// both matter here since these are used as object keys against the
// /api/cameras/models response, not just display labels.
const CAMERA_MODEL_KEYS: { key: string; label: string }[] = [
  { key: 'violence', label: 'Violence' },
  { key: 'weapon', label: 'Weapon' },
  { key: 'robbery', label: 'Robbery' },
  { key: 'vandalism', label: 'Vandalism' },
  { key: 'vandalism_marks', label: 'Graffiti Marks' },
];

type CameraWithModels = {
  id: string;
  name: string;
  barangay_id: string;
  models: Record<string, boolean>;
};

export default function AiModelsPanel() {
  const { apiUrl: API_URL } = useRuntimeConfig();
  const [models, setModels] = useState<DetectionModel[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [optimizeState, setOptimizeState] = useState<OptimizeState | null>(null);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState('');
  const [modelBusy, setModelBusy] = useState<string | null>(null);
  const [restartPending, setRestartPending] = useState(false);
  const [confirmEnable, setConfirmEnable] = useState<DetectionModel | null>(null);
  const [cameras, setCameras] = useState<CameraWithModels[]>([]);
  const [camerasLoaded, setCamerasLoaded] = useState(false);
  const [cameraCellBusy, setCameraCellBusy] = useState<string | null>(null); // `${camera_id}:${model_key}`

  const flash = (msg: string) => { setToast(msg); setTimeout(() => setToast(''), 3000); };

  const fetchCameraModels = async () => {
    try {
      const res = await fetch(`${API_URL}/api/cameras/models`, { headers: authHeaders() });
      if (res.ok) setCameras((await res.json()).cameras || []);
    } catch { /* leave the previous list up */ }
    finally { setCamerasLoaded(true); }
  };

  // A camera override can only narrow a model that's already on globally --
  // same rule the backend enforces (set_camera_model refuses to "enable" a
  // camera override for a class that's off system-wide). Checking it here
  // too means the toggle just looks disabled instead of firing a request
  // that's going to come back as an error.
  const globallyOff = (key: string) => models.find(m => m.name === key)?.enabled === false;

  const toggleCameraModel = async (cam: CameraWithModels, modelKey: string) => {
    const next = !cam.models[modelKey];
    if (next && globallyOff(modelKey)) {
      flash(`${modelKey} is off system-wide -- ask DevTeam to enable it globally first.`);
      return;
    }
    const cellId = `${cam.id}:${modelKey}`;
    setCameraCellBusy(cellId);
    // Optimistic update, rolled back on failure -- a grid of toggles feels
    // wrong if every click waits on a round trip before the switch moves.
    setCameras(prev => prev.map(c => c.id === cam.id ? { ...c, models: { ...c.models, [modelKey]: next } } : c));
    try {
      const res = await fetch(`${API_URL}/api/cameras/${cam.id}/models/${modelKey}`, {
        method: 'PATCH',
        headers: authHeaders(),
        body: JSON.stringify({ enabled: next }),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        flash(d.detail || 'Could not save');
        setCameras(prev => prev.map(c => c.id === cam.id ? { ...c, models: { ...c.models, [modelKey]: !next } } : c));
      }
    } catch {
      flash('Could not reach the server');
      setCameras(prev => prev.map(c => c.id === cam.id ? { ...c, models: { ...c.models, [modelKey]: !next } } : c));
    } finally {
      setCameraCellBusy(null);
    }
  };

  const fetchModels = async () => {
    try {
      const res = await fetch(`${API_URL}/api/devteam/detection-models`, { headers: authHeaders() });
      if (res.ok) setModels((await res.json()).models || []);
    } catch { /* leave the previous list up */ }
    finally { setLoaded(true); }
  };

  const applyModelChange = async (m: DetectionModel, body: Record<string, unknown>) => {
    setModelBusy(m.name);
    try {
      const res = await fetch(`${API_URL}/api/devteam/detection-models/${m.name}`, {
        method: 'PATCH',
        headers: { ...authHeaders() },
        body: JSON.stringify(body),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) { flash(d.detail || 'Could not save'); return; }
      await fetchModels();
      setRestartPending(true);
      flash(`${m.display_name} ${body.enabled === false ? 'turned off' : 'turned on'} — restart detection to apply`);
    } catch {
      flash('Could not reach the server');
    } finally {
      setModelBusy(null);
    }
  };

  // Same rule as the DevTeam panel: enabling a model whose own measurements
  // missed the bar for deployment gets a confirmation step first. Turning
  // one off never does -- that can only reduce output.
  const requestToggle = (m: DetectionModel) => {
    if (!m.enabled && (m.experimental || m.metrics?.status === 'disabled')) {
      setConfirmEnable(m);
      return;
    }
    applyModelChange(m, { enabled: !m.enabled });
  };

  const fetchOptimizeStatus = async () => {
    try {
      const res = await fetch(`${API_URL}/api/devteam/optimize_weights/status`, { headers: authHeaders() });
      if (res.ok) setOptimizeState(await res.json());
    } catch { /* leave the previous panel up */ }
  };

  React.useEffect(() => { fetchModels(); fetchOptimizeStatus(); fetchCameraModels(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useLiveChannel("camera_models", fetchCameraModels);
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
                  <button
                    onClick={() => requestToggle(m)}
                    disabled={modelBusy === m.name || (!m.enabled && !m.weights_present)}
                    title={!m.weights_present ? 'Model file is missing' : (m.enabled ? 'Turn off' : 'Turn on')}
                    className="text-[8px] font-bold uppercase px-1 py-0.5 border shrink-0 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                    style={{
                      color: m.enabled ? 'var(--ok)' : 'var(--text-3)',
                      borderColor: m.enabled ? 'var(--ok)' : 'var(--line-2)',
                    }}
                  >
                    {m.enabled ? 'Active' : 'Off'}
                  </button>
                </div>
                {m.metrics?.headline ? (
                  <div className="data text-base font-bold leading-none" style={{ color: 'var(--text)' }}>
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
          Click Active / Off to switch a model. Thresholds stay fixed — those are set to the
          accuracy each model was measured at, not something to hand-tune here.
        </p>
        {restartPending && (
          <p className="text-[9.5px] leading-relaxed mt-2 pt-2 border-t" style={{ color: 'var(--warn)', borderColor: 'var(--line)' }}>
            <span style={{ fontWeight: 700 }}>Restart required.</span> Detection reads this
            once at startup — your change is saved but won't take effect until it restarts.
          </p>
        )}
      </div>

      {/* Per-camera detector overrides -- narrows the global switch above for
          one specific camera this barangay owns. Never widens it: a model
          that's Off globally can't be turned on here (see globallyOff). */}
      <div className="border p-3" style={{ background: 'var(--panel)', borderColor: 'var(--line)' }}>
        <div className="flex items-center gap-2 mb-1">
          <Gauge size={14} style={{ color: 'var(--text-3)' }} />
          <span className="label">Models per camera</span>
        </div>
        <p className="text-[9.5px] leading-relaxed mb-3" style={{ color: 'var(--text-2)' }}>
          Choose which detectors run on each of your cameras -- e.g. a market-stall camera can
          run vandalism only, while a corridor camera runs violence only.
        </p>
        {!camerasLoaded ? (
          <p className="text-[10.5px]" style={{ color: 'var(--text-2)' }}>Loading…</p>
        ) : cameras.length === 0 ? (
          <p className="text-[10.5px]" style={{ color: 'var(--text-2)' }}>No cameras registered yet.</p>
        ) : (
          <div className="overflow-x-auto custom-scrollbar">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr>
                  <th className="text-[9px] uppercase tracking-wide font-normal pb-2 pr-3" style={{ color: 'var(--text-3)' }}>
                    Camera
                  </th>
                  {CAMERA_MODEL_KEYS.map(({ key, label }) => (
                    <th
                      key={key}
                      className="text-[9px] uppercase tracking-wide font-normal pb-2 px-2 text-center"
                      style={{ color: globallyOff(key) ? 'var(--text-3)' : 'var(--text-2)' }}
                      title={globallyOff(key) ? `${label} is off system-wide` : undefined}
                    >
                      {label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {cameras.map(cam => (
                  <tr key={cam.id} className="border-t" style={{ borderColor: 'var(--line)' }}>
                    <td className="text-[10.5px] py-2 pr-3 truncate max-w-[160px]" style={{ color: 'var(--text)' }}>
                      {cam.name}
                    </td>
                    {CAMERA_MODEL_KEYS.map(({ key }) => {
                      const on = cam.models[key] !== false;
                      const cellId = `${cam.id}:${key}`;
                      const disabled = cameraCellBusy === cellId || (!on && globallyOff(key));
                      return (
                        <td key={key} className="text-center px-2 py-2">
                          <button
                            onClick={() => toggleCameraModel(cam, key)}
                            disabled={disabled}
                            title={
                              globallyOff(key)
                                ? `${key} is off system-wide -- enable it globally first`
                                : on ? 'Running on this camera' : 'Not running on this camera'
                            }
                            className="w-8 h-4 relative border transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                            style={{
                              borderColor: on ? 'var(--ok)' : 'var(--line-2)',
                              background: on ? 'var(--ok-dim)' : 'transparent',
                              borderRadius: 'var(--radius-sm)',
                            }}
                          >
                            <span
                              className="absolute top-0.5 h-2.5 w-2.5 transition-all"
                              style={{
                                left: on ? '18px' : '2px',
                                background: on ? 'var(--ok)' : 'var(--text-3)',
                                borderRadius: 'var(--radius-sm)',
                              }}
                            />
                          </button>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
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
                    <span style={{ color: 'var(--text)' }}> ({s.speedup?.toFixed(2)}x)</span>
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

      {/* CONFIRM ENABLING A MODEL THAT MEASURED BADLY -- same gate as the
          DevTeam panel, since this view can now flip the same switch. */}
      {confirmEnable && (
        <div className="fixed inset-0 z-[130] flex items-center justify-center p-4" style={{ background: 'color-mix(in srgb, var(--bg) 85%, transparent)' }}>
          <div className="border w-full max-w-md p-6" style={{ background: 'var(--panel)', borderColor: 'var(--warn)' }}>
            <div className="flex items-center gap-2 mb-4 pb-3 border-b" style={{ borderColor: 'var(--panel-2)' }}>
              <AlertTriangle size={14} style={{ color: 'var(--warn)' }} />
              <span className="text-[10px] tracking-[0.15em] uppercase" style={{ color: 'var(--text)' }}>
                Turn on {confirmEnable.display_name}?
              </span>
            </div>
            <p className="text-[10.5px] leading-relaxed mb-3" style={{ color: 'var(--text-2)' }}>
              This model did not meet the bar for deployment. Its own measurements:
            </p>
            <div className="border divide-y mb-4" style={{ borderColor: 'var(--line)' }}>
              {confirmEnable.metrics?.stats?.map(s => (
                <div key={s.label} className="flex items-baseline justify-between px-3 py-2">
                  <span className="text-[9.5px]" style={{ color: 'var(--text-2)' }}>{s.label}</span>
                  <span className="text-[11px]" style={{ color: s.good === false ? 'var(--warn)' : 'var(--text)' }}>
                    {s.value}{s.unit}
                  </span>
                </div>
              ))}
            </div>
            {confirmEnable.metrics?.caveat && (
              <p className="text-[9.5px] leading-relaxed mb-5" style={{ color: 'var(--text-2)' }}>
                {confirmEnable.metrics.caveat}
              </p>
            )}
            <div className="flex gap-2">
              <button
                onClick={() => setConfirmEnable(null)}
                className="flex-1 py-2.5 text-[10px] tracking-[0.12em] uppercase border"
                style={{ borderColor: 'var(--line-2)', color: 'var(--text)' }}
              >
                Keep it off
              </button>
              <button
                onClick={() => { const m = confirmEnable; setConfirmEnable(null); applyModelChange(m, { enabled: true }); }}
                className="flex-1 py-2.5 text-[10px] tracking-[0.12em] uppercase border"
                style={{ borderColor: 'var(--warn)', color: 'var(--warn)' }}
              >
                Turn it on anyway
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
