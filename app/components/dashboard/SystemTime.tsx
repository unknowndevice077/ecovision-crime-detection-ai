"use client";
// app/components/dashboard/SystemTime.tsx
//
// Moved verbatim from app/page.tsx. Kept as two separate components for the
// reason the original comment gives:
//
//   Isolated so its own 1s tick doesn't re-render the whole dashboard tree
//   (nav, camera grid, incident queue, every modal-conditional block) --
//   that state used to live in the top-level EcoVisionSentinel component,
//   which has no memoization anywhere below it, so every descendant re-ran
//   its render function once a second just to update this one clock string.
//
// That reasoning is why they must stay leaf components. Do not inline them
// back into a parent that renders anything else.

import { useEffect, useState } from 'react';

export function SystemClockText() {
    const [time, setTime] = useState(() => new Date().toLocaleTimeString('en-GB', { hour12: false }));
    useEffect(() => {
        const t = setInterval(() => setTime(new Date().toLocaleTimeString('en-GB', { hour12: false })), 1000);
        return () => clearInterval(t);
    }, []);
    return <>{time}</>;
}

export function SystemDateText() {
    const [date, setDate] = useState(() => new Date().toISOString().slice(0, 10));
    useEffect(() => {
        const t = setInterval(() => setDate(new Date().toISOString().slice(0, 10)), 60000);
        return () => clearInterval(t);
    }, []);
    return <>{date}</>;
}
