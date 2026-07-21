"use client";

import React, { useState, useMemo } from 'react';
import {
  ShieldAlert, Wifi, WifiOff, ShieldCheck, ShieldX, UserCheck,
  Pencil, Trash2, X, Save, Search, LogOut, KeyRound, Users2, MapPinned,
  Activity, Video, Film, Radio, LayoutGrid, ClipboardList, UserPlus, ChevronDown
} from 'lucide-react';
import { useLiveChannel, useWebSocketContext } from '../../context/WebSocketContext';

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const PERMISSION_KEYS = [
  { key: "view_map", label: "View Crime Map" },
  { key: "view_records", label: "View Video Records" },
  { key: "view_history", label: "View Crime History" },
  { key: "manage_cameras", label: "Manage Cameras" },
  { key: "confirm_dismiss_alerts", label: "Confirm / Dismiss Alerts" },
];

const CREATABLE_ROLES = [
  { role: 'PRECINCT_CAPTAIN', code: 'PD', label: 'Precinct Captain', needsLocation: true },
  { role: 'BARANGAY_CAPTAIN', code: 'BG', label: 'Barangay Captain', needsLocation: true },
  { role: 'POLICE', code: 'PD', label: 'Police', needsLocation: true },
  { role: 'BARANGAY', code: 'BG', label: 'Barangay', needsLocation: true },
];

// Two operating branches, distinguished the way a dispatch board would:
// a callsign-style two-letter code and a single accent, nothing more.
const ROLE_STYLES: Record<string, { code: string; text: string; border: string; bg: string; barText: string }> = {
  PRECINCT_CAPTAIN: { code: 'PD', text: 'text-[#8FA8D9]', border: 'border-[#8FA8D9]/25', bg: 'bg-[#8FA8D9]/[0.07]', barText: 'text-[#8FA8D9]' },
  BARANGAY_CAPTAIN: { code: 'BG', text: 'text-[#6FBF8F]', border: 'border-[#6FBF8F]/25', bg: 'bg-[#6FBF8F]/[0.07]', barText: 'text-[#6FBF8F]' },
  POLICE: { code: 'PD', text: 'text-[#8FA8D9]/70', border: 'border-[#8FA8D9]/15', bg: 'bg-[#8FA8D9]/[0.04]', barText: 'text-[#8FA8D9]/70' },
  BARANGAY: { code: 'BG', text: 'text-[#6FBF8F]/70', border: 'border-[#6FBF8F]/15', bg: 'bg-[#6FBF8F]/[0.04]', barText: 'text-[#6FBF8F]/70' },
};

function authHeaders() {
  const token = typeof window !== "undefined" ? localStorage.getItem("ecoToken") : null;
  return { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) };
}

function initials(name: string) {
  return name.slice(0, 2).toUpperCase();
}

type ManagedUser = {
  id: number;
  username: string;
  role: string;
  barangay_id: string;
  assignment: string;
  parent_admin_id: number | null;
  permissions: string;
};

type PendingLocation = {
  id: string;
  name: string;
  status?: string;
  requester_username: string | null;
  requester_role: string | null;
  requester_assignment: string | null;
  created_at: string;
};

type Tab = 'directory' | 'approvals' | 'create';

export default function DevteamView() {
  const [data, setData] = useState<any>(null);
  const [pendingLocations, setPendingLocations] = useState<PendingLocation[]>([]);
  const [allLocations, setAllLocations] = useState<PendingLocation[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);
  const [tab, setTab] = useState<Tab>('directory');
  const [selectedAdminId, setSelectedAdminId] = useState<number | null>(null);
  const [search, setSearch] = useState('');
  const [editingUser, setEditingUser] = useState<ManagedUser | null>(null);
  const [editDraft, setEditDraft] = useState({ username: '', assignment: '', password: '' });
  const [permsDraft, setPermsDraft] = useState<Record<string, boolean>>({});
  const [pendingActionIds, setPendingActionIds] = useState<Set<string | number>>(new Set());
  const [toast, setToast] = useState('');
  const { connected } = useWebSocketContext();

  // CREATE USER TAB — DevTeam can mint any role directly (PRECINCT_CAPTAIN,
  // BARANGAY_CAPTAIN, POLICE, BARANGAY), skip the pending-approval signup
  // flow entirely, and grant permissions from the same tree admins use for
  // their own sub-accounts.
  const [createForm, setCreateForm] = useState({
    username: '', password: '', assignment: '', display_title: '',
    role: 'PRECINCT_CAPTAIN', barangay_id: '', parent_admin_id: '' as string,
  });
  const [createPerms, setCreatePerms] = useState<Record<string, boolean>>({});
  const [createBusy, setCreateBusy] = useState(false);
  const [createError, setCreateError] = useState('');
  const [locPickerOpen, setLocPickerOpen] = useState(false);

  const fetchOverview = async () => {
    try {
      const [overviewRes, locationsRes, allLocationsRes] = await Promise.all([
        fetch(`${API_URL}/api/devteam/overview`, { headers: authHeaders() }),
        fetch(`${API_URL}/api/devteam/locations?status=pending`, { headers: authHeaders() }),
        fetch(`${API_URL}/api/devteam/locations`, { headers: authHeaders() }),
      ]);
      if (overviewRes.ok && locationsRes.ok) {
        setData(await overviewRes.json());
        setPendingLocations(await locationsRes.json());
        if (allLocationsRes.ok) setAllLocations(await allLocationsRes.json());
        setLoadFailed(false);
      } else {
        setLoadFailed(true);
      }
    } catch {
      setLoadFailed(true);
    } finally {
      setIsLoading(false);
    }
  };

  useLiveChannel("*", fetchOverview);

  const flash = (msg: string) => { setToast(msg); setTimeout(() => setToast(''), 3000); };

  const handleLogout = () => {
    const token = localStorage.getItem('ecoToken');
    fetch(`${API_URL}/api/logout`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    }).catch(() => {});
    localStorage.removeItem('ecoUser');
    localStorage.removeItem('ecoToken');
    window.location.href = '/loginpage/login';
  };

  const handleApproval = async (barangayId: string, decision: 'approve' | 'reject') => {
    const snapshot = pendingLocations;
    setPendingLocations(prev => prev.filter(l => l.id !== barangayId));
    setPendingActionIds(prev => new Set(prev).add(barangayId));
    try {
      const res = await fetch(`${API_URL}/api/devteam/locations/${barangayId}/${decision}`, {
        method: "POST", headers: authHeaders(), body: JSON.stringify({}),
      });
      if (!res.ok) { setPendingLocations(snapshot); flash(`Could not ${decision}.`); }
      else { fetchOverview(); flash(`Location ${decision}d.`); }
    } catch {
      setPendingLocations(snapshot); flash('Backend connection failure.');
    } finally {
      setPendingActionIds(prev => { const n = new Set(prev); n.delete(barangayId); return n; });
    }
  };

  const openEdit = (u: ManagedUser) => {
    setEditingUser(u);
    setEditDraft({ username: u.username, assignment: u.assignment, password: '' });
    try { setPermsDraft(JSON.parse(u.permissions || "{}")); } catch { setPermsDraft({}); }
  };

  const saveEdit = async () => {
    if (!editingUser) return;
    const id = editingUser.id;
    const body: any = { username: editDraft.username, assignment: editDraft.assignment };
    if (editDraft.password.trim()) body.password = editDraft.password.trim();
    setEditingUser(null);
    try {
      const [editRes, permsRes] = await Promise.all([
        fetch(`${API_URL}/api/devteam/users/${id}`, { method: "PATCH", headers: authHeaders(), body: JSON.stringify(body) }),
        fetch(`${API_URL}/api/admin/users/${id}/permissions`, { method: "PATCH", headers: authHeaders(), body: JSON.stringify({ permissions: permsDraft }) }),
      ]);
      if (editRes.ok && permsRes.ok) { fetchOverview(); flash('Account updated.'); }
      else { flash('Some changes failed to save.'); }
    } catch {
      flash('Backend connection failure.');
    }
  };

  const handleDelete = async (u: ManagedUser) => {
    setPendingActionIds(prev => new Set(prev).add(u.id));
    try {
      const res = await fetch(`${API_URL}/api/devteam/users/${u.id}`, { method: "DELETE", headers: authHeaders() });
      if (res.ok) { fetchOverview(); flash(`${u.username} removed.`); }
      else { const d = await res.json().catch(() => ({})); flash(d.detail || 'Delete failed.'); }
    } catch {
      flash('Backend connection failure.');
    } finally {
      setPendingActionIds(prev => { const n = new Set(prev); n.delete(u.id); return n; });
    }
  };

  const resetCreateForm = () => {
    setCreateForm({ username: '', password: '', assignment: '', display_title: '', role: 'PRECINCT_CAPTAIN', barangay_id: '', parent_admin_id: '' });
    setCreatePerms({});
    setCreateError('');
  };

  const handleCreateUser = async () => {
    setCreateError('');
    if (!createForm.username.trim() || !createForm.password.trim() || !createForm.assignment.trim()) {
      setCreateError('Username, password, and assignment are required.');
      return;
    }
    const roleMeta = CREATABLE_ROLES.find(r => r.role === createForm.role);
    if (roleMeta?.needsLocation && !createForm.barangay_id.trim()) {
      setCreateError('A location is required for this role.');
      return;
    }
    setCreateBusy(true);
    try {
      const res = await fetch(`${API_URL}/api/devteam/users`, {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({
          username: createForm.username.trim(),
          password: createForm.password,
          role: createForm.role,
          barangay_id: createForm.barangay_id.trim().toLowerCase() || null,
          assignment: createForm.assignment.trim(),
          display_title: createForm.display_title.trim() || null,
          parent_admin_id: createForm.parent_admin_id ? Number(createForm.parent_admin_id) : null,
          permissions: createPerms,
        }),
      });
      const d = await res.json().catch(() => ({}));
      if (res.ok) {
        flash(`${createForm.username} created and connected to ${createForm.barangay_id || 'no location'}.`);
        resetCreateForm();
        fetchOverview();
        setTab('directory');
      } else {
        setCreateError(d.detail || 'Could not create account.');
      }
    } catch {
      setCreateError('Backend connection failure.');
    } finally {
      setCreateBusy(false);
    }
  };

  const { admins, childrenByAdmin, selectedAdmin, selectedChildren } = useMemo(() => {
    if (!data) return { admins: [], childrenByAdmin: new Map(), selectedAdmin: null, selectedChildren: [] };
    const users: ManagedUser[] = data.users;
    let adminList = users.filter(u => u.role === 'PRECINCT_CAPTAIN' || u.role === 'BARANGAY_CAPTAIN');
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      adminList = adminList.filter(a => a.username.toLowerCase().includes(q) || a.barangay_id?.toLowerCase().includes(q));
    }
    const map = new Map<number, ManagedUser[]>();
    users.filter(u => u.role === 'PRECINCT_CAPTAIN' || u.role === 'BARANGAY_CAPTAIN')
      .forEach(a => map.set(a.id, users.filter(u => u.parent_admin_id === a.id)));
    const sel = adminList.find(a => a.id === selectedAdminId) || adminList[0] || null;
    return { admins: adminList, childrenByAdmin: map, selectedAdmin: sel, selectedChildren: sel ? (map.get(sel.id) || []) : [] };
  }, [data, selectedAdminId, search]);

  // Every barangay's two captain slots, side by side — makes the
  // "one location, two connected accounts" relationship visible instead
  // of implicit in a shared barangay_id column.
  const locationPairs = useMemo(() => {
    if (!data) return [];
    const users: ManagedUser[] = data.users;
    const byLoc = new Map<string, { precinct?: ManagedUser; barangay?: ManagedUser }>();
    users.forEach(u => {
      if (u.role !== 'PRECINCT_CAPTAIN' && u.role !== 'BARANGAY_CAPTAIN') return;
      const key = u.barangay_id || '—';
      const entry = byLoc.get(key) || {};
      if (u.role === 'PRECINCT_CAPTAIN') entry.precinct = u; else entry.barangay = u;
      byLoc.set(key, entry);
    });
    return Array.from(byLoc.entries()).map(([loc, pair]) => ({ loc, ...pair }));
  }, [data]);

  const knownLocationIds = useMemo(() => {
    const set = new Set<string>();
    allLocations.forEach(l => set.add(l.id));
    (data?.users || []).forEach((u: ManagedUser) => u.barangay_id && set.add(u.barangay_id));
    return Array.from(set).sort();
  }, [allLocations, data]);

  const eligibleParents = useMemo(() => {
    if (!data) return [];
    const roleMeta = CREATABLE_ROLES.find(r => r.role === createForm.role);
    if (!roleMeta || roleMeta.role === 'PRECINCT_CAPTAIN' || roleMeta.role === 'BARANGAY_CAPTAIN') return [];
    const wantCaptainRole = roleMeta.role === 'POLICE' ? 'PRECINCT_CAPTAIN' : 'BARANGAY_CAPTAIN';
    return (data.users as ManagedUser[]).filter(u => u.role === wantCaptainRole && (!createForm.barangay_id || u.barangay_id === createForm.barangay_id.trim().toLowerCase()));
  }, [data, createForm.role, createForm.barangay_id]);

  if (isLoading) {
    return (
      <div className="fixed inset-0 bg-[#111214] flex items-center justify-center font-mono">
        <div className="flex flex-col items-center gap-4">
          <div className="w-8 h-8 border border-[#2B2D31] border-t-[#8FA8D9] animate-spin" />
          <span className="text-[10px] tracking-[0.25em] text-[#6B6D73] uppercase">Establishing link</span>
        </div>
      </div>
    );
  }

  if (!data || loadFailed) {
    return (
      <div className="fixed inset-0 bg-[#111214] flex flex-col items-center justify-center gap-4 font-mono">
        <ShieldAlert size={22} className="text-[#D9756A]" />
        <span className="text-[10px] tracking-[0.25em] text-[#D9756A] uppercase">Console link failed</span>
        <button onClick={fetchOverview} className="mt-1 px-5 py-2 border border-[#D9756A]/40 hover:border-[#D9756A] text-[10px] tracking-[0.2em] uppercase text-[#D9756A] transition-colors">
          Retry connection
        </button>
      </div>
    );
  }

  return (
    <div className="fixed inset-0 bg-[#111214] text-[#C4C6CC] flex flex-col overflow-hidden z-40 font-mono">
      {/* HEADER — dispatch console strip, not a hero */}
      <div className="relative flex items-center justify-between px-7 py-4 shrink-0 border-b border-[#2B2D31]">
        <div className="flex items-center gap-3">
          <div className="p-1.5 border border-[#8FA8D9]/30 bg-[#8FA8D9]/10">
            <Radio size={14} className="text-[#8FA8D9]" />
          </div>
          <div className="leading-tight">
            <h1 className="text-[11px] tracking-[0.2em] uppercase text-[#F0F1F3]">Oversight Console</h1>
            <p className="text-[9px] tracking-[0.15em] text-[#6B6D73] uppercase">All locations &middot; full authority</p>
          </div>
        </div>
        <div className="flex items-center gap-5">
          <div className={`flex items-center gap-1.5 text-[9px] tracking-[0.15em] uppercase ${connected ? 'text-[#6FBF8F]' : 'text-[#D9756A]'}`}>
            {connected ? <Wifi size={11} /> : <WifiOff size={11} />} {connected ? 'Synced' : 'Reconnecting'}
          </div>
          <button onClick={handleLogout} className="flex items-center gap-1.5 text-[9px] tracking-[0.15em] uppercase text-[#6B6D73] hover:text-[#D9756A] transition-colors">
            <LogOut size={12} /> Sign out
          </button>
        </div>
      </div>

      {toast && (
        <div className="shrink-0 text-[10px] tracking-[0.1em] text-[#8FA8D9] border-b border-[#2B2D31] bg-[#8FA8D9]/[0.04] px-7 py-1.5">
          &gt; {toast}
        </div>
      )}

      {/* STAT STRIP — inline ledger, not cards */}
      <div className="shrink-0 flex items-stretch border-b border-[#2B2D31] px-7">
        <StatCell icon={<Users2 size={13} />} label="Users" val={data.totals.users} />
        <StatCell icon={<ShieldAlert size={13} />} label="Incidents" val={data.totals.incidents} />
        <StatCell icon={<Activity size={13} />} label="Active" val={data.totals.active_incidents} accent="text-[#D9756A]" />
        <StatCell icon={<Video size={13} />} label="Cameras" val={data.totals.cameras} />
        <StatCell icon={<Film size={13} />} label="Records" val={data.totals.video_records} last />
      </div>

      {/* TABS */}
      <div className="shrink-0 flex items-center gap-1 px-7 border-b border-[#2B2D31]">
        <TabButton icon={<LayoutGrid size={12} />} label="Directory" active={tab === 'directory'} onClick={() => setTab('directory')} />
        <TabButton
          icon={<ClipboardList size={12} />}
          label="Approvals"
          active={tab === 'approvals'}
          onClick={() => setTab('approvals')}
          badge={pendingLocations.length}
        />
        <TabButton icon={<UserPlus size={12} />} label="Create User" active={tab === 'create'} onClick={() => setTab('create')} />
      </div>

      {/* ================= DIRECTORY TAB ================= */}
      {tab === 'directory' && (
        <div className="flex-1 min-h-0 grid grid-cols-12 gap-0 px-7 pb-7 pt-4">
          <div className="col-span-4 flex flex-col border border-[#2B2D31] border-r-0">
            <div className="px-3 py-2.5 border-b border-[#2B2D31] flex items-center gap-2">
              <Search size={12} className="text-[#6B6D73] shrink-0" />
              <input
                value={search}
                onChange={e => setSearch(e.target.value)}
                placeholder="search callsign or location"
                className="bg-transparent text-[11px] text-[#F0F1F3] outline-none w-full placeholder:text-[#4A4C50]"
              />
            </div>
            <div className="flex-1 overflow-y-auto custom-scrollbar">
              {admins.length === 0 ? (
                <p className="text-[10px] tracking-[0.15em] uppercase text-[#4A4C50] text-center py-10">No matching admins</p>
              ) : admins.map(a => {
                const count = (childrenByAdmin.get(a.id) || []).length;
                const active = selectedAdmin?.id === a.id;
                const style = ROLE_STYLES[a.role];
                return (
                  <button
                    key={a.id}
                    onClick={() => setSelectedAdminId(a.id)}
                    className={`w-full flex items-center gap-3 px-3 py-2.5 text-left border-b border-[#1E2023] transition-colors ${active ? 'bg-[#18191C]' : 'hover:bg-[#151517]'}`}
                  >
                    <span className={`text-[8px] font-bold px-1.5 py-1 border ${style.border} ${style.text} shrink-0`}>{style.code}</span>
                    <div className="min-w-0 flex-1">
                      <p className={`text-[11px] truncate ${active ? 'text-[#F0F1F3]' : 'text-[#C4C6CC]'}`}>{a.username}</p>
                      <p className="text-[9px] text-[#6B6D73] truncate">{a.barangay_id} &middot; {count} sub-account{count === 1 ? '' : 's'}</p>
                    </div>
                    {active && <span className="w-1 h-1 rounded-full bg-[#8FA8D9] shrink-0" />}
                  </button>
                );
              })}
            </div>
          </div>

          <div className="col-span-8 border border-[#2B2D31] overflow-y-auto custom-scrollbar">
            {!selectedAdmin ? (
              <div className="h-full flex items-center justify-center">
                <p className="text-[10px] tracking-[0.15em] uppercase text-[#4A4C50]">Select an admin from the directory</p>
              </div>
            ) : (
              <div className="p-6">
                <div className="flex items-start justify-between mb-6 pb-5 border-b border-[#1E2023]">
                  <div className="flex items-center gap-4">
                    <span className={`text-[10px] font-bold px-2 py-1.5 border ${ROLE_STYLES[selectedAdmin.role].border} ${ROLE_STYLES[selectedAdmin.role].text}`}>
                      {ROLE_STYLES[selectedAdmin.role].code}
                    </span>
                    <div>
                      <h2 className="text-[13px] text-[#F0F1F3] tracking-wide">{selectedAdmin.username}</h2>
                      <p className="text-[10px] text-[#6B6D73] mt-1 flex items-center gap-1 tracking-wide">
                        <MapPinned size={10} /> {selectedAdmin.assignment} &middot; {selectedAdmin.barangay_id}
                      </p>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <button onClick={() => openEdit(selectedAdmin)} className="flex items-center gap-1.5 px-3 py-1.5 border border-[#2B2D31] hover:border-[#8FA8D9]/40 text-[#6B6D73] hover:text-[#8FA8D9] transition-colors text-[9px] tracking-[0.15em] uppercase">
                      <Pencil size={11} /> Edit
                    </button>
                    <button onClick={() => handleDelete(selectedAdmin)} className="flex items-center gap-1.5 px-3 py-1.5 border border-[#2B2D31] hover:border-[#D9756A]/40 text-[#6B6D73] hover:text-[#D9756A] transition-colors text-[9px] tracking-[0.15em] uppercase">
                      <Trash2 size={11} /> Remove
                    </button>
                  </div>
                </div>

                {/* CONNECTED COUNTERPART — the other captain sharing this
                    location, surfaced explicitly instead of left implicit. */}
                {(() => {
                  const pair = locationPairs.find(p => p.loc === selectedAdmin.barangay_id);
                  const counterpart = selectedAdmin.role === 'PRECINCT_CAPTAIN' ? pair?.barangay : pair?.precinct;
                  return (
                    <div className="mb-6 flex items-center gap-3 px-3 py-2.5 border border-dashed border-[#2B2D31]">
                      <span className="text-[8px] tracking-[0.15em] uppercase text-[#6B6D73] shrink-0">Connected at {selectedAdmin.barangay_id}</span>
                      {counterpart ? (
                        <span className={`text-[10px] px-2 py-0.5 border ${ROLE_STYLES[counterpart.role].border} ${ROLE_STYLES[counterpart.role].text}`}>
                          {ROLE_STYLES[counterpart.role].code} &middot; {counterpart.username}
                        </span>
                      ) : (
                        <span className="text-[9px] text-[#4A4C50] uppercase tracking-wide">No counterpart yet — vacant</span>
                      )}
                    </div>
                  );
                })()}

                <div className="flex items-center gap-2 mb-3">
                  <Users2 size={11} className="text-[#6B6D73]" />
                  <span className="text-[9px] tracking-[0.2em] uppercase text-[#6B6D73]">Sub-accounts &middot; {selectedChildren.length}</span>
                </div>

                {selectedChildren.length === 0 ? (
                  <div className="border border-dashed border-[#2B2D31] py-10 text-center">
                    <p className="text-[10px] tracking-[0.15em] uppercase text-[#4A4C50]">No sub-accounts created by this admin yet</p>
                  </div>
                ) : (
                  <div className="border border-[#1E2023] divide-y divide-[#1E2023]">
                    {selectedChildren.map(c => {
                      const style = ROLE_STYLES[c.role] || ROLE_STYLES.POLICE;
                      return (
                        <div key={c.id} className={`flex items-center gap-3 px-3 py-2.5 transition-opacity ${pendingActionIds.has(c.id) ? 'opacity-40' : ''}`}>
                          <span className={`text-[8px] font-bold px-1.5 py-1 border ${style.border} ${style.text} shrink-0`}>{style.code}</span>
                          <div className="min-w-0 flex-1">
                            <p className="text-[11px] text-[#F0F1F3] truncate">{c.username}</p>
                            <p className="text-[9px] text-[#6B6D73] truncate">{c.assignment}</p>
                          </div>
                          <div className="flex items-center gap-1 shrink-0">
                            <button onClick={() => openEdit(c)} className="p-1.5 text-[#6B6D73] hover:text-[#8FA8D9] transition-colors"><Pencil size={12} /></button>
                            <button onClick={() => handleDelete(c)} className="p-1.5 text-[#6B6D73] hover:text-[#D9756A] transition-colors"><Trash2 size={12} /></button>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* ================= APPROVALS TAB ================= */}
      {tab === 'approvals' && (
        <div className="flex-1 min-h-0 overflow-y-auto custom-scrollbar px-7 pb-7 pt-4">
          <div className="border border-[#2B2D31]">
            <div className="flex items-center gap-2 px-4 py-2.5 border-b border-[#2B2D31] bg-[#8FA8D9]/[0.03]">
              <UserCheck size={12} className="text-[#8FA8D9]" />
              <span className="text-[9px] tracking-[0.2em] uppercase text-[#8FA8D9]">Awaiting verification</span>
              <span className="ml-auto text-[9px] text-[#8FA8D9]/70">{pendingLocations.length} pending</span>
            </div>
            {pendingLocations.length === 0 ? (
              <div className="py-14 text-center">
                <p className="text-[10px] tracking-[0.15em] uppercase text-[#4A4C50]">No captain signups waiting on review</p>
              </div>
            ) : (
              <div className="divide-y divide-[#1E2023]">
                {pendingLocations.map(loc => {
                  const busy = pendingActionIds.has(loc.id);
                  const roleMeta = ROLE_STYLES[loc.requester_role || ''] || ROLE_STYLES.POLICE;
                  return (
                    <div key={loc.id} className={`flex items-center justify-between gap-4 px-4 py-3.5 transition-opacity ${busy ? 'opacity-40' : ''}`}>
                      <div className="flex items-center gap-3 min-w-0">
                        <span className={`text-[8px] font-bold px-1.5 py-1 border shrink-0 ${roleMeta.border} ${roleMeta.text}`}>{roleMeta.code}</span>
                        <div className="min-w-0">
                          <p className="text-[11px] text-[#F0F1F3] truncate">{loc.requester_username} <span className="text-[#6B6D73]">requests</span> {loc.name}</p>
                          <p className="text-[9px] text-[#6B6D73] flex items-center gap-1">
                            <MapPinned size={9} /> {loc.requester_role} &middot; {loc.requester_assignment} &middot; {new Date(loc.created_at).toLocaleDateString()}
                          </p>
                        </div>
                      </div>
                      <div className="flex items-center gap-1 shrink-0">
                        <button onClick={() => handleApproval(loc.id, 'reject')} className="p-1.5 border border-transparent hover:border-[#D9756A]/40 text-[#6B6D73] hover:text-[#D9756A] transition-colors"><ShieldX size={13} /></button>
                        <button onClick={() => handleApproval(loc.id, 'approve')} className="p-1.5 border border-transparent hover:border-[#6FBF8F]/40 text-[#6B6D73] hover:text-[#6FBF8F] transition-colors"><ShieldCheck size={13} /></button>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          <div className="mt-6 border border-[#2B2D31]">
            <div className="flex items-center gap-2 px-4 py-2.5 border-b border-[#2B2D31]">
              <MapPinned size={12} className="text-[#6B6D73]" />
              <span className="text-[9px] tracking-[0.2em] uppercase text-[#6B6D73]">Locations &amp; their two captain seats</span>
            </div>
            <div className="divide-y divide-[#1E2023]">
              {locationPairs.length === 0 ? (
                <div className="py-10 text-center">
                  <p className="text-[10px] tracking-[0.15em] uppercase text-[#4A4C50]">No locations with captains yet</p>
                </div>
              ) : locationPairs.map(p => (
                <div key={p.loc} className="flex items-center gap-4 px-4 py-3">
                  <span className="text-[10px] text-[#F0F1F3] w-28 shrink-0 truncate uppercase tracking-wide">{p.loc}</span>
                  <SeatChip user={p.precinct} code="PD" />
                  <SeatChip user={p.barangay} code="BG" />
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* ================= CREATE USER TAB ================= */}
      {tab === 'create' && (
        <div className="flex-1 min-h-0 overflow-y-auto custom-scrollbar px-7 pb-7 pt-4">
          <div className="max-w-2xl border border-[#2B2D31] p-6">
            <div className="flex items-center gap-2 mb-1">
              <UserPlus size={13} className="text-[#8FA8D9]" />
              <h2 className="text-[11px] tracking-[0.2em] uppercase text-[#F0F1F3]">Create account — any role</h2>
            </div>
            <p className="text-[9px] text-[#6B6D73] tracking-wide mb-5">
              Skips the self-signup approval queue. Connects the account to a location and grants permissions from the same tree admins use.
            </p>

            <div className="grid grid-cols-2 gap-3 mb-4">
              {CREATABLE_ROLES.map(r => {
                const active = createForm.role === r.role;
                const style = ROLE_STYLES[r.role];
                return (
                  <button
                    key={r.role}
                    onClick={() => setCreateForm({ ...createForm, role: r.role, parent_admin_id: '' })}
                    className={`flex items-center gap-2.5 px-3 py-2.5 border text-left transition-colors ${active ? `${style.border} ${style.bg}` : 'border-[#2B2D31] hover:border-[#3A3C40]'}`}
                  >
                    <span className={`text-[8px] font-bold px-1.5 py-1 border ${style.border} ${style.text}`}>{r.code}</span>
                    <span className={`text-[10px] tracking-wide uppercase ${active ? style.text : 'text-[#C4C6CC]'}`}>{r.label}</span>
                  </button>
                );
              })}
            </div>

            <div className="grid grid-cols-2 gap-3 mb-3">
              <FieldInput label="Username" value={createForm.username} onChange={(v: string) => setCreateForm({ ...createForm, username: v })} />
              <FieldInput label="Password" type="password" value={createForm.password} onChange={(v: string) => setCreateForm({ ...createForm, password: v })} />
            </div>
            <div className="grid grid-cols-2 gap-3 mb-3">
              <FieldInput label="Assignment" value={createForm.assignment} onChange={(v: string) => setCreateForm({ ...createForm, assignment: v })} placeholder="e.g. Patrol Unit 3" />
              <FieldInput label="Display title (optional)" value={createForm.display_title} onChange={(v: string) => setCreateForm({ ...createForm, display_title: v })} placeholder="e.g. Assistant Captain" />
            </div>

            <div className="mb-3 relative">
              <label className="text-[8px] tracking-[0.15em] uppercase text-[#6B6D73] mb-1 block">Location — connects this account to one barangay/precinct</label>
              <button
                type="button"
                onClick={() => setLocPickerOpen(o => !o)}
                className="w-full bg-[#0A0A0B] border border-[#2B2D31] focus:border-[#8FA8D9]/50 p-2.5 text-[11px] text-[#F0F1F3] outline-none flex items-center justify-between transition-colors"
              >
                <span className={createForm.barangay_id ? '' : 'text-[#4A4C50]'}>{createForm.barangay_id || 'select or type a location id'}</span>
                <ChevronDown size={12} className="text-[#6B6D73]" />
              </button>
              <input
                value={createForm.barangay_id}
                onChange={e => setCreateForm({ ...createForm, barangay_id: e.target.value })}
                placeholder="type to create a new location id, e.g. 'cogon'"
                className="w-full mt-2 bg-[#0A0A0B] border border-[#2B2D31] focus:border-[#8FA8D9]/50 p-2.5 text-[11px] text-[#F0F1F3] outline-none placeholder:text-[#4A4C50] transition-colors"
              />
              {locPickerOpen && knownLocationIds.length > 0 && (
                <div className="mt-1 border border-[#2B2D31] bg-[#151517] max-h-32 overflow-y-auto custom-scrollbar">
                  {knownLocationIds.map(loc => (
                    <button
                      key={loc}
                      onClick={() => { setCreateForm({ ...createForm, barangay_id: loc }); setLocPickerOpen(false); }}
                      className="w-full text-left px-3 py-1.5 text-[10px] text-[#C4C6CC] hover:bg-[#1E2023] hover:text-[#F0F1F3] transition-colors"
                    >
                      {loc}
                    </button>
                  ))}
                </div>
              )}
            </div>

            {(createForm.role === 'POLICE' || createForm.role === 'BARANGAY') && (
              <div className="mb-4">
                <label className="text-[8px] tracking-[0.15em] uppercase text-[#6B6D73] mb-1 block">Reports to (optional — auto-attaches to the location's captain if left blank)</label>
                <select
                  value={createForm.parent_admin_id}
                  onChange={e => setCreateForm({ ...createForm, parent_admin_id: e.target.value })}
                  className="w-full bg-[#0A0A0B] border border-[#2B2D31] focus:border-[#8FA8D9]/50 p-2.5 text-[11px] text-[#F0F1F3] outline-none transition-colors"
                >
                  <option value="">Auto-attach to location captain</option>
                  {eligibleParents.map(p => (
                    <option key={p.id} value={p.id}>{p.username} ({p.role})</option>
                  ))}
                </select>
              </div>
            )}

            <div className="mb-5 pt-4 border-t border-[#1E2023]">
              <div className="text-[8px] tracking-[0.15em] uppercase text-[#6B6D73] flex items-center gap-1.5 mb-2">
                <KeyRound size={10} /> Permissions — same tree used everywhere else
              </div>
              <div className="border border-[#1E2023] divide-y divide-[#1E2023]">
                {PERMISSION_KEYS.map(p => (
                  <label key={p.key} className="flex items-center justify-between px-3 py-2 cursor-pointer hover:bg-[#151517] transition-colors">
                    <span className="text-[10px] text-[#C4C6CC]">{p.label}</span>
                    <input
                      type="checkbox"
                      checked={!!createPerms[p.key]}
                      onChange={e => setCreatePerms({ ...createPerms, [p.key]: e.target.checked })}
                      className="w-3.5 h-3.5 accent-[#8FA8D9]"
                    />
                  </label>
                ))}
              </div>
            </div>

            {createError && (
              <p className="text-[10px] text-[#D9756A] uppercase tracking-wide mb-3">{createError}</p>
            )}

            <button
              onClick={handleCreateUser}
              disabled={createBusy}
              className="w-full py-2.5 bg-[#8FA8D9] text-[#0A0A0B] text-[10px] font-bold tracking-[0.15em] uppercase hover:bg-[#A3BAE3] disabled:opacity-50 transition-colors flex items-center justify-center gap-2"
            >
              <Save size={12} /> {createBusy ? 'Creating…' : 'Create account'}
            </button>
          </div>
        </div>
      )}

      {/* EDIT + PERMISSIONS MODAL */}
      {editingUser && (
        <div className="fixed inset-0 z-[130] flex items-center justify-center p-4 bg-[#0A0A0B]/85">
          <div className="bg-[#18191C] border border-[#2B2D31] w-full max-w-sm p-6 max-h-[90vh] overflow-y-auto custom-scrollbar font-mono">
            <div className="flex items-center justify-between mb-5 pb-4 border-b border-[#1E2023]">
              <div className="flex items-center gap-2">
                <span className={`text-[8px] font-bold px-1.5 py-1 border ${(ROLE_STYLES[editingUser.role] || ROLE_STYLES.POLICE).border} ${(ROLE_STYLES[editingUser.role] || ROLE_STYLES.POLICE).text}`}>
                  {(ROLE_STYLES[editingUser.role] || ROLE_STYLES.POLICE).code}
                </span>
                <span className="text-[10px] tracking-[0.15em] uppercase text-[#C4C6CC]">{editingUser.role.replace('_', ' ')}</span>
              </div>
              <button onClick={() => setEditingUser(null)}><X size={15} className="text-[#6B6D73] hover:text-[#F0F1F3]" /></button>
            </div>
            <div className="space-y-3">
              <div>
                <label className="text-[8px] tracking-[0.15em] uppercase text-[#6B6D73] mb-1 block">Username</label>
                <input
                  value={editDraft.username}
                  onChange={e => setEditDraft({ ...editDraft, username: e.target.value })}
                  className="w-full bg-[#0A0A0B] border border-[#2B2D31] focus:border-[#8FA8D9]/50 p-2.5 text-[11px] text-[#F0F1F3] outline-none transition-colors"
                />
              </div>
              <div>
                <label className="text-[8px] tracking-[0.15em] uppercase text-[#6B6D73] mb-1 block">Assignment</label>
                <input
                  value={editDraft.assignment}
                  onChange={e => setEditDraft({ ...editDraft, assignment: e.target.value })}
                  className="w-full bg-[#0A0A0B] border border-[#2B2D31] focus:border-[#8FA8D9]/50 p-2.5 text-[11px] text-[#F0F1F3] outline-none transition-colors"
                />
              </div>
              <div>
                <label className="text-[8px] tracking-[0.15em] uppercase text-[#6B6D73] mb-1 block">New password</label>
                <input
                  type="password"
                  value={editDraft.password}
                  onChange={e => setEditDraft({ ...editDraft, password: e.target.value })}
                  placeholder="leave blank to keep current"
                  className="w-full bg-[#0A0A0B] border border-[#2B2D31] focus:border-[#8FA8D9]/50 p-2.5 text-[11px] text-[#F0F1F3] outline-none placeholder:text-[#4A4C50] transition-colors"
                />
              </div>

              <div className="pt-3 border-t border-[#1E2023]">
                <div className="text-[8px] tracking-[0.15em] uppercase text-[#6B6D73] flex items-center gap-1.5 mb-2">
                  <KeyRound size={10} /> Permissions
                </div>
                <div className="border border-[#1E2023] divide-y divide-[#1E2023]">
                  {PERMISSION_KEYS.map(p => (
                    <label key={p.key} className="flex items-center justify-between px-3 py-2 cursor-pointer hover:bg-[#151517] transition-colors">
                      <span className="text-[10px] text-[#C4C6CC]">{p.label}</span>
                      <input
                        type="checkbox"
                        checked={!!permsDraft[p.key]}
                        onChange={e => setPermsDraft({ ...permsDraft, [p.key]: e.target.checked })}
                        className="w-3.5 h-3.5 accent-[#8FA8D9]"
                      />
                    </label>
                  ))}
                </div>
              </div>

              <button onClick={saveEdit} className="w-full py-2.5 bg-[#8FA8D9] text-[#0A0A0B] text-[10px] font-bold tracking-[0.15em] uppercase hover:bg-[#A3BAE3] transition-colors flex items-center justify-center gap-2 mt-2">
                <Save size={12} /> Save changes
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function StatCell({ icon, label, val, accent, last }: any) {
  return (
    <div className={`flex items-center gap-2.5 py-3 pr-6 ${!last ? 'border-r border-[#1E2023] mr-6' : ''}`}>
      <span className={accent || 'text-[#6B6D73]'}>{icon}</span>
      <div className="leading-tight">
        <span className={`text-[13px] font-semibold tabular-nums ${accent || 'text-[#F0F1F3]'}`}>{val}</span>
        <p className="text-[8px] tracking-[0.15em] uppercase text-[#6B6D73]">{label}</p>
      </div>
    </div>
  );
}

function TabButton({ icon, label, active, onClick, badge }: any) {
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-2 px-4 py-2.5 text-[9px] tracking-[0.15em] uppercase border-b-2 -mb-px transition-colors ${
        active ? 'border-[#8FA8D9] text-[#F0F1F3]' : 'border-transparent text-[#6B6D73] hover:text-[#C4C6CC]'
      }`}
    >
      {icon} {label}
      {typeof badge === 'number' && badge > 0 && (
        <span className={`text-[8px] px-1.5 py-0.5 rounded-full ${active ? 'bg-[#8FA8D9] text-[#0A0A0B]' : 'bg-[#2B2D31] text-[#C4C6CC]'}`}>{badge}</span>
      )}
    </button>
  );
}

function SeatChip({ user, code }: { user?: ManagedUser; code: string }) {
  const style = ROLE_STYLES[code === 'PD' ? 'PRECINCT_CAPTAIN' : 'BARANGAY_CAPTAIN'];
  if (!user) {
    return (
      <span className="flex items-center gap-1.5 text-[9px] px-2 py-1 border border-dashed border-[#2B2D31] text-[#4A4C50] uppercase tracking-wide">
        {code} vacant
      </span>
    );
  }
  return (
    <span className={`flex items-center gap-1.5 text-[9px] px-2 py-1 border ${style.border} ${style.text}`}>
      {code} &middot; {user.username}
    </span>
  );
}

function FieldInput({ label, value, onChange, type = 'text', placeholder }: any) {
  return (
    <div>
      <label className="text-[8px] tracking-[0.15em] uppercase text-[#6B6D73] mb-1 block">{label}</label>
      <input
        type={type}
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full bg-[#0A0A0B] border border-[#2B2D31] focus:border-[#8FA8D9]/50 p-2.5 text-[11px] text-[#F0F1F3] outline-none placeholder:text-[#4A4C50] transition-colors"
      />
    </div>
  );
}