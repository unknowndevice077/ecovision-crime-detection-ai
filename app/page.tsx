"use client";

import CrimeReportsView from './components/CrimeReportsView';
import HistoryView from './components/dashboard/HistoryView';
import RecordsView from './components/RecordsView';
import ProfileView from './components/ProfileView';
import AdminUsersView from './components/dashboard/AdminUsersView';
import DevteamView from './components/dashboard/DevteamView';
import React, { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { 
  Shield, AlertOctagon, Activity, Video, Cpu, Trash2, MapPin, 
  ShieldAlert, Maximize2, X, Sun, User,
  BatteryMedium, Thermometer, Zap, Plus, Film, Clock, Users, Terminal
} from 'lucide-react';

const CrimeReportsViewAny = CrimeReportsView as any;
const RecordsViewAny = RecordsView as any;

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

export default function EcoVisionSentinel() {
  const [currentUser, setCurrentUser] = useState<any>(null);
  const [activeTab, setActiveTab] = useState('dashboard');
  const [selectedCam, setSelectedCam] = useState<Camera | null>(null);
  const [isFullscreenGrid, setIsFullscreenGrid] = useState(false);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [isSirenActive, setIsSirenActive] = useState(false);
  const [showModal, setShowModal] = useState(false);
  const [time, setTime] = useState("");
  const [sqlReportCount, setSqlReportCount] = useState(0);
  const [backendOnline, setBackendOnline] = useState(true);
  const [videoRecordSearchFilter, setVideoRecordSearchQuery] = useState("");
  const [telemetry, setTelemetry] = useState({ battery: 88, solarV: 14.4, tempCPU: 42, tempESP: 38, tempNeural: 51, load: 12.4 });
  const router = useRouter();
  
  useEffect(() => {
    const savedUser = localStorage.getItem('ecoUser');
    if (!savedUser) {
      router.push('/loginpage/login');
    } else {
      const parsedUser = JSON.parse(savedUser);
      setCurrentUser(parsedUser);
      if (parsedUser.role === 'DEVTEAM') {
        setActiveTab('devteam'); // DEVTEAM has no Monitor/dashboard view -- lands on the console
      }
      fetchCameras(parsedUser);
    }
  }, [router]);

  useEffect(() => {
    setTime(new Date().toLocaleTimeString());
    const t = setInterval(() => setTime(new Date().toLocaleTimeString()), 1000);
    return () => clearInterval(t);
  }, []);

const fetchCameras = async (userObj: any) => {
    try {
      const barangay = userObj.barangayId && userObj.barangayId !== 'undefined' ? userObj.barangayId : 'cogon';
      // FIXED: Added 'const' to declare the network response variable cleanly
      const res = await fetch(`http://localhost:8000/api/cameras?barangayId=${encodeURIComponent(barangay)}&role=${encodeURIComponent(userObj.role)}`);
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
      // FIXED: Checks for invalid properties and defaults to 'cogon' to prevent passing literal 'undefined' strings
      const barangay = currentUser.barangayId && currentUser.barangayId !== 'undefined' ? currentUser.barangayId : 'cogon';
      const role = currentUser.role || 'user';
      
      const res = await fetch(`http://localhost:8000/api/incidents?userBarangayId=${encodeURIComponent(barangay)}&role=${encodeURIComponent(role)}&filterBarangayId=all`);
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
      // FIXED: Sanitizes query context variables before initiating the HTTP request
      const barangay = currentUser.barangayId && currentUser.barangayId !== 'undefined' ? currentUser.barangayId : 'cogon';
      const role = currentUser.role || 'user';
      
      const res = await fetch(`http://localhost:8000/api/incidents?userBarangayId=${encodeURIComponent(barangay)}&role=${encodeURIComponent(role)}&filterBarangayId=all`);
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

  useEffect(() => {
    if (currentUser) {
      fetchStats();
      fetchActiveAlertCache();

      const interval = setInterval(() => {
        fetchStats();
        fetchActiveAlertCache();
      }, 5000);
      return () => clearInterval(interval);
    }
  }, [currentUser]);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let reconnectTimeout: any = null;

    const connectWebSocket = () => {
      if (!currentUser) return;
      socket = new WebSocket('ws://localhost:8000/ws');
      
      socket.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data);
          if (data.status === "CRITICAL") {
            setAlerts(prev => {
              if (prev.some(a => a.id === data.id)) return prev;
              return [{
                id: data.id || Math.random().toString(36).substr(2, 9),
                type: data.type || 'VIOLENCE',
                severity: 'CRITICAL',
                location: data.location || 'Cogon Core Smartpole Node',
                area: 'Cogon Sector',
                timestamp: new Date().toLocaleTimeString(),
                confidence: data.conf || 0.94,
                status: 'pending',
                cameraLinkId: data.cameraLinkId || "1"
              }, ...prev];
            });
            setSqlReportCount(prev => prev + 1);
          }
        } catch (err) {
          console.error("Error decoding inbound WebSocket string payload:", err);
        }
      };

      socket.onerror = () => {
        console.warn("WebSocket interface encountered an operational error. Server might be booting up.");
      };

      socket.onclose = () => {
        console.warn("WebSocket stream severed. Re-scheduling connection vector in 5 seconds...");
        reconnectTimeout = setTimeout(connectWebSocket, 5000);
      };
    };

    if (currentUser) {
      connectWebSocket();
    }

    return () => {
      if (socket) socket.close();
      if (reconnectTimeout) clearTimeout(reconnectTimeout);
    };
  }, [currentUser]);

  const handleUpsertNode = async (name: string, url: string) => {
    try {
      const barangay = currentUser.barangayId && currentUser.barangayId !== 'undefined' ? currentUser.barangayId : 'cogon';
      const res = await fetch("http://localhost:8000/api/cameras", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, url, barangayId: barangay })
      });
      if (res.ok) {
        fetchCameras(currentUser);
        setShowModal(false);
      }
    } catch (e) { console.error(e); }
  };

  const deleteCam = async (id: string) => {
    try {
      const res = await fetch(`http://localhost:8000/api/cameras/${id}`, { method: "DELETE" });
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
      fetch("http://localhost:8000/siren/activate", { method: "POST" }).catch(e => console.error(e));
      await fetch(`http://localhost:8000/api/incidents/${id}/status`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: "Confirmed" })
      });
      fetchStats();
      fetchActiveAlertCache();
    } catch (e) { console.error(e); }
  };

  const handleDismissCrime = async (id: string) => {
    setAlerts(prev => prev.filter(a => a.id !== id));
    try {
      fetch("http://localhost:8000/siren/deactivate", { method: "POST" }).catch(e => console.error(e));
      const res = await fetch(`http://localhost:8000/api/incidents/${id}/status`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
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

  if (!currentUser) return <div className="min-h-screen bg-[#0B0F17]" />;

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-[#0B0F17] text-slate-200 font-sans p-4 gap-4 relative selection:bg-emerald-500/30 selection:text-emerald-400">
      <style>{`
        :root { --battery-width: ${telemetry.battery}%; }
        .battery-fill { width: var(--battery-width); }
      `}</style>

      {/* ─── LEFT SIDEBAR PANEL ─── */}
      <aside className="w-64 bg-[#0E131F]/80 border border-white/[0.04] backdrop-blur-xl rounded-2xl flex flex-col p-5 shrink-0 shadow-2xl z-20 justify-between">
        <div className="flex flex-col flex-1 min-h-0">
          <div className="flex items-center gap-3 mb-6 pb-5 border-b border-white/[0.04]">
            <div className="p-2 bg-gradient-to-br from-emerald-500 to-teal-600 rounded-xl shadow-md shadow-emerald-500/10">
              <Shield className="w-4 h-4 text-[#0B0F17] stroke-[2.5]" />
            </div>
            <div>
              <h1 className="text-xs font-bold tracking-widest text-white flex items-center gap-1.5">
                ECOVISION <span className="text-emerald-400 font-mono text-[9px] px-1.5 py-0.5 rounded bg-emerald-500/10 border border-emerald-500/20 font-black">v15.0</span>
              </h1>
              <p className="text-[9px] text-slate-500 font-bold uppercase tracking-wider mt-0.5">Sentinel Security Command</p>
            </div>
          </div>

          <nav className="space-y-1.5 flex-1 overflow-y-auto custom-scrollbar pr-1">
            {(currentUser.role === 'POLICE' || currentUser.role === 'PRECINCT_CAPTAIN') && (
              <>
                <NavItem label="Monitor" icon={<Activity size={15}/>} active={activeTab === 'dashboard'} onClick={() => setActiveTab('dashboard')} />
                <NavItem label="Map Layout" icon={<MapPin size={15}/>} active={activeTab === 'crime-reports'} onClick={() => setActiveTab('crime-reports')} badge={sqlReportCount} />
                <NavItem label="Video Records" icon={<Film size={15}/>} active={activeTab === 'records'} onClick={() => { setActiveTab('records'); setVideoRecordSearchQuery(""); }} />
                <NavItem label="Crime History" icon={<AlertOctagon size={15}/>} active={activeTab === 'alerts'} onClick={() => setActiveTab('alerts')} badge={alerts.filter(a => a.status === 'pending').length} />
              </>
            )}
            {(currentUser.role === 'BARANGAY' || currentUser.role === 'BARANGAY_CAPTAIN') && (
               <>
                 <NavItem label="Monitor" icon={<Activity size={15}/>} active={activeTab === 'dashboard'} onClick={() => setActiveTab('dashboard')} />
                 <NavItem label="Add Cameras" icon={<Video size={15}/>} active={activeTab === 'cameras'} onClick={() => setActiveTab('cameras')} badge={cameras.length} />
                 <NavItem label="Hardware Status" icon={<Zap size={15}/>} active={activeTab === 'health'} onClick={() => setActiveTab('health')} />
               </>
            )}
            {(currentUser.role === 'PRECINCT_CAPTAIN' || currentUser.role === 'BARANGAY_CAPTAIN') && (
              <NavItem label="Manage Users" icon={<Users size={15}/>} active={activeTab === 'manage-users'} onClick={() => setActiveTab('manage-users')} />
            )}
            {currentUser.role === 'DEVTEAM' && (
              <NavItem label="DevTeam Console" icon={<Terminal size={15}/>} active={activeTab === 'devteam'} onClick={() => setActiveTab('devteam')} />
            )}
          </nav>
        </div>
        
        {/* SIDEBAR FOOTER METRICS LAYER */}
        <div className="pt-4 border-t border-white/[0.04] space-y-3 shrink-0">
          <div className="flex items-center justify-between px-4 py-2.5 rounded-xl border border-white/[0.03] bg-black/40 shadow-inner">
            <div className="flex items-center gap-2 text-[9px] font-mono text-slate-500 uppercase tracking-widest font-black">
              <Clock size={12} className="text-emerald-400" />
              <span>System Time</span>
            </div>
            <span className="text-xs font-mono font-bold tracking-widest text-slate-200 tabular-nums">{time}</span>
          </div>

          <button 
            onClick={() => setActiveTab('profile')} 
            className={`w-full p-3 border rounded-xl flex items-center gap-3 transition-all text-left shadow-lg group bg-black/20 ${activeTab === 'profile' ? 'border-emerald-500/40 bg-gradient-to-r from-emerald-500/[0.06] to-transparent' : 'border-white/[0.04] hover:border-white/10 hover:bg-black/40'}`}
          >
            <div className={`p-2 rounded-xl transition-colors ${activeTab === 'profile' ? 'bg-emerald-500 text-black' : 'bg-white/5 text-slate-400 group-hover:text-white'}`}>
              <User size={15} />
            </div>
            <div className="flex-1 min-w-0 font-mono">
              <span className="text-[10px] font-black uppercase tracking-wide text-slate-200 block truncate">{currentUser.username || "Operator"}</span>
              <span className="text-[8px] font-bold uppercase tracking-widest text-slate-500 block mt-0.5 group-hover:text-emerald-400 transition-colors">Scope: {currentUser.role || "UNKNOWN"}</span>
            </div>
          </button>
        </div>
      </aside>

      {/* ─── MAIN APP CONTENT SURFACE ─── */}
      <main className="flex-1 flex flex-col overflow-hidden">
        <div className="flex-1 overflow-hidden grid grid-cols-12 gap-4">
          <div className={`${activeTab === 'dashboard' ? 'col-span-8' : 'col-span-12'} h-full flex flex-col min-h-0 transition-all duration-500`}>
            
            {activeTab === 'dashboard' && (
              <div className="h-full flex flex-col rounded-2xl border border-white/[0.04] bg-[#0E131F]/50 overflow-hidden shadow-2xl relative">
                <div className="absolute top-4 left-4 right-4 h-12 flex items-center justify-between px-4 rounded-xl border border-white/[0.04] bg-[#0E131F]/80 backdrop-blur-md z-10 shadow-lg">
                  <div className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-wider text-slate-300">
                    <Activity size={14} className="text-emerald-400 animate-pulse" />
                    <span>{selectedCam ? `Focal Stream Target: ${selectedCam.name}` : "Surveillance Multi-Grid Feed"}</span>
                    {!backendOnline && (
                      <span className="ml-3 text-rose-400 font-mono text-[9px] px-1.5 py-0.5 rounded bg-rose-500/10 border border-rose-500/20 animate-pulse">⚠️ CORE OFFLINE</span>
                    )}
                  </div>
                  <div className="flex items-center gap-1.5">
                    <button title="Maximize Display Grid" onClick={() => setIsFullscreenGrid(true)} className="p-1.5 hover:bg-white/5 border border-white/[0.04] rounded-lg text-slate-400 hover:text-white transition-colors shadow-sm"><Maximize2 size={13} /></button>
                    {selectedCam && <button onClick={() => setSelectedCam(null)} className="text-[9px] font-bold uppercase tracking-wider text-emerald-400 px-2.5 py-1 rounded-lg border border-emerald-500/20 bg-emerald-500/5 hover:bg-emerald-500/15 transition-all">Reset Focus</button>}
                  </div>
                </div>

                <div className="flex-1 w-full h-full bg-[#0D0F14] p-4 pt-20">
                  {selectedCam ? (
                    <div className={`w-full h-full bg-black rounded-xl overflow-hidden border ${isSirenActive ? 'border-rose-500 animate-pulse shadow-[0_0_20px_rgba(239,68,68,0.2)]' : 'border-white/[0.04]'}`}>
                      <img src="http://localhost:8001/video_feed" className="w-full h-full object-cover" alt="Focused Channel Feed" />
                    </div>
                  ) : (
                    <div className="grid grid-cols-2 gap-4 h-full">
                      {cameras.map(cam => {
                        const hasUnverifiedThreat = alerts.some(a => a.cameraLinkId === cam.id && a.status === 'pending');
                        return (
                          <button key={cam.id} onClick={() => setSelectedCam(cam)} className={`bg-black rounded-xl relative group transition-all overflow-hidden border ${hasUnverifiedThreat ? 'border-rose-500 animate-pulse shadow-[0_0_15px_rgba(220,38,38,0.3)]' : 'border-white/[0.04] hover:border-emerald-500/30'}`}>
                            <img src="http://localhost:8001/video_feed" className="w-full h-full object-cover opacity-60 group-hover:opacity-100 transition-opacity duration-500" alt="Camera Node Capture" />
                            <span className="absolute bottom-3 left-3 text-[9px] font-bold uppercase text-slate-200 tracking-widest bg-[#0E131F]/80 backdrop-blur-md px-2 py-1 rounded border border-white/[0.04] font-mono">{cam.name}</span>
                          </button>
                        );
                      })}
                    </div>
                  )}
                </div>
              </div>
            )}

            {activeTab === 'crime-reports' && (
              <div className="h-full overflow-y-auto custom-scrollbar">
                <CrimeReportsViewAny 
                  onUpdate={fetchStats} 
                  onDeepLink={(crimeId: string) => {
                    setVideoRecordSearchQuery(crimeId);
                    setActiveTab('records');
                  }} 
                />
              </div>
            )}
            
            {activeTab === 'alerts' && <div className="h-full overflow-y-auto custom-scrollbar"><HistoryView /></div>}
            
            {activeTab === 'records' && (
              <div className="h-full overflow-y-auto custom-scrollbar">
                <RecordsViewAny defaultSearchQuery={videoRecordSearchFilter} />
              </div>
            )}

            {activeTab === 'health' && (
              <div className="space-y-4 h-full overflow-y-auto custom-scrollbar pr-1">
                 <div className="grid grid-cols-2 gap-4">
                  <div className="bg-[#0E131F]/40 border border-white/[0.04] rounded-2xl p-6 shadow-xl flex flex-col justify-between">
                    <div className="flex justify-between items-start">
                      <div>
                        <span className="text-[10px] font-bold text-slate-500 uppercase tracking-widest block mb-1">Smartpole Battery Reserve</span>
                        <span className="font-mono text-3xl font-bold text-white tabular-nums">{telemetry.battery}%</span>
                      </div>
                      <div className="p-2.5 bg-emerald-500/10 text-emerald-400 rounded-xl border border-emerald-500/20"><BatteryMedium size={20} /></div>
                    </div>
                    <div className="w-full bg-white/5 h-1.5 rounded-full overflow-hidden mt-6">
                      <div className="bg-emerald-500 h-full shadow-[0_0_10px_#10b981] transition-all duration-700 battery-fill" />
                    </div>
                  </div>
                  <div className="bg-[#0E131F]/40 border border-white/[0.04] rounded-2xl p-6 shadow-xl flex justify-between items-start">
                    <div>
                      <span className="text-[10px] font-bold text-slate-500 uppercase tracking-widest block mb-1">Solar Photovoltaic Input</span>
                      <span className="font-mono text-3xl font-bold text-white tabular-nums">{telemetry.solarV}V</span>
                    </div>
                    <div className="p-2.5 bg-amber-500/10 text-amber-400 rounded-xl border border-amber-500/20"><Sun size={20} /></div>
                  </div>
                </div>
                <div className="grid grid-cols-3 gap-4">
                  <HeatCard label="Neural Engine core" temp={telemetry.tempNeural} icon={<Cpu size={16}/>} />
                  <HeatCard label="Main Application CPU" temp={telemetry.tempCPU} icon={<Thermometer size={16}/>} />
                  <HeatCard label="Edge MCU Core Node" temp={telemetry.tempESP} icon={<Zap size={16}/>} />
                </div>
              </div>
            )}

            {activeTab === 'cameras' && (
              <div className="grid grid-cols-2 gap-4 h-full overflow-y-auto custom-scrollbar pr-1 content-start">
                {cameras.map(cam => (
                  <div key={cam.id} className="bg-[#0E131F]/40 border border-white/[0.04] rounded-2xl p-5 shadow-xl relative group hover:border-emerald-500/20 transition-all">
                    <div className="flex justify-between items-start mb-4">
                      <div className="p-2.5 rounded-xl bg-emerald-500/10 text-emerald-400 border border-emerald-500/10"><Video size={18}/></div>
                      <button title="Dismantle Camera Node" onClick={(e) => { e.stopPropagation(); deleteCam(cam.id); }} className="p-1.5 text-slate-500 hover:text-rose-400 transition-colors rounded-lg hover:bg-white/5"><Trash2 size={14}/></button>
                    </div>
                    <h4 className="text-xs font-bold uppercase text-white tracking-wide">{cam.name}</h4>
                    <p className="text-[10px] font-mono text-slate-500 truncate mt-2 bg-black/20 px-3 py-2 rounded-lg border border-white/[0.03]">{cam.url}</p>
                  </div>
                ))}
                <button onClick={() => setShowModal(true)} className="border border-dashed border-white/[0.08] bg-white/[0.01] hover:bg-white/[0.02] rounded-2xl flex flex-col items-center justify-center p-8 text-slate-500 hover:text-emerald-400 border-emerald-500/20 transition-all group min-h-[140px]">
                  <Plus size={24} className="mb-2 opacity-30 group-hover:opacity-100 transition-opacity" /><span className="text-[10px] font-bold uppercase tracking-widest">Register Smartpole Node</span>
                </button>
              </div>
            )}

            {activeTab === 'manage-users' && <AdminUsersView />}

            {activeTab === 'devteam' && <DevteamView />}

            {activeTab === 'profile' && <ProfileView currentUser={currentUser} time={time} onLogout={handleLogout} />}
          </div>

          {/* ─── RIGHT SIDE LIVE CRIME LOG FEED ─── */}
          {activeTab === 'dashboard' && (
            <div className="col-span-4 bg-[#0E131F]/40 border border-white/[0.04] backdrop-blur-md rounded-2xl flex flex-col overflow-hidden shadow-2xl animate-in slide-in-from-right duration-500">
              <div className="p-4 border-b border-white/[0.04] bg-white/[0.01] flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <ShieldAlert size={14} className="text-rose-400 animate-pulse" />
                  <h3 className="text-xs font-bold uppercase tracking-wider text-white">Live Real-Time Crime Log</h3>
                </div>
                <span className="px-2 py-0.5 text-[9px] font-black rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 uppercase tracking-wider">Active</span>
              </div>
              <div className="flex-1 overflow-y-auto p-4 space-y-3 custom-scrollbar bg-black/[0.08]">
                {alerts.filter(a => a.status === 'pending').map(alert => (
                  <ViolenceCard key={alert.id} alert={alert} onConfirm={handleVerifyCrime} onDismiss={handleDismissCrime} />
                ))}
                {alerts.filter(a => a.status === 'pending').length === 0 && (
                  <div className="h-full flex flex-col items-center justify-center opacity-20 py-20">
                    <Activity className="h-5 w-5 text-emerald-400 animate-pulse mb-2" /><span className="text-[9px] font-bold uppercase tracking-widest text-slate-400">Awaiting Edge Ingestion...</span>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </main>

      {/* ─── GRID EXPANSION OVERLAYS ─── */}
      {isFullscreenGrid && (
        <div className="fixed inset-0 z-[100] bg-[#0B0F17]/96 backdrop-blur-2xl p-6 flex flex-col animate-in fade-in duration-300">
          <div className="flex justify-between items-center mb-6 pb-4 border-b border-white/[0.04]">
            <div className="flex items-center gap-3">
              <div className="p-2.5 bg-rose-500/10 text-rose-400 rounded-xl border border-rose-500/20 animate-pulse"><Shield size={18} /></div>
              <h2 className="text-base font-bold uppercase tracking-wide text-white">{selectedCam ? `Active Focal Target: ${selectedCam.name}` : "Neural Surveillance Matrix Hub"}</h2>
            </div>
            <div className="flex items-center gap-3">
              {selectedCam && <button onClick={() => setSelectedCam(null)} className="text-[10px] font-bold uppercase text-emerald-400 border border-emerald-500/20 bg-emerald-500/5 px-3 py-1.5 rounded-xl hover:bg-emerald-500/10 transition-all tracking-wider">Exit Focal Frame</button>}
              <button title="Close Grid" onClick={() => { setIsFullscreenGrid(false); setIsSirenActive(false); }} className="p-2 bg-white/5 hover:bg-white/10 rounded-full text-slate-400 hover:text-white transition-all shadow-md"><X size={18} /></button>
            </div>
          </div>
          <div className="flex-1 min-h-0">
            {selectedCam ? (
              <div className="w-full h-full bg-black rounded-2xl border border-emerald-500/20 overflow-hidden relative shadow-2xl">
                <img src="http://localhost:8001/video_feed" className="w-full h-full object-cover" alt="Maximized Stream" />
              </div>
            ) : (
              <div className={`grid gap-4 h-full ${cameras.length <= 4 ? 'grid-cols-2' : 'grid-cols-3'}`}>
                {cameras.map(cam => (
                  <button key={cam.id} onClick={() => setSelectedCam(cam)} className="relative rounded-2xl border border-white/[0.04] overflow-hidden group shadow-2xl bg-black text-left transition-all hover:border-emerald-500/30">
                    <img src="http://localhost:8001/video_feed" className="w-full h-full object-cover opacity-50 group-hover:opacity-100 transition-all duration-500" alt="Surveillance Array" />
                    <div className="absolute bottom-4 left-4 px-3 py-1.5 bg-[#0E131F]/80 backdrop-blur-md rounded-xl text-[9px] font-bold uppercase border border-white/[0.04] text-white font-mono tracking-wider shadow-md">{cam.name}</div>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {showModal && (
        <div className="fixed inset-0 bg-black/85 backdrop-blur-sm z-[60] flex items-center justify-center p-6 text-slate-200 animate-in fade-in duration-200">
          <div className="bg-[#0E131F] border border-white/[0.04] rounded-2xl p-8 w-full max-w-sm shadow-2xl relative">
            <button title="Close Registration" onClick={() => setShowModal(false)} className="absolute top-6 right-6 text-slate-500 hover:text-white transition-colors"><X size={18} /></button>
            <div className="flex items-center gap-2.5 mb-6 text-white border-b border-white/[0.04] pb-4"><Video className="w-4 h-4 text-emerald-400" /><h2 className="text-xs font-bold uppercase tracking-widest">Register New Smartpole</h2></div>
            <div className="space-y-4">
              <input title="Node Label" id="cam-name" className="w-full bg-black/40 border border-white/[0.04] rounded-xl p-3.5 text-[11px] text-white outline-none focus:border-emerald-500 font-mono" placeholder="Node Terminal Identifier (e.g., Sector C)" />
              <input title="Network RTSP Link" id="cam-url" className="w-full bg-black/40 border border-white/[0.04] rounded-xl p-3.5 text-[11px] text-white outline-none focus:border-emerald-500 font-mono" placeholder="Network Path (rtsp://...)" />
              <button onClick={() => {
                  const n = (document.getElementById('cam-name') as HTMLInputElement).value;
                  const u = (document.getElementById('cam-url') as HTMLInputElement).value;
                  if(n && u) handleUpsertNode(n, u);
              }} className="w-full bg-emerald-500 hover:bg-emerald-400 text-black py-3.5 rounded-xl text-xs font-bold uppercase transition-all tracking-wider mt-2 shadow-md">Establish Secure Link</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function NavItem({ icon, label, badge, active, onClick }: any) {
  return (
    <button onClick={onClick} className={`w-full flex items-center justify-between px-4 py-3 rounded-xl transition-all duration-200 group relative text-left ${active ? 'bg-gradient-to-r from-emerald-500/[0.12] to-transparent text-emerald-400 border border-emerald-500/20 shadow-inner' : 'text-slate-400 hover:bg-white/[0.02] hover:text-slate-200 border border-transparent'}`}>
      {active && <div className="absolute left-0 top-3 bottom-3 w-1 rounded-r bg-emerald-500 shadow-[0_0_8px_#10b981]" />}
      <div className="flex items-center gap-3"><span>{icon}</span><span className="text-[10px] font-bold uppercase tracking-wider">{label}</span></div>
      {badge > 0 && <span className={`text-[9px] font-bold px-2 py-0.5 rounded-full ${active ? 'bg-emerald-500/20 text-emerald-300' : 'bg-rose-500/10 text-rose-400 border border-rose-500/10'}`}>{badge}</span>}
    </button>
  );
}

function HeatCard({ label, temp, icon }: any) {
  return (
    <div className="bg-[#0E131F]/40 border border-white/[0.04] rounded-xl p-5 shadow-xl flex items-center gap-3.5">
      <div className="p-2 rounded-lg bg-white/5 text-slate-400 border border-white/[0.03]">{icon}</div>
      <div className="flex flex-col min-w-0"><span className="text-[8px] font-black uppercase text-slate-500 tracking-wider truncate">{label}</span><span className="text-lg font-mono font-bold text-slate-100 mt-0.5">{temp}°C</span></div>
    </div>
  );
}

function ViolenceCard({ alert, onConfirm, onDismiss }: any) {
  return (
    <div className="p-5 bg-rose-500/[0.01] border border-rose-500/10 rounded-xl hover:border-rose-500/30 transition-all duration-300 shadow-md relative overflow-hidden group animate-in slide-in-from-right-4">
      <div className="absolute -top-2 -right-2 p-2 opacity-5 group-hover:opacity-15 transition-opacity pointer-events-none"><ShieldAlert size={64} className="text-rose-500" /></div>
      <div className="flex items-center justify-between mb-1"><span className="text-[9px] font-black uppercase text-rose-400/90 tracking-widest">Neural Flag Threat</span><span className="text-[9px] font-mono font-bold text-slate-500 group-hover:text-slate-400">{alert.timestamp}</span></div>
      <h4 className="text-sm font-black uppercase text-white tracking-wide mb-3">{alert.type}</h4>
      <div className="space-y-1.5 mb-4 text-[10px] text-slate-400">
        <div className="flex items-center gap-1.5 font-medium"><MapPin size={11} className="text-emerald-400" /> <span>{alert.location}</span></div>
        <div className="text-[9px] font-bold font-mono text-slate-500">Confidence Match: <span className="text-slate-400">{(alert.confidence * 100).toFixed(1)}%</span></div>
      </div>
      <div className="grid grid-cols-2 gap-2 relative z-10">
        <button onClick={() => onConfirm(alert.id)} className="bg-emerald-500 hover:bg-emerald-400 text-black text-[9px] font-bold uppercase py-2.5 rounded-lg active:scale-95 transition-all shadow-md font-black tracking-wider">Verify</button>
        <button onClick={() => onDismiss(alert.id)} className="border border-white/[0.04] text-slate-400 text-[9px] font-bold uppercase py-2.5 rounded-lg hover:bg-white/5 transition-all tracking-wider">Ignore</button>
      </div>
    </div>
  );
}