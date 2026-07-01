"use client";

import React, { useState, useEffect, useRef } from 'react';
import { 
  X, MapPin, ShieldCheck, Trash2, Plus,
  Info, AlertCircle, FileSignature, FileText,
  Calendar, ListFilter, ShieldAlert, Radio, Check, Video, ArrowLeft, Globe, ImageIcon
} from 'lucide-react';

type SmartpoleNode = {
  id: string; name: string; street: string; lat: number; lng: number;
};

const SMARTPOLE_LOCATIONS: SmartpoleNode[] = [
  { id: 'sp1', name: 'Cogon Core Smartpole Node', street: 'Cogon Combado (Central Grid)', lat: 11.0176, lng: 124.6031 },
  { id: 'sp2', name: 'Sector B Gate Smartpole Node', street: 'Brgy. Cogon Hall Boundary', lat: 11.0182, lng: 124.6025 },
  { id: 'sp3', name: 'North Uplink Smartpole Node', street: 'District 18 (Cogon North Terminal)', lat: 11.0145, lng: 124.6055 }
];

const SAMPLE_REPORTS = [
  {
    id: 'sample-sp1', caseId: 'CASE-C019AA60', type: 'ASSAULT', officer: 'AI_SENTINEL',
    lat: 11.0176, lng: 124.6031, locationName: 'Cogon Core Smartpole Node',
    severity: 'CRITICAL', date: '2026-06-01', militaryTime: '0552',
    narrative: 'Automated neural detection of ASSAULT.',
    natureOfCall: 'AI Threat Flag', arrivalReason: 'Automated Tracking',
    additionalOfficers: 'None', status: 'PENDING', screenshotPath: ''
  },
  {
    id: 'sample-sp2', caseId: 'CASE-B882AC11', type: 'THEFT', officer: 'AI_SENTINEL',
    lat: 11.0182, lng: 124.6025, locationName: 'Sector B Gate Smartpole Node',
    severity: 'MEDIUM', date: '2026-06-02', militaryTime: '1114',
    narrative: 'Automated neural detection of Theft / Larceny.',
    natureOfCall: 'AI Threat Flag', arrivalReason: 'Automated Tracking',
    additionalOfficers: 'None', status: 'Confirmed', screenshotPath: ''
  },
  {
    id: 'sample-sp3', caseId: 'CASE-N993DF44', type: 'PHYSICAL ALTERCATION', officer: 'AI_SENTINEL',
    lat: 11.0145, lng: 124.6055, locationName: 'North Uplink Smartpole Node',
    severity: 'HIGH', date: '2026-05-31', militaryTime: '0245',
    narrative: 'Automated neural detection of a Physical Altercation on public lanes.',
    natureOfCall: 'AI Threat Flag', arrivalReason: 'Automated Tracking',
    additionalOfficers: 'None', status: 'Confirmed', screenshotPath: ''
  }
];

type Incident = {
  id: string; caseId: string; type: string; officer: string;
  lat: number; lng: number; locationName: string;
  severity: string; date: string; militaryTime: string;
  narrative: string; natureOfCall: string; arrivalReason: string;
  additionalOfficers: string; status: string; screenshotPath?: string;
};

interface CrimeReportsViewProps {
  onUpdate: () => void;
  onDeepLink?: (crimeId: string) => void;
}

export default function CrimeReportsView({ onUpdate, onDeepLink }: CrimeReportsViewProps) {
  const [selectedPole, setSelectedPole] = useState<SmartpoleNode | null>(SMARTPOLE_LOCATIONS[0]);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [poleDateFilter, setPoleDateFilter] = useState("");
  const [poleTypeFilter, setPoleTypeFilter] = useState("ALL");
  const [showFilingModal, setShowFilingModal] = useState(false);
  const [filingTarget, setFilingTarget] = useState<Incident | null>(null);
  
  const [brokenImages, setBrokenImages] = useState<Record<string, boolean>>({});

  // Manual Report Creation Fields
  const [isManualFilingActive, setIsManualFilingActive] = useState(false);
  const [manualType, setFormManualType] = useState("ASSAULT");
  const [manualSeverity, setFormManualSeverity] = useState("HIGH");
  const [manualNarrative, setFormManualNarrative] = useState("");

  const [reportForm, setReportForm] = useState({
    badgeNumber: '',
    reportingOfficer: '',
    precinctSector: 'Ormoc Station 1',
    weatherCondition: 'Clear Night',
    lightingCondition: 'Artificial Streetlights',
    victimDetails: 'State Witnesses / Public Property',
    suspectDetails: 'Unknown Subject (Fled Scene)',
    propertyDamaged: 'None Reported',
    evidenceRecovered: 'Digital AI Surveillance Recording Stream',
    finalDisposition: 'Pending Criminal Case Referral to Prosecutors',
    supervisorApproval: ''
  });

  const mapRef = useRef<any>(null);
  const mapContainerRef = useRef<HTMLDivElement>(null);
  const poleMarkersRef = useRef<Record<string, any>>({});
  const incidentMarkersRef = useRef<any[]>([]);
  const selectedPoleIdRef = useRef<string | null>(SMARTPOLE_LOCATIONS[0]?.id ?? null);

  const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

  const formatTo12Hour = (timeStr: string) => {
    if (!timeStr) return "";
    let h = 0, m = "00";
    if (timeStr.includes(":")) {
      const parts = timeStr.split(":");
      h = parseInt(parts[0], 10); m = parts[1];
    } else if (timeStr.length >= 4) {
      h = parseInt(timeStr.substring(0, 2), 10);
      m = timeStr.substring(2, 4);
    } else return timeStr;
    const ampm = h >= 12 ? "PM" : "AM";
    return `${h % 12 || 12}:${m} ${ampm}`;
  };

  const fetchIncidents = async () => {
    try {
      const res = await fetch(`${API_URL}/api/incidents`);
      const data = await res.json();
      setIncidents([...SAMPLE_REPORTS, ...data]);
    } catch {
      setIncidents(SAMPLE_REPORTS);
    }
  };

  const buildPoleIcon = (L: any, pole: SmartpoleNode, selectedId: string | null = selectedPoleIdRef.current) => {
    const isCurrentSelected = selectedId === pole.id;
    return L.divIcon({
      className: 'custom-pole-icon',
      html: `<div class="w-7 h-7 ${isCurrentSelected ? 'bg-emerald-500 text-black scale-110 ring-4 ring-emerald-500/20 font-black' : 'bg-[#0b0f17] text-emerald-400 font-bold'} rounded-full border-2 border-emerald-400 shadow-2xl flex items-center justify-center text-[10px] transition-all duration-200">📡</div>`,
      iconSize: [28, 28], iconAnchor: [14, 14]
    });
  };

  const updatePoleSelectionIcons = (newSelectedId: string) => {
    const L = (window as any).L;
    if (!L || !mapRef.current) return;
    const previousSelectedId = selectedPoleIdRef.current;
    if (previousSelectedId && poleMarkersRef.current[previousSelectedId]) {
      const previousPole = SMARTPOLE_LOCATIONS.find(p => p.id === previousSelectedId);
      if (previousPole) {
        poleMarkersRef.current[previousSelectedId].setIcon(buildPoleIcon(L, previousPole, null));
      }
    }
    const nextPole = SMARTPOLE_LOCATIONS.find(p => p.id === newSelectedId);
    if (nextPole && poleMarkersRef.current[newSelectedId]) {
      poleMarkersRef.current[newSelectedId].setIcon(buildPoleIcon(L, nextPole, newSelectedId));
    }
  };

  const refreshPoleIcons = () => {
    const L = (window as any).L;
    if (!L || !mapRef.current) return;

    SMARTPOLE_LOCATIONS.forEach(pole => {
      const marker = poleMarkersRef.current[pole.id];
      if (!marker) return;
      marker.setIcon(buildPoleIcon(L, pole));
    });
  };

  const refreshIncidentMarkers = () => {
    const L = (window as any).L;
    if (!L || !mapRef.current) return;

    incidentMarkersRef.current.forEach(m => m.remove());
    incidentMarkersRef.current = [];

    incidents.forEach(inc => {
      const icon = L.divIcon({
        className: 'custom-div-icon',
        html: `<div class="w-6 h-6 bg-[#1e293b] rounded-full border-2 border-red-500 shadow-xl flex items-center justify-center text-[10px] text-red-400 font-extrabold">!</div>`,
        iconSize: [24, 24], iconAnchor: [12, 12]
      });
      const m = L.marker([inc.lat, inc.lng], { icon }).addTo(mapRef.current);
      incidentMarkersRef.current.push(m);
    });
  };

  const createPoleMarkers = () => {
    const L = (window as any).L;
    if (!L || !mapRef.current) return;

    SMARTPOLE_LOCATIONS.forEach(pole => {
      const marker = L.marker([pole.lat, pole.lng], { icon: buildPoleIcon(L, pole) })
        .addTo(mapRef.current)
        .on('click', (e: any) => {
          L.DomEvent.stopPropagation(e);
          if (selectedPoleIdRef.current === pole.id) return;
          const oldSelectedId = selectedPoleIdRef.current;
          selectedPoleIdRef.current = pole.id;
          updatePoleSelectionIcons(pole.id);
          setSelectedPole(pole);
          setIsManualFilingActive(false);
          mapRef.current?.setView([pole.lat, pole.lng], 17, { animate: false });
        });
      poleMarkersRef.current[pole.id] = marker;
    });
  };

  useEffect(() => {
    refreshIncidentMarkers();
  }, [incidents]);

  useEffect(() => {
    refreshPoleIcons();
    selectedPoleIdRef.current = selectedPole?.id ?? null;
    if (selectedPole && mapRef.current) {
      mapRef.current.setView([selectedPole.lat, selectedPole.lng], 17, { animate: false });
    }
  }, [selectedPole]);

  useEffect(() => { fetchIncidents(); }, []);
  
  useEffect(() => {
    if (!document.getElementById('leaflet-css')) {
      const link = document.createElement('link');
      link.id = 'leaflet-css'; link.rel = 'stylesheet';
      link.href = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';
      document.head.appendChild(link);
    }
    const script = document.createElement('script');
    script.src = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js';
    script.async = true;
    script.onload = () => {
      const L = (window as any).L;
      if (!mapRef.current && mapContainerRef.current) {
        // FIXED: Explicitly applied doubleClickZoom: false to prevent background map updates from freezing user mouse focus
        mapRef.current = L.map(mapContainerRef.current, {
          center: [11.0176, 124.6031], zoom: 17, zoomControl: false, attributionControl: false, doubleClickZoom: false
        });
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19 }).addTo(mapRef.current);
        createPoleMarkers();
      }
    };
    document.body.appendChild(script);
  }, []);

  const handleExpunge = async (incidentId: string) => {
    if (!confirm("Permanently expunge this incident record file from SQL archive?")) return;
    const res = await fetch(`${API_URL}/api/incidents/${incidentId}`, { method: 'DELETE' });
    if (res.ok) { 
      setIncidents(prev => prev.filter(i => i.id !== incidentId)); 
      onUpdate(); 
    }
  };

  const handleCreateManualReport = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!manualNarrative.trim() || !selectedPole) return;

    const generatedId = Math.random().toString(36).substr(2, 8);
    const now = new Date();
    
    const payload = {
      id: generatedId,
      caseId: `CASE-${now.getFullYear()}${String(now.getMonth()+1).padStart(2,'0')}-${Math.random().toString(36).substr(2,4).toUpperCase()}`,
      type: manualType.toUpperCase(),
      officer: "MANUAL_ENTRY",
      lat: selectedPole.lat,
      lng: selectedPole.lng,
      locationName: selectedPole.name,
      severity: manualSeverity,
      date: now.toISOString().split('T')[0],
      militaryTime: now.toTimeString().split(' ')[0].replace(/:/g, '').substring(0,4),
      narrative: manualNarrative,
      natureOfCall: "Operator Manual Filing",
      arrivalReason: "Field Request",
      additionalOfficers: "None",
      status: "Active"
    };

    const res = await fetch(`${API_URL}/api/incidents`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });

    if (res.ok) {
      setFormManualNarrative("");
      setIsManualFilingActive(false);
      fetchIncidents();
      onUpdate();
    }
  };

  const handleOpenReportFiler = (target: Incident) => {
    setFilingTarget(target);
    setReportForm(prev => ({ ...prev, reportingOfficer: target.officer !== 'AI_SENTINEL' ? target.officer : '' }));
    setShowFilingModal(true);
  };

  const handleSubmitOfficialReport = async () => {
    if (!filingTarget || !reportForm.badgeNumber || !reportForm.reportingOfficer) return;
    try {
      await fetch(`${API_URL}/api/incidents/${filingTarget.id}/status`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: "Confirmed" })
      });
      setShowFilingModal(false); setFilingTarget(null);
      fetchIncidents(); onUpdate();
    } catch (e) { console.error(e); }
  };

  const closeModal = () => { setShowFilingModal(false); setFilingTarget(null); };

  const getFilteredIncidents = () => incidents.filter(inc => {
    if (selectedPole) {
      const match = inc.locationName.toLowerCase().includes(selectedPole.name.toLowerCase()) ||
                    inc.locationName.toLowerCase().includes(selectedPole.street.toLowerCase());
      if (!match) return false;
    }
    if (poleDateFilter && !inc.date.includes(poleDateFilter)) return false;
    if (poleTypeFilter !== 'ALL' && inc.type.toUpperCase() !== poleTypeFilter.toUpperCase()) return false;
    return true;
  });

  const finalLogsDisplay = getFilteredIncidents();
  const inputClass = "w-full bg-white/[0.02] border border-white/5 rounded-lg p-2.5 text-xs text-white outline-none focus:border-emerald-500 font-mono";
  const labelClass = "text-[8px] font-mono text-slate-500 uppercase block mb-1";

  return (
    <div className="flex h-full gap-4 animate-in fade-in duration-500 relative w-full">
      {/* ─── LEFT PANEL: LEAFLET TACTICAL MAP AREA ─── */}
      <div className="flex-1 bg-[#f1f5f9] border border-white/5 rounded-[2.5rem] relative overflow-hidden shadow-2xl">
        <div ref={mapContainerRef} className="w-full h-full z-0" />
      </div>

      {/* ─── RIGHT PANEL: CLEAN RESTRUCTURED REPORT RADAR SIDEBAR ─── */}
      <div className="w-96 bg-[#0a0c10] border border-white/5 rounded-[2.5rem] p-6 flex flex-col overflow-hidden shadow-2xl z-20 text-slate-200">
        <div className="animate-in fade-in duration-300 flex flex-col h-full min-h-0">
          
          {/* HEADER LAYER: Back Navigation button left, Add Report trigger button right */}
          <div className="pb-4 border-b border-white/5 mb-4 flex justify-between items-center shrink-0">
            {/* FIXED: Back button resets the selected pole context cleanly to bring back the global overview list instantly */}
            <button
              title="Return to unfiltered dashboard stream overview"
              onClick={() => { setSelectedPole(null); setIsManualFilingActive(false); }}
              className="flex items-center gap-1.5 text-[10px] font-mono font-bold uppercase tracking-wider text-slate-400 hover:text-emerald-400 transition-colors"
            >
              <ArrowLeft size={13} className="stroke-[2.5]"/>
              <span>Back</span>
            </button>

            <div className="text-center min-w-0 px-2 flex-1">
              <h4 className="text-[10px] font-black text-white uppercase tracking-wider truncate flex items-center justify-center gap-1">
                {selectedPole ? <Radio size={11} className="text-emerald-400 animate-pulse" /> : <Globe size={11} className="text-teal-400" />}
                <span>{selectedPole ? selectedPole.name : 'Global Inbound Feed'}</span>
              </h4>
              <p className="text-[8px] font-mono text-slate-500 uppercase truncate">
                {selectedPole ? selectedPole.street : 'Monitoring All Sector Streets'}
              </p>
            </div>

            <button
              onClick={() => selectedPole && setIsManualFilingActive(!isManualFilingActive)}
              disabled={!selectedPole}
              className="text-[9px] font-mono font-black text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 px-2.5 py-1 rounded hover:bg-emerald-500/20 uppercase tracking-wider disabled:opacity-20 transition-all shrink-0"
            >
              {isManualFilingActive ? 'Cancel' : '+ Add Report'}
            </button>
          </div>

          {/* FORM CONTAINER: MANUAL DISPATCH REPORT ENTRY GENERATOR */}
          {isManualFilingActive && selectedPole && (
            <form onSubmit={handleCreateManualReport} className="p-4 bg-white/[0.02] border border-white/5 rounded-2xl mb-4 space-y-3 animate-in slide-in-from-top duration-300 shrink-0">
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <span className={labelClass}>Classification</span>
                  <select title="Select incident classification" value={manualType} onChange={(e) => setFormManualType(e.target.value)} className="w-full bg-[#0e121a] border border-white/5 rounded-lg p-2 text-[10px] text-white outline-none">
                    <option value="ASSAULT">Assault</option>
                    <option value="THEFT">Theft</option>
                    <option value="PHYSICAL ALTERCATION">Altercation</option>
                    <option value="VANDALISM">Vandalism</option>
                  </select>
                </div>
                <div>
                  <span className={labelClass}>Severity</span>
                  <select title="Select threat severity scale" value={manualSeverity} onChange={(e) => setFormManualSeverity(e.target.value)} className="w-full bg-[#0e121a] border border-white/5 rounded-lg p-2 text-[10px] text-white outline-none">
                    <option value="LOW">Low</option>
                    <option value="MEDIUM">Medium</option>
                    <option value="HIGH">High</option>
                    <option value="CRITICAL">Critical</option>
                  </select>
                </div>
              </div>
              <div>
                <span className={labelClass}>Narrative Statement</span>
                <textarea 
                  value={manualNarrative} 
                  onChange={(e) => setFormManualNarrative(e.target.value)} 
                  placeholder="Enter manual police filing dispatch entry observations..." 
                  className="w-full h-14 bg-[#0e121a] border border-white/5 rounded-lg p-2 text-[10px] text-white resize-none outline-none focus:border-emerald-500"
                />
              </div>
              <button type="submit" className="w-full py-2 bg-emerald-500 hover:bg-emerald-400 text-black text-[9px] font-black uppercase tracking-wider rounded-lg transition-colors">
                File Manual Entry
              </button>
            </form>
          )}

          {/* CRIME TIMELINE SYSTEM INLINE FILTERS */}
          <div className="grid grid-cols-2 gap-2 mb-4 shrink-0">
            <div className="flex items-center gap-1 bg-black/40 border border-white/5 rounded-xl px-2.5 py-1.5">
              <Calendar size={12} className="text-slate-500 shrink-0"/>
              <input
                type="text"
                title="Filter incidents by date"
                placeholder="YYYY-MM-DD"
                value={poleDateFilter}
                onChange={(e) => setPoleDateFilter(e.target.value)}
                className="bg-transparent text-[10px] text-slate-300 font-mono outline-none w-full border-none p-0"
              />
            </div>
            <div className="flex items-center gap-1 bg-black/40 border border-white/5 rounded-xl px-2 py-1.5">
              <ListFilter size={12} className="text-slate-500 shrink-0"/>
              <select
                title="Filter incidents by crime type"
                value={poleTypeFilter}
                onChange={(e) => setPoleTypeFilter(e.target.value)}
                className="bg-transparent text-[10px] text-slate-300 font-mono outline-none w-full cursor-pointer border-none p-0 h-4"
              >
                <option value="ALL">All Crimes</option>
                <option value="ASSAULT">Assault</option>
                <option value="THEFT">Theft</option>
                <option value="PHYSICAL ALTERCATION">Altercation</option>
                <option value="VANDALISM">Vandalism</option>
              </select>
            </div>
          </div>

          {/* ─── TIMELINE REPORT CARD LEDGER FEED ─── */}
          <div className="flex-1 overflow-y-auto space-y-3 bg-black/10 p-2 rounded-xl custom-scrollbar min-h-0">
            {finalLogsDisplay.length === 0 ? (
              <div className="h-full flex flex-col items-center justify-center opacity-30 py-12 text-center">
                <AlertCircle size={20} className="mb-2 text-slate-500"/>
                <span className="text-[9px] font-bold uppercase tracking-widest font-mono">No Incident History</span>
              </div>
            ) : (
              finalLogsDisplay.map(inc => {
                const isImageBroken = brokenImages[inc.id];
                return (
                  <div key={inc.id} className="w-full text-left p-5 bg-[#0a0d14] border border-white/[0.04] rounded-xl flex flex-col gap-2 hover:border-white/10 transition-all relative">
                    
                    {/* Top Row Header Block */}
                    <div className="flex justify-between items-center text-[10px] font-mono">
                      <span className="text-emerald-400 font-bold select-all">{inc.caseId}</span>
                      <span className="text-slate-500 tabular-nums">{formatTo12Hour(inc.militaryTime)}</span>
                    </div>

                    {/* Threat Class Title */}
                    <h5 className="text-[14px] font-black uppercase text-slate-200 tracking-wide mt-0.5">{inc.type}</h5>
                    
                    {/* Scene Snapshot Preview Image Box */}
                    {inc.screenshotPath && !isImageBroken ? (
                      <div className="w-full h-24 bg-black border border-white/5 rounded-lg overflow-hidden relative shadow-inner mt-1">
                        <img 
                          src={inc.screenshotPath.startsWith('http') ? inc.screenshotPath : `${API_URL}${inc.screenshotPath}`} 
                          className="w-full h-full object-cover" 
                          alt="AI Camera Log Snap" 
                          onError={() => setBrokenImages(prev => ({ ...prev, [inc.id]: true }))}
                        />
                      </div>
                    ) : inc.screenshotPath ? (
                      <div className="w-full h-20 bg-white/[0.02] border border-white/5 border-dashed rounded-lg mt-1 flex flex-col items-center justify-center text-slate-600 gap-1 animate-in fade-in">
                        <AlertCircle size={14} className="text-slate-500" />
                        <span className="text-[8px] font-mono font-bold uppercase tracking-wider">Scene Image Missing (404)</span>
                      </div>
                    ) : null}

                    {/* Narrative Text Quote */}
                    <p className="text-[10px] text-slate-400 mt-1 select-text">"{inc.narrative}"</p>
                    
                    {/* Action Footer Block */}
                    <div className="flex gap-2 pt-2 mt-1 border-t border-white/5 justify-end items-center text-[10px] uppercase tracking-wider font-extrabold">
                      <div className="flex items-center gap-3">
                        <button
                          title={`Generate official incident report form for case ${inc.caseId}`}
                          onClick={() => handleOpenReportFiler(inc)}
                          className="text-emerald-400 hover:text-emerald-300 flex items-center gap-1.5 font-black"
                        >
                          <FileSignature size={12}/> Generate Police Report
                        </button>
                        
                        <button
                          onClick={() => handleExpunge(inc.id)}
                          title="Delete log record sheet"
                          className="p-1 text-slate-600 hover:text-rose-400 transition-colors rounded"
                        >
                          <Trash2 size={12} />
                        </button>
                      </div>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>
      </div>

      {/* FILING MODAL */}
      {showFilingModal && filingTarget && (
        <div className="fixed inset-0 z-[120] flex items-center justify-center p-4 bg-black/95 backdrop-blur-md animate-in fade-in duration-300">
          <div className="bg-[#0b0f19] border border-white/10 rounded-2xl w-full max-w-3xl max-h-[90vh] overflow-y-auto shadow-2xl text-slate-200">

            {/* MODAL HEADER */}
            <div className="sticky top-0 bg-[#0b0f19] z-10 flex justify-between items-center px-6 py-4 border-b border-white/5">
              <div className="flex items-center gap-3">
                <div className="p-2 bg-emerald-500/10 rounded-xl text-emerald-500"><FileSignature size={20}/></div>
                <div>
                  <h2 className="text-sm font-bold uppercase tracking-widest text-white">Official Police Incident Report</h2>
                  <p className="text-[9px] text-slate-500 font-mono uppercase">Republic of the Philippines // Ormoc Police District</p>
                </div>
              </div>
              <button
                title="Close report filing modal"
                onClick={closeModal}
                className="p-2 bg-white/5 hover:bg-white/10 text-slate-400 hover:text-white rounded-full transition-all"
              >
                <X size={18}/>
              </button>
            </div>

            <div className="p-6 space-y-5">

              {/* CASE META */}
              <div className="grid grid-cols-3 gap-4 bg-black/20 p-4 rounded-xl border border-white/5 font-mono text-xs text-slate-400">
                <div><span className={labelClass}>Case ID</span><span className="text-emerald-500 font-bold">{filingTarget.caseId}</span></div>
                <div><span className={labelClass}>Type</span><span className="text-white font-bold uppercase">{filingTarget.type}</span></div>
                <div><span className={labelClass}>Timestamp</span><span className="text-slate-300">{filingTarget.date} {formatTo12Hour(filingTarget.militaryTime)}</span></div>
              </div>

              {/* Forensic Evidence Photo Embedded Inside Form */}
              {filingTarget.screenshotPath && (
                <div className="space-y-2 animate-in fade-in duration-300">
                  <h4 className="text-[10px] font-bold text-slate-500 uppercase tracking-widest flex items-center gap-2">
                    <Video size={11} className="text-emerald-500"/> Secured Scene Forensic Evidence Snapshot
                  </h4>
                  {!brokenImages[filingTarget.id] ? (
                    <div className="w-full max-h-72 bg-black rounded-xl overflow-hidden border border-white/10 shadow-2xl relative flex items-center justify-center">
                      <img 
                        src={filingTarget.screenshotPath.startsWith('http') ? filingTarget.screenshotPath : `${API_URL}${filingTarget.screenshotPath}`} 
                        className="w-full h-full object-contain max-h-72" 
                        alt="AI Visual Evidence Log Data"
                        onError={() => setBrokenImages(prev => ({ ...prev, [filingTarget.id]: true }))}
                      />
                    </div>
                  ) : (
                    <div className="w-full h-36 bg-white/[0.01] border border-white/5 border-dashed rounded-xl flex flex-col items-center justify-center text-slate-500 gap-1.5 shadow-inner">
                      <ImageIcon size={18} className="text-slate-600" />
                      <span className="text-[10px] font-mono font-bold uppercase tracking-widest text-slate-500">Secured Forensic Capture Not Found (404)</span>
                    </div>
                  )}
                </div>
              )}

              {/* SECTION I */}
              <div className="space-y-3">
                <h4 className="text-[10px] font-bold text-slate-500 uppercase tracking-widest flex items-center gap-2">
                  <ShieldCheck size={11} className="text-emerald-500"/> I: Officer Credentials
                </h4>
                <div className="grid grid-cols-3 gap-3">
                  <div>
                    <label htmlFor="officerName" className={labelClass}>Officer Name</label>
                    <input
                      id="officerName"
                      type="text"
                      title="Reporting officer full name"
                      placeholder="Dela Cruz, Fritz"
                      value={reportForm.reportingOfficer}
                      onChange={(e) => setReportForm({...reportForm, reportingOfficer: e.target.value})}
                      className={inputClass}
                    />
                  </div>
                  <div>
                    <label htmlFor="badgeNumber" className={labelClass}>Badge Number</label>
                    <input
                      id="badgeNumber"
                      type="text"
                      title="Officer badge or serial number"
                      placeholder="OCPD-2026-993"
                      value={reportForm.badgeNumber}
                      onChange={(e) => setReportForm({...reportForm, badgeNumber: e.target.value})}
                      className={inputClass}
                    />
                  </div>
                  <div>
                    <label htmlFor="precinctSector" className={labelClass}>Precinct</label>
                    <input
                      id="precinctSector"
                      type="text"
                      title="Precinct jurisdiction sector"
                      placeholder="Ormoc Station 1"
                      value={reportForm.precinctSector}
                      onChange={(e) => setReportForm({...reportForm, precinctSector: e.target.value})}
                      className={inputClass}
                    />
                  </div>
                </div>
              </div>

              {/* SECTION II */}
              <div className="space-y-3">
                <h4 className="text-[10px] font-bold text-slate-500 uppercase tracking-widest flex items-center gap-2">
                  <MapPin size={11} className="text-emerald-500"/> II: Scene Parameters
                </h4>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label htmlFor="weatherCondition" className={labelClass}>Weather</label>
                    <input
                      id="weatherCondition"
                      type="text"
                      title="Weather conditions at scene intake"
                      placeholder="Clear Night"
                      value={reportForm.weatherCondition}
                      onChange={(e) => setReportForm({...reportForm, weatherCondition: e.target.value})}
                      className={inputClass}
                    />
                  </div>
                  <div>
                    <label htmlFor="lightingCondition" className={labelClass}>Lighting</label>
                    <input
                      id="lightingCondition"
                      type="text"
                      title="Lighting visibility at scene"
                      placeholder="Artificial Streetlights"
                      value={reportForm.lightingCondition}
                      onChange={(e) => setReportForm({...reportForm, lightingCondition: e.target.value})}
                      className={inputClass}
                    />
                  </div>
                </div>
              </div>

              {/* SECTION III */}
              <div className="space-y-3">
                <h4 className="text-[10px] font-bold text-slate-500 uppercase tracking-widest flex items-center gap-2">
                  <Info size={11} className="text-emerald-500"/> III: Involved Parties
                </h4>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label htmlFor="victimDetails" className={labelClass}>Victim / Complainant</label>
                    <textarea
                      id="victimDetails"
                      title="Victim or complainant details"
                      rows={2}
                      value={reportForm.victimDetails}
                      onChange={(e) => setReportForm({...reportForm, victimDetails: e.target.value})}
                      className={inputClass}
                    />
                  </div>
                  <div>
                    <label htmlFor="suspectDetails" className={labelClass}>Suspect Description</label>
                    <textarea
                      id="suspectDetails"
                      title="Suspect description and demographics"
                      rows={2}
                      value={reportForm.suspectDetails}
                      onChange={(e) => setReportForm({...reportForm, suspectDetails: e.target.value})}
                      className={inputClass}
                    />
                  </div>
                </div>
              </div>

              {/* SECTION IV */}
              <div className="space-y-3">
                <h4 className="text-[10px] font-bold text-slate-500 uppercase tracking-widest flex items-center gap-2">
                  <FileText size={11} className="text-emerald-500"/> IV: Evidence & Damage
                </h4>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label htmlFor="propertyDamaged" className={labelClass}>Property Damaged</label>
                    <input
                      id="propertyDamaged"
                      type="text"
                      title="Property damaged or value destroyed"
                      placeholder="None Reported"
                      value={reportForm.propertyDamaged}
                      onChange={(e) => setReportForm({...reportForm, propertyDamaged: e.target.value})}
                      className={inputClass}
                    />
                  </div>
                  <div>
                    <label htmlFor="evidenceRecovered" className={labelClass}>Evidence Secured</label>
                    <input
                      id="evidenceRecovered"
                      type="text"
                      title="Physical or digital evidence chain secured"
                      placeholder="Digital AI Surveillance Recording"
                      value={reportForm.evidenceRecovered}
                      onChange={(e) => setReportForm({...reportForm, evidenceRecovered: e.target.value})}
                      className={inputClass}
                    />
                  </div>
                </div>
              </div>

              {/* SECTION V */}
              <div className="space-y-3">
                <h4 className="text-[10px] font-bold text-slate-500 uppercase tracking-widest flex items-center gap-2">
                  <ShieldAlert size={11} className="text-emerald-500"/> V: Disposition & Signatures
                </h4>
                <div className="space-y-3">
                  <div>
                    <label htmlFor="narrativeReadOnly" className={labelClass}>Narrative (AI Generated — Read Only)</label>
                    <textarea
                      id="narrativeReadOnly"
                      title="AI generated narrative — read only"
                      rows={2}
                      value={filingTarget.narrative}
                      disabled
                      className="w-full bg-black/40 border border-white/5 rounded-lg p-2.5 text-xs text-slate-400 cursor-not-allowed outline-none font-sans"
                    />
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label htmlFor="finalDisposition" className={labelClass}>Final Disposition</label>
                      <input
                        id="finalDisposition"
                        type="text"
                        title="Final case disposition or next investigative action"
                        placeholder="Pending Criminal Case Referral"
                        value={reportForm.finalDisposition}
                        onChange={(e) => setReportForm({...reportForm, finalDisposition: e.target.value})}
                        className={inputClass}
                      />
                    </div>
                    <div>
                      <label htmlFor="supervisorApproval" className={labelClass}>Supervisor Sign-off</label>
                      <input
                        id="supervisorApproval"
                        type="text"
                        title="Desk supervisor endorsement authorization"
                        placeholder="P/Col. Del Mar, R."
                        value={reportForm.supervisorApproval}
                        onChange={(e) => setReportForm({...reportForm, supervisorApproval: e.target.value})}
                        className={inputClass}
                      />
                    </div>
                  </div>
                </div>
              </div>

            </div>

            {/* MODAL FOOTER */}
            <div className="sticky bottom-0 bg-[#0b0f19] border-t border-white/5 px-6 py-4 flex justify-end gap-3">
              <button
                title="Cancel and close report filing"
                onClick={closeModal}
                className="px-5 py-2.5 border border-white/5 text-[10px] uppercase font-bold text-slate-400 hover:bg-white/5 rounded-xl tracking-widest transition-all"
              >
                Cancel
              </button>
              <button
                title="Commit and sign the official police report"
                onClick={handleSubmitOfficialReport}
                disabled={!reportForm.badgeNumber || !reportForm.reportingOfficer}
                className="px-6 py-2.5 bg-emerald-500 disabled:opacity-20 disabled:cursor-not-allowed text-black text-[10px] tracking-widest font-black uppercase rounded-xl hover:bg-emerald-400 transition-all flex items-center gap-2 shadow-xl"
              >
                <Check size={14}/> Commit & Sign Report
              </button>
            </div>

          </div>
        </div>
      )}
    </div>
  );
}