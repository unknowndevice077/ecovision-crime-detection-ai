"use client";
import React, { useState } from 'react';
import { X, Shield, Loader2, Video } from 'lucide-react';
import { Camera } from '../../types';
import { useRuntimeConfig } from '../../hooks/useRuntimeConfig';

interface ModalProps {
  isFullscreenGrid: boolean;
  setIsFullscreenGrid: (val: boolean) => void;
  showModal: boolean;
  setShowModal: (val: boolean) => void;
  cameras: Camera[];
  handleUpsertNode: (name: string, url: string) => void;
}

// CCTV video-wall convention: operators expect a square-ish matrix, so step
// 1 -> 4 -> 9 -> 16 rather than growing one axis. Mirrors gridColsFor() in
// page.tsx so the fullscreen wall matches the docked wall's layout rules.
function gridColsFor(n: number) {
  if (n <= 1) return 'grid-cols-1';
  if (n <= 4) return 'grid-cols-2';
  if (n <= 9) return 'grid-cols-3';
  return 'grid-cols-4';
}

export default function SharedModals({ isFullscreenGrid, setIsFullscreenGrid, showModal, setShowModal, cameras, handleUpsertNode }: ModalProps) {
  const { aiUrl } = useRuntimeConfig();
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [formError, setFormError] = useState('');

  const handleSubmit = async () => {
    const nameInput = document.getElementById('cam-name') as HTMLInputElement;
    const urlInput = document.getElementById('cam-url') as HTMLInputElement;
    const n = nameInput?.value.trim();
    const u = urlInput?.value.trim();
    if (!n || !u) {
      setFormError('Both a camera name and a stream path are required.');
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

  const inputStyle = { background: 'var(--bg)', borderColor: 'var(--line)' };

  return (
    <>
      {isFullscreenGrid && (
        <div className="fixed inset-0 z-[100] flex flex-col" style={{ background: 'var(--bg)' }}>
          <div
            className="h-11 shrink-0 flex justify-between items-center px-3 border-b"
            style={{ background: 'var(--panel)', borderColor: 'var(--line)' }}
          >
            <div className="flex items-center gap-2.5">
              <Shield size={16} style={{ color: 'var(--accent)' }} />
              <span className="text-[12px] font-bold tracking-wide text-[var(--text)] uppercase">Video Wall</span>
              <span className="data text-[10px] px-1.5 py-0.5 border" style={{ color: 'var(--text-2)', borderColor: 'var(--line-2)' }}>
                {String(cameras.length).padStart(2, '0')} FEEDS
              </span>
            </div>
            <button
              title="Exit video wall (Esc)"
              aria-label="Exit video wall"
              onClick={() => setIsFullscreenGrid(false)}
              className="flex items-center gap-1.5 px-2.5 py-1.5 border text-[10px] font-bold uppercase tracking-wider transition-colors hover:bg-white/5"
              style={{ borderColor: 'var(--line-2)', color: 'var(--text-2)' }}
            >
              <X size={13} /> Exit
            </button>
          </div>

          {cameras.length === 0 ? (
            <div className="flex-1 flex flex-col items-center justify-center gap-2">
              <Video size={26} style={{ color: 'var(--text-3)' }} />
              <span className="label">No cameras registered</span>
            </div>
          ) : (
            <div className={`grid gap-px flex-1 p-px ${gridColsFor(cameras.length)}`} style={{ background: 'var(--line)' }}>
              {cameras.map(cam => (
                <div key={cam.id} className="relative overflow-hidden" style={{ background: '#000' }}>
                  <img src={`${aiUrl}/video_feed`} className="w-full h-full object-cover" alt={cam.name} />
                  {/* Burned-in OSD, the way a real CCTV recorder overlays it */}
                  <div className="osd absolute top-1.5 left-2 flex items-center gap-1.5">
                    <span className="status-dot" style={{ background: 'var(--critical)' }} />
                    <span>{cam.name.toUpperCase()}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {showModal && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center p-6" style={{ background: 'rgba(0,0,0,0.72)' }}>
          <div className="border w-full max-w-md" style={{ background: 'var(--panel)', borderColor: 'var(--line-2)' }}>
            <div className="h-9 flex justify-between items-center px-3 border-b" style={{ borderColor: 'var(--line)' }}>
              <span className="label" style={{ color: 'var(--text)' }}>Register Camera</span>
              <button
                title="Cancel"
                aria-label="Cancel camera registration"
                onClick={() => setShowModal(false)}
                style={{ color: 'var(--text-3)' }}
                className="transition-colors hover:text-[var(--text)]"
              >
                <X size={15} />
              </button>
            </div>

            <div className="p-4 space-y-3">
              <div>
                <label htmlFor="cam-name" className="label block mb-1.5">Camera name</label>
                <input
                  id="cam-name"
                  className="data w-full border p-2.5 text-[12px] text-[var(--text)] outline-none focus:border-[var(--accent)] transition-colors"
                  style={inputStyle}
                  placeholder="e.g. Main Gate — North"
                  disabled={isSubmitting}
                />
              </div>
              <div>
                <label htmlFor="cam-url" className="label block mb-1.5">Stream path (RTSP)</label>
                <input
                  id="cam-url"
                  className="data w-full border p-2.5 text-[12px] text-[var(--text)] outline-none focus:border-[var(--accent)] transition-colors"
                  style={inputStyle}
                  placeholder="rtsp://…"
                  disabled={isSubmitting}
                />
              </div>

              {formError && (
                <p className="text-[10px] font-bold uppercase tracking-wider" style={{ color: 'var(--critical)' }}>
                  {formError}
                </p>
              )}

              <button
                onClick={handleSubmit}
                disabled={isSubmitting}
                className="w-full py-2.5 text-[11px] font-bold uppercase tracking-wider text-white transition-opacity hover:opacity-90 disabled:opacity-50 flex items-center justify-center gap-2"
                style={{ background: 'var(--accent)' }}
              >
                {isSubmitting ? <><Loader2 size={13} className="animate-spin" /> Registering…</> : 'Register Camera'}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
