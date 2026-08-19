"use client";

import React, { useState } from 'react';
import { Users, UserPlus, Trash2, ShieldCheck, X, Save, KeyRound, Eye, EyeOff } from 'lucide-react';
import { useLiveChannel } from '../../context/WebSocketContext';
import { useRuntimeConfig } from '../../hooks/useRuntimeConfig';
import { SkeletonList } from './Skeleton';
import { permissionRowsFor, permissionNoteFor, onlyEditablePermissions } from '../../lib/permissions';

type ManagedUser = {
  id: number;
  username: string;
  role: string;
  barangay_id: string;
  assignment: string;
  parent_admin_id: number | null;
  permissions: string; // JSON string from backend
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
  const [newUser, setNewUser] = useState({ username: '', password: '', assignment: '' });
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
        setNewUser({ username: '', password: '', assignment: '' });
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

  const openPermissions = (u: ManagedUser) => {
    setEditingPerms(u);
    try {
      setPermsDraft(JSON.parse(u.permissions || "{}"));
    } catch {
      setPermsDraft({});
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
                  <div className="data text-[12px] font-bold text-white truncate">{u.username}</div>
                  <div className="text-[9px] mt-0.5" style={{ color: 'var(--text-3)' }}>
                    {activeCount} permission{activeCount === 1 ? '' : 's'} granted
                  </div>
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
              <button onClick={() => setShowCreate(false)} title="Cancel" className="transition-colors hover:text-white" style={{ color: 'var(--text-3)' }}>
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
                  className="data w-full border p-2.5 text-[12px] text-white outline-none focus:border-[var(--accent)] transition-colors"
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
                    className="data w-full border p-2.5 pr-9 text-[12px] text-white outline-none focus:border-[var(--accent)] transition-colors"
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
              <div>
                <label className="label block mb-1.5">Assignment</label>
                <input
                  placeholder="e.g. Patrol Unit 3" required
                  value={newUser.assignment}
                  onChange={e => setNewUser({ ...newUser, assignment: e.target.value })}
                  className="data w-full border p-2.5 text-[12px] text-white outline-none focus:border-[var(--accent)] transition-colors"
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

      {/* PERMISSIONS MODAL */}
      {editingPerms && (
        <div className="fixed inset-0 z-[120] flex items-center justify-center p-4" style={{ background: 'rgba(0,0,0,0.72)' }}>
          <div className="border w-full max-w-sm" style={modalShell}>
            <div className="h-9 flex items-center justify-between px-3 border-b" style={{ borderColor: 'var(--line)' }}>
              <span className="label flex items-center gap-1.5" style={{ color: 'var(--text)' }}>
                <ShieldCheck size={12} style={{ color: 'var(--accent)' }} /> Permissions
              </span>
              <button onClick={() => setEditingPerms(null)} title="Cancel" className="transition-colors hover:text-white" style={{ color: 'var(--text-3)' }}>
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
                    title={p.status === 'banned' ? 'The backend refuses this for every PNP account, any tier — checking it would not do anything.' : p.status === 'always' ? 'Admin-tier accounts get this automatically.' : undefined}
                    className="flex items-center justify-between p-2.5 border transition-colors"
                    style={{
                      background: 'var(--panel-2)', borderColor: 'var(--line)',
                      cursor: p.status === 'editable' ? 'pointer' : 'not-allowed',
                      opacity: p.status === 'editable' ? 1 : 0.4,
                    }}
                  >
                    <span className="text-[11px]" style={{ color: 'var(--text)' }}>
                      {p.label}
                      {p.status === 'banned' && <span className="ml-1.5 text-[9px] uppercase tracking-wide" style={{ color: 'var(--critical)' }}>locked</span>}
                      {p.status === 'always' && <span className="ml-1.5 text-[9px] uppercase tracking-wide" style={{ color: 'var(--ok)' }}>automatic</span>}
                    </span>
                    <input
                      type="checkbox"
                      checked={p.status === 'always' ? true : p.status === 'banned' ? false : !!permsDraft[p.key]}
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
            </div>
          </div>
        </div>
      )}
    </div>
  );
}