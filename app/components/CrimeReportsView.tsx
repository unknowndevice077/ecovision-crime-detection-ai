"use client";

import React, { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import {
  X, MapPin, ShieldCheck, Trash2, Plus,
  Info, AlertCircle, FileSignature, FileText,
  Calendar, ListFilter, ShieldAlert, Radio, Check, Video, ArrowLeft, Globe, ImageIcon
} from 'lucide-react';
import { useRuntimeConfig } from '../hooks/useRuntimeConfig';
import { useLiveChannel } from '../context/WebSocketContext';

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
    id: 'sample-sp1', case_id: 'CASE-C019AA60', type: 'ASSAULT', officer: 'AI_SENTINEL',
    lat: 11.0176, lng: 124.6031, location_name: 'Cogon Core Smartpole Node',
    severity: 'CRITICAL', occurred_date: '2026-06-01', occurred_time: '0552',
    narrative: 'Automated neural detection of ASSAULT.',
    nature_of_call: 'AI Threat Flag', arrival_reason: 'Automated Tracking',
    additional_officers: 'None', status: 'PENDING', screenshot_path: 'https://picsum.photos/seed/sp1/640/360'
  },
  {
    id: 'sample-sp2', case_id: 'CASE-B882AC11', type: 'THEFT', officer: 'AI_SENTINEL',
    lat: 11.0182, lng: 124.6025, location_name: 'Sector B Gate Smartpole Node',
    severity: 'MEDIUM', occurred_date: '2026-06-02', occurred_time: '1114',
    narrative: 'Automated neural detection of Theft / Larceny.',
    nature_of_call: 'AI Threat Flag', arrival_reason: 'Automated Tracking',
    additional_officers: 'None', status: 'Confirmed', screenshot_path: 'https://picsum.photos/seed/sp2/640/360'
  },
  {
    id: 'sample-sp3', case_id: 'CASE-N993DF44', type: 'PHYSICAL ALTERCATION', officer: 'AI_SENTINEL',
    lat: 11.0145, lng: 124.6055, location_name: 'North Uplink Smartpole Node',
    severity: 'HIGH', occurred_date: '2026-05-31', occurred_time: '0245',
    narrative: 'Automated neural detection of a Physical Altercation on public lanes.',
    nature_of_call: 'AI Threat Flag', arrival_reason: 'Automated Tracking',
    additional_officers: 'None', status: 'Confirmed', screenshot_path: 'https://picsum.photos/seed/sp3/640/360'
  }
];

type Incident = {
  id: string; case_id: string; type: string; officer: string;
  lat: number; lng: number; location_name: string;
  severity: string; occurred_date: string; occurred_time: string;
  narrative: string; nature_of_call: string; arrival_reason: string;
  additional_officers: string; status: string;
  screenshot_path?: string;
  map_hidden?: number | boolean;
};

interface CrimeReportsViewProps {
  onUpdate: () => void;
  onDeepLink?: (crimeId: string) => void;
}

export default function CrimeReportsView({ onUpdate, onDeepLink }: CrimeReportsViewProps) {
  const { apiUrl: API_URL } = useRuntimeConfig();
  const [selectedPoleId, setSelectedPoleId] = useState<string | null>(null);
  const selectedPole = useMemo<SmartpoleNode | null>(() => {
    return selectedPoleId ? SMARTPOLE_LOCATIONS.find(p => p.id === selectedPoleId) ?? null : null;
  }, [selectedPoleId]);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [poleDateFilter, setPoleDateFilter] = useState("");
  const [poleTypeFilter, setPoleTypeFilter] = useState("ALL");
  const [showFilingModal, setShowFilingModal] = useState(false);
  const [expungeTargetId, setExpungeTargetId] = useState<string | null>(null);
  const [filingTarget, setFilingTarget] = useState<Incident | null>(null);
  
  const [brokenImages, setBrokenImages] = useState<Record<string, boolean>>({});

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
  const selectedPoleIdRef = useRef<string | null>(null);

  const filteredIncidents = useMemo(() => {
    return incidents.filter(inc => {
      // Expunged from this view only -- Crime History still has it.
      if (inc.map_hidden === 1 || inc.map_hidden === true) return false;
      if (selectedPole) {
        const match = inc.location_name.toLowerCase().includes(selectedPole.name.toLowerCase()) ||
                      inc.location_name.toLowerCase().includes(selectedPole.street.toLowerCase());
        if (!match) return false;
      }
      if (poleDateFilter && !inc.occurred_date.includes(poleDateFilter)) return false;
      if (poleTypeFilter !== 'ALL' && inc.type.toUpperCase() !== poleTypeFilter.toUpperCase()) return false;
   
      return true;
    });
  }, [incidents, selectedPole, poleDateFilter, poleTypeFilter]);
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
      // Inline styles, not Tailwind classes: this HTML is handed to Leaflet
      // and injected outside React, so it reads the same design tokens the
      // rest of the console uses rather than a second, drifting palette.
      html: `<div style="
        width:26px;height:26px;display:flex;align-items:center;justify-content:center;
        font-size:11px;line-height:1;
        background:${isCurrentSelected ? 'var(--accent)' : 'var(--panel)'};
        border:2px solid ${isCurrentSelected ? 'var(--accent)' : 'var(--line-2)'};
        box-shadow:${isCurrentSelected ? '0 0 0 4px rgba(45,111,247,0.25)' : '0 1px 3px rgba(0,0,0,0.6)'};
      ">📡</div>`,
      iconSize: [26, 26], iconAnchor: [13, 13]
    });
  };

  const updatePoleSelectionIcons = (newSelectedId: string | null) => {
    const L = (window as any).L;
    if (!L || !mapRef.current) return;
    const previousSelectedId = selectedPoleIdRef.current;
    if (previousSelectedId && poleMarkersRef.current[previousSelectedId]) {
      const previousPole = SMARTPOLE_LOCATIONS.find(p => p.id === previousSelectedId);
      if (previousPole) {
        poleMarkersRef.current[previousSelectedId].setIcon(buildPoleIcon(L, previousPole, null));
      }
    }
    if (newSelectedId) {
      const nextPole = SMARTPOLE_LOCATIONS.find(p => p.id === newSelectedId);
      if (nextPole && poleMarkersRef.current[newSelectedId]) {
        poleMarkersRef.current[newSelectedId].setIcon(buildPoleIcon(L, nextPole, newSelectedId));
      }
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
    // Dismissed incidents are cleared from the map -- otherwise every pin
    // you ever "Ignore"'d stays glued to the map forever, and it also sits
    // on top of pole markers (same coords) permanently eating their clicks.
    incidents
      .filter(inc => (inc.status || '').toLowerCase() !== 'dismissed')
      .filter(inc => inc.map_hidden !== 1 && inc.map_hidden !== true)
      .forEach(inc => {
        const isConfirmed = (inc.status || '').toLowerCase() === 'confirmed';
        const tone = isConfirmed ? 'var(--ok)' : 'var(--critical)';
        const icon = L.divIcon({
          className: 'custom-div-icon',
          html: `<div style="
            width:22px;height:22px;display:flex;align-items:center;justify-content:center;
            font-size:11px;font-weight:800;line-height:1;
            background:var(--panel);border:2px solid ${tone};color:${tone};
            box-shadow:0 1px 3px rgba(0,0,0,0.6);
          ">!</div>`,
          iconSize: [22, 22], iconAnchor: [11, 11]
        });
        // interactive: false + a negative zIndexOffset keeps these purely
        // visual so they never steal clicks meant for a pole underneath.
        const m = L.marker([inc.lat, inc.lng], { icon, interactive: false, zIndexOffset: -1000 }).addTo(mapRef.current);
        incidentMarkersRef.current.push(m);
      });
  };

  const createPoleMarkers = () => {
    const L = (window as any).L;
    if (!L || !mapRef.current) return;

    SMARTPOLE_LOCATIONS.forEach(pole => {
      const marker = L.marker([pole.lat, pole.lng], { icon: buildPoleIcon(L, pole, null), zIndexOffset: 1000 })
        .addTo(mapRef.current)
        .on('click', (e: any) => {
          L.DomEvent.stopPropagation(e);
          if (selectedPoleIdRef.current === pole.id) return;

          updatePoleSelectionIcons(pole.id);
          selectedPoleIdRef.current = pole.id;
          mapRef.current?.panTo([pole.lat, pole.lng], { animate: true, duration: 0.2 });

          setSelectedPoleId(pole.id);
          setIsManualFilingActive(false);
        });
      poleMarkersRef.current[pole.id] = marker;
    });
  };

  useEffect(() => {
    refreshIncidentMarkers();
  }, [incidents]);

  useEffect(() => {
    if (!selectedPole) {
      selectedPoleIdRef.current = null;
      refreshPoleIcons();
    }
  }, [selectedPole]);
  // Was an unconditional 5s setInterval poll even though useLiveChannel
  // (push-based, backed by the app-wide shared WebSocket) is already used
  // elsewhere in the app for this exact purpose -- this view just never got
  // migrated.
  useLiveChannel("incidents", fetchIncidents);

  useEffect(() => {
    if (!document.getElementById('leaflet-css')) {
      const link = document.createElement('link');
      link.id = 'leaflet-css'; link.rel = 'stylesheet';
      link.href = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';
      document.head.appendChild(link);
    }

    const initLeafletMap = () => {
      const L = (window as any).L;
      if (!mapRef.current && mapContainerRef.current) {
        mapRef.current = L.map(mapContainerRef.current, {
          center: [11.0176, 124.6031], zoom: 17, zoomControl: false, attributionControl: false, doubleClickZoom: false
        });
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19 }).addTo(mapRef.current);
        createPoleMarkers();
      }
    };

    // The CSS injection above was guarded by an id check, but this script
    // tag wasn't -- switching to the Map tab, away, and back (normal
    // operator behavior) appended a fresh duplicate <script> every time,
    // each refetched over the network. If Leaflet is already loaded (or
    // mid-load from an earlier mount), don't inject it again.
    const existingScript = document.getElementById('leaflet-js') as HTMLScriptElement | null;
    if ((window as any).L) {
      initLeafletMap();
    } else if (existingScript) {
      existingScript.addEventListener('load', initLeafletMap, { once: true });
    } else {
      const script = document.createElement('script');
      script.id = 'leaflet-js';
      script.src = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js';
      script.async = true;
      script.onload = initLeafletMap;
      document.body.appendChild(script);
    }
  }, []);
  const handleExpunge = (incidentId: string) => {
    setExpungeTargetId(incidentId);
  };

  const confirmExpunge = async () => {
    if (!expungeTargetId) return;
    const incidentId = expungeTargetId;
    setExpungeTargetId(null);
    // Archive, not delete -- Crime History reads the same incidents table
    // and must keep the permanent record even after this view "removes" it.
    const res = await fetch(`${API_URL}/api/incidents/${incidentId}/archive`, { method: 'PATCH' });
    if (res.ok) {
      setIncidents(prev => prev.map(i => i.id === incidentId ? { ...i, map_hidden: 1 } : i));
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
      case_id: `CASE-${now.getFullYear()}${String(now.getMonth()+1).padStart(2,'0')}-${Math.random().toString(36).substr(2,4).toUpperCase()}`,
      type: manualType.toUpperCase(),
      officer: "MANUAL_ENTRY",
      lat: selectedPole.lat,
      lng: selectedPole.lng,
      location_name: selectedPole.name,
      severity: manualSeverity,
      occurred_date: now.toISOString().split('T')[0],
      occurred_time: now.toTimeString().split(' ')[0].replace(/:/g, '').substring(0,4),
      narrative: manualNarrative,
      nature_of_call: "Operator Manual Filing",
      arrival_reason: "Field Request",
      additional_officers: "None",
      status: "Active",
      barangay_id: "cogon"
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

  // FIXED: Adjusted code parameters to seamlessly pipe form text assets, capture real-time system frames, and compile PDF logs instantly upon confirmation
  const handleSubmitOfficialReport = async () => {
    if (!filingTarget || !reportForm.badgeNumber || !reportForm.reportingOfficer) return;
    try {
      await fetch(`${API_URL}/api/incidents/${filingTarget.id}/confirm-and-report`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          status: "Confirmed",
          capture_snapshot: true,
          report_details: reportForm
        })
      });
      setShowFilingModal(false); 
      setFilingTarget(null);
      fetchIncidents(); 
      onUpdate();
    } catch (e) { 
      console.error(e); 
    }
  };

  const closeModal = () => { setShowFilingModal(false); setFilingTarget(null);
  };

  const reportImageUrl = useMemo(() => {
    if (!filingTarget) return '';
    if (filingTarget.screenshot_path) {
      return filingTarget.screenshot_path.startsWith('http')
        ? filingTarget.screenshot_path
        : `${API_URL}${filingTarget.screenshot_path}`;
    }
    return 'https://picsum.photos/seed/ai-crime-report/900/450';
  }, [filingTarget, API_URL]);
  const handleBarangayJump = (lat: number, lng: number) => {
    if (mapRef.current) {
      mapRef.current.setView([lat, lng], 17, { animate: true });
    }
  };

  const finalLogsDisplay = filteredIncidents;
  const fieldStyle = { background: 'var(--bg)', borderColor: 'var(--line)' };
  const inputClass = "data w-full border p-2.5 text-[12px] text-white outline-none focus:border-[var(--accent)] transition-colors";
  const labelClass = "label block mb-1";

  return (
    <div className="flex h-full flex-col gap-2 relative w-full overflow-hidden">

      {/* ═══ MAP TOOLBAR ══════════════════════════════════════════════════ */}
      <div
        className="w-full h-10 flex items-center justify-between px-2.5 border shrink-0"
        style={{ background: 'var(--panel)', borderColor: 'var(--line)' }}
      >
        <div className="flex items-center gap-2">
          <Radio size={13} style={{ color: 'var(--accent)' }} />
          <span className="label" style={{ color: 'var(--text)' }}>Incident Map</span>
        </div>

        <div className="flex items-center gap-2">
          <span className="label">Jump to</span>
          <select
            title="Navigate directly to a specific area"
            onChange={(e) => {
              const val = e.target.value;
              if (val === 'cogon') handleBarangayJump(11.0176, 124.6031);
              else if (val === 'valencia') handleBarangayJump(11.0055, 124.6122);
              else if (val === 'district18') handleBarangayJump(11.0145, 124.6055);
            }}
            className="data border px-2 py-1.5 text-[11px] outline-none cursor-pointer focus:border-[var(--accent)] transition-colors"
            style={{ ...fieldStyle, color: 'var(--text-2)' }}
          >
            <option value="">Select area…</option>
            <option value="cogon">Brgy. Cogon</option>
            <option value="valencia">Brgy. Valencia</option>
            <option value="district18">District 18 HQ</option>
          </select>

          <div className="w-px h-5" style={{ background: 'var(--line-2)' }} />

          <button
            onClick={() => {
              const name = prompt("Enter New Smartpole Identifier Label:", "Sector D Terminal");
              const path = prompt("Enter Network RTSP Surveillance Feed Stream Path:", "rtsp://192.168.1.50/live");
              if (name && path && mapRef.current) {
                const center = mapRef.current.getCenter();
                alert(`Successfully initiated secure configuration link for ${name} at parameters:\nLat: ${center.lat.toFixed(4)}\nLng: ${center.lng.toFixed(4)}`);
              }
            }}
            className="flex items-center gap-1.5 px-2.5 py-1.5 text-[10px] font-bold uppercase tracking-wider text-white transition-opacity hover:opacity-90"
            style={{ background: 'var(--accent)' }}
          >
            <Plus size={12} /> Add smartpole
          </button>
        </div>
      </div>

      <div className="flex-1 flex gap-2 min-h-0 w-full">
        {/* ═══ MAP CANVAS ═════════════════════════════════════════════════ */}
        <div
          className="flex-1 border relative overflow-hidden h-full"
          style={{ background: 'var(--panel)', borderColor: 'var(--line)' }}
        >
          <div ref={mapContainerRef} className="w-full h-full z-0" />
        </div>

        {/* ═══ INCIDENT FEED ══════════════════════════════════════════════ */}
        <div
          className="w-[340px] shrink-0 border flex flex-col overflow-hidden z-20 h-full"
          style={{ background: 'var(--panel)', borderColor: 'var(--line)' }}
        >
          {/* Panel header */}
          <div
            className="h-9 shrink-0 flex justify-between items-center gap-2 px-2.5 border-b"
            style={{ borderColor: 'var(--line)' }}
          >
            {selectedPole ? (
              <button
                title="Back to all incidents"
                onClick={() => { updatePoleSelectionIcons(null); setSelectedPoleId(null); setIsManualFilingActive(false); }}
                className="flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider transition-colors hover:text-white shrink-0"
                style={{ color: 'var(--text-2)' }}
              >
                <ArrowLeft size={12} /> Back
              </button>
            ) : (
              <Globe size={12} className="shrink-0" style={{ color: 'var(--text-3)' }} />
            )}

            <div className="min-w-0 flex-1 text-center">
              <div className="text-[11px] font-bold text-white uppercase tracking-wide truncate">
                {selectedPole ? selectedPole.name : 'All Incidents'}
              </div>
            </div>

            <button
              onClick={() => selectedPole && setIsManualFilingActive(!isManualFilingActive)}
              disabled={!selectedPole}
              title={selectedPole ? 'File a manual report for this pole' : 'Select a smartpole first'}
              className="px-2 py-1 border text-[9px] font-bold uppercase tracking-wider transition-colors hover:bg-white/5 disabled:opacity-25 shrink-0"
              style={{ borderColor: 'var(--line-2)', color: 'var(--text-2)' }}
            >
              {isManualFilingActive ? 'Cancel' : '+ Report'}
            </button>
          </div>

          {/* Pole subtitle */}
          <div className="shrink-0 px-2.5 py-1.5 border-b" style={{ borderColor: 'var(--line)', background: 'var(--bg)' }}>
            <span className="data text-[10px] truncate block" style={{ color: 'var(--text-3)' }}>
              {selectedPole ? selectedPole.street : 'Monitoring all areas'}
            </span>
          </div>

          {isManualFilingActive && selectedPole && (
            <form
              onSubmit={handleCreateManualReport}
              className="shrink-0 p-2.5 border-b space-y-2.5"
              style={{ background: 'var(--panel-2)', borderColor: 'var(--line)' }}
            >
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <span className={labelClass}>Type</span>
                  <select
                    title="Select incident type"
                    value={manualType}
                    onChange={(e) => setFormManualType(e.target.value)}
                    className="data w-full border p-1.5 text-[11px] text-white outline-none focus:border-[var(--accent)]"
                    style={fieldStyle}
                  >
                    <option value="ASSAULT">Assault</option>
                    <option value="THEFT">Theft</option>
                    <option value="PHYSICAL ALTERCATION">Altercation</option>
                    <option value="VANDALISM">Vandalism</option>
                  </select>
                </div>
                <div>
                  <span className={labelClass}>Severity</span>
                  <select
                    title="Select severity"
                    value={manualSeverity}
                    onChange={(e) => setFormManualSeverity(e.target.value)}
                    className="data w-full border p-1.5 text-[11px] text-white outline-none focus:border-[var(--accent)]"
                    style={fieldStyle}
                  >
                    <option value="LOW">Low</option>
                    <option value="MEDIUM">Medium</option>
                    <option value="HIGH">High</option>
                    <option value="CRITICAL">Critical</option>
                  </select>
                </div>
              </div>
              <div>
                <span className={labelClass}>Narrative</span>
                <textarea
                  value={manualNarrative}
                  onChange={(e) => setFormManualNarrative(e.target.value)}
                  placeholder="What was observed…"
                  className="w-full h-14 border p-2 text-[11px] text-white resize-none outline-none focus:border-[var(--accent)]"
                  style={fieldStyle}
                />
              </div>
              <button
                type="submit"
                className="w-full py-2 text-[10px] font-bold uppercase tracking-wider text-white transition-opacity hover:opacity-90"
                style={{ background: 'var(--accent)' }}
              >
                File report
              </button>
            </form>
          )}

          {/* Filters */}
          <div className="shrink-0 grid grid-cols-2 gap-1.5 p-2 border-b" style={{ borderColor: 'var(--line)' }}>
            <div className="flex items-center gap-1.5 px-2 py-1.5 border" style={fieldStyle}>
              <Calendar size={11} className="shrink-0" style={{ color: 'var(--text-3)' }} />
              <input
                type="text"
                title="Filter incidents by date"
                placeholder="YYYY-MM-DD"
                value={poleDateFilter}
                onChange={(e) => setPoleDateFilter(e.target.value)}
                className="data bg-transparent text-[10px] outline-none w-full border-none p-0"
                style={{ color: 'var(--text)' }}
              />
            </div>
            <div className="flex items-center gap-1.5 px-2 py-1.5 border" style={fieldStyle}>
              <ListFilter size={11} className="shrink-0" style={{ color: 'var(--text-3)' }} />
              <select
                title="Filter incidents by type"
                value={poleTypeFilter}
                onChange={(e) => setPoleTypeFilter(e.target.value)}
                className="data bg-transparent text-[10px] outline-none w-full cursor-pointer border-none p-0"
                style={{ color: 'var(--text-2)' }}
              >
                <option value="ALL">All types</option>
                <option value="ASSAULT">Assault</option>
                <option value="THEFT">Theft</option>
                <option value="PHYSICAL ALTERCATION">Altercation</option>
                <option value="VANDALISM">Vandalism</option>
              </select>
            </div>
          </div>

          {/* Feed */}
          <div className="flex-1 overflow-y-auto custom-scrollbar min-h-0">
            {finalLogsDisplay.length === 0 ? (
              <div className="h-full flex flex-col items-center justify-center gap-2 py-12">
                <AlertCircle size={20} style={{ color: 'var(--text-3)' }} />
                <span className="label">No incidents on record</span>
              </div>
            ) : (
              finalLogsDisplay.map(inc => {
                const isImageBroken = brokenImages[inc.id];
                return (
                  <div
                    key={inc.id}
                    className="w-full text-left p-2.5 border-b flex flex-col gap-1.5 transition-colors hover:bg-white/[0.02] relative"
                    style={{ borderColor: 'var(--line)' }}
                  >
                    <div className="flex justify-between items-center">
                      <span className="data text-[10px] font-bold select-all" style={{ color: 'var(--accent)' }}>
                        {inc.case_id}
                      </span>
                      <span className="data text-[10px]" style={{ color: 'var(--text-3)' }}>
                        {formatTo12Hour(inc.occurred_time)}
                      </span>
                    </div>

                    <h5 className="text-[13px] font-bold uppercase text-white tracking-wide">{inc.type}</h5>

                    {inc.screenshot_path && !isImageBroken ? (
                      <div className="w-full h-24 border overflow-hidden relative" style={{ background: '#000', borderColor: 'var(--line)' }}>
                        <img
                          src={inc.screenshot_path.startsWith('http') ? inc.screenshot_path : `${API_URL}${inc.screenshot_path}`}
                          className="w-full h-full object-cover"
                          alt={`Scene capture for ${inc.case_id}`}
                          onError={() => setBrokenImages(prev => ({ ...prev, [inc.id]: true }))}
                        />
                      </div>
                    ) : inc.screenshot_path ? (
                      <div
                        className="w-full h-16 border border-dashed flex flex-col items-center justify-center gap-1"
                        style={{ background: 'var(--bg)', borderColor: 'var(--line-2)' }}
                      >
                        <AlertCircle size={13} style={{ color: 'var(--text-3)' }} />
                        <span className="label">Scene image unavailable</span>
                      </div>
                    ) : null}

                    <p className="text-[10px] leading-snug select-text" style={{ color: 'var(--text-2)' }}>
                      {inc.narrative}
                    </p>

                    <div
                      className="flex gap-2 pt-1.5 border-t justify-end items-center"
                      style={{ borderColor: 'var(--line)' }}
                    >
                      <button
                        title={`Generate official incident report form for case ${inc.case_id}`}
                        onClick={() => handleOpenReportFiler(inc)}
                        className="flex items-center gap-1.5 px-2 py-1 border text-[9px] font-bold uppercase tracking-wider transition-colors hover:bg-white/5"
                        style={{ borderColor: 'var(--line-2)', color: 'var(--text-2)' }}
                      >
                        <FileSignature size={11} /> Police report
                      </button>

                      <button
                        onClick={() => handleExpunge(inc.id)}
                        title="Remove this incident from the map"
                        className="p-1.5 border transition-colors hover:bg-[rgba(229,52,47,0.12)]"
                        style={{ borderColor: 'var(--line-2)', color: 'var(--text-3)' }}
                      >
                        <Trash2 size={11} />
                      </button>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>
      </div>

      {/* EXPUNGE CONFIRM POPUP */}
      {expungeTargetId && (
        <div className="fixed inset-0 z-[130] flex items-center justify-center p-4" style={{ background: 'rgba(0,0,0,0.72)' }}>
          <div className="border w-full max-w-sm" style={{ background: 'var(--panel)', borderColor: 'var(--line-2)' }}>
            <div className="h-9 flex items-center gap-2 px-3 border-b" style={{ borderColor: 'var(--line)' }}>
              <Trash2 size={13} style={{ color: 'var(--critical)' }} />
              <span className="label" style={{ color: 'var(--text)' }}>Remove From Map</span>
            </div>
            <div className="p-4">
              <p className="text-[12px] leading-relaxed mb-4" style={{ color: 'var(--text-2)' }}>
                This case will be removed from the Incident Map view. It stays in the Incident Log permanently.
              </p>
              <div className="flex justify-end gap-2">
                <button
                  onClick={() => setExpungeTargetId(null)}
                  className="px-3 py-1.5 border text-[10px] font-bold uppercase tracking-wider transition-colors hover:bg-white/5"
                  style={{ borderColor: 'var(--line-2)', color: 'var(--text-2)' }}
                >
                  Cancel
                </button>
                <button
                  onClick={confirmExpunge}
                  className="px-3 py-1.5 text-[10px] font-bold uppercase tracking-wider text-white transition-opacity hover:opacity-90"
                  style={{ background: 'var(--critical)' }}
                >
                  Remove
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* FILING MODAL */}
      {showFilingModal && filingTarget && (
        <div className="fixed inset-0 z-[120] flex items-center justify-center p-4" style={{ background: 'rgba(0,0,0,0.82)' }}>
          <div
            className="border w-full max-w-3xl max-h-[90vh] overflow-y-auto custom-scrollbar"
            style={{ background: 'var(--panel)', borderColor: 'var(--line-2)' }}
          >
            <div
              className="sticky top-0 z-10 h-11 flex justify-between items-center px-3 border-b"
              style={{ background: 'var(--panel)', borderColor: 'var(--line)' }}
            >
              <div className="flex items-center gap-2.5">
                <FileSignature size={15} style={{ color: 'var(--accent)' }} />
                <div>
                  <div className="text-[12px] font-bold uppercase tracking-wide text-white leading-none">Official Police Incident Report</div>
                  <div className="label mt-1">Republic of the Philippines · Ormoc Police District</div>
                </div>
              </div>
              <button
                title="Close report filing form"
                onClick={closeModal}
                className="transition-colors hover:text-white"
                style={{ color: 'var(--text-3)' }}
              >
                <X size={16} />
              </button>
            </div>

            <div className="p-4 space-y-4">
              <div
                className="grid grid-cols-3 gap-4 p-3 border"
                style={{ background: 'var(--panel-2)', borderColor: 'var(--line)' }}
              >
                <div>
                  <span className={labelClass}>Case ID</span>
                  <span className="data text-[12px] font-bold" style={{ color: 'var(--accent)' }}>{filingTarget.case_id}</span>
                </div>
                <div>
                  <span className={labelClass}>Type</span>
                  <span className="text-[12px] font-bold uppercase text-white">{filingTarget.type}</span>
                </div>
                <div>
                  <span className={labelClass}>Occurred</span>
                  <span className="data text-[12px]" style={{ color: 'var(--text)' }}>
                    {filingTarget.occurred_date} {formatTo12Hour(filingTarget.occurred_time)}
                  </span>
                </div>
              </div>

              <div className="space-y-2">
                <h4 className="label flex items-center gap-1.5">
                  <Video size={11} style={{ color: 'var(--text-3)' }} /> Scene evidence capture
                </h4>
                {!brokenImages[filingTarget.id] ? (
                  <div
                    className="w-full max-h-72 overflow-hidden border relative flex items-center justify-center"
                    style={{ background: '#000', borderColor: 'var(--line)' }}
                  >
                    <img
                      src={reportImageUrl}
                      className="w-full h-full object-contain max-h-72"
                      alt={`Evidence capture for case ${filingTarget.case_id}`}
                      onError={() => setBrokenImages(prev => ({ ...prev, [filingTarget.id]: true }))}
                    />
                  </div>
                ) : (
                  <div
                    className="w-full h-32 border border-dashed flex flex-col items-center justify-center gap-1.5"
                    style={{ background: 'var(--bg)', borderColor: 'var(--line-2)' }}
                  >
                    <ImageIcon size={18} style={{ color: 'var(--text-3)' }} />
                    <span className="label">Evidence capture not found</span>
                  </div>
                )}
                {!filingTarget.screenshot_path && (
                  <p className="label">Placeholder image — no AI evidence frame was attached to this case.</p>
                )}
              </div>

              <div className="space-y-3">
                <h4 className="label flex items-center gap-1.5 pb-1.5 border-b" style={{ borderColor: 'var(--line)' }}>
                  <ShieldCheck size={11} style={{ color: 'var(--text-3)' }} /> Officer credentials
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

              <div className="space-y-3">
                <h4 className="label flex items-center gap-1.5 pb-1.5 border-b" style={{ borderColor: 'var(--line)' }}>
                  <MapPin size={11} style={{ color: 'var(--text-3)' }} /> Scene details
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

              <div className="space-y-3">
                <h4 className="label flex items-center gap-1.5 pb-1.5 border-b" style={{ borderColor: 'var(--line)' }}>
                  <Info size={11} style={{ color: 'var(--text-3)' }} /> Involved parties
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

              <div className="space-y-3">
                <h4 className="label flex items-center gap-1.5 pb-1.5 border-b" style={{ borderColor: 'var(--line)' }}>
                  <FileText size={11} style={{ color: 'var(--text-3)' }} /> Evidence and damage
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

              <div className="space-y-3">
                <h4 className="label flex items-center gap-1.5 pb-1.5 border-b" style={{ borderColor: 'var(--line)' }}>
                  <ShieldAlert size={11} style={{ color: 'var(--text-3)' }} /> Disposition and signatures
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
                      className="w-full border p-2.5 text-[12px] cursor-not-allowed outline-none resize-none"
                      style={{ background: 'var(--panel-2)', borderColor: 'var(--line)', color: 'var(--text-2)' }}
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

            <div
              className="sticky bottom-0 border-t px-4 py-3 flex justify-between items-center gap-3"
              style={{ background: 'var(--panel)', borderColor: 'var(--line)' }}
            >
              {/* Say WHY the submit is disabled -- an officer staring at a
                  greyed-out button otherwise has to guess which of ~11 fields
                  is the blocker. */}
              <span className="label">
                {(!reportForm.badgeNumber || !reportForm.reportingOfficer)
                  ? 'Officer name and badge number are required'
                  : 'Ready to file'}
              </span>
              <div className="flex gap-2 shrink-0">
                <button
                  title="Cancel and close report filing"
                  onClick={closeModal}
                  className="px-3.5 py-2 border text-[10px] uppercase font-bold tracking-wider transition-colors hover:bg-white/5"
                  style={{ borderColor: 'var(--line-2)', color: 'var(--text-2)' }}
                >
                  Cancel
                </button>
                <button
                  title="Commit and sign the official police report"
                  onClick={handleSubmitOfficialReport}
                  disabled={!reportForm.badgeNumber || !reportForm.reportingOfficer}
                  className="px-4 py-2 text-[10px] tracking-wider font-bold uppercase text-white transition-opacity hover:opacity-90 disabled:opacity-30 disabled:cursor-not-allowed flex items-center gap-2"
                  style={{ background: 'var(--accent)' }}
                >
                  <Check size={13} /> File report
                </button>
              </div>
            </div>

          </div>
        </div>
      )}
    </div>
  );
}