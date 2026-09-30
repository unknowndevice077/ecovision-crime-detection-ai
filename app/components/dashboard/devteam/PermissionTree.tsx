"use client";

import { useState } from 'react';
import { AlertTriangle, ChevronRight, Radio, ShieldAlert, Video, X } from 'lucide-react';
import {
  DIMENSION_LABELS, PERMISSION_KEYS, RESOURCE_DIMENSIONS, ResourceDimension, dimensionOptions, permissionStatus,
} from '../../../lib/permissions';
import { ADMIN_ROLES, CameraRow } from './shared';

// {permission_key: {dimension: ids | null}} -- mirrors backend.py's
// RESOURCE_DIMENSIONS dicing. null / missing = everything on that dimension.
export type ResourceScopes = Record<string, Partial<Record<ResourceDimension, string[] | null>>>;

const PERMISSION_HINTS: Record<string, string> = {
  view_map: 'Live camera feeds and the incident map',
  view_records: 'Recorded clips in the video vault',
  view_history: 'The full crime history archive',
  manage_cameras: 'Add/remove cameras, PTZ, detection models and thresholds',
  confirm_dismiss_alerts: 'Confirm or dismiss AI alerts',
  manage_notify_targets: 'Who receives SMS/Telegram incident alerts',
};

const DIM_ICONS: Record<ResourceDimension, React.ReactNode> = {
  camera: <Video size={10} />,
  crime_type: <ShieldAlert size={10} />,
  channel: <Radio size={10} />,
};

type Props = {
  // null = role-template mode: no account yet, so nothing is locked or
  // automatic and there are no cameras to dice (crime types and channels
  // still can be).
  role: string | null;
  customPermissions?: boolean;
  perms: Record<string, boolean>;
  onPermsChange: (next: Record<string, boolean>) => void;
  scopes?: ResourceScopes;
  onScopesChange?: (next: ResourceScopes) => void;
  cameras?: CameraRow[];
  cameraHint?: string;
  subject?: string;
  disabled?: boolean;
};

export function effectiveGranted(role: string | null, key: string, perms: Record<string, boolean>, customPermissions = false) {
  if (!role) return !!perms[key];
  const status = permissionStatus(role, key, customPermissions);
  if (status === 'always') return true;
  if (status === 'banned') return false;
  return !!perms[key];
}

// Dimensions shown for a key: cameras only exist once there's an account
// (and so a jurisdiction) to pick them from.
export function dimensionsFor(key: string, role: string | null): ResourceDimension[] {
  return (RESOURCE_DIMENSIONS[key] || []).filter(d => role !== null || d !== 'camera');
}

// Scopes to send to the backend: every dimension of every key the account
// can hold, a list only where it's granted and narrowed, null elsewhere.
export function scopesForSave(role: string | null, perms: Record<string, boolean>, scopes: ResourceScopes, customPermissions = false): ResourceScopes {
  const out: ResourceScopes = {};
  for (const key of Object.keys(RESOURCE_DIMENSIONS)) {
    if (role && permissionStatus(role, key, customPermissions) === 'banned') continue;
    const granted = effectiveGranted(role, key, perms, customPermissions);
    out[key] = {};
    for (const dim of dimensionsFor(key, role)) {
      out[key][dim] = granted ? (scopes[key]?.[dim] ?? null) : null;
    }
  }
  return out;
}

// Only the narrowed entries -- for payloads where "absent" means "all".
export function narrowedOnly(scopes: ResourceScopes): ResourceScopes {
  const out: ResourceScopes = {};
  for (const [key, dims] of Object.entries(scopes)) {
    for (const [dim, ids] of Object.entries(dims)) {
      if (ids) (out[key] ||= {})[dim as ResourceDimension] = ids;
    }
  }
  return out;
}

export function scopeProblem(role: string | null, perms: Record<string, boolean>, scopes: ResourceScopes, customPermissions = false): string | null {
  for (const key of Object.keys(RESOURCE_DIMENSIONS)) {
    if (!effectiveGranted(role, key, perms, customPermissions)) continue;
    for (const dim of dimensionsFor(key, role)) {
      const s = scopes[key]?.[dim];
      if (s && s.length === 0) {
        const label = PERMISSION_KEYS.find(p => p.key === key)?.label || key;
        return `${label}: pick at least one ${DIMENSION_LABELS[dim].noun}, or allow all ${DIMENSION_LABELS[dim].plural}.`;
      }
    }
  }
  return null;
}

export default function PermissionTree({
  role, customPermissions = false, perms, onPermsChange, scopes = {}, onScopesChange,
  cameras = [], cameraHint, subject = 'This account', disabled,
}: Props) {
  const [confirmKey, setConfirmKey] = useState<string | null>(null);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const isAdmin = !!role && ADMIN_ROLES.includes(role);
  const diceable = !!onScopesChange;

  const optionsFor = (key: string, dim: ResourceDimension) =>
    dim === 'camera' ? cameras.map(c => ({ id: c.id, label: c.name, sub: c.barangay_id })) : dimensionOptions(key, dim).map(o => ({ ...o, sub: '' }));

  const setScope = (key: string, dim: ResourceDimension, value: string[] | null) =>
    onScopesChange?.({ ...scopes, [key]: { ...(scopes[key] || {}), [dim]: value } });

  const clearKey = (key: string) => {
    if (!scopes[key]) return;
    const next = { ...scopes };
    delete next[key];
    onScopesChange?.(next);
  };

  const toggleParent = (key: string, next: boolean) => {
    if (!next) {
      onPermsChange({ ...perms, [key]: false });
      if (diceable) clearKey(key);
      return;
    }
    // Turning on a camera permission for a non-admin: make the "every
    // camera" consequence explicit instead of silently granting it.
    if (diceable && dimensionsFor(key, role).includes('camera') && !isAdmin && cameras.length > 0) {
      setConfirmKey(key);
      return;
    }
    onPermsChange({ ...perms, [key]: true });
    if (diceable && dimensionsFor(key, role).length) setOpen(o => ({ ...o, [key]: true }));
  };

  const toggleOption = (key: string, dim: ResourceDimension, id: string) => {
    const allIds = optionsFor(key, dim).map(o => o.id);
    const current = scopes[key]?.[dim] ?? allIds;
    const next = current.includes(id) ? current.filter(x => x !== id) : [...current, id];
    setScope(key, dim, next.length === allIds.length && allIds.every(x => next.includes(x)) ? null : next);
  };

  const summary = (key: string, dim: ResourceDimension) => {
    const total = optionsFor(key, dim).length;
    const s = scopes[key]?.[dim] ?? null;
    if (dim === 'camera' && total === 0) return 'no cameras';
    return s === null ? `all ${DIMENSION_LABELS[dim].plural}` : `${s.length} of ${total}`;
  };

  const confirmLabel = confirmKey ? PERMISSION_KEYS.find(p => p.key === confirmKey)?.label : '';

  return (
    <div className="border border-[var(--panel-2)] divide-y divide-[var(--panel-2)]">
      {PERMISSION_KEYS.map(p => {
        const status = role ? permissionStatus(role, p.key, customPermissions) : 'editable';
        if (status === 'banned') return null;
        const granted = effectiveGranted(role, p.key, perms, customPermissions);
        const editable = status === 'editable' && !disabled;
        const dims = diceable && granted ? dimensionsFor(p.key, role) : [];
        const expanded = dims.length > 0 && !!open[p.key];
        const narrowed = dims.some(d => (scopes[p.key]?.[d] ?? null) !== null);
        return (
          <div key={p.key}>
            <label
              className={`flex items-center gap-2.5 px-3 py-2.5 transition-colors ${editable ? 'cursor-pointer hover:bg-[var(--panel)]' : 'cursor-not-allowed'}`}
              title={status === 'always' ? 'Admin-tier accounts get this automatically.' : undefined}
            >
              <input
                type="checkbox"
                checked={granted}
                disabled={!editable}
                onChange={e => toggleParent(p.key, e.target.checked)}
                className="w-3.5 h-3.5 accent-[var(--accent)] disabled:cursor-not-allowed shrink-0"
              />
              <div className="min-w-0 flex-1">
                <p className="text-[10.5px] text-[var(--text)]">
                  {p.label}
                  {status === 'always' && <span className="ml-1.5 text-[8px] uppercase tracking-wide text-[var(--ok)]">automatic</span>}
                  {narrowed && <span className="ml-1.5 text-[8px] uppercase tracking-wide text-[var(--accent)]">limited</span>}
                </p>
                <p className="text-[9px] text-[var(--text-3)] truncate">{PERMISSION_HINTS[p.key]}</p>
              </div>
              {dims.length > 0 && (
                <button
                  type="button"
                  onClick={e => { e.preventDefault(); setOpen(o => ({ ...o, [p.key]: !o[p.key] })); }}
                  className="flex items-center gap-2 text-[9px] tracking-wide uppercase text-[var(--accent)] shrink-0"
                  title="Choose exactly what this permission covers"
                >
                  {dims.map(d => (
                    <span key={d} className="flex items-center gap-1">{DIM_ICONS[d]}{summary(p.key, d)}</span>
                  ))}
                  <ChevronRight size={10} className={`transition-transform ${expanded ? 'rotate-90' : ''}`} />
                </button>
              )}
            </label>

            {expanded && (
              <div className="pl-9 pr-3 pb-3 space-y-2.5 bg-[var(--bg)]/40">
                {dims.map(dim => {
                  const options = optionsFor(p.key, dim);
                  const scope = scopes[p.key]?.[dim] ?? null;
                  const grid = dim !== 'camera';
                  return (
                    <div key={dim}>
                      <div className="flex items-center justify-between py-1.5">
                        <span className="flex items-center gap-1.5 text-[8px] tracking-[0.15em] uppercase text-[var(--text-3)]">
                          {DIM_ICONS[dim]} {DIMENSION_LABELS[dim].title}
                        </span>
                        {!disabled && options.length > 0 && (
                          scope !== null ? (
                            <button type="button" onClick={() => setScope(p.key, dim, null)} className="text-[9px] uppercase tracking-wide text-[var(--accent)] hover:opacity-80">
                              Allow all
                            </button>
                          ) : (
                            <button type="button" onClick={() => setScope(p.key, dim, [])} className="text-[9px] uppercase tracking-wide text-[var(--text-2)] hover:text-[var(--text)]">
                              Clear all
                            </button>
                          )
                        )}
                      </div>
                      {options.length === 0 ? (
                        <p className="text-[9px] text-[var(--text-3)] py-1">{cameraHint || 'No cameras in this jurisdiction yet.'}</p>
                      ) : (
                        <div className={grid
                          ? 'grid grid-cols-2 gap-px bg-[var(--panel-2)] border border-[var(--panel-2)]'
                          : 'border border-[var(--panel-2)] divide-y divide-[var(--panel-2)] max-h-44 overflow-y-auto custom-scrollbar'}>
                          {options.map(o => (
                            <label key={o.id} className={`flex items-center gap-2.5 px-2.5 py-1.5 bg-[var(--panel)] ${disabled ? '' : 'cursor-pointer hover:bg-[var(--panel-2)]'}`}>
                              <input
                                type="checkbox"
                                checked={scope === null || scope.includes(o.id)}
                                disabled={disabled}
                                onChange={() => toggleOption(p.key, dim, o.id)}
                                className="w-3 h-3 accent-[var(--accent)] shrink-0"
                              />
                              <span className="text-[10px] text-[var(--text)] truncate">{o.label}</span>
                              {o.sub && <span className="text-[9px] text-[var(--text-3)] ml-auto shrink-0">{o.sub}</span>}
                            </label>
                          ))}
                        </div>
                      )}
                      {scope !== null && scope.length === 0 && (
                        <p className="flex items-center gap-1.5 text-[9px] text-[var(--warn)] mt-1.5">
                          <AlertTriangle size={10} /> Nothing selected -- pick at least one {DIMENSION_LABELS[dim].noun}, or uncheck {p.label}.
                        </p>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}

      {confirmKey && (
        <div className="fixed inset-0 z-[140] flex items-center justify-center p-4 bg-[var(--bg)]/85">
          <div className="bg-[var(--panel)] border border-[var(--warn)]/40 w-full max-w-md font-mono">
            <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--panel-2)]">
              <span className="flex items-center gap-2 text-[10px] tracking-[0.15em] uppercase text-[var(--warn)]">
                <AlertTriangle size={13} /> Access to every camera
              </span>
              <button onClick={() => setConfirmKey(null)}><X size={14} className="text-[var(--text-2)] hover:text-[var(--text)]" /></button>
            </div>
            <div className="p-4 space-y-3">
              <p className="text-[11px] leading-relaxed text-[var(--text)]">
                {subject} is not an admin account. Turning on <span className="text-[var(--accent)]">{confirmLabel}</span> with
                no camera limit gives them access to <span className="text-[var(--warn)]">all {cameras.length} camera{cameras.length === 1 ? '' : 's'}</span> in
                their jurisdiction{cameras.length > 0 ? ` (${cameras.slice(0, 3).map(c => c.name).join(', ')}${cameras.length > 3 ? ', …' : ''})` : ''}.
              </p>
              <div className="flex flex-col gap-2 pt-1">
                <button
                  onClick={() => { onPermsChange({ ...perms, [confirmKey]: true }); setScope(confirmKey, 'camera', null); setOpen(o => ({ ...o, [confirmKey]: true })); setConfirmKey(null); }}
                  className="w-full py-2.5 border border-[var(--warn)]/50 text-[var(--warn)] text-[10px] tracking-[0.15em] uppercase hover:bg-[var(--warn)]/10 transition-colors"
                >
                  Give access to all cameras
                </button>
                <button
                  onClick={() => { onPermsChange({ ...perms, [confirmKey]: true }); setScope(confirmKey, 'camera', []); setOpen(o => ({ ...o, [confirmKey]: true })); setConfirmKey(null); }}
                  className="w-full py-2.5 bg-[var(--accent)] text-[#fff] text-[10px] tracking-[0.15em] uppercase hover:opacity-90 transition-opacity"
                >
                  Choose specific cameras
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
