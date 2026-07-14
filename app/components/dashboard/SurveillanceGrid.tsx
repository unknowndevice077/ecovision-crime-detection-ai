"use client";
import React from 'react';
import { Activity, Maximize2 } from 'lucide-react';
import { Camera, Alert } from '../../types';

interface GridProps {
  cameras: Camera[];
  selectedCam: Camera | null;
  setSelectedCam: (cam: Camera | null) => void;
  setIsFullscreenGrid: (val: boolean) => void;
  alerts: Alert[];
}

export default function SurveillanceGrid({ cameras, selectedCam, setSelectedCam, setIsFullscreenGrid, alerts }: GridProps) {
  const pendingCount = alerts.filter(a => a.status === 'pending').length;

  return (
    <div className="space-y-4 flex flex-col h-full animate-in fade-in duration-500">
      <div className="grid grid-cols-3 gap-4">
        <StatCard label="Detections (24h)" val="1,402" color="text-slate-200" subLabel="Total" />
        <StatCard label="Processing Speed" val="58.2" color="text-emerald-400" subLabel="FPS" />
        <StatCard label="Threat Level" val={pendingCount.toString()} color={pendingCount > 0 ? "text-red-500" : "text-slate-500"} subLabel={pendingCount > 0 ? "CRITICAL" : "NORMAL"} />
      </div>

      <div className="flex-1 bg-[#11141b] border border-white/5 rounded-[2rem] p-6 shadow-2xl flex flex-col relative overflow-hidden">
        <div className="flex justify-between items-center mb-6 px-2">
          <div className="flex items-center gap-3 text-[10px] font-bold uppercase tracking-[0.2em] text-slate-300">
            <Activity size={16} className="text-emerald-500" />
            {selectedCam ? `Target Trace: ${selectedCam.name}` : "Global Surveillance Grid"}
          </div>
          <div className="flex items-center gap-2">
            <button 
              title="Maximize Surveillance Grid" 
              aria-label="Maximize Surveillance Grid"
              onClick={() => setIsFullscreenGrid(true)} 
              className="p-2 hover:bg-emerald-500/10 rounded-lg text-emerald-500 border border-white/5 shadow-sm transition-all"
            >
              <Maximize2 size={16} />
            </button>
            {selectedCam && (
              <button onClick={() => setSelectedCam(null)} className="text-[9px] font-bold uppercase text-emerald-500 px-3 py-1.5 rounded-lg border border-emerald-500/20 hover:bg-emerald-500/10 transition-all">
                Exit Focus
              </button>
            )}
          </div>
        </div>
        <div className="flex-1 min-h-0">
          {selectedCam ? (
            <div className="w-full h-full bg-black rounded-2xl border border-emerald-500/10 overflow-hidden shadow-inner">
              <img src="http://localhost:8001/video_feed" className="w-full h-full object-cover" alt="Focused" />
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-4 h-full">
              {cameras.map(cam => (
                <button key={cam.id} onClick={() => setSelectedCam(cam)} className="bg-black rounded-2xl border border-white/5 relative group hover:border-emerald-500/40 transition-all overflow-hidden shadow-md">
                  <img src="http://localhost:8001/video_feed" className="w-full h-full object-cover opacity-60 group-hover:opacity-100 transition-opacity duration-500" alt="stream" />
                  <div className="absolute inset-0 bg-gradient-to-t from-black via-transparent to-transparent opacity-60" />
                  <span className="absolute bottom-4 left-4 text-[9px] font-bold uppercase text-white tracking-widest bg-black/40 px-3 py-1 rounded border border-white/5 font-mono">{cam.name}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function StatCard({ label, val, color, subLabel }: any) {
  return (
    <div className="bg-[#11141b] border border-white/5 rounded-2xl p-5 relative group hover:bg-[#161a22] shadow-xl transition-all">
      <div className={`absolute top-0 left-0 w-full h-[2px] opacity-20 group-hover:opacity-100 transition-opacity ${color.replace('text', 'bg')}`} />
      <div className="flex flex-col gap-1 font-mono tabular-nums">
        <span className="text-[9px] font-bold text-slate-500 uppercase tracking-[0.15em] font-sans">{label}</span>
        <div className="flex items-baseline gap-2"><span className={`text-2xl font-semibold tracking-tight ${color}`}>{val}</span><span className="text-[9px] text-slate-600 uppercase font-bold">{subLabel}</span></div>
      </div>
    </div>
  );
}