// app/components/dashboard/CameraManagement.tsx
"use client";
import React from 'react';
import { Video, Trash2, Plus } from 'lucide-react';
import { Camera } from '../../types';

interface CameraManagementProps {
  cameras: Camera[];
  onDeleteCam: (id: string) => void;
  onAddClick: () => void;
}

export default function CameraManagement({ cameras, onDeleteCam, onAddClick }: CameraManagementProps) {
  return (
    <div className="grid grid-cols-2 gap-4 animate-in zoom-in-95 duration-300">
      {cameras.map((cam) => (
        <div 
          key={cam.id} 
          className="bg-[#11141b] border border-white/5 rounded-3xl p-6 shadow-xl relative group hover:border-emerald-500/20 transition-all"
        >
          <div className="flex justify-between items-start mb-6">
            <div className="p-3 rounded-xl bg-emerald-500/10 text-emerald-500">
              <Video size={22} />
            </div>
            <button 
              title={`Dismantle Link for ${cam.name}`}
              aria-label={`Dismantle Link for ${cam.name}`}
              onClick={(e) => {
                e.stopPropagation();
                onDeleteCam(cam.id);
              }} 
              className="p-2 text-slate-500 hover:text-red-500 transition-colors"
            >
              <Trash2 size={16} />
            </button>
          </div>
          <h4 className="text-sm font-semibold uppercase text-white tracking-tight">{cam.name}</h4>
          <p className="text-[10px] font-mono text-slate-500 truncate mb-6 mt-2 bg-black/20 p-2 rounded">
            {cam.url}
          </p>
          <div className="flex items-center gap-2 px-3 py-1 bg-emerald-500/5 w-fit rounded-full border border-emerald-500/10">
            <div className="w-1 h-1 bg-emerald-500 rounded-full animate-pulse" />
            <span className="text-[9px] font-bold uppercase tracking-widest text-emerald-500/80">Active Node Link</span>
          </div>
        </div>
      ))}
      
      {/* Register Button */}
      <button 
        onClick={onAddClick} 
        className="border-2 border-dashed border-white/5 rounded-3xl flex flex-col items-center justify-center p-12 text-slate-600 hover:text-emerald-500 hover:border-emerald-500/20 transition-all group"
      >
        <Plus size={32} className="mb-2 opacity-20 group-hover:opacity-100 transition-all" />
        <span className="text-[10px] font-bold uppercase tracking-widest">Register New Smartpole Node</span>
      </button>
    </div>
  );
}