"use client";

import React from 'react';
import { useRouter, usePathname } from 'next/navigation';
import { 
  Shield, AlertOctagon, Activity, Video, MapPin, 
  Zap, LogOut, Film, BatteryMedium, Users, Wifi, WifiOff
} from 'lucide-react';
import { usePermissions } from '../../hooks/usePermissions';
import { useWebSocketContext } from '../../context/WebSocketContext';

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

type SidebarProps = {
  currentUser: {
    role: 'DEVTEAM' | 'PRECINCT_CAPTAIN' | 'POLICE' | 'BARANGAY_CAPTAIN' | 'BARANGAY';
    assignment: string;
  } | null;
  sqlReportCount?: number;
  camerasCount?: number;
  telemetry?: {
    battery: number;
    solarV: number;
    tempCPU: number;
    tempESP: number;
    tempNeural: number;
    load: number;
  };
  activeTab?: string;
  setActiveTab?: (tab: string) => void;
};

export default function Sidebar({ 
  currentUser, 
  sqlReportCount = 0, 
  camerasCount = 0,
  telemetry = { battery: 88, solarV: 14.4, tempCPU: 42, tempESP: 38, tempNeural: 51, load: 12.4 },
  activeTab,
  setActiveTab
}: SidebarProps) {
  const router = useRouter();
  const pathname = usePathname();
  const { can } = usePermissions();
  const { connected } = useWebSocketContext();

  if (!currentUser || currentUser.role === 'DEVTEAM') return null;

  const handleLogout = () => {
    const token = localStorage.getItem('ecoToken');
    fetch(`${API_URL}/api/logout`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    }).catch(() => {}); // stateless tokens -- best-effort, logout proceeds regardless
    localStorage.removeItem('ecoUser');
    localStorage.removeItem('ecoToken');
    router.push('/loginpage/login');
  };

  const isDashboardActive = activeTab === 'dashboard' || pathname === '/';
  const isMapActive = activeTab === 'crime-reports' || pathname === '/map';
  const isRecordsActive = activeTab === 'records' || pathname === '/records';
  const isHistoryActive = activeTab === 'alerts' || pathname === '/history';
  const isCamerasActive = activeTab === 'cameras';
  const isHealthActive = activeTab === 'health';
  const isManageUsersActive = activeTab === 'manage-users';

  const changeTab = (tabName: string, fallbackRoute: string) => {
    if (setActiveTab) {
      setActiveTab(tabName);
    } else {
      router.push(fallbackRoute);
    }
  };

  const isPoliceTier = currentUser.role === 'POLICE' || currentUser.role === 'PRECINCT_CAPTAIN';
  const isBarangayTier = currentUser.role === 'BARANGAY' || currentUser.role === 'BARANGAY_CAPTAIN';
  const isAdmin = currentUser.role === 'PRECINCT_CAPTAIN' || currentUser.role === 'BARANGAY_CAPTAIN';
  // DEVTEAM never reaches this point -- it returns null above and renders
  // its own full-viewport console instead. isElevated only needs to cover
  // the admin tiers still routed through this sidebar.
  const isElevated = isAdmin;

  return (
    <aside className="w-64 bg-[#11141b] border border-white/5 rounded-3xl flex flex-col p-6 shrink-0 shadow-2xl overflow-y-auto custom-scrollbar">
      <style>{`
        .sidebar-battery-progress { width: ${telemetry.battery}%; }
      `}</style>

      <div className="flex items-center justify-between mb-8 pb-6 border-b border-white/5">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-emerald-500 rounded-xl">
            <Shield className="w-5 h-5 text-[#0a0c10]" />
          </div>
          <h1 className="text-sm font-semibold tracking-widest uppercase text-white">
            EcoVision <span className="text-emerald-500 font-mono text-[10px]">v16.0</span>
          </h1>
        </div>
        <div title={connected ? "Live connection active" : "Reconnecting..."}>
          {connected
            ? <Wifi size={14} className="text-emerald-500" />
            : <WifiOff size={14} className="text-red-500 animate-pulse" />}
        </div>
      </div>

      <nav className="space-y-1.5 flex-1">
        <NavItem
          label="Monitor"
          icon={<Activity size={18}/>}
          active={isDashboardActive}
          onClick={() => changeTab('dashboard', '/')}
        />

        {/* POLICE TIER -- standard POLICE accounts only see items their
            admin actually granted via AdminUsersView's permissions editor.
            PRECINCT_CAPTAIN sees everything regardless (isElevated). */}
        {isPoliceTier && (
          <>
            {(isElevated || can('view_map')) && (
              <NavItem 
                label="Tactical Map" 
                icon={<MapPin size={18}/>} 
                active={isMapActive} 
                onClick={() => changeTab('crime-reports', '/')} 
                badge={sqlReportCount} 
              />
            )}
            {(isElevated || can('view_records')) && (
              <NavItem 
                label="Records" 
                icon={<Film size={18}/>} 
                active={isRecordsActive} 
                onClick={() => router.push('/records')} 
              />
            )}
            {(isElevated || can('view_history')) && (
              <NavItem 
                label="Crime History" 
                icon={<AlertOctagon size={18}/>} 
                active={isHistoryActive} 
                onClick={() => changeTab('alerts', '/')} 
              />
            )}
          </>
        )}

        {/* BARANGAY TIER */}
        {isBarangayTier && (
          <>
            {(isElevated || can('manage_cameras')) && (
              <NavItem 
                label="Add Cameras" 
                icon={<Video size={18}/>} 
                active={isCamerasActive} 
                onClick={() => changeTab('cameras', '/')} 
                badge={camerasCount} 
              />
            )}
            <NavItem 
              label="Hardware Status" 
              icon={<Zap size={18}/>} 
              active={isHealthActive} 
              onClick={() => changeTab('health', '/')} 
            />
          </>
        )}

        {isAdmin && (
          <NavItem 
            label="Manage Users" 
            icon={<Users size={18}/>} 
            active={isManageUsersActive} 
            onClick={() => changeTab('manage-users', '/')} 
          />
        )}
      </nav>

      {isBarangayTier && (
        <div className="my-4 p-4 bg-black/20 border border-white/5 rounded-2xl space-y-3 shrink-0">
          <div className="flex justify-between items-center text-[10px] uppercase tracking-wider text-slate-400 font-bold">
            <span className="flex items-center gap-1.5"><BatteryMedium size={12}/> Charge Loop</span>
            <span className="font-mono text-white">{telemetry.battery}%</span>
          </div>
          <div className="w-full bg-white/5 h-1.5 rounded-full overflow-hidden">
            <div className="bg-emerald-500 h-full sidebar-battery-progress transition-all duration-500 shadow-[0_0_10px_#10b981]" />
          </div>
        </div>
      )}

      <div className="my-6 space-y-3 pt-6 border-t border-white/5">
        <h3 className="px-2 text-[9px] font-bold text-slate-500 uppercase tracking-widest">System Engine</h3>
      </div>
      
      <div className="mt-auto pt-4">
        <button 
          title="Sign Out" 
          onClick={handleLogout} 
          className="w-full flex items-center gap-3 px-4 py-3 text-slate-500 hover:text-red-500 text-[10px] uppercase font-bold transition-all border border-white/5 rounded-xl hover:border-red-500/20"
        >
          <LogOut size={16}/> Terminate
        </button>
      </div>
    </aside>
  );
}

function NavItem({ icon, label, badge, active, onClick }: any) {
  return (
    <button 
      title={label} 
      onClick={onClick} 
      className={`w-full flex items-center justify-between px-5 py-3.5 rounded-2xl transition-all group ${
        active ? 'bg-emerald-500 text-black shadow-lg' : 'text-slate-500 hover:bg-white/5 hover:text-white'
      }`}
    >
      <div className="flex items-center gap-3">
        {icon}
        <span className="text-[10px] font-bold uppercase tracking-[0.1em]">{label}</span>
      </div>
      {badge > 0 && (
        <span className={`text-[9px] font-bold px-2 py-0.5 rounded-full ${
          active ? 'bg-black text-white' : 'bg-red-500 text-white'
        }`}>
          {badge}
        </span>
      )}
    </button>
  );
}