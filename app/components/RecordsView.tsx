"use client";

import React, { useState, useRef } from 'react';
import {
  ShieldAlert, Film, Clapperboard, Edit3, Save, Play,
  ListFilter, Calendar, Clock, Scissors, AlertCircle
} from 'lucide-react';
import { useLiveChannel } from '../context/WebSocketContext';
import { SkeletonRow } from './dashboard/Skeleton';

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

function authHeaders() {
  const token = typeof window !== "undefined" ? localStorage.getItem("ecoToken") : null;
  return { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) };
}

// Matches backend.py's _row_to_record_dict() -- snake_case, 1:1 with the
// video_records table columns. type is CLIP | FULL_24_7 | CRIME_CLIP per
// schema_final.sql's CHECK constraint.
type VideoRecord = {
  id: string;
  filename: string;
  file_path: string;
  recorded_at: string;
  duration: string;
  type: 'CLIP' | 'FULL_24_7' | 'CRIME_CLIP';
  associated_incident_id?: string | null;
  crime_time_marker?: string;
  notes: string;
};

export default function RecordsView() {
  const [records, setRecords] = useState<VideoRecord[]>([]);
  const [crimes, setCrimes] = useState<any[]>([]);
  const [subView, setSubView] = useState<'CLIPS' | 'DVR'>('CLIPS');
  const [activePlayback, setActivePlayback] = useState<VideoRecord | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editNotes, setEditNotes] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState('');

  const [filterDate, setFilterDate] = useState("");
  const [filterCrimeType, setFilterCrimeType] = useState("ALL");
  const [startTime, setStartTime] = useState("00:00");
  const [endTime, setEndTime] = useState("01:00");

  const videoRef = useRef<HTMLVideoElement | null>(null);

  const fetchRecordsAndCrimes = async () => {
    try {
      const [recordsRes, crimesRes] = await Promise.all([
        fetch(`${API_URL}/api/records`, { headers: authHeaders() }),
        fetch(`${API_URL}/api/incidents`, { headers: authHeaders() }),
      ]);
      if (recordsRes.ok && crimesRes.ok) {
        setRecords(await recordsRes.json());
        setCrimes(await crimesRes.json());
        setError('');
      } else if (recordsRes.status === 401 || crimesRes.status === 401) {
        setError('Session expired -- please log in again.');
      } else {
        setError('Failed to load records.');
      }
    } catch (e) {
      console.error("Failed to query stream archive indices:", e);
      setError('Backend connection failure.');
    } finally {
      setIsLoading(false);
    }
  };

  // Was setInterval(fetchRecordsAndCrimes, 5000) -- now refetches on any
  // relevant WebSocket broadcast (new clips, incident status changes),
  // with a slow 60s fallback poll as a safety net.
  useLiveChannel("*", fetchRecordsAndCrimes);

  const handleUpdateNotes = async (id: string) => {
    try {
      const res = await fetch(`${API_URL}/api/records/${id}/notes`, {
        method: "PATCH",
        headers: authHeaders(),
        body: JSON.stringify({ notes: editNotes })
      });
      if (res.ok) {
        setEditingId(null);
        fetchRecordsAndCrimes();
      } else {
        setError('Could not save notes.');
      }
    } catch (e) {
      console.error("Notes field persistence update error:", e);
      setError('Backend connection failure.');
    }
  };

  const handleExtractClip = async () => {
    if (!activePlayback) return;
    try {
      const res = await fetch(`${API_URL}/api/records/register_clip`, {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({
          filename: `EXTRACT_${Date.now()}_${activePlayback.filename}`,
          duration: "Custom Range",
          type: "CLIP",
          crime_time_marker: startTime,
          notes: `Manually extracted sequence boundary from segment ${activePlayback.filename}.`,
          associated_incident_id: activePlayback.associated_incident_id || null,
        })
      });
      if (res.ok) {
        fetchRecordsAndCrimes();
      } else {
        setError('Could not extract segment.');
      }
    } catch (e) {
      console.error("Clip extraction request failed:", e);
      setError('Backend connection failure.');
    }
  };

  const filteredRecords = records.filter(r => {
    if (r.type !== (subView === 'CLIPS' ? 'CLIP' : 'FULL_24_7')) return false;
    if (filterDate && !r.recorded_at.includes(filterDate)) return false;
    if (subView === 'CLIPS' && filterCrimeType !== 'ALL' && !r.filename.toUpperCase().includes(filterCrimeType)) return false;
    return true;
  });

  return (
    <div className="flex flex-col gap-4 h-full w-full animate-in fade-in duration-300">
      {/* FILTER CONTROL CONSOLE */}
      <div className="bg-[#11141b] border border-white/5 rounded-2xl p-4 flex flex-wrap items-center justify-between gap-4 shrink-0 shadow-lg">
        <div className="flex items-center gap-4 flex-wrap">
          <div className="flex items-center gap-2">
            <Calendar size={14} className="text-slate-500"/>
            <input type="text" title="Filter Records by Date Target" placeholder="YYYY-MM-DD" value={filterDate} onChange={(e) => setFilterDate(e.target.value)} className="bg-black/30 border border-white/5 rounded-xl px-3 py-1.5 text-[11px] font-mono text-white outline-none focus:border-emerald-500 w-36" />
          </div>
          {subView === 'CLIPS' && (
            <div className="flex items-center gap-2">
              <ListFilter size={14} className="text-slate-500"/>
              <select title="Filter Incident Categories" value={filterCrimeType} onChange={(e) => setFilterCrimeType(e.target.value)} className="bg-black/30 border border-white/5 rounded-xl px-3 py-1.5 text-[11px] font-mono text-slate-300 outline-none focus:border-emerald-500">
                <option value="ALL">All Threat Signatures</option>
                <option value="ASSAULT">Assault Intercepts</option>
                <option value="FIREARM">Weapons/Firearms Detects</option>
                <option value="PANIC">Hardware Panic Triggers</option>
              </select>
            </div>
          )}
        </div>

        <div className="flex bg-black/40 border border-white/5 rounded-2xl p-1 gap-1">
          <button onClick={() => { setSubView('CLIPS'); setActivePlayback(null); }} className={`px-4 py-1.5 rounded-xl text-[10px] font-bold uppercase tracking-wider transition-all ${subView === 'CLIPS' ? 'bg-emerald-500 text-black' : 'text-slate-400 hover:text-white'}`}>Automated Clips</button>
          <button onClick={() => { setSubView('DVR'); setActivePlayback(null); }} className={`px-4 py-1.5 rounded-xl text-[10px] font-bold uppercase tracking-wider transition-all ${subView === 'DVR' ? 'bg-emerald-500 text-black' : 'text-slate-400 hover:text-white'}`}>24/7 Records</button>
        </div>
      </div>

      {error && (
        <div className="px-4 py-2 bg-red-500/10 border border-red-500/20 rounded-xl text-[10px] font-bold uppercase text-red-400 text-center shrink-0">
          {error}
        </div>
      )}

      <div className="flex-1 bg-[#11141b] border border-white/5 rounded-[2rem] p-6 shadow-2xl flex overflow-hidden min-h-0">
        <div className="grid grid-cols-12 gap-6 w-full h-full">

          {/* Surveillance Video Display Target Viewport */}
          <div className="col-span-7 flex flex-col gap-4 h-full">
            {activePlayback ? (
              <div className="bg-black rounded-3xl border border-white/5 p-4 flex flex-col gap-4 shadow-inner relative h-full justify-center">
                <video ref={videoRef} controls autoPlay className="w-full rounded-2xl aspect-video bg-neutral-950 shadow-2xl" src={`${API_URL}/static/recordings/${activePlayback.filename}`} />

                <div className="w-full bg-white/5 h-8 rounded-xl relative overflow-hidden flex items-center px-3 border border-white/10 shrink-0">
                  <div className="absolute left-0 top-0 bottom-0 bg-emerald-500/10 w-full" />
                  {subView === 'CLIPS' && activePlayback.crime_time_marker && (
                    <div className="absolute left-1/2 -translate-x-1/2 top-0 bottom-0 bg-red-600/90 text-white font-mono text-[9px] font-bold px-4 flex items-center animate-pulse shadow-[0_0_15px_rgba(220,38,38,0.6)] rounded-lg h-full">
                      <ShieldAlert size={12} className="mr-1.5 inline"/> CRIME INTERCEPT TIME: {activePlayback.crime_time_marker}
                    </div>
                  )}
                  {subView === 'DVR' && crimes.filter(c => c.occurred_date === activePlayback.recorded_at.split(' ')[0]).map((crime, idx) => {
                    const offsetValue = Math.min(84, 15 + (idx * 22));
                    const markerId = `dvr-marker-${idx}`;
                    return (
                      <React.Fragment key={idx}>
                        <style>{`
                          .${markerId} { left: ${offsetValue}%; }
                        `}</style>
                        <div className={`absolute h-full bg-red-600/70 border-x border-red-500 px-2 text-[8px] font-mono text-white flex items-center hover:bg-red-500 z-10 cursor-help ${markerId}`} title={`[${crime.type}] Flagged at ${crime.occurred_time}`}>
                          ⚠️ THREAT DETECT: {crime.occurred_time}
                        </div>
                      </React.Fragment>
                    );
                  })}
                </div>

                <div className="bg-black/40 p-4 rounded-2xl flex items-center justify-between gap-4 shrink-0 border border-white/5">
                  <div className="flex items-center gap-4">
                    <div className="space-y-1">
                      <span className="text-[8px] font-mono uppercase text-slate-500 flex items-center gap-1"><Clock size={10}/> Clip Start</span>
                      <input type="text" title="Start Time Range" value={startTime} onChange={(e) => setStartTime(e.target.value)} className="bg-black/60 border border-white/5 rounded-lg px-2 py-1 text-[10px] font-mono text-white outline-none focus:border-emerald-500 w-20 text-center" />
                    </div>
                    <div className="space-y-1">
                      <span className="text-[8px] font-mono uppercase text-slate-500 flex items-center gap-1"><Clock size={10}/> Clip End</span>
                      <input type="text" title="End Time Range" value={endTime} onChange={(e) => setEndTime(e.target.value)} className="bg-black/60 border border-white/5 rounded-lg px-2 py-1 text-[10px] font-mono text-white outline-none focus:border-emerald-500 w-20 text-center" />
                    </div>
                  </div>
                  <button title="Extract Range Segment" onClick={handleExtractClip} className="bg-emerald-500 hover:bg-emerald-400 text-black px-4 py-2 rounded-xl text-[10px] uppercase font-bold flex items-center gap-1.5 active:scale-95 transition-all"><Scissors size={12}/> Extract Segment</button>
                </div>
              </div>
            ) : (
              <div className="flex-1 border-2 border-dashed border-white/5 rounded-3xl flex flex-col items-center justify-center text-slate-600 opacity-40 h-full">
                <Clapperboard size={48} className="mb-2 text-slate-500"/>
                <span className="text-[10px] font-bold uppercase tracking-widest">Select an archive video to load playback stream</span>
              </div>
            )}
          </div>

          {/* Incident List Records Track metadata Directory column */}
          <div className="col-span-5 flex flex-col overflow-y-auto custom-scrollbar gap-3 h-full pr-1">
            {isLoading ? (
              <div className="space-y-3">
                {Array.from({ length: 4 }).map((_, i) => <SkeletonRow key={i} />)}
              </div>
            ) : filteredRecords.length === 0 ? (
              <div className="h-full flex flex-col items-center justify-center border border-dashed border-white/5 rounded-3xl p-8 opacity-20 text-slate-500">
                <AlertCircle size={32} className="mb-2"/>
                <span className="text-[10px] font-bold uppercase tracking-widest">No matching record files found.</span>
              </div>
            ) : (
              filteredRecords.map((track) => (
                <div key={track.id} className={`p-4 rounded-2xl border transition-all flex flex-col gap-3 ${activePlayback?.id === track.id ? 'bg-emerald-500/5 border-emerald-500/30' : 'bg-black/20 border-white/5 hover:border-white/10'}`}>
                  <div className="flex justify-between items-start gap-2">
                    <div className="min-w-0">
                      <h4 className="text-xs font-bold text-white truncate font-mono">{track.filename}</h4>
                      <span className="text-[9px] font-mono text-slate-500 block mt-1">Logged: {track.recorded_at} // Length: {track.duration}</span>
                    </div>
                    <button title="Play Track Stream" onClick={() => setActivePlayback(track)} className="p-2 bg-emerald-500 text-black rounded-xl hover:bg-emerald-400 transition-all shadow-md shrink-0"><Play size={12}/></button>
                  </div>

                  <div className="bg-black/40 p-3 rounded-xl border border-white/5 flex flex-col gap-2">
                    {editingId === track.id ? (
                      <div className="flex flex-col gap-2">
                        <textarea title="Modify Notes" placeholder="Enter custom notes..." value={editNotes} onChange={(e) => setEditNotes(e.target.value)} className="w-full bg-[#1a1e26] border border-white/10 text-[11px] text-white rounded-lg p-2 focus:border-emerald-500 outline-none font-sans" rows={2} />
                        <div className="flex justify-end gap-1.5">
                          <button onClick={() => setEditingId(null)} className="px-2.5 py-1 border border-white/5 text-[9px] text-slate-400 uppercase font-bold rounded hover:bg-white/5">Cancel</button>
                          <button onClick={() => handleUpdateNotes(track.id)} className="px-2.5 py-1 bg-emerald-500 text-black text-[9px] uppercase font-bold rounded hover:bg-emerald-400 flex items-center gap-1"><Save size={10}/>Save</button>
                        </div>
                      </div>
                    ) : (
                      <div className="flex justify-between items-start gap-4">
                        <p className="text-[11px] text-slate-400 font-sans italic">{track.notes || "No metadata descriptive log notes filled."}</p>
                        <button title="Modify Description" onClick={() => { setEditingId(track.id); setEditNotes(track.notes); }} className="text-slate-500 hover:text-emerald-400 transition-all shrink-0"><Edit3 size={12}/></button>
                      </div>
                    )}
                  </div>
                </div>
              ))
            )}
          </div>

        </div>
      </div>
    </div>
  );
}