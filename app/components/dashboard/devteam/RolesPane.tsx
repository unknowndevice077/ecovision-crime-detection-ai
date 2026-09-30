"use client";

import { useEffect, useState } from 'react';
import { AlertTriangle, KeyRound, Plus, Trash2 } from 'lucide-react';
import RoleEditor, { roleWarnings } from './RoleEditor';
import { CustomRole, EmptyPane, ManagedUser, PaneHeader, authHeaders } from './shared';
import { PERMISSION_KEYS, dimensionOptions, ResourceDimension } from '../../../lib/permissions';
import { ResourceScopes } from './PermissionTree';

export default function RolesPane({ apiUrl, customRoles, users, fetchCustomRoles, flash }: {
  apiUrl: string; customRoles: CustomRole[]; users: ManagedUser[];
  fetchCustomRoles: () => void; flash: (m: string) => void;
}) {
  const [busyId, setBusyId] = useState<string | null>(null);

  useEffect(() => { fetchCustomRoles(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const deleteRole = async (r: CustomRole) => {
    if (!window.confirm(`Delete the role "${r.name}"?

It stops being offered in Create User. (A role still assigned to accounts can't be deleted.)`)) return;
    setBusyId(r.id);
    try {
      const res = await fetch(`${apiUrl}/api/devteam/custom_roles/${r.id}`, { method: 'DELETE', headers: authHeaders() });
      const d = await res.json().catch(() => ({}));
      if (res.ok) { flash(`Role "${r.name}" deleted.`); fetchCustomRoles(); }
      else flash(d.detail || 'Could not delete role.');
    } catch {
      flash('Backend connection failure.');
    } finally {
      setBusyId(null);
    }
  };

  const label = (key: string) => PERMISSION_KEYS.find(p => p.key === key)?.label || key;

  return (
    <div className="flex-1 min-h-0 grid grid-cols-1 md:grid-cols-2 gap-6 px-7 pb-7 pt-4">
      <div className="min-h-0 flex flex-col border border-[var(--line)]">
        <PaneHeader icon={<KeyRound size={12} />} title="Custom roles" right={<span className="text-[9px] text-[var(--text-3)]">{customRoles.length} role{customRoles.length === 1 ? '' : 's'}</span>} />
        <div className="flex-1 min-h-0 overflow-y-auto custom-scrollbar">
          {customRoles.length === 0 ? (
            <EmptyPane text="No custom roles yet" sub="A role is a named permission preset for staff and officers -- create one on the right." />
          ) : (
            <div className="divide-y divide-[var(--panel-2)]">
              {customRoles.map(r => {
                const keys = (r.permission_defaults || []).filter(d => !d.resource_type).map(d => d.permission_key);
                const perms = Object.fromEntries(keys.map(k => [k, true]));
                const scopes: ResourceScopes = {};
                (r.permission_defaults || []).forEach(d => {
                  if (!d.resource_type || !d.resource_id) return;
                  const dims = (scopes[d.permission_key] ||= {});
                  const dim = d.resource_type as ResourceDimension;
                  dims[dim] = [...(dims[dim] || []), d.resource_id];
                });
                const limits = (k: string) => Object.entries(scopes[k] || {}).map(([dim, ids]) => {
                  const opts = dimensionOptions(k, dim as ResourceDimension);
                  return (ids || []).map(id => opts.find(o => o.id === id)?.label || id).join(', ');
                }).filter(Boolean).join(' · ');
                const risky = roleWarnings(perms, scopes).filter(w => w.level === 'danger');
                const assigned = users.filter(u => u.custom_role_id === r.id).length;
                return (
                  <div key={r.id} className={`px-4 py-3 transition-opacity ${busyId === r.id ? 'opacity-40' : ''}`}>
                    <div className="flex items-center gap-2">
                      <p className="text-[11px] text-[var(--text)] truncate flex-1">{r.name}</p>
                      {r.org_type && <span className="text-[8px] uppercase tracking-wide text-[var(--text-3)]">{r.org_type} only</span>}
                      <span className="text-[9px] text-[var(--text-3)]">{assigned} account{assigned === 1 ? '' : 's'}</span>
                      <button
                        onClick={() => deleteRole(r)}
                        disabled={!!busyId || assigned > 0}
                        title={assigned > 0 ? 'Accounts still use this role' : 'Delete role'}
                        className="p-1 text-[var(--text-2)] hover:text-[var(--critical)] disabled:opacity-25 disabled:cursor-not-allowed"
                      >
                        <Trash2 size={12} />
                      </button>
                    </div>
                    <div className="flex flex-wrap gap-1 mt-1.5">
                      {keys.length === 0
                        ? <span className="text-[9px] text-[var(--text-3)]">no permissions</span>
                        : keys.map(k => (
                          <span key={k} className="text-[8.5px] px-1.5 py-0.5 border border-[var(--line-2)] text-[var(--text-2)]">
                            {label(k)}{limits(k) && <span className="text-[var(--accent)]"> · {limits(k)}</span>}
                          </span>
                        ))}
                    </div>
                    {risky.map(w => (
                      <p key={w.text} className="flex items-start gap-1.5 text-[9px] leading-relaxed text-[var(--warn)] mt-1.5">
                        <AlertTriangle size={10} className="shrink-0 mt-0.5" /> {w.text}
                      </p>
                    ))}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      <div className="min-h-0 flex flex-col border border-[var(--line)]">
        <PaneHeader icon={<Plus size={12} />} title="New role" />
        <div className="flex-1 min-h-0 overflow-y-auto custom-scrollbar p-5">
          <RoleEditor apiUrl={apiUrl} flash={flash} onCreated={() => fetchCustomRoles()} />
        </div>
      </div>
    </div>
  );
}
