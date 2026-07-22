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

// Everything lives in one place by default: the venv goes right inside
// the app's own install folder (RESOURCES_ROOT), next to backend/,
// maincode/, weights/, etc. Only falls back to a separate per-user
// AppData folder if the install location turns out to be read-only
// (e.g. Program Files without admin rights) -- no folder picker, no
// second location for the user to think about.
function resolveVenvInstallDir() {
  if (isWritable(RESOURCES_ROOT)) {
    return RESOURCES_ROOT;
  }
  const fallback = path.join(app.getPath("userData"), "EcoVisionRuntime");
  fs.mkdirSync(fallback, { recursive: true });
  return fallback;
}

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

// The folder the user picked in the setup wizard, containing a FULL copy
// of backend/, maincode/, weights/, config.json, requirements.txt, and
// the venv itself -- everything code-related lives here in one place.
// Falls back to RESOURCES_ROOT (the installer's own resources folder)
// if setup hasn't recorded a chosen folder yet.
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

function getPythonExe(venvDir = getVenvDir()) {
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

let setupWindow = null;
let launchWindow = null;

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
    env: {
      ...process.env,
      ECOVISION_WRITABLE_DIR: writableDir,
      PYTHONIOENCODING: "utf-8",
      PYTHONUTF8: "1",
    },
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
}

function openLaunchWindow() {
  launchWindow = new BrowserWindow({
    width: 480,
    height: 520,
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
  launchWindow.once("ready-to-show", () => launchWindow.show());
  launchWindow.loadFile(path.join(__dirname, "launch.html"));
  return launchWindow;
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

  sendProgress(40, "Installing dependencies & GPU binaries...");
  await runStep(
    pythonExe,
    ["-m", "pip", "install", "-r", requirementsPath, "--extra-index-url", "https://download.pytorch.org/whl/cu121"],
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

  if (!isWritable(installDir)) {
    return { error: "This drive/folder can't be written to. Pick a different location." };
  }
  return { path: installDir };
});

function copyAppResourcesInto(targetDir) {
  // Copies everything code/model-related from the installer's resources
  // folder into the folder the user picked, so that folder ends up
  // self-contained: backend/, maincode/, weights/, config.json,
  // requirements.txt, AND (after runFirstTimeSetup) .venv/ all sitting
  // next to each other. Skips re-copying if already present from a
  // previous run/retry.
  const entries = [
    { from: path.join(RESOURCES_ROOT, "backend"), to: path.join(targetDir, "backend") },
    { from: path.join(RESOURCES_ROOT, "maincode"), to: path.join(targetDir, "maincode") },
    { from: path.join(RESOURCES_ROOT, "weights"), to: path.join(targetDir, "weights") },
    { from: path.join(RESOURCES_ROOT, "config.json"), to: path.join(targetDir, "config.json") },
    { from: path.join(RESOURCES_ROOT, "requirements.txt"), to: path.join(targetDir, "requirements.txt") },
  ];
  for (const { from, to } of entries) {
    if (!fs.existsSync(from)) continue;
    sendLog(`Copying ${path.basename(from)}...`);
    fs.cpSync(from, to, { recursive: true, force: true });
  }
}

ipcMain.on("setup:start", (_event, targetInstallDir) => {
  const appDataDir = targetInstallDir || resolveVenvInstallDir();
  const venvDir = path.join(appDataDir, ".venv");

  fs.mkdirSync(appDataDir, { recursive: true });
  sendInstallPath(appDataDir);

  sendProgress(2, "Copying application files...");
  copyAppResourcesInto(appDataDir);

  runFirstTimeSetup(venvDir, path.join(appDataDir, "requirements.txt"))
    .then(() => {
      const configDir = path.dirname(CONFIG_PATH);
      if (!fs.existsSync(configDir)) {
        fs.mkdirSync(configDir, { recursive: true });
      }
      fs.writeFileSync(CONFIG_PATH, JSON.stringify({ venvDir, appDataDir }), "utf8");
      sendProgress(100, "Launching...");
      setTimeout(() => { setupWindow.close(); launchMainApp(); }, 800);
    })
    .catch((err) => sendError(err.message));
});

async function launchMainApp() {
  killAll();
  try {
    const { backendDir, backendScript, maincodeDir, aiScript } = getScriptPaths();

    let backendLog = "";
    let nextLog = "";

    sendLaunchProgress(10, "Starting backend API...");
    sendLaunchStep("backend", "active");
    backendProc = spawnPython(backendScript, backendDir);
    backendProc.stderr.on("data", (d) => { backendLog += d.toString(); sendLaunchLog(d.toString().trimEnd()); });
    backendProc.stdout.on("data", (d) => { backendLog += d.toString(); sendLaunchLog(d.toString().trimEnd()); });

    sendLaunchProgress(35, "Starting AI detection core...");
    sendLaunchStep("ai", "active");
    aiProc = spawnPython(aiScript, maincodeDir);
    aiProc.stderr.on("data", (d) => { sendLaunchLog(d.toString().trimEnd()); });
    aiProc.stdout.on("data", (d) => { sendLaunchLog(d.toString().trimEnd()); });

    sendLaunchProgress(60, "Starting dashboard...");
    sendLaunchStep("next", "active");
    nextProc = spawnNextServer();
    if (nextProc) {
      nextProc.stderr.on("data", (d) => { nextLog += d.toString(); sendLaunchLog(d.toString().trimEnd()); });
      nextProc.stdout.on("data", (d) => { nextLog += d.toString(); sendLaunchLog(d.toString().trimEnd()); });
    }

    try {
      await waitForPort(8000, 60000);
      sendLaunchStep("backend", "done");
      sendLaunchProgress(70, "Backend ready. Waiting on AI core...");

      await waitForPort(8001, 60000);
      sendLaunchStep("ai", "done");
      sendLaunchProgress(85, "AI core ready. Waiting on dashboard...");

      await waitForPort(3000, 60000);
      sendLaunchStep("next", "done");
      sendLaunchProgress(100, "Ready.");
    } catch (portErr) {
      if (portErr.message.includes("8000")) sendLaunchStep("backend", "error");
      else if (portErr.message.includes("8001")) sendLaunchStep("ai", "error");
      else if (portErr.message.includes("3000")) sendLaunchStep("next", "error");

      let detail = portErr.message;
      if (portErr.message.includes("3000") && nextLog) {
        detail += `\n\nNext.js Output (last 2000 chars):\n${nextLog.slice(-2000)}`;
      } else if (portErr.message.includes("8000") && backendLog) {
        detail += `\n\nBackend stderr (last 2000 chars):\n${backendLog.slice(-2000)}`;
      }
      throw new Error(detail);
    }

    await createWindow();
    if (launchWindow && !launchWindow.isDestroyed()) launchWindow.close();
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.loadURL("http://localhost:3000");
    }
  } catch (err) {
    sendLaunchError(err.message);
  }
}

ipcMain.on("launch:start", () => {
  launchMainApp();
});

app.whenReady().then(async () => {
  const pythonExe = getPythonExe();
  const { backendScript } = getScriptPaths();
  if (!fs.existsSync(pythonExe) || !fs.existsSync(backendScript)) {
    openSetupWindow();
    return;
  }

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