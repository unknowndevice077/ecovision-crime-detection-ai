"use client";

import React, { useState } from 'react';
import { Users, UserPlus, Trash2, ShieldCheck, X, Save, KeyRound, Eye, EyeOff, RefreshCw, Copy, Check } from 'lucide-react';
import { useLiveChannel } from '../../context/WebSocketContext';
import { useRuntimeConfig } from '../../hooks/useRuntimeConfig';
import { SkeletonList } from './Skeleton';
import { permissionRowsFor, permissionNoteFor, onlyEditablePermissions } from '../../lib/permissions';
import { positionsForRole } from '../../lib/positions';
import { usePermissions } from '../../hooks/usePermissions';

type ManagedUser = {
  id: number;
  username: string;
  role: string;
  barangay_id: string;
  assignment: string;
  parent_admin_id: number | null;
  permissions: string; // JSON string from backend
  verification_status?: string;
  full_name?: string | null;
  position?: string | null;
};

function authHeaders() {
  const token = typeof window !== "undefined" ? localStorage.getItem("ecoToken") : null;
  return { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) };
}

export default function AdminUsersView() {
  const { apiUrl: API_URL } = useRuntimeConfig();
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const EMPTY_NEW_USER = { username: '', password: '', assignment: '', full_name: '', position: '' };
  const [newUser, setNewUser] = useState(EMPTY_NEW_USER);
  // The account this admin creates is always their side's standard role.
  const { role: myRole } = usePermissions();
  const [showNewPassword, setShowNewPassword] = useState(false);
  const [editingPerms, setEditingPerms] = useState<ManagedUser | null>(null);
  const [permsDraft, setPermsDraft] = useState<Record<string, boolean>>({});
  const [error, setError] = useState('');
  // ids currently mid-flight on an optimistic action, so we can show a
  // subtle disabled/pending state instead of the whole row popping in/out
  const [pendingIds, setPendingIds] = useState<Set<number>>(new Set());

  const fetchUsers = async () => {
    try {
      const res = await fetch(`${API_URL}/api/admin/users`, { headers: authHeaders() });
      if (res.ok) setUsers(await res.json());
    } catch (e) {
      console.error("Failed to load managed users:", e);
    } finally {
      setIsLoading(false);
    }
  };

  // Replaces the old setInterval(fetchUsers, 8000) -- refetches instantly
  // when the shared WebSocket sees any relevant broadcast, with a slow
  // 60s fallback poll as a safety net rather than the primary mechanism.
  useLiveChannel("users", fetchUsers);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    try {
      const res = await fetch(`${API_URL}/api/admin/users`, {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify(newUser),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        setShowCreate(false);
        setNewUser(EMPTY_NEW_USER);
        fetchUsers();
      } else {
        setError(data.detail || "Failed to create user");
      }
    } catch (e) {
      setError("Backend connection failure");
    }
  };

  // OPTIMISTIC DELETE: remove from local state immediately, roll back if
  // the request fails. Previously this waited for the round trip + a full
  // refetch before the row disappeared, which felt laggy for a triage tool.
  const handleDelete = async (id: number) => {
    // One click used to remove the account outright.
    const target = users.find(u => u.id === id);
    if (!window.confirm(`Remove ${target?.username || 'this account'}?

They can no longer sign in. DevTeam can restore the account from the Audit Log.`)) return;
    const snapshot = users;
    setUsers(prev => prev.filter(u => u.id !== id));
    setPendingIds(prev => new Set(prev).add(id));
    try {
      const res = await fetch(`${API_URL}/api/admin/users/${id}`, { method: "DELETE", headers: authHeaders() });
      if (!res.ok) {
        setUsers(snapshot); // roll back
        setError("Could not remove user -- restored.");
        setTimeout(() => setError(''), 3000);
      }
    } catch (e) {
      setUsers(snapshot);
      setError("Backend connection failure -- restored.");
      setTimeout(() => setError(''), 3000);
    } finally {
      setPendingIds(prev => { const next = new Set(prev); next.delete(id); return next; });
    }
  };

  // Generates a new random password server-side and returns it ONCE --
  // same convention as the DevTeam bootstrap credential (shown once, never
  // re-fetchable). Every row in `users` here was already scoped by the
  // backend to this admin's own parent_admin_id, so there's no admin-account
  // row to accidentally target -- the backend still refuses one either way.
  const [resetResult, setResetResult] = useState<{ username: string; password: string } | null>(null);
  const [resetBusyId, setResetBusyId] = useState<number | null>(null);
  const [copied, setCopied] = useState(false);

  const handleResetPassword = async (u: ManagedUser) => {
    // One click used to replace the password straight away, locking the
    // person out until they're handed the new one.
    if (!window.confirm(`Reset the password for ${u.full_name || u.username}?

Their current password stops working immediately. You'll be shown the new one once.`)) return;
    setResetBusyId(u.id);
    setError('');
    try {
      const res = await fetch(`${API_URL}/api/admin/users/${u.id}/reset_password`, {
        method: "POST", headers: authHeaders(),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        setResetResult({ username: data.username, password: data.new_password });
      } else {
        setError(data.detail || "Could not reset that password");
        setTimeout(() => setError(''), 3000);
      }
    } catch {
      setError("Backend connection failure");
      setTimeout(() => setError(''), 3000);
    } finally {
      setResetBusyId(null);
    }
  };

  // ── Camera-level access (Phase 2, 2026-09-23) ───────────────────────────
  // "Dice every permission down to the smallest unit" -- the barangay/PNP
  // admin's own scoped-down version of DevTeam's Permissions tab: restrict
  // ONE of their own subordinates to specific cameras only. Reuses
  // GET /api/cameras as-is for the picker -- it's already org-scoped to
  // this admin's own token, so no separate "cameras I can grant" endpoint
  // is needed the way DevTeam's unscoped picker needed one.
  const [grantCameras, setGrantCameras] = useState<{ id: string; name: string }[]>([]);
  const [userGrants, setUserGrants] = useState<any[]>([]);
  const [grantCameraId, setGrantCameraId] = useState('');
  const [grantBusy, setGrantBusy] = useState(false);

  const fetchUserGrants = async (userId: number) => {
    try {
      const res = await fetch(`${API_URL}/api/admin/users/${userId}/resource_permissions`, { headers: authHeaders() });
      if (res.ok) setUserGrants(await res.json());
    } catch { /* leave whatever was last shown */ }
  };

  const openPermissions = (u: ManagedUser) => {
    setEditingPerms(u);
    try {
      setPermsDraft(JSON.parse(u.permissions || "{}"));
    } catch {
      setPermsDraft({});
    }
    setGrantCameraId('');
    fetchUserGrants(u.id);
    if (grantCameras.length === 0) {
      fetch(`${API_URL}/api/cameras`, { headers: authHeaders() })
        .then(res => res.ok ? res.json() : [])
        .then(setGrantCameras)
        .catch(() => {});
    }
  };

  const grantCameraAccess = async () => {
    if (!editingPerms || !grantCameraId) return;
    setGrantBusy(true);
    try {
      const res = await fetch(`${API_URL}/api/admin/users/${editingPerms.id}/resource_permissions`, {
        method: 'POST', headers: authHeaders(),
        body: JSON.stringify({ permission_key: 'view_map', resource_type: 'camera', resource_id: grantCameraId }),
      });
      const d = await res.json().catch(() => ({}));
      if (res.ok) { fetchUserGrants(editingPerms.id); setGrantCameraId(''); }
      else { setError(d.detail || 'Could not grant.'); setTimeout(() => setError(''), 3000); }
    } catch {
      setError('Backend connection failure.'); setTimeout(() => setError(''), 3000);
    } finally {
      setGrantBusy(false);
    }
  };

  const reviewVerification = async (userId: number, decision: 'verified' | 'rejected') => {
    try {
      const res = await fetch(`${API_URL}/api/admin/users/${userId}/verification`, {
        method: 'POST', headers: authHeaders(), body: JSON.stringify({ decision }),
      });
      const d = await res.json().catch(() => ({}));
      if (res.ok) { fetchUsers(); }
      else { setError(d.detail || 'Could not update.'); setTimeout(() => setError(''), 3000); }
    } catch {
      setError('Backend connection failure.'); setTimeout(() => setError(''), 3000);
    }
  };

  const viewIdDocument = async (userId: number) => {
    try {
      const res = await fetch(`${API_URL}/api/users/${userId}/verification_document`, { headers: authHeaders() });
      if (!res.ok) { setError('No document on file.'); setTimeout(() => setError(''), 3000); return; }
      const blob = await res.blob();
      window.open(URL.createObjectURL(blob), '_blank');
    } catch {
      setError('Backend connection failure.'); setTimeout(() => setError(''), 3000);
    }
  };

  const revokeCameraAccess = async (grant: any) => {
    if (!editingPerms) return;
    setGrantBusy(true);
    try {
      const res = await fetch(`${API_URL}/api/admin/users/${editingPerms.id}/resource_permissions`, {
        method: 'DELETE', headers: authHeaders(),
        body: JSON.stringify({ permission_key: grant.permission_key, resource_type: grant.resource_type, resource_id: grant.resource_id }),
      });
      if (res.ok) fetchUserGrants(editingPerms.id);
    } catch { /* leave the list as-is -- user can retry */ }
    finally {
      setGrantBusy(false);
    }
  };

  // OPTIMISTIC PERMISSIONS SAVE: update the local user's permissions blob
  // immediately so the "N permissions granted" count updates on close,
  // instead of waiting on a refetch.
  const savePermissions = async () => {
    if (!editingPerms) return;
    const snapshot = users;
    const editablePerms = onlyEditablePermissions(editingPerms.role, permsDraft);
    const updatedPermsJson = JSON.stringify(editablePerms);
    setUsers(prev => prev.map(u => u.id === editingPerms.id ? { ...u, permissions: updatedPermsJson } : u));
    setEditingPerms(null);
    try {
      const res = await fetch(`${API_URL}/api/admin/users/${editingPerms.id}/permissions`, {
        method: "PATCH",
        headers: authHeaders(),
        body: JSON.stringify({ permissions: editablePerms }),
      });
      if (!res.ok) {
        setUsers(snapshot);
        setError("Could not save permissions -- reverted.");
        setTimeout(() => setError(''), 3000);
      }
    } catch (e) {
      setUsers(snapshot);
      setError("Backend connection failure -- reverted.");
      setTimeout(() => setError(''), 3000);
    }
  };

  const inputStyle = { background: 'var(--bg)', borderColor: 'var(--line)' };
  const modalShell = { background: 'var(--panel)', borderColor: 'var(--line-2)' };

  return (
    <div className="border h-full flex flex-col w-full min-h-[420px]" style={{ background: 'var(--panel)', borderColor: 'var(--line)' }}>

      {/* Header */}
      <div className="shrink-0 border-b" style={{ borderColor: 'var(--line)' }}>
        <div className="h-9 flex items-center justify-between px-2.5 border-b" style={{ borderColor: 'var(--line)' }}>
          <div className="flex items-baseline gap-2.5">
            <span className="label" style={{ color: 'var(--text)' }}>Personnel</span>
            <span className="text-[10px]" style={{ color: 'var(--text-3)' }}>
              Accounts you created — permissions apply within your assigned area
            </span>
          </div>
          <span className="data text-[10px] px-1.5 py-0.5 border" style={{ color: 'var(--text-2)', borderColor: 'var(--line-2)' }}>
            {String(users.length).padStart(2, '0')} USERS
          </span>
        </div>

        <div className="p-2">
          <button
            onClick={() => setShowCreate(true)}
            className="flex items-center gap-1.5 px-2.5 py-1.5 text-[10px] font-bold uppercase tracking-wider text-white transition-opacity hover:opacity-90"
            style={{ background: 'var(--accent)' }}
          >
            <UserPlus size={12} /> New user
          </button>
        </div>
      </div>

      {error && (
        <div
          className="shrink-0 px-2.5 py-1.5 border-b text-[10px] font-bold uppercase tracking-wider"
          style={{ background: 'rgba(229,52,47,0.08)', borderColor: 'var(--critical)', color: 'var(--critical)' }}
        >
          {error}
        </div>
      )}

      {/* Column headers */}
      {!isLoading && users.length > 0 && (
        <div
          className="shrink-0 grid grid-cols-[1fr_120px_150px_80px] gap-2 px-2.5 py-1.5 border-b"
          style={{ borderColor: 'var(--line)', background: 'var(--bg)' }}
        >
          <span className="label">Operator</span>
          <span className="label">Role</span>
          <span className="label">Assignment</span>
          <span className="label text-right">Actions</span>
        </div>
      )}

      <div className="flex-1 overflow-y-auto custom-scrollbar">
        {isLoading ? (
          <div className="p-2"><SkeletonList rows={4} /></div>
        ) : users.length === 0 ? (
          <div className="h-48 flex flex-col items-center justify-center gap-2">
            <Users size={22} style={{ color: 'var(--text-3)' }} />
            <span className="label">No users created yet</span>
          </div>
        ) : (
          users.map(u => {
            let perms: Record<string, boolean> = {};
            try { perms = JSON.parse(u.permissions || "{}"); } catch {}
            const activeCount = Object.values(perms).filter(Boolean).length;
            const isPending = pendingIds.has(u.id);
            return (
              <div
                key={u.id}
                className={`grid grid-cols-[1fr_120px_150px_80px] gap-2 px-2.5 py-2 border-b items-center transition-colors hover:bg-white/[0.02] ${isPending ? 'opacity-40 pointer-events-none' : ''}`}
                style={{ borderColor: 'var(--line)' }}
              >
                <div className="min-w-0">
                  <div className="data text-[12px] font-bold text-[var(--text)] truncate">{u.full_name || u.username}</div>
                  <div className="text-[9px] mt-0.5" style={{ color: 'var(--text-3)' }}>
                    {u.full_name && <>@{u.username}{u.position ? ` · ${u.position}` : ''} · </>}
                    {activeCount} permission{activeCount === 1 ? '' : 's'} granted
                  </div>
                  {/* Identity verification (#8, 2026-09-23) */}
                  {u.verification_status === 'pending' ? (
                    <div className="flex items-center gap-2 mt-0.5">
                      <button onClick={() => viewIdDocument(u.id)} className="text-[9px] underline" style={{ color: 'var(--text-2)' }}>View ID</button>
                      <button onClick={() => reviewVerification(u.id, 'verified')} className="text-[9px] uppercase tracking-wide" style={{ color: 'var(--ok)' }}>Verify</button>
                      <button onClick={() => reviewVerification(u.id, 'rejected')} className="text-[9px] uppercase tracking-wide" style={{ color: 'var(--critical)' }}>Reject</button>
                    </div>
                  ) : u.verification_status === 'verified' ? (
                    <div className="text-[9px] mt-0.5 uppercase tracking-wide" style={{ color: 'var(--ok)' }}>ID verified</div>
                  ) : null}
                </div>

                <span
                  className="justify-self-start px-1.5 py-0.5 border text-[9px] font-bold uppercase tracking-wider"
                  style={{ color: 'var(--text-2)', borderColor: 'var(--line-2)' }}
                >
                  {u.role}
                </span>

                <span className="text-[11px] truncate" style={{ color: 'var(--text-2)' }}>
                  {u.assignment}
                </span>

                <div className="flex items-center justify-end gap-1.5">
                  <button
                    onClick={() => openPermissions(u)}
                    title="Edit permissions"
                    className="p-1.5 border transition-colors hover:bg-white/5"
                    style={{ borderColor: 'var(--line-2)', color: 'var(--text-2)' }}
                  >
                    <KeyRound size={12} />
                  </button>
                  <button
                    onClick={() => handleResetPassword(u)}
                    disabled={resetBusyId === u.id}
                    title="Reset password -- generates a new one, shown once"
                    className="p-1.5 border transition-colors hover:bg-white/5 disabled:opacity-40"
                    style={{ borderColor: 'var(--line-2)', color: 'var(--text-2)' }}
                  >
                    <RefreshCw size={12} className={resetBusyId === u.id ? 'animate-spin' : ''} />
                  </button>
                  <button
                    onClick={() => handleDelete(u.id)}
                    title="Remove user"
                    className="p-1.5 border transition-colors hover:bg-[rgba(229,52,47,0.12)]"
                    style={{ borderColor: 'var(--critical)', color: 'var(--critical)' }}
                  >
                    <Trash2 size={12} />
                  </button>
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* CREATE USER MODAL */}
      {showCreate && (
        <div className="fixed inset-0 z-[120] flex items-center justify-center p-4" style={{ background: 'rgba(0,0,0,0.72)' }}>
          <div className="border w-full max-w-sm" style={modalShell}>
            <div className="h-9 flex items-center justify-between px-3 border-b" style={{ borderColor: 'var(--line)' }}>
              <span className="label" style={{ color: 'var(--text)' }}>New User</span>
              <button onClick={() => setShowCreate(false)} title="Cancel" className="transition-colors hover:text-[var(--text)]" style={{ color: 'var(--text-3)' }}>
                <X size={15} />
              </button>
            </div>
            <form onSubmit={handleCreate} className="p-4 space-y-3">
              <div>
                <label className="label block mb-1.5">Username</label>
                <input
                  placeholder="Username" required
                  value={newUser.username}
                  onChange={e => setNewUser({ ...newUser, username: e.target.value })}
                  className="data w-full border p-2.5 text-[12px] text-[var(--text)] outline-none focus:border-[var(--accent)] transition-colors"
                  style={inputStyle}
                />
              </div>
              <div>
                <label className="label block mb-1.5">Password</label>
                <div className="relative">
                  <input
                    type={showNewPassword ? 'text' : 'password'} placeholder="Password" required
                    value={newUser.password}
                    onChange={e => setNewUser({ ...newUser, password: e.target.value })}
                    className="data w-full border p-2.5 pr-9 text-[12px] text-[var(--text)] outline-none focus:border-[var(--accent)] transition-colors"
                    style={inputStyle}
                  />
                  <button
                    type="button"
                    onClick={() => setShowNewPassword(s => !s)}
                    title={showNewPassword ? 'Hide password' : 'Show password'}
                    tabIndex={-1}
                    className="absolute right-2.5 top-1/2 -translate-y-1/2 transition-colors"
                    style={{ color: 'var(--text-3)' }}
                  >
                    {showNewPassword ? <EyeOff size={13} /> : <Eye size={13} />}
                  </button>
                </div>
              </div>
              {/* Personal record -- staff created here used to have none,
                  unlike every account DevTeam creates. */}
              <div>
                <label className="label block mb-1.5">Full name</label>
                <input
                  placeholder="e.g. Juan Dela Cruz" required
                  value={newUser.full_name}
                  onChange={e => setNewUser({ ...newUser, full_name: e.target.value })}
                  className="data w-full border p-2.5 text-[12px] text-[var(--text)] outline-none focus:border-[var(--accent)] transition-colors"
                  style={inputStyle}
                />
              </div>
              <div>
                <label className="label block mb-1.5">Position</label>
                <select
                  value={newUser.position}
                  onChange={e => setNewUser({ ...newUser, position: e.target.value })}
                  className="data w-full border p-2.5 text-[12px] text-[var(--text)] outline-none focus:border-[var(--accent)] transition-colors"
                  style={inputStyle}
                >
                  <option value="">select…</option>
                  {positionsForRole(myRole === 'PNP_ADMIN' ? 'PNP_OFFICER' : 'BARANGAY_STAFF').map(p => <option key={p} value={p}>{p}</option>)}
                </select>
              </div>
              <div>
                <label className="label block mb-1.5">Assignment</label>
                <input
                  placeholder="e.g. Patrol Unit 3" required
                  value={newUser.assignment}
                  onChange={e => setNewUser({ ...newUser, assignment: e.target.value })}
                  className="data w-full border p-2.5 text-[12px] text-[var(--text)] outline-none focus:border-[var(--accent)] transition-colors"
                  style={inputStyle}
                />
              </div>
              {error && (
                <p className="text-[10px] font-bold uppercase tracking-wider" style={{ color: 'var(--critical)' }}>{error}</p>
              )}
              <button
                className="w-full py-2.5 text-[11px] font-bold uppercase tracking-wider text-white transition-opacity hover:opacity-90"
                style={{ background: 'var(--accent)' }}
              >
                Create account
              </button>
            </form>
          </div>
        </div>
      )}

      {/* RESET PASSWORD RESULT -- shown once, same convention as the DevTeam
          bootstrap credential. Closing this without copying it loses the
          password for good (the backend never stores or re-returns it). */}
      {resetResult && (
        <div className="fixed inset-0 z-[130] flex items-center justify-center p-4" style={{ background: 'rgba(0,0,0,0.72)' }}>
          <div className="border w-full max-w-sm" style={modalShell}>
            <div className="h-9 flex items-center justify-between px-3 border-b" style={{ borderColor: 'var(--line)' }}>
              <span className="label flex items-center gap-1.5" style={{ color: 'var(--text)' }}>
                <RefreshCw size={12} /> Password reset
              </span>
              <button
                onClick={() => { setResetResult(null); setCopied(false); }}
                title="Close"
                className="transition-colors hover:text-[var(--text)]"
                style={{ color: 'var(--text-3)' }}
              >
                <X size={15} />
              </button>
            </div>
            <div className="p-4 space-y-3">
              <p className="text-[10.5px] leading-relaxed" style={{ color: 'var(--text-2)' }}>
                New password for <span className="text-[var(--text)] font-bold">{resetResult.username}</span>.
                Shown once -- copy it now and hand it to them directly.
              </p>
              <div className="flex items-center gap-2">
                <code
                  className="flex-1 px-2.5 py-2 text-[12px] font-mono truncate border"
                  style={{ background: 'var(--bg)', borderColor: 'var(--line-2)', color: 'var(--ok)' }}
                >
                  {resetResult.password}
                </code>
                <button
                  onClick={() => {
                    navigator.clipboard?.writeText(resetResult.password);
                    setCopied(true);
                    setTimeout(() => setCopied(false), 2000);
                  }}
                  title="Copy to clipboard"
                  className="p-2 border transition-colors hover:bg-white/5 shrink-0"
                  style={{ borderColor: 'var(--line-2)', color: copied ? 'var(--ok)' : 'var(--text-2)' }}
                >
                  {copied ? <Check size={13} /> : <Copy size={13} />}
                </button>
              </div>
              <button
                onClick={() => { setResetResult(null); setCopied(false); }}
                className="w-full py-2.5 text-[10px] tracking-[0.12em] uppercase text-white"
                style={{ background: 'var(--accent)' }}
              >
                Done
              </button>
            </div>
          </div>
        </div>
      )}

      {/* PERMISSIONS MODAL */}
      {editingPerms && (
        <div className="fixed inset-0 z-[120] flex items-center justify-center p-4" style={{ background: 'rgba(0,0,0,0.72)' }}>
          <div className="border w-full max-w-sm" style={modalShell}>
            <div className="h-9 flex items-center justify-between px-3 border-b" style={{ borderColor: 'var(--line)' }}>
              <span className="label flex items-center gap-1.5" style={{ color: 'var(--text)' }}>
                <ShieldCheck size={12} style={{ color: 'var(--accent)' }} /> Permissions
              </span>
              <button onClick={() => setEditingPerms(null)} title="Cancel" className="transition-colors hover:text-[var(--text)]" style={{ color: 'var(--text-3)' }}>
                <X size={15} />
              </button>
            </div>

            <div className="p-4">
              <div className="data text-[11px] mb-3" style={{ color: 'var(--text-2)' }}>{editingPerms.username}</div>

              {permissionNoteFor(editingPerms.role) && (
                <p className="text-[10px] leading-relaxed mb-3" style={{ color: 'var(--text-3)' }}>{permissionNoteFor(editingPerms.role)}</p>
              )}

              <div className="space-y-px mb-4">
                {permissionRowsFor(editingPerms.role).map(p => (
                  <label
                    key={p.key}
                    title={p.status === 'always' ? 'Admin-tier accounts get this automatically.' : undefined}
                    className="flex items-center justify-between p-2.5 border transition-colors"
                    style={{
                      background: 'var(--panel-2)', borderColor: 'var(--line)',
                      cursor: p.status === 'editable' ? 'pointer' : 'not-allowed',
                      opacity: p.status === 'editable' ? 1 : 0.4,
                    }}
                  >
                    <span className="text-[11px]" style={{ color: 'var(--text)' }}>
                      {p.label}
                      {p.status === 'always' && <span className="ml-1.5 text-[9px] uppercase tracking-wide" style={{ color: 'var(--ok)' }}>automatic</span>}
                    </span>
                    <input
                      type="checkbox"
                      checked={p.status === 'always' ? true : !!permsDraft[p.key]}
                      disabled={p.status !== 'editable'}
                      onChange={e => setPermsDraft({ ...permsDraft, [p.key]: e.target.checked })}
                      className="w-4 h-4"
                      style={{ accentColor: 'var(--accent)' }}
                    />
                  </label>
                ))}
              </div>

              <button
                onClick={savePermissions}
                className="w-full py-2.5 text-[11px] font-bold uppercase tracking-wider text-white transition-opacity hover:opacity-90 flex items-center justify-center gap-2"
                style={{ background: 'var(--accent)' }}
              >
                <Save size={12} /> Save permissions
              </button>

              {/* CAMERA-LEVEL ACCESS -- Phase 2, 2026-09-23. Independent of
                  the checkbox list above (applies/saves immediately, not on
                  the Save button) since it's a live restriction, not a
                  draft. Leaving this empty changes nothing -- see
                  DevteamView's Permissions tab for the same "opt-in only"
                  note. */}
              <div className="mt-4 pt-4 border-t" style={{ borderColor: 'var(--line)' }}>
                <div className="label mb-1.5">Camera-level access</div>
                <p className="text-[9.5px] leading-relaxed mb-2" style={{ color: 'var(--text-3)' }}>
                  Restrict this account to specific cameras only. No grants below means they see
                  every camera your account can see.
                </p>
                {userGrants.length > 0 && (
                  <div className="space-y-1 mb-2">
                    {userGrants.map(g => {
                      const cam = grantCameras.find(c => c.id === g.resource_id);
                      return (
                        <div key={g.id} className="flex items-center justify-between px-2 py-1.5 border" style={{ borderColor: 'var(--line-2)' }}>
                          <span className="text-[10.5px]" style={{ color: 'var(--text)' }}>{cam ? cam.name : g.resource_id}</span>
                          <button
                            onClick={() => revokeCameraAccess(g)}
                            disabled={grantBusy}
                            className="text-[9px] uppercase tracking-wider disabled:opacity-40"
                            style={{ color: 'var(--critical)' }}
                          >
                            Revoke
                          </button>
                        </div>
                      );
                    })}
                  </div>
                )}
                <div className="flex items-center gap-1.5">
                  <select
                    value={grantCameraId}
                    onChange={e => setGrantCameraId(e.target.value)}
                    className="flex-1 data border p-2 text-[11px] text-[var(--text)] outline-none focus:border-[var(--accent)] transition-colors"
                    style={inputStyle}
                  >
                    <option value="">select a camera…</option>
                    {grantCameras.map(c => (
                      <option key={c.id} value={c.id}>{c.name}</option>
                    ))}
                  </select>
                  <button
                    onClick={grantCameraAccess}
                    disabled={grantBusy || !grantCameraId}
                    className="px-3 py-2 text-[10px] font-bold uppercase tracking-wider text-white disabled:opacity-40 transition-opacity hover:opacity-90 shrink-0"
                    style={{ background: 'var(--accent)' }}
                  >
                    Grant
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}