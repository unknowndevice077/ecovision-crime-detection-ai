"use client";

import HistoryView from './components/dashboard/HistoryView';
import CrimeReportsView from './components/CrimeReportsView';
import React, { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { 
  Shield, AlertOctagon, Activity, Video, Cpu, Network, Plus, 
  Trash2, MapPin, ShieldAlert, Maximize2, X, ChevronRight, 
  BellRing, BarChart3, Sun, BatteryMedium, Thermometer, Zap, LogOut 
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

  useEffect(() => {
    const savedUser = localStorage.getItem('ecoUser');
    if (!savedUser) {
      router.push('/loginpage/login');
    } else {
      setCurrentUser(JSON.parse(savedUser));
    }
  }, [router]);

  useEffect(() => {
    setTime(new Date().toLocaleTimeString());
    const t = setInterval(() => setTime(new Date().toLocaleTimeString()), 1000);
    return () => clearInterval(t);
  }, []);

  const fetchStats = async () => {
    try {
      const res = await fetch("http://localhost:8000/api/incidents");
      const data = await res.json();
      setSqlReportCount(data.length);
    } catch (e) { console.error("SQL Sync Failed"); }
  };

  useEffect(() => {
    if (currentUser) fetchStats();
  }, [currentUser]);

  useEffect(() => {
    const socket = new WebSocket('ws://localhost:8000/ws');
    socket.onmessage = (e) => {
      const data = JSON.parse(e.data);
      if (data.status === "CRITICAL") {
        setAlerts(prev => [{
          id: Math.random().toString(36).substr(2, 9),
          type: data.type || 'VIOLENCE',
          severity: 'CRITICAL',
          location: 'District 01',
          area: 'Main Sector',
          timestamp: new Date().toLocaleTimeString(),
          confidence: data.conf || 0.94,
          status: 'pending'
        }, ...prev]);
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

  const handleLogout = () => {
    localStorage.removeItem('ecoUser');
    router.push('/loginpage/login');
  };

  if (!currentUser) return <div className="min-h-screen bg-[#0a0c10]" />;

  const isMapView = activeTab === 'crime-reports';

  return (
    <div className="flex h-screen w-full bg-[#0a0c10] text-slate-200 p-4 gap-4 overflow-hidden font-sans relative">
      <style>{`
        :root { --battery-level: ${telemetry.battery}%; }
        .battery-fill { width: var(--battery-level); }
      `}</style>

      <aside className="w-64 bg-[#11141b] border border-white/5 rounded-3xl flex flex-col p-6 shrink-0 shadow-2xl overflow-y-auto custom-scrollbar">
        <div className="flex items-center gap-3 mb-8 pb-6 border-b border-white/5">
          <div className="p-2 bg-emerald-500 rounded-xl">
            <Shield className="w-5 h-5 text-[#0a0c10]" />
          </div>
          <h1 className="text-sm font-semibold tracking-widest uppercase text-white">
            EcoVision <span className="text-emerald-500 font-mono text-[10px]">v13.8</span>
          </h1>
        </div>

        <nav className="space-y-1.5 flex-1">
          {currentUser.role === 'POLICE' && (
            <>
              <NavItem label="Monitor" icon={<Activity size={18}/>} active={activeTab === 'dashboard'} onClick={() => setActiveTab('dashboard')} />
              <NavItem label="Add Cameras" icon={<Video size={18}/>} active={activeTab === 'cameras'} onClick={() => setActiveTab('cameras')} badge={cameras.length} />
              <NavItem label="Tactical Map" icon={<MapPin size={18}/>} active={isMapView} onClick={() => setActiveTab('crime-reports')} badge={sqlReportCount} />
            </>
          )}

          {currentUser.role === 'BARANGAY' && (
             <>
               <NavItem label="Monitor" icon={<Activity size={18}/>} active={activeTab === 'dashboard'} onClick={() => setActiveTab('dashboard')} />
               <NavItem label="System Health" icon={<Zap size={18}/>} active={activeTab === 'health'} onClick={() => setActiveTab('health')} />
             </>
          )}

          <NavItem label="History" icon={<AlertOctagon size={18}/>} active={activeTab === 'alerts'} onClick={() => setActiveTab('alerts')} badge={alerts.filter(a => a.status === 'pending').length} />
          <NavItem label="Analytics" icon={<BarChart3 size={18}/>} active={activeTab === 'analytics'} onClick={() => setActiveTab('analytics')} />
        </nav>

        <div className="my-6 space-y-3 pt-6 border-t border-white/5">
          <h3 className="px-2 text-[9px] font-bold text-slate-500 uppercase tracking-widest">Hardware Status</h3>
          <div className="bg-black/20 border border-white/5 rounded-2xl p-4 space-y-3 shadow-inner">
            <div className="flex justify-between items-center text-[10px]">
              <div className="flex items-center gap-2 text-slate-400 uppercase font-medium">
                <BatteryMedium size={14} className="text-emerald-500" />
                <span>Battery</span>
              </div>
              <span className="font-mono text-white tabular-nums">{telemetry.battery}%</span>
            </div>
            <div className="w-full bg-white/5 h-1.5 rounded-full overflow-hidden">
              <div className="bg-emerald-500 h-full shadow-[0_0_8px_#10b981] transition-all duration-700 battery-fill" />
            </div>
            <div className="flex justify-between items-center text-[10px]">
              <div className="flex items-center gap-2 text-slate-400 uppercase font-medium">
                <Sun size={14} className="text-orange-400" />
                <span>Solar</span>
              </div>
              <span className="font-mono text-white tabular-nums">{telemetry.solarV}V</span>
            </div>
          </div>
        </div>
        
        <div className="mt-auto pt-4 space-y-4">
          <div className="p-4 bg-[#1a1e26] rounded-2xl border border-white/5 shadow-lg">
            <div className="flex justify-between items-center mb-4 uppercase tracking-[0.15em] text-[9px] font-bold text-slate-500">
              Actuator Link
              <div className={`w-2 h-2 rounded-full ${isSirenActive ? 'bg-red-500 animate-pulse' : 'bg-emerald-500'}`} />
            </div>
            <div className="flex flex-col gap-2">
              <button title="Activate Actuator" onClick={() => { fetch("http://localhost:8000/siren/activate", { method: "POST" }); setIsSirenActive(true); }} className="w-full py-2.5 bg-slate-100 text-black text-[10px] font-bold uppercase rounded-xl hover:bg-white transition-all shadow-md active:scale-95">Trigger Panic</button>
              <button title="Reset Actuator" onClick={() => { fetch("http://localhost:8000/siren/reset", { method: "POST" }); setIsSirenActive(false); }} className="w-full py-2 bg-transparent border border-white/10 text-white text-[10px] font-bold uppercase rounded-xl hover:bg-white/5 transition-all">Siren Reset</button>
            </div>
          </div>
          <button title="Sign Out" onClick={handleLogout} className="w-full flex items-center gap-3 px-4 py-3 text-slate-500 hover:text-red-500 text-[10px] uppercase font-bold transition-all border border-white/5 rounded-xl"><LogOut size={16}/> Terminate</button>
        </div>
      </aside>

      <main className="flex-1 flex flex-col gap-4 overflow-hidden">
        <header className="h-16 bg-[#11141b] border border-white/5 rounded-3xl flex items-center justify-between px-8 shadow-xl">
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2 text-[10px] font-mono text-emerald-500 uppercase tracking-tight">
              <div className="w-1.5 h-1.5 bg-emerald-500 rounded-full animate-pulse shadow-[0_0_5px_#10b981]" />
              {currentUser.assignment} // Sync Nominal
            </div>
            <div className="h-4 w-px bg-white/10" />
            <h2 className="text-xs font-medium tracking-[0.1em] uppercase text-slate-400">Ormoc Sector Command</h2>
          </div>
          <div className="text-md font-mono font-medium tracking-tight tabular-nums text-white bg-black/40 px-4 py-1 rounded-xl border border-white/5">{time}</div>
        </header>

        <div className="flex-1 overflow-hidden grid grid-cols-12 gap-4">
          <div className={`${isMapView || activeTab === 'alerts' ? 'col-span-12' : 'col-span-8'} overflow-y-auto custom-scrollbar space-y-4 pr-1 transition-all duration-500`}>
            
            {activeTab === 'dashboard' && (
              <div className="space-y-4 flex flex-col h-full">
                <div className="grid grid-cols-3 gap-4">
                  <StatCard label="Detections (24h)" val="1,402" color="text-slate-200" subLabel="Total" />
                  <StatCard label="Processing Speed" val="58.2" color="text-emerald-400" subLabel="FPS" />
                  <StatCard label="Threat Level" val={alerts.filter(a => a.status === 'pending').length.toString()} color={alerts.filter(a => a.status === 'pending').length > 0 ? "text-red-500" : "text-slate-500"} subLabel={alerts.filter(a => a.status === 'pending').length > 0 ? "CRITICAL" : "NORMAL"} />
                </div>

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
                      <div className="w-full h-full bg-black rounded-2xl border border-emerald-500/10 overflow-hidden shadow-inner"><img src={`http://localhost:8000/video_feed/${selectedCam.id}`} className="w-full h-full object-cover" alt="Focused" /></div>
                    ) : (
                      <div className="grid grid-cols-2 gap-4 h-full">
                        {cameras.map(cam => (
                          <button key={cam.id} title={`Focus ${cam.name}`} aria-label={`Focus ${cam.name}`} onClick={() => setSelectedCam(cam)} className="bg-black rounded-2xl border border-white/5 relative group hover:border-emerald-500/40 transition-all overflow-hidden shadow-md">
                            <img src={`http://localhost:8000/video_feed/${cam.id}`} className="w-full h-full object-cover opacity-60 group-hover:opacity-100 transition-opacity duration-500" alt="stream" />
                            <span className="absolute bottom-4 left-4 text-[9px] font-bold uppercase text-white tracking-widest bg-black/40 px-2 py-1 rounded border border-white/5 font-mono">{cam.name}</span>
                          </button>
                        ))}
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

            {activeTab === 'health' && (
              <div className="space-y-6">
                 <div className="grid grid-cols-2 gap-4">
                  <div className="bg-[#11141b] border border-white/5 rounded-3xl p-8 shadow-xl">
                    <BatteryMedium size={28} className="text-emerald-500 mb-6"/>
                    <span className="font-mono text-3xl font-semibold text-white tabular-nums">{telemetry.battery}%</span>
                    <h4 className="text-xs font-bold uppercase text-slate-400 tracking-widest mb-2 mt-4">Storage Cycle</h4>
                    <div className="w-full bg-white/5 h-2 rounded-full overflow-hidden mt-6"><div className="bg-emerald-500 h-full battery-fill shadow-[0_0_15px_#10b981]" /></div>
                  </div>
                  <div className="bg-[#11141b] border border-white/5 rounded-3xl p-8 shadow-xl">
                    <Sun size={28} className="text-orange-400 mb-6"/>
                    <span className="font-mono text-3xl font-semibold text-white tabular-nums">{telemetry.solarV}V</span>
                    <h4 className="text-xs font-bold uppercase text-slate-400 tracking-widest mb-2 mt-4">Current Intake</h4>
                    <p className="text-[10px] text-slate-500 uppercase font-mono italic mt-6">Smartpole_01 // Array Nominal</p>
                  </div>
                </div>
                <div className="grid grid-cols-3 gap-4">
                  <HeatCard label="Neural Engine" temp={telemetry.tempNeural} icon={<Cpu size={20}/>} />
                  <HeatCard label="Main Processor" temp={telemetry.tempCPU} icon={<Thermometer size={20}/>} />
                  <HeatCard label="Uplink MCU" temp={telemetry.tempESP} icon={<Zap size={20}/>} />
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

            {activeTab === 'analytics' && <SystemFlowView />}
          </div>

          {activeTab !== 'crime-reports' && activeTab !== 'alerts' && (
            <div className="col-span-4 bg-[#11141b] border border-white/5 rounded-[2.5rem] flex flex-col overflow-hidden shadow-2xl animate-in slide-in-from-right duration-500">
              <h3 className="text-[11px] font-bold uppercase tracking-[0.2em] text-slate-500 p-7 border-b border-white/5 flex items-center gap-3"><ShieldAlert size={14} className="text-red-500" /> Neural Buffer</h3>
              <div className="flex-1 overflow-y-auto p-4 space-y-4 custom-scrollbar bg-black/5">
                {alerts.filter(a => a.status === 'pending').map(alert => (
                  <ViolenceCard key={alert.id} alert={alert} onConfirm={() => {}} onDismiss={() => {}} />
                ))}
              </div>
            </div>
          )}
        </div>
      </main>

      {isFullscreenGrid && (
        <div className="fixed inset-0 z-[100] bg-black/98 backdrop-blur-2xl p-8 flex flex-col animate-in fade-in duration-500">
          <div className="flex justify-between items-center mb-8 pb-6 border-b border-white/5">
            <div className="flex items-center gap-4">
              <div className="p-3 bg-red-600 rounded-xl animate-pulse shadow-[0_0_15px_rgba(220,38,38,0.3)]"><Shield size={24} className="text-white" /></div>
              <h2 className="text-xl font-semibold uppercase tracking-tight text-white">Neural Surveillance Command</h2>
            </div>
            <button title="Close Grid" aria-label="Close Grid" onClick={() => setIsFullscreenGrid(false)} className="p-3 bg-white/5 hover:bg-white/10 rounded-full text-slate-400 hover:text-white transition-all shadow-lg"><X size={28} /></button>
          </div>
          <div className={`grid gap-4 flex-1 ${cameras.length <= 4 ? 'grid-cols-2' : 'grid-cols-3'}`}>
            {cameras.map(cam => (
              <div key={cam.id} className="relative rounded-[2rem] border border-white/10 overflow-hidden group shadow-2xl bg-[#0d0f14]">
                <img src={`http://localhost:8000/video_feed/${cam.id}`} className="w-full h-full object-cover grayscale-[0.4] group-hover:grayscale-0 transition-all duration-700" alt="visual" />
                <div className="absolute top-6 left-6 px-4 py-2 bg-black/60 backdrop-blur-md rounded-xl text-[9px] font-bold uppercase border border-white/5 shadow-md tabular-nums">{cam.name}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {showModal && (
        <div className="fixed inset-0 bg-black/90 backdrop-blur-md z-[60] flex items-center justify-center p-6 text-slate-200">
          <div className="bg-[#1a1e26] border border-white/5 rounded-[3rem] p-10 w-full max-w-md shadow-2xl">
            <div className="flex justify-between items-center mb-10 text-white">
              <h2 className="text-sm font-bold uppercase tracking-widest">Initialize Node</h2>
              <button title="Cancel Node Registration" aria-label="Cancel Node Registration" onClick={() => setShowModal(false)}><X /></button>
            </div>
            <div className="space-y-6">
              <input title="Node Name" id="cam-name" className="w-full bg-black/40 border border-white/5 rounded-2xl p-4 text-[12px] text-white outline-none focus:border-emerald-500 font-mono" placeholder="Node Descriptor" />
              <input title="Network Path" id="cam-url" className="w-full bg-black/40 border border-white/5 rounded-2xl p-4 text-[12px] text-white outline-none focus:border-emerald-500 font-mono" placeholder="Network Path (RTSP)" />
              <button title="Establish Secure Uplink" aria-label="Establish Secure Uplink" onClick={() => {
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

function NavItem({ icon, label, badge, active, onClick }: any) {
  return (
    <button title={label} aria-label={label} onClick={onClick} className={`w-full flex items-center justify-between px-5 py-3.5 rounded-2xl transition-all group ${active ? 'bg-emerald-500 text-black shadow-lg' : 'text-slate-500 hover:bg-white/5 hover:text-white'}`}>
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
    <div className="p-6 bg-red-500/[0.02] border border-red-500/10 rounded-[1.8rem] hover:border-red-500/30 shadow-lg relative overflow-hidden group"><div className="absolute top-0 right-0 p-4 opacity-5 group-hover:opacity-20 transition-opacity"><ShieldAlert size={60} className="text-red-500" /></div><span className="text-[9px] font-bold uppercase text-red-500/80">Alert Triggered</span><h4 className="text-lg font-semibold uppercase text-white mb-5 tracking-tight">{alert.type}</h4><div className="space-y-4 mb-7 text-[10px] text-slate-400"><div className="flex items-center gap-2 text-slate-300 font-sans"><MapPin size={12} className="text-emerald-500" /> {alert.location}</div></div><div className="grid grid-cols-2 gap-3 relative z-10"><button title="Verify Node" aria-label="Verify Node" className="bg-emerald-500 text-black text-[9px] font-bold uppercase py-3 rounded-xl active:scale-95 transition-all shadow-md">Verify</button><button title="Ignore Node" aria-label="Ignore Node" className="border border-white/5 text-slate-400 text-[9px] font-bold uppercase py-3 rounded-xl hover:bg-white/5 transition-all">Ignore</button></div></div>
  );
}

function SystemFlowView() {
  return (
    <div className="bg-[#11141b] border border-white/5 rounded-[2.5rem] p-16 flex items-center justify-between shadow-2xl relative overflow-hidden text-slate-200"><div className="absolute top-0 left-0 w-full h-1 bg-gradient-to-r from-emerald-500 via-blue-500 to-red-500 opacity-20" /><FlowBox icon={<Video size={24}/>} label="Source" sub="UPLINK_01" /><ChevronRight className="opacity-10 text-emerald-500" size={24} /><FlowBox icon={<Cpu size={24}/>} label="Neural" sub="YOLOv11_P" /><ChevronRight className="opacity-10 text-emerald-500" size={24} /><FlowBox icon={<Network size={24}/>} label="Bridge" sub="WS_SOCKET" /><ChevronRight className="opacity-10 text-emerald-500" size={24} /><FlowBox icon={<BellRing size={24}/>} label="Actuator" sub="ESP32_GPIO" /></div>
  );
}

function FlowBox({ icon, label, sub }: any) {
  return (
    <div className="flex flex-col items-center text-center group"><div className="mb-5 p-5 border border-white/5 rounded-2xl text-emerald-500/80 shadow-inner group-hover:border-emerald-500/30 transition-all">{icon}</div><span className="text-[10px] font-bold uppercase tracking-widest text-slate-300 mb-1">{label}</span><span className="text-[8px] font-mono opacity-30 uppercase tracking-tighter italic">{sub}</span></div>
  );
}