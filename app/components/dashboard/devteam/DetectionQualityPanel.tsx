"use client";

import { useEffect, useState } from 'react';
import { Crosshair, RotateCw } from 'lucide-react';
import { authHeaders } from './shared';

type Row = {
  camera_id: string | null; camera_name: string | null; event: string;
  alerts: number; confirmed: number; dismissed: number; pending: number; retyped: number;
  precision: number | null; alerts_per_day: number;
  avg_conf_confirmed: number | null; avg_conf_dismissed: number | null;
};
type Quality = { days: number; rows: Row[]; labelled_examples: number; not_yet_exported: number };

// Below this many decided alerts a precision figure is mostly noise.
const MIN_DECIDED = 10;

// How each camera's detectors are doing on the cameras themselves, judged by
// operators' own Confirm/Dismiss decisions -- the live counterpart to the
// short outside-camera measurements the models were chosen on.
export default function DetectionQualityPanel({ apiUrl }: { apiUrl: string }) {
  const [days, setDays] = useState(30);
  const [data, setData] = useState<Quality | null>(null);
  const [error, setError] = useState('');

  const load = async (d = days) => {
    setError('');
    try {
      const res = await fetch(`${apiUrl}/api/devteam/detection_quality?days=${d}`, { headers: authHeaders() });
      if (res.ok) setData(await res.json());
      else setError('Could not load detection quality.');
    } catch {
      setError('Backend connection failure.');
    }
  };
  useEffect(() => { load(days); }, [days]); // eslint-disable-line react-hooks/exhaustive-deps

  const pct = (v: number | null) => (v === null ? '—' : `${Math.round(v * 100)}%`);

  return (
    <div className="border border-[var(--line)]">
      <div className="flex items-center gap-3 px-4 py-3 border-b border-[var(--line)] bg-[var(--accent)]/[0.03]">
        <Crosshair size={13} className="text-[var(--accent)]" />
        <div className="flex-1 min-w-0">
          <p className="text-[11px] tracking-[0.15em] uppercase text-[var(--text)]">Detection quality — your cameras</p>
          <p className="text-[9.5px] text-[var(--text-3)] mt-0.5">
            Judged by operators&apos; Confirm / Dismiss decisions. Each decision is also saved as a training example.
          </p>
        </div>
        <select value={days} onChange={e => setDays(Number(e.target.value))}
          className="bg-[var(--bg)] border border-[var(--line)] text-[10px] text-[var(--text-2)] px-1.5 py-1 outline-none">
          {[7, 30, 90, 365].map(d => <option key={d} value={d}>last {d} days</option>)}
        </select>
        <button onClick={() => load()} title="Refresh" className="text-[var(--text-2)] hover:text-[var(--text)]"><RotateCw size={12} /></button>
      </div>

      {error ? (
        <p className="px-4 py-3 text-[10px] text-[var(--critical)]">{error}</p>
      ) : !data ? (
        <p className="px-4 py-3 text-[10px] text-[var(--text-3)]">Loading…</p>
      ) : data.rows.length === 0 ? (
        <p className="px-4 py-4 text-[10px] text-[var(--text-3)]">No AI alerts in this period yet.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-[10px]">
            <thead>
              <tr className="text-[8px] tracking-[0.12em] uppercase text-[var(--text-3)] border-b border-[var(--panel-2)]">
                {['Camera', 'Alert type', 'Alerts', 'Per day', 'Confirmed', 'Dismissed', 'Undecided', 'Precision', 'Avg conf ✓ / ✗', 'Re-typed'].map(h => (
                  <th key={h} className="text-left font-normal px-3 py-2 whitespace-nowrap">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--panel-2)]">
              {data.rows.map(r => {
                const decided = r.confirmed + r.dismissed;
                const weak = decided >= MIN_DECIDED && r.precision !== null && r.precision < 0.5;
                return (
                  <tr key={`${r.camera_id}-${r.event}`}>
                    <td className="px-3 py-2 text-[var(--text)] whitespace-nowrap">{r.camera_name || r.camera_id || 'unknown camera'}</td>
                    <td className="px-3 py-2 text-[var(--text-2)] whitespace-nowrap">{r.event}</td>
                    <td className="px-3 py-2 text-[var(--text)]">{r.alerts}</td>
                    <td className="px-3 py-2 text-[var(--text-2)]">{r.alerts_per_day}</td>
                    <td className="px-3 py-2 text-[var(--ok)]">{r.confirmed}</td>
                    <td className="px-3 py-2 text-[var(--critical)]">{r.dismissed}</td>
                    <td className="px-3 py-2 text-[var(--text-3)]">{r.pending}</td>
                    <td className={`px-3 py-2 font-bold ${weak ? 'text-[var(--warn)]' : 'text-[var(--text)]'}`}
                      title={decided < MIN_DECIDED ? `Only ${decided} decided -- too few to trust yet` : undefined}>
                      {pct(r.precision)}{decided < MIN_DECIDED && r.precision !== null && <span className="text-[var(--text-3)] font-normal"> (n={decided})</span>}
                    </td>
                    <td className="px-3 py-2 text-[var(--text-2)] whitespace-nowrap">
                      {r.avg_conf_confirmed?.toFixed(2) ?? '—'} / {r.avg_conf_dismissed?.toFixed(2) ?? '—'}
                    </td>
                    <td className="px-3 py-2 text-[var(--text-2)]">{r.retyped || ''}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {data && (
        <div className="px-4 py-3 border-t border-[var(--panel-2)] text-[9.5px] leading-relaxed text-[var(--text-3)] space-y-1">
          <p>
            <span className="text-[var(--text-2)]">{data.labelled_examples}</span> labelled examples collected,{' '}
            <span className="text-[var(--text-2)]">{data.not_yet_exported}</span> not yet exported. Export them with{' '}
            <code className="text-[var(--accent)]">python-env\python.exe tools\export_feedback_dataset.py</code>.
          </p>
          <p>
            Precision in amber = under 50% with at least {MIN_DECIDED} decisions: most alerts from that camera/type are false.
            When the dismissed alerts&apos; average confidence sits well below the confirmed ones&apos;, raising that camera&apos;s
            per-camera threshold for that alert type will cut false alarms without losing many real ones.
          </p>
        </div>
      )}
    </div>
  );
}
