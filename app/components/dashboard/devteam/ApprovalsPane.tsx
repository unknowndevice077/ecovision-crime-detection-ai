"use client";

import { useMemo, useState } from 'react';
import { AlertTriangle, ClipboardList, FileText, Search, ShieldCheck, ShieldX, UserCheck } from 'lucide-react';
import { FacePhoto, openIdentityFile } from './ManageUsersPane';
import {
  EmptyPane, InfoRow, PaneHeader, PendingLocation, PendingSignup, SectionLabel, ageFrom, roleLabel, roleStyle,
  useAuthedObjectUrl,
} from './shared';

// One row per applicant, whichever queue they came from: a barangay
// applicant hangs off a pending location, a PNP applicant off their own
// pending account.
type Application = {
  key: string;
  kind: 'barangay' | 'pnp';
  userId: number | null;
  locationId?: string;
  username: string;
  role: string;
  fullName: string | null;
  birthdate: string | null;
  homeAddress: string | null;
  contactNumber: string | null;
  position: string | null;
  assignment: string | null;
  place: string;
  placeDetail: string | null;
  isNewPlace: boolean;
  submitted: string;
  verification: string;
  hasDocument: boolean;
  hasFacePhoto: boolean;
};

type Props = {
  apiUrl: string;
  pendingLocations: PendingLocation[];
  pendingSignups: PendingSignup[];
  busyIds: Set<string | number>;
  onDecideLocation: (barangayId: string, decision: 'approve' | 'reject') => void;
  onDecideSignup: (userId: number, decision: 'approve' | 'reject') => void;
  reviewVerification: (userId: number, decision: 'verified' | 'rejected') => void;
  flash: (m: string) => void;
};

export default function ApprovalsPane({ apiUrl, pendingLocations, pendingSignups, busyIds, onDecideLocation, onDecideSignup, reviewVerification, flash }: Props) {
  const [search, setSearch] = useState('');
  const [kind, setKind] = useState<'' | 'barangay' | 'pnp'>('');
  const [selectedKey, setSelectedKey] = useState<string | null>(null);

  const applications = useMemo<Application[]>(() => {
    const brgy: Application[] = pendingLocations.map(l => ({
      key: `loc:${l.id}`, kind: 'barangay', userId: l.requester_id ?? null, locationId: l.id,
      username: l.requester_username || '(no applicant)', role: l.requester_role || 'BARANGAY_ADMIN',
      fullName: l.requester_full_name || null, birthdate: l.requester_birthdate || null,
      homeAddress: l.requester_home_address || null, contactNumber: l.requester_contact_number || null,
      position: l.requester_position || null, assignment: l.requester_assignment,
      place: `Barangay ${l.name}`,
      placeDetail: [l.city_municipality, l.province].filter(Boolean).join(', ') || null,
      isNewPlace: true,
      submitted: l.requester_created_at || l.created_at,
      verification: l.requester_verification_status || 'unverified',
      hasDocument: !!l.requester_has_document, hasFacePhoto: !!l.requester_has_face_photo,
    }));
    const pnp: Application[] = pendingSignups.map(s => ({
      key: `usr:${s.id}`, kind: s.role === 'BARANGAY_ADMIN' ? 'barangay' : 'pnp', userId: s.id,
      username: s.username, role: s.role,
      fullName: s.full_name || null, birthdate: s.birthdate || null,
      homeAddress: s.home_address || null, contactNumber: s.contact_number || null,
      position: s.position || null, assignment: s.assignment,
      place: s.station_name || s.station_id || 'Unknown station', placeDetail: null, isNewPlace: false,
      submitted: s.created_at, verification: s.verification_status || 'unverified',
      hasDocument: s.has_document, hasFacePhoto: !!s.has_face_photo,
    }));
    return [...brgy, ...pnp].sort((a, b) => (b.submitted || '').localeCompare(a.submitted || ''));
  }, [pendingLocations, pendingSignups]);

  const list = useMemo(() => {
    const q = search.trim().toLowerCase();
    return applications
      .filter(a => !kind || a.kind === kind)
      .filter(a => !q || [a.fullName, a.username, a.place, a.position, a.homeAddress].some(v => (v || '').toLowerCase().includes(q)));
  }, [applications, search, kind]);

  const selected = applications.find(a => a.key === selectedKey) || null;

  const decide = (a: Application, decision: 'approve' | 'reject') => {
    if (a.kind === 'barangay' && a.locationId) onDecideLocation(a.locationId, decision);
    else if (a.userId !== null) onDecideSignup(a.userId, decision);
    setSelectedKey(null);
  };

  return (
    <div className="flex-1 min-h-0 grid grid-cols-1 md:grid-cols-2 gap-6 px-7 pb-7 pt-4">
      <div className="min-h-0 flex flex-col border border-[var(--line)]">
        <PaneHeader icon={<ClipboardList size={12} />} title="Applications" right={<span className="text-[9px] text-[var(--text-3)]">{applications.length} pending</span>} />
        <div className="shrink-0 flex items-center gap-2 px-3 py-2 border-b border-[var(--line)]">
          <Search size={12} className="text-[var(--text-2)] shrink-0" />
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="search name, username, place, or position"
            className="bg-transparent text-[11px] text-[var(--text)] outline-none w-full placeholder:text-[var(--text-3)]"
          />
          <select value={kind} onChange={e => setKind(e.target.value as any)} className="bg-[var(--bg)] border border-[var(--line)] text-[10px] text-[var(--text-2)] px-1.5 py-1 outline-none shrink-0">
            <option value="">all</option>
            <option value="barangay">barangay</option>
            <option value="pnp">police</option>
          </select>
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto custom-scrollbar divide-y divide-[var(--panel-2)]">
          {list.length === 0 ? (
            <EmptyPane text={applications.length ? 'No matching applications' : 'No applications waiting on review'} />
          ) : list.map(a => {
            const style = roleStyle(a.role);
            const busy = busyIds.has((a.locationId ?? a.userId)!);
            return (
              <button
                key={a.key}
                onClick={() => setSelectedKey(a.key)}
                className={`w-full flex items-center gap-3 px-3 py-2.5 text-left transition-colors ${busy ? 'opacity-40' : ''} ${a.key === selectedKey ? 'bg-[var(--accent)]/[0.08]' : 'hover:bg-[var(--panel)]'}`}
              >
                <span className={`text-[8px] font-bold px-1.5 py-1 border shrink-0 ${style.border} ${style.text}`}>{style.code}</span>
                <div className="min-w-0 flex-1">
                  <p className="text-[11px] text-[var(--text)] truncate">{a.fullName || a.username}</p>
                  <p className="text-[9px] text-[var(--text-2)] truncate">{a.position || roleLabel(a.role)} · {a.place}</p>
                </div>
                <div className="shrink-0 text-right">
                  <p className="text-[9px] text-[var(--text-3)]">{a.submitted ? new Date(a.submitted).toLocaleDateString() : ''}</p>
                  {(!a.hasDocument || !a.hasFacePhoto) && (
                    <p className="text-[8px] uppercase tracking-wide text-[var(--warn)]">{!a.hasDocument ? 'no ID' : 'no photo'}</p>
                  )}
                </div>
              </button>
            );
          })}
        </div>
      </div>

      <div className="min-h-0 flex flex-col border border-[var(--line)]">
        {selected ? (
          <ApplicationDetail
            key={selected.key} apiUrl={apiUrl} a={selected} flash={flash}
            onDecide={decision => decide(selected, decision)}
            reviewVerification={reviewVerification}
          />
        ) : (
          <>
            <PaneHeader icon={<UserCheck size={12} />} title="Application" />
            <EmptyPane text="Select an application" sub="Their face photo, government ID, residence and claimed position appear here before you decide." />
          </>
        )}
      </div>
    </div>
  );
}

function IdPreview({ apiUrl, userId }: { apiUrl: string; userId: number }) {
  const { src, type, failed } = useAuthedObjectUrl(`${apiUrl}/api/users/${userId}/verification_document`);
  if (failed) return <p className="text-[9px] text-[var(--critical)]">Could not load the ID document.</p>;
  if (!src) return <p className="text-[9px] text-[var(--text-3)]">Loading ID…</p>;
  if (type === 'application/pdf') {
    return (
      <button onClick={() => window.open(src, '_blank')} className="flex items-center gap-1.5 px-3 py-2 border border-[var(--line-2)] text-[9px] uppercase tracking-wide text-[var(--text-2)] hover:text-[var(--accent)]">
        <FileText size={12} /> Open ID (PDF)
      </button>
    );
  }
  return (
    <button onClick={() => window.open(src, '_blank')} title="Open full size" className="block w-full border border-[var(--line)] bg-black">
      <img src={src} alt="Government ID" className="w-full max-h-52 object-contain" />
    </button>
  );
}

function ApplicationDetail({ apiUrl, a, onDecide, reviewVerification, flash }: {
  apiUrl: string; a: Application; onDecide: (d: 'approve' | 'reject') => void;
  reviewVerification: (userId: number, decision: 'verified' | 'rejected') => void; flash: (m: string) => void;
}) {
  const age = ageFrom(a.birthdate);
  const missing = [!a.fullName && 'full name', !a.birthdate && 'birthdate', !a.homeAddress && 'residence', !a.hasDocument && 'government ID', !a.hasFacePhoto && 'face photo'].filter(Boolean) as string[];

  return (
    <>
      <PaneHeader icon={<UserCheck size={12} />} title={a.role === 'BARANGAY_ADMIN' ? 'Barangay admin application' : 'PNP admin application'} />
      <div className="flex-1 min-h-0 overflow-y-auto custom-scrollbar p-5 space-y-5">
        <div className="flex items-start gap-4">
          {a.userId !== null && <FacePhoto apiUrl={apiUrl} userId={a.userId} hasPhoto={a.hasFacePhoto} name={a.fullName || a.username} size={96} />}
          <div className="min-w-0 flex-1 space-y-1">
            <p className="text-[15px] text-[var(--text)]">{a.fullName || <span className="text-[var(--text-3)]">No name given</span>}</p>
            <p className="text-[10px] text-[var(--text-2)]">@{a.username}</p>
            <p className="text-[11px] text-[var(--accent)] pt-1">
              {a.position || roleLabel(a.role)} <span className="text-[var(--text-2)]">of</span> {a.place}
            </p>
            {a.placeDetail && <p className="text-[9px] text-[var(--text-3)]">{a.placeDetail}</p>}
          </div>
        </div>

        {missing.length > 0 && (
          <div className="flex items-start gap-2 px-3 py-2 border border-[var(--warn)]/40 bg-[var(--warn)]/[0.06] text-[9.5px] text-[var(--warn)]">
            <AlertTriangle size={11} className="shrink-0 mt-0.5" />
            <span>Missing from this application: {missing.join(', ')}.</span>
          </div>
        )}

        <div>
          <SectionLabel>Who they are</SectionLabel>
          <div className="grid grid-cols-2 gap-x-4 gap-y-3">
            <InfoRow label="Age" value={age !== null ? `${age} (born ${a.birthdate})` : null} />
            <InfoRow label="Contact number" value={a.contactNumber} />
            <div className="col-span-2"><InfoRow label="Residence" value={a.homeAddress} /></div>
            <InfoRow label="Applying as" value={roleLabel(a.role)} />
            <InfoRow label="Assignment" value={a.assignment} />
            <InfoRow label="Submitted" value={a.submitted ? new Date(a.submitted).toLocaleString() : null} />
            <InfoRow label={a.kind === 'barangay' ? 'Location' : 'Station'} value={a.isNewPlace ? `${a.place} (new — not yet approved)` : a.place} />
          </div>
        </div>

        <div>
          <div className="flex items-center justify-between">
            <SectionLabel>Government ID</SectionLabel>
            <span className="text-[9px] uppercase tracking-wide" style={{ color: a.verification === 'verified' ? 'var(--ok)' : a.verification === 'rejected' ? 'var(--critical)' : 'var(--warn)' }}>
              {a.hasDocument ? a.verification : 'not submitted'}
            </span>
          </div>
          {a.hasDocument && a.userId !== null ? (
            <div className="space-y-2">
              <IdPreview apiUrl={apiUrl} userId={a.userId} />
              <div className="flex gap-2">
                {a.verification !== 'verified' && (
                  <button onClick={() => reviewVerification(a.userId!, 'verified')} className="flex items-center gap-1 px-2.5 py-1.5 border border-[var(--ok)]/40 text-[9px] uppercase tracking-wide text-[var(--ok)] hover:bg-[var(--ok)]/10"><ShieldCheck size={11} /> ID matches</button>
                )}
                {a.verification !== 'rejected' && (
                  <button onClick={() => reviewVerification(a.userId!, 'rejected')} className="flex items-center gap-1 px-2.5 py-1.5 border border-[var(--critical)]/40 text-[9px] uppercase tracking-wide text-[var(--critical)] hover:bg-[var(--critical)]/10"><ShieldX size={11} /> ID doesn&apos;t match</button>
                )}
                {a.hasFacePhoto && (
                  <button onClick={() => openIdentityFile(apiUrl, a.userId!, 'face_photo', flash)} className="px-2.5 py-1.5 border border-[var(--line-2)] text-[9px] uppercase tracking-wide text-[var(--text-2)] hover:text-[var(--accent)]">Face photo full size</button>
                )}
              </div>
            </div>
          ) : (
            <p className="text-[10px] text-[var(--text-3)]">The applicant skipped the ID step.</p>
          )}
        </div>
      </div>

      <div className="shrink-0 border-t border-[var(--line)] p-4 flex gap-2">
        <button onClick={() => onDecide('reject')} className="flex-1 flex items-center justify-center gap-1.5 py-2.5 border border-[var(--critical)]/40 text-[var(--critical)] text-[10px] tracking-[0.15em] uppercase hover:bg-[var(--critical)]/10">
          <ShieldX size={12} /> Reject
        </button>
        <button onClick={() => onDecide('approve')} className="flex-1 flex items-center justify-center gap-1.5 py-2.5 bg-[var(--ok)] text-[#fff] text-[10px] tracking-[0.15em] uppercase hover:opacity-90">
          <ShieldCheck size={12} /> Approve
        </button>
      </div>
    </>
  );
}
