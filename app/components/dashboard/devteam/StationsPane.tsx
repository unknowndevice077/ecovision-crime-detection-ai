"use client";

import { useMemo, useState } from 'react';
import { Lock, MapPinned, Pencil, Plus, Radio, Search, Trash2, X } from 'lucide-react';
import {
  EmptyPane, FieldInput, InfoRow, ManagedUser, PaneHeader, PendingLocation, SectionLabel, SelectInput, Station,
  TextAreaInput, authHeaders, roleLabel, roleStyle,
} from './shared';

type StationForm = {
  name: string; station_type: string; parent_office: string; regional_office: string;
  commander: string; address: string; contact_number: string; description: string;
};
const EMPTY_STATION_FORM: StationForm = {
  name: '', station_type: '', parent_office: '', regional_office: '',
  commander: '', address: '', contact_number: '', description: '',
};

type BarangayForm = {
  name: string; psgc_code: string; city_municipality: string; province: string; region: string;
  captain_name: string; hall_address: string; contact_number: string; description: string;
  lat: string; lng: string;
};
const EMPTY_BARANGAY_FORM: BarangayForm = {
  name: '', psgc_code: '', city_municipality: '', province: '', region: '',
  captain_name: '', hall_address: '', contact_number: '', description: '', lat: '', lng: '',
};

// PNP unit types below a City/Provincial Police Office.
const STATION_TYPES = [
  'City Police Station (CPS)',
  'Municipal Police Station (MPS)',
  'Police Community Precinct (PCP)',
  'Police Sub-Station',
];

const PNP_REGIONAL_OFFICES = [
  'NCRPO — National Capital Region', 'PRO-COR — Cordillera', 'PRO 1 — Ilocos Region', 'PRO 2 — Cagayan Valley',
  'PRO 3 — Central Luzon', 'PRO 4A — CALABARZON', 'PRO 4B — MIMAROPA', 'PRO 5 — Bicol Region',
  'PRO 6 — Western Visayas', 'PRO NIR — Negros Island Region', 'PRO 7 — Central Visayas', 'PRO 8 — Eastern Visayas',
  'PRO 9 — Zamboanga Peninsula', 'PRO 10 — Northern Mindanao', 'PRO 11 — Davao Region', 'PRO 12 — SOCCSKSARGEN',
  'PRO 13 — Caraga', 'PRO BAR — Bangsamoro',
];

const PH_REGIONS = [
  'NCR — National Capital Region', 'CAR — Cordillera Administrative Region',
  'Region I — Ilocos Region', 'Region II — Cagayan Valley', 'Region III — Central Luzon',
  'Region IV-A — CALABARZON', 'MIMAROPA Region', 'Region V — Bicol Region',
  'Region VI — Western Visayas', 'NIR — Negros Island Region', 'Region VII — Central Visayas',
  'Region VIII — Eastern Visayas', 'Region IX — Zamboanga Peninsula', 'Region X — Northern Mindanao',
  'Region XI — Davao Region', 'Region XII — SOCCSKSARGEN', 'Region XIII — Caraga', 'BARMM',
];

// Mirrors backend.py's MIN_REGISTRATION_REASON.
const MIN_REASON = 20;

// Registering a jurisdiction changes who can see which cameras and
// incidents, so both forms end with a written reason (audit-logged) and a
// fresh DevTeam password re-entry.
function AuthorizationFields({ reason, setReason, password, setPassword, label = 'Reason for registering *' }: {
  reason: string; setReason: (v: string) => void; password: string; setPassword: (v: string) => void; label?: string;
}) {
  const short = reason.trim().length < MIN_REASON;
  return (
    <div className="border border-[var(--warn)]/30 bg-[var(--warn)]/[0.04] p-4 space-y-3">
      <div className="flex items-center gap-1.5 text-[8px] tracking-[0.15em] uppercase text-[var(--warn)]">
        <Lock size={10} /> Authorization — recorded in the audit log
      </div>
      <div>
        <TextAreaInput
          label={label}
          value={reason}
          onChange={setReason}
          rows={2}
          placeholder="e.g. Coordination request from the City Police Office, memo ref. no. …"
        />
        <p className={`text-[9px] mt-1 ${short ? 'text-[var(--text-3)]' : 'text-[var(--ok)]'}`}>
          {reason.trim().length}/{MIN_REASON} characters minimum
        </p>
      </div>
      <FieldInput label="Your DevTeam password *" type="password" value={password} onChange={setPassword} placeholder="re-enter to confirm it's you" />
    </div>
  );
}

export default function StationsPane({ apiUrl, stations, allLocations, users, flash, refresh }: {
  apiUrl: string; stations: Station[]; allLocations: PendingLocation[]; users: ManagedUser[];
  flash: (m: string) => void; refresh: () => void;
}) {
  const [search, setSearch] = useState('');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [jurisDraft, setJurisDraft] = useState<Record<string, string[]>>({});
  const [busy, setBusy] = useState(false);

  const [stationModalOpen, setStationModalOpen] = useState(false);
  // Set when the station modal is editing an existing station.
  const [editingStation, setEditingStation] = useState<Station | null>(null);
  const [stationForm, setStationForm] = useState<StationForm>(EMPTY_STATION_FORM);
  const [barangayModalStation, setBarangayModalStation] = useState<Station | null>(null);
  const [barangayForm, setBarangayForm] = useState<BarangayForm>(EMPTY_BARANGAY_FORM);
  const [reason, setReason] = useState('');
  const [password, setPassword] = useState('');
  const [formError, setFormError] = useState('');

  const locationName = (id: string) => allLocations.find(l => l.id === id)?.name || id;

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return stations;
    return stations.filter(st =>
      [st.name, st.station_type, st.parent_office, st.regional_office, st.commander, st.address, st.contact_number, st.description]
        .some(v => (v || '').toLowerCase().includes(q))
      || st.barangay_ids.some(b => `${b} ${locationName(b)}`.toLowerCase().includes(q)));
  }, [search, stations, allLocations]); // eslint-disable-line react-hooks/exhaustive-deps

  const selected = stations.find(s => s.id === selectedId) || null;

  const resetAuth = () => { setReason(''); setPassword(''); setFormError(''); };

  const openStationModal = () => { setEditingStation(null); setStationForm(EMPTY_STATION_FORM); resetAuth(); setStationModalOpen(true); };

  const openEditStation = (st: Station) => {
    setEditingStation(st);
    setStationForm({
      name: st.name || '', station_type: st.station_type || '', parent_office: st.parent_office || '',
      regional_office: st.regional_office || '', commander: st.commander || '', address: st.address || '',
      contact_number: st.contact_number || '', description: st.description || '',
    });
    resetAuth();
    setStationModalOpen(true);
  };

  const stationUnchanged = !!editingStation && (Object.keys(EMPTY_STATION_FORM) as (keyof StationForm)[])
    .every(k => (stationForm[k] || '').trim() === ((editingStation[k] as string | null | undefined) || '').trim());

  // LGU fields pre-filled from a barangay this station already covers --
  // a station's barangays are nearly always in the same city/province.
  const openBarangayModal = (st: Station) => {
    const sibling = allLocations.find(l => st.barangay_ids.includes(l.id) && (l.city_municipality || l.province));
    setBarangayForm({
      ...EMPTY_BARANGAY_FORM,
      city_municipality: sibling?.city_municipality || '',
      province: sibling?.province || '',
      region: sibling?.region || '',
    });
    resetAuth();
    setBarangayModalStation(st);
  };

  const authProblem = () => {
    if (reason.trim().length < MIN_REASON) return `Give a reason (at least ${MIN_REASON} characters).`;
    if (!password) return 'Enter your DevTeam password.';
    return null;
  };

  const createStation = async () => {
    const name = stationForm.name.trim();
    if (!name) return setFormError('Station name is required.');
    if (!stationForm.station_type) return setFormError('Pick the unit type.');
    if (stationUnchanged) return setFormError('Nothing has changed.');
    const problem = authProblem();
    if (problem) return setFormError(problem);
    setBusy(true);
    setFormError('');
    const editing = editingStation;
    try {
      const res = await fetch(editing ? `${apiUrl}/api/devteam/stations/${editing.id}` : `${apiUrl}/api/devteam/stations`, {
        method: editing ? 'PATCH' : 'POST', headers: authHeaders(),
        body: JSON.stringify({ ...stationForm, name, reason: reason.trim(), confirm_password: password }),
      });
      const d = await res.json().catch(() => ({}));
      if (res.ok) {
        setStationModalOpen(false);
        setSelectedId(d.id);
        refresh();
        flash(editing ? `${name} updated.` : `Station "${name}" registered.`);
      } else setFormError(d.detail || (editing ? 'Could not update station.' : 'Could not register station.'));
    } catch {
      setFormError('Backend connection failure.');
    } finally {
      setBusy(false);
    }
  };

  const createBarangay = async () => {
    const st = barangayModalStation;
    if (!st) return;
    const name = barangayForm.name.trim();
    if (!name) return setFormError('Barangay name is required.');
    if (!barangayForm.city_municipality.trim()) return setFormError('City / municipality is required.');
    const psgc = barangayForm.psgc_code.replace(/\s/g, '');
    if (psgc && !/^\d{9,10}$/.test(psgc)) return setFormError('PSGC code must be 10 digits (e.g. 0837370015).');
    const lat = barangayForm.lat.trim() ? Number(barangayForm.lat) : null;
    const lng = barangayForm.lng.trim() ? Number(barangayForm.lng) : null;
    if ((lat !== null && Number.isNaN(lat)) || (lng !== null && Number.isNaN(lng))) return setFormError('Latitude/longitude must be numbers.');
    const problem = authProblem();
    if (problem) return setFormError(problem);
    setBusy(true);
    setFormError('');
    try {
      const res = await fetch(`${apiUrl}/api/devteam/stations/${st.id}/barangays`, {
        method: 'POST', headers: authHeaders(),
        body: JSON.stringify({ ...barangayForm, name, psgc_code: psgc, lat, lng, reason: reason.trim(), confirm_password: password }),
      });
      const d = await res.json().catch(() => ({}));
      if (res.ok) {
        setBarangayModalStation(null);
        refresh();
        flash(`Barangay ${name} registered under ${st.name}.`);
      } else setFormError(d.detail || 'Could not register barangay.');
    } catch {
      setFormError('Backend connection failure.');
    } finally {
      setBusy(false);
    }
  };

  const toggleJurisdiction = (st: Station, barangayId: string) => {
    setJurisDraft(prev => {
      const current = prev[st.id] ?? st.barangay_ids;
      const next = current.includes(barangayId) ? current.filter(b => b !== barangayId) : [...current, barangayId];
      return { ...prev, [st.id]: next };
    });
  };

  const saveJurisdiction = async (st: Station) => {
    const draft = jurisDraft[st.id];
    if (!draft) return;
    setBusy(true);
    try {
      const res = await fetch(`${apiUrl}/api/devteam/stations/${st.id}/jurisdiction`, {
        method: 'PUT', headers: authHeaders(), body: JSON.stringify({ barangay_ids: draft }),
      });
      const d = await res.json().catch(() => ({}));
      if (res.ok) {
        setJurisDraft(prev => { const n = { ...prev }; delete n[st.id]; return n; });
        refresh();
        flash(`${st.name} now covers ${draft.length} barangay${draft.length === 1 ? '' : 's'}.`);
      } else flash(d.detail || 'Could not update jurisdiction.');
    } catch {
      flash('Backend connection failure.');
    } finally {
      setBusy(false);
    }
  };

  const deleteStation = async (st: Station) => {
    if (st.staff_count > 0) return;
    // Permanent (a station isn't soft-deleted), and it used to go on one click.
    if (!window.confirm(`Delete ${st.name}?

Its barangays lose their police coverage until another station takes them. This can't be undone.`)) return;
    setBusy(true);
    try {
      const res = await fetch(`${apiUrl}/api/devteam/stations/${st.id}`, { method: 'DELETE', headers: authHeaders() });
      const d = await res.json().catch(() => ({}));
      if (res.ok) { setSelectedId(null); refresh(); flash(`${st.name} deleted.`); }
      else flash(d.detail || 'Could not delete station.');
    } catch {
      flash('Backend connection failure.');
    } finally {
      setBusy(false);
    }
  };

  const draft = selected ? (jurisDraft[selected.id] ?? selected.barangay_ids) : [];
  // Only approved barangays can be covered. A pending one this station
  // already covers (linked before that rule) is listed so it can be
  // unticked; rejected ones are in Approvals > Rejected, never here.
  const coverable = selected
    ? allLocations.filter(l => (l.status || 'approved') === 'approved'
        || (l.status === 'pending' && selected.barangay_ids.includes(l.id)))
    : [];
  const awaitingReview = allLocations.filter(l => l.status === 'pending').length;
  const dirty = !!selected && draft.slice().sort().join(',') !== selected.barangay_ids.slice().sort().join(',');
  const staff = selected ? users.filter(u => u.station_id === selected.id) : [];

  return (
    <div className="flex-1 min-h-0 grid grid-cols-1 md:grid-cols-2 gap-6 px-7 pb-7 pt-4">
      <div className="min-h-0 flex flex-col border border-[var(--line)]">
        <PaneHeader
          icon={<Radio size={12} />}
          title="Police stations"
          right={
            <button onClick={openStationModal} className="flex items-center gap-1 px-2.5 py-1 bg-[var(--accent)] text-[#fff] text-[9px] tracking-[0.12em] uppercase hover:opacity-90">
              <Plus size={11} /> Add station
            </button>
          }
        />
        <div className="shrink-0 flex items-center gap-2 px-3 py-2 border-b border-[var(--line)]">
          <Search size={12} className="text-[var(--text-2)] shrink-0" />
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="search stations, commanders, addresses or barangays"
            className="bg-transparent text-[11px] text-[var(--text)] outline-none w-full placeholder:text-[var(--text-3)]"
          />
          <span className="text-[9px] text-[var(--text-3)] shrink-0">{filtered.length} of {stations.length}</span>
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto custom-scrollbar divide-y divide-[var(--panel-2)]">
          {stations.length === 0 ? (
            <EmptyPane text="No police stations registered" sub="PNP accounts are scoped to a station, so register one before creating its commander or officers." />
          ) : filtered.length === 0 ? (
            <EmptyPane text={`No stations match “${search}”`} />
          ) : filtered.map(st => (
            <button
              key={st.id}
              onClick={() => setSelectedId(st.id)}
              className={`w-full flex items-center gap-3 px-3 py-2.5 text-left transition-colors ${st.id === selectedId ? 'bg-[var(--accent)]/[0.08]' : 'hover:bg-[var(--panel)]'}`}
            >
              <Radio size={12} className="text-[var(--text-3)] shrink-0" />
              <div className="min-w-0 flex-1">
                <p className="text-[11px] text-[var(--text)] truncate">{st.name}</p>
                <p className="text-[9px] text-[var(--text-2)] truncate">{[st.station_type, st.parent_office].filter(Boolean).join(' · ') || 'No details on record'}</p>
              </div>
              <span className="text-[9px] text-[var(--text-3)] shrink-0">
                {st.barangay_ids.length} brgy · {st.staff_count} staff
              </span>
            </button>
          ))}
        </div>
      </div>

      <div className="min-h-0 flex flex-col border border-[var(--line)]">
        {!selected ? (
          <>
            <PaneHeader icon={<MapPinned size={12} />} title="Station details" />
            <EmptyPane text="Select a station" sub="A station owns nothing itself -- it's the set of barangays its PNP accounts can see. Widening or narrowing it never moves a camera or incident." />
          </>
        ) : (
          <>
            <PaneHeader
              icon={<MapPinned size={12} />}
              title={selected.name}
              right={
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => openEditStation(selected)}
                    className="flex items-center gap-1 px-2.5 py-1 border border-[var(--line)] text-[var(--text-2)] text-[9px] tracking-[0.12em] uppercase hover:text-[var(--text)] hover:border-[var(--text-3)]"
                  >
                    <Pencil size={10} /> Edit
                  </button>
                  <button
                    onClick={() => openBarangayModal(selected)}
                    className="flex items-center gap-1 px-2.5 py-1 border border-[var(--accent)]/40 text-[var(--accent)] text-[9px] tracking-[0.12em] uppercase hover:bg-[var(--accent)]/10"
                  >
                    <Plus size={11} /> Add barangay
                  </button>
                </div>
              }
            />
            <div className="flex-1 min-h-0 overflow-y-auto custom-scrollbar p-5 space-y-5">
              <div className="grid grid-cols-2 gap-x-4 gap-y-3">
                <InfoRow label="Unit type" value={selected.station_type} />
                <InfoRow label="Regional office" value={selected.regional_office} />
                <InfoRow label="Parent office" value={selected.parent_office} />
                <InfoRow label="Commander" value={selected.commander} />
                <InfoRow label="Hotline" value={selected.contact_number} />
                <InfoRow label="Address" value={selected.address} />
                {selected.description && <div className="col-span-2"><InfoRow label="Description" value={selected.description} /></div>}
              </div>

              <div>
                <SectionLabel>PNP accounts ({staff.length})</SectionLabel>
                {staff.length === 0 ? (
                  <p className="text-[10px] text-[var(--text-3)]">No accounts attached yet.</p>
                ) : (
                  <div className="border border-[var(--panel-2)] divide-y divide-[var(--panel-2)]">
                    {staff.map(u => {
                      const style = roleStyle(u.role);
                      return (
                        <div key={u.id} className="flex items-center gap-2.5 px-3 py-2">
                          <span className={`text-[8px] font-bold px-1.5 py-0.5 border ${style.border} ${style.text}`}>{style.code}</span>
                          <span className="text-[10px] text-[var(--text)] truncate">{u.full_name || u.username}</span>
                          <span className="text-[9px] text-[var(--text-3)] ml-auto">{u.position || roleLabel(u.role)}</span>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>

              <div>
                <SectionLabel>Jurisdiction — barangays this station can see</SectionLabel>
                {coverable.length === 0 ? (
                  <p className="text-[10px] text-[var(--text-3)]">No approved barangays yet.</p>
                ) : (
                  <div className="border border-[var(--panel-2)] divide-y divide-[var(--panel-2)] max-h-72 overflow-y-auto custom-scrollbar">
                    {coverable.map(loc => (
                      <label key={loc.id} className="flex items-center gap-2.5 px-3 py-2 cursor-pointer hover:bg-[var(--panel)]" title={loc.description || undefined}>
                        <input
                          type="checkbox"
                          checked={draft.includes(loc.id)}
                          disabled={loc.status === 'pending' && !draft.includes(loc.id)}
                          onChange={() => toggleJurisdiction(selected, loc.id)}
                          className="w-3.5 h-3.5 accent-[var(--accent)] shrink-0"
                        />
                        <span className="text-[10px] text-[var(--text)] truncate">{loc.name || loc.id}</span>
                        {loc.city_municipality && <span className="text-[9px] text-[var(--text-3)] truncate">{loc.city_municipality}</span>}
                        {loc.status === 'pending' && <span className="text-[9px] text-[var(--warn)] ml-auto shrink-0" title="Awaiting a decision in Approvals">pending review</span>}
                      </label>
                    ))}
                  </div>
                )}
                {awaitingReview > 0 && (
                  <p className="text-[9px] text-[var(--text-3)] mt-1.5">
                    {awaitingReview} barangay application{awaitingReview === 1 ? ' is' : 's are'} waiting in Approvals; approve one to make it coverable here.
                  </p>
                )}
                {dirty && (
                  <div className="flex justify-end gap-2 mt-2">
                    <button
                      onClick={() => setJurisDraft(prev => { const n = { ...prev }; delete n[selected.id]; return n; })}
                      className="px-3 py-1.5 border border-[var(--line)] text-[9px] uppercase tracking-wide text-[var(--text-2)]"
                    >
                      Discard
                    </button>
                    <button onClick={() => saveJurisdiction(selected)} disabled={busy}
                      className="px-3 py-1.5 bg-[var(--accent)] text-[#fff] text-[9px] uppercase tracking-wide disabled:opacity-40">
                      Save jurisdiction
                    </button>
                  </div>
                )}
              </div>

              <button
                onClick={() => deleteStation(selected)}
                disabled={selected.staff_count > 0 || busy}
                title={selected.staff_count ? 'Reassign its accounts first' : 'Delete station'}
                className="flex items-center gap-1.5 text-[9px] tracking-[0.1em] uppercase text-[var(--critical)]/80 hover:text-[var(--critical)] disabled:opacity-30 disabled:cursor-not-allowed"
              >
                <Trash2 size={11} /> Delete station {selected.staff_count > 0 && '(has accounts)'}
              </button>
            </div>
          </>
        )}
      </div>

      {stationModalOpen && (
        <div className="fixed inset-0 z-[130] flex items-center justify-center p-4 bg-[var(--bg)]/85">
          <div className="bg-[var(--panel)] border border-[var(--line)] w-full max-w-2xl max-h-[90vh] overflow-y-auto custom-scrollbar font-mono">
            <div className="flex items-center justify-between px-5 py-3.5 border-b border-[var(--panel-2)]">
              <div>
                <span className="text-[11px] tracking-[0.15em] uppercase text-[var(--text)]">{editingStation ? `Edit ${editingStation.name}` : 'Register a police station'}</span>
                <p className="text-[9px] text-[var(--text-3)] mt-1">
                  {editingStation
                    ? 'Changes are recorded in the audit log with the old and new value of every field.'
                    : 'PNP accounts are scoped to a station, so it has to exist before its commander or officers.'}
                </p>
              </div>
              <button title="Close" aria-label="Close" onClick={() => setStationModalOpen(false)}><X size={15} className="text-[var(--text-2)] hover:text-[var(--text)]" /></button>
            </div>
            <div className="p-5 space-y-3">
              <FieldInput label="Station name *" value={stationForm.name} onChange={v => setStationForm({ ...stationForm, name: v })} placeholder="e.g. Ormoc City Police Station 1" />
              <div className="grid grid-cols-2 gap-3">
                <SelectInput label="Unit type *" value={stationForm.station_type} onChange={v => setStationForm({ ...stationForm, station_type: v })} options={STATION_TYPES} />
                <SelectInput label="Police Regional Office" value={stationForm.regional_office} onChange={v => setStationForm({ ...stationForm, regional_office: v })} options={PNP_REGIONAL_OFFICES} />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <FieldInput label="Parent office (City / Provincial Police Office)" value={stationForm.parent_office} onChange={v => setStationForm({ ...stationForm, parent_office: v })} placeholder="e.g. Ormoc City Police Office" />
                <FieldInput label="Station commander / Chief of Police" value={stationForm.commander} onChange={v => setStationForm({ ...stationForm, commander: v })} placeholder="e.g. PLtCol. Juan Dela Cruz" />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <FieldInput label="Address" value={stationForm.address} onChange={v => setStationForm({ ...stationForm, address: v })} placeholder="Street, barangay, city" />
                <FieldInput label="Hotline / contact number" value={stationForm.contact_number} onChange={v => setStationForm({ ...stationForm, contact_number: v })} placeholder="e.g. (053) 561-0000" />
              </div>
              <TextAreaInput label="Description" value={stationForm.description} onChange={v => setStationForm({ ...stationForm, description: v })}
                placeholder="Coverage area, notable landmarks, operating notes…" />
              <AuthorizationFields reason={reason} setReason={setReason} password={password} setPassword={setPassword}
                label={editingStation ? 'Reason for this change *' : undefined} />
              {formError && <p className="text-[10px] text-[var(--critical)] uppercase tracking-wide">{formError}</p>}
              <div className="flex justify-end gap-2 pt-1">
                <button onClick={() => setStationModalOpen(false)} className="px-4 py-2.5 border border-[var(--line)] text-[10px] tracking-[0.15em] uppercase text-[var(--text-2)] hover:text-[var(--text)]">Cancel</button>
                <button onClick={createStation} disabled={busy || stationUnchanged}
                  className="px-4 py-2.5 bg-[var(--accent)] text-[#fff] text-[10px] tracking-[0.15em] uppercase disabled:opacity-30 hover:opacity-90">
                  {editingStation ? (busy ? 'Saving…' : 'Save changes') : (busy ? 'Registering…' : 'Register station')}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {barangayModalStation && (
        <div className="fixed inset-0 z-[130] flex items-center justify-center p-4 bg-[var(--bg)]/85">
          <div className="bg-[var(--panel)] border border-[var(--line)] w-full max-w-2xl max-h-[90vh] overflow-y-auto custom-scrollbar font-mono">
            <div className="flex items-center justify-between px-5 py-3.5 border-b border-[var(--panel-2)]">
              <div>
                <span className="text-[11px] tracking-[0.15em] uppercase text-[var(--text)]">Register a barangay</span>
                <p className="text-[9px] text-[var(--text-3)] mt-1">Covered by <span className="text-[var(--text-2)]">{barangayModalStation.name}</span> from the moment it&apos;s created.</p>
              </div>
              <button title="Close" aria-label="Close" onClick={() => setBarangayModalStation(null)}><X size={15} className="text-[var(--text-2)] hover:text-[var(--text)]" /></button>
            </div>
            <div className="p-5 space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <FieldInput label="Barangay name *" value={barangayForm.name} onChange={v => setBarangayForm({ ...barangayForm, name: v })} placeholder="e.g. Cogon" />
                <FieldInput label="PSGC code (10 digits)" value={barangayForm.psgc_code} onChange={v => setBarangayForm({ ...barangayForm, psgc_code: v })} placeholder="e.g. 0837370015" />
              </div>
              <div className="grid grid-cols-3 gap-3">
                <FieldInput label="City / Municipality *" value={barangayForm.city_municipality} onChange={v => setBarangayForm({ ...barangayForm, city_municipality: v })} placeholder="e.g. Ormoc City" />
                <FieldInput label="Province" value={barangayForm.province} onChange={v => setBarangayForm({ ...barangayForm, province: v })} placeholder="e.g. Leyte" />
                <SelectInput label="Region" value={barangayForm.region} onChange={v => setBarangayForm({ ...barangayForm, region: v })} options={PH_REGIONS} />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <FieldInput label="Punong Barangay (captain)" value={barangayForm.captain_name} onChange={v => setBarangayForm({ ...barangayForm, captain_name: v })} placeholder="e.g. Hon. Maria Santos" />
                <FieldInput label="Barangay hall contact number" value={barangayForm.contact_number} onChange={v => setBarangayForm({ ...barangayForm, contact_number: v })} placeholder="e.g. 0917-XXX-XXXX" />
              </div>
              <FieldInput label="Barangay hall address" value={barangayForm.hall_address} onChange={v => setBarangayForm({ ...barangayForm, hall_address: v })} placeholder="Street / purok, barangay, city" />
              <div className="grid grid-cols-2 gap-3">
                <FieldInput label="Latitude (map center, optional)" value={barangayForm.lat} onChange={v => setBarangayForm({ ...barangayForm, lat: v })} placeholder="e.g. 11.0176" />
                <FieldInput label="Longitude (map center, optional)" value={barangayForm.lng} onChange={v => setBarangayForm({ ...barangayForm, lng: v })} placeholder="e.g. 124.6031" />
              </div>
              <TextAreaInput label="Description" value={barangayForm.description} onChange={v => setBarangayForm({ ...barangayForm, description: v })}
                placeholder="Puroks/sitios covered, population, known hotspots, landmarks…" />
              <p className="text-[9px] leading-relaxed text-[var(--text-3)]">
                The PSGC code is on the Philippine Statistics Authority&apos;s PSGC listing (psa.gov.ph/classification/psgc). Already registered? Tick it in the station&apos;s jurisdiction list instead.
              </p>
              <AuthorizationFields reason={reason} setReason={setReason} password={password} setPassword={setPassword} />
              {formError && <p className="text-[10px] text-[var(--critical)] uppercase tracking-wide">{formError}</p>}
              <div className="flex justify-end gap-2 pt-1">
                <button onClick={() => setBarangayModalStation(null)} className="px-4 py-2.5 border border-[var(--line)] text-[10px] tracking-[0.15em] uppercase text-[var(--text-2)] hover:text-[var(--text)]">Cancel</button>
                <button onClick={createBarangay} disabled={busy}
                  className="px-4 py-2.5 bg-[var(--accent)] text-[#fff] text-[10px] tracking-[0.15em] uppercase disabled:opacity-30 hover:opacity-90">
                  {busy ? 'Registering…' : 'Register barangay'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
