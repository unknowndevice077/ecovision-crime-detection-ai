"use client";

import { useState } from 'react';
import { AlertTriangle, ChevronRight, Video, X } from 'lucide-react';
import { PERMISSION_KEYS, permissionStatus } from '../../../lib/permissions';
import { ADMIN_ROLES, CameraRow } from './shared';

// Permissions that can be diced down to individual cameras -- mirrors
// backend.py's CAMERA_SCOPABLE_KEYS. view_map narrows which camera feeds
// the account sees; manage_cameras which cameras it can configure.
export const CAMERA_SCOPABLE_KEYS = ['view_map', 'manage_cameras'];

// null = every camera the account's org allows; a list = only those.
export type CameraScopes = Record<string, string[] | null>;

const PERMISSION_HINTS: Record<string, string> = {
  view_map: 'Live camera feeds and the incident map',
  view_records: 'Recorded clips in the video vault',
  view_history: 'The full crime history archive',
  manage_cameras: 'Add/remove cameras, PTZ, detection models and thresholds',
  confirm_dismiss_alerts: 'Confirm or dismiss AI alerts',
  manage_notify_targets: 'Who receives SMS/Telegram incident alerts',
};

type Props = {
  // null = role-template mode: no account yet, so nothing is locked or
  // automatic and there are no cameras to dice.
  role: string | null;
  customPermissions?: boolean;
  perms: Record<string, boolean>;
  onPermsChange: (next: Record<string, boolean>) => void;
  scopes?: CameraScopes;
  onScopesChange?: (next: CameraScopes) => void;
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

// Scopes to send to the backend: a list only for granted, diced keys;
// null (clear any dicing) for everything else.
export function scopesForSave(role: string, perms: Record<string, boolean>, scopes: CameraScopes, customPermissions = false): CameraScopes {
  const out: CameraScopes = {};
  for (const key of CAMERA_SCOPABLE_KEYS) {
    if (permissionStatus(role, key, customPermissions) === 'banned') continue;
    const granted = effectiveGranted(role, key, perms, customPermissions);
    out[key] = granted ? (scopes[key] ?? null) : null;
  }
  return out;
}

export function scopeProblem(role: string, perms: Record<string, boolean>, scopes: CameraScopes, customPermissions = false): string | null {
  for (const key of CAMERA_SCOPABLE_KEYS) {
    const s = scopes[key];
    if (s && s.length === 0 && effectiveGranted(role, key, perms, customPermissions)) {
      const label = PERMISSION_KEYS.find(p => p.key === key)?.label || key;
      return `${label}: pick at least one camera, or give access to all cameras.`;
    }
  }
  return null;
}

export default function PermissionTree({
  role, customPermissions = false, perms, onPermsChange, scopes = {}, onScopesChange,
  cameras = [], cameraHint, subject = 'This account', disabled,
}: Props) {
  const [confirmKey, setConfirmKey] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const isAdmin = !!role && ADMIN_ROLES.includes(role);
  const diceable = !!role && !!onScopesChange;

  const setScope = (key: string, value: string[] | null) => onScopesChange?.({ ...scopes, [key]: value });

  const toggleParent = (key: string, next: boolean) => {
    if (!next) {
      onPermsChange({ ...perms, [key]: false });
      if (diceable) setScope(key, null);
      return;
    }
    // Turning on a camera permission for a non-admin: make the "every
    // camera" consequence explicit instead of silently granting it.
    if (diceable && CAMERA_SCOPABLE_KEYS.includes(key) && !isAdmin && cameras.length > 0) {
      setConfirmKey(key);
      return;
    }
    onPermsChange({ ...perms, [key]: true });
  };

  const toggleCamera = (key: string, camId: string) => {
    const allIds = cameras.map(c => c.id);
    const current = scopes[key] ?? allIds;
    const next = current.includes(camId) ? current.filter(id => id !== camId) : [...current, camId];
    setScope(key, next.length === allIds.length && allIds.every(id => next.includes(id)) ? null : next);
  };

  const confirmLabel = confirmKey ? PERMISSION_KEYS.find(p => p.key === confirmKey)?.label : '';

  return (
    <div className="border border-[var(--panel-2)] divide-y divide-[var(--panel-2)]">
      {PERMISSION_KEYS.map(p => {
        const status = role ? permissionStatus(role, p.key, customPermissions) : 'editable';
        const granted = effectiveGranted(role, p.key, perms, customPermissions);
        const editable = status === 'editable' && !disabled;
        const showCameras = diceable && CAMERA_SCOPABLE_KEYS.includes(p.key) && granted;
        const scope = scopes[p.key] ?? null;
        const open = showCameras && !collapsed[p.key];
        return (
          <div key={p.key}>
            <label
              className={`flex items-center gap-2.5 px-3 py-2.5 transition-colors ${editable ? 'cursor-pointer hover:bg-[var(--panel)]' : 'cursor-not-allowed'} ${status === 'banned' ? 'opacity-40' : ''}`}
              title={status === 'banned' ? `Not available to this account's side -- the backend refuses it regardless.` : status === 'always' ? 'Admin-tier accounts get this automatically.' : undefined}
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
                  {status === 'banned' && <span className="ml-1.5 text-[8px] uppercase tracking-wide text-[var(--critical)]">not for this side</span>}
                  {status === 'always' && <span className="ml-1.5 text-[8px] uppercase tracking-wide text-[var(--ok)]">automatic</span>}
                </p>
                <p className="text-[9px] text-[var(--text-3)] truncate">{PERMISSION_HINTS[p.key]}</p>
              </div>
              {showCameras && (
                <button
                  type="button"
                  onClick={e => { e.preventDefault(); setCollapsed(c => ({ ...c, [p.key]: !c[p.key] })); }}
                  className="flex items-center gap-1 text-[9px] tracking-wide uppercase text-[var(--accent)] shrink-0"
                >
                  <Video size={10} />
                  {cameras.length === 0 ? 'no cameras' : scope === null ? `all ${cameras.length}` : `${scope.length} of ${cameras.length}`}
                  <ChevronRight size={10} className={`transition-transform ${open ? 'rotate-90' : ''}`} />
                </button>
              )}
            </label>

            {open && (
              <div className="pl-9 pr-3 pb-2.5 bg-[var(--bg)]/40">
                {cameras.length === 0 ? (
                  <p className="text-[9px] text-[var(--text-3)] py-2">{cameraHint || 'No cameras in this jurisdiction yet.'}</p>
                ) : (
                  <>
                    <div className="flex items-center justify-between py-1.5">
                      <span className="text-[8px] tracking-[0.15em] uppercase text-[var(--text-3)]">Cameras</span>
                      {scope !== null && !disabled && (
                        <button type="button" onClick={() => setScope(p.key, null)} className="text-[9px] uppercase tracking-wide text-[var(--accent)] hover:opacity-80">
                          All cameras
                        </button>
                      )}
                    </div>
                    <div className="border border-[var(--panel-2)] divide-y divide-[var(--panel-2)] max-h-44 overflow-y-auto custom-scrollbar">
                      {cameras.map(c => (
                        <label key={c.id} className={`flex items-center gap-2.5 px-2.5 py-1.5 ${disabled ? '' : 'cursor-pointer hover:bg-[var(--panel)]'}`}>
                          <input
                            type="checkbox"
                            checked={scope === null || scope.includes(c.id)}
                            disabled={disabled}
                            onChange={() => toggleCamera(p.key, c.id)}
                            className="w-3 h-3 accent-[var(--accent)] shrink-0"
                          />
                          <span className="text-[10px] text-[var(--text)] truncate">{c.name}</span>
                          <span className="text-[9px] text-[var(--text-3)] ml-auto shrink-0">{c.barangay_id}</span>
                        </label>
                      ))}
                    </div>
                    {scope !== null && scope.length === 0 && (
                      <p className="flex items-center gap-1.5 text-[9px] text-[var(--warn)] mt-1.5">
                        <AlertTriangle size={10} /> No cameras selected -- pick at least one, or uncheck {p.label}.
                      </p>
                    )}
                  </>
                )}
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
                  onClick={() => { onPermsChange({ ...perms, [confirmKey]: true }); setScope(confirmKey, null); setConfirmKey(null); }}
                  className="w-full py-2.5 border border-[var(--warn)]/50 text-[var(--warn)] text-[10px] tracking-[0.15em] uppercase hover:bg-[var(--warn)]/10 transition-colors"
                >
                  Give access to all cameras
                </button>
                <button
                  onClick={() => { onPermsChange({ ...perms, [confirmKey]: true }); setScope(confirmKey, []); setCollapsed(c => ({ ...c, [confirmKey]: false })); setConfirmKey(null); }}
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
