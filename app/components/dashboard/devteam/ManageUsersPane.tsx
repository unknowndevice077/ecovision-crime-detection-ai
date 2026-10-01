"use client";

import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, History, IdCard, KeyRound, Pencil, Save, Search, ShieldCheck, ShieldX, Trash2, Upload, User, Users2, X } from 'lucide-react';
import PermissionTree, { ResourceScopes, narrowedOnly, scopeProblem, scopesForSave } from './PermissionTree';
import {
  ADMIN_ROLES, CameraRow, CustomRole, EmptyPane, FieldInput, InfoRow, ManagedUser, PNP_ROLES, PaneHeader,
  PendingLocation, SectionLabel, SelectInput, Station, ageFrom, applicationStatusLabel, authHeaders, camerasInScope, inputClass,
  labelClass, roleLabel, roleStyle, useAuthedObjectUrl,
} from './shared';
import { onlyEditablePermissions, permissionNoteFor, permissionRowsFor } from '../../../lib/permissions';
import { positionsForRole } from '../../../lib/positions';
import { serverDateTime, serverDay } from '../../../lib/time';

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

// false == absent and null (everything) == absent, so toggling something
// back to how it was doesn't read as an unsaved change.
const normPerms = (p: Record<string, boolean>) => JSON.stringify(Object.keys(p).filter(k => p[k]).sort());
const normScopes = (s: ResourceScopes) => JSON.stringify(
  Object.entries(narrowedOnly(s)).sort(([a], [b]) => a.localeCompare(b))
    .map(([k, dims]) => [k, Object.entries(dims).sort(([a], [b]) => a.localeCompare(b)).map(([d, ids]) => [d, [...(ids || [])].sort()])]));

const detailsFrom = (u: ManagedUser) => ({
  username: u.username, password: '', password_confirm: '', assignment: u.assignment || '', display_title: u.display_title || '',
  full_name: u.full_name || '', birthdate: u.birthdate || '', home_address: u.home_address || '',
  contact_number: u.contact_number || '', position: u.position || '',
  barangay_id: u.barangay_id || '', station_id: u.station_id || '',
  role: u.role, parent_admin_id: u.parent_admin_id ? String(u.parent_admin_id) : '', custom_role_id: u.custom_role_id || '',
});

const EDITABLE_ROLES = ['BARANGAY_ADMIN', 'BARANGAY_STAFF', 'PNP_ADMIN', 'PNP_OFFICER'];

type AuditRow = {
  id: string; action: string; actor_user_id: number | null; actor_username: string;
  target_type: string; target_id: string; created_at: string; target_snapshot?: any;
};

// Everything this account did, and everything done to it -- the per-person
// slice of the Audit Log tab.
function AccountActivity({ apiUrl, user }: { apiUrl: string; user: ManagedUser }) {
  const [rows, setRows] = useState<AuditRow[] | null>(null);
  const [showAll, setShowAll] = useState(false);
  useEffect(() => {
    let cancelled = false;
    fetch(`${apiUrl}/api/devteam/audit_log?user_id=${user.id}&limit=200`, { headers: authHeaders() })
      .then(r => (r.ok ? r.json() : []))
      .then(d => { if (!cancelled) setRows(Array.isArray(d) ? d : []); })
      .catch(() => { if (!cancelled) setRows([]); });
    return () => { cancelled = true; };
  }, [apiUrl, user]);

  const visible = rows ? (showAll ? rows : rows.slice(0, 12)) : [];
  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <span className="flex items-center gap-1.5 text-[8px] tracking-[0.15em] uppercase text-[var(--text-2)]"><History size={10} /> Activity</span>
        {rows && <span className="text-[9px] text-[var(--text-3)]">{rows.length}{rows.length === 200 ? '+' : ''} entr{rows.length === 1 ? 'y' : 'ies'}</span>}
      </div>
      {rows === null ? (
        <p className="text-[10px] text-[var(--text-3)]">Loading…</p>
      ) : rows.length === 0 ? (
        <p className="text-[10px] text-[var(--text-3)]">Nothing recorded for this account yet.</p>
      ) : (
        <>
          <div className="border border-[var(--panel-2)] divide-y divide-[var(--panel-2)]">
            {visible.map(r => {
              const byThem = r.actor_user_id === user.id;
              const denied = r.action.startsWith('denied') || r.action.endsWith('login_failed');
              return (
                <div key={r.id} className="flex items-center gap-2.5 px-3 py-1.5">
                  <span className={`text-[8px] uppercase tracking-wide w-10 shrink-0 ${byThem ? 'text-[var(--accent)]' : 'text-[var(--text-3)]'}`}>{byThem ? 'did' : 'to them'}</span>
                  <span className={`text-[10px] truncate flex-1 ${denied ? 'text-[var(--critical)]' : 'text-[var(--text)]'}`}>
                    {r.action}
                    {!byThem && <span className="text-[var(--text-3)]"> · by {r.actor_username}</span>}
                    {byThem && r.target_type !== 'user' && <span className="text-[var(--text-3)]"> · {r.target_type} {r.target_id}</span>}
                  </span>
                  <span className="text-[9px] text-[var(--text-3)] shrink-0">{serverDateTime(r.created_at)}</span>
                </div>
              );
            })}
          </div>
          {rows.length > 12 && (
            <button onClick={() => setShowAll(v => !v)} className="mt-1.5 text-[9px] uppercase tracking-wide text-[var(--accent)] hover:opacity-80">
              {showAll ? 'Show less' : `Show all ${rows.length}`}
            </button>
          )}
        </>
      )}
    </div>
  );
}

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
                  {applicationStatusLabel(u) && (
                    <p className="text-[8px] uppercase tracking-wide" style={{ color: applicationStatusLabel(u)!.color }}>{applicationStatusLabel(u)!.text}</p>
                  )}
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
  const serverScopes = useMemo<ResourceScopes>(() => ({ ...(u.resource_scopes || {}) }), [u.resource_scopes]);

  const [perms, setPerms] = useState<Record<string, boolean>>(serverPerms);
  const [scopes, setScopes] = useState<ResourceScopes>(serverScopes);
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

  // A typed-in password used to be saved with the rest of the form, no
  // questions asked. It now has to be typed twice and confirmed in a
  // dialog that says what happens to the person.
  const [confirmPassword, setConfirmPassword] = useState(false);
  const passwordProblem = !details.password ? ''
    : details.password.length < 8 ? 'The new password needs at least 8 characters.'
    : details.password !== details.password_confirm ? 'The two passwords don\'t match.' : '';

  const saveDetails = async (passwordConfirmed = false) => {
    if (details.password) {
      if (passwordProblem) { flash(passwordProblem); return; }
      if (!passwordConfirmed) { setConfirmPassword(true); return; }
    }
    setConfirmPassword(false);
    setDetailsBusy(true);
    const body: Record<string, any> = {
      username: details.username.trim(), assignment: details.assignment.trim(), display_title: details.display_title.trim(),
      full_name: details.full_name, birthdate: details.birthdate, home_address: details.home_address,
      contact_number: details.contact_number, position: details.position,
    };
    if (details.password) body.password = details.password;
    if (!isDevteam) {
      const pnpNow = PNP_ROLES.includes(details.role);
      if (details.role !== u.role) body.role = details.role;
      if (pnpNow && details.station_id) body.station_id = details.station_id;
      if (!pnpNow && details.barangay_id) body.barangay_id = details.barangay_id;
      if (details.parent_admin_id !== (u.parent_admin_id ? String(u.parent_admin_id) : '')) {
        body.parent_admin_id = details.parent_admin_id ? Number(details.parent_admin_id) : null;
      }
      if (details.custom_role_id !== (u.custom_role_id || '')) body.custom_role_id = details.custom_role_id || null;
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
      const scopeRes = await fetch(`${apiUrl}/api/devteam/users/${u.id}/resource_scopes`, {
        method: 'PUT', headers: authHeaders(),
        body: JSON.stringify({ scopes: scopesForSave(u.role, perms, scopes, overrideMode) }),
      });
      if (!scopeRes.ok) {
        const d = await scopeRes.json().catch(() => ({}));
        setAccessError(d.detail || 'Permissions saved, but the access limits failed.');
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

  const editPnp = PNP_ROLES.includes(details.role);
  const supervisorOptions = users
    .filter(x => x.id !== u.id && x.role === (editPnp ? 'PNP_ADMIN' : 'BARANGAY_ADMIN'))
    .filter(x => editPnp ? (!details.station_id || x.station_id === details.station_id) : (!details.barangay_id || x.barangay_id === details.barangay_id))
    .map(x => ({ value: String(x.id), label: `${x.full_name || x.username} (@${x.username})` }));

  const [uploadBusy, setUploadBusy] = useState(false);
  const uploadIdentity = async (field: 'id_document' | 'face_photo', file: File | undefined) => {
    if (!file) return;
    setUploadBusy(true);
    try {
      const form = new FormData();
      form.append(field, file);
      const headers: Record<string, string> = { ...authHeaders() };
      delete headers['Content-Type'];
      const res = await fetch(`${apiUrl}/api/devteam/users/${u.id}/identity_files`, { method: 'POST', headers, body: form });
      const d = await res.json().catch(() => ({}));
      if (res.ok) { flash(field === 'id_document' ? 'ID uploaded -- verification is pending again.' : 'Face photo updated.'); refresh(); }
      else flash(d.detail || 'Upload failed.');
    } catch {
      flash('Backend connection failure.');
    } finally {
      setUploadBusy(false);
    }
  };

  return (
    <>
      <PaneHeader
        icon={<User size={12} />}
        title="Account details"
        right={(
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
            {details.password && (
              <div className="grid grid-cols-2 gap-3">
                <div />
                <div>
                  <FieldInput label="Confirm new password" type="password" value={details.password_confirm} onChange={v => set({ password_confirm: v })} placeholder="type it again" />
                  {passwordProblem && <p className="text-[9px] mt-1 text-[var(--warn)]">{passwordProblem}</p>}
                </div>
              </div>
            )}
            <FieldInput label="Full name" value={details.full_name} onChange={v => set({ full_name: v })} />
            <div className="grid grid-cols-2 gap-3">
              <FieldInput label="Birthdate" type="date" value={details.birthdate} onChange={v => set({ birthdate: v })} />
              <FieldInput label="Contact number" value={details.contact_number} onChange={v => set({ contact_number: v })} />
            </div>
            <FieldInput label="Residence" value={details.home_address} onChange={v => set({ home_address: v })} />
            <div className="grid grid-cols-2 gap-3">
              <SelectInput label="Position" value={details.position} onChange={v => set({ position: v })}
                options={Array.from(new Set([...positionsForRole(details.role), ...(details.position ? [details.position] : [])]))} />
              <FieldInput label="Assignment" value={details.assignment} onChange={v => set({ assignment: v })} />
            </div>
            <FieldInput label="Display title" value={details.display_title} onChange={v => set({ display_title: v })} />
            {!isDevteam && (
              <>
                <SectionLabel>Role and placement</SectionLabel>
                <div className="grid grid-cols-2 gap-3">
                  <SelectInput label="Role" value={details.role}
                    onChange={v => set({ role: v, parent_admin_id: '', position: '' })}
                    options={EDITABLE_ROLES.map(r => ({ value: r, label: roleLabel(r) }))} />
                  {editPnp ? (
                    <SelectInput label="Police station" value={details.station_id} onChange={v => set({ station_id: v, parent_admin_id: '' })}
                      options={stations.map(s => ({ value: s.id, label: s.name }))} />
                  ) : (
                    <SelectInput label="Barangay" value={details.barangay_id} onChange={v => set({ barangay_id: v, parent_admin_id: '' })}
                      options={allLocations.filter(l => (l.status || 'approved') === 'approved' || l.id === u.barangay_id).map(l => ({ value: l.id, label: l.name }))} />
                  )}
                </div>
                {editPnp !== isPnp && (
                  <p className="text-[9px] leading-relaxed text-[var(--warn)]">
                    Moving this account to the {editPnp ? 'police' : 'barangay'} side -- pick its {editPnp ? 'station' : 'barangay'} above.
                    Permissions the new side can&apos;t hold stop working immediately.
                  </p>
                )}
                <div className="grid grid-cols-2 gap-3">
                  <SelectInput label="Reports to" value={details.parent_admin_id} onChange={v => set({ parent_admin_id: v })}
                    placeholder="— nobody —" options={supervisorOptions} />
                  <SelectInput label="Custom role" value={details.custom_role_id} onChange={v => set({ custom_role_id: v })}
                    placeholder="— none —" options={customRoles.map(r => ({ value: r.id, label: r.name }))} />
                </div>
              </>
            )}
            <button onClick={() => saveDetails()} disabled={detailsBusy}
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
            <InfoRow label="Created" value={serverDay(u.created_at) || null} />
            <InfoRow label="Last login" value={serverDateTime(u.last_login, 'Never')} />
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
              <label className={`flex items-center gap-1 px-2.5 py-1.5 border border-[var(--line-2)] text-[9px] uppercase tracking-wide text-[var(--text-2)] hover:text-[var(--accent)] cursor-pointer ${uploadBusy ? 'opacity-40 pointer-events-none' : ''}`}>
                <Upload size={10} /> {u.has_document ? 'Replace ID' : 'Upload ID'}
                <input type="file" accept="image/jpeg,image/png,image/webp,application/pdf" className="hidden"
                  onChange={e => { uploadIdentity('id_document', e.target.files?.[0]); e.target.value = ''; }} />
              </label>
              <label className={`flex items-center gap-1 px-2.5 py-1.5 border border-[var(--line-2)] text-[9px] uppercase tracking-wide text-[var(--text-2)] hover:text-[var(--accent)] cursor-pointer ${uploadBusy ? 'opacity-40 pointer-events-none' : ''}`}>
                <Upload size={10} /> {u.has_face_photo ? 'Replace photo' : 'Upload photo'}
                <input type="file" accept="image/jpeg,image/png,image/webp" className="hidden"
                  onChange={e => { uploadIdentity('face_photo', e.target.files?.[0]); e.target.value = ''; }} />
              </label>
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

        <AccountActivity apiUrl={apiUrl} user={u} />

        {confirmPassword && (
          <div className="fixed inset-0 z-[140] flex items-center justify-center p-4 bg-[var(--bg)]/85">
            <div className="bg-[var(--panel)] border border-[var(--warn)]/40 w-full max-w-sm" role="alertdialog" aria-label="Change password">
              <div className="flex items-center gap-2 px-4 py-3 border-b border-[var(--panel-2)]">
                <AlertTriangle size={13} className="text-[var(--warn)]" />
                <span className="text-[10px] tracking-[0.15em] uppercase text-[var(--text)]">Change password</span>
              </div>
              <div className="p-4 space-y-3">
                <p className="text-[12px] leading-relaxed text-[var(--text)]">
                  Change the password for <b>{u.full_name || u.username}</b> <span className="text-[var(--text-3)]">(@{u.username})</span>?
                </p>
                <ul className="text-[11px] leading-relaxed list-disc pl-4 space-y-1 text-[var(--text-2)]">
                  <li>Their current password stops working as soon as you save.</li>
                  <li>You will need to give them the new one yourself.</li>
                  <li>The change is recorded in the Audit Log under your name.</li>
                </ul>
                <div className="flex justify-end gap-2 pt-1">
                  <button onClick={() => setConfirmPassword(false)} autoFocus
                    className="px-3 py-1.5 border border-[var(--line)] text-[10px] uppercase tracking-wide text-[var(--text-2)] hover:text-[var(--text)]">
                    Cancel
                  </button>
                  <button onClick={() => saveDetails(true)}
                    className="px-3 py-1.5 bg-[var(--warn)] text-[#fff] text-[10px] uppercase tracking-wide hover:opacity-90">
                    Change password
                  </button>
                </div>
              </div>
            </div>
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
