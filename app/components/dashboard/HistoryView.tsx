"use client";
import React, { useState } from 'react';
import { 
  Calendar, Clock, Scissors, Save, Play, 
  ArrowLeft, Search, Filter, ShieldAlert
} from 'lucide-react';

type ArchiveItem = {
  id: string;
  time: string;
  label: string;
  duration: string;
  severity: 'LOW' | 'MEDIUM' | 'HIGH';
};

export default function HistoryView() {
  const [selectedVideo, setSelectedVideo] = useState<ArchiveItem | null>(null);
  const [selectedDate, setSelectedDate] = useState("05/15/26");
  const [startTime, setStartTime] = useState("14:30");
  const [endTime, setEndTime] = useState("14:45");

  const archiveGrid: ArchiveItem[] = [
    { id: '1', time: '14:30:05', label: 'AI_DETECTION: VIOLENCE', duration: '15m', severity: 'HIGH' },
    { id: '2', time: '12:15:10', label: 'AI_DETECTION: UNKNOWN_OBJ', duration: '10m', severity: 'MEDIUM' },
    { id: '3', time: '09:04:45', label: 'AI_DETECTION: TRESPASS', duration: '05m', severity: 'HIGH' },
    { id: '4', time: '04:20:00', label: 'ROUTINE_ARCHIVE', duration: '60m', severity: 'LOW' },
    { id: '5', time: '01:00:12', label: 'AI_DETECTION: THEFT', duration: '20m', severity: 'HIGH' },
    { id: '6', time: '22:45:30', label: 'ROUTINE_ARCHIVE', duration: '60m', severity: 'LOW' },
  ];

  if (!selectedVideo) {
    return (
      <div className="h-full flex flex-col gap-6 animate-in fade-in duration-500 text-slate-200">
        <div className="flex items-center justify-between bg-[#11141b] border border-white/5 p-6 rounded-[2rem] shadow-xl">
          <div className="flex items-center gap-6">
            <div className="flex items-center gap-3">
              <Calendar size={18} className="text-emerald-500" />
              <input 
                type="text" 
                title="Select Archive Date"
                aria-label="Select Archive Date"
                placeholder="MM/DD/YY"
                value={selectedDate} 
                onChange={(e) => setSelectedDate(e.target.value)}
                className="bg-black/40 border border-white/10 rounded-xl px-4 py-2 text-sm font-mono text-white outline-none focus:border-emerald-500" 
              />
            </div>
            <div className="h-8 w-px bg-white/10" />
            <div className="flex items-center gap-2 text-slate-500 text-[10px] font-bold uppercase tracking-widest">
              <Filter size={14} /> Total Events: {archiveGrid.length}
            </div>
          </div>
          <div className="relative">
            <Search className="absolute left-4 top-1/2 -translate-y-1/2 text-slate-600" size={16} />
            <input 
              title="Search Archives"
              placeholder="Search Archive Labels..." 
              className="bg-black/40 border border-white/10 rounded-2xl pl-12 pr-6 py-3 text-xs outline-none focus:border-emerald-500 w-64" 
            />
          </div>
        </div>

        <div className="flex-1 overflow-y-auto custom-scrollbar pr-2">
          <div className="grid grid-cols-3 gap-4">
            {archiveGrid.map((item) => (
              <button 
                key={item.id}
                title={`View Archive: ${item.label}`}
                aria-label={`View Archive: ${item.label}`}
                onClick={() => setSelectedVideo(item)}
                className="bg-[#11141b] border border-white/5 rounded-[2rem] overflow-hidden group hover:border-emerald-500/30 transition-all text-left shadow-lg"
              >
                <div className="h-32 bg-slate-900 relative flex items-center justify-center">
                  <div className="absolute inset-0 bg-gradient-to-t from-black/80 to-transparent" />
                  <Play size={32} className="text-white opacity-20 group-hover:opacity-100 group-hover:scale-110 transition-all" />
                  {item.severity === 'HIGH' && (
                    <div className="absolute top-4 right-4 bg-red-600/20 border border-red-500 text-red-500 text-[8px] px-2 py-0.5 rounded font-black uppercase">Critical</div>
                  )}
                  <span className="absolute bottom-3 left-4 text-[10px] font-mono text-emerald-500 font-bold">{item.time}</span>
                </div>
                <div className="p-5 space-y-2">
                  <p className="text-[10px] font-black text-white uppercase tracking-tight truncate">{item.label}</p>
                  <div className="flex justify-between items-center text-[9px] text-slate-500 font-bold">
                    <span className="flex items-center gap-1"><Clock size={10}/> {item.duration}</span>
                    <span className="uppercase font-mono">ID: ARCH_00{item.id}</span>
                  </div>
                </div>
              </button>
            ))}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col gap-4 animate-in slide-in-from-right duration-500 text-slate-200">
      <div className="flex items-center gap-4 mb-2">
        <button 
          title="Return to Grid"
          aria-label="Return to Grid"
          onClick={() => setSelectedVideo(null)}
          className="p-3 bg-white/5 hover:bg-emerald-500 hover:text-black rounded-2xl transition-all shadow-xl"
        >
          <ArrowLeft size={20} />
        </button>
        <div>
          <h2 className="text-lg font-bold uppercase tracking-tight text-white">{selectedVideo.label}</h2>
          <p className="text-[10px] font-mono text-slate-500 uppercase">{selectedDate} • Recorded at {selectedVideo.time}</p>
        </div>
      </div>

      <div className="flex-1 grid grid-cols-12 gap-4">
        <div className="col-span-9 flex flex-col gap-4">
          <div className="flex-1 bg-black rounded-[3rem] border border-white/5 relative overflow-hidden group shadow-2xl">
            <div className="absolute inset-0 flex items-center justify-center">
              <Play size={64} className="text-white opacity-10" />
            </div>
            <div className="absolute bottom-0 left-0 w-full p-10 bg-gradient-to-t from-black to-transparent">
              <div className="space-y-4">
                <div className="flex justify-between text-[9px] font-mono text-slate-500 uppercase">
                  <span>Start Of Clip</span>
                  <span className="text-emerald-500 font-black">MARK_MODE_ACTIVE</span>
                  <span>End Of Clip</span>
                </div>
                <div className="relative h-6 w-full bg-white/5 rounded-xl border border-white/5 cursor-crosshair">
                   <div className="absolute left-[30%] w-[10%] h-full bg-red-500/30 border-x border-red-500/50" />
                   <div className="absolute left-[15%] w-[40%] h-full bg-emerald-500/10 border-x-4 border-emerald-500" />
                </div>
              </div>
            </div>
          </div>

          <div className="bg-[#11141b] border border-white/5 rounded-3xl p-6 flex items-center justify-between shadow-xl">
            <div className="flex items-center gap-8">
              <div className="space-y-1">
                <p className="text-[9px] font-bold text-slate-500 uppercase tracking-widest flex items-center gap-2"><Clock size={12}/> Mark In</p>
                <input 
                  type="time" 
                  title="Mark In Time"
                  aria-label="Mark In Time"
                  value={startTime} 
                  onChange={e => setStartTime(e.target.value)} 
                  className="bg-black/40 border border-white/10 rounded-lg px-4 py-2 text-xs text-white outline-none focus:border-emerald-500" 
                />
              </div>
              <div className="space-y-1">
                <p className="text-[9px] font-bold text-slate-500 uppercase tracking-widest flex items-center gap-2"><Clock size={12}/> Mark Out</p>
                <input 
                  type="time" 
                  title="Mark Out Time"
                  aria-label="Mark Out Time"
                  value={endTime} 
                  onChange={e => setEndTime(e.target.value)} 
                  className="bg-black/40 border border-white/10 rounded-lg px-4 py-2 text-xs text-white outline-none focus:border-emerald-500" 
                />
              </div>
              <div className="h-10 w-px bg-white/5" />
              <div className="space-y-1">
                <p className="text-[9px] font-bold text-slate-500 uppercase tracking-widest">Evidence Length</p>
                <p className="text-md font-mono text-emerald-500 font-black">00:15:00.00</p>
              </div>
            </div>
            <button title="Extract Evidence Clip" aria-label="Extract Evidence Clip" className="px-10 py-5 bg-emerald-600 text-black rounded-2xl font-black uppercase text-[10px] tracking-[0.2em] hover:bg-emerald-400 active:scale-95 transition-all shadow-lg flex items-center gap-3">
              <Scissors size={18}/> Extract Clip
            </button>
          </div>
        </div>

        <div className="col-span-3 bg-[#11141b] border border-white/5 rounded-[3rem] p-6 flex flex-col gap-6 shadow-2xl">
           <div className="p-4 bg-emerald-500/10 rounded-2xl border border-emerald-500/20">
             <h4 className="text-[10px] font-black text-emerald-500 uppercase mb-2">System Metadata</h4>
             <p className="text-[11px] text-slate-300 font-mono leading-relaxed">NODE: COGON_01<br/>RES: 1920x1080<br/>AI_CONF: 0.98</p>
           </div>
           <div className="space-y-4">
             <h4 className="text-[9px] font-bold text-slate-500 uppercase tracking-widest flex items-center gap-2"><ShieldAlert size={14}/> Forensic Labels</h4>
             <div className="flex flex-wrap gap-2">
               {['Violence', 'Physical', 'Theft', 'Evidence'].map(tag => (
                 <span key={tag} className="text-[8px] font-black px-2 py-1 bg-black/40 border border-white/5 rounded-md text-slate-400 uppercase">{tag}</span>
               ))}
             </div>
           </div>
           <div className="mt-auto">
              <button title="Delete Archive" aria-label="Delete Archive" className="w-full py-4 bg-transparent border border-white/10 rounded-2xl text-[9px] font-black uppercase text-slate-500 hover:text-white hover:bg-white/5 transition-all mb-3">Expunge Archive</button>
              <button title="Save To Case" aria-label="Save To Case" className="w-full py-4 bg-white text-black rounded-2xl text-[9px] font-black uppercase flex items-center justify-center gap-2 shadow-xl"><Save size={14}/> Save to Case File</button>
           </div>
        </div>
      </div>
    </div>
  );
}