"use client";

import React, { useState, useEffect, useRef } from 'react';
import { 
  X, MapPin, PlusCircle, ShieldCheck, 
  FileText, Info, Search, ChevronDown, Check
} from 'lucide-react';

type Incident = {
  id: string; caseId: string; type: string; officer: string;
  lat: number; lng: number; locationName: string;
  severity: string; date: string; militaryTime: string;
  narrative: string; natureOfCall: string; arrivalReason: string;
  additionalOfficers: string; status: string;
};

const ORMOC_LOCATIONS = [
  { name: "Cogon Combado (Central)", lat: 11.0176, lng: 124.6031 },
  { name: "Brgy. Cogon Hall", lat: 11.0182, lng: 124.6025 },
  { name: "OCPD Station 1 (Cogon)", lat: 11.0163, lng: 124.6045 },
  { name: "District 18 (Cogon North)", lat: 11.0145, lng: 124.6055 },
  { name: "Purok Dahlia (Cogon)", lat: 11.0195, lng: 124.6035 }
];

export default function CrimeReportsView({ onUpdate }: { onUpdate: () => void }) {
  const [selected, setSelected] = useState<Incident | null>(null);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [isAddMode, setIsAddMode] = useState(false);
  const [showAddModal, setShowAddModal] = useState(false);
  const [pendingCoords, setPendingCoords] = useState<{ lat: number, lng: number } | null>(null);
  const [formData, setFormData] = useState({ type: 'General Disturbance', officer: '', narrative: '', natureOfCall: '', arrivalReason: '', additionalOfficers: '' });

  const mapRef = useRef<any>(null);
  const mapContainerRef = useRef<HTMLDivElement>(null);
  const markersRef = useRef<any[]>([]);
  const isAddModeRef = useRef(isAddMode);

  useEffect(() => { isAddModeRef.current = isAddMode; }, [isAddMode]);

  const fetchIncidents = async () => {
    try {
      const res = await fetch("http://localhost:8000/api/incidents");
      const data = await res.json();
      setIncidents(data);
    } catch (e) { console.error("SQL Offline"); }
  };

  // --- LIVE PIN REFRESH LOGIC ---
  const refreshMarkers = () => {
    const L = (window as any).L;
    if (!L || !mapRef.current) return;

    // Clear existing
    markersRef.current.forEach(m => m.remove());
    markersRef.current = [];

    // Re-draw based on current state
    incidents.forEach(inc => {
      const markerIcon = L.divIcon({
        className: 'custom-div-icon',
        // NO BLINKING (Removed animate-pulse)
        html: `<div class="w-6 h-6 bg-[#1e293b] rounded-full border-2 border-emerald-500 shadow-xl flex items-center justify-center text-[10px] text-emerald-500 font-bold">!</div>`,
        iconSize: [24, 24], iconAnchor: [12, 12]
      });
      const m = L.marker([inc.lat, inc.lng], { icon: markerIcon }).addTo(mapRef.current)
        .on('click', (e: any) => { L.DomEvent.stopPropagation(e); setSelected(inc); });
      markersRef.current.push(m);
    });
  };

  // Trigger marker refresh every time the incidents list changes
  useEffect(() => {
    refreshMarkers();
  }, [incidents]);

  useEffect(() => { fetchIncidents(); }, []);

  useEffect(() => {
    if (!document.getElementById('leaflet-css')) {
      const link = document.createElement('link'); link.id = 'leaflet-css';
      link.rel = 'stylesheet'; link.href = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';
      document.head.appendChild(link);
    }
    const script = document.createElement('script');
    script.src = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js';
    script.async = true;
    script.onload = () => {
      const L = (window as any).L;
      if (!mapRef.current && mapContainerRef.current) {
        mapRef.current = L.map(mapContainerRef.current, { center: [11.0176, 124.6031], zoom: 17, zoomControl: false, attributionControl: false });
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19 }).addTo(mapRef.current);
        
        mapRef.current.on('click', (e: any) => {
          if (isAddModeRef.current) {
            setPendingCoords({ lat: e.latlng.lat, lng: e.latlng.lng });
            setShowAddModal(true);
            setIsAddMode(false);
          }
        });
      }
    };
    document.body.appendChild(script);
  }, []);

  const handleConfirmAdd = async () => {
    if (!pendingCoords || !formData.officer) return;
    const now = new Date();
    const newIncident: Incident = {
      id: Date.now().toString(),
      caseId: `ORM-${Math.floor(100000 + Math.random() * 900000)}`,
      type: formData.type, officer: formData.officer,
      lat: pendingCoords.lat, lng: pendingCoords.lng,
      locationName: `Purok Cogon // ${pendingCoords.lat.toFixed(4)}N`,
      severity: "MEDIUM",
      date: now.toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' }),
      militaryTime: now.getHours().toString().padStart(2, '0') + now.getMinutes().toString().padStart(2, '0'),
      narrative: formData.narrative, natureOfCall: formData.natureOfCall || "Routine Surveillance",
      arrivalReason: formData.arrivalReason || "Area Patrol",
      additionalOfficers: formData.additionalOfficers || "None", status: 'PENDING'
    };

    const res = await fetch("http://localhost:8000/api/incidents", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(newIncident),
    });

    if (res.ok) { 
      setIncidents(prev => [newIncident, ...prev]); // LIVE ADD
      setShowAddModal(false); 
      setPendingCoords(null); 
      onUpdate(); // REFRESH SIDEBAR COUNT
    }
  };

  const handleExpunge = async () => {
    if (!selected) return;
    const res = await fetch(`http://localhost:8000/api/incidents/${selected.id}`, { method: 'DELETE' });
    if (res.ok) {
       setIncidents(prev => prev.filter(i => i.id !== selected.id)); // LIVE DELETE
       setSelected(null);
       onUpdate(); // REFRESH SIDEBAR COUNT
    }
  };

  return (
    <div className="flex h-full gap-4 animate-in fade-in duration-500 relative">
      <style>{`
        .leaflet-container { background: #f1f5f9 !important; outline: none !important; cursor: ${isAddMode ? 'crosshair' : 'grab'} !important; }
        .leaflet-tile { filter: contrast(1.05) saturate(1.1) !important; margin: -1px !important; padding: 1px !important; }
        select option { background-color: #0f172a !important; color: #10b981 !important; padding: 10px; }
        select { color-scheme: dark; background-color: #0f172a !important; color: white !important; }
      `}</style>

      <div className="flex-1 bg-[#f1f5f9] border border-white/5 rounded-[2.5rem] relative overflow-hidden shadow-2xl">
        <div ref={mapContainerRef} className="w-full h-full z-0" />
        <div className="absolute top-8 left-8 z-20 space-y-3 pointer-events-auto">
          <div className="p-4 bg-[#0f172a]/90 backdrop-blur-xl border border-white/10 rounded-2xl shadow-2xl text-slate-200">
            <div className="flex items-center gap-2 mb-1"><ShieldCheck className="w-4 h-4 text-emerald-500" /><h3 className="text-[10px] font-bold uppercase tracking-[0.2em]">Cogon Command</h3></div>
            <div className="text-[8px] text-slate-400 font-mono uppercase flex items-center gap-2"><div className="w-1.5 h-1.5 bg-emerald-500 rounded-full" /> Grid Active</div>
          </div>
          <button onClick={() => setIsAddMode(!isAddMode)} title={isAddMode ? "Cancel Entry" : "New Entry"} aria-label={isAddMode ? "Cancel Entry" : "New Entry"} className={`flex items-center gap-3 px-5 py-3.5 rounded-xl font-bold text-[10px] uppercase shadow-xl transition-all ${isAddMode ? 'bg-red-500 text-white animate-pulse' : 'bg-emerald-600 text-white hover:bg-emerald-500'}`}>
            {isAddMode ? <X size={14} /> : <PlusCircle size={14} />} {isAddMode ? 'Abort Protocol' : 'New Incident Entry'}
          </button>
        </div>

        <div className="absolute top-8 right-8 z-20 space-y-3 pointer-events-auto">
          <div className="relative group w-80">
            <div className="absolute inset-y-0 left-4 flex items-center pointer-events-none text-emerald-500"><MapPin size={16} /></div>
            <select title="Quick Jump" defaultValue="placeholder" onChange={(e) => { const loc = ORMOC_LOCATIONS.find(l => l.name === e.target.value); if (loc) mapRef.current.flyTo([loc.lat, loc.lng], 18); }} className="w-full bg-[#0f172a]/95 backdrop-blur-xl border border-white/10 rounded-2xl p-4 pl-12 text-xs text-white outline-none appearance-none font-bold uppercase cursor-pointer">
              <option value="placeholder" disabled>Neighborhood Quick-Jump</option>
              {ORMOC_LOCATIONS.map((loc) => (<option key={loc.name} value={loc.name}>{loc.name}</option>))}
            </select>
            <div className="absolute inset-y-0 right-4 flex items-center pointer-events-none text-slate-500"><ChevronDown size={16} /></div>
          </div>
        </div>
      </div>

      <div className="w-96 bg-[#0a0c10] border border-white/5 rounded-[2.5rem] flex flex-col overflow-hidden shadow-2xl z-20 text-slate-200">
        <div className="p-6 border-b border-white/5 flex justify-between items-center bg-white/[0.02]">
          <h3 className="text-[11px] font-bold uppercase tracking-[0.2em] text-slate-400 flex items-center gap-2"><FileText size={14} className="text-emerald-500" /> SIR_ARCHIVES</h3>
          {selected && <button title="Close" onClick={() => setSelected(null)} className="p-1 hover:bg-white/5 rounded-lg"><X size={14} /></button>}
        </div>
        <div className="flex-1 overflow-y-auto p-6 custom-scrollbar bg-[#0d0f14]">
          {selected ? (
            <div className="animate-in slide-in-from-right duration-300 space-y-6">
              <div className="border-b-2 border-emerald-500/30 pb-4">
                <div className="flex justify-between items-center mb-1"><span className="text-emerald-500 font-mono text-sm font-bold tracking-tighter">{selected.caseId}</span><div className="bg-emerald-500/10 text-emerald-400 text-[8px] px-2 py-0.5 rounded border border-emerald-500/20 font-black uppercase">Official Record</div></div>
                <div className="grid grid-cols-2 text-[10px] font-mono text-slate-500"><span>Date: {selected.date}</span><span className="text-right">Time: {selected.militaryTime} HRS</span></div>
              </div>
              <div className="space-y-4">
                <div className="space-y-1 bg-white/[0.02] p-3 rounded-lg border border-white/5"><p className="text-[9px] font-bold text-slate-500 uppercase tracking-widest flex items-center gap-2"><MapPin size={10}/> Establish Scene</p><p className="text-[11px] text-slate-200 font-mono">{selected.locationName}</p></div>
                <div className="grid grid-cols-2 gap-3 text-[10px]">
                  <div><p className="text-[9px] text-slate-500 uppercase">Officer</p><p className="font-bold text-white uppercase">{selected.officer}</p></div>
                  <div className="text-right"><p className="text-[9px] text-slate-500 uppercase">Type</p><p className="font-bold text-white uppercase">{selected.type}</p></div>
                </div>
              </div>
              <div className="space-y-2 pt-2"><div className="flex items-center gap-2 text-slate-500 uppercase font-bold text-[9px] tracking-widest"><Info size={12} /> Narrative Statement</div><div className="p-5 rounded-2xl bg-black/40 border border-white/5 shadow-inner"><p className="text-[11px] text-slate-300 leading-relaxed font-serif tracking-wide">"{selected.narrative}"</p></div></div>
              <button title="Expunge" onClick={handleExpunge} className="w-full py-4 bg-red-950/20 border border-red-500/30 text-red-500 text-[9px] font-bold uppercase rounded-xl hover:bg-red-500 transition-all shadow-lg">Expunge From SQL Database</button>
            </div>
          ) : (
            <div className="h-full flex flex-col items-center justify-center text-center opacity-20 mt-20"><MapPin size={40} className="mb-4 text-emerald-500" /><p className="text-[9px] font-bold uppercase tracking-[0.2em]">Select active node to load SQL data</p></div>
          )}
        </div>
      </div>

      {showAddModal && (
        <div className="absolute inset-0 z-[100] flex items-center justify-center p-8 bg-black/90 backdrop-blur-md">
          <div className="bg-[#0f172a] border border-white/10 rounded-[3rem] p-12 w-full max-w-4xl shadow-2xl animate-in zoom-in-95 text-slate-200">
            <div className="flex justify-between items-center mb-10 pb-6 border-b border-white/5">
              <div className="flex items-center gap-4"><div className="p-3 bg-emerald-500/10 rounded-2xl text-emerald-500"><FileText size={28}/></div><div><h2 className="text-xl font-bold uppercase tracking-[0.3em]">Statement of Record</h2><p className="text-[10px] text-slate-500 font-mono">OCPD // SURVEILLANCE PROTOCOL v4.3</p></div></div>
              <button title="Close" onClick={() => setShowAddModal(false)}><X size={24} /></button>
            </div>
            <div className="space-y-6">
              <div className="grid grid-cols-3 gap-6">
                <input title="Officer Name" placeholder="Primary Officer" onChange={(e) => setFormData({...formData, officer: e.target.value})} className="w-full bg-white/[0.03] border border-white/5 rounded-xl p-4 text-sm outline-none focus:border-emerald-500 font-mono" />
                <select title="Classification" defaultValue="General Disturbance" onChange={(e) => setFormData({...formData, type: e.target.value})} className="w-full bg-slate-900 border border-white/10 rounded-xl p-4 text-sm outline-none cursor-pointer">
                  <option value="General Disturbance">General Disturbance</option><option value="Physical Altercation">Physical Altercation</option><option value="Theft">Theft / Larceny</option>
                </select>
                <input title="Nature" placeholder="Nature of Call" onChange={(e) => setFormData({...formData, natureOfCall: e.target.value})} className="w-full bg-white/[0.03] border border-white/5 rounded-xl p-4 text-sm outline-none focus:border-emerald-500" />
              </div>
              <textarea title="Narrative" rows={5} onChange={(e) => setFormData({...formData, narrative: e.target.value})} className="w-full bg-white/[0.03] border border-white/5 rounded-2xl p-6 text-sm outline-none focus:border-emerald-500 font-sans" placeholder="Detailed Observation Statement..." />
              <button onClick={handleConfirmAdd} className="w-full py-5 bg-emerald-600 text-white rounded-2xl font-black uppercase text-xs tracking-[0.4em] hover:bg-emerald-500 shadow-2xl flex items-center justify-center gap-2">
                <Check size={20} /> Log Document to SIR_Archives
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}