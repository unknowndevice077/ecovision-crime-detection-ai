"use client";

import React, { useState } from 'react';
import { Shield, Lock, User, AlertTriangle, Eye, EyeOff } from 'lucide-react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { useRuntimeConfig } from '../../hooks/useRuntimeConfig';
import ThemeToggle from '../../components/ThemeToggle';

export default function LoginPage() {
  const { apiUrl: API_URL } = useRuntimeConfig();
  const [creds, setCreds] = useState({ username: '', password: '' });
  const [showPassword, setShowPassword] = useState(false);
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
    <div className="min-h-screen flex items-center justify-center p-6 relative" style={{ background: 'var(--bg)' }}>
      <div className="absolute top-5 right-5">
        <ThemeToggle />
      </div>

      <div className="w-full max-w-[360px] animate-rise-in">

        {/* Identity block -- kept plain. This is a restricted system sign-in,
            not a product landing page. */}
        <div className="flex items-center gap-3 mb-6 pb-5 border-b" style={{ borderColor: 'var(--line)' }}>
          <div
            className="h-9 w-9 flex items-center justify-center shrink-0"
            style={{ background: 'var(--cyan-dim)', borderRadius: 'var(--radius-md)' }}
          >
            <Shield size={18} style={{ color: 'var(--cyan)' }} className="stroke-[2.2]" />
          </div>
          <div>
            <h1 className="disp text-[15px] font-extrabold tracking-[0.01em]" style={{ color: 'var(--text)' }}>EcoVision Sentinel</h1>
            <p className="label mt-1">Security Monitoring System</p>
          </div>
        </div>

        <div className="border" style={{ background: 'var(--panel)', borderColor: 'var(--line)', borderRadius: 'var(--radius-md)', overflow: 'hidden' }}>
          <div className="h-9 flex items-center px-4 border-b" style={{ borderColor: 'var(--line)', background: 'var(--panel-2)' }}>
            <span className="label" style={{ color: 'var(--text)' }}>Operator Sign-In</span>
          </div>

          <form onSubmit={handleLogin} className="p-4 space-y-4">
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
                className="data w-full px-3 py-2.5 text-[12px] border outline-none disabled:opacity-50 transition-colors focus:border-[var(--accent)]"
                style={{ background: 'var(--bg)', borderColor: 'var(--line)', color: 'var(--text)', borderRadius: 'var(--radius-sm)' }}
                required
              />
            </div>

            <div>
              <label htmlFor="login-pass" className="label flex items-center gap-1.5 mb-1.5">
                <Lock size={11} /> Password
              </label>
              <div className="relative">
                <input
                  id="login-pass"
                  type={showPassword ? 'text' : 'password'}
                  title="Password"
                  autoComplete="current-password"
                  value={creds.password}
                  onChange={e => setCreds({ ...creds, password: e.target.value })}
                  disabled={isSubmitting}
                  className="data w-full px-3 py-2.5 pr-9 text-[12px] border outline-none disabled:opacity-50 transition-colors focus:border-[var(--accent)]"
                  style={{ background: 'var(--bg)', borderColor: 'var(--line)', color: 'var(--text)', borderRadius: 'var(--radius-sm)' }}
                  required
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(s => !s)}
                  title={showPassword ? 'Hide password' : 'Show password'}
                  tabIndex={-1}
                  className="absolute right-3 top-1/2 -translate-y-1/2 transition-colors"
                  style={{ color: 'var(--text-3)' }}
                  onMouseEnter={e => (e.currentTarget.style.color = 'var(--text)')}
                  onMouseLeave={e => (e.currentTarget.style.color = 'var(--text-3)')}
                >
                  {showPassword ? <EyeOff size={13} /> : <Eye size={13} />}
                </button>
              </div>
            </div>

            {error && (
              <div
                className="flex items-start gap-2 px-3 py-2.5 border animate-rise-in"
                style={{ background: 'var(--critical-dim)', borderColor: 'var(--critical)', borderRadius: 'var(--radius-sm)' }}
                role="alert"
              >
                <AlertTriangle size={13} style={{ color: 'var(--critical)' }} className="shrink-0 mt-px" />
                <span className="text-[11px] leading-snug" style={{ color: 'var(--critical)' }}>{error}</span>
              </div>
            )}

            <button
              disabled={isSubmitting}
              className="disp w-full py-2.5 text-[12px] font-bold text-white transition-all hover:opacity-90 active:scale-[0.98] disabled:opacity-50 disabled:active:scale-100"
              style={{ background: 'var(--accent)', borderRadius: 'var(--radius-sm)' }}
            >
              {isSubmitting ? "Signing in…" : "Sign In"}
            </button>
          </form>
        </div>

        <div className="mt-4 pt-3 border-t space-y-2" style={{ borderColor: 'var(--line)' }}>
          <Link
            href="/loginpage/signup"
            className="label block transition-colors"
            style={{ color: 'var(--text-2)' }}
            onMouseEnter={e => (e.currentTarget.style.color = 'var(--text)')}
            onMouseLeave={e => (e.currentTarget.style.color = 'var(--text-2)')}
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
