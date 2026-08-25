"use client";

import React, { useState } from 'react';
import {
  MapPin, X, CheckCircle2, Calendar,
  ListFilter, ArrowUpDown, FileText, Search
} from 'lucide-react';
import { useLiveChannel } from '../../context/WebSocketContext';
import { useRuntimeConfig } from '../../hooks/useRuntimeConfig';
import { SkeletonRow } from './Skeleton';

function authHeaders() {
  const token = typeof window !== "undefined" ? localStorage.getItem("ecoToken") : null;
  return { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) };
}

export default function HistoryView() {
  const { apiUrl: API_URL } = useRuntimeConfig();
  const [historyRecords, setHistoryRecords] = useState<any[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState('');

  const [dateFilter, setDateFilter] = useState("");
  const [typeFilter, setTypeFilter] = useState("ALL");
  const [searchQuery, setSearchQuery] = useState("");
  const [sortDescending, setSortDescending] = useState(true);

  const loadLogs = async () => {
    try {
      // This request had no Authorization header at all -- require_auth()
      // 401s it every time, res.ok is false, and the catch block never
      // runs (a 401 is still a normal HTTP response, not a fetch error),
      // so this silently fell through to "No incidents match the current
      // filters" forever, indistinguishable from a genuinely empty log.
      const res = await fetch(`${API_URL}/api/incidents`, { headers: authHeaders() });
      if (res.ok) {
        const data = await res.json();
        // schema_final.sql: incidents.status is 'Active' | 'Confirmed' | 'Dismissed'
        const processedHistory = data.filter((inc: any) => inc.status !== 'Active');
        setHistoryRecords(processedHistory);
        setError('');
      } else if (res.status === 401) {
        setError('Session expired -- please log in again.');
      } else {
        const body = await res.json().catch(() => ({}));
        setError(body.detail || `Failed to load incident log (server said ${res.status}).`);
      }
    } catch (err) {
      console.error("Could not sync database rows:", err);
      setError('Backend connection failure.');
    } finally {
      setIsLoading(false);
    }
  };

  // Was setInterval(loadLogs, 5000) -- now refetches instantly on any
  // relevant WebSocket broadcast, with a slow 60s fallback poll as a
  // safety net rather than the primary mechanism.
  useLiveChannel("incidents", loadLogs);

  const processedData = historyRecords
    .filter(record => {
      if (dateFilter && record.occurred_date !== dateFilter) return false;
      if (typeFilter !== 'ALL' && record.type !== typeFilter) return false;
      if (searchQuery) {
        const q = searchQuery.toLowerCase();
        const narrative = (record.narrative || "").toLowerCase();
        const caseId = (record.case_id || "").toLowerCase();
        if (!narrative.includes(q) && !caseId.includes(q)) return false;
      }
      return true;
    })
    .sort((a, b) => {
      const timeA = new Date(`${a.occurred_date}T${a.occurred_time}`).getTime();
      const timeB = new Date(`${b.occurred_date}T${b.occurred_time}`).getTime();
      return sortDescending ? timeB - timeA : timeA - timeB;
    });

  const inputStyle = { background: 'var(--bg)', borderColor: 'var(--line)' };

  return (
    <div className="border flex flex-col h-full min-h-[420px]" style={{ background: 'var(--panel)', borderColor: 'var(--line)' }}>

      {/* Header + filter bar */}
      <div className="shrink-0 border-b" style={{ borderColor: 'var(--line)' }}>
        <div className="h-9 flex items-center justify-between px-2.5 border-b" style={{ borderColor: 'var(--line)' }}>
          <div className="flex items-baseline gap-2.5">
            <span className="label" style={{ color: 'var(--text)' }}>Incident Log</span>
            <span className="text-[10px]" style={{ color: 'var(--text-3)' }}>
              Permanent record — retained even when removed from the map
            </span>
          </div>
          <span className="data text-[10px] px-1.5 py-0.5 border" style={{ color: 'var(--text-2)', borderColor: 'var(--line-2)' }}>
            {String(processedData.length).padStart(3, '0')} RECORDS
          </span>
        </div>

        {error && (
          <div
            className="px-2.5 py-1.5 text-[10px] font-bold uppercase tracking-wider border-b"
            style={{ background: 'rgba(229,52,47,0.08)', borderColor: 'var(--critical)', color: 'var(--critical)' }}
          >
            {error}
          </div>
        )}

        <div className="flex flex-wrap items-center gap-1.5 p-2">
          <div className="relative flex-1 min-w-[180px]">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2" size={12} style={{ color: 'var(--text-3)' }} />
            <input
              title="Search narrative or case ID"
              placeholder="Search narrative or case ID…"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="data w-full pl-7 pr-2 py-1.5 text-[11px] text-[var(--text)] border outline-none"
              style={inputStyle}
            />
          </div>

          <div className="flex items-center gap-1.5 px-2 py-1.5 border" style={inputStyle}>
            <Calendar size={12} style={{ color: 'var(--text-3)' }} />
            <input
              type="date"
              title="Filter by date"
              value={dateFilter}
              onChange={(e) => setDateFilter(e.target.value)}
              className="data bg-transparent text-[11px] outline-none cursor-pointer"
              style={{ color: 'var(--text-2)' }}
            />
          </div>

          <div className="flex items-center gap-1.5 px-2 py-1.5 border" style={inputStyle}>
            <ListFilter size={12} style={{ color: 'var(--text-3)' }} />
            <select
              title="Filter by incident type"
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value)}
              className="data bg-transparent text-[11px] outline-none cursor-pointer"
              style={{ color: 'var(--text-2)' }}
            >
              <option value="ALL">All types</option>
              <option value="ASSAULT">Assault</option>
              <option value="ARMED THREAT">Armed threat</option>
              <option value="ROBBERY">Robbery</option>
              <option value="VANDALISM">Vandalism</option>
              <option value="MANUAL_PANIC">Panic trigger</option>
            </select>
          </div>

          <button
            title="Toggle sort order"
            onClick={() => setSortDescending(!sortDescending)}
            className="flex items-center gap-1.5 px-2 py-1.5 border text-[10px] font-bold uppercase tracking-wider transition-colors hover:bg-white/5"
            style={{ borderColor: 'var(--line)', color: 'var(--text-2)' }}
          >
            <ArrowUpDown size={12} /> {sortDescending ? "Newest" : "Oldest"}
          </button>
        </div>
      </div>

      {/* Column headers -- a records archive is tabular data; a table lets an
          operator scan down one column (time, type, status) instead of
          re-reading every card. */}
      {!isLoading && processedData.length > 0 && (
        <div
          className="shrink-0 grid grid-cols-[110px_1fr_150px_92px_104px] gap-2 px-2.5 py-1.5 border-b"
          style={{ borderColor: 'var(--line)', background: 'var(--bg)' }}
        >
          <span className="label">Case ID</span>
          <span className="label">Incident</span>
          <span className="label">Location</span>
          <span className="label">Confidence</span>
          <span className="label text-right">Disposition</span>
        </div>
      )}

      <div className="flex-1 overflow-y-auto custom-scrollbar">
        {isLoading ? (
          <div className="p-2 space-y-2">
            {Array.from({ length: 5 }).map((_, i) => <SkeletonRow key={i} />)}
          </div>
        ) : processedData.length === 0 ? (
          <div className="h-48 flex flex-col items-center justify-center gap-2">
            <FileText size={22} style={{ color: 'var(--text-3)' }} />
            <span className="label">No incidents match the current filters</span>
          </div>
        ) : (
          processedData.map((record) => {
            const confirmed = record.status === 'Confirmed';
            return (
              <div
                key={record.id}
                className="grid grid-cols-[110px_1fr_150px_92px_104px] gap-2 px-2.5 py-2 border-b items-start transition-colors hover:bg-white/[0.02]"
                style={{ borderColor: 'var(--line)' }}
              >
                <span className="data text-[10px] pt-px" style={{ color: 'var(--text-2)' }}>
                  {record.case_id}
                </span>

                <div className="min-w-0">
                  <div className="text-[12px] font-bold text-[var(--text)] tracking-wide">{record.type}</div>
                  <p className="text-[10px] leading-snug mt-0.5 line-clamp-2" style={{ color: 'var(--text-2)' }}>
                    {record.narrative || "No narrative on file."}
                  </p>
                  <div className="data text-[9px] mt-1" style={{ color: 'var(--text-3)' }}>
                    {record.occurred_date} · {record.occurred_time}
                  </div>
                </div>

                <div className="flex items-start gap-1 min-w-0">
                  <MapPin size={10} className="shrink-0 mt-0.5" style={{ color: 'var(--text-3)' }} />
                  <span className="text-[10px] leading-snug" style={{ color: 'var(--text-2)' }}>
                    {record.location_name}
                  </span>
                </div>

                <div className="data text-[11px] pt-px" style={{ color: 'var(--text-2)' }}>
                  {record.confidence != null ? `${(record.confidence * 100).toFixed(1)}%` : '—'}
                </div>

                <div className="flex justify-end">
                  <span
                    className="inline-flex items-center gap-1 px-1.5 py-0.5 border text-[9px] font-bold uppercase tracking-wider"
                    style={
                      confirmed
                        ? { color: 'var(--ok)', borderColor: 'var(--ok)' }
                        : { color: 'var(--text-3)', borderColor: 'var(--line-2)' }
                    }
                  >
                    {confirmed ? <CheckCircle2 size={10} /> : <X size={10} />}
                    {confirmed ? 'Confirmed' : 'Dismissed'}
                  </span>
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
