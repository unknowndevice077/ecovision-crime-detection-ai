"use client";

import React, { useState } from 'react';
import { Shield, UserPlus, ArrowRight, Building, Lock, User } from 'lucide-react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';

export default function SignupPage() {
  const [formData, setFormData] = useState({ username: '', password: '', role: 'POLICE', assignment: '' });
  const [error, setError] = useState('');
  const router = useRouter();

  const handleSignup = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const res = await fetch("http://localhost:8000/api/signup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(formData),
      });
      if (res.ok) router.push('/loginpage/login');
      else setError("System Error: Username Conflict");
    } catch (err) { setError("Backend Connection Failure"); }
  };

  return (
    <div className="min-h-screen bg-[#0a0c10] flex items-center justify-center p-6 text-slate-200">
      <div className="w-full max-w-md bg-[#11141b] border border-white/5 rounded-[3rem] p-10 shadow-2xl space-y-8">
        <div className="text-center">
          <div className="inline-block p-4 bg-emerald-500/10 rounded-2xl text-emerald-500 mb-4">
            <UserPlus size={32} />
          </div>
          <h1 className="text-xl font-bold text-white uppercase tracking-widest">Create Credentials</h1>
          <p className="text-[10px] text-slate-500 uppercase font-mono mt-1">Register New Node Operator</p>
        </div>

        <form onSubmit={handleSignup} className="space-y-4 font-sans">
          <div className="relative">
            <User className="absolute left-4 top-1/2 -translate-y-1/2 text-slate-500" size={16} />
            <input 
              title="Set Username ID"
              placeholder="Username ID" 
              onChange={e => setFormData({...formData, username: e.target.value})}
              className="w-full bg-black/40 border border-white/5 rounded-xl p-4 pl-12 text-xs text-white outline-none focus:border-emerald-500 transition-all" 
              required
            />
          </div>

          <div className="relative">
            <Lock className="absolute left-4 top-1/2 -translate-y-1/2 text-slate-500" size={16} />
            <input 
              type="password" 
              title="Set Secure Access Key"
              placeholder="Secure Access Key" 
              onChange={e => setFormData({...formData, password: e.target.value})}
              className="w-full bg-black/40 border border-white/5 rounded-xl p-4 pl-12 text-xs text-white outline-none focus:border-emerald-500 transition-all" 
              required
            />
          </div>

          <div className="relative group">
            <Shield className="absolute left-4 top-1/2 -translate-y-1/2 text-emerald-500" size={16} />
            <select 
              title="Designate Access Role"
              aria-label="Designate Access Role"
              value={formData.role} 
              onChange={e => setFormData({...formData, role: e.target.value})}
              className="w-full bg-black/40 border border-white/5 rounded-xl p-4 pl-12 text-xs text-white outline-none focus:border-emerald-500 transition-all appearance-none cursor-pointer"
            >
              <option value="POLICE" className="bg-[#0f172a]">POLICE DEPARTMENT (SIR ACCESS)</option>
              <option value="BARANGAY" className="bg-[#0f172a]">BARANGAY UNIT (HEALTH ONLY)</option>
            </select>
          </div>

          <div className="relative">
            <Building className="absolute left-4 top-1/2 -translate-y-1/2 text-slate-500" size={16} />
            <input 
              title="Enter Assigned Location"
              placeholder="Assigned Precinct / Barangay" 
              onChange={e => setFormData({...formData, assignment: e.target.value})}
              className="w-full bg-black/40 border border-white/5 rounded-xl p-4 pl-12 text-xs text-white outline-none focus:border-emerald-500 transition-all" 
              required
            />
          </div>

          {error && <p className="text-red-500 text-[9px] text-center uppercase font-bold">{error}</p>}

          <button className="w-full py-4 bg-emerald-600 text-black font-black uppercase text-[10px] tracking-widest rounded-xl hover:bg-emerald-500 active:scale-[0.98] transition-all flex items-center justify-center gap-2 shadow-lg">
            Establish Account <ArrowRight size={14} />
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