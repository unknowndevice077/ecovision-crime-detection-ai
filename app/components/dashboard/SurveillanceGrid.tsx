"use client";
import React, { useState } from 'react';
import { Activity, Maximize2, Camera as CameraIcon, Check, Loader2 } from 'lucide-react';
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

  const [camIndexInput, setCamIndexInput] = useState("5");
  const [applyState, setApplyState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');

  const handleApplyCameraIndex = async () => {
    const idx = parseInt(camIndexInput, 10);
    if (Number.isNaN(idx) || idx < 0) return;
    setApplyState('saving');
    try {
      // Hits main.py's stream server directly (port 8001), same port the
      // live feed <img> tags already pull from -- swaps the OpenCV capture
      // device (webcam index, OBS Virtual Camera slot, etc.) without
      // restarting the AI process, and persists the choice to config.json.
      const res = await fetch("http://localhost:8001/set_camera_index", {
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

  return (
    <div className="space-y-4 flex flex-col h-full animate-in fade-in duration-500">
      <div className="grid grid-cols-3 gap-4">
        <StatCard label="Detections (24h)" val="1,402" color="text-slate-200" subLabel="Total" />
        <StatCard label="Processing Speed" val="58.2" color="text-emerald-400" subLabel="FPS" />
        <StatCard label="Threat Level" val={pendingCount.toString()} color={pendingCount > 0 ? "text-red-500" : "text-slate-500"} subLabel={pendingCount > 0 ? "CRITICAL" : "NORMAL"} />
      </div>

      <div className="flex-1 bg-[#11141b] border border-white/5 rounded-[2rem] p-6 shadow-2xl flex flex-col relative overflow-hidden">
        <div className="flex justify-between items-center mb-6 px-2 flex-wrap gap-3">
          <div className="flex items-center gap-3 text-[10px] font-bold uppercase tracking-[0.2em] text-slate-300">
            <Activity size={16} className="text-emerald-500" />
            {selectedCam ? `Target Trace: ${selectedCam.name}` : "Global Surveillance Grid"}
          </div>

          <div className="flex items-center gap-3">
            {/* CAMERA INDEX PICKER -- lets a user point the live capture device
                (webcam / OBS Virtual Camera / capture card) at whatever index
                actually exists on this machine, instead of a hardcoded value. */}
            <div className="flex items-center gap-1.5 bg-black/40 border border-white/5 rounded-xl px-3 py-1.5">
              <CameraIcon size={13} className="text-slate-500" />
              <span className="text-[9px] font-mono text-slate-500 uppercase">Device Index</span>
              <input
                type="number"
                min={0}
                title="OpenCV camera device index (webcam / OBS Virtual Camera slot)"
                value={camIndexInput}
                onChange={(e) => setCamIndexInput(e.target.value)}
                className="w-12 bg-black/40 border border-white/10 rounded-lg px-2 py-1 text-[11px] font-mono text-white outline-none focus:border-emerald-500 text-center"
              />
              <button
                onClick={handleApplyCameraIndex}
                disabled={applyState === 'saving'}
                title="Apply camera index"
                className="p-1.5 rounded-lg bg-emerald-500/10 hover:bg-emerald-500 text-emerald-400 hover:text-black border border-emerald-500/20 transition-all disabled:opacity-40"
              >
                {applyState === 'saving' ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
              </button>
              {applyState === 'saved' && <span className="text-[9px] text-emerald-400 font-mono">Applied</span>}
              {applyState === 'error' && <span className="text-[9px] text-red-400 font-mono">Failed</span>}
            </div>

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