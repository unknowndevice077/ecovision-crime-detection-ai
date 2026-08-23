"use client";
// app/hooks/useAlertNotifier.ts
//
// WHY THIS EXISTS
//
// Until this hook, an incoming incident was delivered SILENTLY. useLiveChannel
// ("incidents") fired, page.tsx called fetchStats() + fetchActiveAlertCache(),
// and the alert list re-rendered. There was no sound, no browser notification,
// and no document.title change anywhere in the frontend -- verified by grep:
// zero `new Audio`, zero `Notification`, zero `document.title`.
//
// So an operator who was looking away, filling in a logbook, or had the window
// minimised received nothing they could notice. For a system whose whole
// purpose is to tell a human that something is happening, that is the single
// biggest gap in the product: the detector can be right and still fail.
//
// Three channels, escalating in how hard they are to miss:
//   1. an audible chime                (works if they are at the desk)
//   2. a document.title badge          (works if the window is behind another)
//   3. a desktop Notification          (works if the window is minimised)
//
// DESIGN NOTES
//
// - The chime is SYNTHESISED with the Web Audio API rather than loaded from an
//   .mp3. This app ships as an Electron bundle and must work with no network,
//   and an audio asset is one more thing that can fail to load, get blocked by
//   a CSP, or go missing from a build. An oscillator cannot 404.
//
// - Browsers block audio until the user has interacted with the page
//   (autoplay policy). An AudioContext created before that starts "suspended".
//   primeAudio() is wired to the first pointer/key event to resume it, so the
//   first real alert is not the one that discovers the problem.
//
// - Alerts already on screen at mount are recorded as "seen" WITHOUT firing.
//   Otherwise every page load would blast the operator with a chime per open
//   incident, and they would learn to ignore it -- which is worse than silence.
//
// - Severity matters: an armed/weapon incident uses a more urgent triple tone.
//   An operator should be able to tell a weapon alert from a lower-tier one
//   without looking at the screen.

import { useCallback, useEffect, useRef } from 'react';

export type NotifiableAlert = {
    id: string;
    type: string;
    severity?: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
    location?: string;
    area?: string;
};

// Incident types that warrant the more urgent tone. Matched case-insensitively
// against the incident `type` string the backend sends.
const URGENT_PATTERNS = ['armed', 'weapon', 'gun', 'knife', 'assault', 'panic'];

function isUrgent(a: NotifiableAlert): boolean {
    const t = (a.type || '').toLowerCase();
    return URGENT_PATTERNS.some(p => t.includes(p));
}

let audioCtx: AudioContext | null = null;

function getCtx(): AudioContext | null {
    if (typeof window === 'undefined') return null;
    if (!audioCtx) {
        const Ctor = window.AudioContext || (window as any).webkitAudioContext;
        if (!Ctor) return null;
        try {
            audioCtx = new Ctor();
        } catch {
            return null;
        }
    }
    return audioCtx;
}

/** Resume a suspended AudioContext. Safe to call repeatedly. */
export function primeAudio() {
    const ctx = getCtx();
    if (ctx && ctx.state === 'suspended') ctx.resume().catch(() => { });
}

/**
 * One note of the chime. Uses an attack/release envelope rather than a bare
 * start/stop -- an abrupt gain change produces an audible click, which sounds
 * like a fault rather than a notification.
 */
function tone(ctx: AudioContext, freq: number, startAt: number, dur: number, peak: number) {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = 'sine';
    osc.frequency.setValueAtTime(freq, startAt);

    gain.gain.setValueAtTime(0.0001, startAt);
    gain.gain.exponentialRampToValueAtTime(peak, startAt + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, startAt + dur);

    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start(startAt);
    osc.stop(startAt + dur + 0.02);
}

/**
 * Standard two-tone notification chime (A5 -> D6), or a three-tone rising
 * pattern for urgent incidents. Deliberately short: this fires per incident,
 * and anything longer becomes intolerable during a burst.
 */
export function playAlertChime(urgent = false) {
    const ctx = getCtx();
    if (!ctx) return;
    if (ctx.state === 'suspended') {
        // Autoplay policy has not been satisfied yet. Try to resume, but do not
        // queue the sound -- a chime that arrives seconds late is worse than none.
        ctx.resume().catch(() => { });
        if (ctx.state === 'suspended') return;
    }

    const t0 = ctx.currentTime + 0.01;
    if (urgent) {
        tone(ctx, 880.0, t0, 0.16, 0.22);
        tone(ctx, 1108.7, t0 + 0.17, 0.16, 0.22);
        tone(ctx, 1318.5, t0 + 0.34, 0.26, 0.25);
    } else {
        tone(ctx, 880.0, t0, 0.18, 0.18);
        tone(ctx, 1174.7, t0 + 0.19, 0.30, 0.18);
    }
}

/** Ask for desktop notification permission once, on a user gesture. */
export function requestNotificationPermission() {
    if (typeof window === 'undefined' || !('Notification' in window)) return;
    if (Notification.permission === 'default') {
        Notification.requestPermission().catch(() => { });
    }
}

function showDesktopNotification(a: NotifiableAlert) {
    if (typeof window === 'undefined' || !('Notification' in window)) return;
    if (Notification.permission !== 'granted') return;
    try {
        const where = a.location || a.area || 'Unknown location';
        const n = new Notification(`${a.type} detected`, {
            body: `${where}\nClick to open EcoVision.`,
            tag: `ecovision-${a.id}`,   // collapses duplicates for the same incident
            requireInteraction: isUrgent(a),
        });
        n.onclick = () => {
            window.focus();
            n.close();
        };
    } catch {
        /* notification construction can throw in some embedded webviews */
    }
}

const BASE_TITLE = 'EcoVision Sentinel';

export function useAlertNotifier(
    alerts: NotifiableAlert[],
    opts: { enabled?: boolean } = {}
) {
    const { enabled = true } = opts;
    const seen = useRef<Set<string> | null>(null);
    const unacked = useRef(0);

    // Satisfy the autoplay policy and ask for notification permission on the
    // first real user gesture. Without this the first alert of a session is
    // silent, because an AudioContext created on load starts suspended.
    useEffect(() => {
        const onGesture = () => {
            primeAudio();
            requestNotificationPermission();
        };
        window.addEventListener('pointerdown', onGesture, { once: true });
        window.addEventListener('keydown', onGesture, { once: true });
        return () => {
            window.removeEventListener('pointerdown', onGesture);
            window.removeEventListener('keydown', onGesture);
        };
    }, []);

    // Clear the title badge once the operator actually looks at the window.
    useEffect(() => {
        const onFocus = () => {
            unacked.current = 0;
            document.title = BASE_TITLE;
        };
        window.addEventListener('focus', onFocus);
        return () => window.removeEventListener('focus', onFocus);
    }, []);

    useEffect(() => {
        if (!enabled) return;

        // First run: record what is already on screen without announcing it.
        if (seen.current === null) {
            seen.current = new Set(alerts.map(a => a.id));
            return;
        }

        const fresh = alerts.filter(a => !seen.current!.has(a.id));
        if (fresh.length === 0) {
            // Keep the set from growing without bound as incidents are resolved.
            seen.current = new Set(alerts.map(a => a.id));
            return;
        }

        for (const a of fresh) seen.current.add(a.id);

        // One chime per batch, not per incident -- a burst of six detections
        // should not produce six overlapping chimes.
        playAlertChime(fresh.some(isUrgent));

        for (const a of fresh) showDesktopNotification(a);

        if (!document.hasFocus()) {
            unacked.current += fresh.length;
            document.title = `(${unacked.current}) ALERT - ${BASE_TITLE}`;
        }
    }, [alerts, enabled]);
}
