"use client";

import React from 'react';
import { User, Shield, MapPin, Key, ShieldCheck, LogOut } from 'lucide-react';
import { usePermissions } from '../hooks/usePermissions';

interface ProfileViewProps {
  currentUser: {
    id: string | number;
    username: string;
    role: string;
    barangay_id: string;
    assignment: string;
    display_title?: string;
    is_sub_admin?: boolean;
  };
  time: string;
  onLogout: () => void;
}

export default function ProfileView({ currentUser, onLogout }: ProfileViewProps) {
  const { permissions } = usePermissions();

  if (!currentUser) return <div className="p-8 text-slate-500 font-mono text-xs">Awaiting operator scope ingestion...</div>;

  const labelClass = "text-[8px] font-mono text-slate-500 uppercase block mb-1 font-black tracking-widest";
  const activePerms = Object.entries(permissions).filter(([, v]) => v).map(([k]) => k);

  return (
    <div className="h-full bg-[#0E131F]/40 border border-white/[0.04] p-8 rounded-[2.5rem] shadow-2xl flex flex-col gap-6 overflow-y-auto custom-scrollbar animate-in fade-in duration-300 text-slate-200">
      
      {/* PROFILE HEADER WITH MINI ICON LOGOUT REPOSITIONED TO THE RIGHT */}
      <div className="flex items-center justify-between pb-4 border-b border-white/5 shrink-0">
        <div className="flex items-center gap-4 min-w-0">
          <div className="p-4 rounded-2xl bg-gradient-to-br from-emerald-500/20 to-teal-500/5 text-emerald-400 border border-emerald-500/20 shadow-xl shadow-emerald-500/5 shrink-0">
            <User size={32} />
          </div>
          <div className="min-w-0">
            <h3 className="text-lg font-mono font-black text-white uppercase tracking-wide truncate">{currentUser.username || "Unknown Operator"}</h3>
            <p className="text-xs font-mono text-slate-500 uppercase tracking-widest mt-1">
              {currentUser.display_title || "Personnel Authentication Summary Ledger"}
            </p>
          </div>
        </div>

        {/* COMPACT CLEAN LOGOUT ACTION PLACED NEXT TO NAME */}
        <button
          onClick={onLogout}
          title="Terminate active session shell"
          className="p-3 bg-rose-500/10 hover:bg-rose-500 text-rose-400 hover:text-black border border-rose-500/20 rounded-xl transition-all duration-200 shadow-md group shrink-0 ml-4 flex items-center gap-1.5 text-[10px] font-mono font-bold uppercase tracking-wider"
        >
          <LogOut size={14} />
          <span className="hidden sm:inline">Logout</span>
        </button>
      </div>

      <div className="grid grid-cols-2 gap-4 shrink-0">
        <div className="bg-black/30 border border-white/[0.03] p-5 rounded-2xl font-mono shadow-inner">
          <span className={labelClass}>Clearance Mapping Group</span>
          <div className="flex items-center gap-2 mt-1">
            <Shield size={14} className="text-emerald-400" />
            <span className="text-xs font-bold text-white uppercase">{(currentUser.role || "GUEST").toUpperCase()} ACCESS CONTROL</span>
          </div>
        </div>
        <div className="bg-black/30 border border-white/[0.03] p-5 rounded-2xl font-mono shadow-inner">
          <span className={labelClass}>Assigned Deployment Field</span>
          <div className="flex items-center gap-2 mt-1">
            <MapPin size={14} className="text-teal-400" />
            <span className="text-xs font-bold text-slate-200 uppercase">{(currentUser.barangay_id?.toUpperCase() || "GLOBAL")} SECTOR DETACHMENT</span>
          </div>
        </div>
      </div>

      <div className="bg-black/20 border border-white/[0.03] rounded-2xl p-6 space-y-4 font-mono shadow-2xl flex-1">
        <h4 className="text-[10px] font-black text-slate-400 uppercase tracking-widest flex items-center gap-2 mb-2">
          <Key size={12} className="text-emerald-400"/> {`Operational Cryptographic Signatures`}
        </h4>
        <div className="grid grid-cols-3 gap-4 border-t border-white/5 pt-4 text-xs">
          <div>
            <span className={labelClass}>Badge Status</span>
            <span className="px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 text-[9px] font-black uppercase tracking-wider flex items-center gap-1 w-max">
              <ShieldCheck size={10}/> Verified
            </span>
          </div>
          <div>
            <span className={labelClass}>Station Base Terminal</span>
            <span className="text-slate-300 font-bold uppercase text-[11px]">{currentUser.assignment || "UNASSIGNED"}</span>
          </div>
          <div>
            <span className={labelClass}>Node Reference Token</span>
            <span className="text-slate-500 font-bold font-mono text-[11px]">SEC-ID: {currentUser.id || "0"}026</span>
          </div>
        </div>

        {currentUser.is_sub_admin && (
          <div className="border-t border-white/5 pt-4">
            <span className={labelClass}>Granted Permission Scopes</span>
            <div className="flex flex-wrap gap-2 mt-2">
              {activePerms.length > 0 ? activePerms.map((p) => (
                <span key={p} className="px-2 py-1 rounded bg-teal-500/10 text-teal-400 border border-teal-500/20 text-[9px] font-black uppercase tracking-wider">
                  {p.replace(/_/g, ' ')}
                </span>
              )) : (
                <span className="text-slate-600 text-[10px] uppercase">No scopes granted</span>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}