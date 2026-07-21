const { app, BrowserWindow, dialog, ipcMain } = require("electron");
const { spawn } = require("child_process");
const path = require("path");
const http = require("http");
const fs = require("fs");

let backendProc = null;
let aiProc = null;
let nextProc = null;
let mainWindow = null;

// In dev (npm run desktop) resources live next to this file. In a packaged
// build, electron-builder's extraFiles land under process.resourcesPath
// instead -- so we pick whichever actually exists.
const isPackaged = app.isPackaged;
const RESOURCES_ROOT = isPackaged ? process.resourcesPath : path.join(__dirname, "..");

const VENV_DIR   = path.join(RESOURCES_ROOT, ".venv");
const PYTHON_EXE = process.platform === "win32"
  ? path.join(VENV_DIR, "Scripts", "python.exe")
  : path.join(VENV_DIR, "bin", "python");

const MAINCODE_DIR = path.join(RESOURCES_ROOT, "maincode");
const BACKEND_SCRIPT = path.join(MAINCODE_DIR, "backend.py");
const AI_SCRIPT       = path.join(MAINCODE_DIR, "main.py");
const REQUIREMENTS_TXT = path.join(RESOURCES_ROOT, "requirements.txt");

// System Python (NOT the venv -- used only to create the venv the first time).
// Relies on Python being on PATH, same assumption setup.bat already makes.
const SYSTEM_PYTHON = process.platform === "win32" ? "python" : "python3";

let setupWindow = null;

function spawnPython(scriptPath, cwd) {
  if (!fs.existsSync(scriptPath)) {
    throw new Error(`Expected script not found: ${scriptPath}`);
  }
  const proc = spawn(PYTHON_EXE, [scriptPath], { cwd, windowsHide: true });
  const tag = path.basename(scriptPath);
  proc.stdout.on("data", (d) => console.log(`[${tag}] ${d}`));
  proc.stderr.on("data", (d) => console.error(`[${tag}] ${d}`));
  proc.on("exit", (code) => console.log(`[${tag}] exited with code ${code}`));
  return proc;
}

// Runs `next start` using Electron's own embedded Node (ELECTRON_RUN_AS_NODE)
// so we don't need a separate system Node.js install on the target machine.
function spawnNextServer() {
  const nextBin = path.join(RESOURCES_ROOT, "node_modules", "next", "dist", "bin", "next");
  const proc = spawn(process.execPath, [nextBin, "start", "-p", "3000"], {
    cwd: RESOURCES_ROOT,
    windowsHide: true,
    env: { ...process.env, ELECTRON_RUN_AS_NODE: "1" },
  });
  proc.stdout.on("data", (d) => console.log(`[next] ${d}`));
  proc.stderr.on("data", (d) => console.error(`[next] ${d}`));
  proc.on("exit", (code) => console.log(`[next] exited with code ${code}`));
  return proc;
}

function waitForPort(port, timeoutMs = 45000) {
  const start = Date.now();
  return new Promise((resolve, reject) => {
    (function attempt() {
      const req = http.get({ host: "127.0.0.1", port, timeout: 1000 }, () => {
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

async function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    show: false,
    autoHideMenuBar: true,
    webPreferences: { contextIsolation: true },
  });

  mainWindow.loadURL("http://localhost:3000");
  mainWindow.once("ready-to-show", () => mainWindow.show());
}

function openSetupWindow() {
  setupWindow = new BrowserWindow({
    width: 640,
    height: 480,
    resizable: false,
    autoHideMenuBar: true,
    webPreferences: {
      contextIsolation: true,
      preload: path.join(__dirname, "setup-preload.js"),
    },
  });
  setupWindow.loadFile(path.join(__dirname, "setup.html"));
  return setupWindow;
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

// Runs a command and resolves/rejects on exit, streaming stdout/stderr to the setup window's log.
function runStep(cmd, args, cwd) {
  return new Promise((resolve, reject) => {
    const proc = spawn(cmd, args, { cwd, windowsHide: true, shell: process.platform === "win32" });
    proc.stdout.on("data", (d) => sendLog(d.toString().trimEnd()));
    proc.stderr.on("data", (d) => sendLog(d.toString().trimEnd()));
    proc.on("error", (err) => reject(err));
    proc.on("exit", (code) => {
      if (code === 0) resolve();
      else reject(new Error(`"${cmd} ${args.join(" ")}" exited with code ${code}`));
    });
  });
}

async function runFirstTimeSetup() {
  sendProgress(5, "Checking for Python...");
  try {
    await runStep(SYSTEM_PYTHON, ["--version"], RESOURCES_ROOT);
  } catch {
    throw new Error(
      "Python was not found on your PATH. Install Python 3.11+ from python.org " +
      "(check 'Add python.exe to PATH' during install), then relaunch EcoVision Sentinel."
    );
  }

  sendProgress(15, "Creating Python environment...");
  await runStep(SYSTEM_PYTHON, ["-m", "venv", ".venv"], RESOURCES_ROOT);

  sendProgress(30, "Upgrading pip...");
  await runStep(PYTHON_EXE, ["-m", "pip", "install", "--upgrade", "pip"], RESOURCES_ROOT);

  sendProgress(40, "Installing dependencies (this can take several minutes)...");
  await runStep(PYTHON_EXE, ["-m", "pip", "install", "-r", REQUIREMENTS_TXT], RESOURCES_ROOT);

  sendProgress(100, "Setup complete.");
}

ipcMain.on("setup:retry", () => {
  runFirstTimeSetup()
    .then(() => {
      sendProgress(100, "Setup complete. Launching...");
      setTimeout(() => { setupWindow.close(); launchMainApp(); }, 800);
    })
    .catch((err) => sendError(err.message));
});

async function launchMainApp() {
  try {
    // backend.py -> port 8000, main.py's stream/AI server -> port 8001
    backendProc = spawnPython(BACKEND_SCRIPT, MAINCODE_DIR);
    aiProc = spawnPython(AI_SCRIPT, MAINCODE_DIR);
    nextProc = spawnNextServer();

    await Promise.all([waitForPort(8000), waitForPort(8001), waitForPort(3000)]);

    await createWindow();
  } catch (err) {
    dialog.showErrorBox(
      "EcoVision Sentinel failed to start",
      `A background service didn't come up in time:\n\n${err.message}`
    );
    app.quit();
  }
}

app.whenReady().then(async () => {
  // The installer no longer bundles .venv (it's a multi-GB CUDA build tied
  // to the dev machine's exact CUDA/driver version -- shipping it pre-built
  // risks silent runtime failures on a different GPU). Instead, on first
  // run, install it live against *this* machine's actual GPU with a visible
  // progress window, same steps setup.bat already does, just with a UI.
  if (!fs.existsSync(PYTHON_EXE)) {
    openSetupWindow();
    setupWindow.webContents.once("did-finish-load", () => {
      runFirstTimeSetup()
        .then(() => {
          sendProgress(100, "Setup complete. Launching...");
          setTimeout(() => { setupWindow.close(); launchMainApp(); }, 800);
        })
        .catch((err) => sendError(err.message));
    });
    return;
  }

  await launchMainApp();
});

function killAll() {
  if (backendProc) backendProc.kill();
  if (aiProc) aiProc.kill();
  if (nextProc) nextProc.kill();
}

app.on("window-all-closed", () => {
  killAll();
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", killAll);