"use client";
import React, { useState } from 'react';
import { X, Shield, Loader2 } from 'lucide-react';
import { Camera } from '../../types';

interface ModalProps {
  isFullscreenGrid: boolean;
  setIsFullscreenGrid: (val: boolean) => void;
  showModal: boolean;
  setShowModal: (val: boolean) => void;
  cameras: Camera[];
  handleUpsertNode: (name: string, url: string) => void;
}

export default function SharedModals({ isFullscreenGrid, setIsFullscreenGrid, showModal, setShowModal, cameras, handleUpsertNode }: ModalProps) {
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [formError, setFormError] = useState('');

  const handleSubmit = async () => {
    const nameInput = document.getElementById('cam-name') as HTMLInputElement;
    const urlInput = document.getElementById('cam-url') as HTMLInputElement;
    const n = nameInput?.value.trim();
    const u = urlInput?.value.trim();
    if (!n || !u) {
      setFormError('Both a descriptor and network path are required.');
      return;
    }
    setFormError('');
    setIsSubmitting(true);
    try {
      await handleUpsertNode(n, u);
      nameInput.value = '';
      urlInput.value = '';
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <>
      {isFullscreenGrid && (
        <div className="fixed inset-0 z-[100] bg-black/98 backdrop-blur-2xl p-8 flex flex-col animate-in fade-in duration-500 text-slate-200">
          <div className="flex justify-between items-center mb-8 pb-6 border-b border-white/5">
            <div className="flex items-center gap-4">
              <div className="p-3 bg-red-600 rounded-xl animate-pulse shadow-[0_0_15px_rgba(220,38,38,0.3)]"><Shield size={24} className="text-white" /></div>
              <h2 className="text-xl font-semibold uppercase tracking-tight text-white">Neural Surveillance Command</h2>
            </div>
            <button 
              title="Close Fullscreen Grid" 
              aria-label="Close Fullscreen Grid"
              onClick={() => setIsFullscreenGrid(false)} 
              className="p-3 bg-white/5 hover:bg-white/10 rounded-full text-slate-400 hover:text-white transition-all shadow-lg"
            >
              <X size={28} />
            </button>
          </div>
          {cameras.length === 0 ? (
            <div className="flex-1 flex flex-col items-center justify-center opacity-30 gap-3">
              <Shield size={48} />
              <span className="text-xs font-mono uppercase tracking-widest">No camera nodes registered</span>
            </div>
          ) : (
            <div className={`grid gap-4 flex-1 ${cameras.length <= 4 ? 'grid-cols-2' : 'grid-cols-3'}`}>
              {cameras.map(cam => (
                <div key={cam.id} className="relative rounded-[2rem] border border-white/10 overflow-hidden group shadow-2xl bg-[#0d0f14]">
                  <img src="http://localhost:8001/video_feed" className="w-full h-full object-cover grayscale-[0.4] group-hover:grayscale-0 transition-all duration-700" alt="Tactical" />
                  <div className="absolute top-6 left-6 px-4 py-2 bg-black/60 backdrop-blur-md rounded-xl text-[9px] font-bold uppercase border border-white/5 shadow-md tabular-nums">{cam.name}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {showModal && (
        <div className="fixed inset-0 bg-black/90 backdrop-blur-md z-[60] flex items-center justify-center p-6 text-slate-200">
          <div className="bg-[#1a1e26] border border-white/5 rounded-[3rem] p-10 w-full max-w-md shadow-2xl">
            <div className="flex justify-between items-center mb-10 text-white">
              <h2 className="text-sm font-bold uppercase tracking-widest">Initialize Node</h2>
              <button 
                title="Cancel Node Registration" 
                aria-label="Cancel Node Registration"
                onClick={() => setShowModal(false)}
              >
                <X />
              </button>
            </div>
            <div className="space-y-6">
              <input id="cam-name" className="w-full bg-black/40 border border-white/5 rounded-2xl p-4 text-[12px] text-white outline-none focus:border-emerald-500 font-mono transition-all" placeholder="Node Descriptor" disabled={isSubmitting} />
              <input id="cam-url" className="w-full bg-black/40 border border-white/5 rounded-2xl p-4 text-[12px] text-white outline-none focus:border-emerald-500 font-mono transition-all" placeholder="Network Path (RTSP)" disabled={isSubmitting} />
              {formError && <p className="text-red-500 text-[9px] text-center uppercase font-bold">{formError}</p>}
              <button
                onClick={handleSubmit}
                disabled={isSubmitting}
                className="w-full bg-emerald-500 text-black py-4 rounded-2xl font-bold uppercase active:scale-95 transition-all shadow-lg hover:bg-emerald-400 disabled:opacity-50 disabled:active:scale-100 flex items-center justify-center gap-2"
              >
                {isSubmitting ? <><Loader2 size={14} className="animate-spin" /> Establishing...</> : 'Establish Link'}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}