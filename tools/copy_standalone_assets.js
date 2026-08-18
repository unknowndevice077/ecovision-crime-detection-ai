// Copies public/ and .next/static/ into .next/standalone/ after `next build`.
//
// WHY THIS EXISTS: next.config.ts sets output: "standalone", which produces
// .next/standalone/server.js -- a minimal, self-contained server meant to be
// run directly (`node .next/standalone/server.js`). Next.js does NOT copy
// public/ or .next/static/ into that folder automatically; its own docs say
// so explicitly, and running the standalone server without this step means
// static assets (JS/CSS chunks) and anything in public/ -- including
// runtime-config.json, which electron/main.js writes at launch time so the
// dashboard can find the real backend port -- silently fail to load.
//
// Found 2026-08-19: electron/main.js was launching the frontend with
// `next start` instead, which prints "next start does not work with output:
// standalone" but doesn't hard-fail, so this went unnoticed. Fixed in both
// places: main.js now runs the standalone server.js, and this script makes
// sure that server actually has what it needs to serve.
const fs = require("fs");
const path = require("path");

const ROOT = path.join(__dirname, "..");
const STANDALONE = path.join(ROOT, ".next", "standalone");

function copyDir(src, dest) {
  if (!fs.existsSync(src)) {
    console.warn(`[copy_standalone_assets] source missing, skipping: ${src}`);
    return;
  }
  fs.mkdirSync(dest, { recursive: true });
  fs.cpSync(src, dest, { recursive: true, force: true });
  console.log(`[copy_standalone_assets] copied ${src} -> ${dest}`);
}

if (!fs.existsSync(STANDALONE)) {
  console.error(
    `[copy_standalone_assets] .next/standalone not found -- did "next build" run, ` +
    `and is output: "standalone" still set in next.config.ts?`
  );
  process.exit(1);
}

copyDir(path.join(ROOT, "public"), path.join(STANDALONE, "public"));
copyDir(path.join(ROOT, ".next", "static"), path.join(STANDALONE, ".next", "static"));

console.log("[copy_standalone_assets] done.");
