"use client";

import React, { useState, useEffect } from 'react';
import { 
  MapPin, X, CheckCircle2, AlertCircle, Calendar, 
  ListFilter, ArrowUpDown, FileText, Search, ShieldCheck
} from 'lucide-react';

export default function HistoryView() {
  const [historyRecords, setHistoryRecords] = useState<any[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  
  const [dateFilter, setDateFilter] = useState("");
  const [typeFilter, setTypeFilter] = useState("ALL");
  const [searchQuery, setSearchQuery] = useState("");
  const [sortDescending, setSortDescending] = useState(true);

  useEffect(() => {
    const loadLogs = async () => {
      try {
        const res = await fetch("http://localhost:8000/api/incidents");
        if (res.ok) {
          const data = await res.json();
          const processedHistory = data.filter((inc: any) => inc.status !== 'Active');
          setHistoryRecords(processedHistory);
        }
      } catch (err) {
        // FIXED: Changed window.print call to standard console.error execution
        console.error("Could not sync database rows:", err);
      } finally {
        setIsLoading(false);
      }
    };
    loadLogs();
  }, []);

  const processedData = historyRecords
    .filter(record => {
      if (dateFilter && record.date !== dateFilter) return false;
      if (typeFilter !== 'ALL' && record.type !== typeFilter) return false;
      if (searchQuery && !record.narrative.toLowerCase().includes(searchQuery.toLowerCase()) && !record.caseId.toLowerCase().includes(searchQuery.toLowerCase())) return false;
      return true;
    })
    .sort((a, b) => {
      const timeA = new Date(`${a.date}T${a.militaryTime}`).getTime();
      const timeB = new Date(`${b.date}T${b.militaryTime}`).getTime();
      return sortDescending ? timeB - timeA : timeA - timeB;
    });

  return (
    <div className="bg-[#11141b] border border-white/5 rounded-[2.5rem] p-8 shadow-2xl h-full flex flex-col min-h-[500px] animate-in fade-in duration-300 w-full">
      
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4 pb-6 border-b border-white/5 mb-6 items-center">
        <div className="col-span-1">
          <h3 className="text-sm font-bold uppercase tracking-[0.15em] text-white">Incident File Archives</h3>
          <p className="text-[9px] font-mono text-slate-500 uppercase mt-0.5">Documents Ledger // Read Access Locked</p>
        </div>

        <div className="col-span-3 flex flex-wrap md:flex-nowrap gap-3 items-center justify-end">
          <div className="relative w-full max-w-xs">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-600" size={14} />
            <input title="Search File Records" placeholder="Search Narrative / Case ID..." value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)} className="bg-black/30 border border-white/5 rounded-xl pl-9 pr-4 py-2 text-[11px] text-white outline-none focus:border-emerald-500 w-full" />
          </div>

          <div className="flex items-center gap-1.5 bg-black/30 border border-white/5 rounded-xl px-3 py-2">
            <Calendar size={13} className="text-slate-500"/>
            <input type="date" title="Select Exact Date Log Filter" value={dateFilter} onChange={(e) => setDateFilter(e.target.value)} className="bg-transparent text-[11px] text-slate-300 font-mono outline-none cursor-pointer" />
          </div>

          <div className="flex items-center gap-1.5 bg-black/30 border border-white/5 rounded-xl px-3 py-2">
            <ListFilter size={13} className="text-slate-500"/>
            <select title="Filter Threat Classification Categories" value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)} className="bg-transparent text-[11px] text-slate-300 font-mono outline-none cursor-pointer">
              <option value="ALL">All Manifests</option>
              <option value="ASSAULT">Assault Signatures</option>
              <option value="VIOLENCE">Violence Regions</option>
              <option value="FIREARM_DETECTION">Firearm Detects</option>
              <option value="MANUAL_PANIC">Panic Triggers</option>
            </select>
          </div>

          <button title="Toggle Timeline Chronology Direction" onClick={() => setSortDescending(!sortDescending)} className="p-2 bg-black/40 border border-white/5 rounded-xl text-slate-400 hover:text-emerald-400 active:scale-95 transition-all flex items-center gap-1 text-[11px] font-bold uppercase shrink-0">
            <ArrowUpDown size={14}/> {sortDescending ? "Newest" : "Oldest"}
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto pr-1 custom-scrollbar space-y-3">
        {isLoading ? (
          <div className="h-48 flex items-center justify-center font-mono text-[10px] uppercase text-slate-500 tracking-widest animate-pulse">Querying storage index nodes...</div>
        ) : processedData.length === 0 ? (
          <div className="h-48 flex flex-col items-center justify-center opacity-20 border-2 border-dashed border-white/5 rounded-3xl p-8 text-slate-500">
            <FileText size={32} className="mb-2" />
            <span className="text-[10px] font-bold uppercase tracking-widest">No structural matching documents recorded.</span>
          </div>
        ) : (
          processedData.map((record) => (
            <div key={record.id} className="p-5 bg-black/20 border border-white/5 rounded-2xl flex flex-col md:flex-row items-start md:items-center justify-between gap-4 group hover:border-white/10 transition-all shadow-md">
              <div className="space-y-2 flex-1 min-w-0">
                <div className="flex items-center gap-3">
                  <span className="px-2 py-0.5 bg-white/5 border border-white/10 text-white rounded text-[9px] font-mono tabular-nums tracking-wider">{record.caseId}</span>
                  <h4 className="text-sm font-bold uppercase text-slate-200 tracking-tight truncate">{record.type}</h4>
                </div>
                <p className="text-[11px] text-slate-400 font-sans leading-relaxed">{record.narrative}</p>
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[10px] font-mono text-slate-500 italic">
                  <span className="flex items-center gap-1"><MapPin size={12} className="text-emerald-500" />{record.locationName}</span>
                  <span>Date Tracked: {record.date}</span>
                  <span className="tabular-nums">Time: {record.militaryTime}</span>
                </div>
              </div>

              <div className="shrink-0 flex items-center gap-2 w-full md:w-auto justify-end border-t md:border-t-0 pt-3 md:pt-0 border-white/5">
                {record.confidence && (
                  <span className="px-2.5 py-1.5 rounded-xl text-[9px] font-bold uppercase bg-white/5 border border-white/10 text-emerald-400 font-mono tracking-wider flex items-center gap-1">
                    <ShieldCheck size={11}/> Conf: {(record.confidence * 100).toFixed(1)}%
                  </span>
                )}
                <span className={`px-3 py-1.5 rounded-xl text-[9px] font-bold uppercase tracking-widest flex items-center gap-1.5 font-mono ${
                  record.status === 'Confirmed' 
                    ? 'bg-emerald-500/10 border border-emerald-500/20 text-emerald-400' 
                    : 'bg-red-500/10 border border-red-500/20 text-red-400'
                }`}>
                  {record.status === 'Confirmed' ? (
                    <CheckCircle2 size={12} className="text-emerald-500" />
                  ) : (
                    <X size={12} className="text-red-500" />
                  )}
                  {record.status === 'Confirmed' ? 'ACCEPTED' : 'IGNORED'}
                </span>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}