"use client";

import React, { useState } from 'react';
import { Shield, Lock, User, ArrowRight } from 'lucide-react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';

export default function LoginPage() {
  const [creds, setCreds] = useState({ username: '', password: '' });
  const [error, setError] = useState('');
  const router = useRouter();

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    try {
      const res = await fetch("http://localhost:8000/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(creds),
      });
      const data = await res.json();
      if (res.ok) {
        // Token is a signed, server-verified session credential -- the
        // backend checks it (and the role inside it) on every admin/devteam
        // endpoint, rather than trusting whatever role the client claims.
        localStorage.setItem('ecoUser', JSON.stringify(data.user));
        localStorage.setItem('ecoToken', data.token);
        router.push('/');
      } else {
        setError(data.detail || "Unauthorized: Invalid Credentials");
      }
    } catch (err) {
      setError("System Offline: Check Backend Connection");
    }
  };

  return (
    <div className="min-h-screen bg-[#0a0c10] flex items-center justify-center p-6">
      <div className="w-full max-w-sm bg-[#11141b] border border-white/10 rounded-[3rem] p-10 space-y-10 shadow-2xl ring-1 ring-white/5">
        <div className="flex flex-col items-center gap-4">
          <div className="p-5 bg-emerald-500 rounded-3xl shadow-[0_0_30px_rgba(16,185,129,0.2)]">
            <Shield className="text-[#0a0c10]" size={40} />
          </div>
          <div className="text-center">
            <h1 className="text-xl font-bold text-white uppercase tracking-widest">EcoVision Sentinel</h1>
            <p className="text-[9px] text-slate-500 uppercase font-mono mt-1">Local SQL Authorization Required</p>
          </div>
        </div>

        <form onSubmit={handleLogin} className="space-y-5">
          <div className="space-y-2">
            <label className="text-[9px] font-bold text-slate-500 uppercase ml-2 tracking-widest flex items-center gap-2">
              <User size={12}/> Credential ID
            </label>
            <input 
              title="Enter your Username ID"
              placeholder="Username ID"
              onChange={e => setCreds({...creds, username: e.target.value})}
              className="w-full bg-black/40 border border-white/5 rounded-xl p-4 text-xs text-white outline-none focus:border-emerald-500 font-mono" 
              required
            />
          </div>

          <div className="space-y-2">
            <label className="text-[9px] font-bold text-slate-500 uppercase ml-2 tracking-widest flex items-center gap-2">
              <Lock size={12}/> Access Key
            </label>
            <input 
              type="password" 
              title="Enter your Access Key"
              placeholder="Secure Access Key"
              onChange={e => setCreds({...creds, password: e.target.value})}
              className="w-full bg-black/40 border border-white/5 rounded-xl p-4 text-xs text-white outline-none focus:border-emerald-500 font-mono" 
              required
            />
          </div>

          {error && <p className="text-red-500 text-[9px] text-center font-bold uppercase animate-pulse">{error}</p>}

          <button className="w-full py-4 bg-emerald-600 text-black font-black uppercase text-[10px] tracking-[0.2em] rounded-xl hover:bg-emerald-500 active:scale-95 transition-all shadow-xl flex items-center justify-center gap-2">
            Authorize Node <ArrowRight size={14}/>
          </button>
        </form>

        <div className="text-center pt-4 border-t border-white/5 space-y-2">
          <Link href="/loginpage/signup" className="text-[9px] text-slate-500 hover:text-emerald-500 uppercase font-bold transition-all block">
            Initialize New Precinct / Barangay Admin Account
          </Link>
          <p className="text-[8px] text-slate-600 font-mono">
            Standard operator accounts are created by your admin, not here.
          </p>
        </div>
      </div>
    </div>
  );
}