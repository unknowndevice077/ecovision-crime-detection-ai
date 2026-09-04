const { app, BrowserWindow, dialog, ipcMain } = require("electron");
const { spawn, execSync } = require("child_process");
const path = require("path");
const http = require("http");
const fs = require("fs");
const fsp = require("fs/promises");
const os = require("os");
const crypto = require("crypto");

app.disableHardwareAcceleration();

let backendProc = null;
let aiProc = null;
let nextProc = null;

// Recovery plan §3 ("Backend or AI-core process dies while Electron keeps
// running"): previously there was no recovery from this short of the user
// noticing and reopening the app themselves. isShuttingDown distinguishes a
// deliberate close (killAll(), below) from an actual crash -- only the
// latter should trigger a restart. Capped and backed off rather than
// restart-looping forever if a process is crashing on every launch (a bad
// weights file, a corrupt DB) -- see watchForCrash().
let isShuttingDown = false;
let mainWindow = null;

const isPackaged = app.isPackaged;
const RESOURCES_ROOT = isPackaged ? process.resourcesPath : path.join(__dirname, "..");
const CONFIG_PATH = path.join(app.getPath("userData"), "env_config.json");
const INSTALL_FOLDER_NAME = "EcoVisionSentinel";

const BACKEND_DESIRED_PORT = 8000;
const AI_CORE_DESIRED_PORT = 8001;
const FRONTEND_DESIRED_PORT = 3000;
const HOST = "127.0.0.1";
const MAX_PORT_WAIT_ATTEMPTS = 20;
const PORT_POLL_INTERVAL_MS = 500;

function isWritable(dir) {
  try {
    fs.mkdirSync(dir, { recursive: true });
    const testFile = path.join(dir, ".write_test");
    fs.writeFileSync(testFile, "x");
    fs.unlinkSync(testFile);
    return true;
  } catch {
    return false;
  }
}

// Returns { dir, usedFallback }. Only matters for an unpackaged/dev run, or
// a packaged install whose own folder somehow isn't writable (e.g. the user
// hand-picked Program Files during NSIS setup without keeping elevation for
// every future launch). The normal packaged case never calls this: NSIS's
// own "choose install location" page already picked the one folder
// everything lives in, and RESOURCES_ROOT IS that folder.
function resolveVenvInstallDir() {
  if (isWritable(RESOURCES_ROOT)) {
    return { dir: RESOURCES_ROOT, usedFallback: false };
  }
  const fallback = path.join(app.getPath("userData"), "EcoVisionRuntime");
  fs.mkdirSync(fallback, { recursive: true });
  return { dir: fallback, usedFallback: true };
}

// ONE environment, not split. python-env ships as a complete, ready-to-run
// Python (see build_release.bat / setup.bat: the official embeddable
// distribution + pip install, NOT `python -m venv`) copied verbatim via
// extraResources -- there is nothing to build on the target machine, so
// there is nothing that needs splitting by package weight.
function getVenvDir() {
  if (fs.existsSync(CONFIG_PATH)) {
    try {
      const data = JSON.parse(fs.readFileSync(CONFIG_PATH, "utf8"));
      if (data.venvDir && fs.existsSync(data.venvDir)) return data.venvDir;
    } catch (e) {
      console.error("Failed to read env_config.json", e);
    }
  }
  return path.join(RESOURCES_ROOT, "python-env");
}

function getAppDataDir() {
  if (fs.existsSync(CONFIG_PATH)) {
    try {
      const data = JSON.parse(fs.readFileSync(CONFIG_PATH, "utf8"));
      if (data.appDataDir && fs.existsSync(data.appDataDir)) return data.appDataDir;
    } catch (e) {
      console.error("Failed to read env_config.json", e);
    }
  }
  return RESOURCES_ROOT;
}

// Everything mutable -- database, credentials file, logs, runtime_ports.json,
// engines the optimizer writes -- lives INSIDE the same folder as the static
// app files (a "data" subfolder next to backend/maincode/weights/python-env),
// not in %APPDATA% on the system drive. That split (static files in one
// place, all the actual data on C:) is what made the install look scattered
// across drives instead of being the one folder a user could point at, back
// up, or move. Falls back to userData only if the install folder itself
// truly isn't writable, and that fallback is one consolidated folder too,
// not a second scattering.
// BUG FOUND 2026-08-19: this used to re-run the writability probe (create +
// delete a .write_test file) on every single call -- and it's called once
// per spawnPython() invocation, i.e. once for the backend and separately
// once for the AI core. Real, observed consequence: a live install ended up
// with backend.py writing incidents/screenshots to one directory and
// main.py writing recordings/screenshots to a DIFFERENT one, because the two
// probes didn't agree. Nothing downstream expects that -- the frontend asks
// the backend for a screenshot path, the backend serves /static from ITS
// resolved dir, and the file is sitting in the AI core's dir instead. Result
// was every AI-triggered incident showing a broken image, and clip registry
// entries pointing at files that don't exist where the backend looks.
// Memoized so the probe runs once per app launch and every caller for the
// rest of that session -- backend spawn, AI-core spawn, credential file,
// runtime_ports.json, everything -- gets the exact same answer.
let _writableDataDir = null;
function getWritableDataDir() {
  if (_writableDataDir) return _writableDataDir;
  const installRoot = getAppDataDir();
  const primary = path.join(installRoot, "data");
  if (isWritable(primary)) {
    _writableDataDir = primary;
    return _writableDataDir;
  }
  const fallback = path.join(app.getPath("userData"), "EcoVisionData");
  fs.mkdirSync(fallback, { recursive: true });
  _writableDataDir = fallback;
  return _writableDataDir;
}

function getRuntimePortsPath() {
  return path.join(getWritableDataDir(), "runtime_ports.json");
}

function readRuntimePorts() {
  const p = getRuntimePortsPath();
  if (!fs.existsSync(p)) return {};
  try {
    return JSON.parse(fs.readFileSync(p, "utf8"));
  } catch {
    return {};
  }
}

// python-env (the shipped, packaged case) is the official embeddable
// distribution with packages pip installed straight into it -- see
// build_release.bat -- NOT a `python -m venv`, so python.exe sits at the
// env's ROOT, not under Scripts\. (A venv's own python.exe is a stub tied to
// whatever system Python created it -- its pyvenv.cfg records the exact
// machine path -- which is exactly why the previous build broke on any
// machine other than the one that built it. The embeddable distribution has
// no such pointer; it IS the interpreter, not a wrapper around one.)
//
// The dev/unpackaged fallback path (runFirstTimeSetup, below) still creates
// a real `python -m venv`, where python.exe DOES live under Scripts\ -- so
// this checks root first and falls back to the venv layout, transparently
// supporting whichever kind of environment is actually at venvDir.
function getPythonExe(venvDir) {
  const root = process.platform === "win32"
    ? path.join(venvDir, "python.exe")
    : path.join(venvDir, "bin", "python3");
  if (fs.existsSync(root)) return root;
  return process.platform === "win32"
    ? path.join(venvDir, "Scripts", "python.exe")
    : path.join(venvDir, "bin", "python");
}

function getScriptPaths() {
  const appDataDir = getAppDataDir();
  return {
    maincodeDir: path.join(appDataDir, "maincode"),
    backendDir: path.join(appDataDir, "backend"),
    backendScript: path.join(appDataDir, "backend", "backend.py"),
    aiScript: path.join(appDataDir, "maincode", "main.py"),
  };
}

function getWeightsDir() {
  return path.join(getAppDataDir(), "weights");
}

let setupWindow = null;
let launchWindow = null;
let errorWindow = null;
let splashWindow = null;

// scriptArgs is passed through unquoted to spawn(), which does not go via a
// shell -- so arguments containing spaces or quotes need no escaping here.
function spawnPython(scriptPath, cwd, extraEnv = {}, scriptArgs = []) {
  if (!fs.existsSync(scriptPath)) {
    throw new Error(`Expected script not found: ${scriptPath}`);
  }
  const pythonExe = getPythonExe(getVenvDir());
  const writableDir = getWritableDataDir();

  // -u: the optimizer streams "@@"-prefixed progress that the setup window
  // parses live. Block-buffered stdout would deliver it all at the end and
  // the progress bar would jump from 0 to 100.
  const proc = spawn(pythonExe, ["-u", scriptPath, ...scriptArgs], {
    cwd,
    windowsHide: true,
    env: {
      ...process.env,
      ECOVISION_WRITABLE_DIR: writableDir,
      PYTHONIOENCODING: "utf-8",
      PYTHONUTF8: "1",
      // Our python-env is meant to be fully self-contained. Without this,
      // Python's `site` module also scans %APPDATA%\Python\Python311\
      // site-packages -- a per-USER folder outside our control that can hold
      // anything (or nothing) depending on whose machine this runs on. Left
      // enabled, behavior can silently vary machine to machine based on
      // whatever's sitting in that folder; disabled, every install runs the
      // exact same package set we shipped, deterministically.
      PYTHONNOUSERSITE: "1",
      // BUG FOUND 2026-08-25 (user report: optimize weights -> cancel ->
      // close app -> reopen -> the whole laptop crashed). killAll() below
      // already tree-kills this process tree on a normal close, but only
      // runs if Electron's own shutdown code executes at all -- a crashed
      // or force-killed Electron leaves it running forever with no signal
      // that anything happened, still holding its share of a 6 GB GPU.
      // Windows has no PDEATHSIG-equivalent, so port_utils.start_parent_
      // watchdog() polls for this PID instead and self-exits when it's
      // gone. See that function's docstring for the full mechanism.
      ECOVISION_PARENT_PID: String(process.pid),
      HOST,
      ...extraEnv,
    },
  });
  const tag = path.basename(scriptPath);
  proc.stdout.on("data", (d) => console.log(`[${tag}] ${d}`));
  proc.stderr.on("data", (d) => console.error(`[${tag}] ${d}`));
  proc.on("exit", (code) => console.log(`[${tag}] exited with code ${code}`));
  return proc;
}

// Recovery plan §3. spawnFn() must attach its own stdout/stderr forwarding
// (it gets called again on every restart, so that wiring has to travel with
// it rather than being attached once by the caller). setProc() lets the
// caller's own backendProc/aiProc/nextProc module variable stay pointed at
// whichever process instance is currently alive, since killAll() and other
// code elsewhere read those variables directly.
const RESTART_BACKOFF_MS = [2000, 5000, 15000];
const MAX_RESTART_ATTEMPTS = 3;

function watchForCrash(tag, spawnFn, setProc, attempts = 0) {
  const proc = spawnFn();
  setProc(proc);
  proc.on("exit", (code) => {
    // code === 0 is a clean/deliberate exit (not this app's crash path);
    // isShuttingDown covers the app-level close, where every child is
    // expected to exit non-zero (killTree uses SIGKILL/taskkill) and that
    // must NOT be treated as a crash to recover from.
    if (isShuttingDown || code === 0) return;
    const nextAttempt = attempts + 1;
    if (nextAttempt > MAX_RESTART_ATTEMPTS) {
      console.error(`[${tag}] crashed ${attempts} time(s) in a row -- giving up automatic restart.`);
      sendLaunchLog(`[${tag}] keeps crashing and could not be restarted automatically. Please restart the app.`);
      return;
    }
    const delay = RESTART_BACKOFF_MS[Math.min(attempts, RESTART_BACKOFF_MS.length - 1)];
    console.warn(`[${tag}] exited unexpectedly (code ${code}) -- restarting in ${delay}ms (attempt ${nextAttempt}/${MAX_RESTART_ATTEMPTS})`);
    sendLaunchLog(`[${tag}] exited unexpectedly -- restarting (attempt ${nextAttempt}/${MAX_RESTART_ATTEMPTS})`);
    setTimeout(() => {
      if (!isShuttingDown) watchForCrash(tag, spawnFn, setProc, nextAttempt);
    }, delay);
  });
  return proc;
}

function getAppRoot() {
  if (!app.isPackaged) {
    return path.join(__dirname, "..");
  }
  const appPath = app.getAppPath();
  const unpackedPath = appPath.replace("app.asar", "app.asar.unpacked");
  if (fs.existsSync(unpackedPath)) {
    return unpackedPath;
  }
  return appPath;
}

// BUG FOUND 2026-09-05 (user report: launched the app, it said the launch
// was fine, then the window showed their OWN unrelated portfolio project,
// which happened to have `next dev` already running on port 3000). This
// used to ONLY try to bind a throwaway server to the port and call it
// "free" if that bind succeeded. On Windows, two unrelated processes can
// often both bind() the very same port without either one erroring --
// Windows's default socket-reuse behavior is far more permissive here than
// Linux/macOS, and Node's net.Server never opts into the Windows-specific
// exclusive-owner flag (SO_EXCLUSIVEADDRUSE) that would prevent it. So the
// bind-based probe could report port 3000 "free" while the user's own dev
// server was sitting right there, fully alive and answering real HTTP
// requests -- findFreePortForFrontend then handed that same "free" port
// back to spawnNextServer, and waitForPort (which only checks "did ANY
// HTTP response come back") saw the OTHER app answering and called it a
// successful launch. createWindow loaded exactly what was actually
// listening there: their portfolio, not this app.
// A real TCP client CONNECT is the actual question that matters -- "if I
// dial this port right now, does anything pick up?" -- and that answer
// isn't subject to the same bind-sharing ambiguity, because it asks the
// OS's connection-routing path directly instead of the bind-ownership
// path. Falls back to the original bind test only when the connect itself
// errors out for an unrelated reason (most commonly ECONNREFUSED, i.e.
// genuinely nothing there), preserving the original "port is free" answer
// for the actually-free case.
function isPortFree(port, host = HOST) {
  return new Promise((resolve) => {
    const net = require("net");
    const client = net.connect({ port, host });
    const declareOccupied = () => { client.destroy(); resolve(false); };
    client.once("connect", declareOccupied);
    client.once("error", () => {
      client.destroy();
      const tester = net
        .createServer()
        .once("error", () => resolve(false))
        .once("listening", () => tester.close(() => resolve(true)))
        .listen(port, host);
    });
  });
}

// Second layer, specific to the frontend port: even with isPortFree fixed
// above, nothing stops SOME OTHER free port also happening to already run
// a real HTTP server by the time this app claims it (a race between the
// check and spawnNextServer actually binding, or simply a different
// unrelated app on the very next candidate port). waitForPort only proves
// "something answered" -- it can't tell whose server that was. This asks
// the one question that actually distinguishes EcoVision's own frontend
// from anyone else's: does /runtime-config.json exist and match the
// apiUrl/aiUrl THIS launch just wrote to it via
// writeRuntimeConfigForFrontend? A stranger's dev server won't have that
// file at all (404) or won't have this exact content: near-certain either
// way, and unlike waitForPort's plain "did anything respond" check, this
// fails loudly and specifically instead of silently opening someone else's
// app in the window.
function verifyOwnFrontend(port, expectedApiUrl, host = HOST) {
  return new Promise((resolve, reject) => {
    const req = http.get({ host, port, path: "/runtime-config.json", timeout: 5000 }, (res) => {
      let body = "";
      res.on("data", (d) => { body += d; });
      res.on("end", () => {
        try {
          const parsed = JSON.parse(body);
          if (parsed.apiUrl === expectedApiUrl) return resolve();
        } catch {
          // fall through to the rejection below
        }
        reject(new Error(
          `Port ${port} answered, but not with EcoVision Sentinel's own dashboard -- ` +
          `another application (e.g. a dev server for a different project) appears to ` +
          `already be using this port. Close whatever is running on port ${port} and relaunch.`
        ));
      });
    });
    req.on("timeout", () => { req.destroy(); reject(new Error(`Timed out verifying the dashboard on port ${port}.`)); });
    req.on("error", (e) => reject(e));
  });
}

async function findFreePortForFrontend(preferred, maxAttempts = MAX_PORT_WAIT_ATTEMPTS) {
  for (let i = 0; i < maxAttempts; i++) {
    const candidate = preferred + i;
    if (await isPortFree(candidate)) return candidate;
  }
  throw new Error(`Could not find a free port for the dashboard after ${maxAttempts} attempts starting from ${preferred}.`);
}

function writeRuntimeConfigForFrontend(apiUrl, aiUrl) {
  // The dashboard fetches this at load time (see lib/runtime-config.ts) to
  // discover the real ports the backend/AI core landed on, instead of
  // hardcoding localhost:8000/8001.
  //
  // Must be the STANDALONE server's public/ folder, not the source tree's --
  // the running server (see spawnNextServer) is `.next/standalone/server.js`,
  // which serves static files from its own copied public/ directory
  // (tools/copy_standalone_assets.js put it there at build time). Writing to
  // the original project-root public/ at runtime, as this used to, is
  // invisible to that process; the running server never sees the file.
  const publicDir = path.join(getAppRoot(), ".next", "standalone", "public");
  try {
    fs.mkdirSync(publicDir, { recursive: true });
    fs.writeFileSync(
      path.join(publicDir, "runtime-config.json"),
      JSON.stringify({ apiUrl, aiUrl }),
      "utf8"
    );
  } catch (e) {
    console.error("Failed to write runtime-config.json", e);
  }
}

function spawnNextServer(port) {
  const appRoot = getAppRoot();

  // BUG FOUND 2026-08-19: this used to run `next start` via the full Next.js
  // CLI. next.config.ts sets output: "standalone", and Next.js prints (but
  // does not hard-fail on) "next start does not work with output: standalone
  // configuration -- use node .next/standalone/server.js instead" -- so this
  // went unnoticed. The standalone server is what's actually meant to run:
  // it needs PORT/HOSTNAME env vars rather than next start's -H/-p flags,
  // and it only serves public/ and static assets correctly once
  // tools/copy_standalone_assets.js has copied them in at build time (see
  // that script's own header for why Next.js doesn't do this itself).
  const serverJs = path.join(appRoot, ".next", "standalone", "server.js");
  if (!fs.existsSync(serverJs)) {
    throw new Error(
      `Standalone Next.js server not found at ${serverJs}. ` +
      `Run "npm run build" (which now also runs tools/copy_standalone_assets.js) before packaging.`
    );
  }

  const proc = spawn(process.execPath, [serverJs], {
    cwd: path.dirname(serverJs),
    windowsHide: true,
    env: { ...process.env, ELECTRON_RUN_AS_NODE: "1", PORT: String(port), HOSTNAME: HOST },
  });
  const tag = "next";
  proc.stdout.on("data", (d) => console.log(`[${tag}] ${d}`));
  proc.stderr.on("data", (d) => console.error(`[${tag}] ${d}`));
  proc.on("exit", (code) => console.log(`[${tag}] exited with code ${code}`));
  return proc;
}

function waitForPort(port, host = HOST, timeoutMs = 45000) {
  const start = Date.now();
  return new Promise((resolve, reject) => {
    (function attempt() {
      const req = http.get({ host, port, timeout: 1000 }, () => {
        req.destroy();
        resolve();
      });
      req.on("error", () => {
        if (Date.now() - start > timeoutMs) return reject(new Error(`Timed out waiting on port ${port}`));
        setTimeout(attempt, 500);
      });
    })();
  });
}

// Polls runtime_ports.json (written by backend.py / main.py once they've
// bound their actual port, which may differ from the desired default if
// that port was taken) until the given key appears, or gives up after
// MAX_PORT_WAIT_ATTEMPTS polls.
function waitForRuntimePort(key, maxAttempts = MAX_PORT_WAIT_ATTEMPTS) {
  return new Promise((resolve, reject) => {
    let attempts = 0;
    (function poll() {
      const ports = readRuntimePorts();
      if (ports[key]) return resolve(ports[key]);
      attempts++;
      if (attempts >= maxAttempts) {
        return reject(new Error(`Gave up waiting for "${key}" to appear in runtime_ports.json after ${maxAttempts} attempts.`));
      }
      setTimeout(poll, PORT_POLL_INTERVAL_MS);
    })();
  });
}

async function createWindow(url) {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    show: true,
    backgroundColor: "#0B0F17",
    autoHideMenuBar: true,
    webPreferences: { contextIsolation: true, backgroundThrottling: false },
  });
  mainWindow.loadURL(url);
}

// Shown the instant the process starts, before we've even decided whether
// this is a setup run or a launch run. It has no preload and no logic of its
// own -- it exists purely so double-clicking the app produces an on-screen
// result immediately instead of a silent wait while whenReady/fs checks run.
function openSplashWindow() {
  const win = new BrowserWindow({
    width: 320,
    height: 160,
    icon: path.join(__dirname, "logo.png"),
    frame: false,
    resizable: false,
    autoHideMenuBar: true,
    backgroundColor: "#0A0C10",
    show: false,
    alwaysOnTop: true,
    webPreferences: { contextIsolation: true },
  });
  splashWindow = win;
  // Capture `win` locally rather than reading the mutable `splashWindow`
  // module variable inside this closure. The 8s failsafe timer (see
  // splashFailsafe below) or another window's own ready-to-show can call
  // closeSplash() -- which sets splashWindow = null -- before THIS
  // ready-to-show fires. Reading the shared variable at that point crashed
  // the main process with "Cannot read properties of null (reading 'show')"
  // (an uncaught exception, not the graceful in-window error UI). `win` is
  // never reassigned, so this is race-proof.
  win.once("ready-to-show", () => { if (!win.isDestroyed()) win.show(); });
  win.loadFile(path.join(__dirname, "splash.html"));
  return win;
}

function closeSplash() {
  if (splashWindow && !splashWindow.isDestroyed()) splashWindow.close();
  splashWindow = null;
}

function openLaunchWindow() {
  const win = new BrowserWindow({
    width: 500,
    height: 560,
    icon: path.join(__dirname, "logo.png"),
    title: "EcoVision Sentinel",
    resizable: false,
    autoHideMenuBar: true,
    backgroundColor: "#0B0F17",
    show: false,
    webPreferences: {
      contextIsolation: true,
      preload: path.join(__dirname, "launch-preload.js"),
      backgroundThrottling: false,
    },
  });
  launchWindow = win;
  // See openSplashWindow() for why this captures `win` locally instead of
  // reading the mutable `launchWindow` variable inside the closure.
  win.once("ready-to-show", () => { if (!win.isDestroyed()) win.show(); closeSplash(); });
  win.loadFile(path.join(__dirname, "launch.html"));
  return win;
}

function sendLaunchProgress(pct, label) {
  if (launchWindow && !launchWindow.isDestroyed()) launchWindow.webContents.send("launch:progress", pct, label);
}
function sendLaunchLog(line) {
  if (launchWindow && !launchWindow.isDestroyed()) launchWindow.webContents.send("launch:log", line);
}
function sendLaunchStep(step, state) {
  if (launchWindow && !launchWindow.isDestroyed()) launchWindow.webContents.send("launch:step", step, state);
}
function sendLaunchError(msg) {
  if (launchWindow && !launchWindow.isDestroyed()) launchWindow.webContents.send("launch:error", msg);
  openErrorWindow(msg);
}

// Visible, non-hanging error surface: instead of leaving the launch
// window stuck or the app silently blank, pop a dedicated always-on-top
// window with the real failure reason plus a "Quit" action.
function openErrorWindow(message) {
  if (errorWindow && !errorWindow.isDestroyed()) {
    errorWindow.webContents.send("error:message", message);
    return;
  }
  errorWindow = new BrowserWindow({
    width: 560,
    height: 420,
    resizable: true,
    autoHideMenuBar: true,
    backgroundColor: "#0B0F17",
    webPreferences: { contextIsolation: true },
  });
  const html = `
    <html><body style="background:#0B0F17;color:#f87171;font-family:Consolas,monospace;padding:24px;">
      <h2 style="color:#fff;">EcoVision Sentinel failed to start</h2>
      <pre style="white-space:pre-wrap;word-break:break-word;font-size:12px;line-height:1.6;">${message.replace(/</g, "&lt;")}</pre>
      <p style="color:#5b6572;font-size:11px;">Check the logs above, fix the issue, then relaunch the app.</p>
    </body></html>`;
  errorWindow.loadURL("data:text/html;charset=utf-8," + encodeURIComponent(html));
}

function openSetupWindow() {
  const win = new BrowserWindow({
    // Taller than the old 560: the flow now ends on a before/after table that
    // has to show four models plus the combined figure without scrolling.
    width: 580,
    height: 640,
    icon: path.join(__dirname, "logo.png"),
    title: "EcoVision Sentinel — Setup",
    resizable: false,
    autoHideMenuBar: true,
    backgroundColor: "#0B0F17",
    show: false,
    webPreferences: {
      contextIsolation: true,
      preload: path.join(__dirname, "setup-preload.js"),
      backgroundThrottling: false,
    },
  });
  setupWindow = win;
  // See openSplashWindow() for why this captures `win` locally instead of
  // reading the mutable `setupWindow` variable inside the closure.
  win.once("ready-to-show", () => { if (!win.isDestroyed()) win.show(); closeSplash(); });
  win.loadFile(path.join(__dirname, "setup.html"));
  return win;
}

function sendProgress(pct, label) {
  if (setupWindow && !setupWindow.isDestroyed()) setupWindow.webContents.send("setup:progress", pct, label);
}
function sendLog(line) {
  if (setupWindow && !setupWindow.isDestroyed()) setupWindow.webContents.send("setup:log", line);
}
function sendError(msg) {
  if (setupWindow && !setupWindow.isDestroyed()) setupWindow.webContents.send("setup:error", msg);
}
function sendInstallPath(p) {
  if (setupWindow && !setupWindow.isDestroyed()) setupWindow.webContents.send("setup:install-path", p);
}

function runStep(cmd, args, cwd) {
  return new Promise((resolve, reject) => {
    const proc = spawn(cmd, args, { cwd, windowsHide: true, shell: false });
    proc.stdout.on("data", (d) => sendLog(d.toString().trimEnd()));
    proc.stderr.on("data", (d) => sendLog(d.toString().trimEnd()));
    proc.on("error", (err) => reject(err));
    proc.on("exit", (code) => {
      if (code === 0) resolve();
      else reject(new Error(`"${cmd} ${args.join(" ")}" exited with code ${code}`));
    });
  });
}

function runPowerShellScript(scriptText, cwd) {
  const tmpFile = path.join(os.tmpdir(), `ecovision-setup-${Date.now()}.ps1`);
  fs.writeFileSync(tmpFile, scriptText, "utf8");
  return runStep(
    "powershell.exe",
    ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", tmpFile],
    cwd
  ).finally(() => {
    try { fs.unlinkSync(tmpFile); } catch {}
  });
}

// Only used for an unpackaged/dev run (`npm run desktop` without a
// pre-built python-env/ present) -- a normal packaged install ships a
// complete, ready-to-run environment via extraResources and never reaches
// this at all. Searches the system for something to build a throwaway venv
// with, purely for local development convenience.
async function findCompatiblePython() {
  const candidates = process.platform === "win32"
    ? [["py", ["-3.11"]], ["py", ["-3.12"]], ["python", []], ["python3", []]]
    : [["python3.11", []], ["python3.12", []], ["python3", []], ["python", []]];

  const pyCheckScript = "import sys; assert (3, 10) <= sys.version_info < (3, 13)";

  for (const [cmd, prefixArgs] of candidates) {
    try {
      await runStep(cmd, [...prefixArgs, "-c", pyCheckScript], RESOURCES_ROOT);
      return { cmd, prefixArgs };
    } catch {
      // Continue checking candidates
    }
  }
  return null;
}

async function installPythonAutomatically() {
  sendProgress(8, "Downloading Python 3.11...");
  sendLog("Downloading Python 3.11.9 installer from python.org...");

  const EXPECTED_PYTHON_SHA256 = ""; // <-- put the verified hash here before shipping

  const psScript = `
$ProgressPreference = 'SilentlyContinue'
$installerPath = Join-Path $env:TEMP "python-3.11.9-amd64.exe"
$expectedHash = "${EXPECTED_PYTHON_SHA256}"
Write-Output "Downloading Python 3.11.9..."
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Invoke-WebRequest -Uri "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe" -OutFile $installerPath
if ($expectedHash -ne "") {
    Write-Output "Verifying installer checksum..."
    $actualHash = (Get-FileHash -Path $installerPath -Algorithm SHA256).Hash
    if ($actualHash -ne $expectedHash) {
        Remove-Item $installerPath -Force -ErrorAction SilentlyContinue
        Write-Error "Python installer checksum mismatch (expected $expectedHash, got $actualHash) -- refusing to run it."
        exit 1
    }
    Write-Output "Checksum verified."
} else {
    Write-Output "WARNING: no expected checksum configured -- skipping verification."
}
Write-Output "Installing Python 3.11.9 silently..."
Start-Process -FilePath $installerPath -ArgumentList "/quiet", "InstallAllUsers=1", "PrependPath=1", "SimpleInstall=1" -Wait
Remove-Item $installerPath -Force
Write-Output "Python installation complete."
`;

  await runPowerShellScript(psScript, RESOURCES_ROOT);
}

async function runFirstTimeSetup(targetVenvDir, requirementsPath) {
  sendProgress(5, "Checking for Python 3.11 / 3.12...");

  let validPy = await findCompatiblePython();

  if (!validPy) {
    sendLog("No compatible Python found. Installing automatically...");
    await installPythonAutomatically();
    validPy = await findCompatiblePython();
  }

  if (!validPy) {
    throw new Error(
      "Automatic Python installation failed. Please install Python 3.11 manually from python.org and try again."
    );
  }

  sendProgress(15, "Creating Python environment...");
  await runStep(validPy.cmd, [...validPy.prefixArgs, "-m", "venv", targetVenvDir], RESOURCES_ROOT);

  const pythonExe = getPythonExe(targetVenvDir);

  sendProgress(30, "Upgrading pip...");
  await runStep(pythonExe, ["-m", "pip", "install", "--upgrade", "pip"], RESOURCES_ROOT);

  sendProgress(40, "Installing dependencies...");
  await runStep(
    pythonExe,
    ["-m", "pip", "install", "-r", requirementsPath, "--extra-index-url", "https://download.pytorch.org/whl/cu121"],
    RESOURCES_ROOT
  );

  sendProgress(100, "Setup complete.");
}

// Lets setup.html show the real default path before Install is even
// clicked, instead of a vague "Default location" placeholder the user has
// no way to check -- which is what made the silent AppData/C: fallback feel
// like a surprise.
ipcMain.handle("setup:get-default-path", () => {
  const { dir, usedFallback } = resolveVenvInstallDir();
  return { path: dir, usedFallback };
});

ipcMain.handle("setup:select-directory", async () => {
  const result = await dialog.showOpenDialog(setupWindow, {
    title: "Choose Where to Install EcoVision Sentinel",
    properties: ["openDirectory", "createDirectory"],
    defaultPath: app.getPath("userData"),
  });
  if (result.canceled || !result.filePaths || result.filePaths.length === 0) return null;

  const installDir = path.join(result.filePaths[0], INSTALL_FOLDER_NAME);

  if (!isWritable(installDir)) {
    return { error: "This drive/folder can't be written to. Pick a different location." };
  }
  return { path: installDir };
});

// The .env shipped in resources is a TEMPLATE, not a file to copy verbatim.
// It previously was copied byte-for-byte into every install -- meaning
// SECRET_KEY was IDENTICAL across every machine this app was ever installed
// on. This generates a fresh SECRET_KEY per install instead.
//
// TESTING_PHASE_FIXED_CREDENTIALS -- deliberate, per explicit request (18 Aug):
// this build ships with a KNOWN DevTeam login instead of a random
// per-install password, because it's still in a testing phase and needs a
// login every tester already knows without reading a generated credentials
// file. backend.py's init_db() treats DEVTEAM_BOOTSTRAP_USERNAME/PASSWORD as
// "deployer already knows this" and skips writing devteam_credentials.txt
// when they're set -- so surfaceBootstrapCredentials() below shows THESE
// values directly instead of trying to read that file.
//
// MUST be reverted before any real deployment: flip TESTING_PHASE_FIXED_CREDENTIALS
// to false (or delete the block) and every install goes back to a unique,
// randomly generated password shown once and never stored in the repo.
const TESTING_PHASE_FIXED_CREDENTIALS = true;
const TESTING_DEVTEAM_USERNAME = "devteam";
const TESTING_DEVTEAM_PASSWORD = "EcoVision2026Test!";

function randomSecret(bytes = 24) {
  return crypto.randomBytes(bytes).toString("base64url");
}

function writeGeneratedEnv(targetDir) {
  const templatePath = path.join(RESOURCES_ROOT, ".env");
  let template = fs.existsSync(templatePath) ? fs.readFileSync(templatePath, "utf8") : "";

  const secretKey = randomSecret(32);

  const setOrAppend = (content, key, value) => {
    const line = `${key}=${value}`;
    const re = new RegExp(`^${key}=.*$`, "m");
    return re.test(content) ? content.replace(re, line) : content + `\n${line}\n`;
  };
  const removeLine = (content, key) => content.replace(new RegExp(`^${key}=.*$\\n?`, "m"), "");

  let out = template;
  // BUG FOUND 2026-08-18: this used to force APP_ENV=production. Both
  // backend.py and maincode/main.py select config.<APP_ENV>.json over
  // config.json when that file exists -- a real, intentional feature for
  // Docker Compose (docker-compose.yml sets APP_ENV=production there on
  // purpose, to ship different settings in a container). But config.
  // production.json is a DOCKER-ONLY artifact: violence only, no robbery or
  // vandalism blocks at all, stale track-mode threshold/model_path. Forcing
  // APP_ENV=production here made the DESKTOP app silently load that file
  // instead of config.json on every single packaged install -- every model
  // swap and every robbery/vandalism config change this project has made
  // was invisible to the actual shipped app, which is why the AI Models
  // admin page only ever showed Violence. config.development.json (the
  // previous default) is equally stale and equally wrong for this app.
  // "desktop" matches neither Docker env file, by design, so this always
  // falls through to plain config.json -- the only file the desktop build
  // is meant to read.
  out = out.replace(/^APP_ENV=.*$/m, "APP_ENV=desktop");
  out = setOrAppend(out, "SECRET_KEY", secretKey);
  if (TESTING_PHASE_FIXED_CREDENTIALS) {
    out = setOrAppend(out, "DEVTEAM_BOOTSTRAP_USERNAME", TESTING_DEVTEAM_USERNAME);
    out = setOrAppend(out, "DEVTEAM_BOOTSTRAP_PASSWORD", TESTING_DEVTEAM_PASSWORD);
  } else {
    out = removeLine(out, "DEVTEAM_BOOTSTRAP_USERNAME");
    out = removeLine(out, "DEVTEAM_BOOTSTRAP_PASSWORD");
  }

  fs.writeFileSync(path.join(targetDir, ".env"), out, "utf8");
  sendLog("Generated a unique secret key for this install.");
}

// Was fs.cpSync -- fully synchronous, so copying "weights" (large ML model
// files) blocked the Electron main process for the whole duration, which
// also stalls the setup window's IPC (sendLog/sendProgress calls queued
// behind it, making the window appear frozen). fs.promises.cp does the copy
// on libuv's threadpool instead, keeping the main process responsive.
async function copyAppResourcesInto(targetDir) {
  const entries = [
    { from: path.join(RESOURCES_ROOT, "backend"), to: path.join(targetDir, "backend") },
    { from: path.join(RESOURCES_ROOT, "maincode"), to: path.join(targetDir, "maincode") },
    { from: path.join(RESOURCES_ROOT, "weights"), to: path.join(targetDir, "weights") },
    // .env is intentionally NOT in this list -- see writeGeneratedEnv, called
    // separately so secrets are generated, not copied.
    { from: path.join(RESOURCES_ROOT, "config.json"), to: path.join(targetDir, "config.json") },
    { from: path.join(RESOURCES_ROOT, "requirements.txt"), to: path.join(targetDir, "requirements.txt") },
    // Both are run from the install directory, not from RESOURCES_ROOT: they
    // resolve weights/ relative to their own location, and the engines the
    // optimizer writes have to land beside the weights the app will load.
    { from: path.join(RESOURCES_ROOT, "optimize_weights.py"), to: path.join(targetDir, "optimize_weights.py") },
    { from: path.join(RESOURCES_ROOT, "preflight.py"), to: path.join(targetDir, "preflight.py") },
  ];
  for (const { from, to } of entries) {
    if (!fs.existsSync(from)) continue;
    sendLog(`Copying ${path.basename(from)}...`);
    await fsp.cp(from, to, { recursive: true, force: true });
  }
  writeGeneratedEnv(targetDir);
}

// Shows the DevTeam login in a blocking dialog on first run, instead of
// only printing to a console window that closes on exit.
//
// Two modes, matching writeGeneratedEnv above: with TESTING_PHASE_FIXED_CREDENTIALS,
// backend.py was TOLD the username/password (via .env) and does NOT write
// devteam_credentials.txt -- so this shows the known fixed values directly,
// every time, since they're the same on every install by design. Once that
// flag is reverted, backend.py generates a random password and writes it to
// devteam_credentials.txt in the WRITABLE data dir (getWritableDataDir(),
// NOT the install dir -- a previous version of this function checked the
// wrong folder and the dialog silently never appeared), and this falls back
// to reading and showing that file, once, since a random password can't be
// known ahead of time here.
function surfaceBootstrapCredentials() {
  try {
    if (TESTING_PHASE_FIXED_CREDENTIALS) {
      dialog.showMessageBoxSync({
        type: "info",
        title: "EcoVision Sentinel — Testing-Phase Login",
        message: "DevTeam login for this build:",
        detail: `Username: ${TESTING_DEVTEAM_USERNAME}\nPassword: ${TESTING_DEVTEAM_PASSWORD}\n\n` +
          `This is a FIXED testing credential, the same on every install of this build -- ` +
          `not a per-install secret. Do not ship it in a real deployment.`,
        buttons: ["OK"],
      });
      return;
    }
    const writableDir = getWritableDataDir();
    const credFile = path.join(writableDir, "devteam_credentials.txt");
    if (fs.existsSync(credFile)) {
      const contents = fs.readFileSync(credFile, "utf8");
      dialog.showMessageBoxSync({
        type: "info",
        title: "EcoVision Sentinel — First-Run Credentials",
        message: "A DevTeam account was created on first run.",
        detail: `${contents}\n\nSaved to:\n${credFile}\n\nThis will not be shown again after this dialog.`,
        buttons: ["OK"],
      });
    }
  } catch (e) {
    console.error("Failed to surface bootstrap credentials", e);
  }
}

ipcMain.on("setup:start", async (_event, targetInstallDir) => {
  try {
    let appDataDir = targetInstallDir;
    if (!appDataDir) {
      const resolved = resolveVenvInstallDir();
      appDataDir = resolved.dir;
      if (resolved.usedFallback) {
        // Previously silent. The user is told exactly where their data is
        // going and why, instead of it just landing in AppData with no trace.
        sendLog(
          `Note: the app's own folder isn't writable here, so runtime data ` +
          `(python environments, weights, database) is being installed to ` +
          `${appDataDir} instead. Pick "Choose..." on the previous screen to ` +
          `install somewhere else.`
        );
      }
    }
    const venvDir = path.join(appDataDir, ".venv");

    fs.mkdirSync(appDataDir, { recursive: true });
    sendInstallPath(appDataDir);

    sendProgress(2, "Copying application files...");
    await copyAppResourcesInto(appDataDir);

    await runFirstTimeSetup(venvDir, path.join(appDataDir, "requirements.txt"));

    const configDir = path.dirname(CONFIG_PATH);
    if (!fs.existsSync(configDir)) {
      fs.mkdirSync(configDir, { recursive: true });
    }
    fs.writeFileSync(CONFIG_PATH, JSON.stringify({ venvDir, appDataDir }), "utf8");
    sendProgress(100, "Installed.");
    // Hand control back to the renderer instead of launching straight away:
    // the optional GPU optimization is offered between install and first run,
    // because that is the only moment the user is already waiting. The app is
    // completely installed and runnable right now -- optimizing changes speed,
    // never behaviour -- so "Skip" is a first-class outcome, not a failure.
    if (setupWindow && !setupWindow.isDestroyed()) {
      setupWindow.webContents.send("setup:complete");
    }
  } catch (err) {
    sendError(err.message);
  }
});

function sendOptimizeEvent(ev) {
  if (setupWindow && !setupWindow.isDestroyed()) setupWindow.webContents.send("setup:optimize-event", ev);
}
function sendOptimizeLog(line, isErr) {
  if (setupWindow && !setupWindow.isDestroyed()) setupWindow.webContents.send("setup:optimize-log", line, !!isErr);
}

// Can this machine build TensorRT engines at all? Asked by running the real
// optimizer's own precondition check, so the answer cannot drift from what the
// optimizer will actually do. Offering a button that then fails is worse than
// not offering it.
ipcMain.handle("setup:check-optimize", async () => {
  const appDataDir = getAppDataDir();
  const script = path.join(appDataDir, "optimize_weights.py");
  if (!fs.existsSync(script)) {
    return { available: false, reason: "The optimizer was not included in this build." };
  }
  return new Promise((resolve) => {
    let out = "";
    let settled = false;
    const done = (v) => { if (!settled) { settled = true; resolve(v); } };
    try {
      const proc = spawnPython(script, appDataDir, {}, ["--probe"]);
      proc.stdout.on("data", (d) => { out += d.toString(); });
      proc.stderr.on("data", (d) => { out += d.toString(); });
      proc.on("error", () => done({ available: false, reason: "The optimizer could not be started." }));
      proc.on("close", () => {
        const line = out.split(/\r?\n/).find((l) => l.startsWith("@@"));
        if (!line) return done({ available: false, reason: "The optimizer did not report a result." });
        try {
          const info = JSON.parse(line.slice(2));
          done({ available: !!info.ok, reason: info.ok ? null : info.detail, facts: info.facts || [] });
        } catch {
          done({ available: false, reason: "The optimizer returned an unreadable result." });
        }
      });
      setTimeout(() => done({ available: false, reason: "The hardware check timed out." }), 90000);
    } catch {
      done({ available: false, reason: "The optimizer could not be started." });
    }
  });
});

ipcMain.on("setup:start-optimize", () => {
  const appDataDir = getAppDataDir();
  const script = path.join(appDataDir, "optimize_weights.py");
  let buffer = "";
  let sawSummary = false;

  const handleLine = (line) => {
    if (line.startsWith("@@")) {
      try {
        const ev = JSON.parse(line.slice(2));
        if (ev.kind === "summary") sawSummary = true;
        sendOptimizeEvent(ev);
      } catch {
        sendOptimizeLog(line);
      }
    } else if (line.trim()) {
      sendOptimizeLog(line);
    }
  };

  try {
    const proc = spawnPython(script, appDataDir, {}, ["--workspace-gb", "2"]);
    const consume = (chunk) => {
      buffer += chunk.toString();
      const lines = buffer.split(/\r?\n/);
      buffer = lines.pop();          // keep the partial line for the next chunk
      lines.forEach(handleLine);
    };
    proc.stdout.on("data", consume);
    proc.stderr.on("data", (d) => sendOptimizeLog(d.toString().trimEnd()));
    proc.on("error", (err) => {
      sendOptimizeLog(`Optimizer failed to start: ${err.message}`, true);
      sendOptimizeEvent({ kind: "summary", results: [], combined: null });
    });
    proc.on("close", (code) => {
      if (buffer.trim()) handleLine(buffer);
      // A crash before the summary would otherwise leave the window on the
      // progress screen forever. Report an empty result instead: nothing was
      // installed, so the install itself is still good.
      if (!sawSummary) {
        sendOptimizeLog(`Optimizer exited with code ${code} before finishing.`, true);
        sendOptimizeEvent({ kind: "summary", results: [], combined: null });
      }
    });
  } catch (err) {
    sendOptimizeLog(`Optimizer failed to start: ${err.message}`, true);
    sendOptimizeEvent({ kind: "summary", results: [], combined: null });
  }
});

ipcMain.on("setup:finish", () => {
  if (setupWindow && !setupWindow.isDestroyed()) setupWindow.close();
  launchMainApp();
});

// Runs preflight.py before anything is spawned, so a machine that cannot run
// the detector says so in plain language instead of failing later as an opaque
// Python traceback in a log the user never opens.
//
// --skip-models only: weights, schema and database mode. Loading the models
// here would add seconds to every launch to re-check something that does not
// change between runs. The full benchmark stays a manual tool.
//
// Deliberately NON-FATAL. A failed check is surfaced and startup continues:
// this must never be the reason a working install refuses to open, and the
// checks themselves can be wrong on a machine we have not seen.
function runPreflight() {
  return new Promise((resolve) => {
    const script = path.join(RESOURCES_ROOT, "preflight.py");
    if (!fs.existsSync(script)) return resolve(null);
    let out = "";
    try {
      const proc = spawnPython(script, RESOURCES_ROOT, { ECOVISION_PREFLIGHT: "1" });
      proc.stdout.on("data", (d) => { out += d.toString(); });
      proc.stderr.on("data", (d) => { out += d.toString(); });
      proc.on("close", (code) => resolve({ code, out }));
      proc.on("error", () => resolve(null));
      setTimeout(() => resolve({ code: 0, out: out + "\n(preflight timed out)" }), 45000);
    } catch {
      resolve(null);
    }
  });
}

async function launchMainApp() {
  killAll();
  // killAll() above latches isShuttingDown so its own SIGKILL/taskkill exits
  // aren't mistaken for a crash -- but this is also the start of a fresh
  // launch (including a retry after a failed one), so it must come back off
  // here or every watchForCrash() attached below would be permanently inert.
  isShuttingDown = false;
  try {
    const { backendDir, backendScript, maincodeDir, aiScript } = getScriptPaths();
    const appDataDir = getAppDataDir();

    // BUG FOUND 2026-08-18: writeGeneratedEnv() was only ever called from the
    // old setup.html copy-flow (copyAppResourcesInto), which a normal
    // packaged install never runs -- python-env ships complete, so setup is
    // skipped and launchMainApp() runs directly. That meant .env was NEVER
    // written on a normal install: DEVTEAM_BOOTSTRAP_USERNAME/PASSWORD never
    // reached backend.py's environment, so init_db() fell through to the
    // RANDOM-password branch and wrote a DIFFERENT password to
    // devteam_credentials.txt -- while surfaceBootstrapCredentials() (below,
    // after backend starts) unconditionally displayed the FIXED testing
    // password regardless, because it never checked whether .env had
    // actually been written. The dialog showed a login that did not work.
    // Written once per install (guarded by existsSync) so SECRET_KEY -- and
    // therefore every issued login session -- stays stable across restarts
    // instead of invalidating on every launch.
    const envPath = path.join(appDataDir, ".env");
    if (!fs.existsSync(envPath)) {
      writeGeneratedEnv(appDataDir);
    }

    sendLaunchProgress(5, "Checking this machine...");
    const pre = await runPreflight();
    if (pre) {
      // Always log the full text, but summarise it in one line in the window.
      // A WARN here is ordinary (a laptop with a browser open trips the free-RAM
      // check), so showing warnings with the same weight as failures would
      // teach the user to ignore the panel entirely.
      sendLaunchLog(pre.out.trimEnd());
      const verdict = pre.code !== 0 ? "fail" : /READY, with/.test(pre.out) ? "warn" : "ok";
      const summary = {
        ok: "This machine checked out",
        warn: "Ready, with notes — click for details",
        fail: "Some checks failed — starting anyway",
      }[verdict];
      if (launchWindow && !launchWindow.isDestroyed()) {
        launchWindow.webContents.send("launch:preflight", verdict, summary);
      }
      if (verdict === "fail") {
        sendLaunchLog(
          "\n--- Preflight reported problems. Startup will continue, but " +
          "detection may not work. See INSTALL.md for minimum specifications. ---");
      }
    }

    // Clear stale runtime_ports.json from a previous run so we don't read
    // an old port before the fresh processes have written their own.
    try { fs.unlinkSync(getRuntimePortsPath()); } catch {}

    // Backend/AI-core ports are fixed (8000/8001), unlike the frontend's
    // findFreePortForFrontend below -- changing that is a bigger change
    // (the chosen port has to thread through to the AI core's BACKEND_URL
    // and the frontend's runtime config) than is safe to make right now.
    // What IS safe and directly addresses the actual failure mode: killAll()
    // above only stops THIS Electron instance's own tracked processes, so it
    // does nothing about a previous run's backend.py/main.py left running
    // after a crash, a force-quit, or a killed process that didn't exit
    // cleanly -- exactly what happened repeatedly while testing this build
    // today. Previously that produced a generic 60-second timeout
    // ("gave up waiting for backend to appear") with no indication of why.
    // This turns that into an immediate, specific, actionable message.
    for (const [label, port] of [["backend", BACKEND_DESIRED_PORT], ["AI detection core", AI_CORE_DESIRED_PORT]]) {
      if (!(await isPortFree(port, HOST))) {
        throw new Error(
          `Port ${port} (${label}) is already in use.\n\n` +
          `This almost always means a previous copy of EcoVision Sentinel is ` +
          `still running from a session that didn't close cleanly -- check ` +
          `Task Manager for EcoVisionSentinel.exe or python.exe and end it, ` +
          `then relaunch.`
        );
      }
    }

    let backendLog = "";
    let nextLog = "";

    sendLaunchProgress(10, "Starting backend API...");
    sendLaunchStep("backend", "active");
    const spawnBackend = () => {
      const p = spawnPython(backendScript, backendDir, {
        // MUST match writeGeneratedEnv()'s APP_ENV value. This is passed as an
        // explicit child-process env var, which is already set in os.environ
        // before backend.py's load_dotenv() ever runs -- and load_dotenv()'s
        // default (override=False) does NOT replace a variable that's already
        // set. So THIS value wins over whatever .env says, silently. It was
        // "production" here until 2026-08-19, independently of writeGeneratedEnv
        // also forcing "production" -- fixing only one of the two still left
        // the desktop app loading the Docker-only config.production.json (see
        // the writeGeneratedEnv fix from 2026-08-18 for the full story). Two
        // sources of truth for the same value is exactly how that stayed
        // broken after the first fix; "desktop" is now set in both places.
        APP_ENV: "desktop",
        PORT: String(BACKEND_DESIRED_PORT),
      });
      p.stderr.on("data", (d) => { backendLog += d.toString(); sendLaunchLog(d.toString().trimEnd()); });
      p.stdout.on("data", (d) => { backendLog += d.toString(); sendLaunchLog(d.toString().trimEnd()); });
      return p;
    };
    backendProc = watchForCrash("backend", spawnBackend, (p) => { backendProc = p; });

    let backendPort, aiPort, frontendPort;

    try {
      backendPort = await waitForRuntimePort("backend");
      await waitForPort(backendPort, HOST, 60000);
      sendLaunchStep("backend", "done");
      sendLaunchProgress(35, "Backend ready. Starting AI detection core...");
    } catch (portErr) {
      sendLaunchStep("backend", "error");
      throw new Error(`${portErr.message}\n\nBackend stderr (last 2000 chars):\n${backendLog.slice(-2000)}`);
    }

    sendLaunchStep("ai", "active");
    const spawnAi = () => {
      const p = spawnPython(aiScript, maincodeDir, {
        // See the matching comment on the backend spawn above.
        APP_ENV: "desktop",
        AI_CORE_PORT: String(AI_CORE_DESIRED_PORT),
        WEIGHTS_DIR: getWeightsDir(),
        BACKEND_URL: `http://${HOST}:${backendPort}`,
      });
      p.stderr.on("data", (d) => { sendLaunchLog(d.toString().trimEnd()); });
      p.stdout.on("data", (d) => { sendLaunchLog(d.toString().trimEnd()); });
      return p;
    };
    aiProc = watchForCrash("ai", spawnAi, (p) => { aiProc = p; });

    try {
      aiPort = await waitForRuntimePort("ai_core");
      await waitForPort(aiPort, HOST, 60000);
      sendLaunchStep("ai", "done");
      sendLaunchProgress(60, "AI core ready. Starting dashboard...");
    } catch (portErr) {
      sendLaunchStep("ai", "error");
      throw new Error(portErr.message);
    }

    sendLaunchStep("next", "active");
    try {
      frontendPort = await findFreePortForFrontend(FRONTEND_DESIRED_PORT);
    } catch (portErr) {
      sendLaunchStep("next", "error");
      throw portErr;
    }

    writeRuntimeConfigForFrontend(`http://${HOST}:${backendPort}`, `http://${HOST}:${aiPort}`);

    const spawnNext = () => {
      const p = spawnNextServer(frontendPort);
      if (p) {
        p.stderr.on("data", (d) => { nextLog += d.toString(); sendLaunchLog(d.toString().trimEnd()); });
        p.stdout.on("data", (d) => { nextLog += d.toString(); sendLaunchLog(d.toString().trimEnd()); });
      }
      return p;
    };
    nextProc = watchForCrash("next", spawnNext, (p) => { nextProc = p; });

    try {
      await waitForPort(frontendPort, HOST, 60000);
      // waitForPort only proves something answered on this port -- not that
      // it's actually OUR frontend and not some other app already sitting
      // there (see isPortFree's 2026-09-05 fix note above for the real
      // report this came from). This is the loud, specific check instead of
      // silently opening whatever answered.
      await verifyOwnFrontend(frontendPort, `http://${HOST}:${backendPort}`, HOST);
      sendLaunchStep("next", "done");
      sendLaunchProgress(100, "Ready.");
    } catch (portErr) {
      sendLaunchStep("next", "error");
      throw new Error(`${portErr.message}\n\nNext.js Output (last 2000 chars):\n${nextLog.slice(-2000)}`);
    }

    surfaceBootstrapCredentials();

    await createWindow(`http://${HOST}:${frontendPort}`);
    if (launchWindow && !launchWindow.isDestroyed()) launchWindow.close();
  } catch (err) {
    sendLaunchError(err.message);
  }
}

ipcMain.on("launch:start", () => {
  launchMainApp();
});

app.whenReady().then(async () => {
  // Shown immediately -- before deciding setup vs. launch -- so double-
  // clicking the app produces an on-screen result right away instead of a
  // silent wait while the fs checks below run.
  openSplashWindow();
  // Failsafe: the splash is always-on-top, so if setup/launch window creation
  // ever throws before reaching its own ready-to-show handler, don't leave an
  // always-on-top window covering the screen forever.
  const splashFailsafe = setTimeout(closeSplash, 8000);

  // python-env ships as a complete, ready-to-run environment via
  // extraResources (see build_release.bat) -- there is nothing to build or
  // extract here. This existing on a normal install means setup is skipped
  // entirely and we go straight to launch; it's absent only for an
  // unpackaged/dev run or a genuinely broken install, either of which needs
  // the setup flow to build one.
  const pythonExe = getPythonExe(getVenvDir());
  const { backendScript } = getScriptPaths();
  if (!fs.existsSync(pythonExe) || !fs.existsSync(backendScript)) {
    clearTimeout(splashFailsafe);
    openSetupWindow();
    return;
  }
  clearTimeout(splashFailsafe);

  openLaunchWindow();
});

function killTree(proc) {
  if (!proc || !proc.pid) return;
  try {
    if (process.platform === "win32") {
      execSync(`taskkill /F /T /PID ${proc.pid}`, { stdio: "ignore" });
    } else {
      proc.kill("SIGKILL");
    }
  } catch {
    // Process may have already exited
  }
}

function killAll() {
  // Must be set before any killTree() call below -- those SIGKILL/taskkill
  // the child, which fires its "exit" handler with a non-zero code, and
  // watchForCrash() only knows this was deliberate by checking this flag.
  isShuttingDown = true;
  if (backendProc) {
    killTree(backendProc);
    backendProc = null;
  }
  if (aiProc) {
    killTree(aiProc);
    aiProc = null;
  }
  if (nextProc) {
    killTree(nextProc);
    nextProc = null;
  }
}

app.on("window-all-closed", () => {
  killAll();
  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("before-quit", killAll);
app.on("will-quit", () => {
  killAll();
  process.exit(0);
});