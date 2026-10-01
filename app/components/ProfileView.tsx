"use client";

import React, { useEffect, useRef, useState } from 'react';
import { User, Shield, MapPin, Key, ShieldCheck, LogOut, IdCard, Clock, ShieldX, Upload, FileText, X } from 'lucide-react';
import { usePermissions } from '../hooks/usePermissions';
import { useRuntimeConfig } from '../hooks/useRuntimeConfig';

interface ProfileViewProps {
  currentUser: {
    id: string | number;
    username: string;
    role: string;
    barangay_id: string;
    station_id?: string;
    location_name?: string;
    assignment: string;
    display_title?: string;
    is_sub_admin?: boolean;
    verification_status?: 'unverified' | 'pending' | 'verified' | 'rejected';
    full_name?: string | null;
    position?: string | null;
  };
  onLogout: () => void;
}

function authHeaders() {
  const token = typeof window !== "undefined" ? localStorage.getItem("ecoToken") : null;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

const VERIFICATION_STYLE: Record<string, { label: string; color: string; icon: React.ReactNode }> = {
  unverified: { label: 'Not submitted', color: 'var(--text-3)', icon: <IdCard size={10} /> },
  pending: { label: 'Pending review', color: 'var(--warn)', icon: <Clock size={10} /> },
  verified: { label: 'Verified', color: 'var(--ok)', icon: <ShieldCheck size={10} /> },
  rejected: { label: 'Rejected — resubmit', color: 'var(--critical)', icon: <ShieldX size={10} /> },
};

// Mirrors backend.py's _save_verification_document limits, so a wrong file
// is refused here with the reason instead of after the upload.
const ID_TYPES = ['.jpg', '.jpeg', '.png', '.webp', '.pdf'];
const ID_MAX_BYTES = 10 * 1024 * 1024;

const VERIFICATION_HELP: Record<string, string> = {
  unverified: 'Upload a clear photo or scan of a government-issued ID (PhilSys National ID, UMID, driver’s license, passport, PRC or company/agency ID). Your administrator checks it against your account details.',
  pending: 'Your ID is waiting for review. If the copy you sent is blurry or cropped, upload a clearer one -- it replaces the first.',
  rejected: 'Your ID was not accepted. Upload a new, readable copy showing your full name and photo.',
  verified: 'Your ID has been confirmed.',
};

function formatBytes(n: number) {
  return n < 1024 * 1024 ? `${Math.max(1, Math.round(n / 1024))} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`;
}

/* A labelled read-only field -- the profile screen is a credentials record,
   so every value gets the same label-over-value treatment as the incident
   log rather than bespoke card styling per item. */
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="border p-3" style={{ background: 'var(--panel-2)', borderColor: 'var(--line)' }}>
      <span className="label block mb-1.5">{label}</span>
      {children}
    </div>
  );
}

export default function ProfileView({ currentUser, onLogout }: ProfileViewProps) {
  const { permissions } = usePermissions();
  const { apiUrl: API_URL } = useRuntimeConfig();

  // Identity verification (#8, 2026-09-23) -- the authenticated counterpart
  // to the signup page's pre-approval upload: any logged-in account (an
  // admin-created staff/officer, most directly) can attach or replace their
  // own ID here. Local, optimistic-free: just reflects whatever
  // verification_status the next login/refresh brings back, same as every
  // other read-only field on this screen.
  const [idFile, setIdFile] = useState<File | null>(null);
  const [uploadBusy, setUploadBusy] = useState(false);
  const [uploadDone, setUploadDone] = useState(false);
  const [uploadError, setUploadError] = useState('');
  const [idPreview, setIdPreview] = useState('');
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!idFile || !idFile.type.startsWith('image/')) { setIdPreview(''); return; }
    const url = URL.createObjectURL(idFile);
    setIdPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [idFile]);

  const chooseIdFile = (file: File | null) => {
    setUploadError('');
    if (!file) return;
    const ext = file.name.slice(file.name.lastIndexOf('.')).toLowerCase();
    if (!ID_TYPES.includes(ext)) { setUploadError('Use a JPG, PNG, WEBP or PDF file.'); return; }
    if (file.size > ID_MAX_BYTES) { setUploadError(`That file is ${formatBytes(file.size)}; the limit is 10 MB.`); return; }
    setUploadDone(false);
    setIdFile(file);
  };

  const submitIdDocument = async () => {
    if (!idFile) return;
    setUploadBusy(true);
    setUploadError('');
    try {
      const body = new FormData();
      body.append('id_document', idFile);
      const res = await fetch(`${API_URL}/api/users/me/verification`, { method: 'POST', headers: authHeaders(), body });
      const d = await res.json().catch(() => ({}));
      if (res.ok) { setUploadDone(true); setIdFile(null); }
      else setUploadError(d.detail || 'Could not upload.');
    } catch {
      setUploadError('Backend connection failure.');
    } finally {
      setUploadBusy(false);
    }
  };

  if (!currentUser) {
    return <div className="label p-6">Loading operator record…</div>;
  }

  const activePerms = Object.entries(permissions).filter(([, v]) => v).map(([k]) => k);
  const verifStatus = uploadDone ? 'pending' : (currentUser.verification_status || 'unverified');
  const verifStyle = VERIFICATION_STYLE[verifStatus] || VERIFICATION_STYLE.unverified;

  return (
    <div
      className="h-full border flex flex-col overflow-y-auto custom-scrollbar"
      style={{ background: 'var(--panel)', borderColor: 'var(--line)' }}
    >
      {/* Header */}
      <div className="h-9 shrink-0 flex items-center justify-between px-2.5 border-b" style={{ borderColor: 'var(--line)' }}>
        <span className="label" style={{ color: 'var(--text)' }}>Operator Record</span>
        <button
          onClick={onLogout}
          title="Sign out of this terminal"
          className="flex items-center gap-1.5 px-2 py-1 border text-[10px] font-bold uppercase tracking-wider transition-colors hover:bg-[rgba(229,52,47,0.12)]"
          style={{ borderColor: 'var(--critical)', color: 'var(--critical)' }}
        >
          <LogOut size={12} /> Sign out
        </button>
      </div>

      <div className="p-3 space-y-3">
        {/* Identity */}
        <div className="flex items-center gap-3 border p-3" style={{ background: 'var(--panel-2)', borderColor: 'var(--line)' }}>
          <div
            className="w-12 h-12 shrink-0 flex items-center justify-center border"
            style={{ background: 'var(--bg)', borderColor: 'var(--line-2)', color: 'var(--accent)' }}
          >
            <User size={24} />
          </div>
          <div className="min-w-0">
            <div className="text-[15px] font-bold text-[var(--text)] tracking-wide truncate">
              {currentUser.full_name || currentUser.username || 'Unknown operator'}
            </div>
            <div className="text-[10px] mt-0.5 truncate" style={{ color: 'var(--text-2)' }}>
              @{currentUser.username}{(currentUser.position || currentUser.display_title) ? ` · ${currentUser.position || currentUser.display_title}` : ''}
            </div>
          </div>
        </div>

        {/* Clearance + posting */}
        <div className="grid grid-cols-2 gap-3">
          <Field label="Clearance level">
            <div className="flex items-center gap-1.5">
              <Shield size={13} style={{ color: 'var(--accent)' }} />
              <span className="text-[12px] font-bold text-[var(--text)] uppercase tracking-wide">
                {(currentUser.role || 'GUEST').toUpperCase()}
              </span>
            </div>
          </Field>
          <Field label="Assigned area">
            <div className="flex items-center gap-1.5">
              <MapPin size={13} style={{ color: 'var(--text-3)' }} />
              <span className="text-[12px] font-bold uppercase tracking-wide" style={{ color: 'var(--text)' }}>
                {/* BUG FOUND 2026-09-04: only ever read barangay_id, which
                    is always null for every PNP account (they carry
                    station_id instead) -- every single PNP_ADMIN/PNP_OFFICER
                    profile page has always shown "GLOBAL" here, implying
                    system-wide access no PNP account actually has. Same
                    barangay_id-only blind spot as page.tsx's sidebar footer
                    (see its matching 2026-09-04 fix) -- station_id/
                    location_name were simply never considered as the
                    alternative. Real GLOBAL scope (DEVTEAM) has neither id
                    set, so it still correctly falls through to that label. */}
                {(currentUser.location_name || currentUser.station_id || currentUser.barangay_id || 'GLOBAL').toUpperCase()}
              </span>
            </div>
          </Field>
        </div>

        {/* Credentials */}
        <div className="border" style={{ background: 'var(--panel-2)', borderColor: 'var(--line)' }}>
          <div className="h-8 flex items-center gap-1.5 px-3 border-b" style={{ borderColor: 'var(--line)' }}>
            <Key size={11} style={{ color: 'var(--text-3)' }} />
            <span className="label">Credentials</span>
          </div>

          <div className="grid grid-cols-3 gap-3 p-3">
            {/* This row used to show a hardcoded "Verified" badge (next to a
                real "not submitted" verification status below), an invented
                operator ID, and the assignment labelled "Station". */}
            <div>
              <span className="label block mb-1.5">Identity</span>
              <span
                className="inline-flex items-center gap-1 px-1.5 py-0.5 border text-[9px] font-bold uppercase tracking-wider"
                style={{ color: verifStyle.color, borderColor: verifStyle.color }}
              >
                {verifStatus === 'verified' && <ShieldCheck size={10} />} {verifStyle.label}
              </span>
            </div>
            <div>
              <span className="label block mb-1.5">Assignment</span>
              <span className="text-[11px] font-bold uppercase" style={{ color: 'var(--text)' }}>
                {currentUser.assignment || 'None'}
              </span>
            </div>
            <div>
              <span className="label block mb-1.5">Account ID</span>
              <span className="data text-[11px]" style={{ color: 'var(--text-2)' }}>
                #{currentUser.id}
              </span>
            </div>
          </div>

          {/* Shown for everyone now -- it was limited to the old
              "sub-admin" flag, so most accounts never saw what they can do. */}
          {currentUser.role !== 'DEVTEAM' && (
            <div className="border-t p-3" style={{ borderColor: 'var(--line)' }}>
              <span className="label block mb-1.5">What this account can do</span>
              <div className="flex flex-wrap gap-1.5">
                {activePerms.length > 0 ? activePerms.map((p) => (
                  <span
                    key={p}
                    className="px-1.5 py-0.5 border text-[9px] font-bold uppercase tracking-wider"
                    style={{ color: 'var(--text-2)', borderColor: 'var(--line-2)' }}
                  >
                    {p.replace(/_/g, ' ')}
                  </span>
                )) : (
                  <span className="label">None granted</span>
                )}
              </div>
            </div>
          )}

          {/* Identity verification (#8, 2026-09-23) */}
          <div className="border-t p-3" style={{ borderColor: 'var(--line)' }}>
            <div className="flex items-center justify-between mb-2">
              <span className="label">Identity verification</span>
              <span className="inline-flex items-center gap-1 px-1.5 py-0.5 border text-[9px] font-bold uppercase tracking-wider" style={{ color: verifStyle.color, borderColor: verifStyle.color }}>
                {verifStyle.icon} {verifStyle.label}
              </span>
            </div>
            <p className="text-[11px] leading-relaxed mb-2.5" style={{ color: 'var(--text-2)' }}>
              {uploadDone ? 'ID submitted. It is now waiting for your administrator to review it.' : VERIFICATION_HELP[verifStatus] || VERIFICATION_HELP.unverified}
            </p>
            {verifStatus !== 'verified' && (
              <>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept={ID_TYPES.join(',')}
                  className="hidden"
                  aria-label="Choose an ID file"
                  onChange={e => { chooseIdFile(e.target.files?.[0] || null); e.target.value = ''; }}
                />
                {idFile ? (
                  <div className="border p-2.5 space-y-2.5" style={{ background: 'var(--bg)', borderColor: 'var(--line-2)' }}>
                    <div className="flex items-center gap-2.5">
                      {idPreview ? (
                        <img src={idPreview} alt="Selected ID" className="w-16 h-11 object-cover border shrink-0" style={{ borderColor: 'var(--line)' }} />
                      ) : (
                        <div className="w-16 h-11 flex items-center justify-center border shrink-0" style={{ borderColor: 'var(--line)', color: 'var(--text-3)' }}>
                          <FileText size={18} />
                        </div>
                      )}
                      <div className="min-w-0 flex-1">
                        <div className="text-[11px] font-bold truncate" style={{ color: 'var(--text)' }}>{idFile.name}</div>
                        <div className="data text-[10px]" style={{ color: 'var(--text-3)' }}>{formatBytes(idFile.size)}</div>
                      </div>
                      <button
                        onClick={() => setIdFile(null)}
                        disabled={uploadBusy}
                        title="Remove this file"
                        aria-label="Remove this file"
                        className="p-1 hover:text-[var(--text)] shrink-0"
                        style={{ color: 'var(--text-3)' }}
                      >
                        <X size={13} />
                      </button>
                    </div>
                    <div className="flex justify-end gap-2">
                      <button
                        onClick={() => fileInputRef.current?.click()}
                        disabled={uploadBusy}
                        className="px-2.5 py-1.5 border text-[10px] font-bold uppercase tracking-wider hover:border-[var(--text-3)]"
                        style={{ borderColor: 'var(--line-2)', color: 'var(--text-2)' }}
                      >
                        Choose another
                      </button>
                      <button
                        onClick={submitIdDocument}
                        disabled={uploadBusy}
                        className="px-2.5 py-1.5 text-[10px] font-bold uppercase tracking-wider text-white disabled:opacity-40 hover:opacity-90"
                        style={{ background: 'var(--accent)' }}
                      >
                        {uploadBusy ? 'Uploading…' : 'Submit for review'}
                      </button>
                    </div>
                  </div>
                ) : (
                  <button
                    onClick={() => fileInputRef.current?.click()}
                    className="w-full flex flex-col items-center justify-center gap-1.5 py-4 border border-dashed transition-colors hover:border-[var(--accent)]"
                    style={{ background: 'var(--bg)', borderColor: 'var(--line-2)' }}
                  >
                    <span className="flex items-center gap-1.5 px-3 py-1.5 text-[10px] font-bold uppercase tracking-wider text-white" style={{ background: 'var(--accent)' }}>
                      <Upload size={12} /> {verifStatus === 'unverified' && !uploadDone ? 'Upload ID' : 'Upload a new ID'}
                    </span>
                    <span className="label">JPG, PNG, WEBP or PDF · up to 10 MB</span>
                  </button>
                )}
              </>
            )}
            {uploadError && (
              <p className="text-[10px] mt-1.5" style={{ color: 'var(--critical)' }}>{uploadError}</p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
