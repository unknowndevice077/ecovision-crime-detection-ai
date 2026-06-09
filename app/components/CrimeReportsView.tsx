"use client";

import React, { useState, useEffect, useRef } from 'react';
import { 
  X, MapPin, ShieldCheck, 
  Info, AlertCircle,
  Calendar, ListFilter, ClipboardCopy, FileSignature, ShieldAlert, Radio, Check
} from 'lucide-react';

type SmartpoleNode = {
  id: string; name: string; street: string; lat: number; lng: number;
};

const SMARTPOLE_LOCATIONS: SmartpoleNode[] = [
  { id: 'sp1', name: 'Cogon Core Smartpole', street: 'Cogon Combado (Central)', lat: 11.0176, lng: 124.6031 },
  { id: 'sp2', name: 'Sector B Gate Smartpole', street: 'Brgy. Cogon Hall', lat: 11.0182, lng: 124.6025 },
  { id: 'sp3', name: 'North Uplink Smartpole', street: 'District 18 (Cogon North)', lat: 11.0145, lng: 124.6055 }
];

const SAMPLE_REPORTS = [
  {
    id: 'sample-sp1', caseId: 'CASE-C019AA60', type: 'ASSAULT', officer: 'AI_SENTINEL',
    lat: 11.0176, lng: 124.6031, locationName: 'Cogon Core Smartpole',
    severity: 'CRITICAL', date: '2026-06-01', militaryTime: '0552',
    narrative: 'Automated neural detection of ASSAULT.',
    natureOfCall: 'AI Threat Flag', arrivalReason: 'Automated Tracking',
    additionalOfficers: 'None', status: 'PENDING'
  },
  {
    id: 'sample-sp2', caseId: 'CASE-B882AC11', type: 'Theft', officer: 'AI_SENTINEL',
    lat: 11.0182, lng: 124.6025, locationName: 'Sector B Gate Smartpole',
    severity: 'MEDIUM', date: '2026-06-02', militaryTime: '1114',
    narrative: 'Automated neural detection of Theft / Larceny.',
    natureOfCall: 'AI Threat Flag', arrivalReason: 'Automated Tracking',
    additionalOfficers: 'None', status: 'Confirmed'
  },
  {
    id: 'sample-sp3', caseId: 'CASE-N993DF44', type: 'Physical Altercation', officer: 'AI_SENTINEL',
    lat: 11.0145, lng: 124.6055, locationName: 'North Uplink Smartpole',
    severity: 'HIGH', date: '2026-05-31', militaryTime: '0245',
    narrative: 'Automated neural detection of a Physical Altercation on public lanes.',
    natureOfCall: 'AI Threat Flag', arrivalReason: 'Automated Tracking',
    additionalOfficers: 'None', status: 'Confirmed'
  }
];

type Incident = {
  id: string; caseId: string; type: string; officer: string;
  lat: number; lng: number; locationName: string;
  severity: string; date: string; militaryTime: string;
  narrative: string; natureOfCall: string; arrivalReason: string;
  additionalOfficers: string; status: string;
};

export default function CrimeReportsView({ onUpdate }: { onUpdate: () => void }) {
  const [selected, setSelected] = useState<Incident | null>(null);
  const [selectedPole, setSelectedPole] = useState<SmartpoleNode | null>(null);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [poleDateFilter, setPoleDateFilter] = useState("");
  const [poleTypeFilter, setPoleTypeFilter] = useState("ALL");
  const [showFilingModal, setShowFilingModal] = useState(false);
  const [filingTarget, setFilingTarget] = useState<Incident | null>(null);
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
  const markersRef = useRef<any[]>([]);

  // DYNAMIC API URL DISCOVERY FOR MIGRATION
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

  const refreshMarkers = () => {
    const L = (window as any).L;
    if (!L || !mapRef.current) return;
    markersRef.current.forEach(m => m.remove());
    markersRef.current = [];

    SMARTPOLE_LOCATIONS.forEach(pole => {
      const icon = L.divIcon({
        className: 'custom-pole-icon',
        html: `<div class="w-7 h-7 bg-[#0b0f17] rounded-full border-2 border-emerald-400 shadow-2xl flex items-center justify-center text-[10px] text-emerald-400 font-black">📡</div>`,
        iconSize: [28, 28], iconAnchor: [14, 14]
      });
      const m = L.marker([pole.lat, pole.lng], { icon }).addTo(mapRef.current)
        .on('click', (e: any) => { L.DomEvent.stopPropagation(e); setSelectedPole(pole); setSelected(null); });
      markersRef.current.push(m);
    });

    incidents.forEach(inc => {
      const isNew = inc.status === 'PENDING' || inc.status === 'Active';
      const icon = L.divIcon({
        className: 'custom-div-icon',
        html: `<div class="w-6 h-6 bg-[#1e293b] rounded-full border-2 border-red-500 shadow-xl flex items-center justify-center text-[10px] text-red-400 font-extrabold ${isNew ? 'animate-pulse' : ''}">!</div>`,
        iconSize: [24, 24], iconAnchor: [12, 12]
      });
      const m = L.marker([inc.lat, inc.lng], { icon }).addTo(mapRef.current)
        .on('click', (e: any) => { L.DomEvent.stopPropagation(e); setSelected(inc); });
      markersRef.current.push(m);
    });
  };

  useEffect(() => { refreshMarkers(); }, [incidents]);
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
        mapRef.current = L.map(mapContainerRef.current, {
          center: [11.0176, 124.6031], zoom: 17, zoomControl: false, attributionControl: false
        });
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19 }).addTo(mapRef.current);
      }
    };
    document.body.appendChild(script);
  }, []);

  const handleExpunge = async () => {
    if (!selected) return;
    const res = await fetch(`${API_URL}/api/incidents/${selected.id}`, { method: 'DELETE' });
    if (res.ok) { setIncidents(prev => prev.filter(i => i.id !== selected.id)); setSelected(null); onUpdate(); }
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
    if (poleTypeFilter !== 'ALL' && inc.type !== poleTypeFilter) return false;
    return true;
  });

  const finalLogsDisplay = getFilteredIncidents();

  const inputClass = "w-full bg-white/[0.02] border border-white/5 rounded-lg p-2.5 text-xs text-white outline-none focus:border-emerald-500 font-mono";
  const labelClass = "text-[8px] font-mono text-slate-500 uppercase block mb-1";

  return (
    <div className="flex h-full gap-4 animate-in fade-in duration-500 relative w-full">
      <style>{`
        .leaflet-container { background: #f1f5f9 !important; outline: none !important; cursor: grab !important; }
        .leaflet-tile { filter: contrast(1.05) saturate(1.1) !important; margin: -1px !important; padding: 1px !important; }
        select option { background-color: #0f172a !important; color: #10b981 !important; }
        select { color-scheme: dark; background-color: #0f172a !important; color: white !important; }
      `}</style>

      {/* MAP */}
      <div className="flex-1 bg-[#f1f5f9] border border-white/5 rounded-[2.5rem] relative overflow-hidden shadow-2xl">
        <div ref={mapContainerRef} className="w-full h-full z-0" />
      </div>

      {/* SIDEBAR */}
      <div className="w-96 bg-[#0a0c10] border border-white/5 rounded-[2.5rem] p-6 flex flex-col overflow-hidden shadow-2xl z-20 text-slate-200">

        {selected ? (
          <div className="animate-in slide-in-from-right duration-300 flex flex-col h-full justify-between space-y-4">
            <div className="space-y-4">
              <div className="flex justify-between items-center pb-3 border-b border-white/5">
                <button
                  title="Back to incident logs"
                  onClick={() => setSelected(null)}
                  className="text-[10px] font-bold tracking-widest uppercase text-emerald-400 hover:underline"
                >
                  ← Back to Logs
                </button>
                <div className="bg-emerald-500/10 text-emerald-400 text-[8px] px-2 py-0.5 rounded border border-emerald-500/20 font-black uppercase">Official record</div>
              </div>
              <div>
                <span className="text-emerald-500 font-mono text-sm font-bold tracking-tighter block mb-1">{selected.caseId}</span>
                <div className="grid grid-cols-2 text-[10px] font-mono text-slate-500">
                  <span>Date: {selected.date}</span>
                  <span className="text-right">Time: {formatTo12Hour(selected.militaryTime)}</span>
                </div>
              </div>
              <div className="space-y-3">
                <div className="bg-white/[0.02] p-3 rounded-lg border border-white/5">
                  <p className="text-[9px] font-bold text-slate-500 uppercase tracking-widest flex items-center gap-2 mb-1"><MapPin size={10}/> Location</p>
                  <p className="text-[11px] text-slate-200 font-mono">{selected.locationName}</p>
                </div>
                <div className="grid grid-cols-2 gap-3 text-[10px]">
                  <div><p className="text-[9px] text-slate-500 uppercase">Officer</p><p className="font-bold text-white uppercase">{selected.officer}</p></div>
                  <div className="text-right"><p className="text-[9px] text-slate-500 uppercase">Type</p><p className="font-bold text-white uppercase">{selected.type}</p></div>
                </div>
              </div>
              <div className="space-y-2">
                <div className="flex items-center gap-2 text-slate-500 uppercase font-bold text-[9px] tracking-widest"><Info size={12}/> Narrative</div>
                <div className="p-4 rounded-xl bg-black/40 border border-white/5 max-h-36 overflow-y-auto">
                  <p className="text-[11px] text-slate-300 leading-relaxed font-serif">"{selected.narrative}"</p>
                </div>
              </div>
            </div>
            <div className="space-y-2 mt-auto">
              <button
                title="Generate official police report for this incident"
                onClick={() => handleOpenReportFiler(selected)}
                className="w-full py-3 bg-emerald-600 text-black text-[10px] tracking-widest font-black uppercase rounded-xl hover:bg-emerald-500 transition-all flex items-center justify-center gap-2"
              >
                <ClipboardCopy size={13}/> Generate Police Report
              </button>
              <button
                title="Permanently remove this incident from the SQL database"
                onClick={handleExpunge}
                className="w-full py-2.5 border border-red-500/20 text-red-500 text-[9px] font-bold uppercase rounded-xl hover:bg-red-500 hover:text-white transition-all"
              >
                Expunge From SQL Database
              </button>
            </div>
          </div>
        ) : (
          <div className="animate-in fade-in duration-300 flex flex-col h-full">
            <div className="pb-4 border-b border-white/5 mb-4 flex justify-between items-center">
              <div>
                <h4 className="text-[11px] font-black text-white uppercase tracking-wider flex items-center gap-1.5">
                  <Radio size={12} className="text-emerald-400"/> {selectedPole ? selectedPole.name : 'All Crime Dispatches'}
                </h4>
                <p className="text-[9px] font-mono text-slate-500 mt-0.5 uppercase">
                  {selectedPole ? `Node: ${selectedPole.street}` : 'Global Inbound Logs Matrix'}
                </p>
              </div>
              {selectedPole && (
                <button
                  title="Return to global crime dispatch view"
                  onClick={() => { setSelectedPole(null); setPoleDateFilter(""); setPoleTypeFilter("ALL"); }}
                  className="text-[9px] font-mono font-bold text-slate-500 hover:text-emerald-400 uppercase"
                >
                  Global
                </button>
              )}
            </div>

            {/* FILTERS */}
            <div className="grid grid-cols-2 gap-2 mb-4">
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
                  <option value="Theft">Theft</option>
                  <option value="Physical Altercation">Altercation</option>
                  <option value="General Disturbance">Disturbance</option>
                </select>
              </div>
            </div>

            <div className="flex-1 overflow-y-auto space-y-2 bg-black/10 p-2 rounded-xl">
              {finalLogsDisplay.length === 0 ? (
                <div className="h-full flex flex-col items-center justify-center opacity-30 py-12 text-center">
                  <AlertCircle size={20} className="mb-2 text-slate-500"/>
                  <span className="text-[9px] font-bold uppercase tracking-widest font-mono">No Matching Incidents</span>
                </div>
              ) : (
                finalLogsDisplay.map(inc => (
                  <div key={inc.id} className="w-full text-left p-3 bg-white/[0.01] border border-white/5 rounded-xl flex flex-col gap-2 hover:border-white/10 transition-all">
                    <div className="flex justify-between items-center text-[9px] font-mono">
                      <span className="text-emerald-400 font-bold">{inc.caseId}</span>
                      <span className="text-slate-500">{formatTo12Hour(inc.militaryTime)}</span>
                    </div>
                    <h5 className="text-[11px] font-bold uppercase text-slate-200">{inc.type}</h5>
                    <p className="text-[10px] text-slate-400 line-clamp-2 italic">"{inc.narrative}"</p>
                    <div className="flex gap-2 pt-2 border-t border-white/5 justify-between items-center">
                      <button
                        title={`View full statement for ${inc.caseId}`}
                        onClick={() => setSelected(inc)}
                        className="text-[9px] uppercase font-bold text-slate-400 hover:text-white"
                      >
                        View
                      </button>
                      <button
                        title={`Generate police report for ${inc.caseId}`}
                        onClick={() => handleOpenReportFiler(inc)}
                        className="text-[9px] uppercase font-black text-emerald-400 hover:text-emerald-300 flex items-center gap-1"
                      >
                        <FileSignature size={10}/> Generate Police Report
                      </button>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        )}
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
                  <ClipboardCopy size={11} className="text-emerald-500"/> IV: Evidence & Damage
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