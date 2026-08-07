"use client";

import React, { useState } from 'react';
import { Shield, ArrowRight, Building, Lock, User, MapPin, AlertTriangle } from 'lucide-react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { useRuntimeConfig } from '../../hooks/useRuntimeConfig';

export default function SignupPage() {
  const { apiUrl: API_URL } = useRuntimeConfig();
  const [formData, setFormData] = useState({
    username: '', password: '', role: 'PRECINCT_CAPTAIN', barangayId: '', assignment: ''
  });
  const [error, setError] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const router = useRouter();

  const handleSignup = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setIsSubmitting(true);
    try {
      const res = await fetch(`${API_URL}/api/signup`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(formData),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) router.push('/loginpage/login');
      else setError(data.detail || "That username is already taken");
    } catch (err) {
      setError("Cannot reach server — check that the backend is running");
    } finally {
      setIsSubmitting(false);
    }
  };

  const fieldStyle = { background: 'var(--bg)', borderColor: 'var(--line)' };
  const fieldClass =
    "data w-full px-2.5 py-2.5 text-[12px] text-white border outline-none focus:border-[var(--accent)] transition-colors disabled:opacity-50";

  return (
    <div className="min-h-screen flex items-center justify-center p-6" style={{ background: 'var(--bg)' }}>
      <div className="w-full max-w-[340px]">

        {/* Same identity block as the sign-in screen so the two read as one system */}
        <div className="flex items-center gap-2.5 mb-5 pb-4 border-b" style={{ borderColor: 'var(--line)' }}>
          <Shield size={20} style={{ color: 'var(--accent)' }} className="stroke-[2.2]" />
          <div>
            <h1 className="text-[13px] font-bold tracking-[0.18em] text-white leading-none">ECOVISION SENTINEL</h1>
            <p className="label mt-1.5">Security Monitoring System</p>
          </div>
        </div>

        <div className="border" style={{ background: 'var(--panel)', borderColor: 'var(--line)' }}>
          <div className="h-8 flex items-center px-2.5 border-b" style={{ borderColor: 'var(--line)' }}>
            <span className="label" style={{ color: 'var(--text)' }}>Administrator Registration</span>
          </div>

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
              <input
                id="su-pass"
                type="password"
                title="Password"
                autoComplete="new-password"
                value={formData.password}
                onChange={e => setFormData({ ...formData, password: e.target.value })}
                disabled={isSubmitting}
                className={fieldClass}
                style={fieldStyle}
                required
              />
            </div>

            <div>
              <label htmlFor="su-role" className="label flex items-center gap-1.5 mb-1.5">
                <Shield size={11} /> Administrator role
              </label>
              <select
                id="su-role"
                title="Administrator role"
                value={formData.role}
                onChange={e => setFormData({ ...formData, role: e.target.value })}
                disabled={isSubmitting}
                className={`${fieldClass} cursor-pointer`}
                style={fieldStyle}
              >
                <option value="PRECINCT_CAPTAIN">Precinct Captain (police admin)</option>
                <option value="BARANGAY_CAPTAIN">Barangay Captain (barangay admin)</option>
              </select>
            </div>

            <div>
              <label htmlFor="su-loc" className="label flex items-center gap-1.5 mb-1.5">
                <MapPin size={11} /> Location
              </label>
              <input
                id="su-loc"
                title="Location"
                placeholder="e.g. Cogon"
                value={formData.barangayId}
                onChange={e => setFormData({ ...formData, barangayId: e.target.value })}
                disabled={isSubmitting}
                className={fieldClass}
                style={fieldStyle}
                required
              />
            </div>

            <div>
              <label htmlFor="su-station" className="label flex items-center gap-1.5 mb-1.5">
                <Building size={11} /> Station / precinct
              </label>
              <input
                id="su-station"
                title="Station or precinct name"
                placeholder="e.g. Station 3"
                value={formData.assignment}
                onChange={e => setFormData({ ...formData, assignment: e.target.value })}
                disabled={isSubmitting}
                className={fieldClass}
                style={fieldStyle}
                required
              />
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
        </div>

        <div className="mt-3 pt-3 border-t space-y-2" style={{ borderColor: 'var(--line)' }}>
          <Link href="/loginpage/login" className="label block transition-colors hover:text-white">
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
