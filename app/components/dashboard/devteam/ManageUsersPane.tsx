"use client";

import { useEffect, useMemo, useState } from 'react';
import { IdCard, KeyRound, Pencil, Save, Search, ShieldCheck, ShieldX, Trash2, User, Users2, X } from 'lucide-react';
import PermissionTree, { CameraScopes, scopeProblem, scopesForSave } from './PermissionTree';
import {
  ADMIN_ROLES, CameraRow, CustomRole, EmptyPane, FieldInput, InfoRow, ManagedUser, PNP_ROLES, PaneHeader,
  PendingLocation, SectionLabel, SelectInput, Station, ageFrom, authHeaders, camerasInScope, inputClass,
  labelClass, roleLabel, roleStyle, useAuthedObjectUrl,
} from './shared';
import { onlyEditablePermissions, permissionNoteFor, permissionRowsFor } from '../../../lib/permissions';
import { positionsForRole } from '../../../lib/positions';

type Props = {
  apiUrl: string; users: ManagedUser[]; stations: Station[]; cameras: CameraRow[];
  allLocations: PendingLocation[]; customRoles: CustomRole[];
  flash: (m: string) => void; refresh: () => void;
  reviewVerification: (userId: number, decision: 'verified' | 'rejected') => void;
};

export function FacePhoto({ apiUrl, userId, hasPhoto, name, size = 56 }: {
  apiUrl: string; userId: number; hasPhoto?: boolean; name: string; size?: number;
}) {
  const { src } = useAuthedObjectUrl(hasPhoto ? `${apiUrl}/api/users/${userId}/face_photo` : null);
  return (
    <div
      className="shrink-0 border border-[var(--line-2)] bg-[var(--bg)] flex items-center justify-center overflow-hidden"
      style={{ width: size, height: size }}
    >
      {src
        ? <img src={src} alt={`Face photo of ${name}`} className="w-full h-full object-cover" />
        : hasPhoto ? <span className="text-[8px] text-[var(--text-3)]">loading</span>
        : <User size={size / 2.4} className="text-[var(--text-3)]" />}
    </div>
  );
}

export async function openIdentityFile(apiUrl: string, userId: number, kind: 'verification_document' | 'face_photo', flash: (m: string) => void) {
  try {
    const res = await fetch(`${apiUrl}/api/users/${userId}/${kind}`, { headers: authHeaders() });
    if (!res.ok) { flash('Nothing on file, or you are not authorized to view it.'); return; }
    window.open(URL.createObjectURL(await res.blob()), '_blank');
  } catch {
    flash('Backend connection failure.');
  }
}

// false == absent and null (all cameras) == absent, so toggling something
// back to how it was doesn't read as an unsaved change.
const normPerms = (p: Record<string, boolean>) => JSON.stringify(Object.keys(p).filter(k => p[k]).sort());
const normScopes = (s: CameraScopes) => JSON.stringify(
  Object.entries(s).filter(([, v]) => v !== null).map(([k, v]) => [k, [...(v as string[])].sort()]).sort());

const detailsFrom = (u: ManagedUser) => ({
  username: u.username, password: '', assignment: u.assignment || '', display_title: u.display_title || '',
  full_name: u.full_name || '', birthdate: u.birthdate || '', home_address: u.home_address || '',
  contact_number: u.contact_number || '', position: u.position || '',
  barangay_id: u.barangay_id || '', station_id: u.station_id || '',
});

export default function ManageUsersPane({ apiUrl, users, stations, cameras, allLocations, customRoles, flash, refresh, reviewVerification }: Props) {
  const [search, setSearch] = useState('');
  const [roleFilter, setRoleFilter] = useState('');
  const [selectedId, setSelectedId] = useState<number | null>(null);

  const orgName = (u: ManagedUser) => {
    if (u.role === 'DEVTEAM') return 'DevTeam HQ';
    if (u.station_id) return stations.find(s => s.id === u.station_id)?.name ?? u.station_id;
    if (u.barangay_id) return allLocations.find(l => l.id === u.barangay_id)?.name ?? u.barangay_id;
    return '—';
  };

  const list = useMemo(() => {
    const q = search.trim().toLowerCase();
    return [...users]
      .filter(u => !roleFilter || u.role === roleFilter)
      .filter(u => !q || [u.username, u.full_name, u.role, u.position, orgName(u)].some(v => (v || '').toLowerCase().includes(q)))
      .sort((a, b) => (a.full_name || a.username).localeCompare(b.full_name || b.username));
  }, [users, search, roleFilter, stations, allLocations]); // eslint-disable-line react-hooks/exhaustive-deps

  const selected = users.find(u => u.id === selectedId) || null;

  return (
    <div className="flex-1 min-h-0 grid grid-cols-1 md:grid-cols-2 gap-6 px-7 pb-7 pt-4">
      <div className="min-h-0 flex flex-col border border-[var(--line)]">
        <PaneHeader icon={<Users2 size={12} />} title="Accounts" right={<span className="text-[9px] text-[var(--text-3)]">{list.length} of {users.length}</span>} />
        <div className="shrink-0 flex items-center gap-2 px-3 py-2 border-b border-[var(--line)]">
          <Search size={12} className="text-[var(--text-2)] shrink-0" />
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="search name, username, position, or location"
            className="bg-transparent text-[11px] text-[var(--text)] outline-none w-full placeholder:text-[var(--text-3)]"
          />
          <select value={roleFilter} onChange={e => setRoleFilter(e.target.value)} className="bg-[var(--bg)] border border-[var(--line)] text-[10px] text-[var(--text-2)] px-1.5 py-1 outline-none shrink-0">
            <option value="">all roles</option>
            {['PNP_ADMIN', 'PNP_OFFICER', 'BARANGAY_ADMIN', 'BARANGAY_STAFF', 'DEVTEAM'].map(r => <option key={r} value={r}>{roleLabel(r)}</option>)}
          </select>
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto custom-scrollbar divide-y divide-[var(--panel-2)]">
          {list.length === 0 ? <EmptyPane text="No matching accounts" /> : list.map(u => {
            const style = roleStyle(u.role);
            const active = u.id === selectedId;
            return (
              <button
                key={u.id}
                onClick={() => setSelectedId(u.id)}
                className={`w-full flex items-center gap-3 px-3 py-2.5 text-left transition-colors ${active ? 'bg-[var(--accent)]/[0.08]' : 'hover:bg-[var(--panel)]'}`}
              >
                <span className={`text-[8px] font-bold px-1.5 py-1 border shrink-0 ${style.border} ${style.text}`}>{style.code}</span>
                <div className="min-w-0 flex-1">
                  <p className="text-[11px] text-[var(--text)] truncate">{u.full_name || u.username}</p>
                  <p className="text-[9px] text-[var(--text-2)] truncate">@{u.username} · {u.position || roleLabel(u.role)} · {orgName(u)}</p>
                </div>
                {u.role !== 'DEVTEAM' && (
                  <span
                    className="text-[8px] uppercase tracking-wide shrink-0"
                    style={{ color: u.verification_status === 'verified' ? 'var(--ok)' : u.verification_status === 'pending' ? 'var(--warn)' : u.verification_status === 'rejected' ? 'var(--critical)' : 'var(--text-3)' }}
                  >
                    {u.verification_status || 'unverified'}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      </div>

      <div className="min-h-0 flex flex-col border border-[var(--line)]">
        {selected ? (
          <UserDetail
            key={selected.id}
            apiUrl={apiUrl} user={selected} users={users} stations={stations} cameras={cameras}
            allLocations={allLocations} customRoles={customRoles} orgName={orgName(selected)}
            flash={flash} refresh={refresh} reviewVerification={reviewVerification}
            onDeleted={() => setSelectedId(null)}
          />
        ) : (
          <>
            <PaneHeader icon={<User size={12} />} title="Account details" />
            <EmptyPane text="Select an account" sub="Their personal record, verification and every permission they hold appear here." />
          </>
        )}
      </div>
    </div>
  );
}

function UserDetail({ apiUrl, user: u, users, stations, cameras, allLocations, customRoles, orgName, flash, refresh, reviewVerification, onDeleted }: {
  apiUrl: string; user: ManagedUser; users: ManagedUser[]; stations: Station[]; cameras: CameraRow[];
  allLocations: PendingLocation[]; customRoles: CustomRole[]; orgName: string;
  flash: (m: string) => void; refresh: () => void;
  reviewVerification: (userId: number, decision: 'verified' | 'rejected') => void; onDeleted: () => void;
}) {
  const isDevteam = u.role === 'DEVTEAM';
  const isAdmin = ADMIN_ROLES.includes(u.role);
  const isPnp = PNP_ROLES.includes(u.role);
  const style = roleStyle(u.role);

  const [editing, setEditing] = useState(false);
  const [details, setDetails] = useState(() => detailsFrom(u));
  const [detailsBusy, setDetailsBusy] = useState(false);

  const serverPerms = useMemo(() => { try { return JSON.parse(u.permissions || '{}'); } catch { return {}; } }, [u.permissions]);
  const serverScopes = useMemo<CameraScopes>(() => {
    const s: CameraScopes = {};
    Object.entries(u.camera_scopes || {}).forEach(([k, ids]) => { s[k] = ids; });
    return s;
  }, [u.camera_scopes]);

  const [perms, setPerms] = useState<Record<string, boolean>>(serverPerms);
  const [scopes, setScopes] = useState<CameraScopes>(serverScopes);
  const [overrideMode, setOverrideMode] = useState(!!u.custom_permissions);
  const [overridePassword, setOverridePassword] = useState('');
  const [accessBusy, setAccessBusy] = useState(false);
  const [accessError, setAccessError] = useState('');
  const [confirmDelete, setConfirmDelete] = useState(false);

  // Re-seed from the server after a save/refresh, but never clobber edits
  // in progress.
  const accessDirty = normPerms(perms) !== normPerms(serverPerms)
    || normScopes(scopes) !== normScopes(serverScopes)
    || overrideMode !== !!u.custom_permissions;
  useEffect(() => {
    if (!accessDirty) { setPerms(serverPerms); setScopes(serverScopes); setOverrideMode(!!u.custom_permissions); }
  }, [serverPerms, serverScopes, u.custom_permissions]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (!editing) setDetails(detailsFrom(u)); }, [u, editing]);

  const scopeCameras = useMemo(() => camerasInScope(u.role, u.barangay_id, u.station_id, stations, cameras), [u, stations, cameras]);
  const parent = users.find(x => x.id === u.parent_admin_id);
  const customRole = customRoles.find(r => r.id === u.custom_role_id);
  const age = ageFrom(u.birthdate);

  const saveDetails = async () => {
    setDetailsBusy(true);
    const body: Record<string, any> = {
      username: details.username.trim(), assignment: details.assignment.trim(), display_title: details.display_title.trim(),
      full_name: details.full_name, birthdate: details.birthdate, home_address: details.home_address,
      contact_number: details.contact_number, position: details.position,
    };
    if (details.password.trim()) body.password = details.password.trim();
    if (!isDevteam) {
      if (isPnp && details.station_id) body.station_id = details.station_id;
      if (!isPnp && details.barangay_id) body.barangay_id = details.barangay_id;
    }
    try {
      const res = await fetch(`${apiUrl}/api/devteam/users/${u.id}`, { method: 'PATCH', headers: authHeaders(), body: JSON.stringify(body) });
      const d = await res.json().catch(() => ({}));
      if (res.ok) { flash('Details saved.'); setEditing(false); refresh(); }
      else flash(d.detail || 'Could not save details.');
    } catch {
      flash('Backend connection failure.');
    } finally {
      setDetailsBusy(false);
    }
  };

  const saveAccess = async () => {
    setAccessError('');
    const problem = scopeProblem(u.role, perms, scopes, overrideMode);
    if (problem) { setAccessError(problem); return; }
    const changingOverride = isAdmin && (overrideMode || u.custom_permissions);
    if (changingOverride && !overridePassword) { setAccessError('Enter your DevTeam password to change an admin\'s permissions.'); return; }
    setAccessBusy(true);
    try {
      let res: Response | null = null;
      if (changingOverride) {
        res = await fetch(`${apiUrl}/api/devteam/users/${u.id}/override_permissions`, {
          method: 'POST', headers: authHeaders(),
          body: JSON.stringify({ confirm_password: overridePassword, permissions: overrideMode ? onlyEditablePermissions(u.role, perms, true) : null }),
        });
      } else if (!isAdmin) {
        res = await fetch(`${apiUrl}/api/admin/users/${u.id}/permissions`, {
          method: 'PATCH', headers: authHeaders(), body: JSON.stringify({ permissions: onlyEditablePermissions(u.role, perms) }),
        });
      }
      if (res && !res.ok) {
        const d = await res.json().catch(() => ({}));
        setAccessError(d.detail || 'Could not save permissions.');
        return;
      }
      const scopeRes = await fetch(`${apiUrl}/api/devteam/users/${u.id}/camera_scopes`, {
        method: 'PUT', headers: authHeaders(),
        body: JSON.stringify({ scopes: scopesForSave(u.role, perms, scopes, overrideMode) }),
      });
      if (!scopeRes.ok) {
        const d = await scopeRes.json().catch(() => ({}));
        setAccessError(d.detail || 'Permissions saved, but camera limits failed.');
        return;
      }
      setOverridePassword('');
      flash('Access updated.');
      refresh();
    } catch {
      setAccessError('Backend connection failure.');
    } finally {
      setAccessBusy(false);
    }
  };

  const deleteUser = async () => {
    try {
      const res = await fetch(`${apiUrl}/api/devteam/users/${u.id}`, { method: 'DELETE', headers: authHeaders() });
      const d = await res.json().catch(() => ({}));
      if (res.ok) { flash(`${u.username} removed (restorable from the Audit Log).`); onDeleted(); refresh(); }
      else flash(d.detail || 'Delete failed.');
    } catch {
      flash('Backend connection failure.');
    }
  };

  const set = (patch: Partial<ReturnType<typeof detailsFrom>>) => setDetails(prev => ({ ...prev, ...patch }));

  return (
    <>
      <PaneHeader
        icon={<User size={12} />}
        title="Account details"
        right={!isDevteam && (
          <button onClick={() => setEditing(e => !e)} className="flex items-center gap-1 text-[9px] tracking-[0.1em] uppercase text-[var(--accent)] hover:opacity-80">
            {editing ? <><X size={10} /> Cancel edit</> : <><Pencil size={10} /> Edit details</>}
          </button>
        )}
      />
      <div className="flex-1 min-h-0 overflow-y-auto custom-scrollbar p-5 space-y-5">
        <div className="flex items-start gap-4">
          <FacePhoto apiUrl={apiUrl} userId={u.id} hasPhoto={u.has_face_photo} name={u.full_name || u.username} size={64} />
          <div className="min-w-0 flex-1">
            <p className="text-[14px] text-[var(--text)] truncate">{u.full_name || <span className="text-[var(--text-3)]">No name on record</span>}</p>
            <p className="text-[10px] text-[var(--text-2)] truncate">@{u.username} · ID #{u.id}</p>
            <div className="flex items-center gap-1.5 mt-1.5">
              <span className={`text-[8px] font-bold px-1.5 py-0.5 border ${style.border} ${style.text}`}>{roleLabel(u.role)}</span>
              {isAdmin && u.custom_permissions && <span className="text-[8px] uppercase tracking-wide px-1.5 py-0.5 border border-[var(--accent)]/30 text-[var(--accent)]">custom perms</span>}
              {customRole && <span className="text-[8px] uppercase tracking-wide px-1.5 py-0.5 border border-[var(--line-2)] text-[var(--text-2)]">{customRole.name}</span>}
            </div>
          </div>
        </div>

        {editing ? (
          <div className="space-y-3 border border-[var(--line)] p-4">
            <SectionLabel>Edit details</SectionLabel>
            <div className="grid grid-cols-2 gap-3">
              <FieldInput label="Username" value={details.username} onChange={v => set({ username: v })} />
              <FieldInput label="New password" type="password" value={details.password} onChange={v => set({ password: v })} placeholder="blank = keep current" />
            </div>
            <FieldInput label="Full name" value={details.full_name} onChange={v => set({ full_name: v })} />
            <div className="grid grid-cols-2 gap-3">
              <FieldInput label="Birthdate" type="date" value={details.birthdate} onChange={v => set({ birthdate: v })} />
              <FieldInput label="Contact number" value={details.contact_number} onChange={v => set({ contact_number: v })} />
            </div>
            <FieldInput label="Residence" value={details.home_address} onChange={v => set({ home_address: v })} />
            <div className="grid grid-cols-2 gap-3">
              <SelectInput label="Position" value={details.position} onChange={v => set({ position: v })}
                options={Array.from(new Set([...positionsForRole(u.role), ...(details.position ? [details.position] : [])]))} />
              <FieldInput label="Assignment" value={details.assignment} onChange={v => set({ assignment: v })} />
            </div>
            <FieldInput label="Display title" value={details.display_title} onChange={v => set({ display_title: v })} />
            {!isDevteam && (isPnp ? (
              <SelectInput label="Police station" value={details.station_id} onChange={v => set({ station_id: v })}
                options={stations.map(s => ({ value: s.id, label: s.name }))} />
            ) : (
              <SelectInput label="Barangay" value={details.barangay_id} onChange={v => set({ barangay_id: v })}
                options={allLocations.map(l => ({ value: l.id, label: l.name }))} />
            ))}
            <button onClick={saveDetails} disabled={detailsBusy}
              className="w-full py-2.5 bg-[var(--accent)] text-[#fff] text-[10px] font-bold tracking-[0.15em] uppercase disabled:opacity-50 hover:opacity-90 flex items-center justify-center gap-2">
              <Save size={12} /> {detailsBusy ? 'Saving…' : 'Save details'}
            </button>
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-x-4 gap-y-3">
            <InfoRow label="Age" value={age !== null ? `${age} (born ${u.birthdate})` : null} />
            <InfoRow label="Contact number" value={u.contact_number} />
            <div className="col-span-2"><InfoRow label="Residence" value={u.home_address} /></div>
            <InfoRow label="Position" value={u.position} />
            <InfoRow label="Assignment" value={u.assignment} />
            <InfoRow label={isPnp ? 'Police station' : isDevteam ? 'Organization' : 'Barangay'} value={orgName} />
            <InfoRow label="Reports to" value={parent ? (parent.full_name || parent.username) : null} />
            <InfoRow label="Display title" value={u.display_title} />
            <InfoRow label="Created" value={u.created_at ? new Date(u.created_at).toLocaleDateString() : null} />
            <InfoRow label="Last login" value={u.last_login ? new Date(u.last_login).toLocaleString() : 'Never'} />
          </div>
        )}

        {!isDevteam && (
          <div className="border border-[var(--line)] p-4">
            <div className="flex items-center justify-between mb-2">
              <span className="flex items-center gap-1.5 text-[8px] tracking-[0.15em] uppercase text-[var(--text-2)]"><IdCard size={10} /> Identity verification</span>
              <span className="text-[9px] uppercase tracking-wide" style={{ color: u.verification_status === 'verified' ? 'var(--ok)' : u.verification_status === 'pending' ? 'var(--warn)' : u.verification_status === 'rejected' ? 'var(--critical)' : 'var(--text-3)' }}>
                {u.verification_status || 'unverified'}
              </span>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {u.has_document ? (
                <button onClick={() => openIdentityFile(apiUrl, u.id, 'verification_document', flash)} className="px-2.5 py-1.5 border border-[var(--line-2)] text-[9px] uppercase tracking-wide text-[var(--text-2)] hover:text-[var(--accent)]">View government ID</button>
              ) : <span className="text-[9px] text-[var(--text-3)]">No government ID submitted.</span>}
              {u.has_face_photo && (
                <button onClick={() => openIdentityFile(apiUrl, u.id, 'face_photo', flash)} className="px-2.5 py-1.5 border border-[var(--line-2)] text-[9px] uppercase tracking-wide text-[var(--text-2)] hover:text-[var(--accent)]">View face photo</button>
              )}
              {u.has_document && u.verification_status !== 'verified' && (
                <button onClick={() => reviewVerification(u.id, 'verified')} className="flex items-center gap-1 px-2.5 py-1.5 border border-[var(--ok)]/40 text-[9px] uppercase tracking-wide text-[var(--ok)] hover:bg-[var(--ok)]/10"><ShieldCheck size={11} /> Verify</button>
              )}
              {u.has_document && u.verification_status !== 'rejected' && (
                <button onClick={() => reviewVerification(u.id, 'rejected')} className="flex items-center gap-1 px-2.5 py-1.5 border border-[var(--critical)]/40 text-[9px] uppercase tracking-wide text-[var(--critical)] hover:bg-[var(--critical)]/10"><ShieldX size={11} /> Reject</button>
              )}
            </div>
          </div>
        )}

        {!isDevteam && (
          <div>
            <div className="flex items-center gap-1.5 text-[8px] tracking-[0.15em] uppercase text-[var(--text-2)] mb-2">
              <KeyRound size={10} /> Permissions — tick to activate, untick to deactivate
            </div>

            {isAdmin && (
              <div className="mb-2 border border-[var(--panel-2)]">
                <label className="flex items-center justify-between px-3 py-2 cursor-pointer hover:bg-[var(--panel-2)]/50">
                  <span className="text-[9px] tracking-[0.1em] uppercase text-[var(--text-2)]">Override automatic admin permissions</span>
                  <input
                    type="checkbox"
                    checked={overrideMode}
                    onChange={e => {
                      const on = e.target.checked;
                      setOverrideMode(on);
                      // First override: start from what the admin effectively
                      // has now, so saving without other changes is a no-op.
                      if (on && !u.custom_permissions) {
                        const seeded: Record<string, boolean> = {};
                        permissionRowsFor(u.role, true).forEach(p => { if (p.status !== 'banned') seeded[p.key] = true; });
                        setPerms(seeded);
                      }
                    }}
                    className="w-3.5 h-3.5 accent-[var(--accent)]"
                  />
                </label>
                {(overrideMode || u.custom_permissions) && (
                  <div className="px-3 pb-3 pt-1 border-t border-[var(--panel-2)]">
                    <label className={labelClass}>Confirm DevTeam password</label>
                    <input type="password" value={overridePassword} onChange={e => setOverridePassword(e.target.value)}
                      placeholder={overrideMode ? 'required to apply this override' : 'required to reset to automatic'} className={inputClass} />
                  </div>
                )}
              </div>
            )}

            {permissionNoteFor(u.role, overrideMode) && (
              <p className="text-[9px] leading-relaxed text-[var(--text-3)] mb-2">{permissionNoteFor(u.role, overrideMode)}</p>
            )}
            <PermissionTree
              role={u.role}
              customPermissions={overrideMode}
              perms={perms}
              onPermsChange={setPerms}
              scopes={scopes}
              onScopesChange={setScopes}
              cameras={scopeCameras}
              subject={u.full_name || u.username}
            />
            {accessError && <p className="text-[10px] text-[var(--critical)] uppercase tracking-wide mt-2">{accessError}</p>}
            <button
              onClick={saveAccess}
              disabled={accessBusy || !accessDirty}
              className="w-full mt-3 py-2.5 bg-[var(--accent)] text-[#fff] text-[10px] font-bold tracking-[0.15em] uppercase disabled:opacity-30 hover:opacity-90 flex items-center justify-center gap-2"
            >
              <Save size={12} /> {accessBusy ? 'Saving…' : accessDirty ? 'Save access' : 'No changes'}
            </button>
          </div>
        )}

        {!isDevteam && (
          <div className="border border-[var(--critical)]/25 p-4">
            {confirmDelete ? (
              <div className="flex items-center justify-between gap-3">
                <span className="text-[10px] text-[var(--text)]">Remove {u.username}? They lose access immediately; restorable from the Audit Log.</span>
                <div className="flex gap-2 shrink-0">
                  <button onClick={() => setConfirmDelete(false)} className="px-3 py-1.5 border border-[var(--line)] text-[9px] uppercase tracking-wide text-[var(--text-2)]">Cancel</button>
                  <button onClick={deleteUser} className="px-3 py-1.5 bg-[var(--critical)] text-[#fff] text-[9px] uppercase tracking-wide">Remove</button>
                </div>
              </div>
            ) : (
              <button onClick={() => setConfirmDelete(true)} className="flex items-center gap-1.5 text-[9px] tracking-[0.1em] uppercase text-[var(--critical)]/80 hover:text-[var(--critical)]">
                <Trash2 size={11} /> Remove account
              </button>
            )}
          </div>
        )}
      </div>
    </>
  );
}
