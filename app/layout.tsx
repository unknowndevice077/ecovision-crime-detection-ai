import type { Metadata } from "next";
import { Manrope, Public_Sans, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { WebSocketProvider } from "./context/WebSocketContext";

// Self-hosted via next/font (downloaded and bundled at build time, served
// from this app -- not a runtime fetch to Google's CDN). Matters here
// specifically because this is an Electron desktop app that has to render
// correctly even on a machine with no internet at the moment it launches;
// a <link> to fonts.googleapis.com would silently fall back to the browser
// default on that machine with no indication why headers look different.
const manrope = Manrope({ subsets: ["latin"], weight: ["500", "600", "700", "800"], variable: "--font-display" });
const publicSans = Public_Sans({ subsets: ["latin"], weight: ["400", "500", "600"], variable: "--font-body" });
const jetbrainsMono = JetBrains_Mono({ subsets: ["latin"], weight: ["400", "500", "600"], variable: "--font-mono" });

export const metadata: Metadata = {
  title: "EcoVision Sentinel | High-Precision Security",
  description: "Tactical Violence Detection Dashboard",
};

// Runs before React hydrates so the correct theme is on <html> for the very
// first paint -- without this, the page would render in whatever
// prefers-color-scheme gives it and then visibly flip a frame later once
// this component's own JS could read localStorage. Kept as a plain string
// (not a bundled helper) because it has to run as an inline, blocking
// <script> in <head>, before any bundle -- see ThemeToggle.tsx for the
// same STORAGE_KEY/logic used after mount.
const THEME_INIT_SCRIPT = `
(function () {
  try {
    var stored = localStorage.getItem('ecoTheme');
    if (stored === 'light' || stored === 'dark') {
      document.documentElement.setAttribute('data-theme', stored);
    }
  } catch (e) {}
})();
`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${manrope.variable} ${publicSans.variable} ${jetbrainsMono.variable}`}>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
        {/* TEMP: Figma capture script, reverted after use */}
        <script src="https://mcp.figma.com/mcp/html-to-design/capture.js" async></script>
      </head>
      <body className="antialiased">
        <WebSocketProvider>
          {children}
        </WebSocketProvider>
      </body>
    </html>
  );
}
