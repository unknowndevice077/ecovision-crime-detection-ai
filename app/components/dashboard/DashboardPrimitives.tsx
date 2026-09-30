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
import { createPortal } from 'react-dom';
import { Video, X } from 'lucide-react';
import { SystemClockText, SystemDateText } from './SystemTime';
import { useRuntimeConfig } from '../../hooks/useRuntimeConfig';
import { usePermissions } from '../../hooks/usePermissions';

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
   The actual look-before-you-decide step, side by side: left is the
   evidence frame captured at the moment of detection (banner burned in,
   same file the case record keeps), right is that camera's live feed right
   now -- so the operator compares "what the AI saw" against "what's
   happening" without toggling anything. The live feed is the same
   /video_feed stream every CameraTile shows -- this deployment runs one AI
   core against one active camera at a time (see docs/scaling_plan.md), so
   "live" means whatever that one camera currently sees, and is labelled
   that way rather than implied to be a dedicated per-camera stream. */
function IncidentReviewModal({ alert, cameraName, onClose, onConfirm, onDismiss }: any) {
    const canDecide = usePermissions().can('confirm_dismiss_alerts');
    const { aiUrl } = useRuntimeConfig();
    const [liveBroken, setLiveBroken] = useState(false);

    React.useEffect(() => {
        const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [onClose]);

    // Portalled straight to document.body -- this <article> row (see
    // IncidentRow above) carries `animate-rise-in`, whose `both` fill mode
    // leaves a `transform: translateY(0)` on it permanently after the
    // animation ends. A non-none transform on an ancestor makes THAT
    // element the containing block for any `position: fixed` descendant,
    // so without the portal this modal was sized against the ~310px
    // incident row instead of the viewport. z-[120] so it also opens above
    // the fullscreen video wall (z-[100]), whose queue uses this same row.
    return createPortal(
        <div
            className="fixed inset-0 z-[120] flex items-center justify-center p-4 md:p-6"
            style={{ background: 'rgba(0,0,0,0.8)' }}
            onClick={onClose}
        >
            <div
                className="border w-full max-w-6xl max-h-full flex flex-col"
                style={{ background: 'var(--panel)', borderColor: 'var(--line-2)' }}
                onClick={e => e.stopPropagation()}
            >
                <div className="h-10 shrink-0 flex justify-between items-center px-3 border-b gap-3" style={{ borderColor: 'var(--line)' }}>
                    <div className="flex items-center gap-2 min-w-0">
                        <span className="w-2 h-2 shrink-0 pulse-alert" style={{ background: 'var(--critical)' }} />
                        <span className="label truncate" style={{ color: 'var(--text)' }}>Review — {alert.type}</span>
                        <span className="text-[10px] truncate hidden sm:inline" style={{ color: 'var(--text-3)' }}>
                            <Video size={10} className="inline -mt-0.5 mr-1" />{cameraName} · {alert.timestamp}
                        </span>
                    </div>
                    <div className="flex items-center gap-3 shrink-0">
                        <span className="label" style={{ fontSize: '8px' }}>Confidence</span>
                        <span className="data text-[11px]" style={{ color: 'var(--text)' }}>{(alert.confidence * 100).toFixed(1)}%</span>
                        <button
                            title="Close (Esc)"
                            aria-label="Close review"
                            onClick={onClose}
                            style={{ color: 'var(--text-3)' }}
                            className="transition-colors hover:text-[var(--text)]"
                        >
                            <X size={15} />
                        </button>
                    </div>
                </div>

                <div className="flex-1 min-h-0 overflow-y-auto grid grid-cols-1 md:grid-cols-2 gap-2 p-2">
                    <figure className="flex flex-col min-w-0">
                        <figcaption className="flex items-center justify-between px-1 pb-1.5">
                            <span className="label" style={{ color: 'var(--text-2)' }}>Captured at detection</span>
                            <span className="data text-[10px]" style={{ color: 'var(--text-3)' }}>{alert.timestamp}</span>
                        </figcaption>
                        <div className="relative border overflow-hidden aspect-video" style={{ borderColor: 'var(--line)', background: '#000' }}>
                            {alert.screenshot_path ? (
                                <img src={alert.screenshot_path} alt={`${alert.type} evidence frame`} className="absolute inset-0 w-full h-full object-contain" />
                            ) : (
                                <div className="absolute inset-0 flex items-center justify-center">
                                    <span className="label">No evidence frame captured</span>
                                </div>
                            )}
                        </div>
                    </figure>

                    <figure className="flex flex-col min-w-0">
                        <figcaption className="flex items-center justify-between px-1 pb-1.5">
                            <span className="label" style={{ color: 'var(--text-2)' }}>Live now</span>
                            <span className="flex items-center gap-1">
                                <span className="status-dot live" />
                                <span className="data text-[10px]" style={{ color: 'var(--text-3)' }}>active camera feed</span>
                            </span>
                        </figcaption>
                        <div className="relative border overflow-hidden aspect-video" style={{ borderColor: 'var(--line)', background: '#000' }}>
                            {liveBroken ? (
                                <div className="absolute inset-0 flex items-center justify-center">
                                    <span className="label">Live feed unavailable</span>
                                </div>
                            ) : (
                                <img
                                    src={`${aiUrl}/video_feed`}
                                    alt="Live camera feed"
                                    onError={() => setLiveBroken(true)}
                                    className="absolute inset-0 w-full h-full object-contain"
                                />
                            )}
                            <span className="absolute top-1.5 left-1.5 osd text-[10px] font-bold text-white">{cameraName?.toUpperCase()}</span>
                            <span className="absolute bottom-1.5 left-1.5 osd text-[10px] text-white/90">
                                <SystemDateText /> <SystemClockText />
                            </span>
                        </div>
                    </figure>
                </div>

                {/* Deciding takes confirm_dismiss_alerts (backend
                    update_incident_status); a map-only account used to get
                    both buttons and a 403 after the alert had already been
                    dropped from its queue. */}
                {!canDecide ? (
                    <p className="shrink-0 px-3 pb-3 text-[10px] text-center" style={{ color: 'var(--text-3)' }}>
                        You can view this alert but not confirm or dismiss it.
                    </p>
                ) : <div className="shrink-0 grid grid-cols-2 gap-1.5 p-2 pt-0">
                    <button
                        onClick={onConfirm}
                        aria-label={`Confirm ${alert.type} at ${cameraName}`}
                        className="py-2.5 text-[11px] font-bold uppercase tracking-wider text-white transition-all hover:opacity-90 active:scale-[0.97]"
                        style={{ background: 'var(--critical)' }}
                    >
                        Confirm
                    </button>
                    <button
                        onClick={onDismiss}
                        aria-label={`Dismiss ${alert.type} at ${cameraName}`}
                        className="py-2.5 text-[11px] font-bold uppercase tracking-wider border transition-all hover:bg-white/5 active:scale-[0.97]"
                        style={{ borderColor: 'var(--line-2)', color: 'var(--text-2)' }}
                    >
                        Dismiss
                    </button>
                </div>}
            </div>
        </div>,
        document.body
    );
}
