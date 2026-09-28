"use client";

import React, { useEffect, useState } from 'react';
import { Eye, EyeOff } from 'lucide-react';

export const CREATABLE_ROLES = [
  { role: 'PNP_ADMIN', code: 'PD', label: 'PNP Admin', scope: 'station' },
  { role: 'PNP_OFFICER', code: 'PD', label: 'PNP Officer', scope: 'station' },
  { role: 'BARANGAY_ADMIN', code: 'BG', label: 'Barangay Admin', scope: 'barangay' },
  { role: 'BARANGAY_STAFF', code: 'BG', label: 'Barangay Staff', scope: 'barangay' },
];

export const PNP_ROLES = ['PNP_ADMIN', 'PNP_OFFICER'];
export const ADMIN_ROLES = ['PNP_ADMIN', 'BARANGAY_ADMIN'];

// Two operating branches, distinguished the way a dispatch board would:
// a callsign-style two-letter code and a single accent, nothing more.
export const ROLE_STYLES: Record<string, { code: string; text: string; border: string; bg: string; barText: string }> = {
  PNP_ADMIN: { code: 'PD', text: 'text-[var(--accent)]', border: 'border-[var(--accent)]/25', bg: 'bg-[var(--accent)]/[0.07]', barText: 'text-[var(--accent)]' },
  BARANGAY_ADMIN: { code: 'BG', text: 'text-[var(--ok)]', border: 'border-[var(--ok)]/25', bg: 'bg-[var(--ok)]/[0.07]', barText: 'text-[var(--ok)]' },
  PNP_OFFICER: { code: 'PD', text: 'text-[var(--accent)]/70', border: 'border-[var(--accent)]/15', bg: 'bg-[var(--accent)]/[0.04]', barText: 'text-[var(--accent)]/70' },
  BARANGAY_STAFF: { code: 'BG', text: 'text-[var(--ok)]/70', border: 'border-[var(--ok)]/15', bg: 'bg-[var(--ok)]/[0.04]', barText: 'text-[var(--ok)]/70' },
  // DEVTEAM accounts are real rows in data.users but have no "branch".
  DEVTEAM: { code: 'DT', text: 'text-[var(--text)]', border: 'border-[var(--line-2)]', bg: 'bg-[var(--panel-2)]', barText: 'text-[var(--text)]' },
};

export const DEFAULT_ROLE_STYLE = { code: '??', text: 'text-[var(--text-2)]', border: 'border-[var(--line-2)]', bg: 'bg-[var(--panel-2)]', barText: 'text-[var(--text-2)]' };

export const roleStyle = (role: string) => ROLE_STYLES[role] || DEFAULT_ROLE_STYLE;
export const roleLabel = (role: string) => CREATABLE_ROLES.find(r => r.role === role)?.label || role.replace(/_/g, ' ');

export function authHeaders() {
  const token = typeof window !== "undefined" ? localStorage.getItem("ecoToken") : null;
  return { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) };
}

function bearerOnly() {
  const token = typeof window !== "undefined" ? localStorage.getItem("ecoToken") : null;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export type ManagedUser = {
  id: number;
  username: string;
  role: string;
  barangay_id: string;
  station_id: string;
  assignment: string;
  parent_admin_id: number | null;
  permissions: string;
  last_login: string | null;
  custom_permissions: boolean;
  verification_status?: string;
  display_title?: string | null;
  custom_role_id?: string | null;
  created_at?: string | null;
  signup_status?: string | null;
  has_document?: boolean;
  has_face_photo?: boolean;
  camera_scopes?: Record<string, string[]>;
  full_name?: string | null;
  birthdate?: string | null;
  home_address?: string | null;
  contact_number?: string | null;
  position?: string | null;
};

export type PendingLocation = {
  id: string;
  name: string;
  status?: string;
  requester_id?: number | null;
  requester_username: string | null;
  requester_role: string | null;
  requester_assignment: string | null;
  requester_verification_status?: string | null;
  requester_has_document?: boolean;
  requester_has_face_photo?: boolean;
  requester_full_name?: string | null;
  requester_birthdate?: string | null;
  requester_home_address?: string | null;
  requester_contact_number?: string | null;
  requester_position?: string | null;
  requester_created_at?: string | null;
  created_at: string;
  psgc_code?: string | null;
  city_municipality?: string | null;
  province?: string | null;
  region?: string | null;
  captain_name?: string | null;
  hall_address?: string | null;
  contact_number?: string | null;
  description?: string | null;
};

// A self-signup PNP_ADMIN has no location object to hang a pending status
// on the way a barangay applicant does, so its queue is driven by the
// applicant's own account (signup_status) instead.
export type PendingSignup = {
  id: number;
  username: string;
  role: string;
  assignment: string;
  station_id: string | null;
  station_name: string | null;
  created_at: string;
  verification_status: string;
  has_document: boolean;
  has_face_photo?: boolean;
  full_name?: string | null;
  birthdate?: string | null;
  home_address?: string | null;
  contact_number?: string | null;
  position?: string | null;
};

export type Station = {
  id: string; name: string; barangay_ids: string[]; staff_count: number;
  station_type?: string | null; parent_office?: string | null; regional_office?: string | null;
  commander?: string | null; address?: string | null; contact_number?: string | null; description?: string | null;
};

export type CameraRow = { id: string; name: string; url?: string; status?: string; barangay_id: string };

export type CustomRole = {
  id: string; name: string; org_type: string | null; created_at?: string;
  permission_defaults?: { permission_key: string; resource_type: string | null; resource_id: string | null }[];
};

export function ageFrom(birthdate?: string | null): number | null {
  if (!birthdate) return null;
  const born = new Date(`${birthdate}T00:00:00`);
  if (Number.isNaN(born.getTime())) return null;
  const now = new Date();
  let age = now.getFullYear() - born.getFullYear();
  const m = now.getMonth() - born.getMonth();
  if (m < 0 || (m === 0 && now.getDate() < born.getDate())) age--;
  return age;
}

// Cameras an account could possibly reach: its own barangay's, or every
// camera in its station's jurisdiction. The permission tree only offers
// these -- the backend rejects anything outside them anyway.
export function camerasInScope(role: string, barangayId: string | null | undefined, stationId: string | null | undefined,
                               stations: Station[], cameras: CameraRow[]): CameraRow[] {
  if (PNP_ROLES.includes(role)) {
    const st = stations.find(s => s.id === stationId);
    if (!st) return [];
    const covered = new Set(st.barangay_ids);
    return cameras.filter(c => covered.has(c.barangay_id));
  }
  const bid = (barangayId || '').trim().toLowerCase();
  if (!bid) return [];
  return cameras.filter(c => c.barangay_id === bid);
}

// Identity files are behind the Authorization header (never a static
// mount), so an <img src> can't load them directly -- fetch with the token
// and hand the element an object URL instead.
export function useAuthedObjectUrl(url: string | null) {
  const [state, setState] = useState<{ src: string | null; type: string | null; failed: boolean }>({ src: null, type: null, failed: false });
  useEffect(() => {
    if (!url) { setState({ src: null, type: null, failed: false }); return; }
    let objectUrl: string | null = null;
    let cancelled = false;
    setState({ src: null, type: null, failed: false });
    (async () => {
      try {
        const res = await fetch(url, { headers: bearerOnly() });
        if (!res.ok) { if (!cancelled) setState({ src: null, type: null, failed: true }); return; }
        const blob = await res.blob();
        objectUrl = URL.createObjectURL(blob);
        if (!cancelled) setState({ src: objectUrl, type: blob.type, failed: false });
      } catch {
        if (!cancelled) setState({ src: null, type: null, failed: true });
      }
    })();
    return () => { cancelled = true; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [url]);
  return state;
}

export function PaneHeader({ icon, title, right }: { icon?: React.ReactNode; title: string; right?: React.ReactNode }) {
  return (
    <div className="shrink-0 flex items-center gap-2 px-4 py-2.5 border-b border-[var(--line)] bg-[var(--accent)]/[0.03]">
      {icon && <span className="text-[var(--accent)]">{icon}</span>}
      <span className="text-[9px] tracking-[0.2em] uppercase text-[var(--text)]">{title}</span>
      {right && <div className="ml-auto flex items-center gap-2">{right}</div>}
    </div>
  );
}

export function EmptyPane({ text, sub }: { text: string; sub?: string }) {
  return (
    <div className="flex-1 flex flex-col items-center justify-center gap-2 py-16 px-6 text-center">
      <p className="text-[10px] tracking-[0.15em] uppercase text-[var(--text-3)]">{text}</p>
      {sub && <p className="text-[9px] leading-relaxed text-[var(--text-3)] max-w-xs">{sub}</p>}
    </div>
  );
}

export function InfoRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <span className="text-[8px] tracking-[0.15em] uppercase text-[var(--text-3)] block mb-0.5">{label}</span>
      <span className="text-[11px] text-[var(--text)] break-words">{value || <span className="text-[var(--text-3)]">—</span>}</span>
    </div>
  );
}

export const inputClass = "w-full bg-[var(--bg)] border border-[var(--line)] focus:border-[var(--accent)]/50 p-2.5 text-[11px] text-[var(--text)] outline-none placeholder:text-[var(--text-3)] transition-colors disabled:opacity-50";
export const labelClass = "text-[8px] tracking-[0.15em] uppercase text-[var(--text-2)] mb-1 block";

export function FieldInput({ label, value, onChange, type = 'text', placeholder, disabled }: {
  label: string; value: string; onChange: (v: string) => void; type?: string; placeholder?: string; disabled?: boolean;
}) {
  const [show, setShow] = useState(false);
  const isPassword = type === 'password';
  return (
    <div>
      <label className={labelClass}>{label}</label>
      <div className={isPassword ? 'relative' : undefined}>
        <input
          type={isPassword ? (show ? 'text' : 'password') : type}
          value={value}
          disabled={disabled}
          onChange={e => onChange(e.target.value)}
          placeholder={placeholder}
          className={`${inputClass} ${isPassword ? 'pr-8' : ''}`}
        />
        {isPassword && (
          <button
            type="button"
            onClick={() => setShow(s => !s)}
            title={show ? 'Hide password' : 'Show password'}
            tabIndex={-1}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-[var(--text-2)] hover:text-[var(--text)] transition-colors"
          >
            {show ? <EyeOff size={12} /> : <Eye size={12} />}
          </button>
        )}
      </div>
    </div>
  );
}

export function SelectInput({ label, value, onChange, options, placeholder = 'select…', disabled }: {
  label: string; value: string; onChange: (v: string) => void;
  options: (string | { value: string; label: string })[]; placeholder?: string; disabled?: boolean;
}) {
  return (
    <div>
      <label className={labelClass}>{label}</label>
      <select value={value} disabled={disabled} onChange={e => onChange(e.target.value)} className={inputClass}>
        <option value="">{placeholder}</option>
        {options.map(o => typeof o === 'string'
          ? <option key={o} value={o}>{o}</option>
          : <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </div>
  );
}

export function TextAreaInput({ label, value, onChange, placeholder, rows = 3 }: {
  label: string; value: string; onChange: (v: string) => void; placeholder?: string; rows?: number;
}) {
  return (
    <div>
      <label className={labelClass}>{label}</label>
      <textarea value={value} rows={rows} onChange={e => onChange(e.target.value)} placeholder={placeholder}
        className={`${inputClass} resize-y`} />
    </div>
  );
}

export function SectionLabel({ children }: { children: React.ReactNode }) {
  return <div className="text-[8px] tracking-[0.15em] uppercase text-[var(--text-3)] mb-2.5 mt-1">{children}</div>;
}
