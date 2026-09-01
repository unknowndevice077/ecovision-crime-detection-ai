"use client";

import React, { useState, useMemo, useEffect } from 'react';
import {
  ShieldAlert, Wifi, WifiOff, ShieldCheck, ShieldX, UserCheck,
  Pencil, Trash2, X, Save, Search, LogOut, KeyRound, Users2, MapPinned,
  Activity, Video, Film, Radio, LayoutGrid, ClipboardList, UserPlus, ChevronDown,
  Brain, AlertTriangle, Info, RotateCw, Eye, EyeOff, Gauge, Undo2
} from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useLiveChannel, useWebSocketContext } from '../../context/WebSocketContext';
import { useRuntimeConfig } from '../../hooks/useRuntimeConfig';
import { permissionRowsFor, permissionNoteFor, onlyEditablePermissions } from '../../lib/permissions';

const CREATABLE_ROLES = [
  { role: 'PNP_ADMIN', code: 'PD', label: 'PNP Admin', scope: 'station' },
  { role: 'PNP_OFFICER', code: 'PD', label: 'PNP Officer', scope: 'station' },
  { role: 'BARANGAY_ADMIN', code: 'BG', label: 'Barangay Admin', scope: 'barangay' },
  { role: 'BARANGAY_STAFF', code: 'BG', label: 'Barangay Staff', scope: 'barangay' },
];

const PNP_ROLES = ['PNP_ADMIN', 'PNP_OFFICER'];

// Two operating branches, distinguished the way a dispatch board would:
// a callsign-style two-letter code and a single accent, nothing more.
const ROLE_STYLES: Record<string, { code: string; text: string; border: string; bg: string; barText: string }> = {
  PNP_ADMIN: { code: 'PD', text: 'text-[var(--accent)]', border: 'border-[var(--accent)]/25', bg: 'bg-[var(--accent)]/[0.07]', barText: 'text-[var(--accent)]' },
  BARANGAY_ADMIN: { code: 'BG', text: 'text-[var(--ok)]', border: 'border-[var(--ok)]/25', bg: 'bg-[var(--ok)]/[0.07]', barText: 'text-[var(--ok)]' },
  PNP_OFFICER: { code: 'PD', text: 'text-[var(--accent)]/70', border: 'border-[var(--accent)]/15', bg: 'bg-[var(--accent)]/[0.04]', barText: 'text-[var(--accent)]/70' },
  BARANGAY_STAFF: { code: 'BG', text: 'text-[var(--ok)]/70', border: 'border-[var(--ok)]/15', bg: 'bg-[var(--ok)]/[0.04]', barText: 'text-[var(--ok)]/70' },
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
  station_id: string;
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

type Tab = 'directory' | 'approvals' | 'create' | 'cameras' | 'stations' | 'models';

type ModelStat = {
  label: string; value: number; unit: string; note?: string; good?: boolean;
};
type ModelMetrics = {
  status: string;
  headline?: { label: string; value: number; unit: string };
  stats?: ModelStat[];
  measured_on?: string;
  caveat?: string;
};
type DetectionModel = {
  name: string;
  display_name: string;
  enabled: boolean;
  experimental: boolean;
  threshold: number;
  consecutive_required: number;
  model_path: string;
  weights_present: boolean;
  metrics?: ModelMetrics;
  notes?: Record<string, string>;
};

type OptimizeStep = {
  index: number;
  total?: number;
  label: string;
  stem?: string;
  state: 'start' | 'building' | 'done' | 'skipped' | 'failed';
  before_ms?: number;
  after_ms?: number;
  speedup?: number;
  reason?: string;
  error?: string;
};

type OptimizeSummaryRow = {
  label: string;
  before_ms?: number;
  after_ms?: number;
  speedup?: number;
  error?: string;
};

type OptimizeSummary =
  | { kind: 'summary'; results: OptimizeSummaryRow[]; combined: number | null }
  | { kind: 'reverted'; files: string[] };

type OptimizeState = {
  running: boolean;
  steps: OptimizeStep[];
  summary: OptimizeSummary | null;
  preconditions: { ok: boolean; detail: string } | null;
  returncode: number | null;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
  cancelled?: boolean;
  cancel_requested?: boolean;
};

type Station = { id: string; name: string; barangay_ids: string[]; staff_count: number };

type CameraRow = { id: string; name: string; url: string; status: string; barangay_id: string };

export default function DevteamView() {
  const { apiUrl: API_URL } = useRuntimeConfig();
  const router = useRouter();
  const [data, setData] = useState<any>(null);
  const [cameras, setCameras] = useState<CameraRow[]>([]);
  const [stations, setStations] = useState<Station[]>([]);
  const [pendingLocations, setPendingLocations] = useState<PendingLocation[]>([]);
  const [allLocations, setAllLocations] = useState<PendingLocation[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);
  const [tab, setTab] = useState<Tab>('directory');
  // Keys into locationDirectory below: a barangay's own id for a normal
  // location row, or `station:<id>` for a station that has no barangay
  // jurisdiction assigned yet (see locationDirectory's unassignedStations --
  // that bucket exists so a station created before its jurisdiction is set
  // still has somewhere for its PNP accounts to show up, instead of quietly
  // disappearing from a purely per-barangay view).
  const [selectedLocationKey, setSelectedLocationKey] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [editingUser, setEditingUser] = useState<ManagedUser | null>(null);
  const [editDraft, setEditDraft] = useState({ username: '', assignment: '', password: '', barangay_id: '', station_id: '' });
  const [showEditPassword, setShowEditPassword] = useState(false);
  const [permsDraft, setPermsDraft] = useState<Record<string, boolean>>({});
  const [pendingActionIds, setPendingActionIds] = useState<Set<string | number>>(new Set());
  const [toast, setToast] = useState('');
  const { connected } = useWebSocketContext();

  // CREATE USER TAB — DevTeam can mint any role directly (PNP_ADMIN,
  // BARANGAY_ADMIN, POLICE, BARANGAY), skip the pending-approval signup
  // flow entirely, and grant permissions from the same tree admins use for
  // their own sub-accounts.
  const [createForm, setCreateForm] = useState({
    username: '', password: '', assignment: '', display_title: '',
    role: 'PNP_ADMIN', barangay_id: '', station_id: '', parent_admin_id: '' as string,
  });
  const [createPerms, setCreatePerms] = useState<Record<string, boolean>>({});
  const [createBusy, setCreateBusy] = useState(false);
  const [createError, setCreateError] = useState('');
  const [locPickerOpen, setLocPickerOpen] = useState(false);

  // STATIONS TAB. jurisDraft holds pending edits keyed by station id, so the
  // checkboxes stay responsive and the PUT only fires on Save -- toggling a
  // jurisdiction changes who can see a whole barangay's footage, which is
  // not something to commit on every stray click.
  const [newStationName, setNewStationName] = useState('');
  const [stationBusy, setStationBusy] = useState(false);
  const [jurisDraft, setJurisDraft] = useState<Record<string, string[]>>({});

  const handleCreateStation = async () => {
    const name = newStationName.trim();
    if (!name) return;
    setStationBusy(true);
    try {
      const res = await fetch(`${API_URL}/api/devteam/stations`, {
        method: "POST", headers: authHeaders(), body: JSON.stringify({ name }),
      });
      const d = await res.json().catch(() => ({}));
      if (res.ok) { setNewStationName(''); fetchOverview(); flash(`Station "${name}" created.`); }
      else flash(d.detail || 'Could not create station.');
    } catch {
      flash('Backend connection failure.');
    } finally {
      setStationBusy(false);
    }
  };

  const toggleJurisdiction = (st: Station, barangayId: string) => {
    setJurisDraft(prev => {
      const current = prev[st.id] ?? st.barangay_ids;
      const next = current.includes(barangayId)
        ? current.filter(b => b !== barangayId)
        : [...current, barangayId];
      return { ...prev, [st.id]: next };
    });
  };

  const saveJurisdiction = async (st: Station) => {
    const draft = jurisDraft[st.id];
    if (!draft) return;
    setStationBusy(true);
    try {
      const res = await fetch(`${API_URL}/api/devteam/stations/${st.id}/jurisdiction`, {
        method: "PUT", headers: authHeaders(), body: JSON.stringify({ barangay_ids: draft }),
      });
      const d = await res.json().catch(() => ({}));
      if (res.ok) {
        setJurisDraft(prev => { const n = { ...prev }; delete n[st.id]; return n; });
        fetchOverview();
        flash(`${st.name} now covers ${draft.length} barangay${draft.length === 1 ? '' : 's'}.`);
      } else flash(d.detail || 'Could not update jurisdiction.');
    } catch {
      flash('Backend connection failure.');
    } finally {
      setStationBusy(false);
    }
  };

  const handleDeleteStation = async (st: Station) => {
    if (st.staff_count > 0) return; // button is disabled too; belt and braces
    setStationBusy(true);
    try {
      const res = await fetch(`${API_URL}/api/devteam/stations/${st.id}`, {
        method: "DELETE", headers: authHeaders(),
      });
      const d = await res.json().catch(() => ({}));
      if (res.ok) { fetchOverview(); flash(`${st.name} deleted.`); }
      else flash(d.detail || 'Could not delete station.');
    } catch {
      flash('Backend connection failure.');
    } finally {
      setStationBusy(false);
    }
  };

  const fetchOverview = async () => {
    try {
      const [overviewRes, locationsRes, allLocationsRes, stationsRes] = await Promise.all([
        fetch(`${API_URL}/api/devteam/overview`, { headers: authHeaders() }),
        fetch(`${API_URL}/api/devteam/locations?status=pending`, { headers: authHeaders() }),
        fetch(`${API_URL}/api/devteam/locations`, { headers: authHeaders() }),
        fetch(`${API_URL}/api/devteam/stations`, { headers: authHeaders() }),
      ]);
      if (overviewRes.ok && locationsRes.ok) {
        const overview = await overviewRes.json();
        setData(overview);
        setCameras(overview.cameras || []);
        setPendingLocations(await locationsRes.json());
        if (allLocationsRes.ok) setAllLocations(await allLocationsRes.json());
        if (stationsRes.ok) setStations(await stationsRes.json());
        setLoadFailed(false);
      } else if (overviewRes.status === 401 || locationsRes.status === 401) {
        // BUG FOUND 2026-08-19: a 401 here almost always means the stored
        // token is permanently invalid, not a transient network problem --
        // most commonly, SECRET_KEY was regenerated by a newer install
        // (writeGeneratedEnv() makes a fresh random one per install) while
        // Electron's localStorage, which lives in the app's userData path
        // rather than the install folder, still had a token signed by an
        // older key. That token will never become valid again no matter how
        // many times "Retry Connection" is clicked -- the old code just
        // showed a dead-end error forever. page.tsx's auth gate only checks
        // whether `ecoUser` exists, not whether the token is actually still
        // valid, so a stale pair can get all the way to this screen. The
        // real fix: treat 401 here as "please log in again", exactly what
        // the backend's own error message already says, and act on it.
        localStorage.removeItem('ecoUser');
        localStorage.removeItem('ecoToken');
        router.push('/loginpage/login');
        return;
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

  // ── Detection models ──────────────────────────────────────────────────────
  // Fetched on demand rather than with the overview: config.json changes only
  // when someone here changes it, so polling it alongside live incident data
  // would be pure noise.
  const [models, setModels] = useState<DetectionModel[]>([]);
  const [modelsLoaded, setModelsLoaded] = useState(false);
  const [modelBusy, setModelBusy] = useState<string | null>(null);
  const [restartPending, setRestartPending] = useState(false);
  const [confirmEnable, setConfirmEnable] = useState<DetectionModel | null>(null);
  // Read-only visibility onto camera_threshold_config (docs/progress_report_
  // violence_detection.md §28.1) -- deliberately NOT an edit control, same
  // reasoning as the reverted-editable-threshold comment below: a per-camera
  // number is set by tools/calibrate_camera_quiet.py against that camera's
  // own quiet footage, not typed into a box here. This just answers "is
  // anything actually calibrated right now".
  const [thresholdCounts, setThresholdCounts] = useState<Record<string, number>>({});

  // REVERSED 2026-08-23 (was editable for "weapon" only, since 2026-08-19):
  // every threshold here is the value measured to give the model's reported
  // accuracy on its validation split. Editing it live from the dashboard
  // invalidates the number displayed two lines above it with no warning, so
  // this is now display-only for every class, weapon included. The backend
  // endpoint (set_detection_model) still accepts a threshold in its PATCH
  // body -- nothing here calls it anymore, but removing that capability is a
  // separate, deliberate decision, not a side effect of removing this UI.

  const fetchModels = async () => {
    try {
      const res = await fetch(`${API_URL}/api/devteam/detection-models`, { headers: authHeaders() });
      if (res.ok) {
        const d = await res.json();
        setModels(d.models || []);
      }
    } catch { /* leave the previous list up rather than blanking the panel */ }
    finally { setModelsLoaded(true); }
  };

  const fetchThresholdCounts = async () => {
    try {
      const res = await fetch(`${API_URL}/api/cameras/thresholds`, { headers: authHeaders() });
      if (!res.ok) return;
      const d = await res.json();
      const counts: Record<string, number> = {};
      for (const cam of d.cameras || []) {
        for (const key of Object.keys(cam.thresholds || {})) {
          counts[key] = (counts[key] || 0) + 1;
        }
      }
      setThresholdCounts(counts);
    } catch { /* purely informational -- leave whatever counts were last shown */ }
  };

  const applyModelChange = async (m: DetectionModel, body: Record<string, unknown>) => {
    setModelBusy(m.name);
    try {
      const res = await fetch(`${API_URL}/api/devteam/detection-models/${m.name}`, {
        method: 'PATCH',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) { flash(d.detail || 'Could not save'); return; }
      await fetchModels();
      setRestartPending(true);
      flash(`${m.display_name} ${body.enabled === false ? 'turned off' : 'turned on'} — restart detection to apply`);
    } catch {
      flash('Could not reach the server');
    } finally {
      setModelBusy(null);
    }
  };

  // ── Optimize weights (TensorRT, this machine only) ─────────────────────────
  // Wraps optimize_weights.py: builds .engine files compiled against THIS
  // GPU + this TensorRT version. optimize_weights.py refuses to install an
  // engine whose verdicts disagree with the .pt it came from, so a run
  // either speeds things up or changes nothing -- never changes an answer.
  const [optimizeState, setOptimizeState] = useState<OptimizeState | null>(null);
  const [optimizeBusy, setOptimizeBusy] = useState(false);
  const [optimizeIsRevert, setOptimizeIsRevert] = useState(false);
  const [cancelBusy, setCancelBusy] = useState(false);
  // The progress window opens on click and stays open through the finished
  // state (so the last result is visible), and is only dismissed by the
  // user -- fetchOptimizeStatus alone would close it the instant
  // optimizeState.running flips back to false, taking the result with it.
  const [optimizeWindowOpen, setOptimizeWindowOpen] = useState(false);

  const fetchOptimizeStatus = async () => {
    try {
      const res = await fetch(`${API_URL}/api/devteam/optimize_weights/status`, { headers: authHeaders() });
      if (res.ok) setOptimizeState(await res.json());
    } catch { /* leave the previous panel up */ }
  };

  useLiveChannel("optimize_weights", fetchOptimizeStatus);

  // Reopens the progress window if a run is already in flight when this
  // panel first sees it -- e.g. it was started, the page got reloaded, and
  // the poll picks the still-running state back up. Without this, Cancel
  // would only ever be reachable from the same click that started the run.
  useEffect(() => {
    if (optimizeState?.running) setOptimizeWindowOpen(true);
  }, [optimizeState?.running]);

  const startOptimize = async (revert: boolean) => {
    setOptimizeBusy(true);
    setOptimizeIsRevert(revert);
    setOptimizeWindowOpen(true);
    try {
      const res = await fetch(`${API_URL}/api/devteam/optimize_weights${revert ? '/revert' : ''}`, {
        method: 'POST', headers: authHeaders(),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) { flash(d.detail || 'Could not start'); return; }
      await fetchOptimizeStatus();
      flash(revert ? 'Reverting to .pt weights…' : 'Optimizing for this machine…');
    } catch {
      flash('Could not reach the server');
    } finally {
      setOptimizeBusy(false);
    }
  };

  const cancelOptimize = async () => {
    setCancelBusy(true);
    try {
      const res = await fetch(`${API_URL}/api/devteam/optimize_weights/cancel`, {
        method: 'POST', headers: authHeaders(),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) { flash(d.detail || 'Could not cancel'); return; }
      await fetchOptimizeStatus();
      flash('Cancelled.');
    } catch {
      flash('Could not reach the server');
    } finally {
      setCancelBusy(false);
    }
  };

  // Turning a measured-bad model ON gets a confirmation step; turning anything
  // OFF does not. Disabling a detector can only reduce output, so there is
  // nothing to warn about -- but enabling one whose own numbers say it fires
  // on 3 of 8 quiet clips should not be a single unguarded click.
  const requestToggle = (m: DetectionModel) => {
    if (!m.enabled && (m.experimental || m.metrics?.status === 'disabled')) {
      setConfirmEnable(m);
      return;
    }
    applyModelChange(m, { enabled: !m.enabled });
  };

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
    setEditDraft({
      username: u.username, assignment: u.assignment, password: '',
      barangay_id: u.barangay_id || '', station_id: u.station_id || '',
    });
    try { setPermsDraft(JSON.parse(u.permissions || "{}")); } catch { setPermsDraft({}); }
  };

  const saveEdit = async () => {
    if (!editingUser) return;
    const id = editingUser.id;
    const body: any = { username: editDraft.username, assignment: editDraft.assignment };
    if (editDraft.password.trim()) body.password = editDraft.password.trim();
    // Which scope field to send follows the account's own organization, same
    // as account creation -- chk_user_scope rejects a PNP account with a
    // barangay_id (or vice versa), so only the one this role actually uses
    // gets sent, never both.
    if (PNP_ROLES.includes(editingUser.role)) {
      if (editDraft.station_id.trim()) body.station_id = editDraft.station_id.trim();
    } else {
      if (editDraft.barangay_id.trim()) body.barangay_id = editDraft.barangay_id.trim();
    }
    setEditingUser(null);
    try {
      const [editRes, permsRes] = await Promise.all([
        fetch(`${API_URL}/api/devteam/users/${id}`, { method: "PATCH", headers: authHeaders(), body: JSON.stringify(body) }),
        fetch(`${API_URL}/api/admin/users/${id}/permissions`, { method: "PATCH", headers: authHeaders(), body: JSON.stringify({ permissions: onlyEditablePermissions(editingUser.role, permsDraft) }) }),
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
    setCreateForm({ username: '', password: '', assignment: '', display_title: '', role: 'PNP_ADMIN', barangay_id: '', station_id: '', parent_admin_id: '' });
    setCreatePerms({});
    setCreateError('');
  };

  const handleCreateUser = async () => {
    setCreateError('');
    if (!createForm.username.trim() || !createForm.password.trim() || !createForm.assignment.trim()) {
      setCreateError('Username, password, and assignment are required.');
      return;
    }
    const isPnp = PNP_ROLES.includes(createForm.role);
    if (isPnp && !createForm.station_id) {
      setCreateError('A police station is required for PNP roles.');
      return;
    }
    if (!isPnp && !createForm.barangay_id.trim()) {
      setCreateError('A barangay is required for barangay roles.');
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
          barangay_id: isPnp ? null : (createForm.barangay_id.trim().toLowerCase() || null),
          station_id: isPnp ? createForm.station_id : null,
          assignment: createForm.assignment.trim(),
          display_title: createForm.display_title.trim() || null,
          parent_admin_id: createForm.parent_admin_id ? Number(createForm.parent_admin_id) : null,
          permissions: onlyEditablePermissions(createForm.role, createPerms),
        }),
      });
      const d = await res.json().catch(() => ({}));
      if (res.ok) {
        flash(`${createForm.username} created (${isPnp ? stations.find(st => st.id === createForm.station_id)?.name ?? createForm.station_id : createForm.barangay_id}).`);
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

  // Per-LOCATION directory (replaces the old per-admin flat list). A
  // barangay is a location; a location has at most one connected police
  // station (found via that station's own jurisdiction list, stations.
  // barangay_ids -- NOT via barangay_id on the PNP account, which is always
  // null for PNP roles, see handleCreateUser's `barangay_id: isPnp ? null
  // : ...`). Each location surfaces both seats: the station's PNP admin +
  // its sub-accounts, and the barangay's own admin + its sub-accounts.
  type LocationEntry = {
    loc: PendingLocation; station: Station | undefined;
    barangayAdmins: ManagedUser[]; barangayStaff: ManagedUser[];
    pnpAdmins: ManagedUser[]; pnpStaff: ManagedUser[];
  };
  type UnassignedStationEntry = { station: Station; pnpAdmins: ManagedUser[]; pnpStaff: ManagedUser[] };

  const locationDirectory = useMemo(() => {
    if (!data) return { locations: [] as LocationEntry[], unassignedStations: [] as UnassignedStationEntry[] };
    const users: ManagedUser[] = data.users;
    const q = search.trim().toLowerCase();
    const stationForLoc = (locId: string) => stations.find(st => st.barangay_ids.includes(locId));
    // Computed over every location regardless of search, so a search that
    // hides a barangay never makes its station look "unassigned" and get
    // double-listed in both buckets.
    const claimedStationIds = new Set(
      allLocations.map(l => stationForLoc(l.id)?.id).filter((id): id is string => !!id)
    );

    // Search matches the location/station name AND any callsign (username)
    // under it -- built before filtering, not instead of it, so "search
    // callsign or location" stays true for both halves of that promise.
    const matchesLoc = (loc: PendingLocation, station: Station | undefined, seatUsers: ManagedUser[]) => {
      if (!q) return true;
      if (loc.name.toLowerCase().includes(q) || loc.id.toLowerCase().includes(q)) return true;
      if (station?.name.toLowerCase().includes(q)) return true;
      return seatUsers.some(u => u.username.toLowerCase().includes(q));
    };

    const locations: LocationEntry[] = allLocations
      .map(loc => {
        const barangayAdmins = users.filter(u => u.role === 'BARANGAY_ADMIN' && u.barangay_id === loc.id);
        const barangayStaff = users.filter(u => barangayAdmins.some(a => a.id === u.parent_admin_id));
        const station = stationForLoc(loc.id);
        const pnpAdmins = station ? users.filter(u => u.role === 'PNP_ADMIN' && u.station_id === station.id) : [];
        const pnpStaff = station ? users.filter(u => pnpAdmins.some(a => a.id === u.parent_admin_id)) : [];
        return { loc, station, barangayAdmins, barangayStaff, pnpAdmins, pnpStaff };
      })
      .filter(e => matchesLoc(e.loc, e.station, [...e.barangayAdmins, ...e.barangayStaff, ...e.pnpAdmins, ...e.pnpStaff]));

    // Stations that exist but have no barangay jurisdiction yet (created,
    // not yet assigned in the Stations tab) -- still get a row so their PNP
    // accounts, if any were created early, aren't invisible.
    const unassignedStations: UnassignedStationEntry[] = stations
      .filter(st => !claimedStationIds.has(st.id))
      .map(st => {
        const pnpAdmins = users.filter(u => u.role === 'PNP_ADMIN' && u.station_id === st.id);
        const pnpStaff = users.filter(u => pnpAdmins.some(a => a.id === u.parent_admin_id));
        return { station: st, pnpAdmins, pnpStaff };
      })
      .filter(e => !q || e.station.name.toLowerCase().includes(q) || [...e.pnpAdmins, ...e.pnpStaff].some(u => u.username.toLowerCase().includes(q)));

    return { locations, unassignedStations };
  }, [data, allLocations, stations, search]);

  const selectedLocationEntry = useMemo(() => {
    const rows: Array<{ key: string; kind: 'location' | 'station'; entry: LocationEntry | UnassignedStationEntry }> = [
      ...locationDirectory.locations.map(e => ({ key: e.loc.id, kind: 'location' as const, entry: e })),
      ...locationDirectory.unassignedStations.map(e => ({ key: `station:${e.station.id}`, kind: 'station' as const, entry: e })),
    ];
    return rows.find(r => r.key === selectedLocationKey) || rows[0] || null;
  }, [locationDirectory, selectedLocationKey]);

  // One admin + its sub-accounts, for either seat (police or barangay) of
  // the selected location. Shared renderer so the two seats stay visually
  // identical rather than drifting apart as separate copies.
  const renderSeat = (label: string, code: 'PD' | 'BG', seatAdmins: ManagedUser[], seatStaff: ManagedUser[], emptyText: string) => {
    const style = ROLE_STYLES[code === 'PD' ? 'PNP_ADMIN' : 'BARANGAY_ADMIN'];
    return (
      <div className="border border-[var(--line)]">
        <div className="flex items-center gap-2 px-3 py-2.5 border-b border-[var(--line)]" style={{ background: style.bg }}>
          <span className={`text-[8px] font-bold px-1.5 py-1 border ${style.border} ${style.text} shrink-0`}>{code}</span>
          <span className="text-[9px] tracking-[0.2em] uppercase" style={{ color: 'var(--text-2)' }}>{label}</span>
          <span className="ml-auto text-[9px]" style={{ color: 'var(--text-3)' }}>
            {seatAdmins.length + seatStaff.length} account{seatAdmins.length + seatStaff.length === 1 ? '' : 's'}
          </span>
        </div>
        {seatAdmins.length === 0 && seatStaff.length === 0 ? (
          <div className="py-8 text-center">
            <p className="text-[10px] tracking-[0.15em] uppercase text-[var(--text-3)]">{emptyText}</p>
          </div>
        ) : (
          <div className="divide-y divide-[var(--panel-2)]">
            {[...seatAdmins, ...seatStaff].map(u => {
              const rowStyle = ROLE_STYLES[u.role] || style;
              const isAdmin = u.role === 'PNP_ADMIN' || u.role === 'BARANGAY_ADMIN';
              return (
                <div key={u.id} className={`flex items-center gap-3 px-3 py-2.5 transition-opacity ${pendingActionIds.has(u.id) ? 'opacity-40' : ''}`}>
                  <span className={`text-[8px] font-bold px-1.5 py-1 border ${rowStyle.border} ${rowStyle.text} shrink-0`}>{rowStyle.code}</span>
                  <div className="min-w-0 flex-1">
                    <p className="text-[11px] text-[var(--text)] truncate">
                      {u.username}
                      {isAdmin && <span className="ml-2 text-[8px] tracking-[0.1em] uppercase" style={{ color: 'var(--text-3)' }}>admin</span>}
                    </p>
                    <p className="text-[9px] text-[var(--text-2)] truncate">{u.assignment}</p>
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    <button onClick={() => openEdit(u)} className="p-1.5 text-[var(--text-2)] hover:text-[var(--accent)] transition-colors"><Pencil size={12} /></button>
                    <button onClick={() => handleDelete(u)} className="p-1.5 text-[var(--text-2)] hover:text-[var(--critical)] transition-colors"><Trash2 size={12} /></button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    );
  };

  // Every barangay's two captain slots, side by side — makes the
  // "one location, two connected accounts" relationship visible instead
  // of implicit in a shared barangay_id column.
  //
  // BUG FOUND 2026-08-28: this used to key BOTH roles by u.barangay_id.
  // That's correct for a barangay admin, but handleCreateUser always sends
  // barangay_id: null for PNP roles (a station can cover many barangays, so
  // a PNP login can't be pinned to one) -- so every real PNP_ADMIN account
  // fell into a single '—' bucket regardless of its station's actual
  // jurisdiction, and a barangay with a station genuinely covering it
  // (station_barangays) still showed "PD Vacant". A PNP admin's jurisdiction
  // lives on their station instead (stations.barangay_ids, set on the
  // Stations tab), so they're looked up per-barangay through their
  // station's own coverage list -- the same fix already applied to the
  // Directory tab's grouping.
  const locationPairs = useMemo(() => {
    if (!data) return [];
    const users: ManagedUser[] = data.users;
    const byLoc = new Map<string, { precinct?: ManagedUser; barangay?: ManagedUser }>();
    users.forEach(u => {
      if (u.role !== 'BARANGAY_ADMIN' || !u.barangay_id) return;
      const entry = byLoc.get(u.barangay_id) || {};
      entry.barangay = u;
      byLoc.set(u.barangay_id, entry);
    });
    stations.forEach(st => {
      const precinct = users.find(u => u.role === 'PNP_ADMIN' && u.station_id === st.id);
      if (!precinct) return;
      st.barangay_ids.forEach(locId => {
        const entry = byLoc.get(locId) || {};
        entry.precinct = precinct;
        byLoc.set(locId, entry);
      });
    });
    return Array.from(byLoc.entries()).map(([loc, pair]) => ({ loc, ...pair }));
  }, [data, stations]);

  // Cameras grouped by location, each with whichever captain(s) are
  // responsible for that barangay_id -- reuses the same pairing logic as
  // locationPairs above (same barangay_id = same jurisdiction).
  const camerasByLocation = useMemo(() => {
    const map = new Map<string, { precinct?: ManagedUser; barangay?: ManagedUser; cameras: CameraRow[] }>();
    cameras.forEach(cam => {
      const key = cam.barangay_id || '—';
      if (!map.has(key)) map.set(key, { cameras: [] });
      map.get(key)!.cameras.push(cam);
    });
    locationPairs.forEach(p => {
      const key = p.loc || '—';
      const entry = map.get(key) || { cameras: [] };
      entry.precinct = p.precinct;
      entry.barangay = p.barangay;
      map.set(key, entry);
    });
    return Array.from(map.entries()).map(([loc, v]) => ({ loc, ...v }));
  }, [cameras, locationPairs]);

  const knownLocationIds = useMemo(() => {
    const set = new Set<string>();
    allLocations.forEach(l => set.add(l.id));
    (data?.users || []).forEach((u: ManagedUser) => u.barangay_id && set.add(u.barangay_id));
    return Array.from(set).sort();
  }, [allLocations, data]);

  const eligibleParents = useMemo(() => {
    if (!data) return [];
    const roleMeta = CREATABLE_ROLES.find(r => r.role === createForm.role);
    if (!roleMeta || roleMeta.role === 'PNP_ADMIN' || roleMeta.role === 'BARANGAY_ADMIN') return [];
    const wantCaptainRole = roleMeta.role === 'PNP_OFFICER' ? 'PNP_ADMIN' : 'BARANGAY_ADMIN';
    return (data.users as ManagedUser[]).filter(u => u.role === wantCaptainRole && (!createForm.barangay_id || u.barangay_id === createForm.barangay_id.trim().toLowerCase()));
  }, [data, createForm.role, createForm.barangay_id]);

  if (isLoading) {
    return (
      <div className="fixed inset-0 bg-[var(--bg)] flex items-center justify-center font-mono">
        <div className="flex flex-col items-center gap-4">
          <div className="w-8 h-8 border border-[var(--line)] border-t-[var(--accent)] animate-spin" />
          <span className="text-[10px] tracking-[0.25em] text-[var(--text-2)] uppercase">Establishing link</span>
        </div>
      </div>
    );
  }

  if (!data || loadFailed) {
    return (
      <div className="fixed inset-0 bg-[var(--bg)] flex flex-col items-center justify-center gap-4 font-mono">
        <ShieldAlert size={22} className="text-[var(--critical)]" />
        <span className="text-[10px] tracking-[0.25em] text-[var(--critical)] uppercase">Console link failed</span>
        <button onClick={fetchOverview} className="mt-1 px-5 py-2 border border-[var(--critical)]/40 hover:border-[var(--critical)] text-[10px] tracking-[0.2em] uppercase text-[var(--critical)] transition-colors">
          Retry connection
        </button>
      </div>
    );
  }

  return (
    <div className="fixed inset-0 bg-[var(--bg)] text-[var(--text)] flex flex-col overflow-hidden z-40 font-mono">
      {/* HEADER — dispatch console strip, not a hero */}
      <div className="relative flex items-center justify-between px-7 py-4 shrink-0 border-b border-[var(--line)]">
        <div className="flex items-center gap-3">
          <div className="p-1.5 border border-[var(--accent)]/30 bg-[var(--accent)]/10">
            <Radio size={14} className="text-[var(--accent)]" />
          </div>
          <div className="leading-tight">
            <h1 className="text-[11px] tracking-[0.2em] uppercase text-[var(--text)]">Oversight Console</h1>
            <p className="text-[9px] tracking-[0.15em] text-[var(--text-2)] uppercase">All locations &middot; full authority</p>
          </div>
        </div>
        <div className="flex items-center gap-5">
          <div className={`flex items-center gap-1.5 text-[9px] tracking-[0.15em] uppercase ${connected ? 'text-[var(--ok)]' : 'text-[var(--critical)]'}`}>
            {connected ? <Wifi size={11} /> : <WifiOff size={11} />} {connected ? 'Synced' : 'Reconnecting'}
          </div>
          <button onClick={handleLogout} className="flex items-center gap-1.5 text-[9px] tracking-[0.15em] uppercase text-[var(--text-2)] hover:text-[var(--critical)] transition-colors">
            <LogOut size={12} /> Sign out
          </button>
        </div>
      </div>

      {toast && (
        <div className="shrink-0 text-[10px] tracking-[0.1em] text-[var(--accent)] border-b border-[var(--line)] bg-[var(--accent)]/[0.04] px-7 py-1.5">
          &gt; {toast}
        </div>
      )}

      {/* STAT STRIP — inline ledger, not cards */}
      <div className="shrink-0 flex items-stretch border-b border-[var(--line)] px-7">
        <StatCell icon={<Users2 size={13} />} label="Users" val={data.totals.users} />
        <StatCell icon={<ShieldAlert size={13} />} label="Incidents" val={data.totals.incidents} />
        <StatCell icon={<Activity size={13} />} label="Active" val={data.totals.active_incidents} accent="text-[var(--critical)]" />
        <StatCell icon={<Video size={13} />} label="Cameras" val={data.totals.cameras} />
        <StatCell icon={<Film size={13} />} label="Records" val={data.totals.video_records} last />
      </div>

      {/* TABS */}
      <div className="shrink-0 flex items-center gap-1 px-7 border-b border-[var(--line)]">
        <TabButton icon={<LayoutGrid size={12} />} label="Directory" active={tab === 'directory'} onClick={() => setTab('directory')} />
        <TabButton
          icon={<ClipboardList size={12} />}
          label="Approvals"
          active={tab === 'approvals'}
          onClick={() => setTab('approvals')}
          badge={pendingLocations.length}
        />
        <TabButton icon={<UserPlus size={12} />} label="Create User" active={tab === 'create'} onClick={() => setTab('create')} />
        <TabButton icon={<Video size={12} />} label="Cameras" active={tab === 'cameras'} onClick={() => setTab('cameras')} badge={cameras.length} />
        <TabButton icon={<Radio size={12} />} label="Stations" active={tab === 'stations'} onClick={() => setTab('stations')} badge={stations.length} />
        <TabButton
          icon={<Brain size={12} />}
          label="AI Models"
          active={tab === 'models'}
          onClick={() => { setTab('models'); if (!modelsLoaded) fetchModels(); if (!optimizeState) fetchOptimizeStatus(); fetchThresholdCounts(); }}
        />
      </div>

      {/* ================= DIRECTORY TAB ================= */}
      {tab === 'directory' && (
        <div className="flex-1 min-h-0 grid grid-cols-12 gap-0 px-7 pb-7 pt-4">
          <div className="col-span-4 flex flex-col border border-[var(--line)] border-r-0">
            <div className="px-3 py-2.5 border-b border-[var(--line)] flex items-center gap-2">
              <Search size={12} className="text-[var(--text-2)] shrink-0" />
              <input
                value={search}
                onChange={e => setSearch(e.target.value)}
                placeholder="search callsign or location"
                className="bg-transparent text-[11px] text-[var(--text)] outline-none w-full placeholder:text-[var(--text-3)]"
              />
            </div>
            <div className="flex-1 overflow-y-auto custom-scrollbar">
              {locationDirectory.locations.length === 0 && locationDirectory.unassignedStations.length === 0 ? (
                <p className="text-[10px] tracking-[0.15em] uppercase text-[var(--text-3)] text-center py-10">No matching locations</p>
              ) : (
                <>
                  {locationDirectory.locations.map(entry => {
                    const key = entry.loc.id;
                    const active = selectedLocationEntry?.key === key;
                    const count = entry.barangayAdmins.length + entry.barangayStaff.length + entry.pnpAdmins.length + entry.pnpStaff.length;
                    return (
                      <button
                        key={key}
                        onClick={() => setSelectedLocationKey(key)}
                        className={`w-full flex items-center gap-3 px-3 py-2.5 text-left border-b border-[var(--panel-2)] transition-colors ${active ? 'bg-[var(--panel)]' : 'hover:bg-[var(--panel)]'}`}
                      >
                        <MapPinned size={12} className="shrink-0" style={{ color: 'var(--text-2)' }} />
                        <div className="min-w-0 flex-1">
                          <p className="text-[11px] truncate" style={{ color: 'var(--text)' }}>{entry.loc.name}</p>
                          <p className="text-[9px] text-[var(--text-2)] truncate">
                            {entry.station ? entry.station.name : 'no station assigned'} &middot; {count} account{count === 1 ? '' : 's'}
                          </p>
                        </div>
                        {active && <span className="w-1 h-1 rounded-full bg-[var(--accent)] shrink-0" />}
                      </button>
                    );
                  })}
                  {locationDirectory.unassignedStations.map(entry => {
                    const key = `station:${entry.station.id}`;
                    const active = selectedLocationEntry?.key === key;
                    const count = entry.pnpAdmins.length + entry.pnpStaff.length;
                    return (
                      <button
                        key={key}
                        onClick={() => setSelectedLocationKey(key)}
                        className={`w-full flex items-center gap-3 px-3 py-2.5 text-left border-b border-[var(--panel-2)] transition-colors ${active ? 'bg-[var(--panel)]' : 'hover:bg-[var(--panel)]'}`}
                      >
                        <Radio size={12} className="shrink-0" style={{ color: 'var(--text-2)' }} />
                        <div className="min-w-0 flex-1">
                          <p className="text-[11px] truncate" style={{ color: 'var(--text)' }}>{entry.station.name}</p>
                          <p className="text-[9px] truncate" style={{ color: 'var(--warn)' }}>
                            no barangay assigned &middot; {count} account{count === 1 ? '' : 's'}
                          </p>
                        </div>
                        {active && <span className="w-1 h-1 rounded-full bg-[var(--accent)] shrink-0" />}
                      </button>
                    );
                  })}
                </>
              )}
            </div>
          </div>

          <div className="col-span-8 border border-[var(--line)] overflow-y-auto custom-scrollbar">
            {!selectedLocationEntry ? (
              <div className="h-full flex items-center justify-center">
                <p className="text-[10px] tracking-[0.15em] uppercase text-[var(--text-3)]">Select a location from the directory</p>
              </div>
            ) : selectedLocationEntry.kind === 'location' ? (
              (() => {
                const entry = selectedLocationEntry.entry as LocationEntry;
                return (
                  <div className="p-6">
                    <div className="mb-6 pb-5 border-b border-[var(--panel-2)]">
                      <h2 className="text-[13px] text-[var(--text)] tracking-wide flex items-center gap-2">
                        <MapPinned size={13} style={{ color: 'var(--text-2)' }} /> {entry.loc.name}
                      </h2>
                      <p className="text-[9px] text-[var(--text-3)] mt-1 tracking-wide uppercase">
                        One location, two connected seats — the station covering it, and the barangay itself.
                      </p>
                    </div>
                    <div className="space-y-4">
                      {renderSeat(
                        entry.station ? `Police — ${entry.station.name}` : 'Police — no station covers this barangay yet',
                        'PD', entry.pnpAdmins, entry.pnpStaff,
                        entry.station ? 'No PNP admin created for this station yet' : 'Assign a station to this barangay in the Stations tab first',
                      )}
                      {renderSeat(`Barangay — ${entry.loc.name}`, 'BG', entry.barangayAdmins, entry.barangayStaff, 'No barangay admin created for this location yet')}
                    </div>
                  </div>
                );
              })()
            ) : (
              (() => {
                const entry = selectedLocationEntry.entry as UnassignedStationEntry;
                return (
                  <div className="p-6">
                    <div className="mb-6 pb-5 border-b border-[var(--panel-2)]">
                      <h2 className="text-[13px] text-[var(--text)] tracking-wide flex items-center gap-2">
                        <Radio size={13} style={{ color: 'var(--text-2)' }} /> {entry.station.name}
                      </h2>
                      <p className="text-[9px] mt-1 tracking-wide uppercase" style={{ color: 'var(--warn)' }}>
                        This station has no barangay jurisdiction assigned yet — set it in the Stations tab so it appears under a location.
                      </p>
                    </div>
                    {renderSeat(`Police — ${entry.station.name}`, 'PD', entry.pnpAdmins, entry.pnpStaff, 'No PNP admin created for this station yet')}
                  </div>
                );
              })()
            )}
          </div>
        </div>
      )}

      {/* ================= APPROVALS TAB ================= */}
      {tab === 'approvals' && (
        <div className="flex-1 min-h-0 overflow-y-auto custom-scrollbar px-7 pb-7 pt-4">
          <div className="border border-[var(--line)]">
            <div className="flex items-center gap-2 px-4 py-2.5 border-b border-[var(--line)] bg-[var(--accent)]/[0.03]">
              <UserCheck size={12} className="text-[var(--accent)]" />
              <span className="text-[9px] tracking-[0.2em] uppercase text-[var(--accent)]">Awaiting verification</span>
              <span className="ml-auto text-[9px] text-[var(--accent)]/70">{pendingLocations.length} pending</span>
            </div>
            {pendingLocations.length === 0 ? (
              <div className="py-14 text-center">
                <p className="text-[10px] tracking-[0.15em] uppercase text-[var(--text-3)]">No captain signups waiting on review</p>
              </div>
            ) : (
              <div className="divide-y divide-[var(--panel-2)]">
                {pendingLocations.map(loc => {
                  const busy = pendingActionIds.has(loc.id);
                  const roleMeta = ROLE_STYLES[loc.requester_role || ''] || ROLE_STYLES.POLICE;
                  return (
                    <div key={loc.id} className={`flex items-center justify-between gap-4 px-4 py-3.5 transition-opacity ${busy ? 'opacity-40' : ''}`}>
                      <div className="flex items-center gap-3 min-w-0">
                        <span className={`text-[8px] font-bold px-1.5 py-1 border shrink-0 ${roleMeta.border} ${roleMeta.text}`}>{roleMeta.code}</span>
                        <div className="min-w-0">
                          <p className="text-[11px] text-[var(--text)] truncate">{loc.requester_username} <span className="text-[var(--text-2)]">requests</span> {loc.name}</p>
                          <p className="text-[9px] text-[var(--text-2)] flex items-center gap-1">
                            <MapPinned size={9} /> {loc.requester_role} &middot; {loc.requester_assignment} &middot; {new Date(loc.created_at).toLocaleDateString()}
                          </p>
                        </div>
                      </div>
                      <div className="flex items-center gap-1 shrink-0">
                        <button onClick={() => handleApproval(loc.id, 'reject')} className="p-1.5 border border-transparent hover:border-[var(--critical)]/40 text-[var(--text-2)] hover:text-[var(--critical)] transition-colors"><ShieldX size={13} /></button>
                        <button onClick={() => handleApproval(loc.id, 'approve')} className="p-1.5 border border-transparent hover:border-[var(--ok)]/40 text-[var(--text-2)] hover:text-[var(--ok)] transition-colors"><ShieldCheck size={13} /></button>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          <div className="mt-6 border border-[var(--line)]">
            <div className="flex items-center gap-2 px-4 py-2.5 border-b border-[var(--line)]">
              <MapPinned size={12} className="text-[var(--text-2)]" />
              <span className="text-[9px] tracking-[0.2em] uppercase text-[var(--text-2)]">Locations &amp; their two captain seats</span>
            </div>
            <div className="divide-y divide-[var(--panel-2)]">
              {locationPairs.length === 0 ? (
                <div className="py-10 text-center">
                  <p className="text-[10px] tracking-[0.15em] uppercase text-[var(--text-3)]">No locations with captains yet</p>
                </div>
              ) : locationPairs.map(p => (
                <div key={p.loc} className="flex items-center gap-4 px-4 py-3">
                  <span className="text-[10px] text-[var(--text)] w-28 shrink-0 truncate uppercase tracking-wide">{p.loc}</span>
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
          <div className="max-w-2xl border border-[var(--line)] p-6">
            <div className="flex items-center gap-2 mb-1">
              <UserPlus size={13} className="text-[var(--accent)]" />
              <h2 className="text-[11px] tracking-[0.2em] uppercase text-[var(--text)]">Create account — any role</h2>
            </div>
            <p className="text-[9px] text-[var(--text-2)] tracking-wide mb-5">
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
                    className={`flex items-center gap-2.5 px-3 py-2.5 border text-left transition-colors ${active ? `${style.border} ${style.bg}` : 'border-[var(--line)] hover:border-[var(--line-2)]'}`}
                  >
                    <span className={`text-[8px] font-bold px-1.5 py-1 border ${style.border} ${style.text}`}>{r.code}</span>
                    <span className={`text-[10px] tracking-wide uppercase ${active ? style.text : 'text-[var(--text)]'}`}>{r.label}</span>
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

            {/* Scope field follows the role's organization. PNP accounts are
                scoped to a station's jurisdiction, barangay accounts to a
                single barangay -- the DB's chk_user_scope rejects the wrong
                combination, so offering both at once would just produce a
                confusing 400. */}
            {PNP_ROLES.includes(createForm.role) ? (
              <div className="mb-3">
                <label className="text-[8px] tracking-[0.15em] uppercase text-[var(--text-2)] mb-1 block">
                  Police station — this account sees every barangay in its jurisdiction
                </label>
                <select
                  value={createForm.station_id}
                  onChange={e => setCreateForm({ ...createForm, station_id: e.target.value })}
                  className="w-full bg-[var(--bg)] border border-[var(--line)] focus:border-[var(--accent)]/50 p-2.5 text-[11px] text-[var(--text)] outline-none transition-colors"
                >
                  <option value="">
                    {stations.length ? 'select a station…' : 'no stations yet — create one in the Stations tab'}
                  </option>
                  {stations.map(s => (
                    <option key={s.id} value={s.id}>
                      {s.name} ({s.barangay_ids.length} barangay{s.barangay_ids.length === 1 ? '' : 's'})
                    </option>
                  ))}
                </select>
              </div>
            ) : (
              <div className="mb-3 relative">
                <label className="text-[8px] tracking-[0.15em] uppercase text-[var(--text-2)] mb-1 block">Barangay — this account is scoped to exactly this one</label>
                <button
                  type="button"
                  onClick={() => setLocPickerOpen(o => !o)}
                  className="w-full bg-[var(--bg)] border border-[var(--line)] focus:border-[var(--accent)]/50 p-2.5 text-[11px] text-[var(--text)] outline-none flex items-center justify-between transition-colors"
                >
                  <span className={createForm.barangay_id ? '' : 'text-[var(--text-3)]'}>{createForm.barangay_id || 'select or type a barangay id'}</span>
                  <ChevronDown size={12} className="text-[var(--text-2)]" />
                </button>
                <input
                  value={createForm.barangay_id}
                  onChange={e => setCreateForm({ ...createForm, barangay_id: e.target.value })}
                  placeholder="type to create a new barangay id, e.g. 'cogon'"
                  className="w-full mt-2 bg-[var(--bg)] border border-[var(--line)] focus:border-[var(--accent)]/50 p-2.5 text-[11px] text-[var(--text)] outline-none placeholder:text-[var(--text-3)] transition-colors"
                />
                {locPickerOpen && knownLocationIds.length > 0 && (
                  <div className="mt-1 border border-[var(--line)] bg-[var(--panel)] max-h-32 overflow-y-auto custom-scrollbar">
                    {knownLocationIds.map(loc => (
                      <button
                        key={loc}
                        onClick={() => { setCreateForm({ ...createForm, barangay_id: loc }); setLocPickerOpen(false); }}
                        className="w-full text-left px-3 py-1.5 text-[10px] text-[var(--text)] hover:bg-[var(--panel-2)] hover:text-[var(--text)] transition-colors"
                      >
                        {loc}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}

            {(createForm.role === 'PNP_OFFICER' || createForm.role === 'BARANGAY_STAFF') && (
              <div className="mb-4">
                <label className="text-[8px] tracking-[0.15em] uppercase text-[var(--text-2)] mb-1 block">Reports to (optional — auto-attaches to the location's captain if left blank)</label>
                <select
                  value={createForm.parent_admin_id}
                  onChange={e => setCreateForm({ ...createForm, parent_admin_id: e.target.value })}
                  className="w-full bg-[var(--bg)] border border-[var(--line)] focus:border-[var(--accent)]/50 p-2.5 text-[11px] text-[var(--text)] outline-none transition-colors"
                >
                  <option value="">Auto-attach to location captain</option>
                  {eligibleParents.map(p => (
                    <option key={p.id} value={p.id}>{p.username} ({p.role})</option>
                  ))}
                </select>
              </div>
            )}

            <div className="mb-5 pt-4 border-t border-[var(--panel-2)]">
              <div className="text-[8px] tracking-[0.15em] uppercase text-[var(--text-2)] flex items-center gap-1.5 mb-2">
                <KeyRound size={10} /> Permissions — same tree used everywhere else
              </div>
              {permissionNoteFor(createForm.role) && (
                <p className="text-[9px] leading-relaxed text-[var(--text-3)] mb-2">{permissionNoteFor(createForm.role)}</p>
              )}
              <div className="border border-[var(--panel-2)] divide-y divide-[var(--panel-2)]">
                {permissionRowsFor(createForm.role).map(p => (
                  <label
                    key={p.key}
                    title={p.status === 'banned' ? 'The backend refuses this for every PNP account, any tier — checking it would not do anything.' : p.status === 'always' ? 'Admin-tier accounts get this automatically.' : undefined}
                    className={`flex items-center justify-between px-3 py-2 ${p.status === 'editable' ? 'cursor-pointer hover:bg-[var(--panel)]' : 'cursor-not-allowed opacity-40'} transition-colors`}
                  >
                    <span className="text-[10px] text-[var(--text)]">
                      {p.label}
                      {p.status === 'banned' && <span className="ml-1.5 text-[8px] uppercase tracking-wide text-[var(--critical)]">locked</span>}
                      {p.status === 'always' && <span className="ml-1.5 text-[8px] uppercase tracking-wide text-[var(--ok)]">automatic</span>}
                    </span>
                    <input
                      type="checkbox"
                      checked={p.status === 'always' ? true : p.status === 'banned' ? false : !!createPerms[p.key]}
                      disabled={p.status !== 'editable'}
                      onChange={e => setCreatePerms({ ...createPerms, [p.key]: e.target.checked })}
                      className="w-3.5 h-3.5 accent-[var(--accent)] disabled:cursor-not-allowed"
                    />
                  </label>
                ))}
              </div>
            </div>

            {createError && (
              <p className="text-[10px] text-[var(--critical)] uppercase tracking-wide mb-3">{createError}</p>
            )}

            <button
              onClick={handleCreateUser}
              disabled={createBusy}
              className="w-full py-2.5 bg-[var(--accent)] text-[var(--bg)] text-[10px] font-bold tracking-[0.15em] uppercase hover:bg-[var(--accent)] disabled:opacity-50 transition-colors flex items-center justify-center gap-2"
            >
              <Save size={12} /> {createBusy ? 'Creating…' : 'Create account'}
            </button>
          </div>
        </div>
      )}

      {/* ================= STATIONS TAB ================= */}
      {/* A station COVERS barangays; it owns nothing. Editing a jurisdiction
          only changes who can see what -- no camera, incident or recording
          ever moves, because none of them hang off a station. That's why
          shrinking a jurisdiction here is safe. */}
      {tab === 'stations' && (
        <div className="flex-1 min-h-0 overflow-y-auto custom-scrollbar px-7 pb-7 pt-4 space-y-4">

          <div className="flex items-start gap-2.5 border border-[var(--accent)]/25 bg-[var(--accent)]/[0.05] px-4 py-3">
            <Info size={13} className="text-[var(--accent)] mt-0.5 shrink-0" />
            <p className="text-[10.5px] leading-relaxed text-[var(--text)]">
              <span className="text-[var(--accent)] font-bold">Why a station exists:</span>{' '}
              a barangay account only ever sees its own barangay. A police account
              covers more than one — a precinct's jurisdiction usually spans
              several — so a PNP login can't be scoped to a single barangay the
              way a barangay login is. A station is that grouping: pick which
              barangays it covers below, and every PNP account attached to it sees
              cameras and incidents across all of them. A station owns nothing
              itself — no camera, incident, or recording ever moves when you
              change its jurisdiction, so widening or narrowing one is always safe.
            </p>
          </div>

          <div className="border border-[var(--line)] p-4">
            <div className="text-[8px] tracking-[0.15em] uppercase text-[var(--text-2)] mb-2">
              Register a police station
            </div>
            <div className="flex items-end gap-2">
              <div className="flex-1">
                <input
                  value={newStationName}
                  onChange={e => setNewStationName(e.target.value)}
                  placeholder="e.g. Ormoc City Police Station"
                  className="w-full bg-[var(--bg)] border border-[var(--line)] focus:border-[var(--accent)]/50 p-2.5 text-[11px] text-[var(--text)] outline-none placeholder:text-[var(--text-3)] transition-colors"
                />
              </div>
              <button
                onClick={handleCreateStation}
                disabled={!newStationName.trim() || stationBusy}
                className="px-4 py-2.5 bg-[var(--accent)] text-[#fff] text-[10px] tracking-[0.15em] uppercase disabled:opacity-30 transition-opacity hover:opacity-90"
              >
                Create
              </button>
            </div>
            <p className="text-[9px] leading-relaxed mt-2 text-[var(--text-3)]">
              PNP accounts are scoped to a station's jurisdiction, so a station must
              exist before its commander or officers can be created.
            </p>
          </div>

          {stations.length === 0 ? (
            <div className="border border-[var(--line)] py-14 text-center">
              <p className="text-[10px] tracking-[0.15em] uppercase text-[var(--text-3)]">No police stations registered</p>
            </div>
          ) : stations.map(st => {
            const draft = jurisDraft[st.id] ?? st.barangay_ids;
            const dirty = draft.slice().sort().join(',') !== st.barangay_ids.slice().sort().join(',');
            return (
              <div key={st.id} className="border border-[var(--line)]">
                <div className="flex items-center justify-between gap-4 px-4 py-2.5 border-b border-[var(--line)] bg-[var(--accent)]/[0.03]">
                  <div className="min-w-0">
                    <div className="text-[11px] text-[var(--text)] tracking-wide truncate">{st.name}</div>
                    <div className="text-[9px] text-[var(--text-3)] mt-0.5">
                      {st.id} · {st.staff_count} staff · {st.barangay_ids.length} barangay{st.barangay_ids.length === 1 ? '' : 's'}
                    </div>
                  </div>
                  <button
                    onClick={() => handleDeleteStation(st)}
                    title={st.staff_count ? 'Reassign its staff first' : 'Delete station'}
                    disabled={st.staff_count > 0}
                    className="p-2 border border-[var(--critical)]/30 text-[var(--critical)] disabled:opacity-25 hover:bg-[var(--critical)]/10 transition-colors shrink-0"
                  >
                    <Trash2 size={12} />
                  </button>
                </div>

                <div className="p-4">
                  <div className="text-[8px] tracking-[0.15em] uppercase text-[var(--text-2)] mb-2">
                    Jurisdiction — barangays this station can see
                  </div>
                  {allLocations.length === 0 ? (
                    <p className="text-[10px] text-[var(--text-3)]">No barangays registered yet.</p>
                  ) : (
                    <div className="grid grid-cols-2 gap-px border border-[var(--panel-2)]">
                      {allLocations.map(loc => {
                        const on = draft.includes(loc.id);
                        return (
                          <label
                            key={loc.id}
                            className="flex items-center justify-between px-3 py-2 cursor-pointer bg-[var(--panel)] hover:bg-[var(--panel-2)] transition-colors"
                          >
                            <span className="text-[10px] text-[var(--text)] truncate">
                              {loc.id}
                              {loc.status !== 'approved' && (
                                <span className="text-[var(--warn)] ml-1.5">({loc.status})</span>
                              )}
                            </span>
                            <input
                              type="checkbox"
                              checked={on}
                              onChange={() => toggleJurisdiction(st, loc.id)}
                              className="w-3.5 h-3.5"
                              style={{ accentColor: 'var(--accent)' }}
                            />
                          </label>
                        );
                      })}
                    </div>
                  )}

                  {dirty && (
                    <div className="flex items-center justify-end gap-2 mt-3">
                      <button
                        onClick={() => setJurisDraft(d => { const n = { ...d }; delete n[st.id]; return n; })}
                        className="px-3 py-1.5 border border-[var(--line)] text-[9px] tracking-[0.15em] uppercase text-[var(--text-2)] hover:bg-[var(--panel-2)] transition-colors"
                      >
                        Discard
                      </button>
                      <button
                        onClick={() => saveJurisdiction(st)}
                        disabled={stationBusy}
                        className="px-3 py-1.5 bg-[var(--accent)] text-[#fff] text-[9px] tracking-[0.15em] uppercase disabled:opacity-30 transition-opacity hover:opacity-90 flex items-center gap-1.5"
                      >
                        <Save size={10} /> Save jurisdiction
                      </button>
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* ================= CAMERAS TAB ================= */}
      {tab === 'cameras' && (
        <div className="flex-1 min-h-0 overflow-y-auto custom-scrollbar px-7 pb-7 pt-4 space-y-6">
          {camerasByLocation.length === 0 ? (
            <div className="border border-[var(--line)] py-14 text-center">
              <p className="text-[10px] tracking-[0.15em] uppercase text-[var(--text-3)]">No cameras registered at any location yet</p>
            </div>
          ) : camerasByLocation.map(group => (
            <div key={group.loc} className="border border-[var(--line)]">
              <div className="flex items-center justify-between gap-4 px-4 py-2.5 border-b border-[var(--line)] bg-[var(--accent)]/[0.03]">
                <div className="flex items-center gap-2">
                  <MapPinned size={12} className="text-[var(--accent)]" />
                  <span className="text-[10px] tracking-[0.15em] uppercase text-[var(--text)]">{group.loc}</span>
                  <span className="text-[9px] text-[var(--text-2)]">&middot; {group.cameras.length} camera{group.cameras.length === 1 ? '' : 's'}</span>
                </div>
                <div className="flex items-center gap-2">
                  <SeatChip user={group.precinct} code="PD" />
                  <SeatChip user={group.barangay} code="BG" />
                </div>
              </div>
              <div className="divide-y divide-[var(--panel-2)]">
                {group.cameras.map(cam => (
                  <div key={cam.id} className="flex items-center gap-3 px-4 py-2.5">
                    <Video size={12} className={cam.status === 'online' ? 'text-[var(--ok)]' : 'text-[var(--critical)]'} />
                    <div className="min-w-0 flex-1">
                      <p className="text-[11px] text-[var(--text)] truncate">{cam.name}</p>
                      <p className="text-[9px] text-[var(--text-2)] font-mono truncate">{cam.url}</p>
                    </div>
                    <span className={`text-[8px] font-bold uppercase tracking-wide px-1.5 py-0.5 border ${cam.status === 'online' ? 'border-[var(--ok)]/25 text-[var(--ok)]' : 'border-[var(--critical)]/25 text-[var(--critical)]'}`}>
                      {cam.status}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {tab === 'models' && (
        <div className="flex-1 min-h-0 overflow-y-auto custom-scrollbar px-7 pb-7 pt-4 space-y-4">

          {restartPending && (
            <div className="flex items-start gap-2.5 border border-[var(--warn)]/30 bg-[var(--warn)]/[0.06] px-4 py-3">
              <RotateCw size={12} className="text-[var(--warn)] mt-0.5 shrink-0" />
              <p className="text-[10.5px] leading-relaxed text-[var(--text)]">
                <span className="text-[var(--warn)] font-bold">Restart required.</span>{' '}
                Detection reads this configuration once at startup. Your change is saved
                but will not affect live detection until the AI core restarts.
              </p>
            </div>
          )}

          {/* OPTIMIZE WEIGHTS -- machine-level, not per-model */}
          <div className="border border-[var(--line)]">
            <div className="flex items-center gap-3 px-4 py-3 border-b border-[var(--line)] bg-[var(--accent)]/[0.03]">
              <Gauge size={13} className="text-[var(--accent)]" />
              <div className="min-w-0 flex-1">
                <span className="text-[11px] tracking-[0.12em] uppercase text-[var(--text)]">Optimize weights for this PC</span>
                <p className="text-[9.5px] leading-relaxed text-[var(--text-2)] mt-1">
                  Compiles a TensorRT engine for each model, tuned to this machine's GPU.
                  Optional and reversible — the .pt weights keep working the whole time, and
                  a faster engine is refused unless it agrees with the original model on real
                  input. Takes a few minutes; re-run after a GPU or driver change.
                </p>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <button
                  onClick={() => startOptimize(true)}
                  disabled={optimizeBusy || !!optimizeState?.running}
                  title="Delete every .engine file, returning to the .pt weights"
                  className="flex items-center gap-1.5 px-3 py-1.5 text-[9.5px] tracking-[0.1em] uppercase border border-[var(--line-2)] text-[var(--text-2)] hover:border-[var(--text-3)] hover:text-[var(--text)] disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  <Undo2 size={11} /> Revert
                </button>
                <button
                  onClick={() => startOptimize(false)}
                  disabled={optimizeBusy || !!optimizeState?.running}
                  className="flex items-center gap-1.5 px-3 py-1.5 text-[9.5px] tracking-[0.1em] uppercase border border-[var(--accent)]/50 text-[var(--accent)] hover:bg-[var(--accent)]/10 disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  <Gauge size={11} /> {optimizeState?.running ? 'Running…' : 'Optimize'}
                </button>
              </div>
            </div>

            {optimizeState?.preconditions && !optimizeState.preconditions.ok && (
              <div className="flex items-start gap-2 px-4 py-3 border-b border-[var(--line)]">
                <AlertTriangle size={11} className="text-[var(--warn)] mt-0.5 shrink-0" />
                <p className="text-[9.5px] leading-relaxed text-[var(--text-2)]">
                  <span className="text-[var(--warn)] font-bold">Not available on this machine: </span>
                  {optimizeState.preconditions.detail}
                </p>
              </div>
            )}

            {(optimizeState?.running || optimizeState?.steps?.length) ? (
              <div className="divide-y divide-[var(--panel-2)]">
                {optimizeState.steps.map(s => (
                  <div key={s.label} className="flex items-center justify-between px-4 py-2 text-[9.5px] font-mono">
                    <span className="text-[var(--text-2)]">{s.label}</span>
                    {s.state === 'done' ? (
                      <span className="text-[var(--ok)]">
                        {s.before_ms?.toFixed(1)}ms → {s.after_ms?.toFixed(1)}ms
                        <span className="text-[var(--text)]"> ({s.speedup?.toFixed(2)}x)</span>
                      </span>
                    ) : s.state === 'failed' ? (
                      <span className="text-[var(--critical)] truncate max-w-[50%]" title={s.error}>failed: {s.error}</span>
                    ) : s.state === 'skipped' ? (
                      <span className="text-[var(--text-3)]">skipped — {s.reason}</span>
                    ) : s.state === 'building' ? (
                      <span className="text-[var(--warn)]">building…</span>
                    ) : (
                      <span className="text-[var(--text-3)]">starting…</span>
                    )}
                  </div>
                ))}
                {optimizeState.summary?.kind === 'summary' && optimizeState.summary.combined != null && (
                  <div className="flex items-center justify-between px-4 py-2.5 bg-[var(--accent)]/[0.04]">
                    <span className="text-[9.5px] tracking-[0.1em] uppercase text-[var(--text-2)]">Combined model time</span>
                    <span className="text-[13px] font-mono tabular-nums text-[var(--ok)]">{optimizeState.summary.combined.toFixed(2)}x faster</span>
                  </div>
                )}
                {optimizeState.summary?.kind === 'reverted' && (
                  <div className="px-4 py-2.5 text-[9.5px] text-[var(--text-2)]">
                    Removed {optimizeState.summary.files.length} engine file(s). Back on the .pt weights.
                  </div>
                )}
              </div>
            ) : null}
          </div>

          <p className="text-[10px] leading-relaxed text-[var(--text-2)]">
            Every figure below was measured on footage the model never trained on.
            Turning a model off stops its alerts entirely; it does not affect the others.
          </p>

          {!modelsLoaded ? (
            <div className="border border-[var(--line)] py-14 text-center">
              <p className="text-[10px] tracking-[0.15em] uppercase text-[var(--text-3)]">Loading models…</p>
            </div>
          ) : models.length === 0 ? (
            <div className="border border-[var(--line)] py-14 text-center">
              <p className="text-[10px] tracking-[0.15em] uppercase text-[var(--text-3)]">No detection models configured</p>
            </div>
          ) : models.map(m => {
            const bad = m.metrics?.status === 'disabled' || m.experimental;
            const accent = m.enabled
              ? (bad ? 'var(--warn)' : 'var(--ok)')
              : 'var(--text-3)';
            return (
              <div key={m.name} className="border border-[var(--line)]">

                {/* header: name, state, switch */}
                <div className="flex items-center gap-3 px-4 py-3 border-b border-[var(--line)] bg-[var(--accent)]/[0.03]">
                  <Brain size={13} style={{ color: accent }} />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-[11px] tracking-[0.12em] uppercase text-[var(--text)]">{m.display_name}</span>
                      {m.experimental && (
                        <span className="text-[8px] font-bold uppercase tracking-wide px-1.5 py-0.5 border border-[var(--warn)]/30 text-[var(--warn)]">
                          Experimental
                        </span>
                      )}
                    </div>
                    <p className="text-[9px] text-[var(--text-2)] font-mono truncate mt-0.5">{m.model_path}</p>
                  </div>

                  <span className="text-[8px] font-bold uppercase tracking-wide px-1.5 py-0.5 border"
                        style={{ color: accent, borderColor: accent + '40' }}>
                    {m.enabled ? 'Active' : 'Off'}
                  </span>

                  <button
                    onClick={() => requestToggle(m)}
                    disabled={modelBusy === m.name || (!m.enabled && !m.weights_present)}
                    title={!m.weights_present ? 'Model file is missing' : (m.enabled ? 'Turn off' : 'Turn on')}
                    className="relative w-10 h-[18px] shrink-0 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                    style={{ background: m.enabled ? accent : 'var(--panel-2)', border: `1px solid ${m.enabled ? accent : 'var(--line-2)'}` }}
                  >
                    <span
                      className="absolute top-[2px] w-[12px] h-[12px] transition-all"
                      style={{ left: m.enabled ? '24px' : '2px', background: m.enabled ? 'var(--bg)' : 'var(--text-3)' }}
                    />
                  </button>
                </div>

                {/* measured numbers */}
                {m.metrics?.stats && (
                  <div className="grid grid-cols-2 sm:grid-cols-4 divide-x divide-y sm:divide-y-0 divide-[var(--panel-2)] border-b border-[var(--line)]">
                    {m.metrics.stats.map(s => (
                      <div key={s.label} className="px-4 py-3">
                        <p className="text-[8.5px] tracking-[0.12em] uppercase text-[var(--text-3)]">{s.label}</p>
                        <p className="text-[19px] leading-tight mt-1 font-mono tabular-nums"
                           style={{ color: s.good === false ? 'var(--warn)' : 'var(--text)' }}>
                          {s.value}<span className="text-[11px] text-[var(--text-2)]">{s.unit}</span>
                        </p>
                        {s.note && <p className="text-[9px] leading-snug text-[var(--text-2)] mt-1">{s.note}</p>}
                      </div>
                    ))}
                  </div>
                )}

                {/* settings + provenance */}
                <div className="px-4 py-3 space-y-2">
                  <div className="flex flex-wrap items-center gap-x-6 gap-y-1 text-[9.5px] text-[var(--text-2)] font-mono">
                    <span title="Chosen on the validation split for the best measured accuracy -- not something to hand-tune from the dashboard.">
                      threshold <span className="text-[var(--text)]">{m.threshold}</span>
                    </span>
                    <span>confirmations <span className="text-[var(--text)]">{m.consecutive_required}</span></span>
                    {thresholdCounts[m.name] > 0 && (
                      <span
                        title="Cameras running their own calibrated threshold instead of this global value -- see docs/progress_report_violence_detection.md §28.1 and tools/calibrate_camera_quiet.py."
                      >
                        <span className="text-[var(--ok)]">{thresholdCounts[m.name]}</span> camera{thresholdCounts[m.name] === 1 ? '' : 's'} calibrated
                      </span>
                    )}
                    <span>weights {m.weights_present
                      ? <span className="text-[var(--ok)]">present</span>
                      : <span className="text-[var(--critical)]">MISSING</span>}</span>
                  </div>
                  {m.metrics?.measured_on && (
                    <p className="text-[9.5px] leading-relaxed text-[var(--text-2)]">
                      <span className="text-[var(--text-3)]">Measured on </span>{m.metrics.measured_on}
                    </p>
                  )}
                  {m.metrics?.caveat && (
                    <div className="flex items-start gap-2 pt-1">
                      <Info size={10} className="text-[var(--text-3)] mt-[2px] shrink-0" />
                      <p className="text-[9.5px] leading-relaxed text-[var(--text-2)]">{m.metrics.caveat}</p>
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* OPTIMIZE PROGRESS WINDOW -- opens the moment Optimize/Revert is
          clicked, not only once steps start arriving, so there's never a gap
          where the button visibly did something but nothing on screen shows
          it. Stays open through the finished state so the result (or the
          "Cancelled" notice) is still visible; the user dismisses it with
          Close. Cancel is live for the whole time optimizeState.running is
          true, not just at the start. */}
      {optimizeWindowOpen && (
        <div className="fixed inset-0 z-[130] flex items-center justify-center p-4 bg-[var(--bg)]/85">
          <div className="bg-[var(--panel)] border border-[var(--accent)]/30 w-full max-w-sm p-5 font-mono">
            <div className="flex items-center gap-2 mb-4 pb-3 border-b border-[var(--panel-2)]">
              {optimizeState?.running ? (
                <RotateCw size={13} className="text-[var(--accent)] animate-spin shrink-0" />
              ) : (
                <Gauge size={13} className="text-[var(--accent)] shrink-0" />
              )}
              <span className="text-[10px] tracking-[0.15em] uppercase text-[var(--text)] flex-1">
                {optimizeState?.running
                  ? (optimizeIsRevert ? 'Reverting to .pt weights…' : 'Optimizing for this machine…')
                  : optimizeState?.cancelled
                  ? 'Cancelled'
                  : optimizeState?.error
                  ? 'Optimize failed'
                  : 'Done'}
              </span>
            </div>

            {optimizeState?.steps?.length ? (
              <div className="border border-[var(--line)] divide-y divide-[var(--panel-2)] mb-4 max-h-64 overflow-y-auto">
                {optimizeState.steps.map(s => (
                  <div key={s.label} className="flex items-center justify-between px-3 py-2 text-[9.5px]">
                    <span className="text-[var(--text-2)] truncate pr-2">{s.label}</span>
                    {s.state === 'done' ? (
                      <span className="text-[var(--ok)] shrink-0">{s.speedup?.toFixed(2)}x</span>
                    ) : s.state === 'failed' ? (
                      <span className="text-[var(--critical)] shrink-0">failed</span>
                    ) : s.state === 'skipped' ? (
                      <span className="text-[var(--text-3)] shrink-0">skipped</span>
                    ) : s.state === 'building' ? (
                      <span className="text-[var(--warn)] shrink-0">building…</span>
                    ) : (
                      <span className="text-[var(--text-3)] shrink-0">starting…</span>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-[9.5px] text-[var(--text-2)] mb-4">
                {optimizeState?.running ? 'Starting…' : 'Waiting for the first step…'}
              </p>
            )}

            {optimizeState?.cancelled && (
              <p className="text-[9.5px] leading-relaxed text-[var(--text-2)] mb-4">
                Stopped partway through. Any model already finished before the cancel keeps
                its engine; a model that was mid-build was left on the .pt weights.
              </p>
            )}
            {optimizeState?.error && !optimizeState?.cancelled && (
              <p className="text-[9.5px] leading-relaxed text-[var(--critical)] mb-4">{optimizeState.error}</p>
            )}

            {optimizeState?.running ? (
              <button
                onClick={cancelOptimize}
                disabled={cancelBusy || !!optimizeState?.cancel_requested}
                className="w-full py-2.5 text-[10px] tracking-[0.12em] uppercase border border-[var(--critical)]/50 text-[var(--critical)] hover:bg-[var(--critical)]/10 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {optimizeState?.cancel_requested ? 'Cancelling…' : 'Cancel'}
              </button>
            ) : (
              <button
                onClick={() => setOptimizeWindowOpen(false)}
                className="w-full py-2.5 text-[10px] tracking-[0.12em] uppercase border border-[var(--line-2)] text-[var(--text)] hover:border-[var(--text-3)]"
              >
                Close
              </button>
            )}
          </div>
        </div>
      )}

      {/* CONFIRM ENABLING A MODEL THAT MEASURED BADLY */}
      {confirmEnable && (
        <div className="fixed inset-0 z-[130] flex items-center justify-center p-4 bg-[var(--bg)]/85">
          <div className="bg-[var(--panel)] border border-[var(--warn)]/30 w-full max-w-md p-6 font-mono">
            <div className="flex items-center gap-2 mb-4 pb-3 border-b border-[var(--panel-2)]">
              <AlertTriangle size={14} className="text-[var(--warn)]" />
              <span className="text-[10px] tracking-[0.15em] uppercase text-[var(--text)]">
                Turn on {confirmEnable.display_name}?
              </span>
            </div>
            <p className="text-[10.5px] leading-relaxed text-[var(--text-2)] mb-3">
              This model did not meet the bar for deployment. Its own measurements:
            </p>
            <div className="border border-[var(--line)] divide-y divide-[var(--panel-2)] mb-4">
              {confirmEnable.metrics?.stats?.map(s => (
                <div key={s.label} className="flex items-baseline justify-between px-3 py-2">
                  <span className="text-[9.5px] text-[var(--text-2)]">{s.label}</span>
                  <span className="text-[11px] tabular-nums"
                        style={{ color: s.good === false ? 'var(--warn)' : 'var(--text)' }}>
                    {s.value}{s.unit}
                  </span>
                </div>
              ))}
            </div>
            {confirmEnable.metrics?.caveat && (
              <p className="text-[9.5px] leading-relaxed text-[var(--text-2)] mb-5">
                {confirmEnable.metrics.caveat}
              </p>
            )}
            <div className="flex gap-2">
              <button
                onClick={() => setConfirmEnable(null)}
                className="flex-1 py-2.5 text-[10px] tracking-[0.12em] uppercase border border-[var(--line-2)] text-[var(--text)] hover:border-[var(--text-3)]"
              >
                Keep it off
              </button>
              <button
                onClick={() => { const m = confirmEnable; setConfirmEnable(null); applyModelChange(m, { enabled: true }); }}
                className="flex-1 py-2.5 text-[10px] tracking-[0.12em] uppercase border border-[var(--warn)]/40 text-[var(--warn)] hover:bg-[var(--warn)]/10"
              >
                Turn on anyway
              </button>
            </div>
          </div>
        </div>
      )}

      {/* EDIT + PERMISSIONS MODAL */}
      {editingUser && (
        <div className="fixed inset-0 z-[130] flex items-center justify-center p-4 bg-[var(--bg)]/85">
          <div className="bg-[var(--panel)] border border-[var(--line)] w-full max-w-sm p-6 max-h-[90vh] overflow-y-auto custom-scrollbar font-mono">
            <div className="flex items-center justify-between mb-5 pb-4 border-b border-[var(--panel-2)]">
              <div className="flex items-center gap-2">
                <span className={`text-[8px] font-bold px-1.5 py-1 border ${(ROLE_STYLES[editingUser.role] || ROLE_STYLES.POLICE).border} ${(ROLE_STYLES[editingUser.role] || ROLE_STYLES.POLICE).text}`}>
                  {(ROLE_STYLES[editingUser.role] || ROLE_STYLES.POLICE).code}
                </span>
                <span className="text-[10px] tracking-[0.15em] uppercase text-[var(--text)]">{editingUser.role.replace('_', ' ')}</span>
              </div>
              <button onClick={() => setEditingUser(null)}><X size={15} className="text-[var(--text-2)] hover:text-[var(--text)]" /></button>
            </div>
            <div className="space-y-3">
              <div>
                <label className="text-[8px] tracking-[0.15em] uppercase text-[var(--text-2)] mb-1 block">Username</label>
                <input
                  value={editDraft.username}
                  onChange={e => setEditDraft({ ...editDraft, username: e.target.value })}
                  className="w-full bg-[var(--bg)] border border-[var(--line)] focus:border-[var(--accent)]/50 p-2.5 text-[11px] text-[var(--text)] outline-none transition-colors"
                />
              </div>
              <div>
                <label className="text-[8px] tracking-[0.15em] uppercase text-[var(--text-2)] mb-1 block">Assignment</label>
                <input
                  value={editDraft.assignment}
                  onChange={e => setEditDraft({ ...editDraft, assignment: e.target.value })}
                  className="w-full bg-[var(--bg)] border border-[var(--line)] focus:border-[var(--accent)]/50 p-2.5 text-[11px] text-[var(--text)] outline-none transition-colors"
                />
              </div>

              {/* BUG FOUND 2026-08-23: an account's location could be set at
                  creation but never changed afterward -- this editor and the
                  station_id field it PATCHes were both simply missing. DEVTEAM
                  accounts have no org of their own (chk_user_scope requires
                  both NULL), so there's nothing to show them here. */}
              {editingUser.role !== 'DEVTEAM' && (
                PNP_ROLES.includes(editingUser.role) ? (
                  <div>
                    <label className="text-[8px] tracking-[0.15em] uppercase text-[var(--text-2)] mb-1 block">
                      Police station
                    </label>
                    <select
                      value={editDraft.station_id}
                      onChange={e => setEditDraft({ ...editDraft, station_id: e.target.value })}
                      className="w-full bg-[var(--bg)] border border-[var(--line)] focus:border-[var(--accent)]/50 p-2.5 text-[11px] text-[var(--text)] outline-none transition-colors"
                    >
                      <option value="">
                        {stations.length ? 'select a station…' : 'no stations yet — create one in the Stations tab'}
                      </option>
                      {stations.map(s => (
                        <option key={s.id} value={s.id}>
                          {s.name} ({s.barangay_ids.length} barangay{s.barangay_ids.length === 1 ? '' : 's'})
                        </option>
                      ))}
                    </select>
                  </div>
                ) : (
                  <div>
                    <label className="text-[8px] tracking-[0.15em] uppercase text-[var(--text-2)] mb-1 block">
                      Barangay
                    </label>
                    <input
                      value={editDraft.barangay_id}
                      onChange={e => setEditDraft({ ...editDraft, barangay_id: e.target.value })}
                      placeholder="barangay id"
                      className="w-full bg-[var(--bg)] border border-[var(--line)] focus:border-[var(--accent)]/50 p-2.5 text-[11px] text-[var(--text)] outline-none placeholder:text-[var(--text-3)] transition-colors"
                    />
                  </div>
                )
              )}

              <div>
                <label className="text-[8px] tracking-[0.15em] uppercase text-[var(--text-2)] mb-1 block">New password</label>
                <div className="relative">
                  <input
                    type={showEditPassword ? 'text' : 'password'}
                    value={editDraft.password}
                    onChange={e => setEditDraft({ ...editDraft, password: e.target.value })}
                    placeholder="leave blank to keep current"
                    className="w-full bg-[var(--bg)] border border-[var(--line)] focus:border-[var(--accent)]/50 p-2.5 pr-8 text-[11px] text-[var(--text)] outline-none placeholder:text-[var(--text-3)] transition-colors"
                  />
                  <button
                    type="button"
                    onClick={() => setShowEditPassword(s => !s)}
                    title={showEditPassword ? 'Hide password' : 'Show password'}
                    tabIndex={-1}
                    className="absolute right-2 top-1/2 -translate-y-1/2 text-[var(--text-2)] hover:text-[var(--text)] transition-colors"
                  >
                    {showEditPassword ? <EyeOff size={12} /> : <Eye size={12} />}
                  </button>
                </div>
              </div>

              <div className="pt-3 border-t border-[var(--panel-2)]">
                <div className="text-[8px] tracking-[0.15em] uppercase text-[var(--text-2)] flex items-center gap-1.5 mb-2">
                  <KeyRound size={10} /> Permissions
                </div>
                {permissionNoteFor(editingUser.role) && (
                  <p className="text-[9px] leading-relaxed text-[var(--text-3)] mb-2">{permissionNoteFor(editingUser.role)}</p>
                )}
                <div className="border border-[var(--panel-2)] divide-y divide-[var(--panel-2)]">
                  {permissionRowsFor(editingUser.role).map(p => (
                    <label
                      key={p.key}
                      title={p.status === 'banned' ? 'The backend refuses this for every PNP account, any tier — checking it would not do anything.' : p.status === 'always' ? 'Admin-tier accounts get this automatically.' : undefined}
                      className={`flex items-center justify-between px-3 py-2 ${p.status === 'editable' ? 'cursor-pointer hover:bg-[var(--panel)]' : 'cursor-not-allowed opacity-40'} transition-colors`}
                    >
                      <span className="text-[10px] text-[var(--text)]">
                        {p.label}
                        {p.status === 'banned' && <span className="ml-1.5 text-[8px] uppercase tracking-wide text-[var(--critical)]">locked</span>}
                        {p.status === 'always' && <span className="ml-1.5 text-[8px] uppercase tracking-wide text-[var(--ok)]">automatic</span>}
                      </span>
                      <input
                        type="checkbox"
                        checked={p.status === 'always' ? true : p.status === 'banned' ? false : !!permsDraft[p.key]}
                        disabled={p.status !== 'editable'}
                        onChange={e => setPermsDraft({ ...permsDraft, [p.key]: e.target.checked })}
                        className="w-3.5 h-3.5 accent-[var(--accent)] disabled:cursor-not-allowed"
                      />
                    </label>
                  ))}
                </div>
              </div>

              <button onClick={saveEdit} className="w-full py-2.5 bg-[var(--accent)] text-[var(--bg)] text-[10px] font-bold tracking-[0.15em] uppercase hover:bg-[var(--accent)] transition-colors flex items-center justify-center gap-2 mt-2">
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
    <div className={`flex items-center gap-2.5 py-3 pr-6 ${!last ? 'border-r border-[var(--panel-2)] mr-6' : ''}`}>
      <span className={accent || 'text-[var(--text-2)]'}>{icon}</span>
      <div className="leading-tight">
        <span className={`text-[13px] font-semibold tabular-nums ${accent || 'text-[var(--text)]'}`}>{val}</span>
        <p className="text-[8px] tracking-[0.15em] uppercase text-[var(--text-2)]">{label}</p>
      </div>
    </div>
  );
}

function TabButton({ icon, label, active, onClick, badge }: any) {
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-2 px-4 py-2.5 text-[9px] tracking-[0.15em] uppercase border-b-2 -mb-px transition-colors ${
        active ? 'border-[var(--accent)] text-[var(--text)]' : 'border-transparent text-[var(--text-2)] hover:text-[var(--text)]'
      }`}
    >
      {icon} {label}
      {typeof badge === 'number' && badge > 0 && (
        <span className={`text-[8px] px-1.5 py-0.5 rounded-full ${active ? 'bg-[var(--accent)] text-[var(--bg)]' : 'bg-[var(--line)] text-[var(--text)]'}`}>{badge}</span>
      )}
    </button>
  );
}

function SeatChip({ user, code }: { user?: ManagedUser; code: string }) {
  const style = ROLE_STYLES[code === 'PD' ? 'PNP_ADMIN' : 'BARANGAY_ADMIN'];
  if (!user) {
    return (
      <span className="flex items-center gap-1.5 text-[9px] px-2 py-1 border border-dashed border-[var(--line)] text-[var(--text-3)] uppercase tracking-wide">
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
  const [show, setShow] = useState(false);
  const isPassword = type === 'password';
  return (
    <div>
      <label className="text-[8px] tracking-[0.15em] uppercase text-[var(--text-2)] mb-1 block">{label}</label>
      <div className={isPassword ? 'relative' : undefined}>
        <input
          type={isPassword ? (show ? 'text' : 'password') : type}
          value={value}
          onChange={e => onChange(e.target.value)}
          placeholder={placeholder}
          className={`w-full bg-[var(--bg)] border border-[var(--line)] focus:border-[var(--accent)]/50 p-2.5 text-[11px] text-[var(--text)] outline-none placeholder:text-[var(--text-3)] transition-colors ${isPassword ? 'pr-8' : ''}`}
        />
        {isPassword && (
          <button
            type="button"
            onClick={() => setShow(s => !s)}
            title={show ? 'Hide password' : 'Show password'}
            tabIndex={-1}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-[var(--text-2)] hover:text-[var(--text)] transition-colors"
          >
            {show ? <EyeOff size={12} /> : <Eye size={12} />}
          </button>
        )}
      </div>
    </div>
  );
}