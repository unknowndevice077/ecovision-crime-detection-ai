"use client";

import React, { useEffect, useState } from 'react';

/* Persists to localStorage as "ecoTheme": "light" | "dark". Absence means
 * "follow the OS" -- globals.css's prefers-color-scheme block already
 * handles that case with no JS involved, so this component only needs to
 * write an explicit choice, never a default one. The inline script in
 * layout.tsx applies whatever's stored BEFORE first paint (a script tag
 * running after hydration would still show one frame of the wrong theme).
 */
const STORAGE_KEY = "ecoTheme";

function currentTheme(): "light" | "dark" {
  if (typeof document === "undefined") return "dark";
  const attr = document.documentElement.getAttribute("data-theme");
  if (attr === "light" || attr === "dark") return attr;
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

export default function ThemeToggle({ className = "" }: { className?: string }) {
  // Starts null so the server-rendered markup and the first client render
  // match (both know nothing about the browser's stored preference) --
  // avoids a hydration mismatch warning. Filled in from the DOM immediately
  // after mount, by which point layout.tsx's inline script has already set
  // data-theme correctly.
  const [theme, setTheme] = useState<"light" | "dark" | null>(null);
  useEffect(() => { setTheme(currentTheme()); }, []);

  const toggle = () => {
    const next = currentTheme() === "light" ? "dark" : "light";
    // Scoped, short-lived transition -- see globals.css's .theme-transition
    // comment for why this isn't a permanent global rule.
    document.documentElement.classList.add("theme-transition");
    document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem(STORAGE_KEY, next); } catch { /* private mode etc -- theme still applies, just won't persist */ }
    setTheme(next);
    window.setTimeout(() => document.documentElement.classList.remove("theme-transition"), 220);
  };

  const isLight = theme === "light";

  return (
    <button
      onClick={toggle}
      title={theme === null ? "Toggle theme" : isLight ? "Switch to dark mode" : "Switch to light mode"}
      aria-label="Toggle color theme"
      className={`relative h-7 w-7 flex items-center justify-center border transition-colors hover:bg-[var(--panel-2)] ${className}`}
      style={{ borderColor: 'var(--line)', borderRadius: 'var(--radius-sm)', color: 'var(--text-2)' }}
    >
      {/* Both icons always mount so the swap can crossfade/rotate instead of
          popping -- opacity/transform only, respects prefers-reduced-motion
          via the shared transition-colors utility duration being near-zero
          there already (no extra media query needed for a sub-200ms fade). */}
      <svg
        width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
        style={{
          position: 'absolute',
          opacity: isLight ? 1 : 0,
          transform: `scale(${isLight ? 1 : 0.5}) rotate(${isLight ? 0 : -90}deg)`,
          transition: 'opacity .18s ease, transform .18s ease',
        }}
      >
        <circle cx="12" cy="12" r="4.5" />
        <path d="M12 2.5v2M12 19.5v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M2.5 12h2M19.5 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4" />
      </svg>
      <svg
        width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
        style={{
          position: 'absolute',
          opacity: isLight ? 0 : 1,
          transform: `scale(${isLight ? 0.5 : 1}) rotate(${isLight ? 90 : 0}deg)`,
          transition: 'opacity .18s ease, transform .18s ease',
        }}
      >
        <path d="M20 14.5A8.5 8.5 0 1110.1 4c.3 0 .5.2.4.5A6.5 6.5 0 0019.5 14c.3-.1.6.1.5.5z" />
      </svg>
    </button>
  );
}
