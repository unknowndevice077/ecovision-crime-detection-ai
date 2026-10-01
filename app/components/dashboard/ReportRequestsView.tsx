"use client";

import React, { useEffect, useMemo, useRef, useState } from 'react';
import { ClipboardList, FilePlus2, ShieldCheck, ShieldX, CheckCircle2, Clock, X, AlertTriangle, Paperclip, Download, FileText, Trash2 } from 'lucide-react';
import { useLiveChannel } from '../../context/WebSocketContext';
import { useRuntimeConfig } from '../../hooks/useRuntimeConfig';
import { usePermissions } from '../../hooks/usePermissions';
import { serverDateTime } from '../../lib/time';

/* Barangay -> police report requests (#7). One component, two faces --
 * which one renders depends on the caller's own role, same convention as
 * DevteamView reading its own auth state instead of taking it as a prop.
 * Barangay side: "Request a report" form + track outbox. Police side: inbox
 * with Accept / Decline / Fulfill.
 *
 * Deliberately does NOT surface a "you now have access to the archive"
 * message anywhere -- accepting/fulfilling a request never grants
 * view_history (see backend.py's report_requests migration comment for
 * why). The response_note IS the deliverable; there is no follow-on
 * permission to check for. */

type RequestDetails = {
  report_type?: string; crime_type?: string; period_from?: string; period_to?: string;
  location?: string; persons_involved?: string; reference?: string;
  purpose?: string; purpose_detail?: string; urgency?: 'routine' | 'urgent'; needed_by?: string;
};

type ReportRequest = {
  id: string;
  barangay_id: string;
  barangay_name?: string;
  station_id: string | null;
  incident_id: string | null;
  description: string;
  details?: RequestDetails;
  requested_by: number;
  requested_by_name?: string | null;
  requested_by_position?: string | null;
  status: 'pending' | 'accepted' | 'fulfilled' | 'declined';
  requested_at: string;
  responded_by: number | null;
  responded_by_name?: string | null;
  responded_at: string | null;
  response_note: string | null;
  shared_report?: { incident_id: string; case_id: string; fields: { key: string; label: string; value: string }[];
                    summary_edited?: boolean; shared_by?: string; shared_at?: string } | null;
  files?: RequestFile[];
};

type RequestFile = { id: string; original_name: string; content_type: string; size_bytes: number; uploaded_at: string };
type ShareableIncident = { id: string; case_id: string; type: string; status: string; occurred_date: string;
                           occurred_time: string; location_name: string; report_status: string | null };

// Ticked by default when police share a report; names, badge numbers and
// the people involved are left for the officer to opt into.
const DEFAULT_SHARED = ['case_id', 'incident_type', 'occurred', 'location', 'nature_of_incident', 'narrative', 'action_taken', 'disposition'];

const fileSize = (n: number) => n < 1024 * 1024 ? `${Math.max(1, Math.round(n / 1024))} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`;

type Option = { value: string; label: string };

function authHeaders() {
  const token = typeof window !== "undefined" ? localStorage.getItem("ecoToken") : null;
  return { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) };
}

function readRole(): string | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = localStorage.getItem("ecoUser");
    return raw ? JSON.parse(raw).role : null;
  } catch {
    return null;
  }
}

const STATUS_STYLE: Record<string, { label: string; color: string; icon: React.ReactNode }> = {
  pending: { label: 'Pending', color: 'var(--warn)', icon: <Clock size={11} /> },
  accepted: { label: 'Accepted — working on it', color: 'var(--accent)', icon: <ShieldCheck size={11} /> },
  fulfilled: { label: 'Fulfilled', color: 'var(--ok)', icon: <CheckCircle2 size={11} /> },
  declined: { label: 'Declined', color: 'var(--critical)', icon: <ShieldX size={11} /> },
};

const MIN_DETAILS = 15;

const EMPTY_FORM = {
  report_type: '', crime_type: 'ANY', period_from: '', period_to: '', location: '',
  persons_involved: '', reference: '', purpose: '', purpose_detail: '',
  urgency: 'routine' as 'routine' | 'urgent', needed_by: '', description: '',
};

const titleCase = (s: string) => s.toLowerCase().replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
const today = () => new Date().toLocaleDateString('en-CA');

function Field({ label, required, hint, children }: { label: string; required?: boolean; hint?: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="label block mb-1">
        {label}{required && <span style={{ color: 'var(--critical)' }}> *</span>}
      </span>
      {children}
      {hint && <span className="block mt-1 text-[10px]" style={{ color: 'var(--text-3)' }}>{hint}</span>}
    </label>
  );
}

const inputClass = "w-full border px-2 py-1.5 text-[12px] outline-none focus:border-[var(--accent)] transition-colors";
const inputStyle = { background: 'var(--bg)', borderColor: 'var(--line)', color: 'var(--text)' };

export default function ReportRequestsView() {
  const { apiUrl: API_URL } = useRuntimeConfig();
  const role = readRole();
  const isBarangay = role === 'BARANGAY_ADMIN' || role === 'BARANGAY_STAFF';
  // Answering hands over crime-history information, so it takes view_history
  // (backend _require_report_sharer).
  const { can } = usePermissions();
  const canRespond = !isBarangay && can('view_history');

  const [requests, setRequests] = useState<ReportRequest[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [toast, setToast] = useState('');
  const flash = (msg: string) => { setToast(msg); setTimeout(() => setToast(''), 3000); };

  const fetchRequests = async () => {
    try {
      const res = await fetch(`${API_URL}/api/report_requests`, { headers: authHeaders() });
      if (res.ok) setRequests(await res.json());
    } catch { /* leave whatever was last shown */ }
    finally { setIsLoading(false); }
  };
  useLiveChannel("report_requests", fetchRequests);

  // Choices come from the server's own validation tables.
  const [reportTypes, setReportTypes] = useState<Option[]>([]);
  const [purposes, setPurposes] = useState<Option[]>([]);
  const [crimeTypes, setCrimeTypes] = useState<string[]>([]);
  useEffect(() => {
    fetch(`${API_URL}/api/report_requests/options`, { headers: authHeaders() })
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (!d) return;
        setReportTypes(d.report_types || []);
        setPurposes(d.purposes || []);
        setCrimeTypes(d.crime_types || []);
      })
      .catch(() => {});
  }, [API_URL]);
  const typeLabel = (v?: string) => reportTypes.find(o => o.value === v)?.label || (v ? titleCase(v) : '');
  const purposeLabel = (v?: string) => purposes.find(o => o.value === v)?.label || (v ? titleCase(v) : '');

  // ── Barangay: request form ──────────────────────────────────────────────
  const [formOpen, setFormOpen] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [formError, setFormError] = useState('');
  const [createBusy, setCreateBusy] = useState(false);
  // Editing clears the last refusal; the footer then shows the live hint again.
  const setField = (k: keyof typeof EMPTY_FORM, v: string) => { setFormError(''); setForm(f => ({ ...f, [k]: v })); };

  const formProblem = (() => {
    if (!form.report_type) return 'Choose the report you need.';
    if (!form.purpose) return 'Say what the report is for.';
    if (form.purpose === 'other' && !form.purpose_detail.trim()) return 'Describe the purpose.';
    if (form.period_from && form.period_to && form.period_from > form.period_to) return 'The period ends before it starts.';
    if (form.urgency === 'urgent' && !form.needed_by) return 'Give the date an urgent request is needed by.';
    if (form.description.trim().length < MIN_DETAILS) return `Describe what you need (at least ${MIN_DETAILS} characters).`;
    return '';
  })();

  const openForm = () => { setForm(EMPTY_FORM); setFormError(''); setFormOpen(true); };

  const submitRequest = async () => {
    if (formProblem) { setFormError(formProblem); return; }
    setCreateBusy(true);
    setFormError('');
    try {
      const { description, ...rest } = form;
      const res = await fetch(`${API_URL}/api/report_requests`, {
        method: 'POST', headers: authHeaders(),
        body: JSON.stringify({
          ...rest,
          crime_type: rest.crime_type === 'ANY' ? 'ANY' : rest.crime_type,
          needed_by: rest.needed_by || null,
          period_from: rest.period_from || null,
          period_to: rest.period_to || null,
          description: description.trim(),
        }),
      });
      const d = await res.json().catch(() => ({}));
      if (res.ok) {
        setFormOpen(false);
        flash(d.station_id ? 'Request sent to your covering station.' : 'Request saved — your barangay has no covering station yet, so no station has received it.');
        fetchRequests();
      } else {
        setFormError(typeof d.detail === 'string' ? d.detail : 'Could not send the request.');
      }
    } catch {
      setFormError('Backend connection failure — the request was not sent.');
    } finally {
      setCreateBusy(false);
    }
  };

  // ── Police: respond to a request ────────────────────────────────────────
  const [respondingTo, setRespondingTo] = useState<ReportRequest | null>(null);
  const [respondAction, setRespondAction] = useState<'accept' | 'decline' | 'fulfill'>('accept');
  const [respondNote, setRespondNote] = useState('');
  const [respondBusy, setRespondBusy] = useState(false);

  const [shareable, setShareable] = useState<ShareableIncident[]>([]);
  const [shareIncident, setShareIncident] = useState('');
  const [preview, setPreview] = useState<{ key: string; label: string; value: string }[]>([]);
  const [previewDraft, setPreviewDraft] = useState(false);
  const [shareKeys, setShareKeys] = useState<string[]>(DEFAULT_SHARED);
  const [summary, setSummary] = useState('');
  const [respondError, setRespondError] = useState('');
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const openRespond = (req: ReportRequest, action: 'accept' | 'decline' | 'fulfill') => {
    setRespondingTo(req);
    setRespondAction(action);
    setRespondNote('');
    setRespondError('');
    setShareIncident('');
    setPreview([]);
    setShareKeys(DEFAULT_SHARED);
    setSummary('');
    if (action === 'fulfill') {
      fetch(`${API_URL}/api/report_requests/${req.id}/shareable`, { headers: authHeaders() })
        .then(r => r.ok ? r.json() : null)
        .then(d => setShareable(d?.incidents || []))
        .catch(() => setShareable([]));
    }
  };

  const pickIncident = async (incidentId: string) => {
    setShareIncident(incidentId);
    setPreview([]);
    setPreviewDraft(false);
    setRespondError('');
    if (!incidentId || !respondingTo) return;
    try {
      const res = await fetch(`${API_URL}/api/report_requests/${respondingTo.id}/share_preview?incident_id=${encodeURIComponent(incidentId)}`, { headers: authHeaders() });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) { setRespondError(d.detail || 'Could not load that report.'); return; }
      setPreviewDraft(d.report_status === 'draft');
      setPreview(d.fields || []);
      setSummary((d.fields || []).find((f: any) => f.key === 'narrative')?.value || '');
    } catch {
      setRespondError('Backend connection failure.');
    }
  };

  // The request as currently stored -- files show up as they're attached.
  const liveRequest = respondingTo ? requests.find(r => r.id === respondingTo.id) || respondingTo : null;

  const uploadFiles = async (list: FileList | null) => {
    if (!list || !respondingTo) return;
    setUploading(true);
    setRespondError('');
    for (const f of Array.from(list)) {
      const body = new FormData();
      body.append('file', f);
      const token = typeof window !== "undefined" ? localStorage.getItem("ecoToken") : null;
      try {
        const res = await fetch(`${API_URL}/api/report_requests/${respondingTo.id}/files`, {
          method: 'POST', headers: token ? { Authorization: `Bearer ${token}` } : {}, body,
        });
        if (!res.ok) {
          const d = await res.json().catch(() => ({}));
          setRespondError(`${f.name}: ${d.detail || 'upload failed'}`);
          break;
        }
      } catch {
        setRespondError('Backend connection failure.');
        break;
      }
    }
    setUploading(false);
    fetchRequests();
  };

  const removeFile = async (file: RequestFile) => {
    if (!respondingTo) return;
    await fetch(`${API_URL}/api/report_requests/${respondingTo.id}/files/${file.id}`, { method: 'DELETE', headers: authHeaders() }).catch(() => {});
    fetchRequests();
  };

  const downloadFile = async (req: ReportRequest, file: RequestFile) => {
    try {
      const res = await fetch(`${API_URL}/api/report_requests/${req.id}/files/${file.id}`, { headers: authHeaders() });
      if (!res.ok) { flash('Could not download that file.'); return; }
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement('a');
      a.href = url;
      a.download = file.original_name;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
    } catch {
      flash('Backend connection failure.');
    }
  };

  const sharing = respondAction === 'fulfill' && !!shareIncident && !previewDraft && preview.length > 0;
  const fulfillReady = respondAction !== 'fulfill'
    || (sharing && shareKeys.some(k => preview.some(f => f.key === k)))
    || !!respondNote.trim() || (liveRequest?.files?.length || 0) > 0;

  const submitResponse = async () => {
    if (!respondingTo || !fulfillReady) return;
    setRespondBusy(true);
    setRespondError('');
    try {
      const payload: Record<string, any> = { note: respondNote.trim() || null };
      if (sharing) {
        payload.incident_id = shareIncident;
        payload.share_fields = shareKeys.filter(k => preview.some(f => f.key === k));
        if (shareKeys.includes('narrative')) payload.summary = summary;
      }
      const res = await fetch(`${API_URL}/api/report_requests/${respondingTo.id}/${respondAction}`, {
        method: 'POST', headers: authHeaders(), body: JSON.stringify(payload),
      });
      const d = await res.json().catch(() => ({}));
      if (res.ok) { flash(`Request ${respondAction === 'fulfill' ? 'fulfilled' : respondAction + 'ed'}.`); setRespondingTo(null); fetchRequests(); }
      else setRespondError(typeof d.detail === 'string' ? d.detail : 'Could not respond.');
    } catch {
      flash('Backend connection failure.');
    } finally {
      setRespondBusy(false);
    }
  };

  const [statusFilter, setStatusFilter] = useState<'all' | ReportRequest['status']>('all');
  const sorted = useMemo(
    () => [...requests]
      .filter(r => statusFilter === 'all' || r.status === statusFilter)
      .sort((a, b) => (a.requested_at < b.requested_at ? 1 : -1)),
    [requests, statusFilter]
  );
  const counts = useMemo(() => requests.reduce((acc, r) => { acc[r.status] = (acc[r.status] || 0) + 1; return acc; }, {} as Record<string, number>), [requests]);

  // The structured fields of one request, as label/value rows.
  const detailRows = (req: ReportRequest): [string, string][] => {
    const d = req.details || {};
    const rows: [string, string][] = [];
    if (d.crime_type) rows.push(['Crime', d.crime_type === 'ANY' ? 'Any / all types' : titleCase(d.crime_type)]);
    if (d.period_from || d.period_to) rows.push(['Period', `${d.period_from || '…'} to ${d.period_to || '…'}`]);
    if (d.location) rows.push(['Location', d.location]);
    if (d.persons_involved) rows.push(['Persons involved', d.persons_involved]);
    if (d.reference) rows.push(['Reference', d.reference]);
    if (d.purpose) rows.push(['Purpose', d.purpose === 'other' && d.purpose_detail ? d.purpose_detail : purposeLabel(d.purpose)]);
    if (d.purpose && d.purpose !== 'other' && d.purpose_detail) rows.push(['Purpose note', d.purpose_detail]);
    if (d.needed_by) rows.push(['Needed by', d.needed_by]);
    return rows;
  };

  return (
    <div className="border h-full flex flex-col w-full min-h-[420px]" style={{ background: 'var(--panel)', borderColor: 'var(--line)' }}>
      {/* Header */}
      <div className="shrink-0 flex items-center justify-between gap-3 px-4 py-2.5 border-b" style={{ borderColor: 'var(--line)' }}>
        <div className="min-w-0">
          <div className="label" style={{ color: 'var(--text)' }}>{isBarangay ? 'Report requests to police' : 'Report requests from barangays'}</div>
          <div className="text-[10px] mt-0.5" style={{ color: 'var(--text-3)' }}>
            {isBarangay
              ? 'Ask your covering police station for a blotter copy, incident report, case status or statistics.'
              : 'Requests routed to your station by the barangays it covers.'}
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <select
            title="Filter by status"
            value={statusFilter}
            onChange={e => setStatusFilter(e.target.value as typeof statusFilter)}
            className="data border px-2 py-1.5 text-[11px] outline-none cursor-pointer"
            style={inputStyle}
          >
            <option value="all">All ({requests.length})</option>
            {(['pending', 'accepted', 'fulfilled', 'declined'] as const).map(s => (
              <option key={s} value={s}>{STATUS_STYLE[s].label.split(' —')[0]} ({counts[s] || 0})</option>
            ))}
          </select>
          {isBarangay && (
            <button
              onClick={openForm}
              className="flex items-center gap-1.5 px-3 py-1.5 text-[10px] font-bold uppercase tracking-wider text-white hover:opacity-90"
              style={{ background: 'var(--accent)' }}
            >
              <FilePlus2 size={13} /> Request a report
            </button>
          )}
        </div>
      </div>

      {toast && (
        <div className="shrink-0 text-[10px] tracking-[0.1em] text-[var(--accent)] border-b border-[var(--line)] bg-[var(--accent)]/[0.04] px-4 py-1.5">
          &gt; {toast}
        </div>
      )}

      <div className="flex-1 overflow-y-auto custom-scrollbar">
        {isLoading ? (
          <p className="text-[10px] tracking-[0.15em] uppercase text-[var(--text-3)] text-center py-10">Loading…</p>
        ) : sorted.length === 0 ? (
          <div className="h-48 flex flex-col items-center justify-center gap-3">
            <ClipboardList size={22} style={{ color: 'var(--text-3)' }} />
            <span className="text-[10px] tracking-[0.15em] uppercase" style={{ color: 'var(--text-3)' }}>
              {statusFilter !== 'all' ? 'Nothing with this status' : isBarangay ? 'No requests sent yet' : 'No requests from any barangay yet'}
            </span>
            {isBarangay && statusFilter === 'all' && (
              <button onClick={openForm} className="flex items-center gap-1.5 px-3 py-1.5 border text-[10px] font-bold uppercase tracking-wider hover:border-[var(--accent)]"
                style={{ borderColor: 'var(--line-2)', color: 'var(--text-2)' }}>
                <FilePlus2 size={12} /> Request a report
              </button>
            )}
          </div>
        ) : (
          <div className="divide-y" style={{ borderColor: 'var(--line)' }}>
            {sorted.map(req => {
              const st = STATUS_STYLE[req.status];
              const d = req.details || {};
              const rows = detailRows(req);
              return (
                <div key={req.id} className="px-4 py-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 flex-wrap mb-1">
                        <span className="text-[12px] font-bold" style={{ color: 'var(--text)' }}>
                          {d.report_type ? typeLabel(d.report_type) : 'Report request'}
                        </span>
                        {d.urgency === 'urgent' && (
                          <span className="inline-flex items-center gap-1 px-1.5 py-0.5 border text-[9px] font-bold uppercase tracking-wider"
                            style={{ color: 'var(--critical)', borderColor: 'var(--critical)' }}>
                            <AlertTriangle size={9} /> Urgent
                          </span>
                        )}
                      </div>
                      <p className="text-[10px] mb-1.5" style={{ color: 'var(--text-3)' }}>
                        {!isBarangay && <>From <b style={{ color: 'var(--text-2)' }}>{req.barangay_name || req.barangay_id}</b> · </>}
                        {req.requested_by_name || 'Unknown'}{req.requested_by_position ? `, ${req.requested_by_position}` : ''} · {serverDateTime(req.requested_at)}
                      </p>
                      <p className="text-[11px] leading-relaxed whitespace-pre-wrap" style={{ color: 'var(--text)' }}>{req.description}</p>
                      {rows.length > 0 && (
                        <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-[10.5px]">
                          {rows.map(([k, v]) => (
                            <React.Fragment key={k}>
                              <dt style={{ color: 'var(--text-3)' }}>{k}</dt>
                              <dd className="min-w-0 break-words" style={{ color: 'var(--text-2)' }}>{v}</dd>
                            </React.Fragment>
                          ))}
                        </dl>
                      )}
                      {req.shared_report && (
                        <div className="mt-2 border" style={{ borderColor: 'var(--ok)', background: 'var(--panel-2)' }}>
                          <div className="px-2.5 py-1.5 border-b flex items-center justify-between gap-2" style={{ borderColor: 'var(--line)' }}>
                            <span className="text-[9px] font-bold uppercase tracking-wider" style={{ color: 'var(--ok)' }}>
                              Police report · {req.shared_report.case_id}
                            </span>
                            {req.shared_report.summary_edited && (
                              <span className="text-[9px]" style={{ color: 'var(--text-3)' }}>summary prepared for the barangay</span>
                            )}
                          </div>
                          <dl className="px-2.5 py-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[10.5px]">
                            {req.shared_report.fields.map(f => (
                              <React.Fragment key={f.key}>
                                <dt style={{ color: 'var(--text-3)' }}>{f.label}</dt>
                                <dd className="min-w-0 whitespace-pre-wrap break-words" style={{ color: 'var(--text)' }}>{f.value}</dd>
                              </React.Fragment>
                            ))}
                          </dl>
                        </div>
                      )}
                      {(req.files?.length || 0) > 0 && (
                        <div className="mt-2 flex flex-wrap gap-1.5">
                          {req.files!.map(f => (
                            <button key={f.id} onClick={() => downloadFile(req, f)} title={`Download ${f.original_name}`}
                              className="flex items-center gap-1.5 px-2 py-1 border text-[10px] hover:border-[var(--accent)]"
                              style={{ borderColor: 'var(--line-2)', color: 'var(--text-2)' }}>
                              <Paperclip size={10} /> <span className="max-w-[220px] truncate">{f.original_name}</span>
                              <span style={{ color: 'var(--text-3)' }}>{fileSize(f.size_bytes)}</span> <Download size={10} />
                            </button>
                          ))}
                        </div>
                      )}
                      {req.response_note && (
                        <div className="mt-2 border px-2.5 py-2" style={{ borderColor: 'var(--line-2)', background: 'var(--panel-2)' }}>
                          <p className="text-[8px] uppercase tracking-wide mb-1" style={{ color: 'var(--text-3)' }}>
                            {req.status === 'declined' ? 'Reason' : 'Response'}
                            {req.responded_by_name ? ` · ${req.responded_by_name}` : ''}
                            {req.responded_at ? ` · ${serverDateTime(req.responded_at)}` : ''}
                          </p>
                          <p className="text-[10.5px] whitespace-pre-wrap" style={{ color: 'var(--text)' }}>{req.response_note}</p>
                        </div>
                      )}
                    </div>
                    <div className="flex flex-col items-end gap-2 shrink-0">
                      <span className="flex items-center gap-1.5 text-[9px] tracking-[0.1em] uppercase" style={{ color: st.color }}>
                        {st.icon} {st.label}
                      </span>
                      {canRespond && req.status === 'pending' && (
                        <div className="flex items-center gap-1">
                          <button
                            onClick={() => openRespond(req, 'decline')}
                            className="p-1.5 border border-transparent hover:border-[var(--critical)]/40 text-[var(--text-2)] hover:text-[var(--critical)] transition-colors"
                            title="Decline"
                          >
                            <ShieldX size={13} />
                          </button>
                          <button
                            onClick={() => openRespond(req, 'accept')}
                            className="p-1.5 border border-transparent hover:border-[var(--ok)]/40 text-[var(--text-2)] hover:text-[var(--ok)] transition-colors"
                            title="Accept"
                          >
                            <ShieldCheck size={13} />
                          </button>
                        </div>
                      )}
                      {canRespond && req.status === 'accepted' && (
                        <button
                          onClick={() => openRespond(req, 'fulfill')}
                          className="px-2.5 py-1.5 text-[9px] tracking-[0.1em] uppercase border border-[var(--ok)]/30 text-[var(--ok)] hover:bg-[var(--ok)]/10 transition-colors"
                        >
                          Fulfill
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* REQUEST FORM (barangay) */}
      {formOpen && (
        <div className="fixed inset-0 z-[120] flex items-center justify-center p-4" style={{ background: 'rgba(0,0,0,0.72)' }}>
          <div className="border w-full max-w-2xl max-h-[92vh] flex flex-col" role="dialog" aria-label="Request a report"
            style={{ background: 'var(--panel)', borderColor: 'var(--line-2)' }}>
            <div className="shrink-0 h-11 flex items-center justify-between px-4 border-b" style={{ borderColor: 'var(--line)' }}>
              <div className="flex items-center gap-2">
                <FilePlus2 size={14} style={{ color: 'var(--accent)' }} />
                <span className="text-[12px] font-bold uppercase tracking-wide" style={{ color: 'var(--text)' }}>Request a report</span>
              </div>
              <button title="Close" aria-label="Close" onClick={() => setFormOpen(false)} style={{ color: 'var(--text-3)' }} className="hover:text-[var(--text)]">
                <X size={16} />
              </button>
            </div>

            <div className="flex-1 overflow-y-auto custom-scrollbar p-4 space-y-4">
              <p className="text-[11px] leading-relaxed" style={{ color: 'var(--text-2)' }}>
                Sent to the police station covering your barangay. Be specific — the station uses these details to find the right records.
              </p>

              <section className="space-y-3">
                <div className="label" style={{ color: 'var(--text)' }}>What you need</div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <Field label="Report" required>
                    <select value={form.report_type} onChange={e => setField('report_type', e.target.value)} className={inputClass} style={inputStyle}>
                      <option value="">Choose…</option>
                      {reportTypes.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                  </Field>
                  <Field label="Crime type">
                    <select value={form.crime_type} onChange={e => setField('crime_type', e.target.value)} className={inputClass} style={inputStyle}>
                      <option value="ANY">Any / all types</option>
                      {crimeTypes.map(c => <option key={c} value={c}>{c === 'HARDWARE_PANIC_INTERRUPT' ? 'Panic button activation' : titleCase(c)}</option>)}
                      <option value="OTHER">Other</option>
                    </select>
                  </Field>
                </div>
              </section>

              <section className="space-y-3">
                <div className="label" style={{ color: 'var(--text)' }}>The incident or period</div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <Field label="From">
                    <input type="date" value={form.period_from} max={today()} onChange={e => setField('period_from', e.target.value)} className={inputClass} style={inputStyle} />
                  </Field>
                  <Field label="To">
                    <input type="date" value={form.period_to} min={form.period_from || undefined} max={today()} onChange={e => setField('period_to', e.target.value)} className={inputClass} style={inputStyle} />
                  </Field>
                  <Field label="Location" hint="Street, purok or landmark">
                    <input type="text" value={form.location} maxLength={200} onChange={e => setField('location', e.target.value)}
                      placeholder="e.g. Purok 3, near the public market" className={inputClass} style={inputStyle} />
                  </Field>
                  <Field label="Blotter / case reference" hint="If you have one">
                    <input type="text" value={form.reference} maxLength={100} onChange={e => setField('reference', e.target.value)}
                      placeholder="e.g. Blotter 2026-0918-014 or CASE-202609-AB12" className={`${inputClass} data`} style={inputStyle} />
                  </Field>
                </div>
                <Field label="Persons involved" hint="Complainant, victim or respondent, if the request is about specific people">
                  <input type="text" value={form.persons_involved} maxLength={300} onChange={e => setField('persons_involved', e.target.value)}
                    placeholder="e.g. Complainant: Maria Santos (resident, Purok 3)" className={inputClass} style={inputStyle} />
                </Field>
              </section>

              <section className="space-y-3">
                <div className="label" style={{ color: 'var(--text)' }}>Why and when</div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <Field label="Purpose" required>
                    <select value={form.purpose} onChange={e => setField('purpose', e.target.value)} className={inputClass} style={inputStyle}>
                      <option value="">Choose…</option>
                      {purposes.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                  </Field>
                  <Field label="Priority">
                    <div className="grid grid-cols-2 border" style={{ borderColor: 'var(--line)' }}>
                      {(['routine', 'urgent'] as const).map(u => (
                        <button key={u} type="button" onClick={() => setField('urgency', u)}
                          className="py-1.5 text-[10px] font-bold uppercase tracking-wider"
                          style={form.urgency === u
                            ? { background: u === 'urgent' ? 'var(--critical)' : 'var(--accent)', color: '#fff' }
                            : { color: 'var(--text-2)' }}>
                          {u}
                        </button>
                      ))}
                    </div>
                  </Field>
                </div>
                {(form.purpose === 'other' || form.purpose === 'legal_or_insurance' || form.purpose === 'resident_request') && (
                  <Field label={form.purpose === 'other' ? 'Purpose' : 'Purpose details'} required={form.purpose === 'other'}>
                    <input type="text" value={form.purpose_detail} maxLength={300} onChange={e => setField('purpose_detail', e.target.value)}
                      placeholder={form.purpose === 'resident_request' ? 'Who is asking and why' : 'What it is needed for'}
                      className={inputClass} style={inputStyle} />
                  </Field>
                )}
                <Field label="Needed by" required={form.urgency === 'urgent'}>
                  <input type="date" value={form.needed_by} min={today()} onChange={e => setField('needed_by', e.target.value)}
                    className={`${inputClass} sm:w-1/2`} style={inputStyle} />
                </Field>
              </section>

              <Field label="Details of the request" required hint={`${form.description.trim().length} characters · at least ${MIN_DETAILS}`}>
                <textarea value={form.description} onChange={e => setField('description', e.target.value)} rows={4} maxLength={2000}
                  placeholder="e.g. Requesting a certified copy of the blotter for the altercation at the plaza on 18 September, for the Lupon hearing on 5 October."
                  className={`${inputClass} resize-none`} style={inputStyle} />
              </Field>
            </div>

            <div className="shrink-0 border-t px-4 py-3 flex items-center justify-between gap-3" style={{ borderColor: 'var(--line)' }}>
              <span className="text-[10px]" style={{ color: formError ? 'var(--critical)' : 'var(--text-3)' }}>
                {formError || 'Fields marked * are required.'}
              </span>
              <div className="flex gap-2 shrink-0">
                <button onClick={() => setFormOpen(false)}
                  className="px-3 py-1.5 border text-[10px] font-bold uppercase tracking-wider hover:border-[var(--text-3)]"
                  style={{ borderColor: 'var(--line-2)', color: 'var(--text-2)' }}>
                  Cancel
                </button>
                <button onClick={submitRequest} disabled={createBusy}
                  className="px-3 py-1.5 text-[10px] font-bold uppercase tracking-wider text-white hover:opacity-90 disabled:opacity-50"
                  style={{ background: 'var(--accent)' }}>
                  {createBusy ? 'Sending…' : 'Send request'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* RESPOND MODAL (police) */}
      {respondingTo && liveRequest && (
        <div className="fixed inset-0 z-[120] flex items-center justify-center p-4" style={{ background: 'rgba(0,0,0,0.72)' }}>
          <div className={`border w-full ${respondAction === 'fulfill' ? 'max-w-2xl' : 'max-w-md'} max-h-[92vh] flex flex-col`}
            style={{ background: 'var(--panel)', borderColor: 'var(--line-2)' }} role="dialog" aria-label="Respond to request">
            <div className="shrink-0 h-10 flex items-center justify-between px-4 border-b" style={{ borderColor: 'var(--line)' }}>
              <span className="text-[11px] font-bold tracking-wide uppercase" style={{ color: 'var(--text)' }}>
                {respondAction === 'accept' ? 'Accept request' : respondAction === 'decline' ? 'Decline request' : 'Hand over the report'}
              </span>
              <button title="Close" aria-label="Close" onClick={() => setRespondingTo(null)} style={{ color: 'var(--text-3)' }} className="hover:text-[var(--text)] transition-colors">
                <X size={15} />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto custom-scrollbar p-4 space-y-4">
              <div className="border p-2.5" style={{ borderColor: 'var(--line)', background: 'var(--panel-2)' }}>
                <p className="text-[11px] font-bold" style={{ color: 'var(--text)' }}>
                  {liveRequest.details?.report_type ? typeLabel(liveRequest.details.report_type) : 'Report request'}
                  <span className="font-normal" style={{ color: 'var(--text-3)' }}> · {liveRequest.barangay_name || liveRequest.barangay_id}</span>
                </p>
                <p className="text-[10.5px] leading-relaxed mt-1 whitespace-pre-wrap" style={{ color: 'var(--text-2)' }}>{liveRequest.description}</p>
                {detailRows(liveRequest).length > 0 && (
                  <p className="text-[10px] mt-1" style={{ color: 'var(--text-3)' }}>
                    {detailRows(liveRequest).map(([k, v]) => `${k}: ${v}`).join(' · ')}
                  </p>
                )}
              </div>

              {respondAction === 'fulfill' && (
                <>
                  <section className="space-y-2">
                    <div className="label" style={{ color: 'var(--text)' }}>1 · Share a filed report</div>
                    <select value={shareIncident} onChange={e => pickIncident(e.target.value)} className={inputClass} style={inputStyle}>
                      <option value="">{shareable.length ? 'Choose the incident…' : 'No incidents on record for this barangay'}</option>
                      {shareable.map(i => (
                        <option key={i.id} value={i.id} disabled={i.report_status === 'draft'}>
                          {i.case_id} · {titleCase(i.type)} · {i.occurred_date}
                          {i.report_status === 'confirmed' ? ' · report filed' : i.report_status === 'draft' ? ' · report still a draft' : ' · no report (incident details only)'}
                        </option>
                      ))}
                    </select>
                    {previewDraft && (
                      <p className="text-[10px]" style={{ color: 'var(--warn)' }}>That report is still a draft. Confirm it from the Incident Map before sharing it.</p>
                    )}
                    {preview.length > 0 && (
                      <div className="border" style={{ borderColor: 'var(--line)' }}>
                        <div className="px-2.5 py-1.5 border-b flex items-center justify-between" style={{ borderColor: 'var(--line)' }}>
                          <span className="text-[10px]" style={{ color: 'var(--text-2)' }}>Tick what the barangay receives</span>
                          <span className="flex gap-3">
                            <button type="button" onClick={() => setShareKeys(preview.map(f => f.key))} className="text-[9px] uppercase tracking-wider" style={{ color: 'var(--accent)' }}>All</button>
                            <button type="button" onClick={() => setShareKeys(DEFAULT_SHARED)} className="text-[9px] uppercase tracking-wider" style={{ color: 'var(--text-2)' }}>Default</button>
                            <button type="button" onClick={() => setShareKeys([])} className="text-[9px] uppercase tracking-wider" style={{ color: 'var(--text-2)' }}>None</button>
                          </span>
                        </div>
                        <div className="divide-y" style={{ borderColor: 'var(--line)' }}>
                          {preview.map(f => {
                            const on = shareKeys.includes(f.key);
                            return (
                              <div key={f.key} className="px-2.5 py-1.5">
                                <label className="flex items-start gap-2 cursor-pointer">
                                  <input type="checkbox" checked={on} className="mt-0.5 w-3.5 h-3.5 shrink-0" style={{ accentColor: 'var(--accent)' }}
                                    onChange={e => setShareKeys(k => e.target.checked ? [...k, f.key] : k.filter(x => x !== f.key))} />
                                  <span className="min-w-0 flex-1">
                                    <span className="text-[10px] font-bold uppercase tracking-wide" style={{ color: on ? 'var(--text)' : 'var(--text-3)' }}>{f.label}</span>
                                    {f.key !== 'narrative' && (
                                      <span className="block text-[10.5px] whitespace-pre-wrap break-words" style={{ color: on ? 'var(--text-2)' : 'var(--text-3)' }}>{f.value}</span>
                                    )}
                                  </span>
                                </label>
                                {f.key === 'narrative' && on && (
                                  <div className="mt-1.5 pl-5">
                                    <textarea value={summary} onChange={e => setSummary(e.target.value)} rows={5} maxLength={4000}
                                      className={`${inputClass} resize-y`} style={inputStyle} />
                                    <span className="block text-[9.5px] mt-0.5" style={{ color: 'var(--text-3)' }}>
                                      Edit freely -- this wording is what the barangay sees. The filed report itself is not changed.
                                    </span>
                                  </div>
                                )}
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    )}
                  </section>

                  <section className="space-y-2">
                    <div className="label" style={{ color: 'var(--text)' }}>2 · Attach files <span className="font-normal normal-case" style={{ color: 'var(--text-3)' }}>(blotter scan, certification -- optional)</span></div>
                    <input ref={fileRef} type="file" multiple className="hidden" accept=".pdf,.docx,.jpg,.jpeg,.png,.webp"
                      onChange={e => { uploadFiles(e.target.files); e.target.value = ''; }} />
                    {(liveRequest.files?.length || 0) > 0 && (
                      <div className="space-y-1">
                        {liveRequest.files!.map(f => (
                          <div key={f.id} className="flex items-center gap-2 px-2 py-1.5 border" style={{ borderColor: 'var(--line-2)' }}>
                            <FileText size={12} style={{ color: 'var(--text-3)' }} />
                            <span className="text-[10.5px] truncate flex-1" style={{ color: 'var(--text)' }}>{f.original_name}</span>
                            <span className="text-[10px]" style={{ color: 'var(--text-3)' }}>{fileSize(f.size_bytes)}</span>
                            <button onClick={() => removeFile(f)} title="Remove this file" aria-label={`Remove ${f.original_name}`}
                              className="hover:text-[var(--critical)]" style={{ color: 'var(--text-3)' }}>
                              <Trash2 size={11} />
                            </button>
                          </div>
                        ))}
                      </div>
                    )}
                    <button type="button" onClick={() => fileRef.current?.click()} disabled={uploading}
                      className="flex items-center gap-1.5 px-3 py-1.5 border text-[10px] font-bold uppercase tracking-wider hover:border-[var(--accent)] disabled:opacity-50"
                      style={{ borderColor: 'var(--line-2)', color: 'var(--text-2)' }}>
                      <Paperclip size={11} /> {uploading ? 'Uploading…' : 'Attach files'}
                    </button>
                    <span className="block text-[9.5px]" style={{ color: 'var(--text-3)' }}>PDF, Word or image · up to 15 MB each · 10 files</span>
                  </section>
                </>
              )}

              <section className="space-y-1">
                <label className="label block" style={{ color: respondAction === 'fulfill' ? 'var(--text)' : undefined }}>
                  {respondAction === 'fulfill' ? '3 · Message to the barangay (optional)' : respondAction === 'decline' ? 'Reason (optional)' : 'Note (optional)'}
                </label>
                <textarea
                  value={respondNote}
                  onChange={e => setRespondNote(e.target.value)}
                  rows={3}
                  autoFocus={respondAction !== 'fulfill'}
                  className={`${inputClass} resize-none`} style={inputStyle}
                />
              </section>
            </div>
            <div className="shrink-0 border-t px-4 py-3 flex items-center justify-between gap-3" style={{ borderColor: 'var(--line)' }}>
              <span className="text-[10px]" style={{ color: respondError ? 'var(--critical)' : 'var(--text-3)' }}>
                {respondError || (respondAction === 'fulfill' && !fulfillReady ? 'Share a report, attach a file or write the information.' : '')}
              </span>
              <button
                onClick={submitResponse}
                disabled={respondBusy || uploading || !fulfillReady}
                className="px-4 py-2 text-[10px] font-bold tracking-[0.15em] uppercase text-white disabled:opacity-50 transition-opacity hover:opacity-90 shrink-0"
                style={{ background: respondAction === 'decline' ? 'var(--critical)' : 'var(--accent)' }}
              >
                {respondBusy ? 'Sending…' : respondAction === 'accept' ? 'Accept' : respondAction === 'decline' ? 'Decline' : 'Send to barangay'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
