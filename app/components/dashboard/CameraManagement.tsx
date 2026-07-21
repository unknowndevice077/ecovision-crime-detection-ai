// app/components/dashboard/CameraManagement.tsx
"use client";
import React from 'react';
import { Video, Trash2, Plus, Lock } from 'lucide-react';
import { Camera } from '../../types';
import { usePermissions } from '../../hooks/usePermissions';

interface CameraManagementProps {
  cameras: Camera[];
  onDeleteCam: (id: string) => void;
  onAddClick: () => void;
}

export default function CameraManagement({ cameras, onDeleteCam, onAddClick }: CameraManagementProps) {
  // Permissions were previously only checked (if at all) server-side --
  // a user without manage_cameras still saw working-looking delete/add
  // buttons that would just fail or 403 on click. This gates the UI to
  // match. Backend must still enforce this independently -- see backend.py.
  const { can } = usePermissions();
  const canManage = can('manage_cameras');

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
            {canManage ? (
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
            ) : (
              <span title="You don't have permission to manage cameras" className="p-2 text-slate-700">
                <Lock size={14} />
              </span>
            )}
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

      {/* Register Button -- hidden entirely rather than shown-then-blocked */}
      {canManage ? (
        <button
          onClick={onAddClick}
          className="border-2 border-dashed border-white/5 rounded-3xl flex flex-col items-center justify-center p-12 text-slate-600 hover:text-emerald-500 hover:border-emerald-500/20 transition-all group"
        >
          <Plus size={32} className="mb-2 opacity-20 group-hover:opacity-100 transition-all" />
          <span className="text-[10px] font-bold uppercase tracking-widest">Register New Smartpole Node</span>
        </button>
      ) : (
        <div className="border-2 border-dashed border-white/5 rounded-3xl flex flex-col items-center justify-center p-12 text-slate-700 opacity-50">
          <Lock size={28} className="mb-2" />
          <span className="text-[10px] font-bold uppercase tracking-widest">Camera management restricted</span>
        </div>
      )}
    </div>
  );
}