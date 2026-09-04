"use client";

import dynamic from 'next/dynamic';
import React, { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { useRuntimeConfig } from './hooks/useRuntimeConfig';
import { useLiveChannel } from './context/WebSocketContext';
import { usePermissions } from './hooks/usePermissions';
import { useAlertNotifier } from './hooks/useAlertNotifier';
import { SkeletonRow } from './components/dashboard/Skeleton';
// Presentational pieces moved out of this file -- see DashboardPrimitives.tsx
// for why only these were extracted and the tab bodies deliberately were not.
import { SystemClockText, SystemDateText } from './components/dashboard/SystemTime';
import {
  gridColsFor, tempTone, CameraTile, NavSectionLabel, NavItem, MetricPanel, IncidentRow,
} from './components/dashboard/DashboardPrimitives';
import PTZControls from './components/PTZControls';
import AiModelsPanel from './components/AiModelsPanel';
import ThemeToggle from './components/ThemeToggle';
import {
  Shield, AlertOctagon, Activity, Video, Cpu, Trash2, MapPin,
  Maximize2, X, Sun, User,
  BatteryMedium, Thermometer, Zap, Plus, Film, Users, Terminal,
  Camera as CameraIcon, Check, Loader2, Grid2X2, ArrowLeft, Wifi, Siren
} from 'lucide-react';

// Every tab view was previously eagerly imported at module top -- all six
// (including the Leaflet-map-heavy CrimeReportsView and the large
// DevteamView) got bundled into the initial page chunk even though only one
// tab renders at a time. This bundle also ships inside the Electron package
// and loads from local disk on every cold start, so the eager-load cost
// matters more here than a typical web app. next/dynamic + ssr:false loads
// each tab's code only when that tab is actually opened.
const CrimeReportsView = dynamic(() => import('./components/CrimeReportsView'), { ssr: false }) as any;
const HistoryView = dynamic(() => import('./components/dashboard/HistoryView'), { ssr: false });
const RecordsView = dynamic(() => import('./components/RecordsView'), { ssr: false }) as any;
const ProfileView = dynamic(() => import('./components/ProfileView'), { ssr: false });
const AdminUsersView = dynamic(() => import('./components/dashboard/AdminUsersView'), { ssr: false });
const DevteamView = dynamic(() => import('./components/dashboard/DevteamView'), { ssr: false });

type Alert = {
  id: string;
  type: string;
  severity: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
  location: string;
  area: string;
  timestamp: string;
  confidence: number;
  status: 'pending' | 'confirmed' | 'dismissed';
  cameraLinkId?: string | null;
};

type Camera = {
  id: string;
  name: string;
  url: string;
  status: 'online' | 'offline';
};

export default function EcoVisionSentinel() {
  const { apiUrl, aiUrl, loaded: configLoaded } = useRuntimeConfig();
  const { can } = usePermissions();
  const [currentUser, setCurrentUser] = useState<any>(null);
  const [activeTab, setActiveTab] = useState('dashboard');
  const [selectedCam, setSelectedCam] = useState<Camera | null>(null);
  const [isFullscreenGrid, setIsFullscreenGrid] = useState(false);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [isSirenActive, setIsSirenActive] = useState(false);
  const [showModal, setShowModal] = useState(false);
  const [sqlReportCount, setSqlReportCount] = useState(0);
  const [backendOnline, setBackendOnline] = useState(true);
  // Distinguishes "we have not loaded the queue yet" from "the queue is empty".
  // Without this the incident panel renders "Monitoring - no incidents awaiting
  // review" during the very first fetch, which is indistinguishable from a
  // verified all-clear. On a public-safety dashboard, showing reassurance
  // before the data exists is the wrong default.
  const [alertsLoaded, setAlertsLoaded] = useState(false);
  // Tracked separately from backendOnline: the storage backend (8000) and the
  // AI vision core (8001) are different processes with very different startup
  // times, so "System Nominal" must not imply the camera feed is live.
  const [aiCoreOnline, setAiCoreOnline] = useState(false);
  const [videoRecordSearchFilter, setVideoRecordSearchQuery] = useState("");
  const [telemetry, setTelemetry] = useState({ battery: 88, solarV: 14.4, tempCPU: 42, tempESP: 38, tempNeural: 51, load: 12.4 });
  const [camIndexInput, setCamIndexInput] = useState("5");
  const [availableCameras, setAvailableCameras] = useState<number[]>([]);
  // Credential-stripped description of whatever the AI core is currently
  // watching -- a local index or an RTSP/HTTP stream.
  const [currentSourceLabel, setCurrentSourceLabel] = useState<string>('');
  const [sourceIsNetwork, setSourceIsNetwork] = useState(false);
  const [sourceError, setSourceError] = useState<string | null>(null);
  // Declutter: the SRC input/apply row is a genuinely technical control
  // (device index or a raw RTSP URL) that most sessions never touch --
  // tucked behind an icon by default rather than permanently occupying the
  // video wall header, purely a visual grouping change, not a behavioral
  // one (handleApplyCameraSource etc. are untouched).
  const [srcPanelOpen, setSrcPanelOpen] = useState(false);
  const [applyState, setApplyState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const router = useRouter();

  // BUG FOUND 2026-09-04: fetchCameras/fetchStats/fetchActiveAlertCache below
  // -- this dashboard's own core polling, running on literally every page
  // load for every role -- treated a 401 exactly like any other failed
  // fetch: silently do nothing and let the next poll try again. That retry
  // never helps, because a 401 here means the token itself is invalid (now
  // also raised the moment a DevTeam admin deletes the account or changes
  // its role/barangay/station -- see require_auth's own fix in backend.py --
  // not just the old "wait up to 7 days for it to expire" case), so every
  // subsequent poll 401s forever and the dashboard just sits there frozen on
  // whatever data it last had, looking normal, with no way back to login
  // short of manually clearing storage. DevteamView.tsx already carries this
  // exact fix (see its own 2026-08-19 comment) for its own fetches; this
  // dashboard -- the one every single role actually lands on -- never got
  // it.
  const forceLogoutStaleSession = () => {
    localStorage.removeItem('ecoUser');
    localStorage.removeItem('ecoToken');
    router.push('/loginpage/login');
  };

  // Auth gate only. The camera fetch used to live here too, but this effect
  // runs on mount -- before useRuntimeConfig() has resolved the real apiUrl --
  // so in a packaged build with dynamic ports it hit the default :8000 once,
  // failed silently, and never retried. Splitting it means the fetch waits
  // for the resolved config instead of racing it.
  useEffect(() => {
    const savedUser = localStorage.getItem('ecoUser');
    // Checking ecoUser's presence alone let a stale-but-present ecoUser with
    // a missing or invalid ecoToken straight through to the dashboard, where
    // every real API call then 401s with no way back except manually
    // clearing storage. ecoUser has no expiry of its own and, unlike
    // ecoToken, is never invalidated by a SECRET_KEY change (writeGeneratedEnv
    // in electron/main.js generates a fresh one per install) -- so the two
    // can go out of sync across reinstalls even though Electron's localStorage
    // persists in the app's userData path independent of which install wrote
    // it. This only catches the token being entirely absent; an actually
    // invalid-but-present token is still caught downstream when a real
    // request 401s (see DevteamView's fetchOverview).
    const savedToken = localStorage.getItem('ecoToken');
    if (!savedUser || !savedToken) {
      localStorage.removeItem('ecoUser');
      localStorage.removeItem('ecoToken');
      router.push('/loginpage/login');
      return;
    }
    const parsedUser = JSON.parse(savedUser);
    setCurrentUser(parsedUser);
    if (parsedUser.role === 'DEVTEAM') {
      setActiveTab('devteam'); // DEVTEAM has no Monitor/dashboard view -- lands on the console
    }
  }, [router]);

  useEffect(() => {
    if (!configLoaded || !currentUser) return;
    fetchCameras(currentUser);
  }, [configLoaded, currentUser, apiUrl]);

  // The AI core spends ~45s loading YOLO + X3D weights before it binds its
  // port, so on a cold start this fetch always lost the race -- and because
  // it ran once with [] deps, it gave up permanently and the camera picker
  // stayed empty for the rest of the session (surfacing as a "Failed to
  // fetch" overlay in dev). It also captured the DEFAULT aiUrl: the runtime
  // config resolves asynchronously, so in a packaged build with dynamic
  // ports this polled the wrong port forever. Now it waits for the real
  // config, then retries with backoff until the core answers.
  useEffect(() => {
    if (!configLoaded) return;

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    let delay = 2000;
    const MAX_DELAY = 15000;

    const poll = async () => {
      try {
        const res = await fetch(`${aiUrl}/available_cameras`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (cancelled) return;
        setAvailableCameras(data.available_cameras);
        setCurrentSourceLabel(data.current_source ?? '');
        setSourceIsNetwork(!!data.is_network);
        // For a network camera the input is left blank and the (credential-
        // stripped) URL shown as placeholder instead -- echoing the server's
        // redacted string back into an editable field would mean applying it
        // again submits "rtsp://***@host/stream1" as a literal URL.
        setCamIndexInput(data.is_network ? '' : String(data.current_index ?? ''));
        setAiCoreOnline(true);
      } catch {
        // Expected while the core is still warming up -- not worth a
        // console.error on every attempt.
        if (cancelled) return;
        setAiCoreOnline(false);
        timer = setTimeout(poll, delay);
        delay = Math.min(delay * 2, MAX_DELAY);
      }
    };

    poll();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [aiUrl, configLoaded]);

  /* Applies whatever the operator typed: a local device index ("0") or a
   * network stream URL ("rtsp://user:pass@10.0.0.12:554/stream1"). The core
   * decides which is which -- the dashboard should not have to know, and a
   * barangay adopting its existing IP cameras types a URL here rather than
   * needing the exact webcam hardware this was developed against. */
  const handleApplyCameraSource = async () => {
    const raw = camIndexInput.trim();
    if (!raw) return;
    setApplyState('saving');
    try {
      const res = await fetch(`${aiUrl}/set_camera_source`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source: raw })
      });
      const data = await res.json();
      setApplyState(data.status === "reopened" ? 'saved' : 'error');
      if (data.status !== "reopened" && data.detail) {
        // The core rolls back to the previous source on a bad URL, so the
        // operator keeps their feed; say why the new one did not take.
        setSourceError(data.detail);
        setTimeout(() => setSourceError(null), 6000);
      }
    } catch (e) {
      console.error("Camera source swap failed:", e);
      setApplyState('error');
    }
    setTimeout(() => setApplyState('idle'), 2000);
  };

const fetchCameras = async (userObj: any) => {
    try {
      // Backend returns snake_case (barangay_id) -- this used to read the
      // camelCase .barangayId, which is never present on the stored user
      // object, so it silently fell back to 'cogon' for every account,
      // every time. Invisible while 'cogon' was the only registered
      // barangay in the whole system; would have misrouted every other
      // barangay's cameras/incidents to cogon's the moment a second one
      // existed.
      const barangay = userObj.barangay_id && userObj.barangay_id !== 'undefined' ? userObj.barangay_id : 'cogon';
      const token = localStorage.getItem('ecoToken');
      const res = await fetch(`${apiUrl}/api/cameras?barangayId=${encodeURIComponent(barangay)}&role=${encodeURIComponent(userObj.role)}`, {
        headers: { Authorization: `Bearer ${token}` }
      });
      if (res.status === 401) { forceLogoutStaleSession(); return; }
      if (res.ok) {
        const data = await res.json();
        setCameras(data);
      }
    } catch (e) {
      console.error("Camera registry vector initialization fault:", e);
    }
  };

  const fetchStats = async () => {
    if (!currentUser) return;
    try {
      const barangay = currentUser.barangay_id && currentUser.barangay_id !== 'undefined' ? currentUser.barangay_id : 'cogon';
      const role = currentUser.role || 'user';
      const token = localStorage.getItem('ecoToken');
      // currentUser can hydrate (e.g. from a persisted session) a tick
      // before the token itself lands in localStorage -- without this
      // guard that window sends "Authorization: Bearer null" and the
      // backend correctly 401s it (verify_token can't parse "null" as a
      // signed token). useLiveChannel's own 60s fallback poll retries
      // this naturally, so skipping quietly here is enough -- no need to
      // surface a transient race as a user-visible error.
      if (!token) return;

      const res = await fetch(`${apiUrl}/api/incidents?userBarangayId=${encodeURIComponent(barangay)}&role=${encodeURIComponent(role)}&filterBarangayId=all`, {
        headers: { Authorization: `Bearer ${token}` }
      });
      if (res.status === 401) { forceLogoutStaleSession(); return; }
      if (!res.ok) return;
      const data = await res.json();
      setSqlReportCount(data.length);
      setBackendOnline(true);
    } catch (e) {
      console.warn("📡 [FETCH FAILED] Storage ledger on port 8000 is unreachable inside fetchStats. Retrying...");
      setBackendOnline(false);
    }
  };

  const fetchActiveAlertCache = async () => {
    if (!currentUser) return;
    try {
      const barangay = currentUser.barangay_id && currentUser.barangay_id !== 'undefined' ? currentUser.barangay_id : 'cogon';
      const role = currentUser.role || 'user';
      const token = localStorage.getItem('ecoToken');
      // See matching comment in fetchStats -- same currentUser-before-token
      // hydration race, same safe skip-and-let-the-next-poll-retry fix.
      if (!token) return;

      const res = await fetch(`${apiUrl}/api/incidents?userBarangayId=${encodeURIComponent(barangay)}&role=${encodeURIComponent(role)}&filterBarangayId=all`, {
        headers: { Authorization: `Bearer ${token}` }
      });
      if (res.status === 401) { forceLogoutStaleSession(); return; }
      if (res.ok) {
        const data = await res.json();
        const activeDetections = data.filter((inc: any) => inc.status === 'Active');
        // /api/incidents returns snake_case (location_name, barangay_id,
        // occurred_time) -- these read camelCase names that don't exist on
        // that response, so location/timestamp were always undefined and
        // area always fell through to "GLOBAL Sector". Worse, the
        // .includes() below ran on that undefined and threw, which this
        // block's try/catch swallowed as if the backend were unreachable
        // -- so a real crash here has been surfacing as the wrong error
        // message ("Storage ledger ... unreachable") the whole time.
        const mappedAlerts = activeDetections.map((inc: any) => ({
          id: inc.id,
          type: inc.type,
          severity: 'CRITICAL' as const,
          location: inc.location_name,
          area: `${inc.barangay_id ? inc.barangay_id.toUpperCase() : 'GLOBAL'} Sector`,
          timestamp: inc.occurred_time,
          confidence: inc.confidence ?? 0.925,
          status: 'pending' as const,
          // BUG FOUND 2026-09-03 (user report): this used to guess the
          // camera by checking whether location_name contained the word
          // "Entrance" -- worked only for the two original demo cameras,
          // and would misattribute (or default to camera "2") for any other
          // name. incidents.camera_id is now a real column, populated by
          // main.py at detection time; this just reads it.
          cameraLinkId: inc.camera_id ?? null,
          // Wasn't carried at all -- the Incident Queue could never show a
          // thumbnail no matter how correctly screenshot_path was stored,
          // because this mapping just never read it off the incident.
          // Resolved to a full URL here (rather than in IncidentRow) so
          // that component doesn't need apiUrl threaded down as a new prop.
          screenshot_path: inc.screenshot_path
            ? (inc.screenshot_path.startsWith('http') ? inc.screenshot_path : `${apiUrl}${inc.screenshot_path}`)
            : null,
        }));
        setAlerts(mappedAlerts);
        setBackendOnline(true);
        setAlertsLoaded(true);
      }
    } catch (err) {
      console.warn("📡 [FETCH FAILED] Storage ledger on port 8000 is unreachable inside fetchActiveAlertCache. Retrying...");
      setBackendOnline(false);
    }
  };

  // Previously this dashboard opened its OWN raw WebSocket to /ws on top of
  // the one <WebSocketProvider> (app/layout.tsx) already keeps open app-wide
  // -- two live connections to the same endpoint for the app's whole
  // lifetime -- AND ran an unconditional 5s setInterval polling loop
  // regardless of whether anything changed. useLiveChannel replaces both:
  // one shared socket, refetch only when the backend actually broadcasts
  // something (plus the existing 60s fallback poll inside useLiveChannel
  // itself as a safety net).
  // `!!currentUser` as the ready flag: see the BUG FOUND 2026-09-03 comment
  // on useLiveChannel itself -- without this, the hook's one guaranteed
  // initial call fires while currentUser is still null (this effect runs at
  // mount, before the hydrate-from-localStorage effect above has
  // committed), fetchStats/fetchActiveAlertCache's own `if (!currentUser)
  // return` skips quietly, and nothing calls them again for up to 60s.
  useLiveChannel("incidents", () => {
    fetchStats();
    fetchActiveAlertCache();
  }, !!currentUser);

  // Announce new incidents audibly, in the window title, and on the desktop.
  // Before this, an incoming alert only re-rendered the list -- an operator
  // looking away, or with the window minimised, was told nothing at all.
  // See app/hooks/useAlertNotifier.ts for why each channel exists.
  useAlertNotifier(alerts);

  const handleUpsertNode = async (name: string, url: string) => {
    try {
      const barangay = currentUser.barangay_id && currentUser.barangay_id !== 'undefined' ? currentUser.barangay_id : 'cogon';
      const token = localStorage.getItem('ecoToken');
      const res = await fetch(`${apiUrl}/api/cameras`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ name, url, barangay_id: barangay })
      });
      if (res.ok) {
        fetchCameras(currentUser);
        setShowModal(false);
      } else {
        const errBody = await res.text();
        console.error("Camera creation failed:", res.status, errBody);
      }
    } catch (e) { console.error(e); }
  };

  const deleteCam = async (id: string) => {
    try {
      const token = localStorage.getItem('ecoToken');
      const res = await fetch(`${apiUrl}/api/cameras/${id}`, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${token}` }
      });
      if (res.ok) {
        fetchCameras(currentUser);
        if (selectedCam?.id === id) setSelectedCam(null);
      }
    } catch (e) { console.error(e); }
  };

  const handleVerifyCrime = async (id: string) => {
    const alertTarget = alerts.find(a => a.id === id);
    setAlerts(prev => prev.filter(a => a.id !== id));
    setIsSirenActive(true);
    setActiveTab('dashboard');

    if (alertTarget?.cameraLinkId) {
      const matchCam = cameras.find(c => c.id === alertTarget.cameraLinkId);
      if (matchCam) { setSelectedCam(matchCam); setIsFullscreenGrid(true); }
    } else if (cameras.length > 0) {
      setSelectedCam(cameras[0]);
      setIsFullscreenGrid(true);
    }

    try {
      const token = localStorage.getItem('ecoToken');
      // Missing the Authorization header meant this always 401'd --
      // require_auth() + require_permission(confirm_dismiss_alerts) both
      // reject an unauthenticated request, so "Verify Crime" never
      // actually reached the ESP32. Fire-and-forget on purpose: the siren
      // is best-effort hardware, and with esp32.enabled=false in most dev
      // setups this call is EXPECTED to fail every time (nothing configured
      // to reach) -- console.error here was tripping Next's dev overlay
      // into a full-screen "crash" for a routine, harmless no-op.
      fetch(`${apiUrl}/siren/activate`, { method: "POST", headers: { Authorization: `Bearer ${token}` } }).catch(() => {});
      await fetch(`${apiUrl}/api/incidents/${id}/status`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ status: "Confirmed" })
      });
      fetchStats();
      fetchActiveAlertCache();
    } catch (e) { console.error(e); }
  };

  const handleDismissCrime = async (id: string) => {
    setAlerts(prev => prev.filter(a => a.id !== id));
    try {
      const token = localStorage.getItem('ecoToken');
      // Same as handleVerifyCrime's /siren/activate -- best-effort, and
      // expected to fail when no ESP32 is configured. See that comment.
      fetch(`${apiUrl}/siren/deactivate`, { method: "POST", headers: { Authorization: `Bearer ${token}` } }).catch(() => {});
      const res = await fetch(`${apiUrl}/api/incidents/${id}/status`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ status: "Dismissed" })
      });
      if (!res.ok) {
        console.error("Dismiss PATCH failed with status", res.status, "-- incident will reappear on next poll since backend never updated.");
      }
      fetchStats();
      fetchActiveAlertCache();
    } catch (e) { console.error(e); }
  };

  // Standalone from handleDismissCrime's siren call -- that one only fires
  // alongside dismissing a specific incident. This is a direct panic-style
  // stop, reachable regardless of whether anything is even in the queue
  // (a false-triggered ESP32 siren doesn't care whether the operator has
  // gotten around to triaging the incident that set it off yet).
  const [sirenStopState, setSirenStopState] = useState<'idle' | 'busy' | 'done' | 'error'>('idle');
  const handleEmergencyStopSiren = async () => {
    setSirenStopState('busy');
    try {
      const token = localStorage.getItem('ecoToken');
      const res = await fetch(`${apiUrl}/siren/deactivate`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      });
      setSirenStopState(res.ok ? 'done' : 'error');
    } catch {
      setSirenStopState('error');
    }
    setTimeout(() => setSirenStopState('idle'), 2500);
  };

  const handleLogout = () => {
    // Was only clearing ecoUser, not ecoToken -- the same half-clear that
    // caused the stale-token 401s fixed earlier tonight. A "logged out"
    // session that still carries a valid token isn't actually logged out.
    const token = localStorage.getItem('ecoToken');
    localStorage.removeItem('ecoUser');
    localStorage.removeItem('ecoToken');
    if (token) fetch(`${apiUrl}/api/logout`, { method: 'POST', headers: { Authorization: `Bearer ${token}` } }).catch(() => {});
    router.push('/loginpage/login');
  };

  if (!currentUser) return <div className="min-h-screen" style={{ background: 'var(--bg)' }} />;

  const pendingAlerts = alerts.filter(a => a.status === 'pending');
  const isPolice = currentUser.role === 'PNP_OFFICER' || currentUser.role === 'PNP_ADMIN';
  const isBarangay = currentUser.role === 'BARANGAY_STAFF' || currentUser.role === 'BARANGAY_ADMIN';

  return (
    <div className="flex flex-col h-screen w-screen overflow-hidden" style={{ background: 'var(--bg)', color: 'var(--text)' }}>

      {/* ═══ STATUS BAR ═══════════════════════════════════════════════════
          Always-visible operational state. In a monitoring station the
          operator must never have to navigate to answer "is the system
          actually up, and how many incidents are waiting on me right now" */}
      <header
        className="h-11 shrink-0 flex items-center justify-between px-3 border-b"
        style={{ background: 'var(--panel)', borderColor: 'var(--line)' }}
      >
        <div className="flex items-center gap-3 min-w-0">
          <div className="flex items-center gap-2 pr-3 border-r" style={{ borderColor: 'var(--line)' }}>
            <Shield size={15} style={{ color: 'var(--cyan)' }} className="stroke-[2.2]" />
            <span className="disp text-[12.5px] font-extrabold tracking-[0.02em]" style={{ color: 'var(--text)' }}>EcoVision Sentinel</span>
          </div>

          {/* System health -- reads instantly, no navigation required.
              Two independent processes, so two independent indicators: an
              operator seeing "System Nominal" must not assume the camera
              feed is live when only the storage backend came up. */}
          <div className="flex items-center gap-1.5">
            <span className={`status-dot ${backendOnline ? 'ok' : 'live'}`} />
            <span className="label" style={{ color: backendOnline ? 'var(--text-2)' : 'var(--critical)' }}>
              {backendOnline ? 'Database' : 'Database Offline'}
            </span>
          </div>

          <div className="flex items-center gap-1.5 pl-3 border-l" style={{ borderColor: 'var(--line)' }}>
            <span className={`status-dot ${aiCoreOnline ? 'ok' : 'live'}`} />
            <span className="label" style={{ color: aiCoreOnline ? 'var(--text-2)' : 'var(--warn)' }}>
              {aiCoreOnline ? 'AI Core' : 'AI Core Starting…'}
            </span>
          </div>

          <div className="hidden md:flex items-center gap-1.5 pl-3 border-l" style={{ borderColor: 'var(--line)' }}>
            <span className={`status-dot ${cameras.length > 0 ? 'ok' : 'off'}`} />
            <span className="label">{cameras.length} Camera{cameras.length === 1 ? '' : 's'}</span>
          </div>
        </div>

        <div className="flex items-center gap-3">
          {/* Pending incident count -- the single most operationally
              important number on the screen, so it gets the only
              persistent critical-colored treatment in the chrome. */}
          {pendingAlerts.length > 0 ? (
            <div
              className="flex items-center gap-2 px-2.5 h-7 border pulse-alert"
              style={{ background: 'var(--critical-dim)', borderColor: 'var(--critical)', borderRadius: 'var(--radius-sm)' }}
            >
              <AlertOctagon size={13} style={{ color: 'var(--critical)' }} />
              <span className="data text-[11px] font-bold" style={{ color: 'var(--critical)' }}>
                {pendingAlerts.length} UNVERIFIED
              </span>
            </div>
          ) : (
            <div className="flex items-center gap-2 px-2.5 h-7 border" style={{ borderColor: 'var(--line)', borderRadius: 'var(--radius-sm)' }}>
              <span className="status-dot ok" />
              <span className="label">No Active Incidents</span>
            </div>
          )}

          <div className="flex items-center gap-2 px-2.5 h-7 border" style={{ borderColor: 'var(--line)', borderRadius: 'var(--radius-sm)' }}>
            <span className="data text-[10px]" style={{ color: 'var(--text-3)' }}><SystemDateText /></span>
            <span className="data text-[12px] font-bold" style={{ color: 'var(--text)' }}><SystemClockText /></span>
          </div>

          <ThemeToggle />

          <button
            onClick={() => setActiveTab('profile')}
            title="Operator profile"
            className="flex items-center gap-2 h-7 px-2.5 border transition-colors"
            style={{
              borderColor: activeTab === 'profile' ? 'var(--accent)' : 'var(--line)',
              background: activeTab === 'profile' ? 'var(--accent-dim)' : 'transparent',
              borderRadius: 'var(--radius-sm)',
            }}
          >
            <User size={12} style={{ color: 'var(--text-2)' }} />
            <div className="flex items-baseline gap-1.5">
              <span className="data text-[10px]" style={{ color: 'var(--text)' }}>{currentUser.username || 'OPERATOR'}</span>
              <span className="label" style={{ fontSize: '8px' }}>{currentUser.role}</span>
            </div>
          </button>
        </div>
      </header>

      <div className="flex flex-1 min-h-0">

        {/* ═══ NAV RAIL ═══════════════════════════════════════════════════ */}
        <nav
          className="w-[216px] shrink-0 flex flex-col border-r"
          style={{ background: 'var(--panel)', borderColor: 'var(--line)' }}
        >
          <div className="flex-1 overflow-y-auto custom-scrollbar py-2">
            {isPolice && (
              <>
                <NavSectionLabel>Operations</NavSectionLabel>
                <NavItem label="Live Monitor" icon={<Activity size={16} />} active={activeTab === 'dashboard'} onClick={() => setActiveTab('dashboard')} />
                {/* Admin tiers always pass these three; PNP_OFFICER has
                    granular per-user toggles that can be off. Previously
                    these showed regardless and clicking one surfaced the raw
                    backend 403 text ("Missing permission: view_map or
                    view_history") as an error banner instead of the tab
                    simply not being there -- can() already existed for
                    exactly this, it just wasn't wired to the nav. */}
                {can('view_map') && (
                  <NavItem label="Incident Map" icon={<MapPin size={16} />} active={activeTab === 'crime-reports'} onClick={() => setActiveTab('crime-reports')} badge={sqlReportCount} badgeTone="neutral" />
                )}
                {can('view_records') && (
                  <NavItem label="Recordings" icon={<Film size={16} />} active={activeTab === 'records'} onClick={() => { setActiveTab('records'); setVideoRecordSearchQuery(""); }} />
                )}
                {can('view_history') && (
                  <NavItem label="Incident Log" icon={<AlertOctagon size={16} />} active={activeTab === 'alerts'} onClick={() => setActiveTab('alerts')} badge={pendingAlerts.length} badgeTone="critical" />
                )}
              </>
            )}
            {isBarangay && (
              <>
                <NavSectionLabel>Operations</NavSectionLabel>
                <NavItem label="Live Monitor" icon={<Activity size={16} />} active={activeTab === 'dashboard'} onClick={() => setActiveTab('dashboard')} />
                {/* BUG FOUND 2026-09-04: this branch never had a view_map/
                    view_records/view_history gate at all -- the isPolice
                    branch right above gets Incident Map/Recordings/Incident
                    Log exactly when can() says so, but a barangay account
                    got NONE of the three regardless of permissions. Create
                    User's own form labels view_map/view_records/view_history
                    "AUTOMATIC" for a BARANGAY_ADMIN same as it does for a
                    PNP_ADMIN, and usePermissions() already computes all
                    three as true for BARANGAY_ADMIN (and honors whatever a
                    BARANGAY_STAFF was explicitly granted) -- the permission
                    was real and backend-enforced on every relevant endpoint,
                    there was simply no button anywhere in this branch that
                    could ever reach it. A barangay admin/staff member
                    granted view_map could not open the crime map; granted
                    view_history, could not open the incident log -- not a
                    403, just nothing to click. Mirrors the isPolice branch's
                    gating exactly; Cameras/Hardware stay barangay-exclusive
                    (that's real infrastructure ownership, not a view
                    permission, so no PNP equivalent exists there). */}
                {can('view_map') && (
                  <NavItem label="Incident Map" icon={<MapPin size={16} />} active={activeTab === 'crime-reports'} onClick={() => setActiveTab('crime-reports')} badge={sqlReportCount} badgeTone="neutral" />
                )}
                {can('view_records') && (
                  <NavItem label="Recordings" icon={<Film size={16} />} active={activeTab === 'records'} onClick={() => { setActiveTab('records'); setVideoRecordSearchQuery(""); }} />
                )}
                {can('view_history') && (
                  <NavItem label="Incident Log" icon={<AlertOctagon size={16} />} active={activeTab === 'alerts'} onClick={() => setActiveTab('alerts')} badge={pendingAlerts.length} badgeTone="critical" />
                )}
                <NavItem label="Cameras" icon={<Video size={16} />} active={activeTab === 'cameras'} onClick={() => setActiveTab('cameras')} badge={cameras.length} badgeTone="neutral" />
                <NavItem label="Hardware" icon={<Zap size={16} />} active={activeTab === 'health'} onClick={() => setActiveTab('health')} />
              </>
            )}
            {(currentUser.role === 'PNP_ADMIN' || currentUser.role === 'BARANGAY_ADMIN') && (
              <>
                <NavSectionLabel>Administration</NavSectionLabel>
                <NavItem label="Personnel" icon={<Users size={16} />} active={activeTab === 'manage-users'} onClick={() => setActiveTab('manage-users')} />
              </>
            )}
            {currentUser.role === 'DEVTEAM' && (
              <>
                <NavSectionLabel>System</NavSectionLabel>
                <NavItem label="DevTeam Console" icon={<Terminal size={16} />} active={activeTab === 'devteam'} onClick={() => setActiveTab('devteam')} />
              </>
            )}
          </div>

          {/* This used to say "Assignment" and read currentUser.barangayId --
              a field that doesn't exist on the stored user object (the
              backend returns barangay_id), so it always showed
              "UNASSIGNED", and the label collided with the actual
              "assignment" field (e.g. "Patrol Unit 3") used everywhere else
              in the app, which is a different concept entirely. This is a
              read-only jurisdiction readout, not an assignable control --
              there is no UI anywhere that lets anyone "assign" something
              here. Renamed and pointed at the field that actually matches
              this account's role: barangay accounts are scoped to a
              barangay, PNP accounts to a station. */}
          {/* Reachable regardless of whether anything is in the Incident
              Queue right now -- a false-triggered siren doesn't wait for
              someone to get around to triaging the incident that set it
              off. Best-effort like the queue's own siren calls: it POSTs
              straight to /siren/deactivate, no incident involved. */}
          <button
            onClick={handleEmergencyStopSiren}
            disabled={sirenStopState === 'busy'}
            title="Immediately silence the physical siren, independent of any incident"
            aria-label="Emergency stop: immediately silence the physical siren"
            className="w-full flex items-center justify-center gap-2 py-2.5 border-t text-[10px] font-bold uppercase tracking-[0.15em] transition-colors disabled:opacity-60"
            style={{
              borderColor: 'var(--line)',
              color: sirenStopState === 'done' ? 'var(--ok)' : sirenStopState === 'error' ? 'var(--critical)' : 'var(--critical)',
              background: sirenStopState === 'idle' ? 'transparent' : 'rgba(229,52,47,0.06)',
            }}
          >
            {sirenStopState === 'busy' ? <Loader2 size={12} className="animate-spin" /> :
             sirenStopState === 'done' ? <Check size={12} /> :
             sirenStopState === 'error' ? <X size={12} /> : <Siren size={12} />}
            {sirenStopState === 'busy' ? 'Stopping…' :
             sirenStopState === 'done' ? 'Siren stopped' :
             sirenStopState === 'error' ? 'No response' : 'Emergency stop siren'}
          </button>

          <div className="px-3 py-2.5 border-t" style={{ borderColor: 'var(--line)' }}>
            <div className="label mb-1">{isPolice ? 'Station' : 'Barangay'}</div>
            <div className="data text-[11px] truncate" style={{ color: 'var(--text-2)' }}>
              {/* BUG FOUND 2026-09-04 (caught live: a PNP admin's own sidebar
                  read "STATION-0724F2A3" -- the raw internal station id,
                  never something a human typed or should see). barangay_id
                  happened to read fine because DevTeam types barangay ids by
                  hand as lowercase names ("cogon"), but a station's id is
                  always a generated "station-<uuid8>" -- this was never
                  going to show anything real for any PNP account, ever.
                  location_name is the backend's resolved barangays.name /
                  police_stations.name (see backend.py's _location_name);
                  falling back to the raw id keeps a session logged in
                  before this fix (whose cached ecoUser has no
                  location_name yet) showing SOMETHING rather than blank. */}
              {(currentUser.location_name
                || (isPolice ? currentUser.station_id : currentUser.barangay_id)
                || 'UNASSIGNED').toString().toUpperCase()}
            </div>
          </div>
        </nav>

        {/* ═══ MAIN ═══════════════════════════════════════════════════════ */}
        <main className="flex-1 flex min-w-0">

          {activeTab === 'dashboard' && (
            <>
              {/* Video wall -- takes all remaining space. On a monitoring
                  station the footage IS the interface; everything else is
                  supporting chrome. */}
              <section className="flex-1 flex flex-col min-w-0">
                <div
                  className="h-9 shrink-0 flex items-center justify-between px-2.5 border-b"
                  style={{ background: 'var(--panel)', borderColor: 'var(--line)' }}
                >
                  <div className="flex items-center gap-2 min-w-0">
                    <Grid2X2 size={13} style={{ color: 'var(--text-2)' }} />
                    <span className="label" style={{ color: 'var(--text)' }}>
                      {selectedCam ? `Single View — ${selectedCam.name}` : `Video Wall — ${cameras.length} Feed${cameras.length === 1 ? '' : 's'}`}
                    </span>
                  </div>

                  <div className="flex items-center gap-1.5">
                    {selectedCam && (
                      <button
                        onClick={() => setSelectedCam(null)}
                        className="flex items-center gap-1 px-2 py-1 text-[11px] font-bold uppercase tracking-wider text-white transition-all hover:opacity-90 active:scale-[0.97] hover-lift"
                        style={{ background: 'var(--accent)' }}
                      >
                        <ArrowLeft size={12} />
                        All Feeds
                      </button>
                    )}
                    {/* Camera source selector: local index OR network stream URL.
                        Collapsed behind an icon by default -- a raw device index
                        or RTSP URL is a setup-time control, not something an
                        operator's eye needs competing for space with the feed
                        count and incident count every single shift. */}
                    <div className="relative">
                      <button
                        onClick={() => setSrcPanelOpen(o => !o)}
                        title="Camera source"
                        aria-label="Camera source"
                        className="h-6 w-6 flex items-center justify-center border transition-colors"
                        style={{
                          borderColor: srcPanelOpen ? 'var(--accent)' : 'var(--line)',
                          background: srcPanelOpen ? 'var(--accent-dim)' : 'transparent',
                          color: sourceIsNetwork ? 'var(--ok)' : 'var(--text-2)',
                          borderRadius: 'var(--radius-sm)',
                        }}
                      >
                        {sourceIsNetwork ? <Wifi size={12} /> : <CameraIcon size={12} />}
                      </button>

                      {srcPanelOpen && (
                        <div
                          className="absolute right-0 top-8 z-30 p-2.5 border animate-scale-in"
                          style={{ background: 'var(--panel)', borderColor: 'var(--line)', borderRadius: 'var(--radius-md)', boxShadow: '0 8px 24px rgba(0,0,0,.25)' }}
                        >
                          <div className="label mb-1.5">Camera source</div>
                          <div className="relative flex items-center gap-1.5 h-7 px-2 border" style={{ borderColor: 'var(--line)', borderRadius: 'var(--radius-sm)' }}>
                            <input
                              type="text"
                              autoFocus
                              value={camIndexInput}
                              onChange={(e) => setCamIndexInput(e.target.value)}
                              onKeyDown={(e) => { if (e.key === 'Enter') handleApplyCameraSource(); }}
                              placeholder={sourceIsNetwork ? currentSourceLabel : '0'}
                              title={
                                'Camera source: a local device index (e.g. 0) or a network stream URL' +
                                ' (rtsp://user:pass@10.0.0.12:554/stream1). Any ONVIF/RTSP camera works.' +
                                (currentSourceLabel ? `\nCurrently: ${currentSourceLabel}` : '')
                              }
                              className="data bg-transparent border-none text-[10px] outline-none"
                              style={{ width: sourceIsNetwork ? 190 : 40,
                                       textAlign: sourceIsNetwork ? 'left' : 'center',
                                       color: 'var(--text)' }}
                            />
                            {!sourceIsNetwork && availableCameras.length > 0 && (
                              <span className="data text-[9px]" style={{ color: 'var(--text-3)' }}>
                                [{availableCameras.join(',')}]
                              </span>
                            )}
                            <button
                              onClick={handleApplyCameraSource}
                              disabled={applyState === 'saving'}
                              title="Apply camera source"
                              aria-label="Apply camera source"
                              className="h-4 w-4 flex items-center justify-center transition-colors disabled:opacity-40"
                              style={{ color: 'var(--accent)' }}
                            >
                              {applyState === 'saving' ? <Loader2 size={11} className="animate-spin" /> : <Check size={11} />}
                            </button>
                            {applyState === 'saved' && <span className="data text-[9px]" style={{ color: 'var(--ok)' }}>OK</span>}
                            {applyState === 'error' && <span className="data text-[9px]" style={{ color: 'var(--critical)' }}>ERR</span>}
                          </div>
                          {sourceError && (
                            <div
                              className="data mt-1.5 px-2 py-1 border text-[9px]"
                              style={{ borderColor: 'var(--critical)', color: 'var(--critical)', background: 'var(--critical-dim)', borderRadius: 'var(--radius-sm)' }}
                            >
                              {sourceError}
                            </div>
                          )}
                        </div>
                      )}
                    </div>

                    <button
                      title="Fullscreen video wall"
                      onClick={() => setIsFullscreenGrid(true)}
                      className="h-6 w-6 flex items-center justify-center border transition-colors hover:bg-[var(--panel-2)]"
                      style={{ borderColor: 'var(--line)', color: 'var(--text-2)', borderRadius: 'var(--radius-sm)' }}
                    >
                      <Maximize2 size={12} />
                    </button>
                  </div>
                </div>

                <div className="flex-1 min-h-0 p-1.5" style={{ background: '#06070A' }}>
                  {selectedCam ? (
                    <CameraTile
                      cam={selectedCam}
                      aiUrl={aiUrl}
                      alerted={isSirenActive}
                      large
                    />
                  ) : cameras.length === 0 ? (
                    <div className="h-full flex flex-col items-center justify-center gap-2">
                      <Video size={22} style={{ color: 'var(--text-3)' }} />
                      <span className="label">No cameras registered</span>
                    </div>
                  ) : (
                    <div className={`grid gap-1.5 h-full ${gridColsFor(cameras.length)}`}>
                      {cameras.map(cam => (
                        <CameraTile
                          key={cam.id}
                          cam={cam}
                          aiUrl={aiUrl}
                          alerted={pendingAlerts.some(a => a.cameraLinkId === cam.id)}
                          onClick={() => setSelectedCam(cam)}
                        />
                      ))}
                    </div>
                  )}
                </div>
              </section>

              {/* ═══ INCIDENT QUEUE ═════════════════════════════════════ */}
              <aside
                className="w-[310px] shrink-0 flex flex-col border-l"
                style={{ background: 'var(--panel)', borderColor: 'var(--line)' }}
              >
                <div
                  className="h-9 shrink-0 flex items-center justify-between px-2.5 border-b"
                  style={{ borderColor: 'var(--line)' }}
                >
                  <span className="label" style={{ color: 'var(--text)' }}>Incident Queue</span>
                  <span
                    className="data text-[10px] px-1.5 py-0.5 border"
                    style={{
                      color: pendingAlerts.length ? 'var(--critical)' : 'var(--text-3)',
                      borderColor: pendingAlerts.length ? 'var(--critical)' : 'var(--line)',
                      borderRadius: 'var(--radius-sm)',
                    }}
                  >
                    {String(pendingAlerts.length).padStart(2, '0')}
                  </span>
                </div>

                {/* aria-live="assertive" so a screen reader announces an
                    incoming incident immediately instead of only when the
                    operator happens to navigate here. "assertive" rather than
                    "polite" is deliberate: a detected assault is exactly the
                    case that should interrupt whatever is being read out.
                    aria-relevant="additions" keeps it from re-reading the whole
                    queue every time one incident is resolved. */}
                <div
                  className="flex-1 overflow-y-auto custom-scrollbar"
                  role="log"
                  aria-live="assertive"
                  aria-relevant="additions"
                  aria-label="Incident queue, newest first"
                >
                  {!alertsLoaded ? (
                    <div className="p-2 space-y-2" aria-busy="true">
                      <SkeletonRow />
                      <SkeletonRow />
                      <SkeletonRow />
                    </div>
                  ) : pendingAlerts.length === 0 ? (
                    <div className="h-full flex flex-col items-center justify-center gap-2 px-4">
                      <span className="status-dot ok" />
                      <span className="label text-center">Monitoring — no incidents awaiting review</span>
                    </div>
                  ) : (
                    pendingAlerts.map(alert => (
                      <IncidentRow
                        key={alert.id}
                        alert={alert}
                        cameras={cameras}
                        onConfirm={handleVerifyCrime}
                        onDismiss={handleDismissCrime}
                      />
                    ))
                  )}
                </div>
              </aside>
            </>
          )}

          {activeTab !== 'dashboard' && (
            <div key={activeTab} className="flex-1 min-w-0 overflow-y-auto custom-scrollbar p-3 animate-fade-in">
              {activeTab === 'crime-reports' && (
                <CrimeReportsView
                  onUpdate={fetchStats}
                  currentUserRole={currentUser.role}
                  onDeepLink={(crimeId: string) => {
                    // view_map and view_records are separate permissions --
                    // a user can have one without the other, and this link
                    // lives inside the map (view_map-gated) but jumps to the
                    // Recordings tab (view_records-gated). Same bug as the
                    // nav items above in a different shape: don't navigate
                    // somewhere the click would just 403.
                    if (!can('view_records')) return;
                    setVideoRecordSearchQuery(crimeId);
                    setActiveTab('records');
                  }}
                />
              )}

              {activeTab === 'alerts' && <HistoryView />}

              {activeTab === 'records' && <RecordsView defaultSearchQuery={videoRecordSearchFilter} />}

              {activeTab === 'health' && (
                <div className="space-y-3">
                  <div className="grid grid-cols-2 gap-3">
                    <MetricPanel
                      label="Battery Reserve"
                      value={`${telemetry.battery}%`}
                      icon={<BatteryMedium size={16} />}
                      bar={telemetry.battery}
                      tone={telemetry.battery < 25 ? 'critical' : telemetry.battery < 50 ? 'warn' : 'ok'}
                    />
                    <MetricPanel
                      label="Solar Input"
                      value={`${telemetry.solarV} V`}
                      icon={<Sun size={16} />}
                    />
                  </div>
                  <div className="grid grid-cols-3 gap-3">
                    <MetricPanel label="Neural Engine" value={`${telemetry.tempNeural}°C`} icon={<Cpu size={16} />} tone={tempTone(telemetry.tempNeural)} />
                    <MetricPanel label="Application CPU" value={`${telemetry.tempCPU}°C`} icon={<Thermometer size={16} />} tone={tempTone(telemetry.tempCPU)} />
                    <MetricPanel label="Edge MCU" value={`${telemetry.tempESP}°C`} icon={<Zap size={16} />} tone={tempTone(telemetry.tempESP)} />
                  </div>
                  {/* Barangay-admin only, not staff -- owning the hardware is
                      what earns this, same split as manage_cameras. See
                      backend.py's MODEL_VIEW_ROLES comment for the full
                      reasoning on why optimize is here but the on/off
                      switches are not. */}
                  {currentUser.role === 'BARANGAY_ADMIN' && <AiModelsPanel />}
                </div>
              )}

              {activeTab === 'cameras' && (
                <div className="grid grid-cols-2 xl:grid-cols-3 gap-3 content-start">
                  {cameras.map(cam => (
                    <div
                      key={cam.id}
                      className="border p-3 hover-lift"
                      style={{ background: 'var(--panel)', borderColor: 'var(--line)' }}
                    >
                      <div className="flex justify-between items-start mb-2.5">
                        <div className="flex items-center gap-2 min-w-0">
                          <span className={`status-dot ${cam.status === 'offline' ? 'off' : 'ok'}`} />
                          <span className="text-[11px] font-bold truncate" style={{ color: 'var(--text)' }}>{cam.name}</span>
                        </div>
                        {can('manage_cameras') && (
                          <button
                            title="Remove camera"
                            onClick={(e) => { e.stopPropagation(); deleteCam(cam.id); }}
                            className="h-5 w-5 flex items-center justify-center transition-colors"
                            style={{ color: 'var(--text-3)' }}
                            onMouseEnter={(e) => (e.currentTarget.style.color = 'var(--critical)')}
                            onMouseLeave={(e) => (e.currentTarget.style.color = 'var(--text-3)')}
                          >
                            <Trash2 size={13} />
                          </button>
                        )}
                      </div>
                      <div className="label mb-1">Stream Source</div>
                      <p
                        className="data text-[10px] truncate px-2 py-1.5 border"
                        style={{ background: 'var(--bg)', borderColor: 'var(--line)', color: 'var(--text-2)' }}
                      >
                        {cam.url}
                      </p>
                    </div>
                  ))}
                  {can('manage_cameras') && (
                    <button
                      onClick={() => setShowModal(true)}
                      className="border border-dashed flex flex-col items-center justify-center gap-1.5 p-6 min-h-[112px] transition-colors hover:bg-white/[0.02]"
                      style={{ borderColor: 'var(--line-2)', color: 'var(--text-2)' }}
                    >
                      <Plus size={18} />
                      <span className="label">Register Camera</span>
                    </button>
                  )}
                </div>
              )}

              {activeTab === 'manage-users' && <AdminUsersView />}
              {activeTab === 'devteam' && <DevteamView />}
              {activeTab === 'profile' && <ProfileView currentUser={currentUser} onLogout={handleLogout} />}
            </div>
          )}
        </main>
      </div>

      {/* ═══ FULLSCREEN VIDEO WALL ═══════════════════════════════════════ */}
      {isFullscreenGrid && (
        <div className="fixed inset-0 z-[100] flex flex-col animate-fade-in" style={{ background: '#06070A' }}>
          <div
            className="h-9 shrink-0 flex items-center justify-between px-2.5 border-b"
            style={{ background: 'var(--panel)', borderColor: 'var(--line)' }}
          >
            <div className="flex items-center gap-2">
              <Shield size={13} style={{ color: 'var(--accent)' }} />
              <span className="label" style={{ color: 'var(--text)' }}>
                {selectedCam ? `Single View — ${selectedCam.name}` : 'Video Wall — Fullscreen'}
              </span>
              {isSirenActive && (
                <span
                  className="data text-[10px] px-1.5 py-0.5 border pulse-alert"
                  style={{ color: 'var(--critical)', borderColor: 'var(--critical)', background: 'rgba(229,52,47,0.12)' }}
                >
                  SIREN ACTIVE
                </span>
              )}
            </div>
            <div className="flex items-center gap-1.5">
              {selectedCam && (
                <button
                  onClick={() => setSelectedCam(null)}
                  className="flex items-center gap-1 px-2.5 py-1 text-[11px] font-bold uppercase tracking-wider text-white transition-all hover:opacity-90 active:scale-[0.97] hover-lift"
                  style={{ background: 'var(--accent)' }}
                >
                  <ArrowLeft size={13} />
                  All Feeds
                </button>
              )}
              <button
                title="Exit fullscreen"
                onClick={() => { setIsFullscreenGrid(false); setIsSirenActive(false); }}
                className="h-6 w-6 flex items-center justify-center border transition-colors hover:bg-white/5"
                style={{ borderColor: 'var(--line)', color: 'var(--text-2)' }}
              >
                <X size={13} />
              </button>
            </div>
          </div>
          <div className="flex-1 min-h-0 p-1.5 flex flex-col gap-1.5">
            {selectedCam ? (
              <>
                <div className="flex-1 min-h-0">
                  <CameraTile cam={selectedCam} aiUrl={aiUrl} alerted={isSirenActive} large />
                </div>
                {/* Only on a single selected camera: aiming a camera is a
                    per-camera action, and a control pad over a multi-feed
                    wall would be ambiguous about which camera it drives. */}
                <div className="shrink-0 flex items-center justify-center py-1">
                  <PTZControls apiUrl={apiUrl} />
                </div>
              </>
            ) : (
              <div className={`grid gap-1.5 h-full ${gridColsFor(cameras.length)}`}>
                {cameras.map(cam => (
                  <CameraTile
                    key={cam.id}
                    cam={cam}
                    aiUrl={aiUrl}
                    alerted={pendingAlerts.some(a => a.cameraLinkId === cam.id)}
                    onClick={() => setSelectedCam(cam)}
                  />
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* ═══ REGISTER CAMERA ═════════════════════════════════════════════ */}
      {showModal && (
        <div className="fixed inset-0 bg-black/80 z-[60] flex items-center justify-center p-6 animate-fade-in">
          <div className="w-full max-w-sm border animate-scale-in" style={{ background: 'var(--panel)', borderColor: 'var(--line-2)' }}>
            <div
              className="h-9 flex items-center justify-between px-2.5 border-b"
              style={{ borderColor: 'var(--line)' }}
            >
              <span className="label" style={{ color: 'var(--text)' }}>Register Camera</span>
              <button
                title="Cancel"
                onClick={() => setShowModal(false)}
                className="h-5 w-5 flex items-center justify-center"
                style={{ color: 'var(--text-2)' }}
              >
                <X size={13} />
              </button>
            </div>
            <div className="p-3 space-y-3">
              <div>
                <label htmlFor="cam-name" className="label block mb-1">Camera Name</label>
                <input
                  id="cam-name"
                  className="data w-full px-2.5 py-2 text-[11px] border outline-none"
                  style={{ background: 'var(--bg)', borderColor: 'var(--line)', color: 'var(--text)' }}
                  placeholder="e.g. Sector C Entrance"
                />
              </div>
              <div>
                <label htmlFor="cam-url" className="label block mb-1">Stream URL</label>
                <input
                  id="cam-url"
                  className="data w-full px-2.5 py-2 text-[11px] border outline-none"
                  style={{ background: 'var(--bg)', borderColor: 'var(--line)', color: 'var(--text)' }}
                  placeholder="rtsp://..."
                />
              </div>
              <button
                onClick={() => {
                  const n = (document.getElementById('cam-name') as HTMLInputElement).value;
                  const u = (document.getElementById('cam-url') as HTMLInputElement).value;
                  if (n && u) handleUpsertNode(n, u);
                }}
                className="w-full py-2.5 text-[11px] font-bold uppercase tracking-wider text-white transition-all hover:opacity-90 active:scale-[0.98]"
                style={{ background: 'var(--accent)' }}
              >
                Register
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/* ── Layout helper ──────────────────────────────────────────────────────
   Video-wall column count follows CCTV convention (1/4/9/16 style) rather
   than a fixed 2-up, so tiles stay as large as possible for the number of
   feeds actually connected. */
