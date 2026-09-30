"use client";

import React, { useState, useEffect } from 'react';
import { Shield, ArrowRight, Building, Lock, User, MapPin, AlertTriangle, Eye, EyeOff, IdCard, Check, Camera, Phone, Home, Calendar, Briefcase } from 'lucide-react';
import Link from 'next/link';
import { useRuntimeConfig } from '../../hooks/useRuntimeConfig';
import { positionsForRole } from '../../lib/positions';

type Station = { id: string; name: string };

export default function SignupPage() {
  const { apiUrl: API_URL, loaded: configLoaded } = useRuntimeConfig();
  const [formData, setFormData] = useState({
    username: '', password: '', role: 'BARANGAY_ADMIN',
    barangay_id: '', station_id: '', assignment: '',
    // Personal record DevTeam reviews the application against.
    full_name: '', birthdate: '', home_address: '', contact_number: '', position: '',
  });
  const [showPassword, setShowPassword] = useState(false);
  const [stations, setStations] = useState<Station[]>([]);
  const [error, setError] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Step 2 (#9, 2026-09-23): once the account row exists, offer an ID
  // upload before sending the applicant on to sign-in. Uses the signup-
  // scoped endpoint (username+password re-proves identity) rather than a
  // token -- a still-pending BARANGAY_ADMIN can't log in yet to reach the
  // authenticated .../me/verification endpoint, and a PNP_ADMIN could log
  // in immediately but this keeps one consistent step for both branches.
  const [createdUserId, setCreatedUserId] = useState<number | null>(null);
  const [idFile, setIdFile] = useState<File | null>(null);
  const [faceFile, setFaceFile] = useState<File | null>(null);
  const [idUploadBusy, setIdUploadBusy] = useState(false);
  const [idUploadDone, setIdUploadDone] = useState(false);
  const [idUploadError, setIdUploadError] = useState('');

  const isPnp = formData.role === 'PNP_ADMIN';

  // A PNP admin joins a station DevTeam already created -- a station's whole
  // purpose is the jurisdiction DevTeam assigns it, so a self-created one
  // would see nothing. Barangays, by contrast, are created here as 'pending'
  // and approved afterwards.
  useEffect(() => {
    if (!configLoaded) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`${API_URL}/api/stations`);
        if (!res.ok) return;
        const data = await res.json();
        if (!cancelled) setStations(data);
      } catch {
        /* station list is only needed for the PNP branch; ignore */
      }
    })();
    return () => { cancelled = true; };
  }, [API_URL, configLoaded]);

  const handleSignup = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');

    if (isPnp && !formData.station_id) {
      setError('Select the police station you belong to');
      return;
    }
    if (!isPnp && !formData.barangay_id.trim()) {
      setError('Enter your barangay');
      return;
    }
    if (!formData.full_name.trim() || !formData.birthdate || !formData.home_address.trim() || !formData.position) {
      setError('Full name, birthdate, residence and position are required');
      return;
    }

    setIsSubmitting(true);
    try {
      // snake_case: backend.py's UserSignup has no alias generator, so a
      // camelCase body (this form used to send barangayId) fails validation
      // outright rather than being coerced.
      const res = await fetch(`${API_URL}/api/signup`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: formData.username,
          password: formData.password,
          role: formData.role,
          assignment: formData.assignment,
          barangay_id: isPnp ? null : formData.barangay_id.trim(),
          station_id: isPnp ? formData.station_id : null,
          full_name: formData.full_name.trim(),
          birthdate: formData.birthdate,
          home_address: formData.home_address.trim(),
          contact_number: formData.contact_number.trim() || null,
          position: formData.position,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) setCreatedUserId(data.id);
      else setError(data.detail || "That username is already taken");
    } catch (err) {
      setError("Cannot reach server — check that the backend is running");
    } finally {
      setIsSubmitting(false);
    }
  };

  const submitIdDocument = async () => {
    if (!createdUserId || (!idFile && !faceFile)) return;
    setIdUploadBusy(true);
    setIdUploadError('');
    try {
      const body = new FormData();
      body.append('username', formData.username);
      body.append('password', formData.password);
      if (idFile) body.append('id_document', idFile);
      if (faceFile) body.append('face_photo', faceFile);
      const res = await fetch(`${API_URL}/api/signup/${createdUserId}/verification`, { method: 'POST', body });
      const d = await res.json().catch(() => ({}));
      if (res.ok) setIdUploadDone(true);
      else setIdUploadError(d.detail || 'Could not upload — you can add this later once you can sign in.');
    } catch {
      setIdUploadError('Backend connection failure — you can add this later once you can sign in.');
    } finally {
      setIdUploadBusy(false);
    }
  };

  const fieldStyle = { background: 'var(--bg)', borderColor: 'var(--line)' };
  const fieldClass =
    "data w-full px-2.5 py-2.5 text-[12px] text-[var(--text)] border outline-none focus:border-[var(--accent)] transition-colors disabled:opacity-50";

  return (
    <div className="min-h-screen flex items-center justify-center p-6" style={{ background: 'var(--bg)' }}>
      <div className="w-full max-w-[340px]">

        {/* Same identity block as the sign-in screen so the two read as one system */}
        <div className="flex items-center gap-2.5 mb-5 pb-4 border-b" style={{ borderColor: 'var(--line)' }}>
          <Shield size={20} style={{ color: 'var(--accent)' }} className="stroke-[2.2]" />
          <div>
            <h1 className="text-[13px] font-bold tracking-[0.18em] text-[var(--text)] leading-none">ECOVISION SENTINEL</h1>
            <p className="label mt-1.5">Security Monitoring System</p>
          </div>
        </div>

        <div className="border" style={{ background: 'var(--panel)', borderColor: 'var(--line)' }}>
          <div className="h-8 flex items-center px-2.5 border-b" style={{ borderColor: 'var(--line)' }}>
            <span className="label" style={{ color: 'var(--text)' }}>
              {createdUserId === null ? 'Administrator Registration' : 'Verify your identity'}
            </span>
          </div>

          {createdUserId !== null ? (
            <div className="p-3.5 space-y-3.5">
              {idUploadDone ? (
                <div className="flex items-start gap-2 px-2.5 py-2.5 border" style={{ background: 'rgba(0,180,120,0.08)', borderColor: 'var(--ok)' }}>
                  <Check size={13} style={{ color: 'var(--ok)' }} className="shrink-0 mt-px" />
                  <span className="text-[11px] leading-snug" style={{ color: 'var(--text)' }}>
                    Submitted. It&apos;ll be checked alongside your account review.
                  </span>
                </div>
              ) : (
                <>
                  <p className="text-[10.5px] leading-relaxed" style={{ color: 'var(--text-2)' }}>
                    Account created. Upload a government ID and a clear photo of your face so DevTeam can
                    confirm you are who you say you are — applications without them are hard to approve.
                  </p>
                  <div>
                    <label htmlFor="su-id-doc" className="label flex items-center gap-1.5 mb-1.5">
                      <IdCard size={11} /> Government ID (JPG, PNG, WEBP, or PDF)
                    </label>
                    <input
                      id="su-id-doc"
                      type="file"
                      accept=".jpg,.jpeg,.png,.webp,.pdf"
                      onChange={e => setIdFile(e.target.files?.[0] || null)}
                      disabled={idUploadBusy}
                      className={fieldClass}
                      style={fieldStyle}
                    />
                  </div>
                  <div>
                    <label htmlFor="su-face" className="label flex items-center gap-1.5 mb-1.5">
                      <Camera size={11} /> Face photo (JPG, PNG, or WEBP)
                    </label>
                    {/* capture="user" opens the front camera on phones/tablets;
                        desktops fall back to a normal file picker. */}
                    <input
                      id="su-face"
                      type="file"
                      accept=".jpg,.jpeg,.png,.webp"
                      capture="user"
                      onChange={e => setFaceFile(e.target.files?.[0] || null)}
                      disabled={idUploadBusy}
                      className={fieldClass}
                      style={fieldStyle}
                    />
                  </div>
                  {idUploadError && (
                    <div className="flex items-start gap-2 px-2.5 py-2 border" style={{ background: 'rgba(229,52,47,0.10)', borderColor: 'var(--critical)' }} role="alert">
                      <AlertTriangle size={13} style={{ color: 'var(--critical)' }} className="shrink-0 mt-px" />
                      <span className="text-[11px] leading-snug" style={{ color: 'var(--critical)' }}>{idUploadError}</span>
                    </div>
                  )}
                  <button
                    onClick={submitIdDocument}
                    disabled={idUploadBusy || (!idFile && !faceFile)}
                    className="w-full py-2.5 text-[11px] font-bold uppercase tracking-[0.15em] text-white transition-opacity hover:opacity-90 disabled:opacity-50"
                    style={{ background: 'var(--accent)' }}
                  >
                    {idUploadBusy ? 'Uploading…' : 'Submit'}
                  </button>
                </>
              )}
              <Link
                href="/loginpage/login"
                className="block text-center text-[10px] tracking-[0.1em] uppercase pt-1 transition-colors hover:text-[var(--text)]"
                style={{ color: 'var(--text-3)' }}
              >
                {idUploadDone ? 'Continue to sign-in' : 'Skip for now — continue to sign-in'}
              </Link>
            </div>
          ) : (
          <form onSubmit={handleSignup} className="p-3.5 space-y-3.5">
            <div>
              <label htmlFor="su-user" className="label flex items-center gap-1.5 mb-1.5">
                <User size={11} /> Username
              </label>
              <input
                id="su-user"
                title="Username"
                autoComplete="username"
                value={formData.username}
                onChange={e => setFormData({ ...formData, username: e.target.value })}
                disabled={isSubmitting}
                className={fieldClass}
                style={fieldStyle}
                required
              />
            </div>

            <div>
              <label htmlFor="su-pass" className="label flex items-center gap-1.5 mb-1.5">
                <Lock size={11} /> Password
              </label>
              <div className="relative">
                <input
                  id="su-pass"
                  type={showPassword ? 'text' : 'password'}
                  title="Password"
                  autoComplete="new-password"
                  value={formData.password}
                  onChange={e => setFormData({ ...formData, password: e.target.value })}
                  disabled={isSubmitting}
                  className={`${fieldClass} pr-9`}
                  style={fieldStyle}
                  required
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(s => !s)}
                  title={showPassword ? 'Hide password' : 'Show password'}
                  tabIndex={-1}
                  className="absolute right-2.5 top-1/2 -translate-y-1/2 text-[var(--text-3)] hover:text-[var(--text)] transition-colors"
                >
                  {showPassword ? <EyeOff size={13} /> : <Eye size={13} />}
                </button>
              </div>
            </div>

            <div>
              <label htmlFor="su-role" className="label flex items-center gap-1.5 mb-1.5">
                <Shield size={11} /> Administrator role
              </label>
              <select
                id="su-role"
                title="Administrator role"
                value={formData.role}
                onChange={e => setFormData({ ...formData, role: e.target.value, position: '' })}
                disabled={isSubmitting}
                className={`${fieldClass} cursor-pointer`}
                style={fieldStyle}
              >
                <option value="BARANGAY_ADMIN">Barangay Admin (Punong Barangay)</option>
                <option value="PNP_ADMIN">PNP Admin (station commander)</option>
              </select>
            </div>

            {/* Barangay roles are scoped to one barangay; PNP roles are scoped
                to a station's jurisdiction. Which field you fill in follows
                from the role -- the database enforces the same rule. */}
            {isPnp ? (
              <div>
                <label htmlFor="su-station-id" className="label flex items-center gap-1.5 mb-1.5">
                  <Building size={11} /> Police station
                </label>
                <select
                  id="su-station-id"
                  title="Police station"
                  value={formData.station_id}
                  onChange={e => setFormData({ ...formData, station_id: e.target.value })}
                  disabled={isSubmitting || stations.length === 0}
                  className={`${fieldClass} cursor-pointer`}
                  style={fieldStyle}
                >
                  <option value="">
                    {stations.length ? 'Select your station…' : 'No stations registered yet'}
                  </option>
                  {stations.map(s => (
                    <option key={s.id} value={s.id}>{s.name}</option>
                  ))}
                </select>
                {stations.length === 0 && (
                  <p className="text-[10px] leading-relaxed mt-1.5" style={{ color: 'var(--text-3)' }}>
                    A DevTeam administrator must register your station and set its
                    jurisdiction before you can sign up.
                  </p>
                )}
              </div>
            ) : (
              <div>
                <label htmlFor="su-loc" className="label flex items-center gap-1.5 mb-1.5">
                  <MapPin size={11} /> Barangay
                </label>
                <input
                  id="su-loc"
                  title="Barangay"
                  placeholder="e.g. Cogon"
                  value={formData.barangay_id}
                  onChange={e => setFormData({ ...formData, barangay_id: e.target.value })}
                  disabled={isSubmitting}
                  className={fieldClass}
                  style={fieldStyle}
                />
              </div>
            )}

            <div>
              <label htmlFor="su-station" className="label flex items-center gap-1.5 mb-1.5">
                <Building size={11} /> Office / assignment
              </label>
              {/* This fills the account's assignment. It was labelled
                  "Station / precinct" for both roles, which asked a barangay
                  captain for a police station and a police applicant for
                  the station they had just picked above. */}
              <input
                id="su-station"
                title="Office or assignment"
                placeholder={formData.role === 'PNP_ADMIN' ? "e.g. Office of the Chief of Police" : "e.g. Barangay Hall"}
                value={formData.assignment}
                onChange={e => setFormData({ ...formData, assignment: e.target.value })}
                disabled={isSubmitting}
                className={fieldClass}
                style={fieldStyle}
                required
              />
            </div>

            {/* Personal record -- what DevTeam checks this application against
                (and matches to the ID and face photo on the next step). */}
            <div className="pt-1 border-t" style={{ borderColor: 'var(--line)' }}>
              <p className="label mt-2.5 mb-2.5" style={{ color: 'var(--text)' }}>About you</p>
              <div className="space-y-3.5">
                <div>
                  <label htmlFor="su-name" className="label flex items-center gap-1.5 mb-1.5"><User size={11} /> Full name</label>
                  <input id="su-name" title="Full name" autoComplete="name" placeholder="e.g. Juan Dela Cruz"
                    value={formData.full_name} onChange={e => setFormData({ ...formData, full_name: e.target.value })}
                    disabled={isSubmitting} className={fieldClass} style={fieldStyle} required />
                </div>
                <div className="grid grid-cols-2 gap-2.5">
                  <div>
                    <label htmlFor="su-birth" className="label flex items-center gap-1.5 mb-1.5"><Calendar size={11} /> Birthdate</label>
                    <input id="su-birth" type="date" title="Birthdate"
                      value={formData.birthdate} onChange={e => setFormData({ ...formData, birthdate: e.target.value })}
                      disabled={isSubmitting} className={fieldClass} style={fieldStyle} required />
                  </div>
                  <div>
                    <label htmlFor="su-phone" className="label flex items-center gap-1.5 mb-1.5"><Phone size={11} /> Contact no.</label>
                    <input id="su-phone" title="Contact number" autoComplete="tel" placeholder="09XX-XXX-XXXX"
                      value={formData.contact_number} onChange={e => setFormData({ ...formData, contact_number: e.target.value })}
                      disabled={isSubmitting} className={fieldClass} style={fieldStyle} />
                  </div>
                </div>
                <div>
                  <label htmlFor="su-home" className="label flex items-center gap-1.5 mb-1.5"><Home size={11} /> Residence</label>
                  <input id="su-home" title="Residential address" autoComplete="street-address" placeholder="House no., street, barangay, city"
                    value={formData.home_address} onChange={e => setFormData({ ...formData, home_address: e.target.value })}
                    disabled={isSubmitting} className={fieldClass} style={fieldStyle} required />
                </div>
                <div>
                  <label htmlFor="su-position" className="label flex items-center gap-1.5 mb-1.5"><Briefcase size={11} /> Your position there</label>
                  <select id="su-position" title="Position"
                    value={formData.position} onChange={e => setFormData({ ...formData, position: e.target.value })}
                    disabled={isSubmitting} className={`${fieldClass} cursor-pointer`} style={fieldStyle} required>
                    <option value="">Select your position…</option>
                    {positionsForRole(formData.role).map(p => <option key={p} value={p}>{p}</option>)}
                  </select>
                </div>
              </div>
            </div>

            {error && (
              <div
                className="flex items-start gap-2 px-2.5 py-2 border"
                style={{ background: 'rgba(229,52,47,0.10)', borderColor: 'var(--critical)' }}
                role="alert"
              >
                <AlertTriangle size={13} style={{ color: 'var(--critical)' }} className="shrink-0 mt-px" />
                <span className="text-[11px] leading-snug" style={{ color: 'var(--critical)' }}>{error}</span>
              </div>
            )}

            <button
              disabled={isSubmitting}
              className="w-full py-2.5 text-[11px] font-bold uppercase tracking-[0.15em] text-white transition-opacity hover:opacity-90 disabled:opacity-50 flex items-center justify-center gap-2"
              style={{ background: 'var(--accent)' }}
            >
              {isSubmitting ? "Creating…" : "Create Account"} <ArrowRight size={13} />
            </button>
          </form>
          )}
        </div>

        <div className="mt-3 pt-3 border-t space-y-2" style={{ borderColor: 'var(--line)' }}>
          <Link href="/loginpage/login" className="label block transition-colors hover:text-[var(--text)]">
            ← Back to sign-in
          </Link>
          <p className="text-[10px] leading-relaxed" style={{ color: 'var(--text-3)' }}>
            Only precinct and barangay administrator accounts are registered here. Once signed in,
            you create and manage your own operator accounts from the dashboard.
          </p>
        </div>
      </div>
    </div>
  );
}
