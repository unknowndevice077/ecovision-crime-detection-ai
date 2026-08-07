"use client";

import React, { useState } from 'react';
import { Shield, Lock, User, AlertTriangle } from 'lucide-react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { useRuntimeConfig } from '../../hooks/useRuntimeConfig';

export default function LoginPage() {
  const { apiUrl: API_URL } = useRuntimeConfig();
  const [creds, setCreds] = useState({ username: '', password: '' });
  const [error, setError] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const router = useRouter();

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setIsSubmitting(true);
    try {
      const res = await fetch(`${API_URL}/api/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(creds),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        // Token is a signed, server-verified session credential -- the
        // backend checks it (and the role inside it) on every admin/devteam
        // endpoint, rather than trusting whatever role the client claims.
        localStorage.setItem('ecoUser', JSON.stringify(data.user));
        localStorage.setItem('ecoToken', data.token);
        router.push('/');
      } else {
        setError(data.detail || "Invalid username or password");
      }
    } catch (err) {
      setError("Cannot reach server — check that the backend is running");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center p-6" style={{ background: 'var(--bg)' }}>
      <div className="w-full max-w-[340px]">

        {/* Identity block -- kept plain. This is a restricted system sign-in,
            not a product landing page. */}
        <div className="flex items-center gap-2.5 mb-5 pb-4 border-b" style={{ borderColor: 'var(--line)' }}>
          <Shield size={20} style={{ color: 'var(--accent)' }} className="stroke-[2.2]" />
          <div>
            <h1 className="text-[13px] font-bold tracking-[0.18em] text-white leading-none">ECOVISION SENTINEL</h1>
            <p className="label mt-1.5">Security Monitoring System</p>
          </div>
        </div>

        <div className="border" style={{ background: 'var(--panel)', borderColor: 'var(--line)' }}>
          <div className="h-8 flex items-center px-2.5 border-b" style={{ borderColor: 'var(--line)' }}>
            <span className="label" style={{ color: 'var(--text)' }}>Operator Sign-In</span>
          </div>

          <form onSubmit={handleLogin} className="p-3.5 space-y-3.5">
            <div>
              <label htmlFor="login-user" className="label flex items-center gap-1.5 mb-1.5">
                <User size={11} /> Username
              </label>
              <input
                id="login-user"
                title="Username"
                autoComplete="username"
                value={creds.username}
                onChange={e => setCreds({ ...creds, username: e.target.value })}
                disabled={isSubmitting}
                className="data w-full px-2.5 py-2.5 text-[12px] text-white border outline-none disabled:opacity-50"
                style={{ background: 'var(--bg)', borderColor: 'var(--line)' }}
                required
              />
            </div>

            <div>
              <label htmlFor="login-pass" className="label flex items-center gap-1.5 mb-1.5">
                <Lock size={11} /> Password
              </label>
              <input
                id="login-pass"
                type="password"
                title="Password"
                autoComplete="current-password"
                value={creds.password}
                onChange={e => setCreds({ ...creds, password: e.target.value })}
                disabled={isSubmitting}
                className="data w-full px-2.5 py-2.5 text-[12px] text-white border outline-none disabled:opacity-50"
                style={{ background: 'var(--bg)', borderColor: 'var(--line)' }}
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
              className="w-full py-2.5 text-[11px] font-bold uppercase tracking-[0.15em] text-white transition-opacity hover:opacity-90 disabled:opacity-50"
              style={{ background: 'var(--accent)' }}
            >
              {isSubmitting ? "Signing in…" : "Sign In"}
            </button>
          </form>
        </div>

        <div className="mt-3 pt-3 border-t space-y-2" style={{ borderColor: 'var(--line)' }}>
          <Link
            href="/loginpage/signup"
            className="label block transition-colors hover:text-white"
          >
            Register precinct / barangay admin account →
          </Link>
          <p className="text-[10px] leading-relaxed" style={{ color: 'var(--text-3)' }}>
            Standard operator accounts are issued by your administrator, not self-registered.
          </p>
        </div>

        <p className="mt-4 text-[9px] text-center" style={{ color: 'var(--text-3)' }}>
          Authorized personnel only. Access is logged.
        </p>
      </div>
    </div>
  );
}
