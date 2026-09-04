"use client";

import React from 'react';
import { User, Shield, MapPin, Key, ShieldCheck, LogOut } from 'lucide-react';
import { usePermissions } from '../hooks/usePermissions';

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
  };
  onLogout: () => void;
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

  if (!currentUser) {
    return <div className="label p-6">Loading operator record…</div>;
  }

  const activePerms = Object.entries(permissions).filter(([, v]) => v).map(([k]) => k);

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
              {currentUser.username || 'Unknown operator'}
            </div>
            <div className="text-[10px] mt-0.5 truncate" style={{ color: 'var(--text-2)' }}>
              {currentUser.display_title || 'Personnel authentication record'}
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
            <div>
              <span className="label block mb-1.5">Badge status</span>
              <span
                className="inline-flex items-center gap-1 px-1.5 py-0.5 border text-[9px] font-bold uppercase tracking-wider"
                style={{ color: 'var(--ok)', borderColor: 'var(--ok)' }}
              >
                <ShieldCheck size={10} /> Verified
              </span>
            </div>
            <div>
              <span className="label block mb-1.5">Station</span>
              <span className="text-[11px] font-bold uppercase" style={{ color: 'var(--text)' }}>
                {currentUser.assignment || 'UNASSIGNED'}
              </span>
            </div>
            <div>
              <span className="label block mb-1.5">Operator ID</span>
              <span className="data text-[11px]" style={{ color: 'var(--text-2)' }}>
                SEC-{currentUser.id || '0'}026
              </span>
            </div>
          </div>

          {currentUser.is_sub_admin && (
            <div className="border-t p-3" style={{ borderColor: 'var(--line)' }}>
              <span className="label block mb-1.5">Granted permissions</span>
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
        </div>
      </div>
    </div>
  );
}
