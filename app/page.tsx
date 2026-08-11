"use client";

import dynamic from 'next/dynamic';
import React, { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { useRuntimeConfig } from './hooks/useRuntimeConfig';
import { useLiveChannel } from './context/WebSocketContext';
import { usePermissions } from './hooks/usePermissions';
import {
  Shield, AlertOctagon, Activity, Video, Cpu, Trash2, MapPin,
  Maximize2, X, Sun, User, LogOut,
  BatteryMedium, Thermometer, Zap, Plus, Film, Users, Terminal,
  Camera as CameraIcon, Check, Loader2, Grid2X2
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
  cameraLinkId?: string;
};

type Camera = {
  id: string;
  name: string;
  url: string;
  status: 'online' | 'offline';
};

// Isolated so its own 1s tick doesn't re-render the whole dashboard tree
// (nav, camera grid, incident queue, every modal-conditional block) --
// that state used to live in the top-level EcoVisionSentinel component,
// which has no memoization anywhere below it, so every descendant re-ran
// its render function once a second just to update this one clock string.
function SystemClockText() {
  const [time, setTime] = useState(() => new Date().toLocaleTimeString('en-GB', { hour12: false }));
  useEffect(() => {
    const t = setInterval(() => setTime(new Date().toLocaleTimeString('en-GB', { hour12: false })), 1000);
    return () => clearInterval(t);
  }, []);
  return <>{time}</>;
}

function SystemDateText() {
  const [date, setDate] = useState(() => new Date().toISOString().slice(0, 10));
  useEffect(() => {
    const t = setInterval(() => setDate(new Date().toISOString().slice(0, 10)), 60000);
    return () => clearInterval(t);
  }, []);
  return <>{date}</>;
}

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
  // Tracked separately from backendOnline: the storage backend (8000) and the
  // AI vision core (8001) are different processes with very different startup
  // times, so "System Nominal" must not imply the camera feed is live.
  const [aiCoreOnline, setAiCoreOnline] = useState(false);
  const [videoRecordSearchFilter, setVideoRecordSearchQuery] = useState("");
  const [telemetry, setTelemetry] = useState({ battery: 88, solarV: 14.4, tempCPU: 42, tempESP: 38, tempNeural: 51, load: 12.4 });
  const [camIndexInput, setCamIndexInput] = useState("5");
  const [availableCameras, setAvailableCameras] = useState<number[]>([]);
  const [applyState, setApplyState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const router = useRouter();

  // Auth gate only. The camera fetch used to live here too, but this effect
  // runs on mount -- before useRuntimeConfig() has resolved the real apiUrl --
  // so in a packaged build with dynamic ports it hit the default :8000 once,
  // failed silently, and never retried. Splitting it means the fetch waits
  // for the resolved config instead of racing it.
  useEffect(() => {
    const savedUser = localStorage.getItem('ecoUser');
    if (!savedUser) {
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
        setCamIndexInput(data.current_index.toString());
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

  const handleApplyCameraIndex = async () => {
    const idx = parseInt(camIndexInput, 10);
    if (Number.isNaN(idx) || idx < 0) return;
    setApplyState('saving');
    try {
      const res = await fetch(`${aiUrl}/set_camera_index`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ index: idx })
      });
      const data = await res.json();
      setApplyState(data.status === "reopened" ? 'saved' : 'error');
    } catch (e) {
      console.error("Camera index swap failed:", e);
      setApplyState('error');
    }
    setTimeout(() => setApplyState('idle'), 2000);
  };

const fetchCameras = async (userObj: any) => {
    try {
      const barangay = userObj.barangayId && userObj.barangayId !== 'undefined' ? userObj.barangayId : 'cogon';
      const token = localStorage.getItem('ecoToken');
      const res = await fetch(`${apiUrl}/api/cameras?barangayId=${encodeURIComponent(barangay)}&role=${encodeURIComponent(userObj.role)}`, {
        headers: { Authorization: `Bearer ${token}` }
      });
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
      const barangay = currentUser.barangayId && currentUser.barangayId !== 'undefined' ? currentUser.barangayId : 'cogon';
      const role = currentUser.role || 'user';
      const token = localStorage.getItem('ecoToken');

      const res = await fetch(`${apiUrl}/api/incidents?userBarangayId=${encodeURIComponent(barangay)}&role=${encodeURIComponent(role)}&filterBarangayId=all`, {
        headers: { Authorization: `Bearer ${token}` }
      });
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
      const barangay = currentUser.barangayId && currentUser.barangayId !== 'undefined' ? currentUser.barangayId : 'cogon';
      const role = currentUser.role || 'user';
      const token = localStorage.getItem('ecoToken');

      const res = await fetch(`${apiUrl}/api/incidents?userBarangayId=${encodeURIComponent(barangay)}&role=${encodeURIComponent(role)}&filterBarangayId=all`, {
        headers: { Authorization: `Bearer ${token}` }
      });
      if (res.ok) {
        const data = await res.json();
        const activeDetections = data.filter((inc: any) => inc.status === 'Active');
        const mappedAlerts = activeDetections.map((inc: any) => ({
          id: inc.id,
          type: inc.type,
          severity: 'CRITICAL' as const,
          location: inc.locationName,
          area: `${inc.barangayId ? inc.barangayId.toUpperCase() : 'GLOBAL'} Sector`,
          timestamp: inc.militaryTime,
          confidence: inc.confidence ?? 0.925,
          status: 'pending' as const,
          cameraLinkId: inc.locationName.includes("Entrance") ? "1" : "2"
        }));
        setAlerts(mappedAlerts);
        setBackendOnline(true);
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
  useLiveChannel("incidents", () => {
    fetchStats();
    fetchActiveAlertCache();
  });

  const handleUpsertNode = async (name: string, url: string) => {
    try {
      const barangay = currentUser.barangayId && currentUser.barangayId !== 'undefined' ? currentUser.barangayId : 'cogon';
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
      fetch(`${apiUrl}/siren/activate`, { method: "POST" }).catch(e => console.error(e));
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
      fetch(`${apiUrl}/siren/deactivate`, { method: "POST" }).catch(e => console.error(e));
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

  const handleLogout = () => { localStorage.removeItem('ecoUser'); router.push('/loginpage/login'); };

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
            <Shield size={15} style={{ color: 'var(--accent)' }} className="stroke-[2.2]" />
            <span className="text-[11px] font-bold tracking-[0.18em] text-white">ECOVISION</span>
            <span className="data text-[9px] px-1 py-px border" style={{ color: 'var(--text-3)', borderColor: 'var(--line)' }}>
              SENTINEL
            </span>
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
              style={{ background: 'rgba(229,52,47,0.12)', borderColor: 'var(--critical)' }}
            >
              <AlertOctagon size={13} style={{ color: 'var(--critical)' }} />
              <span className="data text-[11px] font-bold" style={{ color: 'var(--critical)' }}>
                {pendingAlerts.length} UNVERIFIED
              </span>
            </div>
          ) : (
            <div className="flex items-center gap-2 px-2.5 h-7 border" style={{ borderColor: 'var(--line)' }}>
              <span className="status-dot ok" />
              <span className="label">No Active Incidents</span>
            </div>
          )}

          <div className="flex items-center gap-2 px-2.5 h-7 border" style={{ borderColor: 'var(--line)' }}>
            <span className="data text-[10px]" style={{ color: 'var(--text-3)' }}><SystemDateText /></span>
            <span className="data text-[12px] font-bold text-white"><SystemClockText /></span>
          </div>

          <button
            onClick={() => setActiveTab('profile')}
            title="Operator profile"
            className="flex items-center gap-2 h-7 px-2.5 border transition-colors"
            style={{
              borderColor: activeTab === 'profile' ? 'var(--accent)' : 'var(--line)',
              background: activeTab === 'profile' ? 'rgba(45,111,247,0.12)' : 'transparent',
            }}
          >
            <User size={12} style={{ color: 'var(--text-2)' }} />
            <div className="flex items-baseline gap-1.5">
              <span className="data text-[10px] text-white">{currentUser.username || 'OPERATOR'}</span>
              <span className="label" style={{ fontSize: '8px' }}>{currentUser.role}</span>
            </div>
          </button>

          <button
            onClick={handleLogout}
            title="Sign out"
            className="h-7 w-7 flex items-center justify-center border transition-colors hover:bg-white/5"
            style={{ borderColor: 'var(--line)', color: 'var(--text-2)' }}
          >
            <LogOut size={12} />
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
                <NavItem label="Incident Map" icon={<MapPin size={16} />} active={activeTab === 'crime-reports'} onClick={() => setActiveTab('crime-reports')} badge={sqlReportCount} badgeTone="neutral" />
                <NavItem label="Recordings" icon={<Film size={16} />} active={activeTab === 'records'} onClick={() => { setActiveTab('records'); setVideoRecordSearchQuery(""); }} />
                <NavItem label="Incident Log" icon={<AlertOctagon size={16} />} active={activeTab === 'alerts'} onClick={() => setActiveTab('alerts')} badge={pendingAlerts.length} badgeTone="critical" />
              </>
            )}
            {isBarangay && (
              <>
                <NavSectionLabel>Operations</NavSectionLabel>
                <NavItem label="Live Monitor" icon={<Activity size={16} />} active={activeTab === 'dashboard'} onClick={() => setActiveTab('dashboard')} />
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

          <div className="px-3 py-2.5 border-t" style={{ borderColor: 'var(--line)' }}>
            <div className="label mb-1">Assignment</div>
            <div className="data text-[11px] truncate" style={{ color: 'var(--text-2)' }}>
              {(currentUser.barangayId || 'UNASSIGNED').toString().toUpperCase()}
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
                    {selectedCam && (
                      <button
                        onClick={() => setSelectedCam(null)}
                        className="label px-1.5 py-0.5 border transition-colors hover:bg-white/5"
                        style={{ borderColor: 'var(--line-2)', color: 'var(--text-2)' }}
                      >
                        ← All Feeds
                      </button>
                    )}
                  </div>

                  <div className="flex items-center gap-1.5">
                    {/* Camera source selector */}
                    <div className="flex items-center gap-1.5 h-6 px-1.5 border" style={{ borderColor: 'var(--line)' }}>
                      <CameraIcon size={11} style={{ color: 'var(--text-3)' }} />
                      <span className="label" style={{ fontSize: '8px' }}>SRC</span>
                      <input
                        type="number"
                        min={0}
                        value={camIndexInput}
                        onChange={(e) => setCamIndexInput(e.target.value)}
                        title="Camera device index"
                        className="data w-8 bg-transparent border-none text-[10px] text-white outline-none text-center"
                      />
                      {availableCameras.length > 0 && (
                        <span className="data text-[9px]" style={{ color: 'var(--text-3)' }}>
                          [{availableCameras.join(',')}]
                        </span>
                      )}
                      <button
                        onClick={handleApplyCameraIndex}
                        disabled={applyState === 'saving'}
                        title="Apply camera index"
                        className="h-4 w-4 flex items-center justify-center transition-colors disabled:opacity-40"
                        style={{ color: 'var(--accent)' }}
                      >
                        {applyState === 'saving' ? <Loader2 size={11} className="animate-spin" /> : <Check size={11} />}
                      </button>
                      {applyState === 'saved' && <span className="data text-[9px]" style={{ color: 'var(--ok)' }}>OK</span>}
                      {applyState === 'error' && <span className="data text-[9px]" style={{ color: 'var(--critical)' }}>ERR</span>}
                    </div>

                    <button
                      title="Fullscreen video wall"
                      onClick={() => setIsFullscreenGrid(true)}
                      className="h-6 w-6 flex items-center justify-center border transition-colors hover:bg-white/5"
                      style={{ borderColor: 'var(--line)', color: 'var(--text-2)' }}
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
                    }}
                  >
                    {String(pendingAlerts.length).padStart(2, '0')}
                  </span>
                </div>

                <div className="flex-1 overflow-y-auto custom-scrollbar">
                  {pendingAlerts.length === 0 ? (
                    <div className="h-full flex flex-col items-center justify-center gap-2 px-4">
                      <span className="status-dot ok" />
                      <span className="label text-center">Monitoring — no incidents awaiting review</span>
                    </div>
                  ) : (
                    pendingAlerts.map(alert => (
                      <IncidentRow
                        key={alert.id}
                        alert={alert}
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
                  onDeepLink={(crimeId: string) => {
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
                          <span className="text-[11px] font-bold text-white truncate">{cam.name}</span>
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
                  className="label px-2 py-1 border transition-colors hover:bg-white/5"
                  style={{ borderColor: 'var(--line-2)', color: 'var(--text-2)' }}
                >
                  ← All Feeds
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
          <div className="flex-1 min-h-0 p-1.5">
            {selectedCam ? (
              <CameraTile cam={selectedCam} aiUrl={aiUrl} alerted={isSirenActive} large />
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
                  className="data w-full px-2.5 py-2 text-[11px] text-white border outline-none"
                  style={{ background: 'var(--bg)', borderColor: 'var(--line)' }}
                  placeholder="e.g. Sector C Entrance"
                />
              </div>
              <div>
                <label htmlFor="cam-url" className="label block mb-1">Stream URL</label>
                <input
                  id="cam-url"
                  className="data w-full px-2.5 py-2 text-[11px] text-white border outline-none"
                  style={{ background: 'var(--bg)', borderColor: 'var(--line)' }}
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
function gridColsFor(count: number) {
  if (count <= 1) return 'grid-cols-1';
  if (count <= 4) return 'grid-cols-2';
  if (count <= 9) return 'grid-cols-3';
  return 'grid-cols-4';
}

function tempTone(t: number): 'ok' | 'warn' | 'critical' {
  if (t >= 80) return 'critical';
  if (t >= 65) return 'warn';
  return 'ok';
}

/* ── Camera tile ────────────────────────────────────────────────────────
   Feeds render at FULL opacity at all times -- the previous design dimmed
   them to 60% until hover, which is actively unsafe for a monitoring wall
   (you cannot watch what you cannot see). Identification is burned into the
   frame as OSD text the way real CCTV does, so a screenshot of a tile is
   self-documenting for evidence. */
function CameraTile({ cam, aiUrl, alerted, onClick, large }: any) {
  const Wrapper: any = onClick ? 'button' : 'div';
  return (
    <Wrapper
      onClick={onClick}
      className={`relative bg-black overflow-hidden group text-left w-full h-full border transition-colors${onClick ? ' hover-lift cursor-pointer' : ''}`}
      style={{ borderColor: alerted ? 'var(--critical)' : 'var(--line)' }}
    >
      <img
        src={`${aiUrl}/video_feed`}
        className="w-full h-full object-cover"
        alt={`${cam.name} live feed`}
      />

      {/* Top OSD: identity + live state */}
      <div className="absolute top-0 inset-x-0 flex items-start justify-between p-1.5 pointer-events-none">
        <span className={`osd ${large ? 'text-[12px]' : 'text-[10px]'} font-bold text-white`}>
          {cam.name?.toUpperCase()}
        </span>
        <span className="flex items-center gap-1">
          <span className="status-dot live" />
          <span className={`osd ${large ? 'text-[11px]' : 'text-[9px]'} font-bold text-white`}>LIVE</span>
        </span>
      </div>

      {/* Bottom OSD: burned-in timestamp, as on any evidentiary recording */}
      <div className="absolute bottom-0 inset-x-0 flex items-end justify-between p-1.5 pointer-events-none">
        <span className={`osd ${large ? 'text-[11px]' : 'text-[9px]'} text-white/90`}>
          <SystemDateText /> <SystemClockText />
        </span>
        {alerted && (
          <span
            className="osd text-[9px] font-bold px-1 py-0.5 pulse-alert"
            style={{ background: 'var(--critical)', color: '#fff' }}
          >
            THREAT
          </span>
        )}
      </div>

      {/* Alert frame -- a hard border, not a soft glow, so it survives being
          seen at an angle or on a cheap monitor */}
      {alerted && (
        <div
          className="absolute inset-0 border-2 pointer-events-none pulse-alert"
          style={{ borderColor: 'var(--critical)' }}
        />
      )}
    </Wrapper>
  );
}

/* ── Nav rail ───────────────────────────────────────────────────────────── */
// Section dividers: with role-gated items the rail can show anywhere from 1 to
// 5 entries, and an unlabelled flat list gives an operator no cue that
// "Personnel" is a different kind of thing from "Live Monitor".
function NavSectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="label px-3 pt-2.5 pb-1.5 first:pt-0.5" style={{ color: 'var(--text-3)' }}>
      {children}
    </div>
  );
}

function NavItem({ icon, label, badge, badgeTone = 'neutral', active, onClick }: any) {
  return (
    <button
      onClick={onClick}
      className="w-full flex items-center justify-between gap-2 pl-3 pr-2.5 py-2.5 transition-all relative active:scale-[0.99]"
      style={{
        background: active ? 'rgba(45,111,247,0.10)' : 'transparent',
        color: active ? '#fff' : 'var(--text-2)',
      }}
      onMouseEnter={(e) => { if (!active) e.currentTarget.style.background = 'var(--panel-2)'; }}
      onMouseLeave={(e) => { if (!active) e.currentTarget.style.background = 'transparent'; }}
    >
      {active && (
        <span
          className="absolute left-0 inset-y-0 w-[3px] animate-scale-in"
          style={{ background: 'var(--accent)', transformOrigin: 'center' }}
        />
      )}
      <span className="flex items-center gap-2.5 min-w-0">
        <span className="shrink-0" style={{ color: active ? 'var(--accent)' : 'var(--text-3)' }}>{icon}</span>
        <span className="text-[12.5px] font-semibold tracking-wide truncate">{label}</span>
      </span>
      {badge > 0 && (
        <span
          className="data text-[10px] font-bold px-1.5 py-0.5 border shrink-0"
          style={
            badgeTone === 'critical'
              ? { color: 'var(--critical)', borderColor: 'var(--critical)' }
              : { color: 'var(--text-2)', borderColor: 'var(--line-2)' }
          }
        >
          {badge}
        </span>
      )}
    </button>
  );
}

/* ── Metric panel ───────────────────────────────────────────────────────── */
function MetricPanel({ label, value, icon, bar, tone = 'ok' }: any) {
  const toneColor =
    tone === 'critical' ? 'var(--critical)' : tone === 'warn' ? 'var(--warn)' : 'var(--ok)';
  return (
    <div className="border p-3 hover-lift" style={{ background: 'var(--panel)', borderColor: 'var(--line)' }}>
      <div className="flex items-start justify-between mb-2">
        <span className="label">{label}</span>
        <span style={{ color: 'var(--text-3)' }}>{icon}</span>
      </div>
      <div className="data text-2xl font-bold text-white leading-none">{value}</div>
      {typeof bar === 'number' && (
        <div className="mt-2.5 h-1 w-full" style={{ background: 'var(--bg)' }}>
          <div className="h-full transition-all duration-500" style={{ width: `${bar}%`, background: toneColor }} />
        </div>
      )}
    </div>
  );
}

/* ── Incident row ───────────────────────────────────────────────────────
   Dense log row rather than a padded card: an operator triaging a queue
   needs to compare many incidents at once, so vertical space spent on
   decoration is space taken from the next incident. */
function IncidentRow({ alert, onConfirm, onDismiss }: any) {
  return (
    <article
      className="border-b relative animate-rise-in"
      style={{ borderColor: 'var(--line)' }}
    >
      {/* Severity spine */}
      <span className="absolute left-0 inset-y-0 w-[3px]" style={{ background: 'var(--critical)' }} />

      <div className="pl-3 pr-2.5 py-2.5">
        <div className="flex items-baseline justify-between gap-2 mb-1">
          <span className="text-[12px] font-bold text-white tracking-wide truncate">
            {alert.type}
          </span>
          <span className="data text-[10px] shrink-0" style={{ color: 'var(--text-3)' }}>
            {alert.timestamp}
          </span>
        </div>

        <div className="flex items-center gap-1.5 mb-1">
          <MapPin size={10} style={{ color: 'var(--text-3)' }} className="shrink-0" />
          <span className="text-[10px] truncate" style={{ color: 'var(--text-2)' }}>
            {alert.location}
          </span>
        </div>

        <div className="flex items-center gap-1.5 mb-2.5">
          <span className="label" style={{ fontSize: '8px' }}>Confidence</span>
          <div className="flex-1 h-[3px]" style={{ background: 'var(--bg)' }}>
            <div
              className="h-full"
              style={{ width: `${alert.confidence * 100}%`, background: 'var(--warn)' }}
            />
          </div>
          <span className="data text-[10px]" style={{ color: 'var(--text-2)' }}>
            {(alert.confidence * 100).toFixed(1)}%
          </span>
        </div>

        <div className="grid grid-cols-2 gap-1.5">
          <button
            onClick={() => onConfirm(alert.id)}
            className="py-1.5 text-[10px] font-bold uppercase tracking-wider text-white transition-all hover:opacity-90 active:scale-[0.97]"
            style={{ background: 'var(--critical)' }}
          >
            Confirm
          </button>
          <button
            onClick={() => onDismiss(alert.id)}
            className="py-1.5 text-[10px] font-bold uppercase tracking-wider border transition-all hover:bg-white/5 active:scale-[0.97]"
            style={{ borderColor: 'var(--line-2)', color: 'var(--text-2)' }}
          >
            Dismiss
          </button>
        </div>
      </div>
    </article>
  );
}
