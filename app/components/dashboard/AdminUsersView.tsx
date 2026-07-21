"use client";

import React, { useState } from 'react';
import { Users, UserPlus, Trash2, ShieldCheck, X, Save, KeyRound } from 'lucide-react';
import { useLiveChannel } from '../../context/WebSocketContext';
import { SkeletonList } from './Skeleton';

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

type ManagedUser = {
  id: number;
  username: string;
  role: string;
  barangay_id: string;
  assignment: string;
  parent_admin_id: number | null;
  permissions: string; // JSON string from backend
};

const PERMISSION_KEYS = [
  { key: "view_map", label: "View Crime Map" },
  { key: "view_records", label: "View Video Records" },
  { key: "view_history", label: "View Crime History" },
  { key: "manage_cameras", label: "Manage Cameras" },
  { key: "confirm_dismiss_alerts", label: "Confirm / Dismiss Alerts" },
];

function authHeaders() {
  const token = typeof window !== "undefined" ? localStorage.getItem("ecoToken") : null;
  return { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) };
}

export default function AdminUsersView() {
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [newUser, setNewUser] = useState({ username: '', password: '', assignment: '' });
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
    const updatedPermsJson = JSON.stringify(permsDraft);
    setUsers(prev => prev.map(u => u.id === editingPerms.id ? { ...u, permissions: updatedPermsJson } : u));
    setEditingPerms(null);
    try {
      const res = await fetch(`${API_URL}/api/admin/users/${editingPerms.id}/permissions`, {
        method: "PATCH",
        headers: authHeaders(),
        body: JSON.stringify({ permissions: permsDraft }),
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

  return (
    <div className="bg-[#11141b] border border-white/5 rounded-[2.5rem] p-8 shadow-2xl h-full flex flex-col min-h-[500px] animate-in fade-in duration-300 w-full">
      <div className="flex items-center justify-between pb-6 border-b border-white/5 mb-6">
        <div>
          <h3 className="text-sm font-bold uppercase tracking-[0.15em] text-white flex items-center gap-2">
            <Users size={16} className="text-emerald-500" /> Manage My Users
          </h3>
          <p className="text-[9px] font-mono text-slate-500 uppercase mt-0.5">
            Accounts you created only -- permissions apply to their access within your location
          </p>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          className="flex items-center gap-2 bg-emerald-500 hover:bg-emerald-400 text-black px-4 py-2.5 rounded-xl text-[10px] font-bold uppercase tracking-wider active:scale-95 transition-all"
        >
          <UserPlus size={14} /> New User
        </button>
      </div>

      {error && (
        <div className="mb-4 px-4 py-2 bg-red-500/10 border border-red-500/20 rounded-xl text-[10px] font-bold uppercase text-red-400 text-center">
          {error}
        </div>
      )}

      <div className="flex-1 overflow-y-auto custom-scrollbar space-y-3 pr-1">
        {isLoading ? (
          <SkeletonList rows={4} />
        ) : users.length === 0 ? (
          <div className="h-48 flex flex-col items-center justify-center opacity-20 border-2 border-dashed border-white/5 rounded-3xl text-slate-500">
            <Users size={32} className="mb-2" />
            <span className="text-[10px] font-bold uppercase tracking-widest">No users created yet</span>
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
                className={`p-4 bg-black/20 border border-white/5 rounded-2xl flex items-center justify-between gap-4 transition-opacity ${isPending ? 'opacity-40 pointer-events-none' : ''}`}
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <h4 className="text-xs font-bold text-white font-mono truncate">{u.username}</h4>
                    <span className="px-2 py-0.5 bg-white/5 border border-white/10 text-emerald-400 rounded text-[8px] font-mono uppercase">{u.role}</span>
                  </div>
                  <p className="text-[10px] text-slate-500 font-mono mt-1">
                    {u.assignment} &middot; {activeCount} permission{activeCount === 1 ? '' : 's'} granted
                  </p>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <button
                    onClick={() => openPermissions(u)}
                    title="Edit Permissions"
                    className="p-2.5 bg-emerald-500/10 hover:bg-emerald-500 text-emerald-400 hover:text-black border border-emerald-500/20 rounded-xl transition-all"
                  >
                    <KeyRound size={14} />
                  </button>
                  <button
                    onClick={() => handleDelete(u.id)}
                    title="Remove User"
                    className="p-2.5 bg-rose-500/10 hover:bg-rose-500 text-rose-400 hover:text-black border border-rose-500/20 rounded-xl transition-all"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* CREATE USER MODAL */}
      {showCreate && (
        <div className="fixed inset-0 z-[120] flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm">
          <div className="bg-[#11141b] border border-white/10 rounded-2xl w-full max-w-sm shadow-2xl text-slate-200 p-6">
            <div className="flex items-center justify-between mb-6">
              <h3 className="text-sm font-bold uppercase tracking-wide text-white">New User</h3>
              <button onClick={() => setShowCreate(false)}><X size={18} className="text-slate-500 hover:text-white" /></button>
            </div>
            <form onSubmit={handleCreate} className="space-y-3">
              <input
                placeholder="Username" required
                value={newUser.username}
                onChange={e => setNewUser({ ...newUser, username: e.target.value })}
                className="w-full bg-black/40 border border-white/10 rounded-xl p-3 text-xs text-white outline-none focus:border-emerald-500 font-mono"
              />
              <input
                type="password" placeholder="Password" required
                value={newUser.password}
                onChange={e => setNewUser({ ...newUser, password: e.target.value })}
                className="w-full bg-black/40 border border-white/10 rounded-xl p-3 text-xs text-white outline-none focus:border-emerald-500 font-mono"
              />
              <input
                placeholder="Assignment (e.g. Patrol Unit 3)" required
                value={newUser.assignment}
                onChange={e => setNewUser({ ...newUser, assignment: e.target.value })}
                className="w-full bg-black/40 border border-white/10 rounded-xl p-3 text-xs text-white outline-none focus:border-emerald-500 font-mono"
              />
              {error && <p className="text-red-500 text-[9px] text-center uppercase font-bold">{error}</p>}
              <button className="w-full py-3 bg-emerald-500 text-black rounded-xl text-[10px] font-bold uppercase hover:bg-emerald-400 transition-all">
                Create Account
              </button>
            </form>
          </div>
        </div>
      )}

      {/* PERMISSIONS MODAL */}
      {editingPerms && (
        <div className="fixed inset-0 z-[120] flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm">
          <div className="bg-[#11141b] border border-white/10 rounded-2xl w-full max-w-sm shadow-2xl text-slate-200 p-6">
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-sm font-bold uppercase tracking-wide text-white flex items-center gap-2">
                <ShieldCheck size={16} className="text-emerald-500" /> Permissions
              </h3>
              <button onClick={() => setEditingPerms(null)}><X size={18} className="text-slate-500 hover:text-white" /></button>
            </div>
            <p className="text-[10px] text-slate-500 font-mono mb-4">{editingPerms.username}</p>
            <div className="space-y-2 mb-6">
              {PERMISSION_KEYS.map(p => (
                <label key={p.key} className="flex items-center justify-between p-3 bg-black/30 border border-white/5 rounded-xl cursor-pointer">
                  <span className="text-[11px] text-slate-300">{p.label}</span>
                  <input
                    type="checkbox"
                    checked={!!permsDraft[p.key]}
                    onChange={e => setPermsDraft({ ...permsDraft, [p.key]: e.target.checked })}
                    className="w-4 h-4 accent-emerald-500"
                  />
                </label>
              ))}
            </div>
            <button
              onClick={savePermissions}
              className="w-full py-3 bg-emerald-500 text-black rounded-xl text-[10px] font-bold uppercase hover:bg-emerald-400 transition-all flex items-center justify-center gap-2"
            >
              <Save size={12} /> Save Permissions
            </button>
          </div>
        </div>
      )}
    </div>
  );
}