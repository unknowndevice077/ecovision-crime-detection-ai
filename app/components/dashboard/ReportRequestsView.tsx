"use client";

import React, { useMemo, useState } from 'react';
import { ClipboardList, Send, ShieldCheck, ShieldX, CheckCircle2, Clock, X } from 'lucide-react';
import { useLiveChannel } from '../../context/WebSocketContext';
import { useRuntimeConfig } from '../../hooks/useRuntimeConfig';

/* Barangay -> police report requests (#7). One component, two faces --
 * which one renders depends on the caller's own role, same convention as
 * DevteamView reading its own auth state instead of taking it as a prop.
 * Barangay side: submit + track outbox. Police side: inbox with Accept /
 * Decline / Fulfill.
 *
 * Deliberately does NOT surface a "you now have access to the archive"
 * message anywhere -- accepting/fulfilling a request never grants
 * view_history (see backend.py's report_requests migration comment for
 * why). The response_note IS the deliverable; there is no follow-on
 * permission to check for. */

type ReportRequest = {
  id: string;
  barangay_id: string;
  station_id: string | null;
  incident_id: string | null;
  description: string;
  requested_by: number;
  status: 'pending' | 'accepted' | 'fulfilled' | 'declined';
  requested_at: string;
  responded_by: number | null;
  responded_at: string | null;
  response_note: string | null;
};

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

export default function ReportRequestsView() {
  const { apiUrl: API_URL } = useRuntimeConfig();
  const role = readRole();
  const isBarangay = role === 'BARANGAY_ADMIN' || role === 'BARANGAY_STAFF';

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

  // ── Barangay: new request form ──────────────────────────────────────────
  const [newDescription, setNewDescription] = useState('');
  const [createBusy, setCreateBusy] = useState(false);

  const submitRequest = async () => {
    const description = newDescription.trim();
    if (!description) return;
    setCreateBusy(true);
    try {
      const res = await fetch(`${API_URL}/api/report_requests`, {
        method: 'POST', headers: authHeaders(),
        body: JSON.stringify({ description }),
      });
      const d = await res.json().catch(() => ({}));
      if (res.ok) { flash('Request sent to your station.'); setNewDescription(''); fetchRequests(); }
      else flash(d.detail || 'Could not send request.');
    } catch {
      flash('Backend connection failure.');
    } finally {
      setCreateBusy(false);
    }
  };

  // ── Police: respond to a request ────────────────────────────────────────
  const [respondingTo, setRespondingTo] = useState<ReportRequest | null>(null);
  const [respondAction, setRespondAction] = useState<'accept' | 'decline' | 'fulfill'>('accept');
  const [respondNote, setRespondNote] = useState('');
  const [respondBusy, setRespondBusy] = useState(false);

  const openRespond = (req: ReportRequest, action: 'accept' | 'decline' | 'fulfill') => {
    setRespondingTo(req);
    setRespondAction(action);
    setRespondNote('');
  };

  const submitResponse = async () => {
    if (!respondingTo) return;
    if (respondAction === 'fulfill' && !respondNote.trim()) return;
    setRespondBusy(true);
    try {
      const res = await fetch(`${API_URL}/api/report_requests/${respondingTo.id}/${respondAction}`, {
        method: 'POST', headers: authHeaders(),
        body: JSON.stringify({ note: respondNote.trim() || null }),
      });
      const d = await res.json().catch(() => ({}));
      if (res.ok) { flash(`Request ${respondAction}d.`); setRespondingTo(null); fetchRequests(); }
      else flash(d.detail || 'Could not respond.');
    } catch {
      flash('Backend connection failure.');
    } finally {
      setRespondBusy(false);
    }
  };

  const sorted = useMemo(
    () => [...requests].sort((a, b) => (a.requested_at < b.requested_at ? 1 : -1)),
    [requests]
  );

  return (
    <div className="border h-full flex flex-col w-full min-h-[420px]" style={{ background: 'var(--panel)', borderColor: 'var(--line)' }}>
      {toast && (
        <div className="shrink-0 text-[10px] tracking-[0.1em] text-[var(--accent)] border-b border-[var(--line)] bg-[var(--accent)]/[0.04] px-4 py-1.5">
          &gt; {toast}
        </div>
      )}

      {isBarangay && (
        <div className="shrink-0 border-b p-4" style={{ borderColor: 'var(--line)' }}>
          <div className="text-[9px] tracking-[0.15em] uppercase mb-2" style={{ color: 'var(--text-2)' }}>
            Request a report from your covering station
          </div>
          <div className="flex items-start gap-2">
            <textarea
              value={newDescription}
              onChange={e => setNewDescription(e.target.value)}
              placeholder="e.g. Requesting the incident report for the disturbance reported near the plaza on the 18th"
              rows={2}
              className="flex-1 bg-[var(--bg)] border border-[var(--line)] focus:border-[var(--accent)]/50 p-2.5 text-[11px] text-[var(--text)] outline-none placeholder:text-[var(--text-3)] transition-colors resize-none"
            />
            <button
              onClick={submitRequest}
              disabled={createBusy || !newDescription.trim()}
              className="flex items-center gap-1.5 px-3 py-2.5 bg-[var(--accent)] text-[#fff] text-[10px] tracking-[0.1em] uppercase disabled:opacity-30 transition-opacity hover:opacity-90 shrink-0"
            >
              <Send size={12} /> Send
            </button>
          </div>
        </div>
      )}

      <div className="flex-1 overflow-y-auto custom-scrollbar">
        {isLoading ? (
          <p className="text-[10px] tracking-[0.15em] uppercase text-[var(--text-3)] text-center py-10">Loading…</p>
        ) : sorted.length === 0 ? (
          <div className="h-48 flex flex-col items-center justify-center gap-2">
            <ClipboardList size={22} style={{ color: 'var(--text-3)' }} />
            <span className="text-[10px] tracking-[0.15em] uppercase" style={{ color: 'var(--text-3)' }}>
              {isBarangay ? 'No requests sent yet' : 'No requests from any barangay yet'}
            </span>
          </div>
        ) : (
          <div className="divide-y" style={{ borderColor: 'var(--line)' }}>
            {sorted.map(req => {
              const st = STATUS_STYLE[req.status];
              return (
                <div key={req.id} className="px-4 py-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      {!isBarangay && (
                        <p className="text-[9px] uppercase tracking-wide mb-0.5" style={{ color: 'var(--text-3)' }}>
                          from {req.barangay_id}
                        </p>
                      )}
                      <p className="text-[11px]" style={{ color: 'var(--text)' }}>{req.description}</p>
                      <p className="text-[9px] mt-1" style={{ color: 'var(--text-3)' }}>
                        {new Date(req.requested_at).toLocaleString()}
                      </p>
                      {req.response_note && (
                        <div className="mt-2 border px-2.5 py-2" style={{ borderColor: 'var(--line-2)', background: 'var(--panel-2)' }}>
                          <p className="text-[8px] uppercase tracking-wide mb-1" style={{ color: 'var(--text-3)' }}>
                            {req.status === 'declined' ? 'Reason' : 'Response'}
                          </p>
                          <p className="text-[10.5px]" style={{ color: 'var(--text)' }}>{req.response_note}</p>
                        </div>
                      )}
                    </div>
                    <div className="flex flex-col items-end gap-2 shrink-0">
                      <span className="flex items-center gap-1.5 text-[9px] tracking-[0.1em] uppercase" style={{ color: st.color }}>
                        {st.icon} {st.label}
                      </span>
                      {!isBarangay && req.status === 'pending' && (
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
                      {!isBarangay && req.status === 'accepted' && (
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

      {/* RESPOND MODAL */}
      {respondingTo && (
        <div className="fixed inset-0 z-[120] flex items-center justify-center p-4" style={{ background: 'rgba(0,0,0,0.72)' }}>
          <div className="border w-full max-w-sm" style={{ background: 'var(--panel)', borderColor: 'var(--line-2)' }}>
            <div className="h-9 flex items-center justify-between px-3 border-b" style={{ borderColor: 'var(--line)' }}>
              <span className="text-[10px] tracking-[0.15em] uppercase" style={{ color: 'var(--text)' }}>
                {respondAction === 'accept' ? 'Accept request' : respondAction === 'decline' ? 'Decline request' : 'Fulfill request'}
              </span>
              <button onClick={() => setRespondingTo(null)} style={{ color: 'var(--text-3)' }} className="hover:text-[var(--text)] transition-colors">
                <X size={15} />
              </button>
            </div>
            <div className="p-4 space-y-3">
              <p className="text-[10.5px] leading-relaxed" style={{ color: 'var(--text-2)' }}>{respondingTo.description}</p>
              <div>
                <label className="text-[8px] tracking-[0.15em] uppercase mb-1 block" style={{ color: 'var(--text-2)' }}>
                  {respondAction === 'fulfill' ? 'The report / information being handed over' : respondAction === 'decline' ? 'Reason (optional)' : 'Note (optional)'}
                </label>
                <textarea
                  value={respondNote}
                  onChange={e => setRespondNote(e.target.value)}
                  rows={4}
                  autoFocus
                  className="w-full bg-[var(--bg)] border border-[var(--line)] focus:border-[var(--accent)]/50 p-2.5 text-[11px] text-[var(--text)] outline-none resize-none transition-colors"
                />
              </div>
              <button
                onClick={submitResponse}
                disabled={respondBusy || (respondAction === 'fulfill' && !respondNote.trim())}
                className="w-full py-2.5 text-[10px] font-bold tracking-[0.15em] uppercase text-white disabled:opacity-50 transition-opacity hover:opacity-90"
                style={{ background: respondAction === 'decline' ? 'var(--critical)' : 'var(--accent)' }}
              >
                {respondBusy ? 'Sending…' : respondAction === 'accept' ? 'Accept' : respondAction === 'decline' ? 'Decline' : 'Mark fulfilled'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
