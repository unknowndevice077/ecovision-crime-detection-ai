"use client";

import React, { useEffect, useState } from 'react';
import { X, MapPin, Clock, Camera, Film, FileSignature, Sparkles, ImageIcon, CheckCircle2, ShieldX } from 'lucide-react';
import { useRuntimeConfig } from '../../hooks/useRuntimeConfig';
import { usePermissions } from '../../hooks/usePermissions';
import { serverDateTime } from '../../lib/time';

/* Everything on record about one incident, opened from the Incident Log:
 * when and where, what the AI saw, the officer's filed report, and the
 * evidence clips. Reads only endpoints the log's own permission already
 * covers (police side, view_history); clips appear when the account can
 * also open the video vault. */

type Report = {
  report_status?: string; report_body?: Record<string, string> | null;
  reported_by_username?: string; created_at?: string; updated_at?: string;
};
type Clip = { id: string; filename: string; label?: string | null; duration?: string; recorded_at: string; associated_incident_id?: string | null };

function authHeaders() {
  const token = typeof window !== "undefined" ? localStorage.getItem("ecoToken") : null;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

const REPORT_SECTIONS: { title: string; fields: [string, string][] }[] = [
  { title: 'Incident', fields: [['incident_type', 'Type'], ['severity', 'Severity'], ['nature_of_incident', 'Nature'], ['narrative', 'Narrative']] },
  { title: 'Persons involved', fields: [['complainant', 'Complainant'], ['victim_details', 'Victim'], ['suspect_description', 'Suspect'], ['witnesses', 'Witnesses']] },
  { title: 'Scene and evidence', fields: [['property_damaged', 'Property damaged'], ['evidence_secured', 'Evidence secured']] },
  { title: 'Action and disposition', fields: [['action_taken', 'Action taken'], ['disposition', 'Disposition'], ['additional_officers', 'Other officers']] },
  { title: 'Reporting officer', fields: [['reporting_officer', 'Officer'], ['rank', 'Rank'], ['badge_number', 'Badge'], ['supervisor', 'Supervisor']] },
];

function Row({ label, value }: { label: string; value?: React.ReactNode }) {
  if (value === undefined || value === null || value === '') return null;
  return (
    <div className="grid grid-cols-[110px_1fr] gap-2 py-1">
      <span className="label">{label}</span>
      <span className="text-[11px] whitespace-pre-wrap break-words" style={{ color: 'var(--text)' }}>{value}</span>
    </div>
  );
}

function Section({ icon, title, children }: { icon: React.ReactNode; title: string; children: React.ReactNode }) {
  return (
    <section className="border" style={{ borderColor: 'var(--line)', background: 'var(--panel-2)' }}>
      <div className="h-8 flex items-center gap-1.5 px-3 border-b" style={{ borderColor: 'var(--line)' }}>
        <span style={{ color: 'var(--accent)' }}>{icon}</span>
        <span className="label" style={{ color: 'var(--text)' }}>{title}</span>
      </div>
      <div className="px-3 py-2">{children}</div>
    </section>
  );
}

export default function IncidentDetail({ incident, onClose }: { incident: any; onClose: () => void }) {
  const { apiUrl: API_URL } = useRuntimeConfig();
  const { can } = usePermissions();
  const [ai, setAi] = useState<any>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [clips, setClips] = useState<Clip[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [playing, setPlaying] = useState<Clip | null>(null);
  const [imageBroken, setImageBroken] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`${API_URL}/api/incidents/${incident.id}/report_draft`, { headers: authHeaders() });
        const d = await res.json().catch(() => ({}));
        if (cancelled) return;
        if (res.ok) { setAi(d.ai_draft); setReport(d.report); }
        else setError(d.detail || 'Could not load the full record.');
        if (can('view_records')) {
          const r2 = await fetch(`${API_URL}/api/records`, { headers: authHeaders() });
          if (r2.ok && !cancelled) {
            const all: Clip[] = await r2.json();
            setClips(all.filter(c => c.associated_incident_id === incident.id)
              .sort((a, b) => (a.recorded_at < b.recorded_at ? -1 : 1)));
          }
        }
      } catch {
        if (!cancelled) setError('Backend connection failure.');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [incident.id, API_URL]); // eslint-disable-line react-hooks/exhaustive-deps

  const confirmed = incident.status === 'Confirmed';
  const body = report?.report_body || {};
  const imageUrl = incident.screenshot_path
    ? (incident.screenshot_path.startsWith('http') ? incident.screenshot_path : `${API_URL}${incident.screenshot_path}`)
    : '';
  const det = ai?.detection || {};
  const loc = ai?.location || {};

  return (
    <div className="fixed inset-0 z-[120] flex justify-end" style={{ background: 'rgba(0,0,0,0.6)' }} onClick={onClose}>
      <div
        className="h-full w-full max-w-3xl border-l flex flex-col"
        style={{ background: 'var(--panel)', borderColor: 'var(--line-2)' }}
        onClick={e => e.stopPropagation()}
        role="dialog"
        aria-label={`Incident ${incident.case_id}`}
      >
        <div className="shrink-0 h-12 flex items-center justify-between gap-3 px-4 border-b" style={{ borderColor: 'var(--line)' }}>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="data text-[11px] font-bold" style={{ color: 'var(--accent)' }}>{incident.case_id}</span>
              <span className="text-[13px] font-bold uppercase tracking-wide" style={{ color: 'var(--text)' }}>{incident.type}</span>
            </div>
          </div>
          <div className="flex items-center gap-3 shrink-0">
            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 border text-[9px] font-bold uppercase tracking-wider"
              style={confirmed ? { color: 'var(--ok)', borderColor: 'var(--ok)' } : { color: 'var(--text-3)', borderColor: 'var(--line-2)' }}>
              {confirmed ? <CheckCircle2 size={10} /> : <ShieldX size={10} />} {incident.status}
            </span>
            <button title="Close" aria-label="Close" onClick={onClose} style={{ color: 'var(--text-3)' }} className="hover:text-[var(--text)]">
              <X size={16} />
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto custom-scrollbar p-4 space-y-3">
          {error && <p className="text-[10px] font-bold uppercase tracking-wide" style={{ color: 'var(--critical)' }}>{error}</p>}

          <div className="grid grid-cols-1 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-3">
            <Section icon={<Clock size={11} />} title="When and where">
              <Row label="Date" value={incident.occurred_date} />
              <Row label="Time" value={incident.occurred_time} />
              <Row label="Severity" value={incident.severity} />
              <Row label="Location" value={incident.location_name} />
              <Row label="Barangay" value={[loc.barangay, loc.city_municipality].filter(Boolean).join(', ')} />
              <Row label="Station" value={loc.station} />
              <Row label="Coordinates" value={incident.lat != null ? `${Number(incident.lat).toFixed(5)}, ${Number(incident.lng).toFixed(5)}` : undefined} />
            </Section>

            <Section icon={<ImageIcon size={11} />} title="Image at detection">
              {imageUrl && !imageBroken ? (
                <img src={imageUrl} alt={`Scene at detection for ${incident.case_id}`} className="w-full max-h-52 object-contain bg-black"
                  onError={() => setImageBroken(true)} />
              ) : (
                <p className="text-[10px] py-6 text-center" style={{ color: 'var(--text-3)' }}>No image on file.</p>
              )}
            </Section>
          </div>

          <Section icon={<Sparkles size={11} />} title="AI detection">
            {loading ? <p className="label py-1">Loading…</p> : !ai ? (
              <p className="text-[10px]" style={{ color: 'var(--text-3)' }}>Not available.</p>
            ) : (
              <>
                <Row label="Source" value={det.source === 'AI_AUTOMATION' ? 'AI surveillance' : det.source === 'HARDWARE_PANIC' ? 'Panic button' : 'Filed manually'} />
                <Row label="Confidence" value={typeof det.confidence === 'number' && det.source === 'AI_AUTOMATION'
                  ? `${Math.round(det.confidence * 100)}% (${det.confidence_band})` : undefined} />
                <Row label="People in view" value={typeof det.people_in_frame === 'number' ? String(det.people_in_frame) : undefined} />
                <Row label="Weapons" value={det.weapons?.length ? det.weapons.map((w: any) => `${w.name} (${Math.round(w.conf * 100)}%)`).join(', ') : undefined} />
                <Row label="Camera" value={loc.camera_name} />
              </>
            )}
          </Section>

          <Section icon={<FileSignature size={11} />} title={report ? (report.report_status === 'confirmed' ? 'Official report' : 'Report draft (not official)') : 'Official report'}>
            {loading ? <p className="label py-1">Loading…</p> : !report ? (
              <p className="text-[10px]" style={{ color: 'var(--text-3)' }}>
                No report has been filed for this incident{incident.narrative ? '. Narrative on the incident:' : '.'}
              </p>
            ) : (
              <p className="text-[10px] mb-1" style={{ color: 'var(--text-3)' }}>
                {report.reported_by_username ? `Filed by ${report.reported_by_username}` : 'Filed'}
                {report.updated_at || report.created_at ? ` · ${serverDateTime(report.updated_at || report.created_at)}` : ''}
              </p>
            )}
            {!loading && !report && incident.narrative && <Row label="Narrative" value={incident.narrative} />}
            {report && REPORT_SECTIONS.map(sec => {
              const rows = sec.fields.filter(([k]) => (body[k] || '').toString().trim());
              if (!rows.length) return null;
              return (
                <div key={sec.title} className="pt-2 mt-1 border-t first:border-t-0" style={{ borderColor: 'var(--line)' }}>
                  <div className="label mb-0.5" style={{ color: 'var(--text-2)' }}>{sec.title}</div>
                  {rows.map(([k, l]) => <Row key={k} label={l} value={body[k]} />)}
                </div>
              );
            })}
          </Section>

          <Section icon={<Film size={11} />} title={`Evidence clips${clips.length ? ` (${clips.length})` : ''}`}>
            {!can('view_records') ? (
              <p className="text-[10px]" style={{ color: 'var(--text-3)' }}>Your account can&apos;t open the video vault.</p>
            ) : clips.length === 0 ? (
              <p className="text-[10px]" style={{ color: 'var(--text-3)' }}>{loading ? 'Loading…' : 'No clips linked to this incident.'}</p>
            ) : (
              <div className="space-y-2">
                <div className="flex flex-wrap gap-1.5">
                  {clips.map(c => (
                    <button key={c.id} onClick={() => setPlaying(playing?.id === c.id ? null : c)}
                      className="flex items-center gap-1.5 px-2 py-1 border text-[10px] font-bold uppercase tracking-wider"
                      style={playing?.id === c.id
                        ? { background: 'var(--accent)', borderColor: 'var(--accent)', color: '#fff' }
                        : { borderColor: 'var(--line-2)', color: 'var(--text-2)' }}>
                      <Camera size={10} /> {c.label || c.filename}
                    </button>
                  ))}
                </div>
                {playing && (
                  <video key={playing.id} controls autoPlay className="w-full max-h-72 bg-black"
                    src={`${API_URL}/static/recordings/${encodeURIComponent(playing.filename)}`} />
                )}
              </div>
            )}
          </Section>

          {incident.location_name && (
            <p className="flex items-center gap-1 text-[10px]" style={{ color: 'var(--text-3)' }}>
              <MapPin size={10} /> Kept permanently in the Incident Log, including after it is removed from the map.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
