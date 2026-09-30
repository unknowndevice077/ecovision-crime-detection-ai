"use client";

import React, { useState, useMemo, useEffect, useRef } from 'react';
import {
  ShieldAlert, Wifi, WifiOff, ShieldCheck, ShieldX,
  Search, LogOut, KeyRound, Users2, MapPinned,
  Activity, Video, Film, Radio, LayoutGrid, ClipboardList, UserPlus,
  Brain, AlertTriangle, Info, RotateCw, Gauge, Undo2
} from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useLiveChannel, useWebSocketContext } from '../../context/WebSocketContext';
import { useRuntimeConfig } from '../../hooks/useRuntimeConfig';
import {
  DEFAULT_ROLE_STYLE, ROLE_STYLES, CameraRow, CustomRole, EmptyPane, InfoRow, ManagedUser, PaneHeader,
  PendingLocation, PendingSignup, Station, applicationStatusLabel, authHeaders,
} from './devteam/shared';
import ManageUsersPane from './devteam/ManageUsersPane';
import CreateUserPane from './devteam/CreateUserPane';
import ApprovalsPane, { ApplicationAction, ApplicationTarget } from './devteam/ApprovalsPane';
import StationsPane from './devteam/StationsPane';
import RolesPane from './devteam/RolesPane';
import DetectionQualityPanel from './devteam/DetectionQualityPanel';
import { serverDateTime } from '../../lib/time';
import { maskStreamUrl } from '../../lib/streams';
import ThemeToggle from '../ThemeToggle';

// Split 2026-09-23 (explicit teacher requirement: separate configuration/
// CRUD from monitoring in the DevTeam console). Monitoring is read-only --
// Directory and Users no longer render Edit/Delete anywhere; every mutating
// action (including the account editor those two tabs used to open inline)
// lives under Configuration's "Manage Users" tab instead. This is a single
// frontend-only reorganization: no endpoint changes, no new permission
// checks -- the backend already required DEVTEAM for every one of these
// calls regardless of which tab triggered them.
type Section = 'monitoring' | 'configuration';
type Tab = 'directory' | 'users' | 'manage_users' | 'approvals' | 'create' | 'cameras' | 'stations' | 'models' | 'audit' | 'permissions';

const SECTION_TABS: Record<Section, Tab[]> = {
  monitoring: ['directory', 'users', 'cameras', 'models'],
  configuration: ['manage_users', 'create', 'permissions', 'approvals', 'stations', 'audit'],
};

// 2026-09-29 user request: Configuration's tab order is drag-and-drop
// reorderable, EXCEPT Manage Users and Create User, which always lead --
// "first and second is manage users and create users next are the other
// stuff." CONFIG_TAIL_DEFAULT_ORDER is just the default arrangement of
// that draggable remainder; the live order lives in configTabOrder state
// (persisted to localStorage), not here.
const CONFIG_TAIL_DEFAULT_ORDER: Tab[] = ['permissions', 'approvals', 'stations', 'audit'];
const CONFIG_TAB_ORDER_STORAGE_KEY = 'devteam_config_tab_order_v1';

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

type AuditEntry = {
  id: string;
  actor_user_id: number | null;
  actor_username: string;
  action: string;
  target_type: string;
  target_id: string;
  target_snapshot: Record<string, any> | null;
  created_at: string;
};

export default function DevteamView() {
  const { apiUrl: API_URL } = useRuntimeConfig();
  const router = useRouter();
  const [data, setData] = useState<any>(null);
  const [cameras, setCameras] = useState<CameraRow[]>([]);
  const [stations, setStations] = useState<Station[]>([]);
  const [pendingLocations, setPendingLocations] = useState<PendingLocation[]>([]);
  const [pendingSignups, setPendingSignups] = useState<PendingSignup[]>([]);
  const [rejectedLocations, setRejectedLocations] = useState<PendingLocation[]>([]);
  const [rejectedSignups, setRejectedSignups] = useState<PendingSignup[]>([]);
  const [allLocations, setAllLocations] = useState<PendingLocation[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);
  const [section, setSection] = useState<Section>('monitoring');
  const [tab, setTab] = useState<Tab>('directory');
  const switchSection = (s: Section) => { setSection(s); setTab(SECTION_TABS[s][0]); };

  // 2026-09-29: drag-and-drop tab reordering for Configuration's draggable
  // tail (Permissions/Approvals/Stations/Audit Log) -- Manage Users and
  // Create User are rendered separately, always first, never draggable.
  // Persisted per-browser (localStorage) since this is a personal layout
  // preference, not something that needs to follow the account across
  // devices. Loaded lazily (useState initializer, not an effect) so the
  // tab bar never flashes the default order before snapping to a saved one.
  // Reordering only engages behind an explicit Edit button (not "always
  // draggable" or a long-press gesture) so an ordinary click on a tab
  // never risks starting a drag.
  const [configEditMode, setConfigEditMode] = useState(false);
  const [configTabOrder, setConfigTabOrder] = useState<Tab[]>(() => {
    if (typeof window === "undefined") return CONFIG_TAIL_DEFAULT_ORDER;
    try {
      const raw = localStorage.getItem(CONFIG_TAB_ORDER_STORAGE_KEY);
      if (!raw) return CONFIG_TAIL_DEFAULT_ORDER;
      const saved: string[] = JSON.parse(raw);
      // Keep only tabs that still belong in the draggable tail, then
      // append any tail tab missing from the saved order (a future tab
      // added after this preference was saved) so nothing silently
      // disappears from the bar.
      const valid = saved.filter((t): t is Tab => CONFIG_TAIL_DEFAULT_ORDER.includes(t as Tab));
      const missing = CONFIG_TAIL_DEFAULT_ORDER.filter(t => !valid.includes(t));
      return [...valid, ...missing];
    } catch {
      return CONFIG_TAIL_DEFAULT_ORDER;
    }
  });
  const [draggedConfigTab, setDraggedConfigTab] = useState<Tab | null>(null);

  // Long-press (~500ms without moving) on a reorderable tab enters reorder
  // mode; Esc or a pointer-down outside the tabs leaves it.
  const longPressTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const longPressOriginRef = useRef<{ x: number; y: number } | null>(null);
  const longPressFiredRef = useRef(false);
  const cancelLongPress = () => {
    if (longPressTimerRef.current) clearTimeout(longPressTimerRef.current);
    longPressTimerRef.current = null;
    longPressOriginRef.current = null;
  };
  const startLongPress = (x: number, y: number) => {
    if (configEditMode) return;
    cancelLongPress();
    longPressOriginRef.current = { x, y };
    longPressTimerRef.current = setTimeout(() => {
      longPressFiredRef.current = true;
      setConfigEditMode(true);
      longPressTimerRef.current = null;
    }, 500);
  };
  const moveLongPress = (x: number, y: number) => {
    const o = longPressOriginRef.current;
    if (o && Math.hypot(x - o.x, y - o.y) > 8) cancelLongPress();
  };
  useEffect(() => {
    if (!configEditMode) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setConfigEditMode(false); };
    const onDown = (e: PointerEvent) => {
      if (!(e.target as HTMLElement | null)?.closest('[data-config-tab]')) setConfigEditMode(false);
    };
    window.addEventListener('keydown', onKey);
    window.addEventListener('pointerdown', onDown);
    return () => { window.removeEventListener('keydown', onKey); window.removeEventListener('pointerdown', onDown); };
  }, [configEditMode]);

  const reorderConfigTab = (target: Tab) => {
    if (!draggedConfigTab || draggedConfigTab === target) return;
    setConfigTabOrder(prev => {
      // targetIdx must come from `prev` (before the dragged tab is removed).
      // Computing it from the post-removal array instead (the earlier bug)
      // shifts every index after the dragged tab's old spot down by one,
      // so a forward drag always landed one slot short of the drop target
      // -- an adjacent forward drag silently did nothing at all.
      const targetIdx = prev.indexOf(target);
      const next = prev.filter(t => t !== draggedConfigTab);
      next.splice(targetIdx, 0, draggedConfigTab);
      try { localStorage.setItem(CONFIG_TAB_ORDER_STORAGE_KEY, JSON.stringify(next)); } catch { /* per-device convenience only */ }
      return next;
    });
  };
  // Separate from `search` (Directory tab's own callsign/location filter) --
  // sharing one box across tabs meant switching tabs silently carried a
  // stale filter over, or typing in one unexpectedly filtered the other.
  const [userSearch, setUserSearch] = useState('');
  // Keys into locationDirectory below: a barangay's own id for a normal
  // location row, or `station:<id>` for a station that has no barangay
  // jurisdiction assigned yet (see locationDirectory's unassignedStations --
  // that bucket exists so a station created before its jurisdiction is set
  // still has somewhere for its PNP accounts to show up, instead of quietly
  // disappearing from a purely per-barangay view).
  const [selectedLocationKey, setSelectedLocationKey] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [pendingActionIds, setPendingActionIds] = useState<Set<string | number>>(new Set());
  const [toast, setToast] = useState('');
  const { connected } = useWebSocketContext();

  const fetchOverview = async () => {
    try {
      const [overviewRes, locationsRes, allLocationsRes, stationsRes, signupsRes, rejLocRes, rejSignupRes] = await Promise.all([
        fetch(`${API_URL}/api/devteam/overview`, { headers: authHeaders() }),
        fetch(`${API_URL}/api/devteam/locations?status=pending`, { headers: authHeaders() }),
        fetch(`${API_URL}/api/devteam/locations`, { headers: authHeaders() }),
        fetch(`${API_URL}/api/devteam/stations`, { headers: authHeaders() }),
        fetch(`${API_URL}/api/devteam/signups?status=pending`, { headers: authHeaders() }),
        fetch(`${API_URL}/api/devteam/locations?status=rejected`, { headers: authHeaders() }),
        fetch(`${API_URL}/api/devteam/signups?status=rejected`, { headers: authHeaders() }),
      ]);
      if (overviewRes.ok && locationsRes.ok) {
        const overview = await overviewRes.json();
        setData(overview);
        setCameras(overview.cameras || []);
        setPendingLocations(await locationsRes.json());
        if (allLocationsRes.ok) setAllLocations(await allLocationsRes.json());
        if (stationsRes.ok) setStations(await stationsRes.json());
        if (signupsRes.ok) setPendingSignups(await signupsRes.json());
        if (rejLocRes.ok) setRejectedLocations(await rejLocRes.json());
        if (rejSignupRes.ok) setRejectedSignups(await rejSignupRes.json());
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

  // Not a static mount (VERIFICATION_DOCS_DIR is authenticated-only, see
  // backend.py) -- a plain <a href> can't carry the Authorization header,
  // so this fetches the bytes with the token and opens them as a blob URL
  // instead. Revoked on close via the tab's own lifecycle (best-effort;
  // browsers don't guarantee a revoke callback for a manually-opened tab,
  // but this is a small, occasional admin action, not something run at
  // any volume that would make a leaked blob URL meaningfully costly).
  const viewVerificationDocument = async (userId: number) => {
    try {
      const res = await fetch(`${API_URL}/api/users/${userId}/verification_document`, { headers: authHeaders() });
      if (!res.ok) { flash('No document on file, or you are not authorized to view it.'); return; }
      const blob = await res.blob();
      window.open(URL.createObjectURL(blob), '_blank');
    } catch {
      flash('Backend connection failure.');
    }
  };

  const reviewVerification = async (userId: number, decision: 'verified' | 'rejected') => {
    try {
      const res = await fetch(`${API_URL}/api/devteam/users/${userId}/verification`, {
        method: 'POST', headers: authHeaders(), body: JSON.stringify({ decision }),
      });
      const d = await res.json().catch(() => ({}));
      if (res.ok) { flash(`Marked ${decision}.`); fetchOverview(); }
      else flash(d.detail || 'Could not update.');
    } catch {
      flash('Backend connection failure.');
    }
  };

  // ── Audit log (2026-09-23) ──────────────────────────────────────────────
  // GET /api/devteam/audit_log returns a raw array (not {entries:[...]}) --
  // see backend.py's devteam_list_audit_log. Fetched on demand like the AI
  // Models tab, not on every overview poll: this is a review surface, not
  // live-incident data.
  const [auditEntries, setAuditEntries] = useState<AuditEntry[]>([]);
  const [auditLoaded, setAuditLoaded] = useState(false);
  const [auditActionFilter, setAuditActionFilter] = useState('');
  const [auditBusyIds, setAuditBusyIds] = useState<Set<string>>(new Set());
  const [selectedAuditId, setSelectedAuditId] = useState<string | null>(null);

  const fetchAuditLog = async (action?: string) => {
    try {
      const qs = action ? `?q=${encodeURIComponent(action)}&limit=500` : '?limit=500';
      const res = await fetch(`${API_URL}/api/devteam/audit_log${qs}`, { headers: authHeaders() });
      if (res.ok) setAuditEntries(await res.json());
    } catch { /* leave whatever was last shown */ }
    finally { setAuditLoaded(true); }
  };

  // ── Phase 2: resource-scoped permissions + custom roles (2026-09-23) ────
  // "Dice every permission down to the smallest unit" -- the user's own
  // example was camera-level, and that's the only resource type wired into
  // an actual enforcement point right now (GET /api/cameras filters on a
  // camera-scoped view_map grant, see backend.py). The picker below is
  // deliberately narrow to just that -- offering other permission keys
  // here would create grant rows nothing ever consults, the same "looks
  // like a promise the app doesn't keep" problem lib/permissions.ts's own
  // top comment warns about.
  const [customRoles, setCustomRoles] = useState<CustomRole[]>([]);
  const [rolesLoaded, setRolesLoaded] = useState(false);
  const fetchCustomRoles = async () => {
    try {
      const res = await fetch(`${API_URL}/api/custom_roles`, { headers: authHeaders() });
      if (res.ok) setCustomRoles(await res.json());
    } catch { /* leave whatever was last shown */ }
    finally { setRolesLoaded(true); }
  };

  const restoreAuditEntry = async (entry: AuditEntry) => {
    setAuditBusyIds(prev => new Set(prev).add(entry.id));
    try {
      const res = await fetch(`${API_URL}/api/devteam/audit_log/${entry.id}/restore`, { method: 'POST', headers: authHeaders() });
      const d = await res.json().catch(() => ({}));
      if (res.ok) { flash(`${entry.target_type} ${entry.target_id} restored.`); fetchAuditLog(auditActionFilter || undefined); fetchOverview(); }
      else flash(d.detail || 'Could not restore.');
    } catch {
      flash('Backend connection failure.');
    } finally {
      setAuditBusyIds(prev => { const n = new Set(prev); n.delete(entry.id); return n; });
    }
  };

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
  // BUG FOUND 2026-09-03: fetchThresholdCounts was only ever called from the
  // "AI Models" tab's own onClick -- no live-channel subscription and not
  // even the 60s useLiveChannel fallback poll. backend.py already broadcasts
  // "camera_thresholds" on both set and clear (see set_camera_threshold /
  // clear_camera_threshold), so recalibrating a camera via
  // tools/calibrate_camera_quiet.py while an admin sits on this tab never
  // updated the "N cameras calibrated" count until they clicked away and
  // back. Purely informational per the comment above, but still stale for
  // no reason once the backend was already telling anyone listening.
  useLiveChannel("camera_thresholds", fetchThresholdCounts);

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

  // One handler for every application decision. A barangay application is
  // keyed by the barangay id, a PNP one (or a barangay applicant for an
  // already-approved barangay) by the applicant's user id. Returns the
  // server's refusal, if any, for the pane to show next to the button --
  // a 409 here means someone else decided it first, so the lists reload.
  const decideApplication = async (
    target: ApplicationTarget, action: ApplicationAction, body: Record<string, unknown>,
  ): Promise<string | null> => {
    const url = target.kind === 'location'
      ? `${API_URL}/api/devteam/locations/${target.id}/${action}`
      : `${API_URL}/api/devteam/users/${target.id}/${action}_signup`;
    setPendingActionIds(prev => new Set(prev).add(target.id));
    try {
      const res = await fetch(url, { method: 'POST', headers: authHeaders(), body: JSON.stringify(body) });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) {
        if (res.status === 409) fetchOverview();
        return d.detail || `Could not ${action} this application.`;
      }
      fetchOverview();
      if (action === 'approve') {
        const uncovered = target.kind === 'location' && Array.isArray(d.covered_by) && d.covered_by.length === 0;
        flash(uncovered ? 'Approved. No station covers this barangay yet -- assign it in Stations.' : 'Application approved.');
      } else flash(action === 'reject' ? 'Application rejected.' : 'Application reopened -- it is back in Pending.');
      return null;
    } catch {
      return 'Backend connection failure.';
    } finally {
      setPendingActionIds(prev => { const n = new Set(prev); n.delete(target.id); return n; });
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

    // Only approved barangays are places in the directory. Pending and
    // rejected ones are applications and live in Approvals -- a pending one
    // used to show here with its applicant labelled ADMIN and a prompt to
    // assign it a station, which the Stations tab (rightly) refuses.
    const locations: LocationEntry[] = allLocations
      .filter(loc => (loc.status || 'approved') === 'approved')
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
                      {/* Visible without opening the edit modal -- DevTeam
                          scanning this list should see at a glance which
                          admins are on the automatic default and which have
                          had that overridden (see backend.py's
                          custom_permissions / 2026-09-04 override feature). */}
                      {isAdmin && u.custom_permissions && (
                        <span className="ml-1.5 text-[8px] tracking-[0.1em] uppercase" style={{ color: 'var(--accent)' }}>· custom perms</span>
                      )}
                    </p>
                    <p className="text-[9px] text-[var(--text-2)] truncate">{u.assignment}</p>
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
    // Applications (pending/rejected barangays) aren't places yet: listed
    // only if they somehow already have cameras, never as empty groups.
    const approved = new Set(allLocations.filter(l => (l.status || 'approved') === 'approved').map(l => l.id));
    locationPairs.forEach(p => {
      const key = p.loc || '—';
      if (!approved.has(key) && !map.has(key)) return;
      const entry = map.get(key) || { cameras: [] };
      entry.precinct = p.precinct;
      entry.barangay = p.barangay;
      map.set(key, entry);
    });
    return Array.from(map.entries()).map(([loc, v]) => ({ loc, ...v }));
  }, [cameras, locationPairs, allLocations]);

  // Monitoring "Users" tab (read-only). Editing lives in ManageUsersPane.
  const userOrgName = (u: ManagedUser) => {
    if (u.role === 'DEVTEAM') return 'DevTeam HQ';
    if (u.station_id) return stations.find(st => st.id === u.station_id)?.name ?? u.station_id;
    if (u.barangay_id) return allLocations.find(l => l.id === u.barangay_id)?.name ?? u.barangay_id;
    return '—';
  };

  const visibleUsers = useMemo(() => {
    if (!data) return [] as ManagedUser[];
    const q = userSearch.trim().toLowerCase();
    return ([...data.users] as ManagedUser[])
      .sort((a, b) => a.username.localeCompare(b.username))
      .filter(u => !q ||
        u.username.toLowerCase().includes(q) ||
        u.role.toLowerCase().includes(q) ||
        userOrgName(u).toLowerCase().includes(q));
  }, [data, userSearch, stations, allLocations]);

  const renderUserListRow = (u: ManagedUser) => {
    const rowStyle = ROLE_STYLES[u.role] || DEFAULT_ROLE_STYLE;
    return (
      <div key={u.id} className={`flex items-center gap-3 px-3 py-2.5 transition-opacity ${pendingActionIds.has(u.id) ? 'opacity-40' : ''}`}>
        <span className={`text-[8px] font-bold px-1.5 py-1 border ${rowStyle.border} ${rowStyle.text} shrink-0`}>{rowStyle.code}</span>
        <div className="min-w-0" style={{ width: '22%' }}>
          <p className="text-[11px] text-[var(--text)] truncate">{u.username}</p>
          <p className="text-[9px] text-[var(--text-2)] truncate">{u.assignment}</p>
        </div>
        <p className="text-[10px] text-[var(--text-2)] truncate" style={{ width: '18%' }}>
          {u.role.replace(/_/g, ' ')}
          {(u.role === 'PNP_ADMIN' || u.role === 'BARANGAY_ADMIN') && u.custom_permissions && (
            <span className="ml-1.5 text-[8px] tracking-[0.1em] uppercase" style={{ color: 'var(--accent)' }}>· custom perms</span>
          )}
          {applicationStatusLabel(u) && (
            <span className="ml-1.5 text-[8px] tracking-[0.1em] uppercase" style={{ color: applicationStatusLabel(u)!.color }}>· {applicationStatusLabel(u)!.text}</span>
          )}
        </p>
        <p className="text-[10px] text-[var(--text-2)] truncate flex-1 min-w-0">{userOrgName(u)}</p>
        <p className="text-[9px] shrink-0" style={{ color: u.last_login ? 'var(--text-2)' : 'var(--text-3)', width: '140px' }}>
          {serverDateTime(u.last_login, 'Never logged in')}
        </p>
        {/* Identity verification (#8, 2026-09-23) -- pending is the only
            state DevTeam needs to act on here; unverified (never
            submitted) and verified/rejected (already resolved) render as
            plain, non-actionable text so this stays quiet outside the
            handful of rows that actually need a decision. */}
        <div className="shrink-0 flex items-center gap-1" style={{ width: '150px' }}>
          {u.role !== 'DEVTEAM' && u.verification_status === 'pending' ? (
            <>
              <button onClick={() => viewVerificationDocument(u.id)} className="text-[9px] underline text-[var(--text-2)] hover:text-[var(--accent)] transition-colors">View ID</button>
              <button title="Mark ID as verified" aria-label="Mark ID as verified" onClick={() => reviewVerification(u.id, 'verified')} className="p-1 text-[var(--text-2)] hover:text-[var(--ok)] transition-colors"><ShieldCheck size={12} /></button>
              <button title="Reject this ID" aria-label="Reject this ID" onClick={() => reviewVerification(u.id, 'rejected')} className="p-1 text-[var(--text-2)] hover:text-[var(--critical)] transition-colors"><ShieldX size={12} /></button>
            </>
          ) : u.role !== 'DEVTEAM' ? (
            <span
              className="text-[9px] uppercase tracking-wide"
              style={{ color: u.verification_status === 'verified' ? 'var(--ok)' : u.verification_status === 'rejected' ? 'var(--critical)' : 'var(--text-3)' }}
            >
              {u.verification_status || 'unverified'}
            </span>
          ) : null}
        </div>
      </div>
    );
  };

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
            <h1 className="text-[11px] tracking-[0.2em] uppercase text-[var(--text)]">Developer Console</h1>
            <p className="text-[9px] tracking-[0.15em] text-[var(--text-2)] uppercase">All locations &middot; full authority</p>
          </div>
        </div>
        <div className="flex items-center gap-5">
          <div className={`flex items-center gap-1.5 text-[9px] tracking-[0.15em] uppercase ${connected ? 'text-[var(--ok)]' : 'text-[var(--critical)]'}`}>
            {connected ? <Wifi size={11} /> : <WifiOff size={11} />} {connected ? 'Synced' : 'Reconnecting'}
          </div>
          {/* The console covers the whole dashboard, so DevTeam had no way
              to reach the theme switch in its header. */}
          <ThemeToggle />
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

      {/* SECTION TOGGLE — 2026-09-23: config/CRUD split from monitoring,
          explicit teacher requirement. Everything that only shows state
          (Directory, Users) lives under Monitoring; everything that changes
          state (account edits/deletes, create, cameras, stations, AI model
          toggles, approvals, the audit log's restore action) lives under
          Configuration. Switching section jumps to that section's first tab
          rather than leaving `tab` pointed at a tab the new section doesn't
          have. */}
      <div className="shrink-0 flex items-center gap-2 px-7 pt-3 border-b border-[var(--line)]">
        <SectionButton label="Monitoring" active={section === 'monitoring'} onClick={() => switchSection('monitoring')} />
        <SectionButton label="Configuration" active={section === 'configuration'} onClick={() => switchSection('configuration')} />
      </div>

      {/* STAT STRIP — inline ledger, not cards. Monitoring only: these are
          read-only totals, not controls, and belong with the rest of the
          read-only section. */}
      {section === 'monitoring' && (
        <div className="shrink-0 flex items-stretch border-b border-[var(--line)] px-7">
          <StatCell icon={<Users2 size={13} />} label="Users" val={data.totals.users} />
          <StatCell icon={<ShieldAlert size={13} />} label="Incidents" val={data.totals.incidents} />
          <StatCell icon={<Activity size={13} />} label="Active" val={data.totals.active_incidents} accent="text-[var(--critical)]" />
          <StatCell icon={<Video size={13} />} label="Cameras" val={data.totals.cameras} />
          <StatCell icon={<Film size={13} />} label="Records" val={data.totals.video_records} last />
        </div>
      )}

      {/* TABS */}
      <div className="shrink-0 flex items-center gap-1 px-7 border-b border-[var(--line)]">
        {section === 'monitoring' ? (
          <>
            <TabButton icon={<LayoutGrid size={12} />} label="Directory" active={tab === 'directory'} onClick={() => setTab('directory')} />
            <TabButton icon={<Users2 size={12} />} label="Users" active={tab === 'users'} onClick={() => setTab('users')} badge={data.users.length} />
            <TabButton icon={<Video size={12} />} label="Cameras" active={tab === 'cameras'} onClick={() => setTab('cameras')} badge={cameras.length} />
            <TabButton
              icon={<Brain size={12} />}
              label="AI Models"
              active={tab === 'models'}
              onClick={() => { setTab('models'); if (!modelsLoaded) fetchModels(); if (!optimizeState) fetchOptimizeStatus(); fetchThresholdCounts(); }}
            />
          </>
        ) : (
          <>
            {/* Fixed, never draggable -- "first and second is manage users
                and create users." */}
            <TabButton icon={<Users2 size={12} />} label="Manage Users" active={tab === 'manage_users'} onClick={() => setTab('manage_users')} badge={data.users.length} />
            <TabButton icon={<UserPlus size={12} />} label="Create User" active={tab === 'create'} onClick={() => setTab('create')} />

            {/* Reorderable tail (2026-09-29). Long-press any of these tabs
                (~0.5s) to enter reorder mode, then drag; Esc or a click
                anywhere else finishes. A plain click still just opens the
                tab. Order persists per-browser (configTabOrder). */}
            {configTabOrder.map(t => {
              const def: { icon: React.ReactNode; label: string; onClick: () => void; badge?: number } | null =
                t === 'permissions' ? {
                  icon: <KeyRound size={12} />, label: 'Permissions',
                  onClick: () => { setTab('permissions'); if (!rolesLoaded) fetchCustomRoles(); },
                } : t === 'approvals' ? {
                  icon: <ClipboardList size={12} />, label: 'Approvals',
                  onClick: () => setTab('approvals'),
                  badge: pendingLocations.length + pendingSignups.length,
                } : t === 'stations' ? {
                  icon: <Radio size={12} />, label: 'Stations',
                  onClick: () => setTab('stations'),
                  badge: stations.length,
                } : t === 'audit' ? {
                  icon: <Undo2 size={12} />, label: 'Audit Log',
                  onClick: () => { setTab('audit'); fetchAuditLog(auditActionFilter || undefined); },
                } : null;
              if (!def) return null;
              return (
                <div
                  key={t}
                  data-config-tab
                  draggable={configEditMode}
                  onPointerDown={e => startLongPress(e.clientX, e.clientY)}
                  onPointerMove={e => moveLongPress(e.clientX, e.clientY)}
                  onPointerUp={cancelLongPress}
                  onPointerLeave={cancelLongPress}
                  onDragStart={() => configEditMode && setDraggedConfigTab(t)}
                  onDragOver={e => { if (configEditMode) e.preventDefault(); }}
                  onDrop={() => configEditMode && reorderConfigTab(t)}
                  onDragEnd={() => setDraggedConfigTab(null)}
                  className={configEditMode
                    ? `cursor-move border border-dashed border-[var(--accent)]/50 transition-opacity ${draggedConfigTab === t ? 'opacity-30' : ''}`
                    : 'select-none'}
                  title={configEditMode ? 'Drag to reorder' : undefined}
                >
                  <TabButton
                    icon={def.icon} label={def.label} active={tab === t} badge={def.badge}
                    onClick={() => {
                      // The click that ends a long-press must not also open the tab.
                      if (longPressFiredRef.current) { longPressFiredRef.current = false; return; }
                      if (!configEditMode) def.onClick();
                    }}
                  />
                </div>
              );
            })}

            {configEditMode && (
              <span className="ml-auto text-[9px] tracking-[0.1em] uppercase text-[var(--accent)]">
                Drag to reorder · Esc or click elsewhere to finish
              </span>
            )}
          </>
        )}
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

      {/* ================= USERS TAB ================= */}
      {/* Added 2026-09-04 (user request: "add a users list, just to see
          which users, how many there are, and which are active"). The
          Directory tab already shows every account, but only grouped under
          its own barangay/station -- an account created for a jurisdiction
          that isn't rendering right, or one nobody remembered to check, is
          invisible there in practice even though it exists. This is the
          same data (data.users), flat, with nothing to navigate into. */}
      {tab === 'users' && (
        <div className="flex-1 min-h-0 flex flex-col px-7 pb-7 pt-4">
          <div className="shrink-0 flex items-center gap-2 border border-[var(--line)] border-b-0 px-3 py-2.5">
            <Search size={12} className="text-[var(--text-2)] shrink-0" />
            <input
              value={userSearch}
              onChange={e => setUserSearch(e.target.value)}
              placeholder="search username, role, or organization"
              className="bg-transparent text-[11px] text-[var(--text)] outline-none w-full placeholder:text-[var(--text-3)]"
            />
            <span className="text-[9px] shrink-0" style={{ color: 'var(--text-3)' }}>
              {data.users.length} account{data.users.length === 1 ? '' : 's'}
            </span>
          </div>
          <div className="flex-1 overflow-y-auto custom-scrollbar border border-[var(--line)]">
            {visibleUsers.length === 0 ? (
              <p className="text-[10px] tracking-[0.15em] uppercase text-[var(--text-3)] text-center py-10">No matching accounts</p>
            ) : (
              <div className="divide-y divide-[var(--panel-2)]">
                {visibleUsers.map(u => renderUserListRow(u))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* ================= CONFIGURATION PANES =================
          2026-09-29 redesign: every Configuration tab is two halves -- the
          list on the left, the selected item's details on the right --
          instead of full-width rows the eye has to sweep across. Each pane
          lives in ./devteam/ to keep this file from growing further. */}
      {tab === 'manage_users' && (
        <ManageUsersPane
          apiUrl={API_URL} users={data.users} stations={stations} cameras={cameras}
          allLocations={allLocations} customRoles={customRoles} flash={flash} refresh={fetchOverview}
          reviewVerification={reviewVerification}
        />
      )}

      {tab === 'permissions' && (
        <RolesPane apiUrl={API_URL} customRoles={customRoles} users={data.users} fetchCustomRoles={fetchCustomRoles} flash={flash} />
      )}

      {tab === 'approvals' && (
        <ApprovalsPane
          apiUrl={API_URL} pendingLocations={pendingLocations} pendingSignups={pendingSignups}
          rejectedLocations={rejectedLocations} rejectedSignups={rejectedSignups} stations={stations}
          busyIds={pendingActionIds} onDecide={decideApplication}
          reviewVerification={reviewVerification} flash={flash}
        />
      )}

      {tab === 'create' && (
        <CreateUserPane
          apiUrl={API_URL} users={data.users} stations={stations} cameras={cameras}
          allLocations={allLocations} customRoles={customRoles} fetchCustomRoles={fetchCustomRoles} flash={flash}
          onCreated={() => { fetchOverview(); setTab('manage_users'); }}
        />
      )}

      {tab === 'stations' && (
        <StationsPane apiUrl={API_URL} stations={stations} allLocations={allLocations} users={data.users} flash={flash} refresh={fetchOverview} />
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
                      <p className="text-[9px] text-[var(--text-2)] font-mono truncate">{maskStreamUrl(cam.url)}</p>
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

      {/* ================= AUDIT LOG TAB (Configuration) ================= */}
      {/* Who-did-what + recover. Only delete-type entries whose target is
          still soft-deleted can be restored -- the backend enforces that,
          the button just isn't offered for anything else. */}
      {tab === 'audit' && (
        <div className="flex-1 min-h-0 grid grid-cols-1 md:grid-cols-2 gap-6 px-7 pb-7 pt-4">
          <div className="min-h-0 flex flex-col border border-[var(--line)]">
            <PaneHeader
              icon={<Undo2 size={12} />}
              title="Audit log"
              right={<span className="text-[9px] text-[var(--text-3)]">{auditEntries.length} entr{auditEntries.length === 1 ? 'y' : 'ies'}</span>}
            />
            <div className="shrink-0 flex items-center gap-2 px-3 py-2 border-b border-[var(--line)]">
              <Search size={12} className="text-[var(--text-2)] shrink-0" />
              <input
                value={auditActionFilter}
                onChange={e => setAuditActionFilter(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') fetchAuditLog(auditActionFilter.trim() || undefined); }}
                placeholder="search action, person or details, e.g. confirmed, login_failed, juan (enter)"
                className="bg-transparent text-[11px] text-[var(--text)] outline-none w-full placeholder:text-[var(--text-3)]"
              />
              {auditActionFilter && (
                <button
                  onClick={() => { setAuditActionFilter(''); fetchAuditLog(); }}
                  className="text-[9px] tracking-[0.1em] uppercase text-[var(--text-2)] hover:text-[var(--text)] shrink-0"
                >
                  Clear
                </button>
              )}
            </div>
            <div className="flex-1 min-h-0 overflow-y-auto custom-scrollbar divide-y divide-[var(--panel-2)]">
              {!auditLoaded ? (
                <EmptyPane text="Loading…" />
              ) : auditEntries.length === 0 ? (
                <EmptyPane text="No audit entries yet" />
              ) : auditEntries.map(entry => {
                const label = entry.target_snapshot?.full_name || entry.target_snapshot?.username || entry.target_snapshot?.name || entry.target_id;
                const tone = entry.action.endsWith('.deleted') ? 'text-[var(--critical)]'
                  : entry.action.endsWith('.restored') || entry.action.endsWith('.created') ? 'text-[var(--ok)]' : 'text-[var(--text-2)]';
                return (
                  <button
                    key={entry.id}
                    onClick={() => setSelectedAuditId(entry.id)}
                    className={`w-full flex items-center gap-3 px-3 py-2.5 text-left transition-colors ${entry.id === selectedAuditId ? 'bg-[var(--accent)]/[0.08]' : 'hover:bg-[var(--panel)]'}`}
                  >
                    <div className="min-w-0 flex-1">
                      <p className="text-[11px] text-[var(--text)] truncate">
                        <span className={tone}>{entry.action}</span> <span className="text-[var(--text-2)]">{label}</span>
                      </p>
                      <p className="text-[9px] text-[var(--text-3)] truncate">by {entry.actor_username} · {serverDateTime(entry.created_at)}</p>
                    </div>
                  </button>
                );
              })}
            </div>
          </div>

          <div className="min-h-0 flex flex-col border border-[var(--line)]">
            {(() => {
              const entry = auditEntries.find(e => e.id === selectedAuditId);
              if (!entry) {
                return (
                  <>
                    <PaneHeader icon={<Info size={12} />} title="Entry details" />
                    <EmptyPane text="Select an entry" sub="Who did it, when, the reason they gave, and a snapshot of what changed." />
                  </>
                );
              }
              const busy = auditBusyIds.has(entry.id);
              const snapshot = Object.entries(entry.target_snapshot || {})
                .filter(([k, v]) => k !== 'password' && k !== 'reason' && v !== null && v !== '');
              return (
                <>
                  <PaneHeader icon={<Info size={12} />} title={entry.action} />
                  <div className="flex-1 min-h-0 overflow-y-auto custom-scrollbar p-5 space-y-5">
                    <div className="grid grid-cols-2 gap-x-4 gap-y-3">
                      <InfoRow label="Target" value={`${entry.target_type} ${entry.target_id}`} />
                      <InfoRow label="By" value={entry.actor_username} />
                      <InfoRow label="When" value={serverDateTime(entry.created_at)} />
                      <InfoRow label="Action" value={entry.action} />
                    </div>
                    {entry.target_snapshot?.reason && (
                      <div className="border border-[var(--warn)]/30 bg-[var(--warn)]/[0.04] px-3 py-2.5">
                        <span className="text-[8px] tracking-[0.15em] uppercase text-[var(--warn)] block mb-1">Reason given</span>
                        <p className="text-[11px] text-[var(--text)]">{entry.target_snapshot.reason}</p>
                      </div>
                    )}
                    {snapshot.length > 0 && (
                      <div>
                        <span className="text-[8px] tracking-[0.15em] uppercase text-[var(--text-3)] block mb-2">Snapshot</span>
                        <div className="border border-[var(--panel-2)] divide-y divide-[var(--panel-2)]">
                          {snapshot.map(([k, v]) => (
                            <div key={k} className="flex gap-3 px-3 py-1.5">
                              <span className="text-[9px] text-[var(--text-3)] w-36 shrink-0 truncate">{k}</span>
                              <span className="text-[10px] text-[var(--text)] break-all">{typeof v === 'object' ? JSON.stringify(v) : String(v)}</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                    {entry.action.endsWith('.deleted') && (
                      <button
                        onClick={() => restoreAuditEntry(entry)}
                        disabled={busy}
                        className="flex items-center gap-1.5 px-3 py-2 text-[9px] tracking-[0.1em] uppercase border border-[var(--ok)]/30 text-[var(--ok)] hover:bg-[var(--ok)]/10 disabled:opacity-40"
                      >
                        <Undo2 size={11} /> {busy ? 'Restoring…' : 'Restore'}
                      </button>
                    )}
                  </div>
                </>
              );
            })()}
          </div>
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

          <DetectionQualityPanel apiUrl={API_URL} />

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
                    role="switch"
                    aria-checked={m.enabled}
                    aria-label={`${m.display_name || m.name} detection`}
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

function SectionButton({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={`px-3.5 py-1.5 text-[10px] font-bold tracking-[0.15em] uppercase border transition-colors ${
        active
          ? 'border-[var(--accent)] text-[var(--accent)] bg-[var(--accent)]/[0.08]'
          : 'border-[var(--line)] text-[var(--text-2)] hover:border-[var(--line-2)] hover:text-[var(--text)]'
      }`}
    >
      {label}
    </button>
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
