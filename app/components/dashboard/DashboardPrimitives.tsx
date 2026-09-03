"use client";
// app/components/dashboard/DashboardPrimitives.tsx
//
// The presentational pieces of the operator dashboard, moved here verbatim
// from app/page.tsx (which had grown past 1,290 lines, and is where the
// permission-gating bug managed to hide).
//
// Only these components moved: they take props, hold no shared state, and
// touch none of page.tsx's ~40 useState values. That is exactly why they were
// safe to extract. The tab bodies were deliberately LEFT in page.tsx -- they
// reference dozens of local state variables, and threading all of that through
// props days before a defense is how a working dashboard arrives broken.
//
// Behaviour is unchanged. This is a move, not a rewrite.

import React, { useState } from 'react';
import { Video, X } from 'lucide-react';
import { SystemClockText, SystemDateText } from './SystemTime';
import { useRuntimeConfig } from '../../hooks/useRuntimeConfig';

export function gridColsFor(count: number) {
    if (count <= 1) return 'grid-cols-1';
    if (count <= 4) return 'grid-cols-2';
    if (count <= 9) return 'grid-cols-3';
    return 'grid-cols-4';
}

export function tempTone(t: number): 'ok' | 'warn' | 'critical' {
    if (t >= 80) return 'critical';
    if (t >= 65) return 'warn';
    return 'ok';
}

/* ── Camera tile ────────────────────────────────────────────────────────
   Feeds render at FULL opacity at all times -- the previous design dimmed
   them to 60% until hover, which is actively unsafe for a monitoring wall
   (you cannot watch what you cannot see). Identification is burned into the
   frame as OSD text the way real CCTV does, so a screenshot of a tile is
   self-documenting for evidence. */
export function CameraTile({ cam, aiUrl, alerted, onClick, large }: any) {
    const Wrapper: any = onClick ? 'button' : 'div';
    return (
        <Wrapper
            onClick={onClick}
            className={`relative bg-black overflow-hidden group text-left w-full h-full border transition-colors${onClick ? ' hover-lift cursor-pointer' : ''}`}
            style={{ borderColor: alerted ? 'var(--critical)' : 'var(--line)', borderRadius: 'var(--radius-md)' }}
            aria-label={onClick ? `Open ${cam.name} full view` : undefined}
        >
            <img
                src={`${aiUrl}/video_feed`}
                className="w-full h-full object-cover"
                alt={`${cam.name} live feed`}
            />

            {/* Top OSD: identity + live state */}
            <div className="absolute top-0 inset-x-0 flex items-start justify-between p-1.5 pointer-events-none">
                <span className={`osd ${large ? 'text-[12px]' : 'text-[10px]'} font-bold text-white`}>
                    {cam.name?.toUpperCase()}
                </span>
                <span className="flex items-center gap-1">
                    <span className="status-dot live" />
                    <span className={`osd ${large ? 'text-[11px]' : 'text-[9px]'} font-bold text-white`}>LIVE</span>
                </span>
            </div>

            {/* Bottom OSD: burned-in timestamp, as on any evidentiary recording */}
            <div className="absolute bottom-0 inset-x-0 flex items-end justify-between p-1.5 pointer-events-none">
                <span className={`osd ${large ? 'text-[11px]' : 'text-[9px]'} text-white/90`}>
                    <SystemDateText /> <SystemClockText />
                </span>
                {alerted && (
                    <span
                        className="osd text-[9px] font-bold px-1 py-0.5 pulse-alert"
                        style={{ background: 'var(--critical)', color: '#fff' }}
                    >
                        THREAT
                    </span>
                )}
            </div>

            {/* Alert frame -- a hard border, not a soft glow, so it survives being
          seen at an angle or on a cheap monitor */}
            {alerted && (
                <div
                    className="absolute inset-0 border-2 pointer-events-none pulse-alert"
                    style={{ borderColor: 'var(--critical)' }}
                />
            )}
        </Wrapper>
    );
}

/* ── Nav rail ───────────────────────────────────────────────────────────── */
// Section dividers: with role-gated items the rail can show anywhere from 1 to
// 5 entries, and an unlabelled flat list gives an operator no cue that
// "Personnel" is a different kind of thing from "Live Monitor".
export function NavSectionLabel({ children }: { children: React.ReactNode }) {
    return (
        <div className="label px-3 pt-2.5 pb-1.5 first:pt-0.5" style={{ color: 'var(--text-3)' }}>
            {children}
        </div>
    );
}

export function NavItem({ icon, label, badge, badgeTone = 'neutral', active, onClick }: any) {
    return (
        <button
            onClick={onClick}
            aria-current={active ? 'page' : undefined}
            className="w-full flex items-center justify-between gap-2 pl-3 pr-2.5 py-2.5 transition-all relative active:scale-[0.99]"
            style={{
                background: active ? 'var(--accent-dim)' : 'transparent',
                color: active ? 'var(--text)' : 'var(--text-2)',
            }}
            onMouseEnter={(e: any) => { if (!active) e.currentTarget.style.background = 'var(--panel-2)'; }}
            onMouseLeave={(e: any) => { if (!active) e.currentTarget.style.background = 'transparent'; }}
        >
            {active && (
                <span
                    className="absolute left-0 inset-y-0 w-[3px] animate-scale-in"
                    style={{ background: 'var(--accent)', transformOrigin: 'center' }}
                />
            )}
            <span className="flex items-center gap-2.5 min-w-0">
                <span className="shrink-0" style={{ color: active ? 'var(--accent)' : 'var(--text-3)' }}>{icon}</span>
                <span className="text-[12.5px] font-semibold tracking-wide truncate">{label}</span>
            </span>
            {badge > 0 && (
                <span
                    className="data text-[10px] font-bold px-1.5 py-0.5 border shrink-0"
                    aria-label={`${badge} pending`}
                    style={
                        badgeTone === 'critical'
                            ? { color: 'var(--critical)', borderColor: 'var(--critical)' }
                            : { color: 'var(--text-2)', borderColor: 'var(--line-2)' }
                    }
                >
                    {badge}
                </span>
            )}
        </button>
    );
}

/* ── Metric panel ───────────────────────────────────────────────────────── */
export function MetricPanel({ label, value, icon, bar, tone = 'ok' }: any) {
    const toneColor =
        tone === 'critical' ? 'var(--critical)' : tone === 'warn' ? 'var(--warn)' : 'var(--ok)';
    return (
        <div className="border p-3 hover-lift" style={{ background: 'var(--panel)', borderColor: 'var(--line)' }}>
            <div className="flex items-start justify-between mb-2">
                <span className="label">{label}</span>
                <span style={{ color: 'var(--text-3)' }} aria-hidden="true">{icon}</span>
            </div>
            <div className="data text-2xl font-bold leading-none" style={{ color: 'var(--text)' }}>{value}</div>
            {typeof bar === 'number' && (
                <div
                    className="mt-2.5 h-1 w-full"
                    style={{ background: 'var(--bg)' }}
                    role="progressbar"
                    aria-valuenow={Math.round(bar)}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-label={`${label} level`}
                >
                    <div className="h-full transition-all duration-500" style={{ width: `${bar}%`, background: toneColor }} />
                </div>
            )}
        </div>
    );
}

/* ── Incident row ───────────────────────────────────────────────────────
   Dense log row rather than a padded card: an operator triaging a queue
   needs to compare many incidents at once, so vertical space spent on
   decoration is space taken from the next incident.

   BUG FOUND 2026-09-03 (user report): Confirm/Dismiss sat directly on this
   row with nothing between "incident appears" and "operator decides" --
   no way to actually look at what the camera saw before acting on it. The
   row now opens a Review panel instead of deciding blind; Confirm/Dismiss
   moved there, behind an actual look at the evidence. */
export function IncidentRow({ alert, onConfirm, onDismiss, cameras }: any) {
    const [imgBroken, setImgBroken] = useState(false);
    const [reviewing, setReviewing] = useState(false);

    // alert.cameraLinkId is now the real cameras.id (main.py sends it with
    // every AI-triggered incident) -- resolved against the roster this
    // dashboard already has loaded, rather than showing a raw id or
    // falling back to the free-text location string, which is only ever a
    // snapshot of whatever the camera was named at detection time.
    const camera = cameras?.find((c: any) => c.id === alert.cameraLinkId);
    const cameraName = camera?.name || alert.location || 'Unregistered camera';

    return (
        <article
            className="border-b relative animate-rise-in"
            style={{ borderColor: 'var(--line)' }}
        >
            {/* Severity spine */}
            <span className="absolute left-0 inset-y-0 w-[3px]" style={{ background: 'var(--critical)' }} aria-hidden="true" />

            <button
                type="button"
                onClick={() => setReviewing(true)}
                aria-label={`Review ${alert.type} at ${cameraName}`}
                className="w-full text-left pl-3 pr-2.5 py-2.5 flex gap-2.5 transition-colors hover:bg-white/[0.03]"
            >
                {/* Thumbnail stays small on purpose -- see this component's header
            comment on density. Just hides itself on a load failure rather
            than showing a broken-image icon; the text rows carry the
            incident either way. */}
                {alert.screenshot_path && !imgBroken && (
                    <img
                        src={alert.screenshot_path}
                        onError={() => setImgBroken(true)}
                        alt=""
                        className="w-11 h-11 shrink-0 object-cover border"
                        style={{ borderColor: 'var(--line)' }}
                    />
                )}
                <div className="min-w-0 flex-1">
                    <div className="flex items-baseline justify-between gap-2 mb-1">
                        <span className="text-[12px] font-bold tracking-wide truncate" style={{ color: 'var(--text)' }}>
                            {alert.type}
                        </span>
                        <span className="data text-[10px] shrink-0" style={{ color: 'var(--text-3)' }}>
                            {alert.timestamp}
                        </span>
                    </div>

                    <div className="flex items-center gap-1.5 mb-1">
                        <Video size={10} style={{ color: 'var(--text-3)' }} className="shrink-0" aria-hidden="true" />
                        <span className="text-[10px] truncate" style={{ color: 'var(--text-2)' }}>
                            {cameraName}
                        </span>
                    </div>

                    <div className="flex items-center gap-1.5">
                        <span className="label" style={{ fontSize: '8px' }}>Confidence</span>
                        <div className="flex-1 h-[3px]" style={{ background: 'var(--bg)' }}>
                            <div
                                className="h-full"
                                style={{ width: `${alert.confidence * 100}%`, background: 'var(--warn)' }}
                            />
                        </div>
                        <span className="data text-[10px]" style={{ color: 'var(--text-2)' }}>
                            {(alert.confidence * 100).toFixed(1)}%
                        </span>
                    </div>
                </div>
            </button>

            {reviewing && (
                <IncidentReviewModal
                    alert={alert}
                    cameraName={cameraName}
                    onClose={() => setReviewing(false)}
                    onConfirm={() => { onConfirm(alert.id); setReviewing(false); }}
                    onDismiss={() => { onDismiss(alert.id); setReviewing(false); }}
                />
            )}
        </article>
    );
}

/* ── Incident review modal ──────────────────────────────────────────────
   The actual look-before-you-decide step: the evidence frame captured at
   the moment of detection (banner burned in, same file the case record
   keeps), plus which camera saw it, plus a live check of that camera's
   current feed. The live feed is the same /video_feed stream every
   CameraTile shows -- this deployment runs one AI core against one active
   camera at a time (see docs/scaling_plan.md), so "live" here means
   whatever that one camera currently sees. Labelled honestly rather than
   implied to be a dedicated per-camera stream this system doesn't have. */
function IncidentReviewModal({ alert, cameraName, onClose, onConfirm, onDismiss }: any) {
    const { aiUrl } = useRuntimeConfig();
    return (
        <div className="fixed inset-0 z-[70] flex items-center justify-center p-6" style={{ background: 'rgba(0,0,0,0.75)' }}>
            <div className="border w-full max-w-lg" style={{ background: 'var(--panel)', borderColor: 'var(--line-2)' }}>
                <div className="h-9 flex justify-between items-center px-3 border-b" style={{ borderColor: 'var(--line)' }}>
                    <span className="label" style={{ color: 'var(--text)' }}>Review — {alert.type}</span>
                    <button
                        title="Close"
                        aria-label="Close review"
                        onClick={onClose}
                        style={{ color: 'var(--text-3)' }}
                        className="transition-colors hover:text-[var(--text)]"
                    >
                        <X size={15} />
                    </button>
                </div>

                <div className="p-3 space-y-3">
                    {/* Evidence frame: what the detector actually saw, banner and all --
              the moment itself, not a live view of whatever's happening now. */}
                    <div className="border overflow-hidden" style={{ borderColor: 'var(--line)', background: '#000' }}>
                        {alert.screenshot_path ? (
                            <img src={alert.screenshot_path} alt={`${alert.type} evidence frame`} className="w-full h-auto block" />
                        ) : (
                            <div className="h-40 flex items-center justify-center">
                                <span className="label">No evidence frame captured</span>
                            </div>
                        )}
                    </div>

                    <div className="grid grid-cols-2 gap-2 text-[11px]">
                        <div className="flex items-center gap-1.5">
                            <Video size={11} style={{ color: 'var(--text-3)' }} />
                            <span style={{ color: 'var(--text-2)' }}>{cameraName}</span>
                        </div>
                        <div className="flex items-center gap-1.5 justify-end">
                            <span className="label" style={{ fontSize: '8px' }}>Confidence</span>
                            <span className="data" style={{ color: 'var(--text)' }}>{(alert.confidence * 100).toFixed(1)}%</span>
                        </div>
                    </div>

                    {/* Live check -- see this component's header comment: same single
              active-camera feed every tile on the dashboard shows, not a
              guarantee this is footage of the pole that raised the alert. */}
                    <details className="border" style={{ borderColor: 'var(--line)' }}>
                        <summary className="px-2.5 py-1.5 text-[10px] font-bold uppercase tracking-wider cursor-pointer select-none" style={{ color: 'var(--text-2)' }}>
                            Check the live feed now
                        </summary>
                        <div className="p-1.5 pt-0">
                            <img src={`${aiUrl}/video_feed`} alt="Live camera feed" className="w-full h-auto block border" style={{ borderColor: 'var(--line)' }} />
                        </div>
                    </details>

                    <div className="grid grid-cols-2 gap-1.5 pt-1">
                        <button
                            onClick={onConfirm}
                            aria-label={`Confirm ${alert.type} at ${cameraName}`}
                            className="py-2 text-[10px] font-bold uppercase tracking-wider text-white transition-all hover:opacity-90 active:scale-[0.97]"
                            style={{ background: 'var(--critical)' }}
                        >
                            Confirm
                        </button>
                        <button
                            onClick={onDismiss}
                            aria-label={`Dismiss ${alert.type} at ${cameraName}`}
                            className="py-2 text-[10px] font-bold uppercase tracking-wider border transition-all hover:bg-white/5 active:scale-[0.97]"
                            style={{ borderColor: 'var(--line-2)', color: 'var(--text-2)' }}
                        >
                            Dismiss
                        </button>
                    </div>
                </div>
            </div>
        </div>
    );
}
