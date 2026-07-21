const { app, BrowserWindow, dialog, ipcMain } = require("electron");
const { spawn, execSync } = require("child_process");
const path = require("path");
const http = require("http");
const fs = require("fs");
const os = require("os");

app.disableHardwareAcceleration();

let backendProc = null;
let aiProc = null;
let nextProc = null;
let mainWindow = null;

const isPackaged = app.isPackaged;
const RESOURCES_ROOT = isPackaged ? process.resourcesPath : path.join(__dirname, "..");
const CONFIG_PATH = path.join(app.getPath("userData"), "env_config.json");

const INSTALL_FOLDER_NAME = "EcoVisionSentinel";

function getVenvDir() {
  if (fs.existsSync(CONFIG_PATH)) {
    try {
      const data = JSON.parse(fs.readFileSync(CONFIG_PATH, "utf8"));
      if (data.venvDir && fs.existsSync(data.venvDir)) return data.venvDir;
    } catch (e) {
      console.error("Failed to read env_config.json", e);
    }
  }
  return path.join(RESOURCES_ROOT, ".venv");
}

function getPythonExe(venvDir = getVenvDir()) {
  return process.platform === "win32"
    ? path.join(venvDir, "Scripts", "python.exe")
    : path.join(venvDir, "bin", "python");
}

const MAINCODE_DIR = path.join(RESOURCES_ROOT, "maincode");
const BACKEND_DIR = path.join(RESOURCES_ROOT, "backend");
const BACKEND_SCRIPT = path.join(BACKEND_DIR, "backend.py");
const AI_SCRIPT = path.join(MAINCODE_DIR, "main.py");
const REQUIREMENTS_TXT = path.join(RESOURCES_ROOT, "requirements.txt");

let setupWindow = null;

function spawnPython(scriptPath, cwd) {
  if (!fs.existsSync(scriptPath)) {
    throw new Error(`Expected script not found: ${scriptPath}`);
  }
  const pythonExe = getPythonExe();
  const writableDir = path.join(app.getPath("userData"), "EcoVisionData");
  fs.mkdirSync(writableDir, { recursive: true });

  const proc = spawn(pythonExe, [scriptPath], {
    cwd,
    windowsHide: true,
    env: { ...process.env, ECOVISION_WRITABLE_DIR: writableDir },
  });
  const tag = path.basename(scriptPath);
  proc.stdout.on("data", (d) => console.log(`[${tag}] ${d}`));
  proc.stderr.on("data", (d) => console.error(`[${tag}] ${d}`));
  proc.on("exit", (code) => console.log(`[${tag}] exited with code ${code}`));
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

function spawnNextServer() {
  const appRoot = getAppRoot();
  let nextBin = path.join(appRoot, "node_modules", "next", "dist", "bin", "next");

  if (!fs.existsSync(nextBin) && app.isPackaged) {
    const unpackedBin = path.join(process.resourcesPath, "app.asar.unpacked", "node_modules", "next", "dist", "bin", "next");
    if (fs.existsSync(unpackedBin)) {
      nextBin = unpackedBin;
    }
  }

  const proc = spawn(process.execPath, [nextBin, "start", "-p", "3000"], {
    cwd: appRoot,
    windowsHide: true,
    env: { ...process.env, ELECTRON_RUN_AS_NODE: "1" },
  });
  const tag = "next";
  proc.stdout.on("data", (d) => console.log(`[${tag}] ${d}`));
  proc.stderr.on("data", (d) => console.error(`[${tag}] ${d}`));
  proc.on("exit", (code) => console.log(`[${tag}] exited with code ${code}`));
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
    show: true,
    backgroundColor: "#0B0F17",
    autoHideMenuBar: true,
    webPreferences: { contextIsolation: true, backgroundThrottling: false },
  });

  const splashHtml = `
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="utf-8">
      <style>
        body { background: #0B0F17; color: #f8fafc; font-family: -apple-system, Segoe UI, sans-serif; display: flex; height: 100vh; margin: 0; align-items: center; justify-content: center; flex-direction: column; }
        .spinner { width: 44px; height: 44px; border: 3px solid rgba(16,185,129,0.15); border-top-color: #10b981; border-radius: 50%; animation: spin 0.8s linear infinite; margin-bottom: 24px; }
        @keyframes spin { to { transform: rotate(360deg); } }
        h2 { font-size: 15px; letter-spacing: 0.12em; text-transform: uppercase; font-weight: 700; color: #10b981; margin: 0; }
        p { font-size: 12px; color: #64748b; margin-top: 8px; font-weight: 500; }
      </style>
    </head>
    <body>
      <div class="spinner"></div>
      <h2>EcoVision Sentinel</h2>
      <p>Initializing AI Detection Engines & Next.js Server...</p>
    </body>
    </html>
  `;
  mainWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(splashHtml)}`);
}

function openSetupWindow() {
  setupWindow = new BrowserWindow({
    width: 560,
    height: 560,
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
  setupWindow.once("ready-to-show", () => setupWindow.show());
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

  const psScript = `
$ProgressPreference = 'SilentlyContinue'
$installerPath = Join-Path $env:TEMP "python-3.11.9-amd64.exe"
Write-Output "Downloading Python 3.11.9..."
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Invoke-WebRequest -Uri "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe" -OutFile $installerPath
Write-Output "Installing Python 3.11.9 silently..."
Start-Process -FilePath $installerPath -ArgumentList "/quiet", "InstallAllUsers=1", "PrependPath=1", "SimpleInstall=1" -Wait
Remove-Item $installerPath -Force
Write-Output "Python installation complete."
`;

  await runPowerShellScript(psScript, RESOURCES_ROOT);
}

async function runFirstTimeSetup(targetVenvDir) {
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

  sendProgress(40, "Installing dependencies & GPU binaries...");
  await runStep(
    pythonExe,
    ["-m", "pip", "install", "-r", REQUIREMENTS_TXT, "--extra-index-url", "https://download.pytorch.org/whl/cu121"],
    RESOURCES_ROOT
  );

  sendProgress(100, "Setup complete.");
}

ipcMain.handle("setup:select-directory", async () => {
  const result = await dialog.showOpenDialog(setupWindow, {
    title: "Choose Where to Install EcoVision Sentinel",
    properties: ["openDirectory", "createDirectory"],
    defaultPath: app.getPath("userData"),
  });
  if (result.canceled || !result.filePaths || result.filePaths.length === 0) return null;

  const installDir = path.join(result.filePaths[0], INSTALL_FOLDER_NAME);
  return installDir;
});

ipcMain.on("setup:start", (_event, targetInstallDir) => {
  const installDir = targetInstallDir || path.join(RESOURCES_ROOT, INSTALL_FOLDER_NAME);
  const venvDir = path.join(installDir, ".venv");

  fs.mkdirSync(installDir, { recursive: true });
  sendInstallPath(installDir);

  runFirstTimeSetup(venvDir)
    .then(() => {
      const configDir = path.dirname(CONFIG_PATH);
      if (!fs.existsSync(configDir)) {
        fs.mkdirSync(configDir, { recursive: true });
      }
      fs.writeFileSync(CONFIG_PATH, JSON.stringify({ venvDir }), "utf8");
      sendProgress(100, "Launching...");
      setTimeout(() => { setupWindow.close(); launchMainApp(); }, 800);
    })
    .catch((err) => sendError(err.message));
});

async function launchMainApp() {
  try {
    await createWindow();

    let backendLog = "";
    let nextLog = "";

    backendProc = spawnPython(BACKEND_SCRIPT, BACKEND_DIR);
    backendProc.stderr.on("data", (d) => { backendLog += d.toString(); });
    backendProc.stdout.on("data", (d) => { backendLog += d.toString(); });

    aiProc = spawnPython(AI_SCRIPT, MAINCODE_DIR);

    nextProc = spawnNextServer();
    if (nextProc) {
      nextProc.stderr.on("data", (d) => { nextLog += d.toString(); });
      nextProc.stdout.on("data", (d) => { nextLog += d.toString(); });
    }

    try {
      await Promise.all([
        waitForPort(8000, 60000),
        waitForPort(8001, 60000),
        waitForPort(3000, 60000)
      ]);
    } catch (portErr) {
      let detail = portErr.message;
      if (portErr.message.includes("3000") && nextLog) {
        detail += `\n\nNext.js Output (last 2000 chars):\n${nextLog.slice(-2000)}`;
      } else if (portErr.message.includes("8000") && backendLog) {
        detail += `\n\nBackend stderr (last 2000 chars):\n${backendLog.slice(-2000)}`;
      }
      throw new Error(detail);
    }

    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.loadURL("http://localhost:3000");
    }
  } catch (err) {
    dialog.showErrorBox("EcoVision Sentinel failed to start", err.message);
    killAll();
    app.quit();
  }
}

app.whenReady().then(async () => {
  const pythonExe = getPythonExe();
  if (!fs.existsSync(pythonExe)) {
    openSetupWindow();
    return;
  }

  await launchMainApp();
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