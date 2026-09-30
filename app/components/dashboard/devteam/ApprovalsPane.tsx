"use client";

import { useMemo, useState } from 'react';
import { AlertTriangle, Archive, ClipboardList, FileText, Lock, RotateCcw, Search, ShieldCheck, ShieldX, UserCheck } from 'lucide-react';
import { FacePhoto, openIdentityFile } from './ManageUsersPane';
import {
  EmptyPane, FieldInput, InfoRow, PaneHeader, PendingLocation, PendingSignup, SectionLabel, SelectInput, Station,
  TextAreaInput, ageFrom, roleLabel, roleStyle, useAuthedObjectUrl,
} from './shared';

// Mirror backend.py's MIN_DECISION_REASON and MIN_REGISTRATION_REASON.
const MIN_REJECT_REASON = 10;
const MIN_REOPEN_REASON = 20;

export type ApplicationTarget = { kind: 'location'; id: string } | { kind: 'signup'; id: number };
export type ApplicationAction = 'approve' | 'reject' | 'reopen';

// One row per applicant, whichever queue they came from: a barangay
// applicant hangs off a pending (or rejected) barangay, a PNP applicant off
// their own account.
type Application = {
  key: string;
  target: ApplicationTarget;
  kind: 'barangay' | 'pnp';
  status: 'pending' | 'rejected';
  userId: number | null;
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
  applicantGone: boolean;
  decidedBy: string | null;
  decidedAt: string | null;
  decisionReason: string | null;
};

type Props = {
  apiUrl: string;
  pendingLocations: PendingLocation[];
  pendingSignups: PendingSignup[];
  rejectedLocations: PendingLocation[];
  rejectedSignups: PendingSignup[];
  stations: Station[];
  busyIds: Set<string | number>;
  onDecide: (target: ApplicationTarget, action: ApplicationAction, body: Record<string, unknown>) => Promise<string | null>;
  reviewVerification: (userId: number, decision: 'verified' | 'rejected') => void;
  flash: (m: string) => void;
};

function fromLocation(l: PendingLocation, status: 'pending' | 'rejected'): Application {
  return {
    key: `loc:${l.id}`, target: { kind: 'location', id: l.id }, kind: 'barangay', status,
    userId: l.requester_id ?? null,
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
    applicantGone: !l.requester_id || !!l.requester_deleted,
    decidedBy: l.decided_by_username || null, decidedAt: l.decided_at || null, decisionReason: l.decision_reason || null,
  };
}

function fromSignup(s: PendingSignup, status: 'pending' | 'rejected'): Application {
  return {
    key: `usr:${s.id}`, target: { kind: 'signup', id: s.id }, kind: s.role === 'BARANGAY_ADMIN' ? 'barangay' : 'pnp', status,
    userId: s.id, username: s.username, role: s.role,
    fullName: s.full_name || null, birthdate: s.birthdate || null,
    homeAddress: s.home_address || null, contactNumber: s.contact_number || null,
    position: s.position || null, assignment: s.assignment,
    place: s.station_name || s.station_id || 'Unknown station', placeDetail: null, isNewPlace: false,
    submitted: s.created_at, verification: s.verification_status || 'unverified',
    hasDocument: s.has_document, hasFacePhoto: !!s.has_face_photo, applicantGone: false,
    decidedBy: s.decided_by_username || null, decidedAt: s.decided_at || null, decisionReason: s.decision_reason || null,
  };
}

const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : null);

export default function ApprovalsPane({
  apiUrl, pendingLocations, pendingSignups, rejectedLocations, rejectedSignups, stations, busyIds, onDecide,
  reviewVerification, flash,
}: Props) {
  const [view, setView] = useState<'pending' | 'rejected'>('pending');
  const [search, setSearch] = useState('');
  const [kind, setKind] = useState<'' | 'barangay' | 'pnp'>('');
  const [selectedKey, setSelectedKey] = useState<string | null>(null);

  const pending = useMemo<Application[]>(() => [
    ...pendingLocations.map(l => fromLocation(l, 'pending')),
    ...pendingSignups.map(s => fromSignup(s, 'pending')),
  ].sort((a, b) => (b.submitted || '').localeCompare(a.submitted || '')), [pendingLocations, pendingSignups]);

  const rejected = useMemo<Application[]>(() => [
    ...rejectedLocations.map(l => fromLocation(l, 'rejected')),
    ...rejectedSignups.map(s => fromSignup(s, 'rejected')),
  ].sort((a, b) => (b.decidedAt || '').localeCompare(a.decidedAt || '')), [rejectedLocations, rejectedSignups]);

  const applications = view === 'pending' ? pending : rejected;

  const list = useMemo(() => {
    const q = search.trim().toLowerCase();
    return applications
      .filter(a => !kind || a.kind === kind)
      .filter(a => !q || [a.fullName, a.username, a.place, a.position, a.homeAddress, a.decisionReason]
        .some(v => (v || '').toLowerCase().includes(q)));
  }, [applications, search, kind]);

  const selected = applications.find(a => a.key === selectedKey) || null;

  const switchView = (v: 'pending' | 'rejected') => { setView(v); setSelectedKey(null); };

  return (
    <div className="flex-1 min-h-0 grid grid-cols-1 md:grid-cols-2 gap-6 px-7 pb-7 pt-4">
      <div className="min-h-0 flex flex-col border border-[var(--line)]">
        <PaneHeader icon={<ClipboardList size={12} />} title="Applications" />
        <div className="shrink-0 grid grid-cols-2 border-b border-[var(--line)]">
          {(['pending', 'rejected'] as const).map(v => (
            <button
              key={v}
              onClick={() => switchView(v)}
              className={`py-2 text-[9px] tracking-[0.15em] uppercase border-b-2 transition-colors ${view === v
                ? (v === 'pending' ? 'border-[var(--accent)] text-[var(--text)]' : 'border-[var(--critical)] text-[var(--text)]')
                : 'border-transparent text-[var(--text-3)] hover:text-[var(--text-2)]'}`}
            >
              {v} <span className="text-[var(--text-3)]">({v === 'pending' ? pending.length : rejected.length})</span>
            </button>
          ))}
        </div>
        <div className="shrink-0 flex items-center gap-2 px-3 py-2 border-b border-[var(--line)]">
          <Search size={12} className="text-[var(--text-2)] shrink-0" />
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder={view === 'pending' ? 'search name, username, place, or position' : 'search name, place, or rejection reason'}
            className="bg-transparent text-[11px] text-[var(--text)] outline-none w-full placeholder:text-[var(--text-3)]"
          />
          <select value={kind} onChange={e => setKind(e.target.value as '' | 'barangay' | 'pnp')} className="bg-[var(--bg)] border border-[var(--line)] text-[10px] text-[var(--text-2)] px-1.5 py-1 outline-none shrink-0">
            <option value="">all</option>
            <option value="barangay">barangay</option>
            <option value="pnp">police</option>
          </select>
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto custom-scrollbar divide-y divide-[var(--panel-2)]">
          {list.length === 0 ? (
            <EmptyPane
              text={applications.length ? 'No matching applications' : view === 'pending' ? 'No applications waiting on review' : 'No rejected applications'}
              sub={view === 'rejected' && !applications.length ? 'Rejected applications are kept here as a record, and can be reopened for another review.' : undefined}
            />
          ) : list.map(a => {
            const style = roleStyle(a.role);
            const busy = busyIds.has(a.target.id);
            return (
              <button
                key={a.key}
                onClick={() => setSelectedKey(a.key)}
                className={`w-full flex items-center gap-3 px-3 py-2.5 text-left transition-colors ${busy ? 'opacity-40' : ''} ${a.key === selectedKey ? 'bg-[var(--accent)]/[0.08]' : 'hover:bg-[var(--panel)]'}`}
              >
                <span className={`text-[8px] font-bold px-1.5 py-1 border shrink-0 ${style.border} ${style.text}`}>{style.code}</span>
                <div className="min-w-0 flex-1">
                  <p className="text-[11px] text-[var(--text)] truncate">{a.fullName || a.username}</p>
                  <p className="text-[9px] text-[var(--text-2)] truncate">
                    {view === 'rejected' && a.decisionReason ? a.decisionReason : `${a.position || roleLabel(a.role)} · ${a.place}`}
                  </p>
                </div>
                <div className="shrink-0 text-right">
                  {view === 'pending' ? (
                    <>
                      <p className="text-[9px] text-[var(--text-3)]">{a.submitted ? new Date(a.submitted).toLocaleDateString() : ''}</p>
                      {(!a.hasDocument || !a.hasFacePhoto) && (
                        <p className="text-[8px] uppercase tracking-wide text-[var(--warn)]">{!a.hasDocument ? 'no ID' : 'no photo'}</p>
                      )}
                    </>
                  ) : (
                    <>
                      <p className="text-[8px] uppercase tracking-wide text-[var(--critical)]">rejected</p>
                      <p className="text-[9px] text-[var(--text-3)]">{a.decidedAt ? new Date(a.decidedAt).toLocaleDateString() : ''}</p>
                    </>
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
            key={selected.key} apiUrl={apiUrl} a={selected} stations={stations} flash={flash}
            busy={busyIds.has(selected.target.id)}
            onDecide={async (action, body) => {
              const err = await onDecide(selected.target, action, body);
              if (!err) setSelectedKey(null);
              return err;
            }}
            reviewVerification={reviewVerification}
          />
        ) : (
          <>
            <PaneHeader icon={<UserCheck size={12} />} title="Application" />
            <EmptyPane
              text="Select an application"
              sub={view === 'pending'
                ? 'Their face photo, government ID, residence and claimed position appear here before you decide.'
                : 'See who rejected it, when and why, and reopen it if it deserves another review.'}
            />
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

type Confirming = null | 'approve' | 'reject' | 'reopen';

function ApplicationDetail({ apiUrl, a, stations, busy, onDecide, reviewVerification, flash }: {
  apiUrl: string; a: Application; stations: Station[]; busy: boolean;
  onDecide: (action: ApplicationAction, body: Record<string, unknown>) => Promise<string | null>;
  reviewVerification: (userId: number, decision: 'verified' | 'rejected') => void; flash: (m: string) => void;
}) {
  const [confirming, setConfirming] = useState<Confirming>(null);
  const [reason, setReason] = useState('');
  const [password, setPassword] = useState('');
  const [stationId, setStationId] = useState('');
  const [error, setError] = useState('');

  const age = ageFrom(a.birthdate);
  const missing = [!a.fullName && 'full name', !a.birthdate && 'birthdate', !a.homeAddress && 'residence', !a.hasDocument && 'government ID', !a.hasFacePhoto && 'face photo'].filter(Boolean) as string[];
  const rejected = a.status === 'rejected';
  const title = a.role === 'BARANGAY_ADMIN' ? 'Barangay admin application' : 'PNP admin application';

  const open = (c: Confirming) => { setConfirming(c); setReason(''); setPassword(''); setError(''); };

  const submit = async () => {
    setError('');
    const r = reason.trim();
    let body: Record<string, unknown> = {};
    if (confirming === 'reject') {
      if (r.length < MIN_REJECT_REASON) return setError(`Give a reason (at least ${MIN_REJECT_REASON} characters).`);
      body = { reason: r };
    } else if (confirming === 'approve') {
      body = { reason: r || null, ...(stationId ? { station_id: stationId } : {}) };
    } else if (confirming === 'reopen') {
      if (r.length < MIN_REOPEN_REASON) return setError(`Give a reason (at least ${MIN_REOPEN_REASON} characters).`);
      if (!password) return setError('Enter your DevTeam password.');
      body = { reason: r, confirm_password: password };
    }
    const err = await onDecide(confirming as ApplicationAction, body);
    if (err) setError(err);
  };

  return (
    <>
      <PaneHeader
        icon={rejected ? <Archive size={12} /> : <UserCheck size={12} />}
        title={rejected ? `${title} — rejected` : title}
      />
      <div className="flex-1 min-h-0 overflow-y-auto custom-scrollbar p-5 space-y-5">
        {rejected && (
          <div className="border border-[var(--critical)]/40 bg-[var(--critical)]/[0.05] px-4 py-3 space-y-1.5">
            <div className="flex items-center gap-1.5 text-[8px] tracking-[0.15em] uppercase text-[var(--critical)]">
              <ShieldX size={11} /> Rejected{a.decidedBy && <> by <span className="text-[var(--text)]">{a.decidedBy}</span></>}
              {a.decidedAt && <span className="text-[var(--text-3)] normal-case tracking-normal"> · {when(a.decidedAt)}</span>}
            </div>
            <p className="text-[11px] text-[var(--text)]">{a.decisionReason || <span className="text-[var(--text-3)]">No reason was recorded (decided before reasons were required).</span>}</p>
            {a.kind === 'barangay' && a.isNewPlace && (
              <p className="text-[9px] text-[var(--text-3)]">While rejected, this barangay can&apos;t be in any station&apos;s jurisdiction, get accounts, or be applied for again.</p>
            )}
          </div>
        )}

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

        {a.applicantGone && (
          <div className="flex items-start gap-2 px-3 py-2 border border-[var(--line-2)] text-[9.5px] text-[var(--text-2)]">
            <AlertTriangle size={11} className="shrink-0 mt-0.5" />
            <span>The applicant&apos;s account no longer exists. Deciding this only decides the barangay itself.</span>
          </div>
        )}

        {!rejected && missing.length > 0 && (
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
            <InfoRow label="Submitted" value={when(a.submitted)} />
            <InfoRow label={a.kind === 'barangay' ? 'Location' : 'Station'} value={a.isNewPlace ? `${a.place} (${rejected ? 'rejected' : 'new — not yet approved'})` : a.place} />
          </div>
        </div>

        <div>
          <div className="flex items-center justify-between">
            <SectionLabel>Government ID</SectionLabel>
            <span className="text-[9px] uppercase tracking-wide" style={{ color: a.verification === 'verified' ? 'var(--ok)' : a.verification === 'rejected' ? 'var(--critical)' : 'var(--warn)' }}>
              {a.hasDocument ? a.verification : 'not submitted'}
            </span>
          </div>
          {a.hasDocument && a.userId !== null && !a.applicantGone ? (
            <div className="space-y-2">
              <IdPreview apiUrl={apiUrl} userId={a.userId} />
              <div className="flex gap-2">
                {!rejected && a.verification !== 'verified' && (
                  <button onClick={() => reviewVerification(a.userId!, 'verified')} className="flex items-center gap-1 px-2.5 py-1.5 border border-[var(--ok)]/40 text-[9px] uppercase tracking-wide text-[var(--ok)] hover:bg-[var(--ok)]/10"><ShieldCheck size={11} /> ID matches</button>
                )}
                {!rejected && a.verification !== 'rejected' && (
                  <button onClick={() => reviewVerification(a.userId!, 'rejected')} className="flex items-center gap-1 px-2.5 py-1.5 border border-[var(--critical)]/40 text-[9px] uppercase tracking-wide text-[var(--critical)] hover:bg-[var(--critical)]/10"><ShieldX size={11} /> ID doesn&apos;t match</button>
                )}
                {a.hasFacePhoto && (
                  <button onClick={() => openIdentityFile(apiUrl, a.userId!, 'face_photo', flash)} className="px-2.5 py-1.5 border border-[var(--line-2)] text-[9px] uppercase tracking-wide text-[var(--text-2)] hover:text-[var(--accent)]">Face photo full size</button>
                )}
              </div>
            </div>
          ) : (
            <p className="text-[10px] text-[var(--text-3)]">{a.applicantGone ? 'No applicant on file.' : 'The applicant skipped the ID step.'}</p>
          )}
        </div>
      </div>

      <div className="shrink-0 border-t border-[var(--line)] p-4 space-y-3">
        {confirming === null ? (
          rejected ? (
            <button onClick={() => open('reopen')} className="w-full flex items-center justify-center gap-1.5 py-2.5 border border-[var(--accent)]/50 text-[var(--accent)] text-[10px] tracking-[0.15em] uppercase hover:bg-[var(--accent)]/10">
              <RotateCcw size={12} /> Reopen for review
            </button>
          ) : (
            <div className="flex gap-2">
              <button onClick={() => open('reject')} className="flex-1 flex items-center justify-center gap-1.5 py-2.5 border border-[var(--critical)]/40 text-[var(--critical)] text-[10px] tracking-[0.15em] uppercase hover:bg-[var(--critical)]/10">
                <ShieldX size={12} /> Reject
              </button>
              <button onClick={() => open('approve')} className="flex-1 flex items-center justify-center gap-1.5 py-2.5 bg-[var(--ok)] text-[#fff] text-[10px] tracking-[0.15em] uppercase hover:opacity-90">
                <ShieldCheck size={12} /> Approve
              </button>
            </div>
          )
        ) : (
          <>
            {confirming === 'approve' && (
              <>
                <p className="text-[10px] text-[var(--text)]">
                  Approve <span className="text-[var(--accent)]">{a.fullName || a.username}</span> as {a.position || roleLabel(a.role)} of {a.place}?
                  {a.hasDocument && a.verification !== 'verified' && <span className="block text-[9px] text-[var(--warn)] mt-1">Their ID hasn&apos;t been marked as matching yet.</span>}
                </p>
                {a.kind === 'barangay' && a.isNewPlace && (
                  <SelectInput
                    label="Covering police station" value={stationId} onChange={setStationId} placeholder="decide later in Stations"
                    options={stations.map(s => ({ value: s.id, label: s.name }))}
                  />
                )}
                <TextAreaInput label="Note (optional)" value={reason} onChange={setReason} rows={2} placeholder="e.g. Verified by phone with the city DILG office" />
              </>
            )}
            {confirming === 'reject' && (
              <>
                <TextAreaInput label="Reason for rejecting *" value={reason} onChange={setReason} rows={2}
                  placeholder="e.g. ID name doesn't match the applicant; no such position in this barangay" />
                <p className={`text-[9px] ${reason.trim().length < MIN_REJECT_REASON ? 'text-[var(--text-3)]' : 'text-[var(--ok)]'}`}>
                  {reason.trim().length}/{MIN_REJECT_REASON} characters minimum · kept with the application and in the audit log
                </p>
              </>
            )}
            {confirming === 'reopen' && (
              <div className="border border-[var(--warn)]/30 bg-[var(--warn)]/[0.04] p-3 space-y-3">
                <div className="flex items-center gap-1.5 text-[8px] tracking-[0.15em] uppercase text-[var(--warn)]">
                  <Lock size={10} /> Reversing a decision — recorded in the audit log
                </div>
                <TextAreaInput label="Why this deserves another review *" value={reason} onChange={setReason} rows={2}
                  placeholder="e.g. Applicant submitted a corrected ID; verified with the municipal office" />
                <p className={`text-[9px] ${reason.trim().length < MIN_REOPEN_REASON ? 'text-[var(--text-3)]' : 'text-[var(--ok)]'}`}>
                  {reason.trim().length}/{MIN_REOPEN_REASON} characters minimum
                </p>
                <FieldInput label="Your DevTeam password *" type="password" value={password} onChange={setPassword} placeholder="re-enter to confirm it's you" />
                <p className="text-[9px] text-[var(--text-3)]">It goes back to Pending, where it is approved or rejected again. The earlier rejection stays in the audit log.</p>
              </div>
            )}
            {error && <p className="text-[10px] text-[var(--critical)]">{error}</p>}
            <div className="flex gap-2">
              <button onClick={() => open(null)} disabled={busy} className="flex-1 py-2.5 border border-[var(--line)] text-[10px] tracking-[0.15em] uppercase text-[var(--text-2)] hover:text-[var(--text)] disabled:opacity-40">
                Cancel
              </button>
              <button
                onClick={submit} disabled={busy}
                className={`flex-1 py-2.5 text-[10px] tracking-[0.15em] uppercase disabled:opacity-40 ${confirming === 'reject'
                  ? 'bg-[var(--critical)] text-[#fff]' : confirming === 'approve' ? 'bg-[var(--ok)] text-[#fff]' : 'bg-[var(--accent)] text-[#fff]'}`}
              >
                {busy ? 'Saving…' : confirming === 'reject' ? 'Confirm rejection' : confirming === 'approve' ? 'Confirm approval' : 'Reopen application'}
              </button>
            </div>
          </>
        )}
      </div>
    </>
  );
}
