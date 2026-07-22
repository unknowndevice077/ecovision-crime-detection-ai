"use client";

import React, { useState } from 'react';
import { Shield, UserPlus, ArrowRight, Building, Lock, User, MapPin } from 'lucide-react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export default function SignupPage() {
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
      else setError(data.detail || "System Error: Username Conflict");
    } catch (err) {
      setError("Backend Connection Failure");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen bg-[#0a0c10] flex items-center justify-center p-6 text-slate-200">
      <div className="w-full max-w-md bg-[#11141b] border border-white/5 rounded-[3rem] p-10 shadow-2xl space-y-8">
        <div className="text-center">
          <div className="inline-block p-4 bg-emerald-500/10 rounded-2xl text-emerald-500 mb-4">
            <UserPlus size={32} />
          </div>
          <h1 className="text-xl font-bold text-white uppercase tracking-widest">Create Admin Account</h1>
          <p className="text-[10px] text-slate-500 uppercase font-mono mt-1">Precinct Captain / Barangay Captain Registration</p>
          <p className="text-[9px] text-slate-600 font-mono mt-2 leading-relaxed">
            Standard operator accounts aren't created here -- once you're signed in
            as an admin, you'll create and manage your own users from the dashboard.
          </p>
        </div>

        <form onSubmit={handleSignup} className="space-y-4 font-sans">
          <div className="relative">
            <User className="absolute left-4 top-1/2 -translate-y-1/2 text-slate-500" size={16} />
            <input 
              title="Set Username ID"
              placeholder="Username ID" 
              value={formData.username}
              onChange={e => setFormData({...formData, username: e.target.value})}
              disabled={isSubmitting}
              className="w-full bg-black/40 border border-white/5 rounded-xl p-4 pl-12 text-xs text-white outline-none focus:border-emerald-500 transition-all disabled:opacity-50" 
              required
            />
          </div>

          <div className="relative">
            <Lock className="absolute left-4 top-1/2 -translate-y-1/2 text-slate-500" size={16} />
            <input 
              type="password" 
              title="Set Secure Access Key"
              placeholder="Secure Access Key" 
              value={formData.password}
              onChange={e => setFormData({...formData, password: e.target.value})}
              disabled={isSubmitting}
              className="w-full bg-black/40 border border-white/5 rounded-xl p-4 pl-12 text-xs text-white outline-none focus:border-emerald-500 transition-all disabled:opacity-50" 
              required
            />
          </div>

          <div className="relative group">
            <Shield className="absolute left-4 top-1/2 -translate-y-1/2 text-emerald-500" size={16} />
            <select 
              title="Designate Admin Role"
              aria-label="Designate Admin Role"
              value={formData.role} 
              onChange={e => setFormData({...formData, role: e.target.value})}
              disabled={isSubmitting}
              className="w-full bg-black/40 border border-white/5 rounded-xl p-4 pl-12 text-xs text-white outline-none focus:border-emerald-500 transition-all appearance-none cursor-pointer disabled:opacity-50"
            >
              <option value="PRECINCT_CAPTAIN" className="bg-[#0f172a]">PRECINCT CAPTAIN (POLICE ADMIN)</option>
              <option value="BARANGAY_CAPTAIN" className="bg-[#0f172a]">BARANGAY CAPTAIN (BARANGAY ADMIN)</option>
            </select>
          </div>

          <div className="relative">
            <MapPin className="absolute left-4 top-1/2 -translate-y-1/2 text-slate-500" size={16} />
            <input 
              title="Enter Location (e.g. Cogon)"
              placeholder="Location (e.g. Cogon)" 
              value={formData.barangayId}
              onChange={e => setFormData({...formData, barangayId: e.target.value})}
              disabled={isSubmitting}
              className="w-full bg-black/40 border border-white/5 rounded-xl p-4 pl-12 text-xs text-white outline-none focus:border-emerald-500 transition-all disabled:opacity-50" 
              required
            />
          </div>

          <div className="relative">
            <Building className="absolute left-4 top-1/2 -translate-y-1/2 text-slate-500" size={16} />
            <input 
              title="Enter Station / Precinct Name"
              placeholder="Station / Precinct Name" 
              value={formData.assignment}
              onChange={e => setFormData({...formData, assignment: e.target.value})}
              disabled={isSubmitting}
              className="w-full bg-black/40 border border-white/5 rounded-xl p-4 pl-12 text-xs text-white outline-none focus:border-emerald-500 transition-all disabled:opacity-50" 
              required
            />
          </div>

          {error && <p className="text-red-500 text-[9px] text-center uppercase font-bold">{error}</p>}

          <button
            disabled={isSubmitting}
            className="w-full py-4 bg-emerald-600 text-black font-black uppercase text-[10px] tracking-widest rounded-xl hover:bg-emerald-500 active:scale-[0.98] transition-all flex items-center justify-center gap-2 shadow-lg disabled:opacity-50 disabled:active:scale-100"
          >
            {isSubmitting ? "Submitting…" : "Establish Account"} <ArrowRight size={14} />
          </button>
        </form>

        <div className="text-center border-t border-white/5 pt-6">
          <Link href="/loginpage/login" className="text-[10px] text-slate-500 hover:text-emerald-500 uppercase font-bold transition-colors">
            Return to Authorization
          </Link>
        </div>
      </div>
    </div>
  );
}