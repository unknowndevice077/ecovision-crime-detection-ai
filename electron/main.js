const { app, BrowserWindow, dialog } = require("electron");
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

app.whenReady().then(async () => {
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