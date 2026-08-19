"use client";

import React, { useState, useRef } from 'react';
import {
  ShieldAlert, Clapperboard, Edit3, Save, Play,
  ListFilter, Calendar, Clock, Scissors, AlertCircle, Download, Trash2
} from 'lucide-react';
import { useLiveChannel } from '../context/WebSocketContext';
import { useRuntimeConfig } from '../hooks/useRuntimeConfig';
import { SkeletonRow } from './dashboard/Skeleton';

function authHeaders() {
  const token = typeof window !== "undefined" ? localStorage.getItem("ecoToken") : null;
  return { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) };
}

// Matches backend.py's _row_to_record_dict() -- snake_case, 1:1 with the
// video_records table columns. type is CLIP | FULL_24_7 | CRIME_CLIP per
// schema_final.sql's CHECK constraint.
type VideoRecord = {
  id: string;
  filename: string;
  file_path: string;
  recorded_at: string;
  duration: string;
  type: 'CLIP' | 'FULL_24_7' | 'CRIME_CLIP';
  associated_incident_id?: string | null;
  crime_time_marker?: string;
  notes: string;
};

export default function RecordsView() {
  const { apiUrl: API_URL } = useRuntimeConfig();
  const [records, setRecords] = useState<VideoRecord[]>([]);
  const [crimes, setCrimes] = useState<any[]>([]);
  const [subView, setSubView] = useState<'CLIPS' | 'DVR'>('CLIPS');
  const [activePlayback, setActivePlayback] = useState<VideoRecord | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editNotes, setEditNotes] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState('');

  const [filterDate, setFilterDate] = useState("");
  const [filterCrimeType, setFilterCrimeType] = useState("ALL");
  const [startTime, setStartTime] = useState("00:00");
  const [endTime, setEndTime] = useState("01:00");

  const videoRef = useRef<HTMLVideoElement | null>(null);

  const fetchRecordsAndCrimes = async () => {
    try {
      const [recordsRes, crimesRes] = await Promise.all([
        fetch(`${API_URL}/api/records`, { headers: authHeaders() }),
        fetch(`${API_URL}/api/incidents`, { headers: authHeaders() }),
      ]);
      if (recordsRes.ok && crimesRes.ok) {
        setRecords(await recordsRes.json());
        setCrimes(await crimesRes.json());
        setError('');
      } else if (recordsRes.status === 401 || crimesRes.status === 401) {
        setError('Session expired -- please log in again.');
      } else {
        // A flat "Failed to load records" used to cover both a genuine
        // outage AND a 403 from a standard account that was never granted
        // view_records/view_history -- indistinguishable to whoever hit it.
        // The 403's own detail already names the missing permission key
        // (require_permission's HTTPException), so surface it verbatim
        // instead of a generic message that gives no next step.
        const failed = !recordsRes.ok ? recordsRes : crimesRes;
        const body = await failed.json().catch(() => ({}));
        if (failed.status === 403) {
          setError(body.detail ? `${body.detail} -- ask a DevTeam admin to grant it.` : 'Missing permission for this view.');
        } else {
          setError(`Failed to load records (server said ${failed.status}).`);
        }
      }
    } catch (e) {
      console.error("Failed to query stream archive indices:", e);
      setError('Backend connection failure.');
    } finally {
      setIsLoading(false);
    }
  };

  // Was setInterval(fetchRecordsAndCrimes, 5000) -- now refetches on any
  // relevant WebSocket broadcast (new clips, incident status changes),
  // with a slow 60s fallback poll as a safety net.
  useLiveChannel("*", fetchRecordsAndCrimes);

  const handleUpdateNotes = async (id: string) => {
    try {
      const res = await fetch(`${API_URL}/api/records/${id}/notes`, {
        method: "PATCH",
        headers: authHeaders(),
        body: JSON.stringify({ notes: editNotes })
      });
      if (res.ok) {
        setEditingId(null);
        fetchRecordsAndCrimes();
      } else {
        setError('Could not save notes.');
      }
    } catch (e) {
      console.error("Notes field persistence update error:", e);
      setError('Backend connection failure.');
    }
  };

  // BUG FOUND 2026-08-19: this used to POST to /api/records/register_clip with
  // a fabricated filename and NEVER CUT ANY VIDEO -- it created a database row
  // pointing at a file that was never written, so every "extracted" clip
  // appeared in the archive and then failed to play, permanently. It now calls
  // the real trim endpoint, which runs ffmpeg against the source file.
  const [extracting, setExtracting] = useState(false);
  const handleExtractClip = async () => {
    if (!activePlayback) return;
    setExtracting(true);
    setError('');
    try {
      const res = await fetch(`${API_URL}/api/records/${activePlayback.id}/extract`, {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({ start: startTime, end: endTime }),
      });
      const body = await res.json().catch(() => ({}));
      if (res.ok) {
        fetchRecordsAndCrimes();
      } else {
        setError(body.detail || 'Could not extract segment.');
      }
    } catch (e) {
      console.error("Clip extraction request failed:", e);
      setError('Backend connection failure.');
    } finally {
      setExtracting(false);
    }
  };

  const handleDeleteRecord = async (rec: VideoRecord) => {
    if (!window.confirm(`Delete "${rec.filename}"?\n\nThis removes the video file from disk as well. It cannot be undone.`)) return;
    try {
      const res = await fetch(`${API_URL}/api/records/${rec.id}`, {
        method: "DELETE",
        headers: authHeaders(),
      });
      const body = await res.json().catch(() => ({}));
      if (res.ok) {
        if (activePlayback?.id === rec.id) setActivePlayback(null);
        // Surface the partial case honestly: the row is gone but the file
        // survived (locked by another process, permissions), rather than
        // reporting a clean success.
        if (body.file_removed === false) setError('Record removed, but the video file could not be deleted from disk.');
        fetchRecordsAndCrimes();
      } else {
        setError(body.detail || 'Could not delete recording.');
      }
    } catch (e) {
      console.error("Record delete failed:", e);
      setError('Backend connection failure.');
    }
  };

  const filteredRecords = records.filter(r => {
    if (r.type !== (subView === 'CLIPS' ? 'CLIP' : 'FULL_24_7')) return false;
    if (filterDate && !r.recorded_at.includes(filterDate)) return false;
    if (subView === 'CLIPS' && filterCrimeType !== 'ALL' && !r.filename.toUpperCase().includes(filterCrimeType)) return false;
    return true;
  });

  const inputStyle = { background: 'var(--bg)', borderColor: 'var(--line)' };

  const tabStyle = (on: boolean) =>
    on
      ? { background: 'var(--accent)', color: '#fff', borderColor: 'var(--accent)' }
      : { background: 'transparent', color: 'var(--text-2)', borderColor: 'var(--line-2)' };

  return (
    <div className="flex flex-col gap-2 h-full w-full min-h-0">

      {/* ═══ FILTER BAR ═══════════════════════════════════════════════════ */}
      <div
        className="border flex flex-wrap items-center justify-between gap-2 p-2 shrink-0"
        style={{ background: 'var(--panel)', borderColor: 'var(--line)' }}
      >
        <div className="flex items-center gap-1.5 flex-wrap">
          <div className="flex items-center gap-1.5 px-2 py-1.5 border" style={inputStyle}>
            <Calendar size={12} style={{ color: 'var(--text-3)' }} />
            <input
              type="text"
              title="Filter records by date"
              placeholder="YYYY-MM-DD"
              value={filterDate}
              onChange={(e) => setFilterDate(e.target.value)}
              className="data bg-transparent text-[11px] text-white outline-none w-[92px]"
            />
          </div>

          {subView === 'CLIPS' && (
            <div className="flex items-center gap-1.5 px-2 py-1.5 border" style={inputStyle}>
              <ListFilter size={12} style={{ color: 'var(--text-3)' }} />
              <select
                title="Filter by incident type"
                value={filterCrimeType}
                onChange={(e) => setFilterCrimeType(e.target.value)}
                className="data bg-transparent text-[11px] outline-none cursor-pointer"
                style={{ color: 'var(--text-2)' }}
              >
                <option value="ALL">All types</option>
                <option value="ASSAULT">Assault</option>
                <option value="FIREARM">Weapons / firearms</option>
                <option value="PANIC">Panic triggers</option>
              </select>
            </div>
          )}
        </div>

        <div className="flex gap-px">
          <button
            onClick={() => { setSubView('CLIPS'); setActivePlayback(null); }}
            className="px-3 py-1.5 border text-[10px] font-bold uppercase tracking-wider transition-colors"
            style={tabStyle(subView === 'CLIPS')}
          >
            Event Clips
          </button>
          <button
            onClick={() => { setSubView('DVR'); setActivePlayback(null); }}
            className="px-3 py-1.5 border text-[10px] font-bold uppercase tracking-wider transition-colors"
            style={tabStyle(subView === 'DVR')}
          >
            24/7 Archive
          </button>
        </div>
      </div>

      {error && (
        <div
          className="px-2.5 py-1.5 border text-[10px] font-bold uppercase tracking-wider shrink-0"
          style={{ background: 'rgba(229,52,47,0.08)', borderColor: 'var(--critical)', color: 'var(--critical)' }}
        >
          {error}
        </div>
      )}

      {/* ═══ PLAYBACK + ARCHIVE LIST ══════════════════════════════════════ */}
      <div className="flex-1 grid grid-cols-12 gap-2 min-h-0">

        {/* Playback viewport */}
        <div className="col-span-7 flex flex-col min-h-0 border" style={{ background: 'var(--panel)', borderColor: 'var(--line)' }}>
          <div className="h-9 shrink-0 flex items-center justify-between px-2.5 border-b" style={{ borderColor: 'var(--line)' }}>
            <span className="label" style={{ color: 'var(--text)' }}>Playback</span>
            {activePlayback && (
              <span className="data text-[10px] truncate max-w-[60%]" style={{ color: 'var(--text-2)' }}>
                {activePlayback.filename}
              </span>
            )}
          </div>

          {activePlayback ? (
            <div className="flex-1 flex flex-col min-h-0 p-2 gap-2">
              <video
                ref={videoRef}
                controls
                autoPlay
                className="w-full aspect-video min-h-0"
                style={{ background: '#000' }}
                src={`${API_URL}/static/recordings/${activePlayback.filename}`}
              />

              {/* Timeline scrub bar with threat markers */}
              <div
                className="w-full h-7 relative overflow-hidden flex items-center px-2 border shrink-0"
                style={{ background: 'var(--bg)', borderColor: 'var(--line)' }}
              >
                {subView === 'CLIPS' && activePlayback.crime_time_marker && (
                  <div
                    className="absolute left-1/2 -translate-x-1/2 inset-y-0 px-3 flex items-center data text-[9px] font-bold text-white"
                    style={{ background: 'var(--critical)' }}
                  >
                    <ShieldAlert size={11} className="mr-1.5" /> DETECTED {activePlayback.crime_time_marker}
                  </div>
                )}
                {subView === 'DVR' && crimes
                  .filter(c => c.occurred_date === activePlayback.recorded_at.split(' ')[0])
                  .map((crime, idx) => (
                    // Inline left offset -- the old version emitted a fresh
                    // <style> tag per marker on every render just to set one
                    // percentage, which piled up style nodes during playback.
                    <div
                      key={idx}
                      className="absolute inset-y-0 px-1.5 flex items-center data text-[8px] text-white z-10 cursor-help"
                      style={{ left: `${Math.min(84, 15 + idx * 22)}%`, background: 'var(--critical)' }}
                      title={`[${crime.type}] flagged at ${crime.occurred_time}`}
                    >
                      {crime.occurred_time}
                    </div>
                  ))}
              </div>

              {/* Extract range */}
              <div
                className="flex items-end justify-between gap-3 p-2 border shrink-0"
                style={{ background: 'var(--panel-2)', borderColor: 'var(--line)' }}
              >
                <div className="flex items-end gap-3">
                  <div>
                    <span className="label flex items-center gap-1 mb-1"><Clock size={10} /> Start</span>
                    <input
                      type="text"
                      title="Segment start time"
                      value={startTime}
                      onChange={(e) => setStartTime(e.target.value)}
                      className="data border px-2 py-1 text-[11px] text-white outline-none focus:border-[var(--accent)] w-[68px] text-center"
                      style={inputStyle}
                    />
                  </div>
                  <div>
                    <span className="label flex items-center gap-1 mb-1"><Clock size={10} /> End</span>
                    <input
                      type="text"
                      title="Segment end time"
                      value={endTime}
                      onChange={(e) => setEndTime(e.target.value)}
                      className="data border px-2 py-1 text-[11px] text-white outline-none focus:border-[var(--accent)] w-[68px] text-center"
                      style={inputStyle}
                    />
                  </div>
                </div>
                <div className="flex items-center gap-1.5">
                  <a
                    href={`${API_URL}/static/recordings/${encodeURIComponent(activePlayback.filename)}`}
                    download={activePlayback.filename}
                    title="Download the full recording"
                    className="px-3 py-1.5 border text-[10px] font-bold uppercase tracking-wider flex items-center gap-1.5 transition-colors hover:bg-white/5"
                    style={{ borderColor: 'var(--line-2)', color: 'var(--text-2)' }}
                  >
                    <Download size={11} /> Download
                  </a>
                  <button
                    title="Cut this time range into a new clip"
                    onClick={handleExtractClip}
                    disabled={extracting}
                    className="px-3 py-1.5 text-[10px] font-bold uppercase tracking-wider text-white flex items-center gap-1.5 transition-opacity hover:opacity-90 disabled:opacity-50"
                    style={{ background: 'var(--accent)' }}
                  >
                    <Scissors size={11} /> {extracting ? 'Cutting…' : 'Extract'}
                  </button>
                </div>
              </div>
            </div>
          ) : (
            <div className="flex-1 flex flex-col items-center justify-center gap-2">
              <Clapperboard size={26} style={{ color: 'var(--text-3)' }} />
              <span className="label">Select a recording to begin playback</span>
            </div>
          )}
        </div>

        {/* Archive list */}
        <div className="col-span-5 flex flex-col min-h-0 border" style={{ background: 'var(--panel)', borderColor: 'var(--line)' }}>
          <div className="h-9 shrink-0 flex items-center justify-between px-2.5 border-b" style={{ borderColor: 'var(--line)' }}>
            <span className="label" style={{ color: 'var(--text)' }}>
              {subView === 'CLIPS' ? 'Event Clips' : '24/7 Archive'}
            </span>
            <span className="data text-[10px] px-1.5 py-0.5 border" style={{ color: 'var(--text-2)', borderColor: 'var(--line-2)' }}>
              {String(filteredRecords.length).padStart(3, '0')}
            </span>
          </div>

          <div className="flex-1 overflow-y-auto custom-scrollbar">
            {isLoading ? (
              <div className="p-2 space-y-2">
                {Array.from({ length: 4 }).map((_, i) => <SkeletonRow key={i} />)}
              </div>
            ) : filteredRecords.length === 0 ? (
              <div className="h-full flex flex-col items-center justify-center gap-2 py-12">
                <AlertCircle size={22} style={{ color: 'var(--text-3)' }} />
                <span className="label">No recordings match the current filters</span>
              </div>
            ) : (
              filteredRecords.map((track) => {
                const selected = activePlayback?.id === track.id;
                return (
                  <div
                    key={track.id}
                    className="border-b p-2.5 transition-colors"
                    style={{
                      borderColor: 'var(--line)',
                      background: selected ? 'rgba(45,111,247,0.10)' : 'transparent',
                      borderLeft: selected ? '3px solid var(--accent)' : '3px solid transparent',
                    }}
                  >
                    <div className="flex justify-between items-start gap-2">
                      <div className="min-w-0">
                        <div className="data text-[11px] font-bold text-white truncate">{track.filename}</div>
                        <div className="data text-[9px] mt-0.5" style={{ color: 'var(--text-3)' }}>
                          {track.recorded_at} · {track.duration}
                        </div>
                      </div>
                      <div className="flex items-center gap-1 shrink-0">
                        <button
                          title="Play this recording"
                          onClick={() => setActivePlayback(track)}
                          className="p-1.5 text-white transition-opacity hover:opacity-90"
                          style={{ background: 'var(--accent)' }}
                        >
                          <Play size={11} />
                        </button>
                        {/* Plain anchor with `download`, not a fetch+blob: the
                            file is already served by the backend's /static
                            mount, so the browser can stream it straight to
                            disk without buffering a whole video in memory. */}
                        <a
                          href={`${API_URL}/static/recordings/${encodeURIComponent(track.filename)}`}
                          download={track.filename}
                          title="Download this recording"
                          className="p-1.5 border transition-colors hover:bg-white/5"
                          style={{ borderColor: 'var(--line-2)', color: 'var(--text-2)' }}
                        >
                          <Download size={11} />
                        </a>
                        <button
                          title="Delete this recording (removes the file from disk)"
                          onClick={() => handleDeleteRecord(track)}
                          className="p-1.5 border transition-colors hover:bg-[rgba(229,52,47,0.12)]"
                          style={{ borderColor: 'var(--line-2)', color: 'var(--critical)' }}
                        >
                          <Trash2 size={11} />
                        </button>
                      </div>
                    </div>

                    <div className="mt-2 p-2 border" style={{ background: 'var(--panel-2)', borderColor: 'var(--line)' }}>
                      {editingId === track.id ? (
                        <div className="flex flex-col gap-1.5">
                          <textarea
                            title="Edit notes"
                            placeholder="Add case notes…"
                            value={editNotes}
                            onChange={(e) => setEditNotes(e.target.value)}
                            className="w-full border text-[11px] text-white p-1.5 outline-none focus:border-[var(--accent)]"
                            style={inputStyle}
                            rows={2}
                          />
                          <div className="flex justify-end gap-1.5">
                            <button
                              onClick={() => setEditingId(null)}
                              className="px-2 py-1 border text-[9px] font-bold uppercase tracking-wider transition-colors hover:bg-white/5"
                              style={{ borderColor: 'var(--line-2)', color: 'var(--text-2)' }}
                            >
                              Cancel
                            </button>
                            <button
                              onClick={() => handleUpdateNotes(track.id)}
                              className="px-2 py-1 text-[9px] font-bold uppercase tracking-wider text-white flex items-center gap-1 transition-opacity hover:opacity-90"
                              style={{ background: 'var(--accent)' }}
                            >
                              <Save size={9} /> Save
                            </button>
                          </div>
                        </div>
                      ) : (
                        <div className="flex justify-between items-start gap-2">
                          <p className="text-[10px] leading-snug" style={{ color: 'var(--text-2)' }}>
                            {track.notes || 'No notes on file.'}
                          </p>
                          <button
                            title="Edit notes"
                            onClick={() => { setEditingId(track.id); setEditNotes(track.notes); }}
                            className="shrink-0 transition-colors hover:text-white"
                            style={{ color: 'var(--text-3)' }}
                          >
                            <Edit3 size={11} />
                          </button>
                        </div>
                      )}
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
