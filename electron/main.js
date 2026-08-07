const { app, BrowserWindow, dialog, ipcMain } = require("electron");
const { spawn, execSync } = require("child_process");
const path = require("path");
const http = require("http");
const fs = require("fs");
const fsp = require("fs/promises");
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

function getWritableDataDir() {
  const dir = path.join(app.getPath("userData"), "EcoVisionData");
  fs.mkdirSync(dir, { recursive: true });
  return dir;
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

function getWeightsDir() {
  return path.join(getAppDataDir(), "weights");
}

let setupWindow = null;
let launchWindow = null;
let errorWindow = null;

function spawnPython(scriptPath, cwd, extraEnv = {}) {
  if (!fs.existsSync(scriptPath)) {
    throw new Error(`Expected script not found: ${scriptPath}`);
  }
  const pythonExe = getPythonExe();
  const writableDir = getWritableDataDir();

  const proc = spawn(pythonExe, [scriptPath], {
    cwd,
    windowsHide: true,
    env: {
      ...process.env,
      ECOVISION_WRITABLE_DIR: writableDir,
      PYTHONIOENCODING: "utf-8",
      PYTHONUTF8: "1",
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

function isPortFree(port, host = HOST) {
  return new Promise((resolve) => {
    const tester = require("net")
      .createServer()
      .once("error", () => resolve(false))
      .once("listening", () => tester.close(() => resolve(true)))
      .listen(port, host);
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
  const publicDir = path.join(getAppRoot(), "public");
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
  let nextBin = path.join(appRoot, "node_modules", "next", "dist", "bin", "next");

  if (!fs.existsSync(nextBin) && app.isPackaged) {
    const unpackedBin = path.join(process.resourcesPath, "app.asar.unpacked", "node_modules", "next", "dist", "bin", "next");
    if (fs.existsSync(unpackedBin)) {
      nextBin = unpackedBin;
    }
  }

  const proc = spawn(process.execPath, [nextBin, "start", "-H", HOST, "-p", String(port)], {
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

  sendProgress(40, "Installing dependencies (CPU build)...");
  await runStep(pythonExe, ["-m", "pip", "install", "-r", requirementsPath], RESOURCES_ROOT);

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
    { from: path.join(RESOURCES_ROOT, "config.json"), to: path.join(targetDir, "config.json") },
    { from: path.join(RESOURCES_ROOT, "requirements.txt"), to: path.join(targetDir, "requirements.txt") },
  ];
  for (const { from, to } of entries) {
    if (!fs.existsSync(from)) continue;
    sendLog(`Copying ${path.basename(from)}...`);
    await fsp.cp(from, to, { recursive: true, force: true });
  }
}

// Writes DEVTEAM bootstrap credentials to a plain text file next to the
// install folder AND shows a blocking dialog, instead of only printing to
// a console window that closes on exit.
function surfaceBootstrapCredentials(appDataDir) {
  try {
    const ports = readRuntimePorts();
    const backendPort = ports.backend || BACKEND_DESIRED_PORT;
    const credFile = path.join(appDataDir, "devteam_credentials.txt");
    // backend.py prints bootstrap creds to stdout on first run only; we
    // can't recover them after the fact here, so this just ensures the
    // *file location* is visible/known. The actual write of this file is
    // done from backend.py itself on bootstrap (see backend.py's
    // init_db()) -- if it exists, surface it prominently.
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
    const appDataDir = targetInstallDir || resolveVenvInstallDir();
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
    sendProgress(100, "Launching...");
    setTimeout(() => { setupWindow.close(); launchMainApp(); }, 800);
  } catch (err) {
    sendError(err.message);
  }
});

async function launchMainApp() {
  killAll();
  try {
    const { backendDir, backendScript, maincodeDir, aiScript } = getScriptPaths();
    const appDataDir = getAppDataDir();

    // Clear stale runtime_ports.json from a previous run so we don't read
    // an old port before the fresh processes have written their own.
    try { fs.unlinkSync(getRuntimePortsPath()); } catch {}

    let backendLog = "";
    let nextLog = "";

    sendLaunchProgress(10, "Starting backend API...");
    sendLaunchStep("backend", "active");
    backendProc = spawnPython(backendScript, backendDir, {
      APP_ENV: "production",
      PORT: String(BACKEND_DESIRED_PORT),
    });
    backendProc.stderr.on("data", (d) => { backendLog += d.toString(); sendLaunchLog(d.toString().trimEnd()); });
    backendProc.stdout.on("data", (d) => { backendLog += d.toString(); sendLaunchLog(d.toString().trimEnd()); });

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
    aiProc = spawnPython(aiScript, maincodeDir, {
      APP_ENV: "production",
      AI_CORE_PORT: String(AI_CORE_DESIRED_PORT),
      WEIGHTS_DIR: getWeightsDir(),
      BACKEND_URL: `http://${HOST}:${backendPort}`,
    });
    aiProc.stderr.on("data", (d) => { sendLaunchLog(d.toString().trimEnd()); });
    aiProc.stdout.on("data", (d) => { sendLaunchLog(d.toString().trimEnd()); });

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

    nextProc = spawnNextServer(frontendPort);
    if (nextProc) {
      nextProc.stderr.on("data", (d) => { nextLog += d.toString(); sendLaunchLog(d.toString().trimEnd()); });
      nextProc.stdout.on("data", (d) => { nextLog += d.toString(); sendLaunchLog(d.toString().trimEnd()); });
    }

    try {
      await waitForPort(frontendPort, HOST, 60000);
      sendLaunchStep("next", "done");
      sendLaunchProgress(100, "Ready.");
    } catch (portErr) {
      sendLaunchStep("next", "error");
      throw new Error(`${portErr.message}\n\nNext.js Output (last 2000 chars):\n${nextLog.slice(-2000)}`);
    }

    surfaceBootstrapCredentials(appDataDir);

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