"use client";

import CrimeReportsView from './components/CrimeReportsView';
import HistoryView from './components/dashboard/HistoryView';
import RecordsView from './components/RecordsView';
import React, { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { 
  Shield, AlertOctagon, Activity, Video, Cpu, Trash2, MapPin, 
  ShieldAlert, Maximize2, X, BarChart3, Sun, 
  BatteryMedium, Thermometer, Zap, LogOut, CheckCircle2, AlertCircle, Plus, Film
} from 'lucide-react';

// --- TYPES ---
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

// --- MAIN DASHBOARD ---
export default function EcoVisionSentinel() {
  const [currentUser, setCurrentUser] = useState<any>(null);
  const [activeTab, setActiveTab] = useState('dashboard');
  const [selectedCam, setSelectedCam] = useState<Camera | null>(null);
  const [isFullscreenGrid, setIsFullscreenGrid] = useState(false);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [cameras, setCameras] = useState<Camera[]>([
    { id: '1', name: 'Main Entrance Hub', url: 'rtsp://ecovision:REDACTED_ROTATE_THIS_PASSWORD@192.168.254.106:554/stream1', status: 'online' },
    { id: '2', name: 'Sector B Gate', url: 'rtsp://192.168.1.15/stream', status: 'online' },
  ]);
  const [isSirenActive, setIsSirenActive] = useState(false);
  const [showModal, setShowModal] = useState(false);
  const [time, setTime] = useState("");
  const [sqlReportCount, setSqlReportCount] = useState(0);
  const router = useRouter();
  
  const [telemetry, setTelemetry] = useState({ 
    battery: 88, 
    solarV: 14.4, 
    tempCPU: 42, 
    tempESP: 38, 
    tempNeural: 51, 
    load: 12.4 
  });

  // --- AUTH GUARD ---
  useEffect(() => {
    const savedUser = localStorage.getItem('ecoUser');
    if (!savedUser) {
      router.push('/loginpage/login');
    } else {
      setCurrentUser(JSON.parse(savedUser));
    }
  }, [router]);

  // --- SYSTEM CLOCK ---
  useEffect(() => {
    setTime(new Date().toLocaleTimeString());
    const t = setInterval(() => setTime(new Date().toLocaleTimeString()), 1000);
    return () => clearInterval(t);
  }, []);

  // --- PERSISTENT COUNTER STATS ---
  const fetchStats = async () => {
    try {
      const res = await fetch("http://localhost:8000/api/incidents");
      if (!res.ok) return;
      const data = await res.json();
      setSqlReportCount(data.length);
    } catch (e) { 
      console.error("SQL Counter fetch failure:", e); 
    }
  };

  // --- PERSISTENT SEEDING DATA PARSER LINK ---
  const fetchActiveAlertCache = async () => {
    try {
      const res = await fetch("http://localhost:8000/api/incidents");
      if (res.ok) {
        const data = await res.json();
        const activeDetections = data.filter((inc: any) => inc.status === 'Active');
        
        const mappedAlerts = activeDetections.map((inc: any) => {
          // FIXED: Resolved 'None' reference compilation error with clean null/undefined evaluation safety checks
          return {
            id: inc.id,
            type: inc.type,
            severity: 'CRITICAL' as const,
            location: inc.locationName,
            area: 'Cogon Sector',
            timestamp: inc.militaryTime,
            confidence: inc.confidence !== null && inc.confidence !== undefined ? inc.confidence : 0.925,
            status: 'pending' as const,
            cameraLinkId: inc.locationName.includes("Entrance") ? "1" : "2"
          };
        });
        setAlerts(mappedAlerts);
      }
    } catch (err) {
      console.error("Failed to seed card collection mapping indexes on boot:", err);
    }
  };

  useEffect(() => {
    if (currentUser) {
      fetchStats();
      fetchActiveAlertCache();
    }
  }, [currentUser]);

  // --- WEBSOCKET BRIDGE ---
  useEffect(() => {
    const socket = new WebSocket('ws://localhost:8000/ws');
    socket.onmessage = (e) => {
      const data = JSON.parse(e.data);
      if (data.status === "CRITICAL") {
        setAlerts(prev => {
          if (prev.some(a => a.id === data.id)) return prev;
          return [{
            id: data.id || Math.random().toString(36).substr(2, 9),
            type: data.type || 'VIOLENCE',
            severity: 'CRITICAL',
            location: data.location || 'Cogon Core Smartpole',
            area: 'Cogon Sector',
            timestamp: new Date().toLocaleTimeString(),
            confidence: data.conf || 0.94,
            status: 'pending',
            cameraLinkId: data.cameraLinkId || "1"
          }, ...prev];
        });
        setSqlReportCount(prev => prev + 1);
      }
    };
    return () => socket.close();
  }, []);

  const handleUpsertNode = (name: string, url: string) => {
    const newNode: Camera = { id: Date.now().toString(), name, url, status: 'online' };
    setCameras(prev => [...prev, newNode]);
    setShowModal(false);
  };

  const deleteCam = (id: string) => {
    setCameras(prev => prev.filter(c => c.id !== id));
    if (selectedCam?.id === id) setSelectedCam(null);
  };

  const handleVerifyCrime = async (id: string) => {
    const alertTarget = alerts.find(a => a.id === id);

    setAlerts(prev => prev.filter(a => a.id !== id));
    setIsSirenActive(true);
    setActiveTab('dashboard');

    if (alertTarget && alertTarget.cameraLinkId) {
      const matchCam = cameras.find(c => c.id === alertTarget.cameraLinkId);
      if (matchCam) {
        setSelectedCam(matchCam);
        setIsFullscreenGrid(true); 
      }
    } else if (cameras.length > 0) {
      setSelectedCam(cameras[0]);
      setIsFullscreenGrid(true);
    }

    try {
      fetch("http://localhost:8000/siren/activate", { method: "POST" }).catch(e => console.error("Siren request dropped:", e));
      
      await fetch(`http://localhost:8000/api/incidents/${id}/status`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: "Confirmed" })
      });
      fetchStats();
    } catch (e) {
      console.error("Failed to route verification state changes to backend server", e);
    }
  };

  const handleDismissCrime = async (id: string) => {
    setAlerts(prev => prev.filter(a => a.id !== id));

    try {
      await fetch(`http://localhost:8000/api/incidents/${id}/status`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: "Dismissed" })
      });
      fetchStats();
    } catch (e) {
      console.error("Failed to post exclusion parameters to backend server databases", e);
    }
  };

  const handleLogout = () => {
    localStorage.removeItem('ecoUser');
    router.push('/loginpage/login');
  };

  if (!currentUser) return <div className="min-h-screen bg-[#0a0c10]" />;

  const isMapView = activeTab === 'crime-reports';

  return (
    <div className="flex h-screen w-full bg-[#0a0c10] text-slate-200 p-4 gap-4 overflow-hidden font-sans relative">
      <style>{`
        :root { --battery-width: ${telemetry.battery}%; }
        .battery-fill { width: var(--battery-width); }
      `}</style>
      
      <div className="hidden" data-battery={telemetry.battery} />

      {/* 🟢 SIDEBAR */}
      <aside className="w-64 bg-[#11141b] border border-white/5 rounded-3xl flex flex-col p-6 shrink-0 shadow-2xl overflow-y-auto custom-scrollbar">
        <div className="flex items-center gap-3 mb-8 pb-6 border-b border-white/5">
          <div className="p-2 bg-emerald-500 rounded-xl">
            <Shield className="w-5 h-5 text-[#0a0c10]" />
          </div>
          <h1 className="text-sm font-semibold tracking-widest uppercase text-white">
            EcoVision <span className="text-emerald-500 font-mono text-[10px]">v15.0</span>
          </h1>
        </div>

        <nav className="space-y-1.5 flex-1">
          {currentUser.role === 'POLICE' && (
            <>
              <NavItem label="Monitor" icon={<Activity size={18}/>} active={activeTab === 'dashboard'} onClick={() => setActiveTab('dashboard')} />
              <NavItem label="Map" icon={<MapPin size={18}/>} active={isMapView} onClick={() => setActiveTab('crime-reports')} badge={sqlReportCount} />
              <NavItem label="Records" icon={<Film size={18}/>} active={activeTab === 'records'} onClick={() => setActiveTab('records')} />
              <NavItem label="Crime History" icon={<AlertOctagon size={18}/>} active={activeTab === 'alerts'} onClick={() => setActiveTab('alerts')} badge={alerts.filter(a => a.status === 'pending').length} />
            </>
          )}

          {currentUser.role === 'BARANGAY' && (
             <>
               <NavItem label="Monitor" icon={<Activity size={18}/>} active={activeTab === 'dashboard'} onClick={() => setActiveTab('dashboard')} />
               <NavItem label="Add Cameras" icon={<Video size={18}/>} active={activeTab === 'cameras'} onClick={() => setActiveTab('cameras')} badge={cameras.length} />
               <NavItem label="Hardware Status" icon={<Zap size={18}/>} active={activeTab === 'health'} onClick={() => setActiveTab('health')} />
             </>
          )}
        </nav>
        
        <div className="mt-auto pt-4">
          <button title="Sign Out" onClick={handleLogout} className="w-full flex items-center gap-3 px-4 py-3 text-slate-500 hover:text-red-500 text-[10px] uppercase font-bold transition-all border border-white/5 rounded-xl"><LogOut size={16}/> Terminate</button>
        </div>
      </aside>

      {/* 🔴 MAIN STAGE */}
      <main className="flex-1 flex flex-col gap-4 overflow-hidden">
        <header className="h-16 bg-[#11141b] border border-white/5 rounded-3xl flex items-center justify-between px-8 shadow-xl">
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2 text-[10px] font-mono text-emerald-500 uppercase tracking-tight">
              <div className="w-1.5 h-1.5 bg-emerald-500 rounded-full animate-pulse shadow-[0_0_5px_#10b981]" />
              {currentUser.role === 'POLICE' ? 'Police Account' : 'Barangay Account'}
            </div>
            <div className="h-4 w-px bg-white/10" />
            <h2 className="text-xs font-medium tracking-[0.1em] uppercase text-slate-400">Ormoc Sector Command</h2>
          </div>
          <div className="text-md font-mono font-medium tracking-tight tabular-nums text-white bg-black/40 px-4 py-1 rounded-xl border border-white/5">{time}</div>
        </header>

        <div className="flex-1 overflow-hidden grid grid-cols-12 gap-4">
          <div className={`${activeTab === 'dashboard' ? 'col-span-8' : 'col-span-12'} overflow-y-auto custom-scrollbar space-y-4 pr-1 transition-all duration-500`}>
            
            {activeTab === 'dashboard' && (
              <div className="space-y-4 flex flex-col h-full">
                <div className="flex-1 bg-[#11141b] border border-white/5 rounded-[2rem] p-6 shadow-2xl flex flex-col relative overflow-hidden">
                  <div className="flex justify-between items-center mb-6 px-2">
                    <div className="flex items-center gap-3 text-[10px] font-bold uppercase tracking-[0.2em] text-slate-300">
                      <Activity size={16} className="text-emerald-500" />
                      {selectedCam ? `Target: ${selectedCam.name}` : "Global Grid Feed"}
                    </div>
                    <div className="flex items-center gap-2">
                      <button title="Maximize Display Grid" aria-label="Maximize Display Grid" onClick={() => setIsFullscreenGrid(true)} className="p-2 hover:bg-emerald-500/10 rounded-lg text-emerald-500 border border-white/5 shadow-sm"><Maximize2 size={16} /></button>
                      {selectedCam && <button title="Exit Focal View" aria-label="Exit Focal View" onClick={() => setSelectedCam(null)} className="text-[9px] font-bold uppercase text-emerald-500 px-3 py-1.5 rounded-lg border border-emerald-500/20 hover:bg-emerald-500/10 transition-all">Exit Focus</button>}
                    </div>
                  </div>
                  <div className="flex-1 min-h-0">
                    {selectedCam ? (
                      <div className={`w-full h-full bg-black rounded-2xl overflow-hidden shadow-inner border-2 ${isSirenActive ? 'border-red-600 animate-pulse shadow-[0_0_20px_rgba(220,38,38,0.4)]' : 'border-emerald-500/10'}`}>
                        <img src="http://localhost:8001/video_feed" className="w-full h-full object-cover" alt="Focused" />
                      </div>
                    ) : (
                      <div className="grid grid-cols-2 gap-4 h-full">
                        {cameras.map(cam => {
                          const hasUnverifiedThreat = alerts.some(a => a.cameraLinkId === cam.id && a.status === 'pending');
                          return (
                            <button key={cam.id} title={`Focus ${cam.name}`} aria-label={`Focus ${cam.name}`} onClick={() => setSelectedCam(cam)} className={`bg-black rounded-2xl relative group transition-all overflow-hidden shadow-md border-2 ${hasUnverifiedThreat ? 'border-red-600 animate-pulse shadow-[0_0_15px_rgba(220,38,38,0.5)]' : 'border-white/5 hover:border-emerald-500/40'}`}>
                              <img src="http://localhost:8001/video_feed" className="w-full h-full object-cover opacity-60 group-hover:opacity-100 transition-opacity duration-500" alt="stream" />
                              <span className="absolute bottom-4 left-4 text-[9px] font-bold uppercase text-white tracking-widest bg-black/40 px-2 py-1 rounded border border-white/5 font-mono">{cam.name}</span>
                            </button>
                          );
                        })}
                      </div>
                    )}
                  </div>
                </div>
              </div>
            )}

            {activeTab === 'crime-reports' && (
              <div className="h-full">
                <CrimeReportsView onUpdate={fetchStats} />
              </div>
            )}

            {activeTab === 'alerts' && <HistoryView />}

            {activeTab === 'records' && <RecordsView />}

            {activeTab === 'health' && (
              <div className="space-y-6">
                 <div className="grid grid-cols-2 gap-4">
                  <div className="bg-[#11141b] border border-white/5 rounded-3xl p-8 shadow-xl">
                    <div className="flex justify-between items-start">
                      <div>
                        <span className="text-[10px] font-bold text-slate-500 uppercase tracking-widest block mb-1">Node Power Cycle</span>
                        <span className="font-mono text-4xl font-semibold text-white tabular-nums">{telemetry.battery}%</span>
                      </div>
                      <div className="p-3 bg-emerald-500/10 text-emerald-500 rounded-xl"><BatteryMedium size={24} /></div>
                    </div>
                    <div className="w-full bg-white/5 h-2 rounded-full overflow-hidden mt-6">
                      <div className="bg-emerald-500 h-full shadow-[0_0_15px_#10b981] transition-all duration-700 battery-fill" />
                    </div>
                  </div>
                  <div className="bg-[#11141b] border border-white/5 rounded-3xl p-8 shadow-xl flex justify-between items-start">
                    <div>
                      <span className="text-[10px] font-bold text-slate-500 uppercase tracking-widest block mb-1">Solar Panel Array Input</span>
                      <span className="font-mono text-4xl font-semibold text-white tabular-nums">{telemetry.solarV}V</span>
                      <p className="text-[9px] text-slate-500 uppercase font-mono italic mt-4">Smartpole_01 Core // Array Nominal</p>
                    </div>
                    <div className="p-3 bg-orange-500/10 text-orange-400 rounded-xl"><Sun size={24} /></div>
                  </div>
                </div>
                <div className="grid grid-cols-3 gap-4">
                  <HeatCard label="Neural Engine Processing core" temp={telemetry.tempNeural} icon={<Cpu size={20}/>} />
                  <HeatCard label="Main Linux CPU Host" temp={telemetry.tempCPU} icon={<Thermometer size={20}/>} />
                  <HeatCard label="Uplink MCU (LAFVIN Node)" temp={telemetry.tempESP} icon={<Zap size={20}/>} />
                </div>
              </div>
            )}

            {activeTab === 'cameras' && (
              <div className="grid grid-cols-2 gap-4">
                {cameras.map(cam => (
                  <div key={cam.id} className="bg-[#11141b] border border-white/5 rounded-3xl p-6 shadow-xl relative group hover:border-emerald-500/20 transition-all">
                    <div className="flex justify-between items-start mb-6">
                      <div className="p-3 rounded-xl bg-emerald-500/10 text-emerald-500"><Video size={22}/></div>
                      <button title="Dismantle Camera Node" aria-label="Dismantle Camera Node" onClick={(e) => { e.stopPropagation(); deleteCam(cam.id); }} className="p-2 text-slate-500 hover:text-red-500 transition-colors"><Trash2 size={16}/></button>
                    </div>
                    <h4 className="text-sm font-semibold uppercase text-white tracking-tight">{cam.name}</h4>
                    <p className="text-[10px] font-mono text-slate-500 truncate mb-6 mt-2 bg-black/20 p-2 rounded">{cam.url}</p>
                  </div>
                ))}
                <button title="Add New Node" aria-label="Add New Node" onClick={() => setShowModal(true)} className="border-2 border-dashed border-white/5 rounded-3xl flex flex-col items-center justify-center p-12 text-slate-600 hover:text-emerald-500 transition-all group">
                  <Plus size={32} className="mb-2 opacity-20 group-hover:opacity-100" />
                  <span className="text-[10px] font-bold uppercase tracking-widest">Register New Smartpole Node</span>
                </button>
              </div>
            )}
          </div>

          {/* 🏹 CRIME LOG PANEL */}
          {activeTab === 'dashboard' && (
            <div className="col-span-4 bg-[#11141b] border border-white/5 rounded-[2.5rem] flex flex-col overflow-hidden shadow-2xl animate-in slide-in-from-right duration-500">
              <h3 className="text-[11px] font-bold uppercase tracking-[0.2em] text-slate-500 p-7 border-b border-white/5 flex items-center gap-3">
                <ShieldAlert size={14} className="text-red-500" /> Crime Log
              </h3>
              <div className="flex-1 overflow-y-auto p-4 space-y-4 custom-scrollbar bg-black/5">
                {alerts.filter(a => a.status === 'pending').map(alert => (
                  <ViolenceCard 
                    key={alert.id} 
                    alert={alert} 
                    onConfirm={handleVerifyCrime} 
                    onDismiss={handleDismissCrime} 
                  />
                ))}
                
                {alerts.filter(a => a.status === 'pending').length === 0 && (
                  <div className="h-full flex flex-col items-center justify-center opacity-10">
                    <svg xmlns="http://www.w3.org/2000/svg" width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="lucide lucide-activity mb-4 text-emerald-500 animate-pulse" aria-hidden="true">
                      <path d="M22 12h-2.48a2 2 0 0 0-1.93 1.46l-2.35 8.36a.25.25 0 0 1-.48 0L9.24 2.18a.25.25 0 0 0-.48 0l-2.35 8.36A2 2 0 0 1 4.49 12H2" />
                    </svg>
                    <span className="text-[9px] font-bold uppercase tracking-widest">Awaiting Analysis...</span>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </main>

      {/* FULLSCREEN GRID MODAL */}
      {isFullscreenGrid && (
        <div className="fixed inset-0 z-[100] bg-black/98 backdrop-blur-2xl p-8 flex flex-col animate-in fade-in duration-500">
          <div className="flex justify-between items-center mb-8 pb-6 border-b border-white/5">
            <div className="flex items-center gap-4">
              <div className="p-3 bg-red-600 rounded-xl animate-pulse shadow-[0_0_15px_rgba(220,38,38,0.3)]"><Shield size={24} className="text-white" /></div>
              <h2 className="text-xl font-semibold uppercase tracking-tight text-white">
                {selectedCam ? `Focal Max Grid: ${selectedCam.name}` : "Neural Surveillance Command"}
              </h2>
            </div>
            <div className="flex items-center gap-4">
              {selectedCam && (
                <button title="Return to Multi-Grid" onClick={() => setSelectedCam(null)} className="text-[10px] font-bold uppercase text-emerald-400 border border-emerald-400/20 px-4 py-2 rounded-xl bg-emerald-400/5 hover:bg-emerald-400/10 transition-all tracking-wider">
                  Exit Focus
                </button>
              )}
              <button title="Close Grid" aria-label="Close Grid" onClick={() => { setIsFullscreenGrid(false); setIsSirenActive(false); }} className="p-3 bg-white/5 hover:bg-white/10 rounded-full text-slate-400 hover:text-white transition-all shadow-lg"><X size={28} /></button>
            </div>
          </div>
          
          <div className="flex-1 min-h-0">
            {selectedCam ? (
              <div className="w-full h-full bg-black rounded-[2rem] border border-emerald-500/20 overflow-hidden relative shadow-2xl">
                <img src="http://localhost:8001/video_feed" className="w-full h-full object-cover" alt="Fullscreen Focus Node" />
                <div className="absolute bottom-6 left-6 px-4 py-2 bg-black/70 border border-white/5 text-[10px] uppercase font-bold text-emerald-400 tracking-widest rounded-xl font-mono">
                  Active Focal Lock // Stream Connected
                </div>
              </div>
            ) : (
              <div className={`grid gap-4 h-full ${cameras.length <= 4 ? 'grid-cols-2' : 'grid-cols-3'}`}>
                {cameras.map(cam => (
                  <button key={cam.id} title={`Maximize Node Feed: ${cam.name}`} onClick={() => setSelectedCam(cam)} className="relative rounded-[2rem] border border-white/10 overflow-hidden group shadow-2xl bg-[#0d0f14] hover:border-emerald-500/40 text-left transition-all">
                    <img src="http://localhost:8001/video_feed" className="w-full h-full object-cover grayscale-[0.4] group-hover:grayscale-0 transition-all duration-700 opacity-60 group-hover:opacity-100" alt="visual" />
                    <div className="absolute bottom-6 left-6 px-4 py-2 bg-black/60 backdrop-blur-md rounded-xl text-[9px] font-bold uppercase border border-white/5 shadow-md tabular-nums text-white font-mono tracking-wider">{cam.name}</div>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {showModal && (
        <div className="fixed inset-0 bg-black/90 backdrop-blur-md z-[60] flex items-center justify-center p-6 text-slate-200">
          <div className="bg-[#1a1e26] border border-white/5 rounded-[3rem] p-10 w-full max-w-md shadow-2xl">
            <div className="flex justify-between items-center mb-10 text-white">
              <h2 className="text-sm font-bold uppercase tracking-widest">Initialize Node</h2>
              <button title="Close Registration Modal" aria-label="Close Registration Modal" onClick={() => setShowModal(false)}><X /></button>
            </div>
            <div className="space-y-6">
              <input title="Node Terminal Descriptor Label" id="cam-name" className="w-full bg-black/40 border border-white/5 rounded-2xl p-4 text-[12px] text-white outline-none focus:border-emerald-500 font-mono" placeholder="Node Descriptor" />
              <input title="Network Transport URL (RTSP)" id="cam-url" className="w-full bg-black/40 border border-white/5 rounded-2xl p-4 text-[12px] text-white outline-none focus:border-emerald-500 font-mono" placeholder="Network Path (RTSP)" />
              <button title="Submit and Secure Uplink" aria-label="Submit and Secure Uplink" onClick={() => {
                  const n = (document.getElementById('cam-name') as HTMLInputElement).value;
                  const u = (document.getElementById('cam-url') as HTMLInputElement).value;
                  if(n && u) handleUpsertNode(n, u);
                }} className="w-full bg-emerald-500 text-black py-4 rounded-2xl font-bold uppercase active:scale-95 transition-all shadow-lg hover:bg-emerald-400">Establish Link</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// --- SUB-COMPONENTS ---
function NavItem({ icon, label, badge, active, onClick }: any) {
  return (
    <button title={label} onClick={onClick} className={`w-full flex items-center justify-between px-5 py-3.5 rounded-2xl transition-all group ${active ? 'bg-emerald-500 text-black shadow-lg' : 'text-slate-500 hover:bg-white/5 hover:text-white'}`}>
      <div className="flex items-center gap-3">{icon}<span className="text-[10px] font-bold uppercase tracking-[0.1em]">{label}</span></div>
      {badge > 0 && <span className={`text-[9px] font-bold px-2 py-0.5 rounded-full ${active ? 'bg-black text-white' : 'bg-red-500 text-white'}`}>{badge}</span>}
    </button>
  );
}

function StatCard({ label, val, color = "text-emerald-500", subLabel }: any) {
  return (
    <div className="bg-[#11141b] border border-white/5 rounded-2xl p-5 shadow-xl transition-all"><span className="text-[9px] font-bold text-slate-500 uppercase tracking-[0.15em]">{label}</span><div className="flex items-baseline gap-2"><span className={`text-2xl font-semibold tracking-tight ${color}`}>{val}</span><span className="text-[9px] text-slate-600 uppercase font-bold">{subLabel}</span></div></div>
  );
}

function HeatCard({ label, temp, icon }: any) {
  return (
    <div className="bg-[#11141b] border border-white/5 rounded-2xl p-6 shadow-xl flex items-center gap-4"><div className="p-2 rounded-lg bg-white/5 text-slate-400">{icon}</div><div className="flex flex-col"><span className="text-[8px] font-bold uppercase text-slate-500">{label}</span><span className="text-xl font-mono font-semibold text-white">{temp}°C</span></div></div>
  );
}

function ViolenceCard({ alert, onConfirm, onDismiss }: any) {
  return (
    <div className="p-6 bg-red-500/[0.02] border border-red-500/10 rounded-[1.8rem] hover:border-red-500/30 shadow-lg relative overflow-hidden group animate-in slide-in-from-right-4 duration-300">
      <div className="absolute top-0 right-0 p-4 opacity-5 group-hover:opacity-20 transition-opacity"><ShieldAlert size={60} className="text-red-500" /></div>
      <span className="text-[9px] font-bold uppercase text-red-500/80 tracking-widest">Alert Triggered</span>
      <h4 className="text-lg font-semibold uppercase text-white mb-5 tracking-tight">{alert.type}</h4>
      <div className="space-y-4 mb-7 text-[10px] text-slate-400">
        <div className="flex items-center gap-2 text-slate-300 font-sans"><MapPin size={12} className="text-emerald-500" /> {alert.location}</div>
        <div className="flex justify-between font-mono italic opacity-60"><span>{alert.timestamp}</span><span>Confidence: {(alert.confidence * 100).toFixed(1)}%</span></div>
      </div>
      <div className="grid grid-cols-2 gap-3 relative z-10">
        <button title="Verify Crime" aria-label="Verify Crime" onClick={() => onConfirm(alert.id)} className="bg-emerald-500 text-black text-[9px] font-bold uppercase py-3 rounded-xl active:scale-95 transition-all shadow-md font-bold">Verify</button>
        <button title="Ignore Node" aria-label="Ignore Node" onClick={() => onDismiss(alert.id)} className="border border-white/5 text-slate-400 text-[9px] font-bold uppercase py-3 rounded-xl hover:bg-white/5 transition-all">Ignore</button>
      </div>
    </div>
  );
}