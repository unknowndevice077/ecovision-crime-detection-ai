"use client";

import React, { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import {
  X, ShieldCheck, Trash2, Plus,
  Info, AlertCircle, FileSignature, FileText,
  Calendar, ListFilter, ShieldAlert, Radio, Check, ArrowLeft, Globe, ImageIcon,
  Sparkles, RotateCcw, Save, Pencil
} from 'lucide-react';
import { useRuntimeConfig } from '../hooks/useRuntimeConfig';
import { useLiveChannel } from '../context/WebSocketContext';

// Every fetch in this file used to skip this entirely -- backend.py's
// require_auth() 401s an unauthenticated request, so every read AND write
// here (view incidents, file a report, archive one, confirm-and-report) was
// silently failing. The read path masked it further: fetchIncidents() below
// caught the resulting crash and fell back to SAMPLE_REPORTS, so the map
// showed three fake incidents forever and nobody could tell real ones
// weren't loading.
function authHeaders() {
  const token = typeof window !== "undefined" ? localStorage.getItem("ecoToken") : null;
  return { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) };
}

// BUG FOUND 2026-09-02 (full account/feature sweep): both write paths below
// (manual incident filing, manual camera registration) sent a literal
// barangay_id: "cogon" -- not a fallback, unconditional -- so a real admin
// from any OTHER barangay filing a report or registering a camera here had
// it silently saved under Cogon's jurisdiction instead of their own. Their
// own barangay's incident/camera views would never show it; Cogon's would,
// wrongly. This is the actual database row's owner, which has real scoping
// consequences via apply_scope()/scope_clause(). Matches the same "fell back to cogon for every account" bug
// class already fixed in page.tsx's fetchCameras/fetchStats.
function currentUserBarangayId(): string {
  if (typeof window === "undefined") return "cogon";
  try {
    const u = JSON.parse(localStorage.getItem("ecoUser") || "{}");
    return u.barangay_id && u.barangay_id !== "undefined" ? u.barangay_id : "cogon";
  } catch {
    return "cogon";
  }
}

// A smartpole is a registered camera pinned on the map (cameras.lat/lng,
// 2026-10-01). These used to be three hardcoded Cogon poles that ignored
// what was actually registered.
type SmartpoleNode = {
  id: string; name: string; street: string; lat: number; lng: number; barangay_id: string;
};

type CameraRow = {
  id: string; name: string; barangay_id: string;
  lat: number | null; lng: number | null; location_label: string | null;
};

// Placing a pole: pick a spot, confirm it, then describe it. moveId set
// means re-pinning an existing camera rather than registering one.
type Placement = {
  step: 'pick' | 'confirm' | 'details';
  lat?: number; lng?: number;
  moveId?: string;
};


type Incident = {
  id: string; case_id: string; type: string; officer: string;
  lat: number; lng: number; location_name: string;
  severity: string; occurred_date: string; occurred_time: string;
  narrative: string; nature_of_call: string; arrival_reason: string;
  additional_officers: string; status: string;
  screenshot_path?: string;
  map_hidden?: number | boolean;
  camera_id?: string | null;
};

// Officer's report body -- stored whole as incident_reports.report_body.
// Keys are snake_case to match backend.py's confirm_and_report /
// _REPORT_REQUIRED (the old camelCase form was silently discarded).
type ReportBody = {
  incident_type: string; severity: string; nature_of_incident: string; narrative: string;
  complainant: string; victim_details: string; suspect_description: string; witnesses: string;
  property_damaged: string; evidence_secured: string;
  action_taken: string; disposition: string; additional_officers: string;
  reporting_officer: string; rank: string; badge_number: string; supervisor: string;
};

const EMPTY_REPORT: ReportBody = {
  incident_type: '', severity: '', nature_of_incident: '', narrative: '',
  complainant: '', victim_details: '', suspect_description: '', witnesses: '',
  property_damaged: '', evidence_secured: '',
  action_taken: '', disposition: '', additional_officers: '',
  reporting_officer: '', rank: '', badge_number: '', supervisor: '',
};

// Mirrors backend.py's build_ai_report_draft() return value.
type AiDraft = {
  incident_type: string; severity: string; occurred_date: string; occurred_time: string; time_of_day: string;
  location: { camera_name?: string; barangay?: string; city_municipality?: string; province?: string; station?: string };
  detection: {
    source: string; detector?: string; confidence?: number; confidence_band: string;
    people_in_frame?: number; attribution?: string; track_id?: number;
    weapons: { name: string; conf: number }[];
  };
  // Clips are named "Assault 1", "Assault 2"...; file names and integrity
  // hashes stay in the record, not in what the officer reads.
  evidence: { snapshot?: string; clips: { label: string; length?: string | null }[] };
  narrative: string; suspect_description: string; recommended_action: string;
};

type FiledReport = {
  id: string; report_status: string; report_body: Partial<ReportBody> | null;
  reported_by_username?: string; created_at?: string; updated_at?: string;
};

const INCIDENT_TYPES = ['ASSAULT', 'ARMED THREAT', 'ROBBERY', 'THEFT', 'PHYSICAL VIOLENCE', 'VANDALISM', 'HARDWARE_PANIC_INTERRUPT'];
const DISPOSITIONS = [
  'Under investigation',
  'Referred to prosecutor (inquest / regular filing)',
  'Settled at barangay level (Katarungang Pambarangay)',
  'Suspect apprehended — case filed',
  'Unfounded / false alarm',
];
const POLICE_REPORT_ROLES = new Set(['PNP_ADMIN', 'PNP_OFFICER', 'DEVTEAM']);

function aiToBody(ai: AiDraft): ReportBody {
  const evidence: string[] = [];
  if (ai.evidence.snapshot) {
    evidence.push('Still image captured at the moment of detection');
  }
  ai.evidence.clips.forEach(c => evidence.push(`Video clip "${c.label}"${c.length ? ` (${c.length})` : ''}`));
  return {
    ...EMPTY_REPORT,
    incident_type: ai.incident_type || '',
    severity: ai.severity || 'HIGH',
    nature_of_incident: ai.detection.source === 'AI_AUTOMATION' ? 'AI surveillance alert'
      : ai.detection.source === 'HARDWARE_PANIC' ? 'Panic button activation' : 'Operator-filed report',
    narrative: ai.narrative,
    suspect_description: ai.suspect_description,
    evidence_secured: evidence.join('\n'),
  };
}

interface CrimeReportsViewProps {
  onUpdate: () => void;
  onDeepLink?: (crimeId: string) => void;
  currentUserRole?: string;
  // From the account's real permissions (usePermissions), not its role:
  // Add smartpole / Move pin show only for accounts that can manage
  // cameras. The backend enforces the same (manage_cameras + ownership).
  canManageCameras?: boolean;
}

export default function CrimeReportsView({ onUpdate, onDeepLink, currentUserRole, canManageCameras = false }: CrimeReportsViewProps) {
  const { apiUrl: API_URL } = useRuntimeConfig();
  const [cameras, setCameras] = useState<CameraRow[]>([]);
  const poles = useMemo<SmartpoleNode[]>(() => cameras
    .filter(c => c.lat != null && c.lng != null)
    .map(c => ({ id: c.id, name: c.name, street: c.location_label || '', lat: c.lat as number, lng: c.lng as number, barangay_id: c.barangay_id })),
  [cameras]);
  const unplacedCameras = useMemo(() => cameras.filter(c => c.lat == null || c.lng == null), [cameras]);
  // Leaflet handlers are bound once; they read the live lists via refs.
  const polesRef = useRef<SmartpoleNode[]>([]);
  polesRef.current = poles;
  const [selectedPoleId, setSelectedPoleId] = useState<string | null>(null);
  const selectedPole = useMemo<SmartpoleNode | null>(() => {
    return selectedPoleId ? poles.find(p => p.id === selectedPoleId) ?? null : null;
  }, [selectedPoleId, poles]);
  const [placement, setPlacement] = useState<Placement | null>(null);
  const placementRef = useRef<Placement | null>(null);
  placementRef.current = placement;
  const draftMarkerRef = useRef<any>(null);
  const [placeMode, setPlaceMode] = useState<'new' | 'existing'>('new');
  const [placeExistingId, setPlaceExistingId] = useState('');
  const [newSmartpoleLabel, setNewSmartpoleLabel] = useState('');
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [poleDateFilter, setPoleDateFilter] = useState("");
  const [poleTypeFilter, setPoleTypeFilter] = useState("ALL");
  const [showFilingModal, setShowFilingModal] = useState(false);
  const [expungeTargetId, setExpungeTargetId] = useState<string | null>(null);
  const [filingTarget, setFilingTarget] = useState<Incident | null>(null);
  const [actionError, setActionError] = useState('');

  // In-app form, never window.prompt(): Electron's renderer doesn't
  // implement prompt(), so the 2026-09-22 version of this button did
  // nothing in the packaged app.
  const [newSmartpoleName, setNewSmartpoleName] = useState('');
  const [newSmartpolePath, setNewSmartpolePath] = useState('');
  const [addSmartpoleBusy, setAddSmartpoleBusy] = useState(false);

  const [brokenImages, setBrokenImages] = useState<Record<string, boolean>>({});

  const [isManualFilingActive, setIsManualFilingActive] = useState(false);
  const [manualType, setFormManualType] = useState("ASSAULT");
  const [manualSeverity, setFormManualSeverity] = useState("HIGH");
  const [manualNarrative, setFormManualNarrative] = useState("");
  const canFileReports = POLICE_REPORT_ROLES.has(currentUserRole || '');
  const [aiDraft, setAiDraft] = useState<AiDraft | null>(null);
  const [aiBaseline, setAiBaseline] = useState<ReportBody | null>(null);
  const [filedReport, setFiledReport] = useState<FiledReport | null>(null);
  const [reportBody, setReportBody] = useState<ReportBody>(EMPTY_REPORT);
  const [reportLoading, setReportLoading] = useState(false);
  const [reportBusy, setReportBusy] = useState(false);
  const [reportNotice, setReportNotice] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null);
  const [amending, setAmending] = useState(false);
  const reportLocked = filedReport?.report_status === 'confirmed' && !amending;
  const reportMissing = (['reporting_officer', 'badge_number', 'narrative'] as const).filter(k => !reportBody[k].trim());
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
        // The incident's real camera link; older rows without one fall
        // back to the pole's name appearing in the location text.
        const match = inc.camera_id ? inc.camera_id === selectedPole.id
          : (inc.location_name || '').toLowerCase().includes(selectedPole.name.toLowerCase());
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
      const res = await fetch(`${API_URL}/api/incidents?purpose=history`, { headers: authHeaders() });
      if (res.status === 401) {
        setActionError('Session expired -- please log in again.');
        return;
      }
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setActionError(body.detail || `Failed to load incidents (server said ${res.status}).`);
        return;
      }
      const data = await res.json();
      setIncidents(data);
      setActionError('');
    } catch {
      // Real connection failure -- SAMPLE_REPORTS is a deliberate fallback
      // here (not silently merged with live data, which is what made the
      // fake incidents invisible before), so the map still demos something
      // instead of going blank.
      // No fake incidents on a police map: it used to fall back to sample
      // crimes with stock photos here. Keep whatever was last loaded.
      setActionError('Backend unreachable -- showing the last loaded incidents.');
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
      const previousPole = polesRef.current.find(p => p.id === previousSelectedId);
      if (previousPole) {
        poleMarkersRef.current[previousSelectedId].setIcon(buildPoleIcon(L, previousPole, null));
      }
    }
    if (newSelectedId) {
      const nextPole = polesRef.current.find(p => p.id === newSelectedId);
      if (nextPole && poleMarkersRef.current[newSelectedId]) {
        poleMarkersRef.current[newSelectedId].setIcon(buildPoleIcon(L, nextPole, newSelectedId));
      }
    }
  };

  const refreshPoleIcons = () => {
    const L = (window as any).L;
    if (!L || !mapRef.current) return;

    polesRef.current.forEach(pole => {
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

  // Redrawn whenever the camera list changes (add, move, delete -- pushed
  // live on the "cameras" channel).
  const fittedRef = useRef(false);
  const createPoleMarkers = () => {
    const L = (window as any).L;
    if (!L || !mapRef.current) return;

    Object.values(poleMarkersRef.current).forEach((m: any) => m.remove());
    poleMarkersRef.current = {};
    polesRef.current.forEach(pole => {
      const marker = L.marker([pole.lat, pole.lng], {
        icon: buildPoleIcon(L, pole), zIndexOffset: 1000,
        title: pole.street ? `${pole.name} — ${pole.street}` : pole.name,
      })
        .addTo(mapRef.current)
        .on('click', (e: any) => {
          L.DomEvent.stopPropagation(e);
          if (placementRef.current) return;
          if (selectedPoleIdRef.current === pole.id) return;

          updatePoleSelectionIcons(pole.id);
          selectedPoleIdRef.current = pole.id;
          mapRef.current?.panTo([pole.lat, pole.lng], { animate: true, duration: 0.2 });

          setSelectedPoleId(pole.id);
          setIsManualFilingActive(false);
        });
      poleMarkersRef.current[pole.id] = marker;
    });
    // First load: frame the account's own poles instead of a fixed spot.
    if (!fittedRef.current && polesRef.current.length) {
      fittedRef.current = true;
      const pts = polesRef.current.map(p => [p.lat, p.lng]);
      if (pts.length === 1) mapRef.current.setView(pts[0], 17);
      else mapRef.current.fitBounds(pts, { padding: [40, 40], maxZoom: 17 });
    }
  };

  const fetchCameras = useCallback(async () => {
    try {
      const res = await fetch(`${API_URL}/api/cameras`, { headers: authHeaders() });
      if (res.ok) setCameras(await res.json());
    } catch { /* keep the last list; the incident fetch reports connectivity */ }
  }, [API_URL]);
  useLiveChannel("cameras", fetchCameras);

  useEffect(() => {
    createPoleMarkers();
    if (selectedPoleId && !poles.some(p => p.id === selectedPoleId)) {
      selectedPoleIdRef.current = null;
      setSelectedPoleId(null);
    }
  }, [poles]);

  // ── Placing a pole ────────────────────────────────────────────────────
  const clearDraftMarker = () => {
    draftMarkerRef.current?.remove();
    draftMarkerRef.current = null;
  };

  const dropDraftPin = (lat: number, lng: number) => {
    const L = (window as any).L;
    if (!L || !mapRef.current) return;
    clearDraftMarker();
    const icon = L.divIcon({
      className: 'draft-pole-icon',
      html: `<div style="position:relative;width:30px;height:42px;">
        <div style="position:absolute;left:3px;top:0;width:24px;height:24px;border-radius:50% 50% 50% 0;transform:rotate(-45deg);
          background:var(--accent);border:2px solid #fff;box-shadow:0 2px 6px rgba(0,0,0,0.6);"></div>
        <div style="position:absolute;left:13px;top:31px;width:4px;height:4px;border-radius:50%;background:var(--accent);
          box-shadow:0 0 0 4px rgba(45,111,247,0.3);"></div></div>`,
      iconSize: [30, 42], iconAnchor: [15, 33],
    });
    const m = L.marker([lat, lng], { icon, draggable: true, zIndexOffset: 2000, title: 'Drag to adjust' }).addTo(mapRef.current);
    m.on('dragend', () => {
      const ll = m.getLatLng();
      setPlacement(prev => prev ? { ...prev, lat: ll.lat, lng: ll.lng, step: 'confirm' } : prev);
    });
    draftMarkerRef.current = m;
  };

  const onMapClick = (e: any) => {
    const current = placementRef.current;
    if (!current || current.step === 'details') return;
    const { lat, lng } = e.latlng;
    dropDraftPin(lat, lng);
    setPlacement({ ...current, step: 'confirm', lat, lng });
  };
  const onMapClickRef = useRef(onMapClick);
  onMapClickRef.current = onMapClick;

  const startPlacement = (moveId?: string) => {
    setActionError('');
    setIsManualFilingActive(false);
    setPlaceMode(unplacedCameras.length && !moveId ? 'existing' : 'new');
    setPlaceExistingId(unplacedCameras[0]?.id || '');
    setNewSmartpoleName('');
    setNewSmartpolePath('');
    setNewSmartpoleLabel(moveId ? (cameras.find(c => c.id === moveId)?.location_label || '') : '');
    setPlacement({ step: 'pick', moveId });
  };

  const cancelPlacement = () => {
    clearDraftMarker();
    setPlacement(null);
    setActionError('');
  };

  useEffect(() => {
    const el = mapContainerRef.current;
    if (el) el.style.cursor = placement?.step === 'pick' ? 'crosshair' : '';
    if (!placement) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') cancelPlacement(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [placement]);

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
        mapRef.current.on('click', (e: any) => onMapClickRef.current(e));
        createPoleMarkers();
        refreshIncidentMarkers();
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
    const res = await fetch(`${API_URL}/api/incidents/${incidentId}/archive`, { method: 'PATCH', headers: authHeaders() });
    if (res.ok) {
      setIncidents(prev => prev.map(i => i.id === incidentId ? { ...i, map_hidden: 1 } : i));
      setActionError('');
      onUpdate();
    } else {
      const body = await res.json().catch(() => ({}));
      setActionError(body.detail || 'Could not dismiss that incident.');
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
      location_name: selectedPole.street ? `${selectedPole.name}, ${selectedPole.street}` : selectedPole.name,
      camera_id: selectedPole.id,
      severity: manualSeverity,
      occurred_date: now.toISOString().split('T')[0],
      occurred_time: now.toTimeString().split(' ')[0].replace(/:/g, '').substring(0,4),
      narrative: manualNarrative,
      nature_of_call: "Operator Manual Filing",
      arrival_reason: "Field Request",
      additional_officers: "None",
      status: "Active",
      // The pole's own barangay: a police account has none of its own.
      barangay_id: selectedPole.barangay_id || currentUserBarangayId()
    };
    const res = await fetch(`${API_URL}/api/incidents`, {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify(payload)
    });
    if (res.ok) {
      setFormManualNarrative("");
      setIsManualFilingActive(false);
      setActionError('');
      fetchIncidents();
      onUpdate();
    } else {
      const body = await res.json().catch(() => ({}));
      setActionError(body.detail || 'Could not file that report.');
    }
  };
  // Opens the report workspace: loads the AI's draft (built server-side from
  // the detection metadata + evidence files) and the latest officer report,
  // if any. An existing report wins over the AI draft for prefilling.
  const handleOpenReportFiler = async (target: Incident) => {
    setFilingTarget(target);
    setShowFilingModal(true);
    setAiDraft(null);
    setAiBaseline(null);
    setFiledReport(null);
    setReportBody(EMPTY_REPORT);
    setAmending(false);
    setReportNotice(null);
    setReportLoading(true);
    try {
      const res = await fetch(`${API_URL}/api/incidents/${target.id}/report_draft`, { headers: authHeaders() });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) {
        setReportNotice({ tone: 'error', text: d.detail || 'Could not load the AI draft.' });
        return;
      }
      const baseline = d.ai_draft ? aiToBody(d.ai_draft) : EMPTY_REPORT;
      setAiDraft(d.ai_draft);
      setAiBaseline(baseline);
      setFiledReport(d.report);
      setReportBody(d.report?.report_body ? { ...baseline, ...d.report.report_body } : baseline);
    } catch {
      setReportNotice({ tone: 'error', text: 'Backend connection failure.' });
    } finally {
      setReportLoading(false);
    }
  };

  const saveReportDraft = async () => {
    if (!filingTarget) return;
    setReportBusy(true);
    setReportNotice(null);
    try {
      const res = await fetch(`${API_URL}/api/incidents/${filingTarget.id}/report_draft`, {
        method: 'PUT', headers: authHeaders(), body: JSON.stringify({ report_body: reportBody }),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) {
        setReportNotice({ tone: 'error', text: d.detail || 'Draft was NOT saved.' });
        return;
      }
      setFiledReport({ id: d.id, report_status: 'draft', report_body: reportBody, updated_at: new Date().toISOString() });
      setAmending(false);
      setReportNotice({ tone: 'ok', text: 'Draft saved. It is not an official report until confirmed.' });
    } catch {
      setReportNotice({ tone: 'error', text: 'Backend connection failure -- draft was NOT saved.' });
    } finally {
      setReportBusy(false);
    }
  };

  const handleSubmitOfficialReport = async () => {
    if (!filingTarget || reportMissing.length) return;
    setReportBusy(true);
    setReportNotice(null);
    try {
      const res = await fetch(`${API_URL}/api/incidents/${filingTarget.id}/confirm-and-report`, {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({ status: "Confirmed", capture_snapshot: true, report_details: reportBody }),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) {
        setReportNotice({ tone: 'error', text: d.detail || 'Could not file that report -- it was NOT saved.' });
        return;
      }
      setFiledReport({ id: filedReport?.id || '', report_status: 'confirmed', report_body: reportBody, updated_at: new Date().toISOString() });
      setAmending(false);
      setReportNotice({ tone: 'ok', text: 'Report confirmed and filed. The incident is now marked Confirmed.' });
      fetchIncidents();
      onUpdate();
    } catch {
      setReportNotice({ tone: 'error', text: 'Backend connection failure -- report was NOT saved.' });
    } finally {
      setReportBusy(false);
    }
  };

  const setReportField = (key: keyof ReportBody, value: string) => setReportBody(prev => ({ ...prev, [key]: value }));

  const closeModal = () => { setShowFilingModal(false); setFilingTarget(null); };

  // Saves a confirmed spot: re-pins an existing camera, or registers a new
  // one already placed.
  const placingExisting = !placement?.moveId && placeMode === 'existing' && unplacedCameras.length > 0;
  const placementReady = !!placement?.moveId || (placingExisting
    ? !!placeExistingId : !!(newSmartpoleName.trim() && newSmartpolePath.trim()));
  const savePlacement = async () => {
    if (!placement || placement.lat == null || placement.lng == null || !placementReady) return;
    const location = { lat: placement.lat, lng: placement.lng, location_label: newSmartpoleLabel.trim() || null };
    const existingId = placement.moveId || (placingExisting ? placeExistingId : '');
    setAddSmartpoleBusy(true);
    setActionError('');
    try {
      const res = existingId
        ? await fetch(`${API_URL}/api/cameras/${existingId}/location`, {
            method: 'PUT', headers: authHeaders(), body: JSON.stringify(location),
          })
        : await fetch(`${API_URL}/api/cameras`, {
            method: 'POST', headers: authHeaders(),
            body: JSON.stringify({ name: newSmartpoleName.trim(), url: newSmartpolePath.trim(),
                                   barangay_id: currentUserBarangayId(), location }),
          });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        setActionError(typeof body.detail === 'string' ? body.detail : 'The smartpole was not saved.');
        return;
      }
      clearDraftMarker();
      setPlacement(null);
      await fetchCameras();
      onUpdate();
    } catch {
      setActionError('Backend connection failure -- the smartpole was not saved.');
    } finally {
      setAddSmartpoleBusy(false);
    }
  };

  const reportImageUrl = useMemo(() => {
    if (!filingTarget?.screenshot_path) return '';
    return filingTarget.screenshot_path.startsWith('http')
      ? filingTarget.screenshot_path
      : `${API_URL}${filingTarget.screenshot_path}`;
  }, [filingTarget, API_URL]);
  const handleBarangayJump = (lat: number, lng: number) => {
    if (mapRef.current) {
      mapRef.current.setView([lat, lng], 17, { animate: true });
    }
  };

  const finalLogsDisplay = filteredIncidents;
  const fieldStyle = { background: 'var(--bg)', borderColor: 'var(--line)' };
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
            title="Go to a smartpole"
            value=""
            disabled={!poles.length}
            onChange={(e) => {
              const pole = poles.find(p => p.id === e.target.value);
              if (pole) handleBarangayJump(pole.lat, pole.lng);
            }}
            className="data border px-2 py-1.5 text-[11px] outline-none cursor-pointer focus:border-[var(--accent)] transition-colors disabled:opacity-50"
            style={{ ...fieldStyle, color: 'var(--text-2)' }}
          >
            <option value="">{poles.length ? 'Select smartpole…' : 'No smartpoles pinned'}</option>
            {poles.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>

          {canManageCameras && (
          <>
          <div className="w-px h-5" style={{ background: 'var(--line-2)' }} />

          <button
            onClick={() => placement ? cancelPlacement() : startPlacement()}
            className="flex items-center gap-1.5 px-2.5 py-1.5 text-[10px] font-bold uppercase tracking-wider text-white transition-opacity hover:opacity-90"
            style={{ background: placement ? 'var(--text-3)' : 'var(--accent)' }}
          >
            {placement ? <><X size={12} /> Cancel placing</> : <><Plus size={12} /> Add smartpole</>}
          </button>
          </>
          )}
        </div>
      </div>

      {actionError && (
        <div
          className="w-full px-2.5 py-1.5 border text-[10px] font-bold uppercase tracking-wider shrink-0 flex items-center justify-between gap-2"
          style={{ background: 'rgba(229,52,47,0.08)', borderColor: 'var(--critical)', color: 'var(--critical)' }}
        >
          <span>{actionError}</span>
          <button title="Close" aria-label="Close" onClick={() => setActionError('')} className="shrink-0 hover:opacity-70"><X size={11} /></button>
        </div>
      )}

      <div className="flex-1 flex gap-2 min-h-0 w-full">
        {/* ═══ MAP CANVAS ═════════════════════════════════════════════════ */}
        <div
          className="flex-1 border relative overflow-hidden h-full"
          style={{ background: 'var(--panel)', borderColor: 'var(--line)' }}
        >
          <div ref={mapContainerRef} className="w-full h-full z-0" />

          {!poles.length && !placement && (
            <div className="absolute left-1/2 top-3 -translate-x-1/2 z-[400] px-3 py-1.5 border text-[10px] font-bold uppercase tracking-wider"
              style={{ background: 'var(--panel)', borderColor: 'var(--line-2)', color: 'var(--text-2)' }}>
              No smartpoles pinned yet{canManageCameras ? ' — use Add smartpole to place one' : ''}
            </div>
          )}

          {placement?.step === 'pick' && (
            <div className="absolute left-1/2 top-3 -translate-x-1/2 z-[400] flex items-center gap-3 px-3 py-2 border"
              style={{ background: 'var(--panel)', borderColor: 'var(--accent)' }}>
              <span className="text-[11px] font-bold uppercase tracking-wide" style={{ color: 'var(--text)' }}>
                {placement.moveId ? 'Click the map where this smartpole now stands' : 'Click the map where the smartpole stands'}
              </span>
              <span className="label">Esc to cancel</span>
            </div>
          )}

          {placement && placement.step !== 'pick' && placement.lat != null && placement.lng != null && (
            <div className="absolute right-3 top-3 z-[400] w-[300px] border shadow-xl"
              style={{ background: 'var(--panel)', borderColor: 'var(--line-2)' }}
              role="dialog" aria-label="Confirm smartpole location">
              <div className="h-9 flex items-center justify-between px-3 border-b" style={{ borderColor: 'var(--line)' }}>
                <span className="label" style={{ color: 'var(--text)' }}>
                  {placement.step === 'confirm' ? 'Confirm location' : placement.moveId ? 'Move smartpole' : 'Smartpole details'}
                </span>
                <button title="Cancel" aria-label="Cancel" onClick={cancelPlacement} className="hover:text-[var(--text)]" style={{ color: 'var(--text-3)' }}>
                  <X size={14} />
                </button>
              </div>
              <div className="p-3 space-y-3">
                {placement.step === 'confirm' ? (
                  <>
                    <p className="text-[13px] font-bold" style={{ color: 'var(--text)' }}>Is the smartpole located here?</p>
                    <p className="data text-[11px]" style={{ color: 'var(--text-2)' }}>
                      {placement.lat.toFixed(6)}, {placement.lng.toFixed(6)}
                    </p>
                    <p className="label leading-relaxed normal-case">
                      Zoom in and check the pin sits on the pole itself. Drag the pin to adjust, or click elsewhere on the map.
                      Alerts from this camera will be placed at this spot.
                    </p>
                    <div className="flex justify-end gap-2 pt-1">
                      <button
                        onClick={() => { clearDraftMarker(); setPlacement({ step: 'pick', moveId: placement.moveId }); }}
                        className="px-3 py-1.5 text-[10px] font-bold uppercase tracking-wider border hover:border-[var(--text-3)]"
                        style={{ borderColor: 'var(--line-2)', color: 'var(--text-2)' }}
                      >
                        No, pick again
                      </button>
                      <button
                        onClick={() => setPlacement({ ...placement, step: 'details' })}
                        className="px-3 py-1.5 text-[10px] font-bold uppercase tracking-wider text-white hover:opacity-90"
                        style={{ background: 'var(--accent)' }}
                      >
                        Yes, it&apos;s here
                      </button>
                    </div>
                  </>
                ) : (
                  <>
                    <p className="data text-[10px]" style={{ color: 'var(--text-3)' }}>
                      Pinned at {placement.lat.toFixed(6)}, {placement.lng.toFixed(6)}
                    </p>
                    {placement.moveId ? (
                      <p className="text-[11px]" style={{ color: 'var(--text-2)' }}>
                        Moving <b style={{ color: 'var(--text)' }}>{cameras.find(c => c.id === placement.moveId)?.name}</b>
                      </p>
                    ) : unplacedCameras.length > 0 && (
                      <div className="grid grid-cols-2 border" style={{ borderColor: 'var(--line-2)' }}>
                        {(['existing', 'new'] as const).map(m => (
                          <button key={m} onClick={() => setPlaceMode(m)}
                            className="py-1.5 text-[10px] font-bold uppercase tracking-wider"
                            style={placeMode === m ? { background: 'var(--accent)', color: '#fff' } : { color: 'var(--text-2)' }}>
                            {m === 'existing' ? 'Registered camera' : 'New smartpole'}
                          </button>
                        ))}
                      </div>
                    )}
                    {placingExisting ? (
                      <label className="block">
                        <span className={labelClass}>Camera not yet on the map</span>
                        <select value={placeExistingId} onChange={e => setPlaceExistingId(e.target.value)}
                          className="w-full data border px-2 py-1.5 text-[12px] outline-none focus:border-[var(--accent)]"
                          style={{ ...fieldStyle, color: 'var(--text)' }}>
                          {unplacedCameras.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
                        </select>
                      </label>
                    ) : !placement.moveId && (
                      <>
                        <label className="block">
                          <span className={labelClass}>Smartpole name</span>
                          <input type="text" value={newSmartpoleName} autoFocus
                            onChange={e => setNewSmartpoleName(e.target.value)} placeholder="e.g. Plaza North Pole"
                            className="w-full data border px-2 py-1.5 text-[12px] outline-none focus:border-[var(--accent)]"
                            style={{ ...fieldStyle, color: 'var(--text)' }} />
                        </label>
                        <label className="block">
                          <span className={labelClass}>Camera stream (RTSP)</span>
                          <input type="text" value={newSmartpolePath}
                            onChange={e => setNewSmartpolePath(e.target.value)} placeholder="rtsp://user:pass@192.168.1.50/live"
                            className="w-full data border px-2 py-1.5 text-[12px] outline-none focus:border-[var(--accent)]"
                            style={{ ...fieldStyle, color: 'var(--text)' }} />
                        </label>
                      </>
                    )}
                    <label className="block">
                      <span className={labelClass}>Street / landmark (optional)</span>
                      <input type="text" value={newSmartpoleLabel}
                        onChange={e => setNewSmartpoleLabel(e.target.value)} placeholder="e.g. Corner Real St. & Bonifacio"
                        className="w-full data border px-2 py-1.5 text-[12px] outline-none focus:border-[var(--accent)]"
                        style={{ ...fieldStyle, color: 'var(--text)' }} />
                    </label>
                    {actionError && (
                      <div className="px-2.5 py-1.5 border text-[10px] font-bold uppercase tracking-wider"
                        style={{ background: 'rgba(229,52,47,0.08)', borderColor: 'var(--critical)', color: 'var(--critical)' }}>
                        {actionError}
                      </div>
                    )}
                    <div className="flex justify-end gap-2 pt-1">
                      <button onClick={() => setPlacement({ ...placement, step: 'confirm' })}
                        className="px-3 py-1.5 text-[10px] font-bold uppercase tracking-wider border hover:border-[var(--text-3)]"
                        style={{ borderColor: 'var(--line-2)', color: 'var(--text-2)' }}>
                        Back
                      </button>
                      <button onClick={savePlacement} disabled={addSmartpoleBusy || !placementReady}
                        className="px-3 py-1.5 text-[10px] font-bold uppercase tracking-wider text-white hover:opacity-90 disabled:opacity-50"
                        style={{ background: 'var(--accent)' }}>
                        {addSmartpoleBusy ? 'Saving…' : placement.moveId ? 'Save new location'
                          : placingExisting ? 'Pin camera here' : 'Register smartpole'}
                      </button>
                    </div>
                  </>
                )}
              </div>
            </div>
          )}
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
                className="flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider transition-colors hover:text-[var(--text)] shrink-0"
                style={{ color: 'var(--text-2)' }}
              >
                <ArrowLeft size={12} /> Back
              </button>
            ) : (
              <Globe size={12} className="shrink-0" style={{ color: 'var(--text-3)' }} />
            )}

            <div className="min-w-0 flex-1 text-center">
              <div className="text-[11px] font-bold text-[var(--text)] uppercase tracking-wide truncate">
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
          <div className="shrink-0 px-2.5 py-1.5 border-b flex items-center gap-2" style={{ borderColor: 'var(--line)', background: 'var(--bg)' }}>
            <span className="data text-[10px] truncate block flex-1" style={{ color: 'var(--text-3)' }}>
              {selectedPole
                ? (selectedPole.street || `${selectedPole.lat.toFixed(5)}, ${selectedPole.lng.toFixed(5)}`)
                : 'Monitoring all areas'}
            </span>
            {selectedPole && canManageCameras && !placement && (
              <button
                onClick={() => startPlacement(selectedPole.id)}
                title="Move this smartpole's pin"
                className="shrink-0 text-[9px] font-bold uppercase tracking-wider hover:text-[var(--text)]"
                style={{ color: 'var(--text-2)' }}
              >
                Move pin
              </button>
            )}
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
                    className="data w-full border p-1.5 text-[11px] text-[var(--text)] outline-none focus:border-[var(--accent)]"
                    style={fieldStyle}
                  >
                    <option value="ASSAULT">Assault</option>
                    <option value="THEFT">Theft</option>
                    <option value="PHYSICAL VIOLENCE">Physical Violence</option>
                    <option value="VANDALISM">Vandalism</option>
                  </select>
                </div>
                <div>
                  <span className={labelClass}>Severity</span>
                  <select
                    title="Select severity"
                    value={manualSeverity}
                    onChange={(e) => setFormManualSeverity(e.target.value)}
                    className="data w-full border p-1.5 text-[11px] text-[var(--text)] outline-none focus:border-[var(--accent)]"
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
                  className="w-full h-14 border p-2 text-[11px] text-[var(--text)] resize-none outline-none focus:border-[var(--accent)]"
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
                <option value="PHYSICAL VIOLENCE">Physical Violence</option>
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

                    <h5 className="text-[13px] font-bold uppercase text-[var(--text)] tracking-wide">{inc.type}</h5>

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

                    {inc.narrative && (
                      <p className="text-[10px] leading-snug select-text" style={{ color: 'var(--text-2)' }}>
                        {inc.narrative}
                      </p>
                    )}

                    <div
                      className="flex gap-2 pt-1.5 border-t justify-end items-center"
                      style={{ borderColor: 'var(--line)' }}
                    >
                      {canFileReports && (
                        <button
                          title={`Open the AI draft and officer report for case ${inc.case_id}`}
                          onClick={() => handleOpenReportFiler(inc)}
                          className="flex items-center gap-1.5 px-2 py-1 border text-[9px] font-bold uppercase tracking-wider transition-colors hover:bg-white/5"
                          style={{ borderColor: 'var(--line-2)', color: 'var(--text-2)' }}
                        >
                          <FileSignature size={11} /> {(inc.status || '').toLowerCase() === 'confirmed' ? 'View report' : 'Police report'}
                        </button>
                      )}

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

      {/* REPORT WORKSPACE (2026-09-29). Left: what the AI actually recorded
          (evidence frame, detector, confidence, people, weapons, the
          evidence clips by name). Right: the officer's report, prefilled from the
          AI draft and fully editable -- fields the officer changed are
          tagged EDITED so a reviewer can see what the AI said vs. what the
          officer confirmed. Save draft keeps it unofficial; Confirm & file
          makes it the official report and confirms the incident. */}
      {showFilingModal && filingTarget && (
        <div className="fixed inset-0 z-[120] flex items-center justify-center p-4" style={{ background: 'rgba(0,0,0,0.82)' }}>
          <div
            className="border w-full max-w-6xl max-h-[92vh] flex flex-col"
            style={{ background: 'var(--panel)', borderColor: 'var(--line-2)' }}
          >
            <div className="shrink-0 h-12 flex justify-between items-center px-4 border-b" style={{ borderColor: 'var(--line)' }}>
              <div className="flex items-center gap-2.5 min-w-0">
                <FileSignature size={15} style={{ color: 'var(--accent)' }} />
                <div className="min-w-0">
                  <div className="text-[12px] font-bold uppercase tracking-wide text-[var(--text)] leading-none truncate">
                    Incident Report · <span className="data" style={{ color: 'var(--accent)' }}>{filingTarget.case_id}</span>
                  </div>
                  <div className="label mt-1">
                    {aiDraft?.location.station || 'Philippine National Police'}
                    {aiDraft?.location.city_municipality ? ` · ${aiDraft.location.city_municipality}` : ''}
                  </div>
                </div>
                {filedReport && (
                  <span
                    className="ml-2 shrink-0 px-1.5 py-0.5 border text-[9px] font-bold uppercase tracking-wider"
                    style={filedReport.report_status === 'confirmed'
                      ? { color: 'var(--ok)', borderColor: 'var(--ok)' }
                      : { color: 'var(--warn)', borderColor: 'var(--warn)' }}
                  >
                    {filedReport.report_status === 'confirmed' ? 'Filed · confirmed' : 'Draft · not official'}
                  </span>
                )}
              </div>
              <button title="Close" aria-label="Close" onClick={closeModal} className="transition-colors hover:text-[var(--text)]" style={{ color: 'var(--text-3)' }}>
                <X size={16} />
              </button>
            </div>

            {reportLoading ? (
              <div className="flex-1 flex items-center justify-center py-24">
                <span className="label">Building AI draft from detection data…</span>
              </div>
            ) : (
              <div className="flex-1 min-h-0 grid grid-cols-1 md:grid-cols-[minmax(0,360px)_1fr] overflow-hidden">

                {/* LEFT — AI findings */}
                <div className="min-h-0 overflow-y-auto custom-scrollbar border-r p-4 space-y-4" style={{ borderColor: 'var(--line)', background: 'var(--panel-2)' }}>
                  <div className="flex items-center gap-1.5">
                    <Sparkles size={12} style={{ color: 'var(--accent)' }} />
                    <span className="label" style={{ color: 'var(--text)' }}>AI findings</span>
                  </div>

                  {reportImageUrl && !brokenImages[filingTarget.id] ? (
                    <div className="w-full border overflow-hidden flex items-center justify-center" style={{ background: '#000', borderColor: 'var(--line)' }}>
                      <img
                        src={reportImageUrl}
                        className="w-full max-h-56 object-contain"
                        alt={`Evidence capture for case ${filingTarget.case_id}`}
                        onError={() => setBrokenImages(prev => ({ ...prev, [filingTarget.id]: true }))}
                      />
                    </div>
                  ) : (
                    <div className="w-full h-28 border border-dashed flex flex-col items-center justify-center gap-1.5" style={{ background: 'var(--bg)', borderColor: 'var(--line-2)' }}>
                      <ImageIcon size={18} style={{ color: 'var(--text-3)' }} />
                      <span className="label">No evidence frame on file</span>
                    </div>
                  )}

                  {aiDraft ? (
                    <>
                      <div className="border p-3 space-y-2.5" style={{ background: 'var(--panel)', borderColor: 'var(--line)' }}>
                        <div className="flex items-baseline justify-between gap-2">
                          <span className="text-[13px] font-bold uppercase tracking-wide text-[var(--text)]">{aiDraft.incident_type}</span>
                          <span className="data text-[10px]" style={{ color: 'var(--text-3)' }}>
                            {aiDraft.occurred_date} {formatTo12Hour(aiDraft.occurred_time)} · {aiDraft.time_of_day}
                          </span>
                        </div>
                        {typeof aiDraft.detection.confidence === 'number' && aiDraft.detection.source === 'AI_AUTOMATION' && (
                          <div>
                            <div className="flex justify-between mb-1">
                              <span className="label">Model confidence</span>
                              <span className="data text-[10px] font-bold" style={{ color: aiDraft.detection.confidence_band === 'high' ? 'var(--critical)' : aiDraft.detection.confidence_band === 'moderate' ? 'var(--warn)' : 'var(--text-2)' }}>
                                {Math.round(aiDraft.detection.confidence * 100)}% · {aiDraft.detection.confidence_band}
                              </span>
                            </div>
                            <div className="h-1.5 w-full" style={{ background: 'var(--bg)' }}>
                              <div className="h-full" style={{ width: `${Math.round(aiDraft.detection.confidence * 100)}%`, background: aiDraft.detection.confidence_band === 'high' ? 'var(--critical)' : aiDraft.detection.confidence_band === 'moderate' ? 'var(--warn)' : 'var(--text-3)' }} />
                            </div>
                          </div>
                        )}
                        <FactRow label="Source" value={aiDraft.detection.source === 'AI_AUTOMATION' ? 'AI surveillance' : aiDraft.detection.source === 'HARDWARE_PANIC' ? 'Panic button' : 'Manual filing'} />
                        {aiDraft.detection.detector && <FactRow label="Detector" value={aiDraft.detection.detector} />}
                        {typeof aiDraft.detection.people_in_frame === 'number' && <FactRow label="People in view" value={String(aiDraft.detection.people_in_frame)} />}
                        {aiDraft.detection.attribution && (
                          <FactRow label="Attributed to" value={aiDraft.detection.attribution === 'track' && aiDraft.detection.track_id != null ? `Tracked person #${aiDraft.detection.track_id}` : 'Whole scene'} />
                        )}
                        {aiDraft.detection.weapons.length > 0 && (
                          <FactRow label="Weapons" value={aiDraft.detection.weapons.map(w => `${w.name} (${Math.round(w.conf * 100)}%)`).join(', ')} tone="var(--critical)" />
                        )}
                        <FactRow label="Camera" value={aiDraft.location.camera_name || '—'} />
                        <FactRow label="Barangay" value={[aiDraft.location.barangay, aiDraft.location.city_municipality].filter(Boolean).join(', ') || '—'} />
                      </div>

                      <div className="border p-3 space-y-1.5" style={{ background: 'var(--panel)', borderColor: 'var(--line)' }}>
                        <span className="label block">Evidence on file</span>
                        {aiDraft.evidence.snapshot ? (
                          <p className="text-[10.5px]" style={{ color: 'var(--text-2)' }}>Still image from the moment of detection</p>
                        ) : <p className="text-[10px]" style={{ color: 'var(--text-3)' }}>No still image.</p>}
                        {aiDraft.evidence.clips.map(c => (
                          <p key={c.label} className="text-[10.5px]" style={{ color: 'var(--text-2)' }}>
                            {c.label}{c.length ? <span style={{ color: 'var(--text-3)' }}> · {c.length}</span> : null}
                          </p>
                        ))}
                      </div>

                      <div className="border p-3" style={{ background: 'var(--panel)', borderColor: 'var(--line)' }}>
                        <span className="label block mb-1">AI recommended action</span>
                        <p className="text-[11px] leading-relaxed" style={{ color: 'var(--text-2)' }}>{aiDraft.recommended_action}</p>
                      </div>
                    </>
                  ) : (
                    <p className="text-[10px]" style={{ color: 'var(--text-3)' }}>No AI draft available for this incident.</p>
                  )}
                </div>

                {/* RIGHT — officer's report */}
                <div className="min-h-0 overflow-y-auto custom-scrollbar p-4 space-y-5">
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-1.5">
                      <ShieldCheck size={12} style={{ color: 'var(--text-3)' }} />
                      <span className="label" style={{ color: 'var(--text)' }}>Officer's report</span>
                      <span className="label">— prefilled by AI, verify every field</span>
                    </div>
                    {!reportLocked && aiBaseline && (
                      <button
                        type="button"
                        onClick={() => setReportBody(prev => ({ ...prev, ...aiBaseline, reporting_officer: prev.reporting_officer, rank: prev.rank, badge_number: prev.badge_number, supervisor: prev.supervisor }))}
                        className="flex items-center gap-1 text-[9px] font-bold uppercase tracking-wider hover:text-[var(--text)] transition-colors"
                        style={{ color: 'var(--text-3)' }}
                        title="Replace the AI-prefilled fields with the original AI draft"
                      >
                        <RotateCcw size={10} /> Reset to AI draft
                      </button>
                    )}
                  </div>

                  <ReportSection icon={<AlertCircle size={11} />} title="Incident">
                    <div className="grid grid-cols-3 gap-3">
                      <ReportField label="Incident type" value={reportBody.incident_type} ai={aiBaseline?.incident_type} disabled={reportLocked}
                        onChange={v => setReportField('incident_type', v)}
                        options={Array.from(new Set([...INCIDENT_TYPES, reportBody.incident_type].filter(Boolean)))} />
                      <ReportField label="Severity" value={reportBody.severity} ai={aiBaseline?.severity} disabled={reportLocked}
                        onChange={v => setReportField('severity', v)} options={['LOW', 'MEDIUM', 'HIGH', 'CRITICAL']} />
                      <ReportField label="Nature of incident" value={reportBody.nature_of_incident} ai={aiBaseline?.nature_of_incident} disabled={reportLocked}
                        onChange={v => setReportField('nature_of_incident', v)} />
                    </div>
                    <ReportField label="Narrative" required rows={9} value={reportBody.narrative} ai={aiBaseline?.narrative} disabled={reportLocked}
                      onChange={v => setReportField('narrative', v)} />
                  </ReportSection>

                  <ReportSection icon={<Info size={11} />} title="Persons involved">
                    <div className="grid grid-cols-2 gap-3">
                      <ReportField label="Complainant" rows={2} value={reportBody.complainant} disabled={reportLocked}
                        onChange={v => setReportField('complainant', v)} placeholder="Name, address, contact number" />
                      <ReportField label="Victim(s)" rows={2} value={reportBody.victim_details} disabled={reportLocked}
                        onChange={v => setReportField('victim_details', v)} placeholder="Name, age, injuries sustained" />
                      <ReportField label="Suspect description" rows={3} value={reportBody.suspect_description} ai={aiBaseline?.suspect_description} disabled={reportLocked}
                        onChange={v => setReportField('suspect_description', v)} />
                      <ReportField label="Witnesses" rows={3} value={reportBody.witnesses} disabled={reportLocked}
                        onChange={v => setReportField('witnesses', v)} placeholder="Names and contact details" />
                    </div>
                  </ReportSection>

                  <ReportSection icon={<FileText size={11} />} title="Evidence and action">
                    <div className="grid grid-cols-2 gap-3">
                      <ReportField label="Evidence secured" rows={3} value={reportBody.evidence_secured} ai={aiBaseline?.evidence_secured} disabled={reportLocked}
                        onChange={v => setReportField('evidence_secured', v)} />
                      <ReportField label="Property damaged / stolen" rows={3} value={reportBody.property_damaged} disabled={reportLocked}
                        onChange={v => setReportField('property_damaged', v)} placeholder="Item, estimated value, owner" />
                      <ReportField label="Action taken" rows={3} value={reportBody.action_taken} disabled={reportLocked}
                        onChange={v => setReportField('action_taken', v)} placeholder={aiDraft?.recommended_action || 'Responding unit, time of arrival, what was done'} />
                      <ReportField label="Other responding officers" rows={3} value={reportBody.additional_officers} disabled={reportLocked}
                        onChange={v => setReportField('additional_officers', v)} placeholder="Names and ranks" />
                    </div>
                    <ReportField label="Disposition" value={reportBody.disposition} disabled={reportLocked}
                      onChange={v => setReportField('disposition', v)} options={['', ...DISPOSITIONS]} />
                  </ReportSection>

                  <ReportSection icon={<ShieldAlert size={11} />} title="Reporting officer">
                    <div className="grid grid-cols-4 gap-3">
                      <ReportField label="Rank" value={reportBody.rank} disabled={reportLocked}
                        onChange={v => setReportField('rank', v)} placeholder="PCpl" />
                      <ReportField label="Officer name" required value={reportBody.reporting_officer} disabled={reportLocked}
                        onChange={v => setReportField('reporting_officer', v)} placeholder="Dela Cruz, Juan" />
                      <ReportField label="Badge number" required value={reportBody.badge_number} disabled={reportLocked}
                        onChange={v => setReportField('badge_number', v)} placeholder="OCPD-2026-993" />
                      <ReportField label="Supervisor sign-off" value={reportBody.supervisor} disabled={reportLocked}
                        onChange={v => setReportField('supervisor', v)} placeholder="PLt. Santos, R." />
                    </div>
                  </ReportSection>
                </div>
              </div>
            )}

            <div className="shrink-0 border-t px-4 py-3 flex justify-between items-center gap-3" style={{ borderColor: 'var(--line)' }}>
              <span className="text-[10px] font-bold uppercase tracking-wider" style={{ color: reportNotice?.tone === 'error' ? 'var(--critical)' : reportNotice?.tone === 'ok' ? 'var(--ok)' : 'var(--text-3)' }}>
                {reportNotice?.text
                  || (reportLocked ? 'This is the filed official report.'
                    : reportMissing.length ? `Required: ${reportMissing.map(k => k.replace(/_/g, ' ')).join(', ')}`
                    : 'Ready to confirm')}
              </span>
              <div className="flex gap-2 shrink-0">
                <button onClick={closeModal} className="px-3.5 py-2 border text-[10px] uppercase font-bold tracking-wider transition-colors hover:bg-white/5" style={{ borderColor: 'var(--line-2)', color: 'var(--text-2)' }}>
                  Close
                </button>
                {reportLocked ? (
                  <button onClick={() => { setAmending(true); setReportNotice(null); }} className="px-3.5 py-2 border text-[10px] uppercase font-bold tracking-wider transition-colors hover:bg-white/5 flex items-center gap-1.5" style={{ borderColor: 'var(--line-2)', color: 'var(--text)' }}>
                    <Pencil size={11} /> Amend report
                  </button>
                ) : (
                  <>
                    <button
                      onClick={saveReportDraft}
                      disabled={reportBusy || reportLoading}
                      className="px-3.5 py-2 border text-[10px] uppercase font-bold tracking-wider transition-colors hover:bg-white/5 disabled:opacity-30 flex items-center gap-1.5"
                      style={{ borderColor: 'var(--line-2)', color: 'var(--text)' }}
                    >
                      <Save size={11} /> Save draft
                    </button>
                    <button
                      onClick={handleSubmitOfficialReport}
                      disabled={reportBusy || reportLoading || reportMissing.length > 0}
                      className="px-4 py-2 text-[10px] tracking-wider font-bold uppercase text-white transition-opacity hover:opacity-90 disabled:opacity-30 disabled:cursor-not-allowed flex items-center gap-2"
                      style={{ background: 'var(--accent)' }}
                    >
                      <Check size={13} /> {reportBusy ? 'Filing…' : 'Confirm & file'}
                    </button>
                  </>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function FactRow({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="flex justify-between gap-3">
      <span className="label shrink-0">{label}</span>
      <span className="text-[10px] text-right" style={{ color: tone || 'var(--text)' }}>{value}</span>
    </div>
  );
}

function ReportSection({ icon, title, children }: { icon: React.ReactNode; title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-3">
      <h4 className="label flex items-center gap-1.5 pb-1.5 border-b" style={{ borderColor: 'var(--line)', color: 'var(--text-2)' }}>
        <span style={{ color: 'var(--text-3)' }}>{icon}</span> {title}
      </h4>
      {children}
    </div>
  );
}

// `ai` is the AI draft's value for this field, when the AI prefilled it:
// the label then shows AI while untouched and EDITED once the officer
// changes it, so the filed report shows which statements were verified.
function ReportField({ label, value, onChange, ai, disabled, rows, options, placeholder, required }: {
  label: string; value: string; onChange: (v: string) => void; ai?: string; disabled?: boolean;
  rows?: number; options?: string[]; placeholder?: string; required?: boolean;
}) {
  const style = {
    background: disabled ? 'var(--panel-2)' : 'var(--bg)',
    borderColor: required && !value.trim() && !disabled ? 'var(--warn)' : 'var(--line)',
    color: disabled ? 'var(--text-2)' : 'var(--text)',
  };
  const cls = "w-full border p-2.5 text-[12px] outline-none focus:border-[var(--accent)] transition-colors disabled:cursor-not-allowed";
  const aiTag = ai ? (value === ai ? 'AI' : 'EDITED') : null;
  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <span className="label">{label}{required && <span style={{ color: 'var(--warn)' }}> *</span>}</span>
        {aiTag && (
          <span className="text-[8px] font-bold uppercase tracking-wider" style={{ color: aiTag === 'AI' ? 'var(--accent)' : 'var(--warn)' }}>
            {aiTag}
          </span>
        )}
      </div>
      {options ? (
        <select value={value} disabled={disabled} onChange={e => onChange(e.target.value)} className={`${cls} data`} style={style}>
          {options.map(o => <option key={o} value={o}>{o || 'Select…'}</option>)}
        </select>
      ) : rows ? (
        <textarea value={value} disabled={disabled} rows={rows} placeholder={placeholder} onChange={e => onChange(e.target.value)}
          className={`${cls} resize-y leading-relaxed`} style={style} />
      ) : (
        <input type="text" value={value} disabled={disabled} placeholder={placeholder} onChange={e => onChange(e.target.value)}
          className={`${cls} data`} style={style} />
      )}
    </div>
  );
}
