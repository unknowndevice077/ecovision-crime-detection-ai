"use client";
import React from 'react';
import { 
  Shield, Activity, Video, Zap, AlertOctagon, 
  BarChart3, BatteryMedium, Sun, LogOut, MapPin 
} from 'lucide-react';
import { User, Telemetry } from '../../types';

interface SidebarProps {
  currentUser: User;
  activeTab: string;
  setActiveTab: (tab: string) => void;
  telemetry: Telemetry;
  isSirenActive: boolean;
  sqlReportCount: number;
  pendingAlertsCount: number;
  onLogout: () => void;
}

export default function Sidebar({ 
  currentUser, activeTab, setActiveTab, telemetry, 
  isSirenActive, sqlReportCount, pendingAlertsCount, onLogout 
}: SidebarProps) {
  
  const isMapView = activeTab === 'crime-reports';

  return (
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
        {/* POLICE NAVIGATION */}
        {currentUser.role === 'POLICE' && (
          <>
            <NavItem 
              label="Monitor" 
              icon={<Activity size={18}/>} 
              active={activeTab === 'dashboard'} 
              onClick={() => setActiveTab('dashboard')} 
            />
            <NavItem 
              label="Add Cameras" 
              icon={<Video size={18}/>} 
              active={activeTab === 'cameras'} 
              onClick={() => setActiveTab('cameras')} 
            />
            {/* Integrated Tactical Map Button - CrimeReportsItem.tsx is no longer needed */}
            <NavItem 
              label="Tactical Map" 
              icon={<MapPin size={18}/>} 
              active={isMapView} 
              onClick={() => setActiveTab('crime-reports')} 
              badge={sqlReportCount} 
            />
          </>
        )}

        {/* BARANGAY NAVIGATION */}
        {currentUser.role === 'BARANGAY' && (
          <>
            <NavItem 
              label="Monitor" 
              icon={<Activity size={18}/>} 
              active={activeTab === 'dashboard'} 
              onClick={() => setActiveTab('dashboard')} 
            />
            <NavItem 
              label="System Health" 
              icon={<Zap size={18}/>} 
              active={activeTab === 'health'} 
              onClick={() => setActiveTab('health')} 
            />
          </>
        )}

        {/* GLOBAL NODES */}
        <NavItem 
          label="Incidents" 
          icon={<AlertOctagon size={18}/>} 
          active={activeTab === 'alerts'} 
          onClick={() => setActiveTab('alerts')} 
          badge={pendingAlertsCount} 
        />
        <NavItem 
          label="Analytics" 
          icon={<BarChart3 size={18}/>} 
          active={activeTab === 'analytics'} 
          onClick={() => setActiveTab('analytics')} 
        />
      </nav>

      {/* HARDWARE TELEMETRY SUMMARY */}
      <div className="my-6 space-y-3 pt-6 border-t border-white/5">
        <h3 className="px-2 text-[9px] font-bold text-slate-500 uppercase tracking-widest">Hardware Status</h3>
        <div className="bg-black/20 border border-white/5 rounded-2xl p-4 space-y-3 shadow-inner">
          <div className="flex justify-between items-center text-[10px]">
            <span className="flex items-center gap-2 text-slate-400 uppercase font-medium">
              <BatteryMedium size={14} className="text-emerald-500" /> Battery
            </span>
            <span className="font-mono text-white tabular-nums">{telemetry.battery}%</span>
          </div>
          <div className="w-full bg-white/5 h-1.5 rounded-full overflow-hidden">
            <div 
              className="bg-emerald-500 h-full transition-all duration-700" 
              style={{ width: `${telemetry.battery}%` } as React.CSSProperties} 
            />
          </div>
          <div className="flex justify-between items-center text-[10px]">
            <span className="flex items-center gap-2 text-slate-400 uppercase font-medium">
              <Sun size={14} className="text-orange-400" /> Solar
            </span>
            <span className="font-mono text-white tabular-nums">{telemetry.solarV}V</span>
          </div>
        </div>
      </div>
      
      {/* ACTUATOR CONTROLS */}
      <div className="mt-auto pt-4 space-y-4">
        <div className="p-4 bg-[#1a1e26] rounded-2xl border border-white/5 shadow-lg">
          <div className="flex justify-between items-center mb-4 uppercase tracking-[0.15em] text-[9px] font-bold text-slate-500">
            Actuator Link
            <div className={`w-2 h-2 rounded-full ${isSirenActive ? 'bg-red-500 animate-pulse' : 'bg-emerald-500'}`} />
          </div>
          <div className="flex flex-col gap-2">
            <button 
              onClick={() => fetch("http://localhost:8000/siren/activate", { method: "POST" })} 
              className="w-full py-2.5 bg-slate-100 text-black text-[10px] font-bold uppercase rounded-xl hover:bg-white transition-all shadow-md active:scale-95"
            >
              Trigger Panic
            </button>
            <button 
              onClick={() => fetch("http://localhost:8000/siren/reset", { method: "POST" })} 
              className="w-full py-2 bg-transparent border border-white/10 text-white text-[10px] font-bold uppercase rounded-xl hover:bg-white/5 transition-all"
            >
              Siren Reset
            </button>
          </div>
        </div>
        <button 
          onClick={onLogout} 
          className="w-full flex items-center gap-3 px-4 py-3 text-slate-500 hover:text-red-500 text-[10px] uppercase font-bold transition-all border border-white/5 rounded-xl"
        >
          <LogOut size={16}/> Terminate
        </button>
      </div>
    </aside>
  );
}

// --- REUSABLE NAVIGATION ITEM ---
function NavItem({ icon, label, badge, active, onClick }: any) {
  return (
    <button 
      onClick={onClick} 
      className={`w-full flex items-center justify-between px-5 py-3.5 rounded-2xl transition-all group ${
        active ? 'bg-emerald-500 text-black shadow-lg shadow-emerald-500/10 active:scale-95' : 'text-slate-500 hover:bg-white/5 hover:text-white'
      }`}
    >
      <div className="flex items-center gap-3">
        <span className={active ? 'text-black' : 'text-emerald-500 group-hover:text-emerald-400'}>{icon}</span>
        <span className="text-[10px] font-bold uppercase tracking-[0.1em]">{label}</span>
      </div>
      {badge > 0 && (
        <span className={`text-[9px] font-bold px-2 py-0.5 rounded-full ${active ? 'bg-black text-white' : 'bg-red-500 text-white animate-pulse'}`}>
          {badge}
        </span>
      )}
    </button>
  );
}