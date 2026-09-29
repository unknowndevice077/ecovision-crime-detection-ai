"use client";

import { useState } from 'react';
import { AlertTriangle, Info, Save } from 'lucide-react';
import PermissionTree, { ResourceScopes, narrowedOnly, scopeProblem, scopesForSave } from './PermissionTree';
import { FieldInput, authHeaders } from './shared';
import { PERMISSION_KEYS } from '../../../lib/permissions';

type Warning = { level: 'danger' | 'note'; text: string };

// What an admin account gets automatically (backend require_permission's
// admin bypass): everything except the side-restricted keys.
const ADMIN_AUTOMATIC = ['view_map', 'confirm_dismiss_alerts', 'manage_notify_targets'];

export function roleWarnings(perms: Record<string, boolean>, scopes: ResourceScopes = {}): Warning[] {
  const on = (k: string) => !!perms[k];
  const limited = (k: string) => !!scopes[k]?.crime_type;
  const granted = PERMISSION_KEYS.filter(p => on(p.key)).map(p => p.key);
  const out: Warning[] = [];
  if (ADMIN_AUTOMATIC.every(on)) {
    out.push({ level: 'danger', text: 'Includes everything an admin gets automatically (crime map, alert decisions, responder notifications) -- this role is effectively an admin. Create an admin account instead if that is the intent.' });
  } else if (granted.length >= 4) {
    out.push({ level: 'danger', text: `Grants ${granted.length} of ${PERMISSION_KEYS.length} permissions -- close to a full admin account.` });
  }
  if (on('view_map') && on('view_history') && !(limited('view_map') && limited('view_history'))) {
    out.push({ level: 'danger', text: 'Crime map + crime history together expose the full incident record of the whole jurisdiction -- admin-level visibility. Limit both to specific crime types to narrow it.' });
  }
  if (on('manage_notify_targets')) {
    out.push({ level: 'danger', text: 'Can change who receives SMS/Telegram incident alerts -- a misconfigured target silences a responder.' });
  }
  if (on('manage_cameras')) {
    out.push({ level: 'note', text: 'Camera control (PTZ, detection models, thresholds) is barangay-only -- dropped when this role is given to a police officer.' });
  }
  if (on('view_history') || on('view_records')) {
    out.push({ level: 'note', text: 'Crime history / video records are police-only -- dropped when this role is given to barangay staff.' });
  }
  return out;
}

export function RoleWarnings({ perms, scopes }: { perms: Record<string, boolean>; scopes?: ResourceScopes }) {
  const warnings = roleWarnings(perms, scopes);
  if (!warnings.length) return null;
  return (
    <div className="space-y-1.5">
      {warnings.map(w => (
        <div
          key={w.text}
          className={`flex items-start gap-2 px-3 py-2 border text-[9.5px] leading-relaxed ${w.level === 'danger'
            ? 'border-[var(--warn)]/40 bg-[var(--warn)]/[0.06] text-[var(--warn)]'
            : 'border-[var(--line)] text-[var(--text-2)]'}`}
        >
          {w.level === 'danger' ? <AlertTriangle size={11} className="shrink-0 mt-0.5" /> : <Info size={11} className="shrink-0 mt-0.5" />}
          <span>{w.text}</span>
        </div>
      ))}
    </div>
  );
}

export default function RoleEditor({ apiUrl, onCreated, flash }: {
  apiUrl: string;
  onCreated: (id: string, name: string) => void;
  flash: (msg: string) => void;
}) {
  const [name, setName] = useState('');
  const [perms, setPerms] = useState<Record<string, boolean>>({});
  const [scopes, setScopes] = useState<ResourceScopes>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const create = async () => {
    const trimmed = name.trim();
    if (!trimmed) { setError('Give the role a name.'); return; }
    if (!Object.values(perms).some(Boolean)) { setError('Pick at least one permission.'); return; }
    const problem = scopeProblem(null, perms, scopes);
    if (problem) { setError(problem); return; }
    setBusy(true);
    setError('');
    try {
      const res = await fetch(`${apiUrl}/api/devteam/custom_roles`, {
        method: 'POST', headers: authHeaders(),
        body: JSON.stringify({ name: trimmed, permissions: perms, scopes: narrowedOnly(scopesForSave(null, perms, scopes)) }),
      });
      const d = await res.json().catch(() => ({}));
      if (res.ok) {
        flash(`Role "${trimmed}" created.`);
        setName('');
        setPerms({});
        setScopes({});
        onCreated(d.id, trimmed);
      } else setError(d.detail || 'Could not create role.');
    } catch {
      setError('Backend connection failure.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      <FieldInput label="Role name" value={name} onChange={setName} placeholder="e.g. Gate Monitor" />
      <div>
        <div className="text-[8px] tracking-[0.15em] uppercase text-[var(--text-2)] mb-2">Permissions this role pre-applies</div>
        <PermissionTree role={null} perms={perms} onPermsChange={setPerms} scopes={scopes} onScopesChange={setScopes} />
        <p className="text-[9px] leading-relaxed text-[var(--text-3)] mt-2">
          Works for barangay staff and police officers alike. Anything an account&apos;s side can&apos;t hold is dropped when the role is assigned,
          crime-type and channel limits carry over, and camera limits are set per account in Create User or Manage Users.
        </p>
      </div>
      <RoleWarnings perms={perms} scopes={scopes} />
      {error && <p className="text-[10px] text-[var(--critical)] uppercase tracking-wide">{error}</p>}
      <button
        onClick={create}
        disabled={busy || !name.trim()}
        className="w-full py-2.5 bg-[var(--accent)] text-[#fff] text-[10px] font-bold tracking-[0.15em] uppercase disabled:opacity-40 transition-opacity hover:opacity-90 flex items-center justify-center gap-2"
      >
        <Save size={12} /> {busy ? 'Saving…' : 'Create role'}
      </button>
    </div>
  );
}
