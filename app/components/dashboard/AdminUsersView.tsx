"use client";

import React, { useState } from 'react';
import { Users, UserPlus, Trash2, ShieldCheck, X, Save, KeyRound, Eye, EyeOff, RefreshCw, Copy, Check, AlertTriangle } from 'lucide-react';
import { useLiveChannel } from '../../context/WebSocketContext';
import { useRuntimeConfig } from '../../hooks/useRuntimeConfig';
import { SkeletonList } from './Skeleton';
import { permissionNoteFor, onlyEditablePermissions } from '../../lib/permissions';
import PermissionTree, { ResourceScopes, scopeProblem, scopesForSave } from './devteam/PermissionTree';
import type { CameraRow } from './devteam/shared';
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
  resource_scopes?: ResourceScopes;
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
  // Asked in an in-app dialog (confirmDeleteUser), not window.confirm --
  // one click used to remove the account outright.
  const [confirmDeleteUser, setConfirmDeleteUser] = useState<ManagedUser | null>(null);
  const handleDelete = async (id: number) => {
    setConfirmDeleteUser(null);
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

  // One click used to replace the password straight away, locking the
  // person out until they're handed the new one. Confirmed in-app first.
  const [confirmResetUser, setConfirmResetUser] = useState<ManagedUser | null>(null);
  const handleResetPassword = async (u: ManagedUser) => {
    setConfirmResetUser(null);
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

  // ── Access editor (2026-10-01): the same diced tree DevTeam uses, so a
  // captain can give "View Crime Map, but only these cameras and these
  // crime types". Capped server-side at the admin's own reach.
  const [scopeCameras, setScopeCameras] = useState<CameraRow[]>([]);
  const [scopesDraft, setScopesDraft] = useState<ResourceScopes>({});
  const [accessError, setAccessError] = useState('');
  const [accessBusy, setAccessBusy] = useState(false);

  const openPermissions = (u: ManagedUser) => {
    setEditingPerms(u);
    setAccessError('');
    try {
      setPermsDraft(JSON.parse(u.permissions || "{}"));
    } catch {
      setPermsDraft({});
    }
    setScopesDraft({ ...(u.resource_scopes || {}) });
    fetch(`${API_URL}/api/cameras`, { headers: authHeaders() })
      .then(res => res.ok ? res.json() : [])
      .then(setScopeCameras)
      .catch(() => {});
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

  const savePermissions = async () => {
    if (!editingPerms) return;
    const problem = scopeProblem(editingPerms.role, permsDraft, scopesDraft);
    if (problem) { setAccessError(problem); return; }
    setAccessBusy(true);
    setAccessError('');
    try {
      const editablePerms = onlyEditablePermissions(editingPerms.role, permsDraft);
      const res = await fetch(`${API_URL}/api/admin/users/${editingPerms.id}/permissions`, {
        method: "PATCH", headers: authHeaders(), body: JSON.stringify({ permissions: editablePerms }),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        setAccessError(d.detail || 'Could not save permissions.');
        return;
      }
      const scopeRes = await fetch(`${API_URL}/api/admin/users/${editingPerms.id}/resource_scopes`, {
        method: 'PUT', headers: authHeaders(),
        body: JSON.stringify({ scopes: scopesForSave(editingPerms.role, permsDraft, scopesDraft) }),
      });
      if (!scopeRes.ok) {
        const d = await scopeRes.json().catch(() => ({}));
        setAccessError(d.detail || 'Permissions saved, but the access limits were not.');
        fetchUsers();
        return;
      }
      setEditingPerms(null);
      fetchUsers();
    } catch {
      setAccessError('Backend connection failure -- nothing was saved.');
    } finally {
      setAccessBusy(false);
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
                    onClick={() => setConfirmResetUser(u)}
                    disabled={resetBusyId === u.id}
                    title="Reset password -- generates a new one, shown once"
                    className="p-1.5 border transition-colors hover:bg-white/5 disabled:opacity-40"
                    style={{ borderColor: 'var(--line-2)', color: 'var(--text-2)' }}
                  >
                    <RefreshCw size={12} className={resetBusyId === u.id ? 'animate-spin' : ''} />
                  </button>
                  <button
                    onClick={() => setConfirmDeleteUser(u)}
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
          <div className="border w-full max-w-lg max-h-[90vh] flex flex-col" style={modalShell} role="dialog" aria-label="Edit access">
            <div className="shrink-0 h-9 flex items-center justify-between px-3 border-b" style={{ borderColor: 'var(--line)' }}>
              <span className="label flex items-center gap-1.5" style={{ color: 'var(--text)' }}>
                <ShieldCheck size={12} style={{ color: 'var(--accent)' }} /> Access
              </span>
              <button onClick={() => setEditingPerms(null)} title="Cancel" aria-label="Cancel" className="transition-colors hover:text-[var(--text)]" style={{ color: 'var(--text-3)' }}>
                <X size={15} />
              </button>
            </div>

            <div className="flex-1 overflow-y-auto custom-scrollbar p-4">
              <div className="text-[12px] font-bold" style={{ color: 'var(--text)' }}>{editingPerms.full_name || editingPerms.username}</div>
              <div className="data text-[10px] mb-3" style={{ color: 'var(--text-3)' }}>
                @{editingPerms.username}{editingPerms.position ? ` · ${editingPerms.position}` : ''}
              </div>
              <p className="text-[10px] leading-relaxed mb-3" style={{ color: 'var(--text-3)' }}>
                Tick what this account can use. Where a permission shows a limit, open it to choose exactly which
                cameras or crime types it covers — for example, the crime map for theft and vandalism only.
                {permissionNoteFor(editingPerms.role) ? ` ${permissionNoteFor(editingPerms.role)}` : ''}
              </p>
              <PermissionTree
                role={editingPerms.role}
                perms={permsDraft}
                onPermsChange={setPermsDraft}
                scopes={scopesDraft}
                onScopesChange={setScopesDraft}
                cameras={scopeCameras}
                cameraHint="No cameras in your area yet."
                subject={editingPerms.full_name || editingPerms.username}
              />
            </div>

            <div className="shrink-0 border-t p-3 space-y-2" style={{ borderColor: 'var(--line)' }}>
              {accessError && (
                <p className="text-[10px] font-bold uppercase tracking-wide" style={{ color: 'var(--critical)' }}>{accessError}</p>
              )}
              <div className="flex gap-2">
                <button
                  onClick={() => setEditingPerms(null)}
                  className="px-3 py-2.5 border text-[10px] font-bold uppercase tracking-wider hover:border-[var(--text-3)]"
                  style={{ borderColor: 'var(--line-2)', color: 'var(--text-2)' }}
                >
                  Cancel
                </button>
                <button
                  onClick={savePermissions}
                  disabled={accessBusy}
                  className="flex-1 py-2.5 text-[11px] font-bold uppercase tracking-wider text-white transition-opacity hover:opacity-90 disabled:opacity-50 flex items-center justify-center gap-2"
                  style={{ background: 'var(--accent)' }}
                >
                  <Save size={12} /> {accessBusy ? 'Saving…' : 'Save access'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* REMOVE ACCOUNT -- in-app confirmation */}
      {confirmDeleteUser && (
        <div className="fixed inset-0 z-[130] flex items-center justify-center p-4" style={{ background: 'rgba(0,0,0,0.72)' }}>
          <div className="border w-full max-w-sm" style={modalShell} role="alertdialog" aria-label="Remove account">
            <div className="h-9 flex items-center gap-2 px-3 border-b" style={{ borderColor: 'var(--line)' }}>
              <Trash2 size={13} style={{ color: 'var(--critical)' }} />
              <span className="label" style={{ color: 'var(--text)' }}>Remove account</span>
            </div>
            <div className="p-4 space-y-3">
              <p className="text-[12px] leading-relaxed" style={{ color: 'var(--text)' }}>
                Remove <b>{confirmDeleteUser.full_name || confirmDeleteUser.username}</b>
                <span className="data" style={{ color: 'var(--text-3)' }}> (@{confirmDeleteUser.username})</span>?
              </p>
              <p className="text-[11px] leading-relaxed" style={{ color: 'var(--text-2)' }}>
                They can no longer sign in. Their past actions stay in the audit log, and DevTeam can restore the account if this was a mistake.
              </p>
              <div className="flex justify-end gap-2 pt-1">
                <button
                  onClick={() => setConfirmDeleteUser(null)}
                  autoFocus
                  className="px-3 py-1.5 border text-[10px] font-bold uppercase tracking-wider hover:border-[var(--text-3)]"
                  style={{ borderColor: 'var(--line-2)', color: 'var(--text-2)' }}
                >
                  Cancel
                </button>
                <button
                  onClick={() => handleDelete(confirmDeleteUser.id)}
                  className="px-3 py-1.5 text-[10px] font-bold uppercase tracking-wider text-white hover:opacity-90"
                  style={{ background: 'var(--critical)' }}
                >
                  Remove account
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* RESET PASSWORD -- in-app confirmation */}
      {confirmResetUser && (
        <div className="fixed inset-0 z-[130] flex items-center justify-center p-4" style={{ background: 'rgba(0,0,0,0.72)' }}>
          <div className="border w-full max-w-sm" style={modalShell} role="alertdialog" aria-label="Reset password">
            <div className="h-9 flex items-center gap-2 px-3 border-b" style={{ borderColor: 'var(--line)' }}>
              <AlertTriangle size={13} style={{ color: 'var(--warn)' }} />
              <span className="label" style={{ color: 'var(--text)' }}>Reset password</span>
            </div>
            <div className="p-4 space-y-3">
              <p className="text-[12px] leading-relaxed" style={{ color: 'var(--text)' }}>
                Reset the password for <b>{confirmResetUser.full_name || confirmResetUser.username}</b>?
              </p>
              <ul className="text-[11px] leading-relaxed list-disc pl-4 space-y-1" style={{ color: 'var(--text-2)' }}>
                <li>Their current password stops working immediately.</li>
                <li>A new password is generated and shown to you once. Hand it to them in person.</li>
              </ul>
              <div className="flex justify-end gap-2 pt-1">
                <button
                  onClick={() => setConfirmResetUser(null)}
                  autoFocus
                  className="px-3 py-1.5 border text-[10px] font-bold uppercase tracking-wider hover:border-[var(--text-3)]"
                  style={{ borderColor: 'var(--line-2)', color: 'var(--text-2)' }}
                >
                  Cancel
                </button>
                <button
                  onClick={() => handleResetPassword(confirmResetUser)}
                  className="px-3 py-1.5 text-[10px] font-bold uppercase tracking-wider text-white hover:opacity-90"
                  style={{ background: 'var(--warn)' }}
                >
                  Reset password
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}